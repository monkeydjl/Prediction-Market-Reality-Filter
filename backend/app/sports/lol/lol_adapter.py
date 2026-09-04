# backend/app/sports/lol/lol_adapter.py
"""LolAdapter — DataAdapter Protocol implementation for League of Legends esports.

Reads only from local Kernel fixture tables and optional dry-run JSON import.
Production schedule source defaults to NullLolScheduleSource (no HTTP).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import or_, select

from app.core import config
from app.kernel.domain import (
    CompetitionIdentity,
    MatchIdentity,
    MatchOutcome,
    SeasonIdentity,
    SportIdentity,
    TeamIdentity,
)
from app.kernel.kernel_db import (
    KernelMatchFixture,
    KernelMatchResult,
    get_kernel_session,
)
from app.kernel.protocols import RawMatchData, ScheduleFilter
from app.sports.lol.dry_run_import import import_lol_series_file
from app.sports.lol.source import (
    LolScheduleSource,
    LolSeriesRecord,
    LolSourceResolution,
    resolve_lol_schedule_source,
)

logger = logging.getLogger(__name__)

_LOL = SportIdentity(code="lol", name="League of Legends")
_DEFAULT_COMPETITION = CompetitionIdentity(code="lol", name="League of Legends", sport=_LOL)
_DEFAULT_SEASON = "dry-run"
_DEFAULT_STAGE = "regular"
_DEFAULT_KICKOFF = datetime(2099, 1, 1, tzinfo=timezone.utc)

_DEFAULT_SAMPLE_PATH = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "fixtures"
    / "lol"
    / "sample_series.json"
)

_BO_RE = re.compile(r"^Bo(\d+)$", re.IGNORECASE)


def _competition_for(code: str | None) -> CompetitionIdentity:
    raw = (code or "lol").strip() or "lol"
    return CompetitionIdentity(code=raw, name=raw, sport=_LOL)


def _parse_best_of(venue: str | None) -> int | None:
    if not venue:
        return None
    match = _BO_RE.match(venue.strip())
    if not match:
        return None
    return int(match.group(1))


def _team_code(name: str | None, fallback: str) -> str:
    if not name:
        return fallback
    cleaned = re.sub(r"[^A-Za-z0-9]", "", name)
    if not cleaned:
        return fallback
    return cleaned[:3].upper()


def query_fixture(match_id: str) -> KernelMatchFixture | None:
    """Return the fixture row for match_id, or None when no row carries that id.

    A failed read raises. The swallow that used to return None here fed three
    consumers that each restate None as a fact: ``get_match_identity``
    substitutes a stub (so the match routes answer **404 "Match not found"**),
    ``fetch_all_data`` reports ``venue=None`` and a default ``best_of``, and
    ``process_outcome`` skips the learning step. Measured identical to an
    empty-but-readable table at every door; the numbers are in
    ``football/adapters/_shared.query_fixture``.
    """
    session = get_kernel_session()
    try:
        return session.get(KernelMatchFixture, match_id)
    finally:
        session.close()


def query_result(match_id: str) -> KernelMatchResult | None:
    """Return the result row for match_id, or None when no row carries that id.

    A failed read raises: the swallow made ``process_outcome`` log "No outcome
    found" and ``POST /predictions/outcomes/{id}/process`` answer
    **200 {"status": "processed"}** having processed nothing.
    """
    session = get_kernel_session()
    try:
        return session.get(KernelMatchResult, match_id)
    finally:
        session.close()


def build_match_outcome(result: KernelMatchResult | None) -> MatchOutcome | None:
    if result is None:
        return None
    if result.home_score is None or result.away_score is None:
        return None
    home_score = int(result.home_score)
    away_score = int(result.away_score)
    if home_score == away_score:
        return None
    outcome = "home_win" if home_score > away_score else "away_win"
    return MatchOutcome(
        match_id=result.match_id,
        home_score=home_score,
        away_score=away_score,
        outcome=outcome,
        finished_at=result.finished_at or datetime.now(timezone.utc),
    )


def _upsert_series_record(record: LolSeriesRecord) -> None:
    session = get_kernel_session()
    try:
        now = datetime.now(timezone.utc)
        match_id = f"lol-{record.external_id}"
        venue = f"Bo{int(record.best_of or 1)}"
        existing = session.get(KernelMatchFixture, match_id)
        if existing:
            existing.home_team = record.home_name
            existing.away_team = record.away_name
            existing.kickoff_utc = record.kickoff_utc
            existing.stage = record.stage
            existing.status = record.status or "scheduled"
            existing.venue = venue
            existing.competition = record.competition
            existing.season = _DEFAULT_SEASON
            existing.updated_at = now
        else:
            session.add(
                KernelMatchFixture(
                    match_id=match_id,
                    competition=record.competition,
                    season=_DEFAULT_SEASON,
                    home_team=record.home_name,
                    away_team=record.away_name,
                    kickoff_utc=record.kickoff_utc,
                    stage=record.stage,
                    status=record.status or "scheduled",
                    venue=venue,
                    created_at=now,
                    updated_at=now,
                )
            )
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.warning("Failed to upsert LoL series %s: %s", record.external_id, exc)
        raise
    finally:
        session.close()


class LolAdapter:
    """DataAdapter Protocol implementation for League of Legends esports."""

    def __init__(self, source: LolScheduleSource | None = None) -> None:
        if source is not None:
            self._source: LolScheduleSource = source
            self._source_resolution: LolSourceResolution | None = None
        else:
            resolution = resolve_lol_schedule_source()
            self._source = resolution.source
            self._source_resolution = resolution
        self._competition = _DEFAULT_COMPETITION
        self.competition = "lol"

    def _stub_identity(self, match_id: str) -> MatchIdentity:
        home = TeamIdentity(code="HOME", name="Home", competition=_DEFAULT_COMPETITION)
        away = TeamIdentity(code="AWAY", name="Away", competition=_DEFAULT_COMPETITION)
        return MatchIdentity(
            match_id=match_id,
            season=SeasonIdentity(competition=_DEFAULT_COMPETITION, season_key=_DEFAULT_SEASON),
            stage=_DEFAULT_STAGE,
            round=None,
            home=home,
            away=away,
            kickoff_utc=_DEFAULT_KICKOFF,
            is_stub=True,
        )

    def get_match_identity(self, match_id: str) -> MatchIdentity:
        fixture = query_fixture(match_id)
        if fixture is None:
            return self._stub_identity(match_id)

        competition = _competition_for(fixture.competition)
        home_name = fixture.home_team or "Home"
        away_name = fixture.away_team or "Away"
        home = TeamIdentity(
            code=_team_code(home_name, "HOME"),
            name=home_name,
            competition=competition,
        )
        away = TeamIdentity(
            code=_team_code(away_name, "AWAY"),
            name=away_name,
            competition=competition,
        )
        return MatchIdentity(
            match_id=fixture.match_id,
            season=SeasonIdentity(
                competition=competition,
                season_key=fixture.season or _DEFAULT_SEASON,
            ),
            stage=fixture.stage or _DEFAULT_STAGE,
            round=None,
            home=home,
            away=away,
            kickoff_utc=fixture.kickoff_utc or _DEFAULT_KICKOFF,
        )

    def _resolve_dry_run_path(self) -> Path | None:
        configured = (config.settings.LOL_DRY_RUN_FIXTURES_PATH or "").strip()
        if configured:
            path = Path(configured)
            if path.is_file():
                return path
            return None
        if _DEFAULT_SAMPLE_PATH.is_file():
            return _DEFAULT_SAMPLE_PATH
        return None

    def sync_schedule(self) -> int:
        if config.settings.LOL_DRY_RUN_IMPORT:
            path = self._resolve_dry_run_path()
            if path is not None:
                return import_lol_series_file(path)

        records = self._source.list_upcoming()
        if not records:
            return 0

        count = 0
        for record in records:
            _upsert_series_record(record)
            count += 1
        return count

    def fetch_schedule(self, filters: ScheduleFilter) -> list[RawMatchData]:
        session = get_kernel_session()
        try:
            query = select(KernelMatchFixture).where(
                or_(
                    KernelMatchFixture.match_id.like("lol-%"),
                    KernelMatchFixture.competition.like("lol%"),
                )
            )
            if filters.competition:
                query = query.where(KernelMatchFixture.competition == filters.competition)
            if filters.status:
                query = query.where(KernelMatchFixture.status == filters.status)
            if filters.stage:
                query = query.where(KernelMatchFixture.stage == filters.stage)
            if filters.limit:
                query = query.limit(filters.limit)

            fixtures = session.execute(query).scalars().all()
            rows: list[RawMatchData] = []
            for fixture in fixtures:
                best_of = _parse_best_of(fixture.venue)
                raw_json: dict = {}
                if best_of is not None:
                    raw_json["best_of"] = best_of
                if fixture.venue:
                    raw_json["venue"] = fixture.venue
                rows.append(
                    RawMatchData(
                        match=self.get_match_identity(fixture.match_id),
                        raw_json=raw_json,
                    )
                )
            return rows
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch LoL schedule: %s", exc)
            return []
        finally:
            session.close()

    def fetch_all_data(self, match: MatchIdentity) -> dict:
        fixture = query_fixture(match.match_id)
        best_of = _parse_best_of(fixture.venue if fixture is not None else None)
        return {
            "team": {},
            "general": {},
            "market": {},
            "player": {},
            "environment": {
                "venue": fixture.venue if fixture is not None else None,
                "is_home_advantage": True,
            },
            "custom": {
                "best_of": best_of,
            },
        }

    def fetch_outcome(self, match_id: str) -> MatchOutcome | None:
        return build_match_outcome(query_result(match_id))

    def fetch_team_data(self, team: TeamIdentity) -> dict:
        return {}

    def fetch_player_data(self, team: TeamIdentity) -> dict:
        return {}

    def fetch_market_data(self, match: MatchIdentity) -> dict:
        return {}
