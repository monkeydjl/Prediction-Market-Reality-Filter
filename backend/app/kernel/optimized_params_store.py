# backend/app/kernel/optimized_params_store.py
"""Store for optimized parameter sets (Phase 9)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.kernel.kernel_db import KernelOptimizedParams, KernelBase


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
        session = self._session_factory()
        try:
            row = KernelOptimizedParams(
                sport=sport,
                competition=competition,
                factor_weights=json.dumps(factor_weights),
                elo_params=json.dumps(elo_params),
                score=score,
                accuracy=accuracy,
                brier_score=brier_score,
                mae=mae,
                sample_count=sample_count,
                trial_number=trial_number,
                status="candidate",
            )
            session.add(row)
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

    def apply(self, params_id: int) -> dict:
        session = self._session_factory()
        try:
            # Archive any currently-applied params for this sport/competition
            target = session.query(KernelOptimizedParams).filter_by(id=params_id).first()
            if target is None:
                raise ValueError(f"Params id {params_id} not found")
            existing = (
                session.query(KernelOptimizedParams)
                .filter_by(sport=target.sport, competition=target.competition, status="applied")
                .all()
            )
            previous_applied: dict[str, Any] | None = None
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
            return {
                "applied": applied,
                "previous_applied": previous_applied,
                "weight_diff": weight_diff,
            }
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
