"""Protected backfill from finished fixtures into World Cup result facts."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.memory import loop_run_store
from app.models.world_cup_prediction import MatchFixture
from app.services.audit_metadata import normalize_audit_metadata
from app.services.sports_fact_service import (
    WORLD_CUP_TOURNAMENT,
    import_sports_facts,
    load_sports_facts,
    sports_fact_status,
)
from app.utils.prediction_db import close_prediction_session, get_prediction_session

logger = logging.getLogger(__name__)
AUDIT_JOB_NAME = "world_cup_result_fact_backfill"


def list_world_cup_result_fact_backfill_runs(limit: int = 10) -> dict[str, Any]:
    """Return recent audit runs for confirmed result fact backfills."""

    runs = loop_run_store.recent_runs(limit=limit, job_name=AUDIT_JOB_NAME)
    return {
        "status": "ok",
        "job_name": AUDIT_JOB_NAME,
        "count": len(runs),
        "runs": [_audit_run_payload(run) for run in runs],
    }


def run_world_cup_result_fact_backfill(
    session: Session | None = None,
    *,
    dry_run: bool = True,
    confirm: bool = False,
    limit: int = 100,
    audit: bool = True,
    audit_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create missing ``match_result`` facts from finished prediction fixtures.

    The operation is protected: only ``dry_run=False`` with ``confirm=True``
    writes to the sports facts store. Dry-runs do not write audit rows.
    """

    should_close = session is None
    if session is None:
        session = get_prediction_session()

    candidates = _candidate_fixtures(session, limit=limit)
    protected = not dry_run and not confirm
    audit_meta = normalize_audit_metadata(audit_metadata)
    run_id = _start_audit_run(audit and not dry_run and confirm)

    def finish(result: dict[str, Any]) -> dict[str, Any]:
        if run_id:
            result["run_id"] = run_id
        _finish_audit_run(run_id, result)
        return result

    try:
        items = [_candidate_payload(match) for match in candidates["matches"]]
        if dry_run:
            action = "would_import"
        elif protected:
            action = "confirmation_required"
        else:
            action = "imported"
        for item in items:
            item["action"] = action

        imported = 0
        import_result: dict[str, Any] | None = None
        if not dry_run and not protected and items:
            import_result = import_sports_facts(
                {"facts": [item["fact"] for item in items]},
                replace=False,
                default_tournament=WORLD_CUP_TOURNAMENT,
            )
            imported = int(import_result.get("imported", 0))

        return finish({
            "status": "protected" if protected else "ok",
            "dry_run": dry_run,
            "confirm": confirm,
            "protected": protected,
            "finished_fixture_count": candidates["finished_fixture_count"],
            "existing_fact_matches": candidates["existing_fact_matches"],
            "candidate_count": len(items),
            "imported": imported,
            "skipped_existing": candidates["existing_fact_matches"],
            "audit_metadata": audit_meta,
            "items": items,
            "import_result": import_result,
            "fact_store": sports_fact_status(tournament=WORLD_CUP_TOURNAMENT),
        })
    except Exception as exc:
        _finish_audit_run(run_id, {
            "status": "error",
            "dry_run": dry_run,
            "confirm": confirm,
            "protected": protected,
            "candidate_count": len(candidates.get("matches", [])),
            "imported": 0,
            "audit_metadata": audit_meta,
            "error": str(exc),
        })
        raise
    finally:
        if should_close:
            close_prediction_session(session)


def _candidate_fixtures(session: Session, *, limit: int) -> dict[str, Any]:
    finished_matches = session.query(MatchFixture).filter(
        MatchFixture.status == "finished",
        MatchFixture.home_score.isnot(None),
        MatchFixture.away_score.isnot(None),
    ).order_by(MatchFixture.kickoff_utc, MatchFixture.match_id).all()

    existing_match_ids = {
        str(fact.get("match_id"))
        for fact in load_sports_facts(
            tournament=WORLD_CUP_TOURNAMENT,
            kind="match_result",
        )
        if fact.get("match_id")
    }
    matches = [
        match for match in finished_matches
        if str(match.match_id) not in existing_match_ids
    ]
    capped_limit = max(1, int(limit))
    return {
        "finished_fixture_count": len(finished_matches),
        "existing_fact_matches": sum(
            1 for match in finished_matches
            if str(match.match_id) in existing_match_ids
        ),
        "matches": matches[:capped_limit],
    }


def _candidate_payload(match: MatchFixture) -> dict[str, Any]:
    return {
        "match_id": match.match_id,
        "fixture_id": match.fixture_id,
        "home_team": match.home_team,
        "away_team": match.away_team,
        "score": {"home": int(match.home_score), "away": int(match.away_score)},
        "fact": _fixture_result_fact(match),
    }


def _fixture_result_fact(match: MatchFixture) -> dict[str, Any]:
    return {
        "fact_id": f"wc2026:prediction-fixture-result:{match.match_id}",
        "kind": "match_result",
        "tournament": WORLD_CUP_TOURNAMENT,
        "match_id": match.match_id,
        "stage": match.stage,
        "home_team": match.home_team,
        "away_team": match.away_team,
        "status": "finished",
        "score": {"home": int(match.home_score), "away": int(match.away_score)},
        "source": "prediction_fixture_db",
        "confidence": 0.8,
        "observed_at": _observed_at(match),
    }


def _observed_at(match: MatchFixture) -> str:
    value = match.updated_at or match.created_at or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _start_audit_run(enabled: bool) -> str | None:
    if not enabled:
        return None
    try:
        return loop_run_store.start_run(AUDIT_JOB_NAME)
    except Exception:
        logger.exception("Failed to start result fact backfill audit run")
        return None


def _finish_audit_run(run_id: str | None, result: dict[str, Any]) -> None:
    if not run_id:
        return
    try:
        status = "failed" if result.get("status") == "error" else "success"
        loop_run_store.finish_run(
            run_id,
            status,
            result=_audit_summary(result),
            error=result.get("error") if status == "failed" else None,
        )
    except Exception:
        logger.exception("Failed to finish result fact backfill audit run")


def _audit_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": result.get("status"),
        "dry_run": result.get("dry_run"),
        "confirm": result.get("confirm"),
        "protected": result.get("protected"),
        "finished_fixture_count": result.get("finished_fixture_count", 0),
        "existing_fact_matches": result.get("existing_fact_matches", 0),
        "candidate_count": result.get("candidate_count", 0),
        "imported": result.get("imported", 0),
        "audit_metadata": normalize_audit_metadata(result.get("audit_metadata")),
    }


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
        "confirm": result.get("confirm"),
        "protected": result.get("protected"),
        "finished_fixture_count": result.get("finished_fixture_count", 0),
        "existing_fact_matches": result.get("existing_fact_matches", 0),
        "candidate_count": result.get("candidate_count", 0),
        "imported": result.get("imported", 0),
        "audit_metadata": normalize_audit_metadata(result.get("audit_metadata")),
    }
