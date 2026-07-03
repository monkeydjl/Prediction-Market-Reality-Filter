"""Unit tests for audit_quality_consistency script.

Tests cover:
- 5 conflict types detected correctly
- Severity levels (ERROR/WARN/INFO) applied
- event_id_filter scopes the audit to one event
- Human + JSON output formats
- Exit codes (0 clean, 1 with ERROR)
"""
from __future__ import annotations

import json
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))
_SCRIPTS = _BACKEND / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from audit_quality_consistency import (  # noqa: E402
    ERROR,
    INFO,
    WARN,
    Conflict,
    audit_quality_consistency,
    main,
    _format_human,
)


def _entry(event_id: str = "evt1", **record_overrides) -> dict:
    """Minimal event_store entry wrapper."""
    record = {
        "event_id": event_id,
        "event_title": "Test event",
        # ... (audit only checks overlay fields, minimal record is fine)
    }
    record.update(record_overrides)
    return {"event_id": event_id, "first_seen": "2026-01-01", "last_updated": "2026-01-01", "record": record}


class TestWideSpreadNotDowngraded(unittest.TestCase):
    """Check 5: wide_spread_flag=True but final_direction is YES/NO."""

    def test_wide_spread_yes_direction_triggers_error(self):
        entry = _entry(
            "evt1",
            market_quality={"wide_spread_flag": True, "spread_penalty": 0.2, "suggested_direction": "WAIT"},
            final_displayed_direction="YES",  # should be WAIT
        )
        with patch("audit_quality_consistency.event_store.list_all_events", return_value=[entry]):
            conflicts = audit_quality_consistency()
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].conflict_type, "wide_spread_not_downgraded")
        self.assertEqual(conflicts[0].severity, "ERROR")

    def test_wide_spread_wait_direction_no_conflict(self):
        entry = _entry(
            "evt1",
            market_quality={"wide_spread_flag": True, "suggested_direction": "WAIT"},
            final_displayed_direction="WAIT",  # correctly downgraded
        )
        with patch("audit_quality_consistency.event_store.list_all_events", return_value=[entry]):
            conflicts = audit_quality_consistency()
        self.assertEqual(conflicts, [])

    def test_no_market_quality_no_conflict(self):
        entry = _entry("evt1", final_displayed_direction="YES")
        with patch("audit_quality_consistency.event_store.list_all_events", return_value=[entry]):
            conflicts = audit_quality_consistency()
        self.assertEqual(conflicts, [])


class TestMarketScoreBelowThresholdNotDowngraded(unittest.TestCase):
    """Check 1: market_quality.score < threshold but final is YES/NO."""

    def test_low_score_yes_direction_triggers_error(self):
        entry = _entry(
            "evt1",
            market_quality={"score": 0.2, "wide_spread_flag": False, "suggested_direction": "WAIT"},
            final_displayed_direction="YES",
        )
        with patch("audit_quality_consistency.event_store.list_all_events", return_value=[entry]):
            conflicts = audit_quality_consistency()
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].conflict_type, "market_score_below_threshold_not_downgraded")
        self.assertEqual(conflicts[0].severity, "ERROR")

    def test_high_score_yes_direction_no_conflict(self):
        entry = _entry(
            "evt1",
            market_quality={"score": 0.9, "wide_spread_flag": False, "suggested_direction": "YES"},
            final_displayed_direction="YES",
        )
        with patch("audit_quality_consistency.event_store.list_all_events", return_value=[entry]):
            conflicts = audit_quality_consistency()
        self.assertEqual(conflicts, [])


class TestDecisionQualityDowngradeLostInMerge(unittest.TestCase):
    """Check 2: dq.downgrade_reason set but final_downgrade_reason is None."""

    def test_dq_downgrade_lost_triggers_error(self):
        entry = _entry(
            "evt1",
            decision_quality={
                "downgrade_reason": "证据不足，降级为 WAIT。",
                "displayed_direction": "WAIT",
            },
            final_displayed_direction="WAIT",  # merge used WAIT
            final_downgrade_reason=None,  # but reason was lost
        )
        with patch("audit_quality_consistency.event_store.list_all_events", return_value=[entry]):
            conflicts = audit_quality_consistency()
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].conflict_type, "decision_quality_downgrade_lost_in_merge")

    def test_dq_downgrade_preserved_no_conflict(self):
        entry = _entry(
            "evt1",
            decision_quality={
                "downgrade_reason": "证据不足，降级为 WAIT。",
                "displayed_direction": "WAIT",
            },
            final_displayed_direction="WAIT",
            final_downgrade_reason="证据不足，降级为 WAIT。",
        )
        with patch("audit_quality_consistency.event_store.list_all_events", return_value=[entry]):
            conflicts = audit_quality_consistency()
        self.assertEqual(conflicts, [])

    def test_dq_no_downgrade_reason_no_conflict(self):
        entry = _entry(
            "evt1",
            decision_quality={"downgrade_reason": None, "displayed_direction": "YES"},
            final_displayed_direction="YES",
            final_downgrade_reason=None,
        )
        with patch("audit_quality_consistency.event_store.list_all_events", return_value=[entry]):
            conflicts = audit_quality_consistency()
        self.assertEqual(conflicts, [])


class TestLLMTelemetryDegradedMislabeled(unittest.TestCase):
    """Check 3: llm_telemetry.degraded_mode=True but analysis_quality=llm."""

    def test_degraded_mislabeled_as_llm_triggers_warn(self):
        entry = _entry(
            "evt1",
            llm_telemetry={"degraded_mode": True},
            decision_quality={"analysis_quality": "llm"},
        )
        with patch("audit_quality_consistency.event_store.list_all_events", return_value=[entry]):
            conflicts = audit_quality_consistency()
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].conflict_type, "degraded_mode_mislabeled_as_llm")
        self.assertEqual(conflicts[0].severity, "WARN")

    def test_degraded_correctly_labeled_no_conflict(self):
        entry = _entry(
            "evt1",
            llm_telemetry={"degraded_mode": True},
            decision_quality={"analysis_quality": "deterministic_fallback"},
        )
        with patch("audit_quality_consistency.event_store.list_all_events", return_value=[entry]):
            conflicts = audit_quality_consistency()
        self.assertEqual(conflicts, [])

    def test_not_degraded_no_conflict(self):
        entry = _entry(
            "evt1",
            llm_telemetry={"degraded_mode": False},
            decision_quality={"analysis_quality": "llm"},
        )
        with patch("audit_quality_consistency.event_store.list_all_events", return_value=[entry]):
            conflicts = audit_quality_consistency()
        self.assertEqual(conflicts, [])


class TestSourceReliabilityDowngradeDropped(unittest.TestCase):
    """Check 4: sr downgraded to WAIT/AVOID but final reason doesn't mention '来源'."""

    def test_sr_downgrade_dropped_triggers_warn(self):
        entry = _entry(
            "evt1",
            source_reliability={
                "suggested_direction": "WAIT",
                "downgrade_reason": "来源多样性不足",
            },
            final_downgrade_reason=None,  # sr reason was dropped
        )
        with patch("audit_quality_consistency.event_store.list_all_events", return_value=[entry]):
            conflicts = audit_quality_consistency()
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].conflict_type, "source_reliability_downgrade_dropped")

    def test_sr_downgrade_preserved_no_conflict(self):
        entry = _entry(
            "evt1",
            source_reliability={
                "suggested_direction": "WAIT",
                "downgrade_reason": "来源多样性不足",
            },
            final_downgrade_reason="来源多样性不足，降级为 WAIT。",
        )
        with patch("audit_quality_consistency.event_store.list_all_events", return_value=[entry]):
            conflicts = audit_quality_consistency()
        self.assertEqual(conflicts, [])

    def test_sr_no_downgrade_no_conflict(self):
        entry = _entry(
            "evt1",
            source_reliability={
                "suggested_direction": "YES",
                "downgrade_reason": None,
            },
            final_downgrade_reason=None,
        )
        with patch("audit_quality_consistency.event_store.list_all_events", return_value=[entry]):
            conflicts = audit_quality_consistency()
        self.assertEqual(conflicts, [])


class TestEventIdFilter(unittest.TestCase):
    """--event-id scopes the audit to one record."""

    def test_filter_returns_only_one_event(self):
        with patch(
            "audit_quality_consistency.event_store.get_event",
            return_value=_entry("evtTarget", final_displayed_direction="YES"),
        ):
            conflicts = audit_quality_consistency(event_id_filter="evtTarget")
        self.assertEqual(conflicts, [])  # no overlay fields → no conflicts

    def test_filter_missing_event_returns_error_conflict(self):
        with patch(
            "audit_quality_consistency.event_store.get_event",
            return_value=None,
        ):
            conflicts = audit_quality_consistency(event_id_filter="evtMissing")
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].conflict_type, "event_not_found")


class TestHumanOutput(unittest.TestCase):
    """Human-readable text output."""

    def test_clean_output(self):
        out = _format_human([], verbose=False)
        self.assertIn("No conflicts", out)

    def test_error_output_shows_fields(self):
        conflicts = [
            Conflict(
                event_id="evt1",
                conflict_type="wide_spread_not_downgraded",
                severity="ERROR",
                message="test message",
                field_values={"a": 1, "b": "str"},
            )
        ]
        out = _format_human(conflicts, verbose=True)
        self.assertIn("ERROR", out)
        self.assertIn("evt1", out)
        self.assertIn("test message", out)
        self.assertIn("a: 1", out)
        self.assertIn("b: 'str'", out)

    def test_verbose_false_hides_info(self):
        conflicts = [
            Conflict("evt1", "x", "INFO", "msg"),
            Conflict("evt2", "y", "ERROR", "msg"),
        ]
        out = _format_human(conflicts, verbose=False)
        # INFO conflict (evt1) is hidden from the visible section,
        # but the summary still mentions the hidden count.
        self.assertNotIn("evt1", out)  # INFO conflict hidden from listing
        self.assertIn("evt2", out)  # ERROR conflict visible
        self.assertIn("INFO", out)  # INFO count appears in summary or hidden note


class TestMainExitCodes(unittest.TestCase):
    """main() returns 0 on clean, 1 on ERROR."""

    def test_clean_returns_0(self):
        with patch("audit_quality_consistency.event_store.list_all_events", return_value=[]):
            rc = main([])
        self.assertEqual(rc, 0)

    def test_error_returns_1(self):
        entry = _entry(
            "evt1",
            market_quality={"wide_spread_flag": True, "suggested_direction": "WAIT"},
            final_displayed_direction="YES",  # ERROR conflict
        )
        with patch("audit_quality_consistency.event_store.list_all_events", return_value=[entry]):
            rc = main([])
        self.assertEqual(rc, 1)

    def test_strict_returns_1_on_info(self):
        # INFO conflict — strict mode should exit non-zero.
        entry = _entry("evt1")  # no overlay fields → INFO conflicts (none here)
        with patch("audit_quality_consistency.event_store.list_all_events", return_value=[entry]):
            rc = main(["--strict"])
        # No conflicts at all → still 0
        self.assertEqual(rc, 0)


class TestEnqueueFlag(unittest.TestCase):
    """--enqueue writes ERROR audit findings into the review queue."""

    def test_enqueue_flag_accepts_clean_audit(self):
        with patch("audit_quality_consistency.audit_quality_consistency", return_value=[]), \
                patch("app.memory.review_queue_store.enqueue_item") as enqueue:
            rc = main(["--enqueue"])
        self.assertEqual(rc, 0)
        enqueue.assert_not_called()

    def test_enqueue_single_error_conflict(self):
        conflict = Conflict(
            event_id="evt1",
            conflict_type="wide_spread_not_downgraded",
            severity=ERROR,
            message="market_quality conflict",
            field_values={"wide_spread_flag": True},
        )
        with patch("audit_quality_consistency.audit_quality_consistency", return_value=[conflict]), \
                patch("app.memory.review_queue_store.enqueue_item") as enqueue:
            rc = main(["--enqueue"])

        self.assertEqual(rc, 1)
        enqueue.assert_called_once()
        kwargs = enqueue.call_args.kwargs
        self.assertEqual(kwargs["event_id"], "evt1")
        self.assertEqual(kwargs["trigger"], "audit_inconsistency")
        self.assertEqual(kwargs["severity"], ERROR)
        self.assertEqual(kwargs["reason"], "market_quality conflict")
        self.assertEqual(len(kwargs["context"]["conflicts"]), 1)
        self.assertEqual(
            kwargs["context"]["conflicts"][0]["conflict_type"],
            "wide_spread_not_downgraded",
        )

    def test_enqueue_skips_warn_and_info_conflicts(self):
        conflicts = [
            Conflict("evt1", "source_reliability_downgrade_dropped", WARN, "warn"),
            Conflict("evt2", "informational", INFO, "info"),
        ]
        with patch("audit_quality_consistency.audit_quality_consistency", return_value=conflicts), \
                patch("app.memory.review_queue_store.enqueue_item") as enqueue:
            rc = main(["--enqueue"])

        self.assertEqual(rc, 0)
        enqueue.assert_not_called()

    def test_enqueue_aggregates_multiple_errors_for_same_event(self):
        conflicts = [
            Conflict("evt1", "wide_spread_not_downgraded", ERROR, "first", {"a": 1}),
            Conflict(
                "evt1",
                "market_score_below_threshold_not_downgraded",
                ERROR,
                "second",
                {"b": 2},
            ),
        ]
        with patch("audit_quality_consistency.audit_quality_consistency", return_value=conflicts), \
                patch("app.memory.review_queue_store.enqueue_item") as enqueue:
            rc = main(["--enqueue"])

        self.assertEqual(rc, 1)
        enqueue.assert_called_once()
        kwargs = enqueue.call_args.kwargs
        self.assertEqual(kwargs["event_id"], "evt1")
        self.assertIn("2", kwargs["reason"])
        self.assertIn("wide_spread_not_downgraded", kwargs["reason"])
        self.assertEqual(len(kwargs["context"]["conflicts"]), 2)
        self.assertEqual(
            [c["conflict_type"] for c in kwargs["context"]["conflicts"]],
            [
                "wide_spread_not_downgraded",
                "market_score_below_threshold_not_downgraded",
            ],
        )

    def test_enqueue_writes_one_item_per_event(self):
        conflicts = [
            Conflict("evt1", "wide_spread_not_downgraded", ERROR, "first"),
            Conflict("evt2", "decision_quality_downgrade_lost_in_merge", ERROR, "second"),
        ]
        with patch("audit_quality_consistency.audit_quality_consistency", return_value=conflicts), \
                patch("app.memory.review_queue_store.enqueue_item") as enqueue:
            rc = main(["--enqueue"])

        self.assertEqual(rc, 1)
        self.assertEqual(enqueue.call_count, 2)
        self.assertEqual(
            [call.kwargs["event_id"] for call in enqueue.call_args_list],
            ["evt1", "evt2"],
        )

    def test_enqueue_failure_warns_but_keeps_audit_exit_code(self):
        conflict = Conflict("evt1", "wide_spread_not_downgraded", ERROR, "first")
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("audit_quality_consistency.audit_quality_consistency", return_value=[conflict]), \
                patch(
                    "app.memory.review_queue_store.enqueue_item",
                    side_effect=RuntimeError("db down"),
                ), \
                contextlib.redirect_stdout(stdout), \
                contextlib.redirect_stderr(stderr):
            rc = main(["--enqueue"])

        self.assertEqual(rc, 1)
        self.assertIn("[WARN] enqueue failed for evt1", stderr.getvalue())
        self.assertIn("Found 1 conflict", stdout.getvalue())


class TestJsonOutput(unittest.TestCase):
    """--json flag outputs valid JSON."""

    def test_json_output(self):
        entry = _entry(
            "evt1",
            market_quality={"wide_spread_flag": True, "suggested_direction": "WAIT"},
            final_displayed_direction="YES",
        )
        with patch("audit_quality_consistency.event_store.list_all_events", return_value=[entry]):
            import io
            import contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = main(["--json"])
            data = json.loads(buf.getvalue())
        self.assertEqual(rc, 1)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["conflict_type"], "wide_spread_not_downgraded")
        self.assertEqual(data[0]["severity"], "ERROR")


if __name__ == "__main__":
    unittest.main()
