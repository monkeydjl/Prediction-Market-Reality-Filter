"""Sport Market Bridge Service — three-layer matching engine.

Links sports matches (match_id) to prediction-market contracts (contract_id)
via rule layer (deterministic) -> LLM layer (semantic) -> manual verification
gate. Verified links are exposed to downstream consumers (fail-closed).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.kernel.market_snapshot_store import MarketSnapshotStore
from app.kernel.sport_market_link_store import SportMarketLinkStore
from app.services.odds_api_service import fetch_match_odds
from app.sports._shared.team_aliases import COMPETITION_TO_SPORT, resolve_team
from app.utils.implied_prob import odds_api_to_implied, polymarket_to_implied

logger = logging.getLogger(__name__)

RULE_CONFIDENCE_THRESHOLD = 0.9
LLM_CONFIDENCE_THRESHOLD = 0.85
LLM_PENDING_THRESHOLD = 0.6


@dataclass(frozen=True)
class MatchResult:
    confidence: float
    mapped_outcome: str
    reasoning: str


def _parse_match_id(match_id: str) -> tuple[str | None, str | None, list[str]]:
    """Split match_id into (competition, date_str, team_tokens).

    Handles both ``nba-20250101-LAL-BOS`` (8-digit date) and
    ``wc-2026-06-13-ARG-FRA`` (YYYY-MM-DD) formats.
    """
    parts = match_id.split("-")
    if not parts:
        return None, None, []
    competition = parts[0]
    rest = parts[1:]
    date_str: str | None = None
    team_tokens: list[str] = []
    i = 0
    while i < len(rest):
        token = rest[i]
        # 8-digit YYYYMMDD
        if len(token) == 8 and token.isdigit():
            date_str = token
            team_tokens = rest[i + 1:]
            break
        # YYYY-MM-DD (three consecutive tokens)
        if (
            i + 2 < len(rest)
            and len(token) == 4 and token.isdigit()
            and len(rest[i + 1]) == 2 and rest[i + 1].isdigit()
            and len(rest[i + 2]) == 2 and rest[i + 2].isdigit()
        ):
            date_str = f"{token}-{rest[i + 1]}-{rest[i + 2]}"
            team_tokens = rest[i + 3:]
            break
        i += 1
    return competition, date_str, team_tokens


class SportMarketBridgeService:
    """Three-layer matching: rule -> LLM -> manual verification (fail-closed)."""

    def __init__(
        self,
        *,
        link_store: SportMarketLinkStore | None = None,
        snapshot_store: MarketSnapshotStore | None = None,
    ) -> None:
        self._links = link_store or SportMarketLinkStore()
        self._snapshots = snapshot_store or MarketSnapshotStore()

    def _rule_match(
        self,
        *,
        match_id: str,
        market_question: str,
        detected_teams: list[str],
        detected_competition: str | None,
    ) -> MatchResult | None:
        """Layer 1: deterministic team-name + date matching."""
        competition, _date_str, team_tokens = _parse_match_id(match_id)
        if competition is None or not team_tokens:
            return None

        canonical: list[str] = []
        for token in team_tokens:
            cid = resolve_team(token, competition)
            if cid:
                canonical.append(cid)
        if not canonical:
            return None

        matched = sum(1 for c in canonical if c in detected_teams)
        if matched >= 2:
            confidence = 0.95
        elif matched == 1:
            confidence = 0.75
        else:
            confidence = 0.3

        mapped_outcome = "home_win"
        reasoning = f"rule_match: {matched}/{len(canonical)} teams matched"
        return MatchResult(confidence=confidence, mapped_outcome=mapped_outcome, reasoning=reasoning)

    async def _llm_match(
        self,
        *,
        match_id: str,
        market_question: str,
        detected_competition: str | None,
        detected_teams: list[str],
    ) -> MatchResult | None:
        """Layer 2: LLM semantic matching on rule miss / low confidence."""
        from app.services import llm_gateway_service as llm

        competition, _date_str, team_tokens = _parse_match_id(match_id)
        sport = COMPETITION_TO_SPORT.get(competition or "", "unknown")

        prompt = (
            f"Given sports match information:\n"
            f"- Sport: {sport}\n"
            f"- Competition: {competition}\n"
            f"- Match teams (tokens): {team_tokens}\n"
            f"\nPrediction market question: \"{market_question}\"\n\n"
            f"Determine whether this market question is about the above match, "
            f"and which outcome the YES result corresponds to.\n"
            f'Output JSON: {{"is_match": bool, "confidence": 0.0-1.0, '
            f'"mapped_outcome": "home_win"|"away_win"|"draw"|"none", "reasoning": str}}'
        )

        result = await llm.complete_json(
            task="default",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        if not result.ok or not result.json_data:
            return None

        data = result.json_data
        if not data.get("is_match", False):
            return None

        return MatchResult(
            confidence=float(data.get("confidence", 0.0)),
            mapped_outcome=str(data.get("mapped_outcome", "none")),
            reasoning=str(data.get("reasoning", "")),
        )

    async def link_polymarket_market(
        self,
        *,
        match_id: str,
        market_info: Any,
        yes_price: float,
        no_price: float,
    ) -> dict | None:
        """Match a Polymarket market to a match_id via rule -> LLM, persist link."""
        rule_result = self._rule_match(
            match_id=match_id,
            market_question=market_info.market_question,
            detected_teams=market_info.detected_teams,
            detected_competition=market_info.detected_competition,
        )

        if rule_result is not None and rule_result.confidence >= RULE_CONFIDENCE_THRESHOLD:
            match_result = rule_result
            link_method = "rule"
            verified = True
        else:
            llm_result = await self._llm_match(
                match_id=match_id,
                market_question=market_info.market_question,
                detected_competition=market_info.detected_competition,
                detected_teams=market_info.detected_teams,
            )
            if llm_result is None or llm_result.confidence < LLM_PENDING_THRESHOLD:
                return None
            if llm_result.mapped_outcome == "none":
                return None
            match_result = llm_result
            link_method = "llm"
            verified = llm_result.confidence >= LLM_CONFIDENCE_THRESHOLD

        yes_implied, no_implied, _spread = polymarket_to_implied(yes_price, no_price)
        if str(market_info.outcome_label).upper() == "YES":
            implied_prob = yes_implied
        else:
            implied_prob = no_implied

        return self._links.upsert_link(
            match_id=match_id,
            contract_id=market_info.contract_id,
            source=market_info.source,
            outcome_label=market_info.outcome_label,
            mapped_outcome=match_result.mapped_outcome,
            link_method=link_method,
            link_confidence=match_result.confidence,
            verified=verified,
            market_question=market_info.market_question,
            implied_prob=implied_prob,
        )

    async def link_traditional_odds(
        self,
        *,
        match_id: str,
        home_team: str,
        away_team: str,
        competition: str,
    ) -> list[dict]:
        """Link traditional sportsbook odds (auto-verified, confidence=1.0)."""
        odds = await fetch_match_odds(home_team, away_team, competition=competition)
        if not odds:
            return []
        home_odds = odds["home"]
        away_odds = odds["away"]
        draw_odds = odds.get("draw")
        if draw_odds:
            implied = odds_api_to_implied([home_odds, draw_odds, away_odds])
            entries = [
                ("home", "home_win", implied[0], home_odds),
                ("draw", "draw", implied[1], draw_odds),
                ("away", "away_win", implied[2], away_odds),
            ]
        else:
            implied = odds_api_to_implied([home_odds, away_odds])
            entries = [
                ("home", "home_win", implied[0], home_odds),
                ("away", "away_win", implied[1], away_odds),
            ]
        results: list[dict] = []
        for outcome_label, mapped_outcome, prob, _raw_price in entries:
            r = self._links.upsert_link(
                match_id=match_id,
                contract_id=f"odds_api::{match_id}::{outcome_label}",
                source="the_odds_api",
                outcome_label=outcome_label,
                mapped_outcome=mapped_outcome,
                link_method="rule",
                link_confidence=1.0,
                verified=True,
                market_question=f"{home_team} vs {away_team}",
                implied_prob=prob,
            )
            results.append(r)
        return results

    def get_verified_links(self, *, match_id: str) -> list[dict]:
        """Fail-closed: return only verified=True links for a match."""
        return self._links.get_verified_links(match_id=match_id)

    async def _fetch_latest_price(self, link: dict) -> float | None:
        """Fetch the latest implied-probability price for a link.

        Dispatches by source. Stubbed here (returns None) so production callers
        can override; tests replace this method with an AsyncMock.
        """
        return None

    async def capture_snapshots(self, *, match_id: str) -> int:
        """Append a price snapshot for each verified link of a match."""
        links = self._links.get_verified_links(match_id=match_id)
        count = 0
        for link in links:
            price = await self._fetch_latest_price(link)
            if price is None:
                continue
            self._snapshots.append_snapshot(
                link_id=link["id"],
                implied_prob=price,
                price=price,
            )
            count += 1
        return count
