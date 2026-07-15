"""Sport market detector — determines whether a candidate market is a
single-match sports market and extracts structured info.

Deterministic (no LLM). Reverse-looks up team names in the market question
via TeamAliasRegistry, infers sport/competition via keywords, and extracts a
date via regex. Futures/championship markets are filtered out (return None).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from app.sports._shared.team_aliases import (
    TEAM_ALIASES,
    COMPETITION_TO_SPORT,
)

# Keywords that indicate a futures/season market, NOT a single match.
FUTURES_KEYWORDS = (
    "championship", "win the", "win it all", "mvp", "title",
    "playoffs bracket", "draft", "award", "golden boot", "top scorer",
    "regular season", "standings", "qualified", "qualify for",
)

# Competition -> keyword(s) that hint the market is about this competition.
SPORT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "nba": ("nba", "national basketball association"),
    "mlb": ("mlb", "major league baseball"),
    "nhl": ("nhl", "national hockey league"),
    "epl": ("epl", "premier league"),
    "ucl": ("ucl", "champions league", "uefa champions league"),
    "laliga": ("la liga", "laliga"),
    "bundesliga": ("bundesliga",),
    "seriea": ("serie a", "seriea"),
    "ligue1": ("ligue 1", "ligue1"),
    "wc": ("world cup", "fifa world cup"),
}

# Date patterns (ISO + common English forms). Captures YYYY-MM-DD or MM/DD.
DATE_PATTERNS = (
    re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})"),
    re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})"),
)


@dataclass(frozen=True)
class SportMarketInfo:
    contract_id: str
    source: str
    market_question: str
    market_type: str  # "single_match_binary" | "traditional_odds" | "unknown"
    detected_sport: Optional[str]
    detected_competition: Optional[str]
    detected_teams: list[str]
    detected_date: Optional[date] = None
    outcome_label: str = "YES"


def _extract_date(question: str) -> Optional[date]:
    for pat in DATE_PATTERNS:
        m = pat.search(question)
        if not m:
            continue
        groups = m.groups()
        if len(groups[0]) == 4:  # YYYY-MM-DD
            year, month, day = int(groups[0]), int(groups[1]), int(groups[2])
        else:  # MM/DD/YYYY
            month, day, year = int(groups[0]), int(groups[1]), int(groups[2])
        try:
            return date(year, month, day)
        except ValueError:
            continue
    return None


def _detect_teams(question: str) -> tuple[Optional[str], list[str]]:
    """Return (competition, canonical_teams) by reverse-looking up aliases.

    Scans all competitions; returns the first competition that yields >= 1
    team match, plus all matched canonical team ids for that competition.
    """
    q_lower = question.lower()
    for competition, alias_map in TEAM_ALIASES.items():
        matched: list[str] = []
        for alias, canonical in alias_map.items():
            if alias.lower() in q_lower:
                if canonical not in matched:
                    matched.append(canonical)
        if matched:
            return competition, matched
    return None, []


def _detect_competition_by_keyword(question: str) -> Optional[str]:
    q_lower = question.lower()
    for competition, keywords in SPORT_KEYWORDS.items():
        for kw in keywords:
            if kw in q_lower:
                return competition
    return None


def detect_sport_market(
    *,
    contract_id: str,
    question: str,
    source: str,
) -> SportMarketInfo | None:
    """Detect whether a market is a single-match sport market.

    Returns None for futures/season markets or non-sport markets.
    The Odds API source is tagged market_type="traditional_odds" without
    further text filtering.
    """
    if source == "the_odds_api":
        comp, teams = _detect_teams(question)
        return SportMarketInfo(
            contract_id=contract_id,
            source=source,
            market_question=question,
            market_type="traditional_odds",
            detected_sport=COMPETITION_TO_SPORT.get(comp) if comp else None,
            detected_competition=comp,
            detected_teams=teams,
            detected_date=_extract_date(question),
            outcome_label="home",
        )

    q_lower = question.lower()
    # Filter out futures/season markets.
    for kw in FUTURES_KEYWORDS:
        if kw in q_lower:
            return None

    comp_from_teams, teams = _detect_teams(question)
    comp_from_kw = _detect_competition_by_keyword(question)
    competition = comp_from_teams or comp_from_kw

    if competition is None and not teams:
        # No team and no sport keyword -> not a sport market.
        return None

    sport = COMPETITION_TO_SPORT.get(competition) if competition else None

    return SportMarketInfo(
        contract_id=contract_id,
        source=source,
        market_question=question,
        market_type="single_match_binary",
        detected_sport=sport,
        detected_competition=competition,
        detected_teams=teams,
        detected_date=_extract_date(question),
        outcome_label="YES",
    )
