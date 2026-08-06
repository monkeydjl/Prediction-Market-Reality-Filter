# backend/app/kernel/optimized_params_store.py
"""Store for optimized parameter sets (Phase 9)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.kernel.kernel_db import KernelOptimizedParams


def _get_engine(db_path: str):
    return create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})


class OptimizedParamsStore:
    """Stores and applies optimized parameter sets.

    Follows existing Store pattern: keyword-only args, session-per-call,
    fail-closed reads (return None / [] on exception).
    """

    def __init__(self, *, db_path: str | None = None) -> None:
        if db_path is None:
            from app.kernel.kernel_db import get_kernel_session
            self._session_factory = get_kernel_session
        else:
            engine = _get_engine(db_path)
            self._session_factory = sessionmaker(bind=engine)

    def _row_to_dict(self, row: KernelOptimizedParams) -> dict[str, Any]:
        return {
            "id": row.id,
            "sport": row.sport,
            "competition": row.competition,
            "factor_weights": row.factor_weights,
            "elo_params": row.elo_params,
            "score": row.score,
            "accuracy": row.accuracy,
            "brier_score": row.brier_score,
            "mae": row.mae,
            "sample_count": row.sample_count,
            "trial_number": row.trial_number,
            "status": row.status,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "applied_at": row.applied_at.isoformat() if row.applied_at else None,
        }

    def save_candidate(
        self,
        *,
        sport: str,
        competition: str,
        factor_weights: dict,
        elo_params: dict,
        score: float,
        accuracy: float,
        brier_score: float,
        mae: float,
        sample_count: int,
        trial_number: int | None = None,
    ) -> dict:
        """Persist a candidate (upsert).

        UniqueConstraint (sport, competition, status) allows at most one
        ``candidate`` row per sport/competition, so re-runs update in place
        rather than inserting a second candidate (which would collide when an
        ``archived`` row already occupies that status slot).
        """
        session = self._session_factory()
        try:
            row = (
                session.query(KernelOptimizedParams)
                .filter_by(sport=sport, competition=competition, status="candidate")
                .first()
            )
            if row is None:
                row = KernelOptimizedParams(
                    sport=sport,
                    competition=competition,
                    status="candidate",
                )
                session.add(row)
            row.factor_weights = json.dumps(factor_weights)
            row.elo_params = json.dumps(elo_params)
            row.score = score
            row.accuracy = accuracy
            row.brier_score = brier_score
            row.mae = mae
            row.sample_count = sample_count
            row.trial_number = trial_number
            row.created_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(row)
            return self._row_to_dict(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_applied(self, sport: str, competition: str) -> dict | None:
        session = self._session_factory()
        try:
            row = (
                session.query(KernelOptimizedParams)
                .filter_by(sport=sport, competition=competition, status="applied")
                .order_by(KernelOptimizedParams.id.desc())
                .first()
            )
            return self._row_to_dict(row) if row else None
        except Exception:
            return None
        finally:
            session.close()

    def get_candidates(self, sport: str | None = None, limit: int = 50) -> list[dict]:
        session = self._session_factory()
        try:
            q = session.query(KernelOptimizedParams)
            if sport is not None:
                q = q.filter_by(sport=sport)
            q = q.order_by(KernelOptimizedParams.created_at.desc()).limit(limit)
            return [self._row_to_dict(r) for r in q.all()]
        except Exception:
            return []
        finally:
            session.close()

    def apply(self, params_id: int, *, reseed_elo: bool = True) -> dict:
        session = self._session_factory()
        try:
            # Archive any currently-applied params for this sport/competition
            target = session.query(KernelOptimizedParams).filter_by(id=params_id).first()
            if target is None:
                raise ValueError(f"Params id {params_id} not found")
            sport = target.sport
            existing = (
                session.query(KernelOptimizedParams)
                .filter_by(sport=target.sport, competition=target.competition, status="applied")
                .all()
            )
            previous_applied: dict[str, Any] | None = None
            # At most one archived row per (sport, competition) due to UNIQUE.
            # Free the slot before demoting a previous applied row.
            if any(row.id != params_id for row in existing):
                session.query(KernelOptimizedParams).filter_by(
                    sport=target.sport,
                    competition=target.competition,
                    status="archived",
                ).delete(synchronize_session=False)
            for row in existing:
                if row.id != params_id:
                    if previous_applied is None:
                        previous_applied = self._row_to_dict(row)
                    row.status = "archived"
            target.status = "applied"
            target.applied_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(target)

            # Update KernelFactor weights via FactorRegistry (spec §7.5 step 3)
            factor_weights = json.loads(target.factor_weights)
            elo_params_raw = target.elo_params
            from app.kernel.factor_registry import FactorRegistry
            registry = FactorRegistry()
            for factor_id, weight in factor_weights.items():
                registry.update_weight(
                    factor_id, target.competition, weight, source="optimized",
                )

            applied = self._row_to_dict(target)
            before_weights: dict[str, Any] = {}
            if previous_applied and previous_applied.get("factor_weights"):
                try:
                    before_weights = json.loads(previous_applied["factor_weights"])
                except (TypeError, json.JSONDecodeError):
                    before_weights = {}
            keys = sorted(set(before_weights) | set(factor_weights))
            weight_diff = [
                {
                    "factor": k,
                    "before": before_weights.get(k),
                    "after": factor_weights.get(k),
                }
                for k in keys
            ]

            elo_params: dict[str, Any] | None = None
            if elo_params_raw:
                try:
                    elo_params = (
                        json.loads(elo_params_raw)
                        if isinstance(elo_params_raw, str)
                        else elo_params_raw
                    )
                except (TypeError, json.JSONDecodeError):
                    elo_params = None

            elo_seed: dict[str, Any]
            if reseed_elo:
                try:
                    from app.services.historical_data_ingestor import HistoricalDataIngestor

                    seed_result = HistoricalDataIngestor().seed_elo_ratings(sport=sport)
                    elo_seed = {"ok": True, **(seed_result or {})}
                except Exception as exc:  # noqa: BLE001
                    elo_seed = {"ok": False, "error": str(exc)}
            else:
                elo_seed = {"ok": None, "skipped": True}

            try:
                from app.api.routes.predictions import reset_kernel_singleton

                reset_kernel_singleton()
            except Exception:  # noqa: BLE001
                pass

            return {
                "applied": applied,
                "previous_applied": previous_applied,
                "weight_diff": weight_diff,
                "elo_params": elo_params,
                "elo_seed": elo_seed,
            }
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
