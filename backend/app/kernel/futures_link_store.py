# backend/app/kernel/futures_link_store.py
"""Persistence for futures/championship market links (Phase 12).

Mirrors SportMarketLinkStore pattern: keyword-only args, session-per-call,
fail-closed reads. Distinct table (kernel_futures_links) because futures
markets are season-level (competition+season+team), not match-level.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


from app.kernel.kernel_db import (
    KernelFuturesLink,
    KernelFuturesSnapshot,
    get_kernel_session,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _link_row_to_dict(row: KernelFuturesLink) -> dict[str, Any]:
    return {
        "id": row.id,
        "competition": row.competition,
        "season": row.season,
        "team": row.team,
        "contract_id": row.contract_id,
        "source": row.source,
        "market_question": row.market_question,
        "implied_prob": row.implied_prob,
        "verified": bool(row.verified),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _snapshot_row_to_dict(row: KernelFuturesSnapshot, *, team: str | None = None) -> dict[str, Any]:
    # Annotated: every value here is a number or a datetime, so the literal
    # infers datetime | float | None and rejects the str "team" added below.
    d: dict[str, Any] = {
        "id": row.id,
        "link_id": row.link_id,
        "implied_prob": row.implied_prob,
        "price": row.price,
        "liquidity": row.liquidity,
        "volume": row.volume,
        "captured_at": row.captured_at,
    }
    if team is not None:
        d["team"] = team
    return d


class FuturesLinkStore:
    """CRUD facade over KernelFuturesLink and KernelFuturesSnapshot.

    All methods open a short session and close it in finally, mirroring
    SportMarketLinkStore.
    """

    def upsert_link(
        self,
        *,
        competition: str,
        season: str,
        team: str,
        contract_id: str,
        source: str,
        market_question: str | None,
        implied_prob: float,
        verified: bool,
    ) -> dict[str, Any]:
        """Insert or update by (competition, season, team, source)."""
        now = _utcnow()
        session = get_kernel_session()
        try:
            existing = (
                session.query(KernelFuturesLink)
                .filter_by(
                    competition=competition,
                    season=season,
                    team=team,
                    source=source,
                )
                .one_or_none()
            )
            if existing is not None:
                existing.contract_id = contract_id
                existing.market_question = market_question
                existing.implied_prob = implied_prob
                existing.verified = 1 if verified else 0
                existing.updated_at = now
                session.commit()
                session.refresh(existing)
                return _link_row_to_dict(existing)
            row = KernelFuturesLink(
                competition=competition,
                season=season,
                team=team,
                contract_id=contract_id,
                source=source,
                market_question=market_question,
                implied_prob=implied_prob,
                verified=1 if verified else 0,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _link_row_to_dict(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_links(self, competition: str, season: str) -> list[dict[str, Any]]:
        """Return all links for a competition+season pair. Fail-closed: [] on error."""
        session = get_kernel_session()
        try:
            rows = (
                session.query(KernelFuturesLink)
                .filter_by(competition=competition, season=season)
                .all()
            )
            return [_link_row_to_dict(r) for r in rows]
        except Exception:
            return []
        finally:
            session.close()

    def get_verified_links(self) -> list[dict[str, Any]]:
        """Return all verified futures links. Fail-closed: [] on error."""
        session = get_kernel_session()
        try:
            rows = (
                session.query(KernelFuturesLink)
                .filter_by(verified=1)
                .all()
            )
            return [_link_row_to_dict(r) for r in rows]
        except Exception:
            return []
        finally:
            session.close()

    def append_snapshot(
        self,
        *,
        link_id: int,
        implied_prob: float,
        price: float | None,
        liquidity: float | None,
        volume: float | None,
        captured_at: datetime,
    ) -> dict[str, Any]:
        """Append a new snapshot row for a link."""
        session = get_kernel_session()
        try:
            row = KernelFuturesSnapshot(
                link_id=link_id,
                implied_prob=implied_prob,
                price=price,
                liquidity=liquidity,
                volume=volume,
                captured_at=captured_at,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _snapshot_row_to_dict(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_latest_snapshots(self, competition: str, season: str) -> list[dict[str, Any]]:
        """Return the most recent snapshot per link for a competition+season.

        Uses a single query with a max(captured_at) subquery to pick the
        latest snapshot per link_id in one round-trip (avoids N+1).
        Fail-closed: [] on error.
        """
        session = get_kernel_session()
        try:
            # Get all links for this competition+season
            links = (
                session.query(KernelFuturesLink)
                .filter_by(competition=competition, season=season)
                .all()
            )
            if not links:
                return []
            link_ids = [link.id for link in links]
            team_by_id = {link.id: link.team for link in links}

            # Single query: latest snapshot per link_id via subquery
            from sqlalchemy import func

            # Subquery: max captured_at per link_id
            max_dates = (
                session.query(
                    KernelFuturesSnapshot.link_id,
                    func.max(KernelFuturesSnapshot.captured_at).label("max_date"),
                )
                .filter(KernelFuturesSnapshot.link_id.in_(link_ids))
                .group_by(KernelFuturesSnapshot.link_id)
                .subquery()
            )

            # Join back to get full rows
            rows = (
                session.query(KernelFuturesSnapshot)
                .join(
                    max_dates,
                    (KernelFuturesSnapshot.link_id == max_dates.c.link_id)
                    & (KernelFuturesSnapshot.captured_at == max_dates.c.max_date),
                )
                .all()
            )
            return [
                _snapshot_row_to_dict(row, team=team_by_id.get(row.link_id))
                for row in rows
            ]
        except Exception:
            return []
        finally:
            session.close()
