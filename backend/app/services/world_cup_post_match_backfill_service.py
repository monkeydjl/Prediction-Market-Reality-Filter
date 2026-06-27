"""Post-match backfill loop for World Cup predictions."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.memory import loop_run_store
from app.models.world_cup_prediction import MatchFixture, MatchResult
from app.services.audit_metadata import normalize_audit_metadata
from app.services.world_cup_match_service import sync_world_cup_fixtures
from app.services.world_cup_quality_service import build_quality_loop_report
from app.services.world_cup_scoring_service import score_finished_match
from app.utils.prediction_db import get_prediction_session, close_prediction_session

logger = logging.getLogger(__name__)
AUDIT_JOB_NAME = "world_cup_post_match_backfill"


def _start_audit_run(enabled: bool) -> str | None:
    if not enabled:
        return None
    try:
        return loop_run_store.start_run(AUDIT_JOB_NAME)
    except Exception:
        logger.exception("Failed to start post-match backfill audit run")
        return None


def _audit_summary(result: dict[str, Any]) -> dict[str, Any]:
    scoring = result.get("scoring") or {}
    quality = result.get("quality") or {}
    sync = result.get("sync") or {}
    return {
        "status": result.get("status"),
        "dry_run": result.get("dry_run"),
        "source": result.get("source"),
        "sync_status": sync.get("status"),
        "candidate_count": result.get("candidate_count", 0),
        "scored": scoring.get("scored", 0),
        "skipped": scoring.get("skipped", 0),
        "errors": scoring.get("errors", 0),
        "quality_samples": quality.get("samples"),
        "audit_metadata": normalize_audit_metadata(result.get("audit_metadata")),
    }


def _finish_audit_run(run_id: str | None, result: dict[str, Any]) -> None:
    if not run_id:
        return
    try:
        status = "success" if result.get("status") == "ok" else "failed"
        loop_run_store.finish_run(
            run_id,
            status,
            result=_audit_summary(result),
            error=result.get("error") if status == "failed" else None,
        )
    except Exception:
        logger.exception("Failed to finish post-match backfill audit run")


def _audit_run_payload(run: dict[str, Any]) -> dict[str, Any]:
    result = run.get("result") or {}
    return {
        "id": run.get("id"),
        "status": run.get("status"),
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "duration_ms": run.get("duration_ms"),
        "error": run.get("error"),
        "dry_run": result.get("dry_run"),
        "source": result.get("source"),
        "sync_status": result.get("sync_status"),
        "candidate_count": result.get("candidate_count", 0),
        "scored": result.get("scored", 0),
        "skipped": result.get("skipped", 0),
        "errors": result.get("errors", 0),
        "quality_samples": result.get("quality_samples"),
        "audit_metadata": normalize_audit_metadata(result.get("audit_metadata")),
    }


def list_post_match_backfill_runs(limit: int = 10) -> dict[str, Any]:
    """Return recent audit runs for the World Cup post-match backfill job."""
    runs = loop_run_store.recent_runs(limit=limit, job_name=AUDIT_JOB_NAME)
    return {
        "status": "ok",
        "job_name": AUDIT_JOB_NAME,
        "count": len(runs),
        "runs": [_audit_run_payload(run) for run in runs],
    }


def _finished_unscored_matches(session: Session) -> list[MatchFixture]:
    finished = session.query(MatchFixture).filter(
        MatchFixture.status == "finished",
        MatchFixture.home_score.isnot(None),
        MatchFixture.away_score.isnot(None),
    ).order_by(MatchFixture.kickoff_utc, MatchFixture.match_id).all()

    candidates: list[MatchFixture] = []
    for match in finished:
        result = session.query(MatchResult).filter_by(match_id=match.match_id).first()
        if result is None or result.brier_score is None:
            candidates.append(match)
    return candidates


def _candidate_payload(match: MatchFixture) -> dict[str, Any]:
    return {
        "match_id": match.match_id,
        "home_team": match.home_team,
        "away_team": match.away_team,
        "kickoff_utc": match.kickoff_utc.isoformat() if match.kickoff_utc else None,
        "actual_score": {"home": match.home_score, "away": match.away_score},
    }


def _quality_snapshot(session: Session) -> dict[str, Any]:
    quality = build_quality_loop_report(session=session)
    return {
        "samples": quality["overall"]["samples"],
        "outcome_accuracy": quality["overall"]["outcome_accuracy"],
        "avg_brier_score": quality["overall"]["avg_brier_score"],
        "expected_calibration_error": quality["overall"]["expected_calibration_error"],
        "trend_days": len(quality.get("trends", {}).get("overall", [])),
        "consistency_issues": len(quality.get("consistency_issues", [])),
    }


def run_post_match_backfill(
    *,
    source: str = "football-data",
    dry_run: bool = True,
    sync_first: bool = True,
    audit: bool = True,
    audit_metadata: dict[str, Any] | None = None,
    session: Session | None = None,
) -> dict[str, Any]:
    """Sync final scores, score finished matches, and return quality snapshot.

    ``dry_run=True`` never writes and does not call external data sources. It
    only reports currently known finished matches that would be scored.
    """
    should_close = session is None
    if session is None:
        session = get_prediction_session()

    run_id = _start_audit_run(audit)
    audit_meta = normalize_audit_metadata(audit_metadata)

    def finish(result: dict[str, Any]) -> dict[str, Any]:
        if run_id:
            result["run_id"] = run_id
        _finish_audit_run(run_id, result)
        return result

    try:
        sync_result: dict[str, Any] = {
            "status": "skipped",
            "reason": "dry_run" if dry_run else "sync_first_false",
        }
        if sync_first and not dry_run:
            sync_result = sync_world_cup_fixtures(source=source)
            if sync_result.get("status") == "error":
                return finish({
                    "status": "error",
                    "step": "fixture_sync",
                    "source": source,
                    "dry_run": dry_run,
                    "sync": sync_result,
                    "error": sync_result.get("error"),
                    "audit_metadata": audit_meta,
                })
            session.expire_all()

        candidates = _finished_unscored_matches(session)

        if dry_run:
            return finish({
                "status": "ok",
                "dry_run": True,
                "source": source,
                "sync": sync_result,
                "candidate_count": len(candidates),
                "candidates": [_candidate_payload(match) for match in candidates],
                "scoring": {"scored": 0, "skipped": 0, "errors": 0},
                "quality": _quality_snapshot(session),
                "audit_metadata": audit_meta,
            })

        scored = 0
        skipped = 0
        errors = 0
        results: list[dict[str, Any]] = []
        for match in candidates:
            result = score_finished_match(match.match_id, session=session)
            if result:
                scored += 1
                results.append(result)
            else:
                errors += 1

        logger.info(
            "Post-match backfill: source=%s candidates=%d scored=%d skipped=%d errors=%d",
            source,
            len(candidates),
            scored,
            skipped,
            errors,
        )

        return finish({
            "status": "ok",
            "dry_run": False,
            "source": source,
            "ran_at": datetime.now(timezone.utc).isoformat(),
            "sync": sync_result,
            "candidate_count": len(candidates),
            "candidates": [_candidate_payload(match) for match in candidates],
            "scoring": {"scored": scored, "skipped": skipped, "errors": errors},
            "results": results,
            "quality": _quality_snapshot(session),
            "audit_metadata": audit_meta,
        })
    except Exception as exc:
        if run_id:
            error_result = {
                "status": "error",
                "dry_run": dry_run,
                "source": source,
                "candidate_count": 0,
                "scoring": {"scored": 0, "skipped": 0, "errors": 1},
                "audit_metadata": audit_meta,
                "error": str(exc),
            }
            _finish_audit_run(run_id, error_result)
        raise
    finally:
        if should_close:
            close_prediction_session(session)
