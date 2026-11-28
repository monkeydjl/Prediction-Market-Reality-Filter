"""The eight service-layer stamps that reach a response must carry an offset.

Audit 53.186, the repair of 53.185's open list, plus the site 53.186 deferred
(53.187). The census there found these sites by reading ``app/services``; the AST
rule in ``test_row_timestamp_serialization.py`` cannot see them because it
sweeps only ``app.api.routes.`` and ``app.kernel.`` -- a service is neither a
route nor a store. That is the whole reason this family survived three sweeps.

What is different here, and why a plain call-site sweep still would not have
been enough: the conversion is the *producer's* job, and the dict it returns
**is** the body. Every response model involved declares these fields ``str`` or
``str | None`` (``CalibrationDetail.created_at``, ``QualityConsistencyIssue``,
``PredictionQualitySample.timestamp``, ``PredictionCoverageFixture.updated_at``),
so nothing between the builder and the wire rewrites the string. Scanning the
builder's return value is therefore scanning the wire.

One layer plus two anti-vacuity guards:

* **value level** -- the builder's dict is scanned *recursively*, so a stamp
  added to one of these dicts later is covered without editing this file;
* **the columns really are naive** on the way back out of SQLite -- otherwise
  the conversion is untested rather than unnecessary;
* **each dict really contains a stamp** -- the scan passes trivially on a body
  with no stamps in it.

The envelope is now scanned too. 53.186 left it alone because
``consistency_issues[].timestamp`` was ``_timestamp_bucket``'s grouping key and
still naive: scanning that body would have failed for a reason this file is not
about, and widening the assertion to tolerate one unzoned stamp would have
deleted the guard. 53.187 fixed the bucket, so the whole report can be scanned
without an exception -- which is the point. A guard that needs a carve-out is a
guard that has already been negotiated down.

The shared scan lives in ``test_kernel_timestamp_wire.py`` -- one owner for the
rule "every stamp parses to an aware datetime". This file still runs its own
positive control, so it cannot be made vacuous by an edit over there.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core import config
from app.models.world_cup_prediction import (
    EngineCalibration,
    MatchFixture,
    PredictionHistory,
)
from app.services.engine_auto_tuning_service import get_active_calibration
from app.services.world_cup_post_match_backfill_service import (
    _candidate_payload,
    _stale_unfinished_payload,
)
from app.services.world_cup_quality_service import (
    _build_sample,
    build_quality_loop_report,
    preview_consistency_history_repair,
)
from app.services.world_cup_result_consistency_service import _fixture_snapshot
from app.services.world_cup_verified_result_correction_service import (
    _fixture_payload as _correction_fixture_payload,
)
from app.utils import prediction_db
from tests.test_kernel_timestamp_wire import (
    _assert_every_stamp_is_zoned,
    _stamp_strings,
)

# 13:00 UTC: away from midnight so an eight-hour shift changes the date too.
AT = datetime(2026, 6, 20, 13, 0, tzinfo=timezone.utc)


@pytest.fixture
def prediction_db_file(tmp_path, monkeypatch):
    """A private prediction DB per test.

    ``_engine`` / ``_SessionLocal`` are nulled on both sides of the test: the
    session factory is a module global and ``get_prediction_session`` guards
    only on ``_SessionLocal``, so a leftover factory would hand out sessions
    bound to the previous test's file.
    """
    monkeypatch.setattr(
        config.settings,
        "WORLD_CUP_PREDICTION_DB_FILE",
        str(tmp_path / "world_cup_predictions.db"),
    )
    prediction_db._engine = None
    prediction_db._SessionLocal = None
    prediction_db.init_prediction_db()
    try:
        yield
    finally:
        prediction_db._engine = None
        prediction_db._SessionLocal = None


def _seed() -> None:
    """One fixture, one history row, one calibration -- every builder's input.

    The values written here are **aware**. Everything the wire used to get wrong
    came from SQLite dropping the offset on the way back out, not from a
    careless writer; the seed is aware on purpose so the anti-vacuity test above
    can prove that the round-trip is what loses the offset.
    """
    session = prediction_db.get_prediction_session()
    try:
        session.add(MatchFixture(
            match_id="wc-1",
            fixture_id="fx-1",
            home_team="Norway",
            away_team="France",
            kickoff_utc=AT,
            stage="group_stage",
            status="finished",
            home_score=2,
            away_score=1,
            created_at=AT,
            updated_at=AT,
        ))
        session.add(PredictionHistory(
            match_id="wc-1",
            timestamp=AT,
            predicted_home_score=1.8,
            predicted_away_score=1.2,
            home_win_prob=0.55,
            draw_prob=0.25,
            away_win_prob=0.20,
            confidence=0.7,
            trigger="daily_update",
            prediction_method="hybrid",
        ))
        session.add(EngineCalibration(
            engine_name="hybrid",
            calibration_params={"confidence_deflation": 0.9},
            based_on_matches=12,
            avg_improvement=0.03,
            version=1,
            is_active=1,
            created_at=AT,
            updated_at=AT,
        ))
        session.commit()
    finally:
        prediction_db.close_prediction_session(session)


def _seed_conflict() -> None:
    """A second history row at the same instant with a different score.

    ``_timestamp_bucket`` only appears in the body when a conflict exists: the
    group has to contain two variants before ``issues.append`` runs at all. One
    row would have made the envelope guard below silently vacuous -- it would
    still find ``generated_at`` and pass.
    """
    session = prediction_db.get_prediction_session()
    try:
        session.add(PredictionHistory(
            match_id="wc-1",
            timestamp=AT,
            predicted_home_score=1.2,
            predicted_away_score=1.8,
            home_win_prob=0.20,
            draw_prob=0.25,
            away_win_prob=0.55,
            confidence=0.7,
            trigger="daily_update",
            prediction_method="hybrid",
        ))
        session.commit()
    finally:
        prediction_db.close_prediction_session(session)


def _rows(model):
    """Read a row back through the ORM, i.e. through SQLite's type handling."""
    session = prediction_db.get_prediction_session()
    try:
        return session.query(model).first()
    finally:
        prediction_db.close_prediction_session(session)


def _claim_instant(stamp: str) -> None:
    """A zoned stamp must also be the *same instant*, not just zoned."""
    parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None, stamp
    assert parsed.astimezone(timezone.utc) == AT, stamp


class TestTheColumnsComeBackNaive:
    """Anti-vacuity: if SQLite ever returns aware values, stop trusting this file."""

    @pytest.mark.parametrize("model, column", [
        (MatchFixture, "kickoff_utc"),
        (MatchFixture, "updated_at"),
        (PredictionHistory, "timestamp"),
        (EngineCalibration, "created_at"),
    ])
    def test_column_really_round_trips_without_an_offset(
        self, prediction_db_file, model, column
    ):
        _seed()
        value = getattr(_rows(model), column)
        assert isinstance(value, datetime), value
        assert value.tzinfo is None, (
            f"{model.__name__}.{column} came back aware; the producer conversion "
            "is now untested, not unnecessary"
        )


class TestTheBuildersEmitZonedStamps:
    """One test per producer, because one test per *file* would leave the second
    call site in ``world_cup_quality_service`` and the two in
    ``world_cup_post_match_backfill_service`` unguarded."""

    def test_active_calibration_created_at(self, prediction_db_file):
        _seed()
        payload = get_active_calibration("hybrid")
        assert payload is not None
        _assert_every_stamp_is_zoned(payload)
        _claim_instant(payload["created_at"])

    def test_quality_sample_timestamp(self, prediction_db_file):
        _seed()
        session = prediction_db.get_prediction_session()
        try:
            sample = _build_sample(
                session.query(MatchFixture).first(),
                session.query(PredictionHistory).first(),
            )
        finally:
            prediction_db.close_prediction_session(session)
        _assert_every_stamp_is_zoned(sample)
        _claim_instant(sample["timestamp"])

    def test_consistency_repair_preview_timestamp(self, prediction_db_file):
        _seed()
        session = prediction_db.get_prediction_session()
        try:
            history_id = session.query(PredictionHistory).first().id
            payload = preview_consistency_history_repair([history_id], session=session)
        finally:
            prediction_db.close_prediction_session(session)
        _assert_every_stamp_is_zoned(payload)
        _claim_instant(payload["items"][0]["timestamp"])

    def test_quality_report_consistency_issue_timestamp(self, prediction_db_file):
        """Not a builder -- the report envelope, the site 53.186 deferred.

        ``consistency_issues[].timestamp`` is ``_timestamp_bucket``'s grouping
        key *and* the string the dashboard prints. One value, two jobs, which is
        why it could not be wrapped like the seven above: the truncation that
        makes it a key had to survive the change.
        """
        _seed()
        _seed_conflict()
        session = prediction_db.get_prediction_session()
        try:
            report = build_quality_loop_report(session=session)
        finally:
            prediction_db.close_prediction_session(session)
        assert len(report["consistency_issues"]) == 1, report["consistency_issues"]
        _assert_every_stamp_is_zoned(report)
        _claim_instant(report["consistency_issues"][0]["timestamp"])

    def test_result_consistency_fixture_updated_at(self, prediction_db_file):
        _seed()
        payload = _fixture_snapshot(_rows(MatchFixture))
        _assert_every_stamp_is_zoned(payload)
        _claim_instant(payload["updated_at"])

    def test_verified_correction_fixture_updated_at(self, prediction_db_file):
        _seed()
        payload = _correction_fixture_payload(_rows(MatchFixture))
        _assert_every_stamp_is_zoned(payload)
        _claim_instant(payload["updated_at"])

    def test_post_match_candidate_kickoff(self, prediction_db_file):
        _seed()
        payload = _candidate_payload(_rows(MatchFixture))
        _assert_every_stamp_is_zoned(payload)
        _claim_instant(payload["kickoff_utc"])

    def test_stale_unfinished_kickoff(self, prediction_db_file):
        _seed()
        payload = _stale_unfinished_payload(_rows(MatchFixture))
        _assert_every_stamp_is_zoned(payload)
        _claim_instant(payload["kickoff_utc"])


class TestTheScanSeesWhatItClaimsToGuard:
    """Per producer, not per endpoint: the scan is silent on an empty dict."""

    def test_every_guarded_dict_carries_exactly_one_stamp(self, prediction_db_file):
        _seed()
        session = prediction_db.get_prediction_session()
        try:
            fixture = session.query(MatchFixture).first()
            history = session.query(PredictionHistory).first()
            payloads = {
                "quality_sample": _build_sample(fixture, history),
                "consistency_fixture": _fixture_snapshot(fixture),
                "correction_fixture": _correction_fixture_payload(fixture),
                "candidate": _candidate_payload(fixture),
                "stale_unfinished": _stale_unfinished_payload(fixture),
            }
        finally:
            prediction_db.close_prediction_session(session)
        payloads["active_calibration"] = get_active_calibration("hybrid")
        for name, payload in payloads.items():
            assert len(_stamp_strings(payload)) == 1, (name, payload)

    def test_the_quality_report_carries_one_stamp_per_source(self, prediction_db_file):
        """The envelope again, this time as a count.

        ``_assert_every_stamp_is_zoned`` on the report would still pass if the
        issue list came back empty -- it would just find ``generated_at``. The
        count is what makes the claim cover the bucket.

        Two, not three: the report does **not** carry ``samples``. The per-sample
        dicts are reduced to counters and trend points here (``trends[].date`` is
        a date, not a stamp), so the body's only stamps are its own clock and the
        bucket. ``_build_sample`` is guarded as a producer in its own right.
        """
        _seed()
        _seed_conflict()
        session = prediction_db.get_prediction_session()
        try:
            report = build_quality_loop_report(session=session)
        finally:
            prediction_db.close_prediction_session(session)
        stamps = _stamp_strings(report)
        assert report["generated_at"] in stamps
        assert report["consistency_issues"][0]["timestamp"] in stamps
        assert len(stamps) == 2, stamps


def test_the_zone_assertion_rejects_a_bare_stamp() -> None:
    """Positive control, local on purpose.

    Without this, an edit that makes the shared assertion permissive turns every
    test above into a no-op and nothing goes red here.
    """
    with pytest.raises(AssertionError, match="has no offset"):
        _assert_every_stamp_is_zoned({"created_at": "2026-06-20T13:00:00"})
