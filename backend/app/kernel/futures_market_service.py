# backend/app/kernel/futures_market_service.py
"""Futures/championship market service (Phase 12).

Orchestrates discovery (Kalshi), linking (competition+season+team -> contract),
and price snapshot capture. Distinct from SportMarketBridgeService which
handles single-match binary markets.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from app.kernel.futures_link_store import FuturesLinkStore
from app.services.futures_market_source import fetch_kalshi_futures_markets

logger = logging.getLogger(__name__)

# Matches "2024-25" or "2024" in event titles
_SEASON_PATTERN = re.compile(r"\b(\d{4}-\d{2,4}|\d{4})\b")


class FuturesMarketService:
    """Discovers, links, and captures futures/championship market data."""

    def __init__(self, store: FuturesLinkStore | None = None) -> None:
        self._store = store or FuturesLinkStore()

    def _parse_season_from_title(self, title: str) -> str:
        """Extract a season string from an event title.

        Looks for patterns like "2024-25" or "2024" in the title. Returns ""
        if no match. Used to namespace futures links by season.
        """
        if not title:
            return ""
        match = _SEASON_PATTERN.search(title)
        if match is None:
            return ""
        return match.group(1)

    async def link_futures_market(self, candidate: dict[str, Any]) -> dict[str, int]:
        """Link a single futures candidate (one event -> N contracts).

        Upserts one FuturesLink per contract. Returns counts dict.
        """
        competition = candidate.get("competition", "")
        championship_type = candidate.get("championship_type", "")
        source = candidate.get("source", "kalshi")
        title = candidate.get("title", "")
        season = self._parse_season_from_title(title)
        contracts = candidate.get("contracts", [])

        linked = 0
        errors = 0
        for contract in contracts:
            try:
                team = contract.get("team", "")
                ticker = contract.get("ticker", "")
                price = float(contract.get("price", 0) or 0)
                if not team or not ticker:
                    errors += 1
                    continue
                market_question = f"{championship_type} - {team}" if championship_type else team
                self._store.upsert_link(
                    competition=competition,
                    season=season,
                    team=team,
                    contract_id=ticker,
                    source=source,
                    market_question=market_question,
                    implied_prob=price,
                    verified=True,  # Auto-verified: ticker prefix already implies sport
                )
                linked += 1
            except Exception:
                logger.warning("Failed to link futures contract", exc_info=True)
                errors += 1
        return {"links": linked, "errors": errors}

    async def discover_and_link(self) -> dict[str, int]:
        """Fetch futures candidates from Kalshi and link each.

        Returns counts: {"discovered": int, "linked": int, "errors": int}.
        """
        try:
            candidates = await fetch_kalshi_futures_markets(limit=200)
        except Exception:
            logger.warning("Failed to fetch Kalshi futures markets", exc_info=True)
            return {"discovered": 0, "linked": 0, "errors": 0}

        discovered = len(candidates)
        total_linked = 0
        total_errors = 0
        for candidate in candidates:
            try:
                result = await self.link_futures_market(candidate)
                total_linked += result["links"]
                total_errors += result["errors"]
            except Exception:
                logger.warning("Failed to link futures candidate", exc_info=True)
                total_errors += 1
        return {
            "discovered": discovered,
            "linked": total_linked,
            "errors": total_errors,
        }

    async def capture_snapshots(self) -> dict[str, int]:
        """Capture price snapshots for all verified futures links.

        Re-fetches Kalshi futures events to get fresh prices, then matches
        each verified link's contract_id to a fresh price. Returns counts.
        """
        verified = self._store.get_verified_links()
        if not verified:
            return {"captured": 0, "errors": 0}

        # Build contract_id -> contract price lookup from a fresh fetch
        try:
            candidates = await fetch_kalshi_futures_markets(limit=200)
        except Exception:
            logger.warning("Failed to fetch Kalshi futures markets for snapshots", exc_info=True)
            return {"captured": 0, "errors": len(verified)}

        price_by_ticker: dict[str, dict[str, Any]] = {}
        for candidate in candidates:
            for contract in candidate.get("contracts", []):
                ticker = contract.get("ticker", "")
                if ticker:
                    price_by_ticker[ticker] = contract

        captured = 0
        errors = 0
        now = datetime.now(timezone.utc)
        for link in verified:
            try:
                contract_id = link.get("contract_id", "")
                contract = price_by_ticker.get(contract_id)
                if contract is None:
                    errors += 1
                    continue
                self._store.append_snapshot(
                    link_id=link["id"],
                    implied_prob=float(contract.get("price", 0) or 0),
                    price=float(contract.get("price", 0) or 0),
                    liquidity=float(contract.get("liquidity", 0) or 0),
                    volume=float(contract.get("volume", 0) or 0),
                    captured_at=now,
                )
                captured += 1
            except Exception:
                logger.warning("Failed to capture futures snapshot", exc_info=True)
                errors += 1
        return {"captured": captured, "errors": errors}
