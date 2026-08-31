"""Load historical matches from kernel DB for Phase 9 optimization."""
from __future__ import annotations

from typing import Any, Callable


def load_sport_matches_for_backtest(
    sport: str,
    *,
    session_factory: Callable[[], Any] | None = None,
) -> list[dict[str, Any]]:
    """Return chronological match dicts suitable for BacktestRunner / EloTimeMachine.

    Joins kernel_match_fixtures with kernel_match_results for the given
    competition/sport code (nba / mlb / nhl). Matches without scores are skipped.
    Rest/form are leakage-safe as-of features (not flat defaults).

    ``session_factory=None`` reads the global kernel session, which is what all
    four production callers want. It is a parameter so that a caller already
    scoped to one specific database -- ``OptimizedParamsStore(db_path=...)``
    reseeding Elo -- reads the matches it is about to replay out of *its* DB
    rather than out of ``settings.KERNEL_DB_FILE``.
    """
    from app.kernel.kernel_db import (
        KernelMatchFixture,
        KernelMatchResult,
        get_kernel_session,
    )
    from app.sports._shared.rest_form import enrich_matches_rest_form

    session = (session_factory or get_kernel_session)()
    try:
        rows = (
            session.query(KernelMatchFixture, KernelMatchResult)
            .join(
                KernelMatchResult,
                KernelMatchFixture.match_id == KernelMatchResult.match_id,
            )
            .filter(KernelMatchFixture.competition == sport)
            .all()
        )
        matches: list[dict[str, Any]] = []
        for fixture, result in rows:
            if result.home_score is None or result.away_score is None:
                continue
            season_raw = fixture.season or "0"
            try:
                season_key: int | str = int(str(season_raw).split("-")[0])
            except (TypeError, ValueError):
                season_key = str(season_raw)
            matches.append({
                "match_id": fixture.match_id,
                "home_team": fixture.home_team,
                "away_team": fixture.away_team,
                "home_score": int(result.home_score),
                "away_score": int(result.away_score),
                "season": season_key,
                "is_playoff": (fixture.stage or "").lower() in {
                    "playoff", "playoffs", "postseason",
                },
                "kickoff_utc": fixture.kickoff_utc,
            })

        matches = enrich_matches_rest_form(matches)
        matches.sort(
            key=lambda m: (
                m["season"],
                m["kickoff_utc"].isoformat() if m.get("kickoff_utc") else "",
                m["match_id"],
            ),
        )
        for m in matches:
            m.pop("kickoff_utc", None)
        return matches
    finally:
        session.close()


def time_series_split(
    matches: list[dict[str, Any]],
    *,
    test_ratio: float = 0.2,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Chronological train/test split (no shuffle)."""
    if not matches:
        return [], []
    n = len(matches)
    if n < 2:
        return [], list(matches)
    test_n = max(1, int(round(n * test_ratio)))
    if test_n >= n:
        test_n = n - 1
    train = matches[: n - test_n]
    test = matches[n - test_n :]
    return train, test
