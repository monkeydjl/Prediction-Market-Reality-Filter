"""Protected operator workflow for verified World Cup result corrections."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.memory import loop_run_store
from app.models.world_cup_prediction import MatchFixture
from app.services.audit_metadata import normalize_audit_metadata
from app.services.sports_fact_service import WORLD_CUP_TOURNAMENT, import_sports_facts
from app.services.world_cup_scoring_service import score_finished_match
from app.utils.prediction_db import close_prediction_session, get_prediction_session

logger = logging.getLogger(__name__)

VERIFIED_RESULT_CORRECTION_AUDIT_JOB_NAME = "world_cup_verified_result_correction"


def apply_verified_result_correction(
    *,
    match_id: str,
    home_score: int,
    away_score: int,
    source: str,
    source_url: str | None = None,
    winner: str | None = None,
    penalty_score: dict[str, Any] | None = None,
    notes: str | None = None,
    confirmed: bool = False,
    audit_metadata: dict[str, Any] | None = None,
    session: Session | None = None,
) -> dict[str, Any]:
    """Record a verified real final score and score the stored prediction.

    This is intentionally not a general editing endpoint. It only writes after
    explicit confirmation and provenance, so an operator can resolve a stale
    fixture when the upstream result feed is lagging.
    """

    audit_meta = normalize_audit_metadata(audit_metadata)
    clean_match_id = str(match_id or "").strip()
    clean_source = str(source or "").strip()
    if not clean_match_id:
        return _error_result("missing_match_id", audit_meta)
    if not clean_source:
        return _error_result("missing_source", audit_meta)
    if int(home_score) < 0 or int(away_score) < 0:
        return _error_result("negative_score", audit_meta)

    if not confirmed:
        return {
            "status": "protected",
            "protected": True,
            "confirm": False,
            "match_id": clean_match_id,
            "reason": "confirmation_required",
            "audit_metadata": audit_meta,
        }

    should_close = session is None
    if session is None:
        session = get_prediction_session()

    run_id = _start_audit_run()
    try:
        fixture = session.query(MatchFixture).filter_by(match_id=clean_match_id).first()
        if fixture is None:
            result = _error_result("match_not_found", audit_meta)
            result["match_id"] = clean_match_id
            return _finish(run_id, result)

        validation_error = _validate_knockout_winner(
            fixture,
            home_score=int(home_score),
            away_score=int(away_score),
            winner=winner,
            penalty_score=penalty_score,
        )
        if validation_error:
            result = _error_result(validation_error, audit_meta)
            result["match_id"] = clean_match_id
            return _finish(run_id, result)

        now = datetime.now(timezone.utc)
        fixture.status = "finished"
        fixture.home_score = int(home_score)
        fixture.away_score = int(away_score)
        fixture.updated_at = now
        session.commit()

        scoring = score_finished_match(clean_match_id, session=session)
        fact = _verified_result_fact(
            fixture,
            source=clean_source,
            source_url=source_url,
            notes=notes,
            winner=winner,
            penalty_score=penalty_score,
            audit_metadata=audit_meta,
            observed_at=now,
        )
        fact_import = import_sports_facts(
            {"facts": [fact]},
            replace=False,
            default_tournament=WORLD_CUP_TOURNAMENT,
        )

        result = {
            "status": "ok",
            "protected": False,
            "confirm": True,
            "match_id": clean_match_id,
            "fixture": _fixture_payload(fixture),
            "scoring": scoring,
            "fact": fact,
            "fact_import": fact_import,
            "audit_metadata": audit_meta,
        }
        return _finish(run_id, result)
    except Exception as exc:
        session.rollback()
        logger.exception("Verified result correction failed for %s", clean_match_id)
        result = _error_result(str(exc), audit_meta)
        result["match_id"] = clean_match_id
        return _finish(run_id, result)
    finally:
        if should_close:
            close_prediction_session(session)


def _error_result(reason: str, audit_metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "error",
        "protected": False,
        "confirm": True,
        "reason": reason,
        "audit_metadata": audit_metadata,
    }


def _verified_result_fact(
    fixture: MatchFixture,
    *,
    source: str,
    source_url: str | None,
    notes: str | None,
    winner: str | None,
    penalty_score: dict[str, Any] | None,
    audit_metadata: dict[str, Any],
    observed_at: datetime,
) -> dict[str, Any]:
    fact = {
        "fact_id": f"wc2026:verified-result:{fixture.match_id}",
        "kind": "match_result",
        "tournament": WORLD_CUP_TOURNAMENT,
        "match_id": fixture.match_id,
        "stage": fixture.stage,
        "home_team": fixture.home_team,
        "away_team": fixture.away_team,
        "status": "finished",
        "score": {"home": int(fixture.home_score), "away": int(fixture.away_score)},
        "source": source,
        "source_url": str(source_url or "").strip(),
        "confidence": 1.0,
        "observed_at": observed_at.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
        "notes": _provenance_notes(notes, audit_metadata),
    }
    clean_winner = str(winner or "").strip()
    if clean_winner:
        fact["winner"] = clean_winner
    clean_penalty_score = _normalize_penalty_score(penalty_score)
    if clean_penalty_score:
        fact["penalty_score"] = clean_penalty_score
        fact["penalty_shootout"] = True
    return fact


def _validate_knockout_winner(
    fixture: MatchFixture,
    *,
    home_score: int,
    away_score: int,
    winner: str | None,
    penalty_score: dict[str, Any] | None,
) -> str | None:
    home = str(fixture.home_team or "").strip()
    away = str(fixture.away_team or "").strip()
    clean_winner = str(winner or "").strip()
    if clean_winner and clean_winner.casefold() not in {home.casefold(), away.casefold()}:
        return "winner_must_match_fixture_team"

    normalized_penalty = _normalize_penalty_score(penalty_score)
    if penalty_score is not None and normalized_penalty is None:
        return "invalid_penalty_score"

    if normalized_penalty:
        home_penalties = normalized_penalty["home"]
        away_penalties = normalized_penalty["away"]
        if home_penalties == away_penalties:
            return "penalty_score_must_have_winner"
        penalty_winner = home if home_penalties > away_penalties else away
        if clean_winner and clean_winner.casefold() != penalty_winner.casefold():
            return "winner_must_match_penalty_score"

    if _is_knockout_stage(fixture.stage) and home_score == away_score:
        if not clean_winner:
            return "knockout_draw_requires_winner"
        if not normalized_penalty:
            return "knockout_draw_requires_penalty_score"
    if clean_winner and home_score != away_score:
        score_winner = home if home_score > away_score else away
        if clean_winner.casefold() != score_winner.casefold():
            return "winner_must_match_score"
    return None


def _normalize_penalty_score(value: dict[str, Any] | None) -> dict[str, int] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        return None
    try:
        home = int(value["home"])
        away = int(value["away"])
    except (KeyError, TypeError, ValueError):
        return None
    if home < 0 or away < 0:
        return None
    return {"home": home, "away": away}


def _is_knockout_stage(stage: str | None) -> bool:
    normalized = str(stage or "").strip().lower().replace("-", "_").replace(" ", "_")
    return normalized in {
        "round_of_32",
        "round_of_16",
        "quarterfinal",
        "quarterfinals",
        "semi_final",
        "semifinal",
        "semifinals",
        "third_place",
        "final",
    }


def _provenance_notes(notes: str | None, audit_metadata: dict[str, Any]) -> str:
    parts = []
    clean_notes = str(notes or "").strip()
    if clean_notes:
        parts.append(clean_notes)
    parts.append("verified_result_correction")
    operator = str(audit_metadata.get("operator") or "").strip()
    if operator:
        parts.append(f"operator={operator}")
    trigger_source = str(audit_metadata.get("trigger_source") or "").strip()
    if trigger_source:
        parts.append(f"trigger_source={trigger_source}")
    return "; ".join(parts)


def _fixture_payload(fixture: MatchFixture) -> dict[str, Any]:
    return {
        "match_id": fixture.match_id,
        "fixture_id": fixture.fixture_id,
        "home_team": fixture.home_team,
        "away_team": fixture.away_team,
        "stage": fixture.stage,
        "status": fixture.status,
        "score": {"home": int(fixture.home_score), "away": int(fixture.away_score)},
        "updated_at": fixture.updated_at.isoformat() if fixture.updated_at else None,
    }


def _start_audit_run() -> str | None:
    try:
        return loop_run_store.start_run(VERIFIED_RESULT_CORRECTION_AUDIT_JOB_NAME)
    except Exception:
        logger.exception("Failed to start verified result correction audit run")
        return None


def _finish(run_id: str | None, result: dict[str, Any]) -> dict[str, Any]:
    if run_id:
        result["run_id"] = run_id
        try:
            loop_run_store.finish_run(
                run_id,
                "failed" if result.get("status") == "error" else "success",
                result=_audit_summary(result),
                error=result.get("reason") if result.get("status") == "error" else None,
            )
        except Exception:
            logger.exception("Failed to finish verified result correction audit run")
    return result


def _audit_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": result.get("status"),
        "match_id": result.get("match_id"),
        "confirm": result.get("confirm"),
        "protected": result.get("protected"),
        "fixture": result.get("fixture"),
        "scoring_status": (result.get("scoring") or {}).get("status"),
        "fact_imported": (result.get("fact_import") or {}).get("imported", 0),
        "audit_metadata": normalize_audit_metadata(result.get("audit_metadata")),
    }
