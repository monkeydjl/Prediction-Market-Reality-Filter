"""API endpoints for prediction analytics and monitoring."""

import logging
import threading
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import Any

from app.api.audit_metadata import get_audit_metadata
from app.api.security import require_write_key
from app.memory import loop_run_store
from app.utils.prediction_db import get_prediction_session_dep
from app.models.world_cup_prediction import (
    MatchFixture,
    MatchPrediction,
    MatchResult,
    PredictionHistory,
)
from app.services.odds_cache_service import OddsCache
from app.services.world_cup_quality_service import (
    apply_consistency_history_repair,
    build_consistency_repair_plan,
    build_quality_loop_report,
    bucket_engine,
    preview_consistency_history_repair,
)
from app.services.world_cup_result_consistency_service import (
    audit_world_cup_result_consistency,
)
from app.services.world_cup_result_fact_backfill_service import (
    list_world_cup_result_fact_backfill_runs,
    run_world_cup_result_fact_backfill,
)
from app.services.world_cup_scoring_service import (
    SCORING_RECONCILE_AUDIT_JOB_NAME,
    score_all_finished_matches,
)
from app.services.world_cup_post_match_backfill_service import (
    list_post_match_backfill_runs,
    run_post_match_backfill,
)
from app.services.world_cup_verified_result_correction_service import (
    apply_verified_result_correction,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analytics", tags=["analytics"])


class VerifiedResultCorrectionRequest(BaseModel):
    match_id: str = Field(..., min_length=1)
    home_score: int = Field(..., ge=0)
    away_score: int = Field(..., ge=0)
    winner: str | None = Field(default=None, min_length=1)
    penalty_score: dict[str, int] | None = None
    source: str = Field(..., min_length=1)
    source_url: str | None = None
    notes: str | None = None
    confirmed: bool = False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_fixture_kickoff(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        kickoff = value
    elif isinstance(value, str) and value.strip():
        raw = value.strip()
        if raw.endswith("Z"):
            raw = f"{raw[:-1]}+00:00"
        try:
            kickoff = datetime.fromisoformat(raw)
        except ValueError:
            return None
    else:
        return None

    if kickoff.tzinfo is None:
        return kickoff.replace(tzinfo=timezone.utc)
    return kickoff.astimezone(timezone.utc)


def _unfinished_past_knockout_issue_details(
    fixtures: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    grace_period: timedelta = timedelta(hours=4),
) -> list[dict[str, Any]]:
    current_time = now or _utcnow()
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    current_time = current_time.astimezone(timezone.utc)

    details: list[dict[str, Any]] = []
    for fixture in fixtures:
        status = str(fixture.get("status") or "").strip().lower()
        if status == "finished":
            continue
        kickoff = _parse_fixture_kickoff(fixture.get("kickoff_utc"))
        if kickoff is None or kickoff + grace_period >= current_time:
            continue
        home = str(fixture.get("home_team") or "").strip()
        away = str(fixture.get("away_team") or "").strip()
        details.append(
            {
                "code": "stale_unfinished_knockout_fixture",
                "severity": "error",
                "match_id": fixture.get("match_id"),
                "stage": fixture.get("stage"),
                "kickoff_utc": kickoff.isoformat(),
                "message": f"{home} vs {away} 已过开球时间，但状态仍为 {status or 'unfinished'}。",
                "action": "先刷新真实比赛数据源，或回填最终比分，再信任冠军概率。",
            }
        )
    return details


def _ambiguous_finished_knockout_issue_details(
    fixtures: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    for fixture in fixtures:
        status = str(fixture.get("status") or "").strip().lower()
        if status != "finished":
            continue
        home = str(fixture.get("home_team") or "").strip()
        away = str(fixture.get("away_team") or "").strip()
        if not home or not away:
            continue
        try:
            home_score = int(fixture.get("home_score"))
            away_score = int(fixture.get("away_score"))
        except (TypeError, ValueError):
            continue
        if home_score != away_score:
            continue
        if _valid_knockout_tiebreaker(fixture):
            continue
        details.append(
            {
                "code": "ambiguous_finished_knockout_fixture",
                "severity": "error",
                "match_id": fixture.get("match_id"),
                "stage": fixture.get("stage"),
                "message": f"{home} vs {away} is finished and tied, but no knockout winner is recorded.",
                "action": "Record a verified winner and penalty score before trusting title probabilities.",
            }
        )
    return details


def _inconsistent_finished_knockout_issue_details(
    fixtures: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    for fixture in fixtures:
        status = str(fixture.get("status") or "").strip().lower()
        if status != "finished":
            continue
        home = str(fixture.get("home_team") or "").strip()
        away = str(fixture.get("away_team") or "").strip()
        winner = str(fixture.get("winner") or "").strip()
        if not home or not away or not winner:
            continue
        try:
            home_score = int(fixture.get("home_score"))
            away_score = int(fixture.get("away_score"))
        except (TypeError, ValueError):
            continue
        if home_score == away_score:
            continue
        score_winner = home if home_score > away_score else away
        if winner.casefold() == score_winner.casefold():
            continue
        details.append(
            {
                "code": "inconsistent_finished_knockout_fixture",
                "severity": "error",
                "match_id": fixture.get("match_id"),
                "stage": fixture.get("stage"),
                "message": f"{home} vs {away} has a winner that conflicts with the final score.",
                "action": "Correct the verified match result before trusting title probabilities.",
            }
        )
    return details


def _valid_knockout_tiebreaker(fixture: dict[str, Any]) -> bool:
    home = str(fixture.get("home_team") or "").strip()
    away = str(fixture.get("away_team") or "").strip()
    winner = str(fixture.get("winner") or "").strip()
    if not home or not away or not winner:
        return False
    if winner.casefold() not in {home.casefold(), away.casefold()}:
        return False

    penalty_score = fixture.get("penalty_score")
    if not isinstance(penalty_score, dict):
        return False
    try:
        home_penalties = int(penalty_score.get("home"))
        away_penalties = int(penalty_score.get("away"))
    except (TypeError, ValueError):
        return False
    if home_penalties < 0 or away_penalties < 0 or home_penalties == away_penalties:
        return False

    penalty_winner = home if home_penalties > away_penalties else away
    return winner.casefold() == penalty_winner.casefold()


def _merge_readiness_issue_details(
    readiness: dict[str, Any],
    issue_details: list[dict[str, Any]],
) -> dict[str, Any]:
    if not issue_details:
        return readiness

    merged = dict(readiness)
    existing_details = list(merged.get("issue_details") or [])
    existing_issues = list(merged.get("issues") or [])
    for issue in issue_details:
        code = issue.get("code")
        if code and code not in existing_issues:
            existing_issues.append(code)
    merged["ok"] = False
    merged["issues"] = existing_issues
    merged["issue_details"] = existing_details + issue_details
    return merged


def _simulation_real_data_readiness(
    fixtures: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    issue_details = _unfinished_past_knockout_issue_details(fixtures, now=now)
    issue_details.extend(_ambiguous_finished_knockout_issue_details(fixtures))
    issue_details.extend(_inconsistent_finished_knockout_issue_details(fixtures))
    return _merge_readiness_issue_details(
        _current_world_cup_real_data_readiness(),
        issue_details,
    )


def _enrich_knockout_fixture_payload(
    fixture: dict[str, Any],
    result_fact: dict[str, Any] | None,
) -> dict[str, Any]:
    if not result_fact:
        return fixture
    enriched = dict(fixture)
    if result_fact.get("status") == "finished":
        enriched["status"] = "finished"
    score = result_fact.get("score")
    if isinstance(score, dict):
        if score.get("home") is not None:
            enriched["home_score"] = score.get("home")
        if score.get("away") is not None:
            enriched["away_score"] = score.get("away")
    winner = str(result_fact.get("winner") or "").strip()
    if winner:
        enriched["winner"] = winner
    penalty_score = result_fact.get("penalty_score")
    if isinstance(penalty_score, dict):
        enriched["penalty_score"] = penalty_score
    return enriched


@router.get("/engine-stats")
async def get_engine_stats(session: Session = Depends(get_prediction_session_dep)) -> dict[str, Any]:
    """Get prediction engine usage statistics."""

    # Count predictions by engine
    predictions = session.query(MatchPrediction).all()

    total = len(predictions)
    engine_keys = ("elo_odds", "hybrid", "integrated", "gbm")
    grouped: dict[str, list[MatchPrediction]] = {engine: [] for engine in engine_keys}
    for prediction in predictions:
        grouped.setdefault(bucket_engine(prediction.prediction_method), []).append(prediction)

    def engine_summary(engine: str) -> dict[str, Any]:
        engine_predictions = grouped[engine]
        count = len(engine_predictions)
        avg_confidence = (
            sum(float(prediction.confidence or 0.0) for prediction in engine_predictions) / count
            if count else 0.0
        )
        return {
            "count": count,
            "percentage": (count / total * 100) if total > 0 else 0,
            "avg_confidence": round(avg_confidence, 3),
        }

    return {
        "total_predictions": total,
        "by_engine": {
            engine: engine_summary(engine)
            for engine in engine_keys
        }
    }


def _utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _fixture_payload(fixture: MatchFixture) -> dict[str, Any]:
    return {
        "match_id": fixture.match_id,
        "fixture_id": fixture.fixture_id,
        "home_team": fixture.home_team,
        "away_team": fixture.away_team,
        "kickoff_utc": fixture.kickoff_utc.isoformat() if fixture.kickoff_utc else None,
        "stage": fixture.stage,
        "status": fixture.status,
    }


@router.get("/prediction-coverage")
async def get_prediction_coverage(
    stale_after_hours: int = Query(
        24,
        ge=1,
        le=24 * 14,
        description="Mark scheduled-match predictions older than this many hours as stale",
    ),
    session: Session = Depends(get_prediction_session_dep),
) -> dict[str, Any]:
    """Report scheduled World Cup fixtures that lack fresh predictions."""
    rows = (
        session.query(MatchFixture, MatchPrediction)
        .outerjoin(MatchPrediction, MatchFixture.match_id == MatchPrediction.match_id)
        .filter(MatchFixture.status == "scheduled")
        .order_by(MatchFixture.kickoff_utc.asc())
        .all()
    )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    stale_cutoff = now - timedelta(hours=stale_after_hours)
    missing: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    predicted_count = 0

    for fixture, prediction in rows:
        if prediction is None:
            missing.append(_fixture_payload(fixture))
            continue

        predicted_count += 1
        last_updated = _utc_naive(prediction.last_updated)
        if last_updated is None or last_updated < stale_cutoff:
            payload = _fixture_payload(fixture)
            payload["prediction_method"] = prediction.prediction_method
            payload["last_updated"] = (
                prediction.last_updated.isoformat() if prediction.last_updated else None
            )
            payload["age_hours"] = (
                round((now - last_updated).total_seconds() / 3600, 2)
                if last_updated is not None
                else None
            )
            stale.append(payload)

    return {
        "status": "ok",
        "coverage_ok": not missing and not stale,
        "scheduled_count": len(rows),
        "predicted_count": predicted_count,
        "missing_count": len(missing),
        "stale_count": len(stale),
        "stale_after_hours": stale_after_hours,
        "missing_predictions": missing,
        "stale_predictions": stale,
    }


@router.get("/quality-loop")
async def get_quality_loop(session: Session = Depends(get_prediction_session_dep)) -> dict[str, Any]:
    """Get prediction quality metrics and confidence calibration buckets."""
    return build_quality_loop_report(session=session)


@router.get("/consistency-repair-plan")
async def get_consistency_repair_plan(
    limit: int = Query(25, ge=1, le=100, description="Maximum consistency issues to inspect"),
    session: Session = Depends(get_prediction_session_dep),
) -> dict[str, Any]:
    """Get a dry-run repair plan for prediction history consistency issues."""
    return build_consistency_repair_plan(session=session, limit=limit)


@router.get("/result-consistency")
async def get_result_consistency(
    limit: int = Query(100, ge=1, le=500, description="Maximum result consistency issues to return"),
    session: Session = Depends(get_prediction_session_dep),
) -> dict[str, Any]:
    """Audit match-result facts against prediction DB fixtures."""
    return audit_world_cup_result_consistency(session=session, limit=limit)


@router.post("/result-fact-backfill")
async def post_result_fact_backfill(
    limit: int = Query(100, ge=1, le=500, description="Maximum missing result facts to backfill"),
    dry_run: bool = Query(True, description="When true, only report result facts that would be imported"),
    confirm: bool = Query(False, description="Must be true with dry_run=false to write facts"),
    _auth: None = Depends(require_write_key),
    audit_metadata: dict[str, str] = Depends(get_audit_metadata),
    session: Session = Depends(get_prediction_session_dep),
) -> dict[str, Any]:
    """Backfill missing match-result facts from finished prediction fixtures."""
    return run_world_cup_result_fact_backfill(
        session=session,
        dry_run=dry_run,
        confirm=confirm,
        limit=limit,
        audit_metadata=audit_metadata,
    )


@router.get("/result-fact-backfill/runs")
async def get_result_fact_backfill_runs(
    limit: int = Query(10, ge=1, le=50, description="Maximum result fact backfill audit runs to return"),
) -> dict[str, Any]:
    """Get recent audit runs for confirmed result fact backfills."""
    return list_world_cup_result_fact_backfill_runs(limit=limit)


@router.get("/consistency-repair-preview")
async def get_consistency_repair_preview(
    history_ids: list[int] = Query(..., description="Prediction history row IDs to preview"),
    session: Session = Depends(get_prediction_session_dep),
) -> dict[str, Any]:
    """Preview method-fill repairs for selected prediction history rows."""
    return preview_consistency_history_repair(history_ids=history_ids, session=session)


@router.post("/consistency-repair")
async def post_consistency_repair(
    history_ids: list[int] = Query(..., description="Prediction history row IDs to repair"),
    dry_run: bool = Query(True, description="When true, only report repair actions"),
    confirm: bool = Query(False, description="Must be true with dry_run=false to write changes"),
    _auth: None = Depends(require_write_key),
    audit_metadata: dict[str, str] = Depends(get_audit_metadata),
    session: Session = Depends(get_prediction_session_dep),
) -> dict[str, Any]:
    """Apply selected method-fill repairs with dry-run and confirmation guards."""
    return apply_consistency_history_repair(
        history_ids=history_ids,
        session=session,
        dry_run=dry_run,
        confirm=confirm,
        audit_metadata=audit_metadata,
    )


@router.post("/reconcile-scoring")
async def reconcile_scoring(
    _auth: None = Depends(require_write_key),
    audit_metadata: dict[str, str] = Depends(get_audit_metadata),
) -> dict[str, Any]:
    """Score finished matches against stored predictions."""
    return score_all_finished_matches(audit_metadata=audit_metadata)


@router.get("/reconcile-scoring/runs")
async def get_scoring_reconcile_runs(
    limit: int = Query(10, ge=1, le=50, description="Maximum audit runs to return"),
) -> dict[str, Any]:
    """Get recent audit runs for scoring reconciliation."""
    runs = loop_run_store.recent_runs(
        limit=limit, job_name=SCORING_RECONCILE_AUDIT_JOB_NAME
    )
    return {
        "status": "ok",
        "job_name": SCORING_RECONCILE_AUDIT_JOB_NAME,
        "count": len(runs),
        "runs": [
            {
                "id": r["id"],
                "status": r["status"],
                "started_at": r.get("started_at"),
                "finished_at": r.get("finished_at"),
                "duration_ms": (r.get("result") or {}).get("duration_ms"),
                "scored": (r.get("result") or {}).get("scored"),
                "skipped": (r.get("result") or {}).get("skipped"),
                "errors": (r.get("result") or {}).get("errors"),
                "trigger_source": (r.get("result") or {}).get("audit_metadata", {}).get("trigger_source"),
                "operator": (r.get("result") or {}).get("audit_metadata", {}).get("operator"),
                "error": r.get("error"),
            }
            for r in runs
        ],
    }


@router.post("/post-match-backfill")
async def post_match_backfill(
    source: str = Query("football-data", description="Fixture/result source to sync before scoring"),
    dry_run: bool = Query(True, description="When true, report candidates without syncing or writing"),
    _auth: None = Depends(require_write_key),
    audit_metadata: dict[str, str] = Depends(get_audit_metadata),
) -> dict[str, Any]:
    """Run the World Cup post-match backfill loop."""
    return run_post_match_backfill(
        source=source,
        dry_run=dry_run,
        sync_first=True,
        audit_metadata=audit_metadata,
    )


@router.post("/verified-result-correction")
async def post_verified_result_correction(
    request: VerifiedResultCorrectionRequest,
    _auth: None = Depends(require_write_key),
    audit_metadata: dict[str, str] = Depends(get_audit_metadata),
    session: Session = Depends(get_prediction_session_dep),
) -> dict[str, Any]:
    """Record an operator-verified final score when the upstream result feed lags."""
    return apply_verified_result_correction(
        match_id=request.match_id,
        home_score=request.home_score,
        away_score=request.away_score,
        winner=request.winner,
        penalty_score=request.penalty_score,
        source=request.source,
        source_url=request.source_url,
        notes=request.notes,
        confirmed=request.confirmed,
        audit_metadata=audit_metadata,
        session=session,
    )


@router.get("/post-match-backfill/runs")
async def get_post_match_backfill_runs(
    limit: int = Query(10, ge=1, le=50, description="Maximum audit runs to return"),
) -> dict[str, Any]:
    """Get recent audit runs for the World Cup post-match backfill loop."""
    return list_post_match_backfill_runs(limit=limit)


@router.get("/accuracy-stats")
async def get_accuracy_stats(session: Session = Depends(get_prediction_session_dep)) -> dict[str, Any]:
    """Get prediction accuracy statistics."""
    # Refresh PredictionAccuracy table from live MatchResult data
    from app.services.world_cup_quality_service import refresh_prediction_accuracy
    try:
        refresh_prediction_accuracy(session)
    except Exception as e:
        logger.warning("Prediction accuracy refresh failed (using stale data): %s", e)

    results = session.query(MatchResult).all()

    if not results:
        return {
            "total_matches": 0,
            "outcome_accuracy": 0.0,
            "avg_score_mae": 0.0,
            "avg_brier_score": 0.0,
            "by_engine": {},
            "by_stage": [],
        }

    # Only count matches that actually have predictions (outcome_correct is not None).
    # Matches without predictions have outcome_correct=None and would dilute accuracy.
    predicted = [r for r in results if r.outcome_correct is not None]
    total = len(results)
    predicted_count = len(predicted)
    outcome_correct = sum(1 for r in predicted if r.outcome_correct == 1)

    # Calculate averages — only over rows that have the metric
    scored_mae = [r.score_mae for r in results if r.score_mae is not None]
    scored_brier = [r.brier_score for r in results if r.brier_score is not None]
    avg_mae = sum(scored_mae) / len(scored_mae) if scored_mae else 0.0
    avg_brier = sum(scored_brier) / len(scored_brier) if scored_brier else 0.0

    # By-stage breakdown from PredictionAccuracy table
    from app.models.world_cup_prediction import PredictionAccuracy
    stage_rows = session.query(PredictionAccuracy).all()
    by_stage = [
        {
            "stage": r.stage,
            "matches_evaluated": r.matches_evaluated,
            "exact_score_pct": round(r.exact_score_correct / r.matches_evaluated, 3) if r.matches_evaluated and r.exact_score_correct else 0,
            "goal_diff_pct": round(r.goal_diff_correct / r.matches_evaluated, 3) if r.matches_evaluated and r.goal_diff_correct else 0,
            "outcome_accuracy": round(r.outcome_accuracy, 3) if r.outcome_accuracy else 0,
            "score_mae": round(r.score_mae, 2) if r.score_mae else None,
        }
        for r in stage_rows
    ]

    return {
        "total_matches": total,
        "predicted_matches": predicted_count,
        "outcome_accuracy": round(outcome_correct / predicted_count, 3) if predicted_count > 0 else 0,
        "avg_score_mae": round(avg_mae, 2),
        "avg_brier_score": round(avg_brier, 3),
        "exact_score_correct": sum(1 for r in predicted if abs(r.home_error or 0) < 0.5 and abs(r.away_error or 0) < 0.5),
        "by_stage": by_stage,
    }


# ---------------------------------------------------------------------------
# Tournament simulation (Monte Carlo)
# ---------------------------------------------------------------------------

_TOURNAMENT_CACHE: dict[str, dict[str, Any]] = {}
_TOURNAMENT_CACHE_TIME: dict[str, float] = {}
_TOURNAMENT_CACHE_TTL = 3600.0  # 1 hour
_TOURNAMENT_CACHE_LOCK = threading.Lock()


def _current_world_cup_real_data_readiness() -> dict[str, Any]:
    from app.services.world_cup_data_source_status_service import world_cup_data_source_status

    return world_cup_data_source_status()["real_data_readiness"]


@router.get("/tournament-simulation")
async def get_tournament_simulation(
    num_simulations: int = Query(default=1000, ge=200, le=10000),
    force_refresh: bool = Query(default=False),
    session: Session = Depends(get_prediction_session_dep),
) -> dict[str, Any]:
    """Run Monte Carlo tournament simulation and return win/progression probabilities."""
    import time as _time

    global _TOURNAMENT_CACHE, _TOURNAMENT_CACHE_TIME

    # Load groups from fixtures before cache lookup so the cache key reflects
    # the current fixture set instead of serving stale probabilities for 1 hour.
    from app.models.world_cup_prediction import MatchFixture

    fixtures = session.query(MatchFixture).filter(
        MatchFixture.group.isnot(None),
        func.lower(MatchFixture.stage) == "group_stage",
    ).all()

    groups: dict[str, list[str]] = {}
    for f in fixtures:
        group_name = (f.group or "").strip()
        if not group_name:
            continue
        groups.setdefault(group_name, [])
        if f.home_team and f.home_team not in groups[group_name]:
            groups[group_name].append(f.home_team)
        if f.away_team and f.away_team not in groups[group_name]:
            groups[group_name].append(f.away_team)

    if len(groups) < 2:
        return {
            "error": "insufficient_group_data",
            "message": "Need at least 2 groups of fixture data to run tournament simulation.",
            "groups_found": len(groups),
        }

    from app.services.sports_fact_service import (
        WORLD_CUP_TOURNAMENT,
        load_sports_facts,
        sports_fact_status,
    )
    from app.services.world_cup_tournament_state_service import (
        build_qualification_state,
        qualification_cache_signature,
    )

    tournament_facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT)
    match_result_facts = {
        str(fact.get("match_id") or "").strip(): fact
        for fact in tournament_facts
        if fact.get("kind") == "match_result" and str(fact.get("match_id") or "").strip()
    }

    knockout_fixtures = session.query(MatchFixture).filter(
        func.lower(MatchFixture.stage) == "round_of_16",
    ).order_by(MatchFixture.kickoff_utc, MatchFixture.match_id).all()
    knockout_fixture_payload = [
        _enrich_knockout_fixture_payload(
            {
                "match_id": fixture.match_id,
                "stage": fixture.stage,
                "status": fixture.status,
                "home_team": fixture.home_team,
                "away_team": fixture.away_team,
                "home_score": fixture.home_score,
                "away_score": fixture.away_score,
                "kickoff_utc": fixture.kickoff_utc.isoformat() if fixture.kickoff_utc else "",
            },
            match_result_facts.get(str(fixture.match_id or "").strip()),
        )
        for fixture in knockout_fixtures
        if fixture.home_team and fixture.away_team
    ]

    qualification_state = build_qualification_state(tournament_facts)
    qualification_signature = qualification_cache_signature(qualification_state)
    groups_signature = "|".join(
        f"{group}:{','.join(sorted(teams))}" for group, teams in sorted(groups.items())
    )
    knockout_signature = "|".join(
        f"{fixture['match_id']}:{fixture['status']}:{fixture['home_team']}:{fixture['away_team']}:"
        f"{fixture['home_score']}-{fixture['away_score']}:"
        f"winner={fixture.get('winner', '')}:penalties={fixture.get('penalty_score', '')}"
        for fixture in knockout_fixture_payload
    )
    simulation_basis = "knockout_fixtures" if knockout_fixture_payload else "group_stage_projection"
    cache_key = (
        f"sim={num_simulations}|basis={simulation_basis}|{qualification_signature}|"
        f"groups={groups_signature}|knockout={knockout_signature}"
    )
    real_data_readiness = _simulation_real_data_readiness(knockout_fixture_payload)

    # Return cached result only if the qualification/fixture signature still matches.
    # A manual "simulate again" action sets force_refresh=true to bypass the cache.
    if not force_refresh:
        with _TOURNAMENT_CACHE_LOCK:
            now = _time.monotonic()
            cached_result = _TOURNAMENT_CACHE.get(cache_key)
            cached_at = _TOURNAMENT_CACHE_TIME.get(cache_key, 0.0)
            if cached_result is not None and (now - cached_at) < _TOURNAMENT_CACHE_TTL:
                return {
                    **cached_result,
                    "real_data_readiness": real_data_readiness,
                    "cached": True,
                }

    # Pre-fetch Elo ratings for active teams only; eliminated teams must remain
    # visible with 0 probability but do not need live Elo lookups.
    from app.services.elo_ratings_service import get_elo_rating

    eliminated_keys = {team.strip().casefold() for team in qualification_state["eliminated_teams"]}
    all_teams = set()
    if knockout_fixture_payload:
        for fixture in knockout_fixture_payload:
            for team in (fixture["home_team"], fixture["away_team"]):
                if team.strip().casefold() not in eliminated_keys:
                    all_teams.add(team)
    else:
        for teams in groups.values():
            all_teams.update(team for team in teams if team.strip().casefold() not in eliminated_keys)

    elo_cache: dict[str, float] = {}
    for team in all_teams:
        try:
            data = await get_elo_rating(team)
            elo_cache[team] = data.get("elo_rating", 1500.0)
        except Exception as e:
            logger.warning("Elo fetch failed for %s, using default 1500: %s", team, e)
            elo_cache[team] = 1500.0

    # Run simulation
    from app.services.world_cup_tournament_simulator import (
        simulate_remaining_knockout,
        simulate_tournament,
    )

    if knockout_fixture_payload:
        result = simulate_remaining_knockout(
            fixtures=knockout_fixture_payload,
            elo_cache=elo_cache,
            num_simulations=num_simulations,
        )
    else:
        result = simulate_tournament(
            groups=groups,
            elo_cache=elo_cache,
            num_simulations=num_simulations,
            eliminated_teams=set(qualification_state["eliminated_teams"]),
        )

    result["cached_at"] = datetime.now(timezone.utc).isoformat()
    result["groups"] = {group: teams for group, teams in groups.items()}
    result["knockout_fixtures"] = knockout_fixture_payload
    result["qualification_state"] = qualification_state
    result["sports_fact_status"] = sports_fact_status(tournament=WORLD_CUP_TOURNAMENT)
    result["real_data_readiness"] = real_data_readiness
    result["cache_signature"] = cache_key
    result["cached"] = False
    result["force_refreshed"] = force_refresh

    # Cache for subsequent requests (under lock)
    with _TOURNAMENT_CACHE_LOCK:
        _TOURNAMENT_CACHE[cache_key] = result
        _TOURNAMENT_CACHE_TIME[cache_key] = _time.monotonic()

    return result


@router.get("/odds-cache-stats")
async def get_odds_cache_stats(session: Session = Depends(get_prediction_session_dep)) -> dict[str, Any]:
    """Get odds API cache statistics."""

    from datetime import datetime, timedelta

    cache_entries = session.query(OddsCache).all()

    if not cache_entries:
        return {
            "total_entries": 0,
            "fresh_count": 0,
            "stale_count": 0,
            "estimated_api_calls_saved": 0
        }

    now = datetime.now(timezone.utc)
    # OddsCache has no expires_at column; derive freshness from cached_at + 1h TTL
    ttl = timedelta(hours=1)
    fresh = sum(1 for e in cache_entries if e.cached_at and (now - e.cached_at) < ttl)
    stale = len(cache_entries) - fresh

    # Estimate API calls saved (each cache hit = 1 API call saved)
    # Assume each entry was used at least once
    api_calls_saved = len(cache_entries)

    return {
        "total_entries": len(cache_entries),
        "fresh_count": fresh,
        "stale_count": stale,
        "estimated_api_calls_saved": api_calls_saved,
        "cache_hit_rate": round(fresh / len(cache_entries), 3) if cache_entries else 0
    }


@router.get("/prediction-timeline")
async def get_prediction_timeline(
    match_id: str,
    session: Session = Depends(get_prediction_session_dep)
) -> dict[str, Any]:
    """Get prediction evolution timeline for a match."""

    history = (
        session.query(PredictionHistory)
        .filter_by(match_id=match_id)
        .order_by(PredictionHistory.timestamp)
        .all()
    )

    if not history:
        return {
            "match_id": match_id,
            "snapshots": []
        }

    snapshots = []
    for entry in history:
        snapshots.append({
            "timestamp": entry.timestamp.isoformat(),
            "predicted_score": {
                "home": entry.predicted_home_score,
                "away": entry.predicted_away_score
            },
            "outcome_probabilities": {
                "home_win": entry.home_win_prob,
                "draw": entry.draw_prob,
                "away_win": entry.away_win_prob
            },
            "confidence": entry.confidence,
            "trigger": entry.trigger,
            "match_minute": entry.match_minute,
            "actual_score": {
                "home": entry.actual_home_score,
                "away": entry.actual_away_score
            } if entry.actual_home_score is not None else None
        })

    return {
        "match_id": match_id,
        "snapshots": snapshots,
        "total_updates": len(snapshots)
    }


@router.get("/system-health")
async def get_system_health(session: Session = Depends(get_prediction_session_dep)) -> dict[str, Any]:
    """Get overall system health metrics."""

    from datetime import datetime, timedelta

    # Recent predictions (last 24 hours)
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    recent_predictions = (
        session.query(MatchPrediction)
        .filter(MatchPrediction.last_updated >= yesterday)
        .count()
    )

    # Cache health
    cache_entries = session.query(OddsCache).count()

    # Data freshness
    latest_prediction = (
        session.query(MatchPrediction)
        .order_by(MatchPrediction.last_updated.desc())
        .first()
    )

    data_age_hours = 0
    if latest_prediction and latest_prediction.last_updated:
        # SQLite stores naive datetimes; attach UTC tzinfo before subtracting.
        last_updated = latest_prediction.last_updated.replace(tzinfo=timezone.utc)
        data_age = datetime.now(timezone.utc) - last_updated
        data_age_hours = data_age.total_seconds() / 3600

    return {
        "status": "healthy" if recent_predictions > 0 else "stale",
        "recent_predictions_24h": recent_predictions,
        "cache_entries": cache_entries,
        "data_freshness_hours": round(data_age_hours, 1),
        "last_update": latest_prediction.last_updated.isoformat() if latest_prediction else None
    }
