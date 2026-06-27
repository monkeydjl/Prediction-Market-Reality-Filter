import unittest
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.memory import loop_run_store
from app.models.world_cup_prediction import Base, MatchFixture, PredictionHistory
from app.services.world_cup_quality_service import (
    apply_consistency_history_repair,
    build_consistency_repair_plan,
    build_quality_loop_report,
    calibrate_confidence_from_quality,
    preview_consistency_history_repair,
    suggest_integrated_engine_weights,
)
from app.utils import sqlite_db


def naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class WorldCupQualityServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.loop_db_patch = patch.object(
            sqlite_db,
            "loop_db_path",
            return_value=str(Path(self.tmp.name) / "loop.db"),
        )
        self.loop_db_patch.start()
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()
        self.loop_db_patch.stop()
        self.tmp.cleanup()

    def _add_finished_match(
        self,
        match_id: str,
        home_score: int = 2,
        away_score: int = 1,
        *,
        kickoff: datetime | None = None,
    ) -> datetime:
        kickoff = kickoff or naive() - timedelta(hours=2)
        self.session.add(
            MatchFixture(
                match_id=match_id,
                fixture_id=match_id,
                home_team=f"Home {match_id}",
                away_team=f"Away {match_id}",
                kickoff_utc=kickoff,
                venue="Test Stadium",
                stage="GROUP_STAGE",
                status="finished",
                home_score=home_score,
                away_score=away_score,
            )
        )
        return kickoff

    def _add_history(
        self,
        match_id: str,
        kickoff: datetime,
        method: str | None,
        home: float,
        away: float,
        confidence: float,
        *,
        trigger: str = "manual",
        after_kickoff: bool = False,
        timestamp: datetime | None = None,
    ):
        entry = PredictionHistory(
            match_id=match_id,
            timestamp=timestamp
            or (kickoff + timedelta(minutes=5) if after_kickoff else kickoff - timedelta(hours=1)),
            predicted_home_score=home,
            predicted_away_score=away,
            home_win_prob=0.70 if home > away else 0.15,
            draw_prob=0.15,
            away_win_prob=0.70 if away > home else 0.15,
            confidence=confidence,
            trigger=trigger,
            prediction_method=method,
        )
        self.session.add(entry)
        return entry

    def test_scores_latest_applied_prematch_prediction_by_engine(self):
        kickoff = self._add_finished_match("m1", 2, 1)
        self._add_history("m1", kickoff, "elo_only", 2, 1, 0.82)
        self._add_history("m1", kickoff, "hybrid", 0, 1, 0.74)
        self._add_history("m1", kickoff, "integrated", 2, 1, 0.90, trigger="manual_comparison")
        self.session.commit()

        report = build_quality_loop_report(session=self.session)

        self.assertEqual(report["overall"]["samples"], 2)
        self.assertEqual(report["by_engine"]["elo_odds"]["samples"], 1)
        self.assertEqual(report["by_engine"]["elo_odds"]["outcome_accuracy"], 1.0)
        self.assertEqual(report["by_engine"]["hybrid"]["samples"], 1)
        self.assertEqual(report["by_engine"]["hybrid"]["outcome_accuracy"], 0.0)
        self.assertEqual(report["by_engine"]["integrated"]["samples"], 0)
        self.assertEqual(report["counters"]["history_rows_excluded_comparison"], 1)

    def test_quality_report_includes_daily_trends(self):
        first_day = datetime(2026, 6, 14, 18, 0, 0)
        second_day = datetime(2026, 6, 15, 18, 0, 0)
        kickoff_1 = self._add_finished_match("m1", 2, 1, kickoff=first_day)
        kickoff_2 = self._add_finished_match("m2", 0, 1, kickoff=second_day)
        self._add_history("m1", kickoff_1, "hybrid", 2, 1, 0.80)
        self._add_history("m2", kickoff_2, "elo_odds", 2, 1, 0.60)
        self.session.commit()

        report = build_quality_loop_report(session=self.session)

        self.assertEqual(
            [point["date"] for point in report["trends"]["overall"]],
            ["2026-06-14", "2026-06-15"],
        )
        self.assertEqual(report["trends"]["overall"][0]["samples"], 1)
        self.assertEqual(report["trends"]["overall"][0]["outcome_accuracy"], 1.0)
        self.assertEqual(report["trends"]["overall"][0]["avg_brier_score"], 0.135)
        self.assertEqual(report["trends"]["overall"][0]["avg_log_loss"], 0.3567)
        self.assertEqual(report["trends"]["overall"][1]["outcome_accuracy"], 0.0)
        self.assertEqual(report["trends"]["overall"][1]["expected_calibration_error"], 0.6)
        self.assertEqual(len(report["trends"]["by_engine"]["hybrid"]), 1)
        self.assertEqual(report["trends"]["by_engine"]["hybrid"][0]["date"], "2026-06-14")
        self.assertEqual(len(report["trends"]["by_engine"]["elo_odds"]), 1)
        self.assertEqual(report["trends"]["by_engine"]["elo_odds"][0]["date"], "2026-06-15")

    def test_quality_report_flags_same_timestamp_score_conflicts(self):
        kickoff = self._add_finished_match("m1", 2, 1)
        timestamp = kickoff - timedelta(hours=1)
        self._add_history("m1", kickoff, "hybrid", 2, 1, 0.80, timestamp=timestamp)
        self._add_history("m1", kickoff, "hybrid", 1, 2, 0.80, timestamp=timestamp)
        self.session.commit()

        report = build_quality_loop_report(session=self.session)

        self.assertEqual(len(report["consistency_issues"]), 1)
        issue = report["consistency_issues"][0]
        self.assertEqual(issue["type"], "conflicting_same_timestamp_score")
        self.assertEqual(issue["match_id"], "m1")
        self.assertEqual(issue["engine"], "hybrid")
        self.assertEqual(issue["rows"], 2)
        self.assertEqual(issue["variant_count"], 2)
        self.assertFalse(issue["has_unknown_method"])
        self.assertEqual(
            {tuple(variant["predicted_score"].values()) for variant in issue["variants"]},
            {(2.0, 1.0), (1.0, 2.0)},
        )
        self.assertTrue(all(variant["history_ids"] for variant in issue["variants"]))
        self.assertEqual(
            {tuple(variant["methods"]) for variant in issue["variants"]},
            {("hybrid",)},
        )

    def test_consistency_report_marks_unknown_prediction_method(self):
        kickoff = self._add_finished_match("m1", 2, 1)
        timestamp = kickoff - timedelta(hours=1)
        self._add_history("m1", kickoff, None, 2, 1, 0.80, timestamp=timestamp)
        self._add_history("m1", kickoff, None, 1, 2, 0.80, timestamp=timestamp)
        self.session.commit()

        report = build_quality_loop_report(session=self.session)

        self.assertEqual(len(report["consistency_issues"]), 1)
        issue = report["consistency_issues"][0]
        self.assertTrue(issue["has_unknown_method"])
        self.assertEqual(
            {tuple(variant["methods"]) for variant in issue["variants"]},
            {("unknown",)},
        )

    def test_consistency_report_does_not_bucket_unknown_with_known_method(self):
        kickoff = self._add_finished_match("m1", 2, 1)
        timestamp = kickoff - timedelta(hours=1)
        self._add_history("m1", kickoff, None, 2, 1, 0.80, timestamp=timestamp)
        self._add_history("m1", kickoff, "rule_only", 1, 2, 0.80, timestamp=timestamp)
        self.session.commit()

        report = build_quality_loop_report(session=self.session)

        self.assertEqual(report["consistency_issues"], [])

    def test_consistency_repair_plan_is_dry_run_manual_for_unknown_methods(self):
        kickoff = self._add_finished_match("m1", 2, 1)
        timestamp = kickoff - timedelta(hours=1)
        self._add_history("m1", kickoff, None, 2, 1, 0.80, timestamp=timestamp)
        self._add_history("m1", kickoff, None, 1, 2, 0.80, timestamp=timestamp)
        self.session.commit()

        plan = build_consistency_repair_plan(session=self.session)

        self.assertEqual(plan["status"], "ok")
        self.assertTrue(plan["dry_run"])
        self.assertEqual(plan["issue_count"], 1)
        self.assertEqual(plan["auto_fixable"], 0)
        self.assertEqual(plan["manual_review"], 1)
        item = plan["items"][0]
        self.assertFalse(item["can_autofix"])
        self.assertEqual(item["recommended_action"], "manual_review_unknown_method")
        self.assertEqual(item["methods"], ["unknown"])
        self.assertEqual(len(item["history_ids"]), 2)

    def test_consistency_repair_preview_infers_method_from_same_score(self):
        kickoff = self._add_finished_match("m1", 2, 1)
        unknown = self._add_history("m1", kickoff, None, 2.2, 0.4, 0.80)
        known = self._add_history("m1", kickoff, "elo_only", 2.2, 0.4, 0.82)
        self.session.commit()

        preview = preview_consistency_history_repair([unknown.id], session=self.session)

        self.assertEqual(preview["status"], "ok")
        self.assertTrue(preview["dry_run"])
        self.assertEqual(preview["requested"], 1)
        self.assertEqual(preview["inferable"], 1)
        self.assertEqual(preview["manual_review"], 0)
        item = preview["items"][0]
        self.assertEqual(item["history_id"], unknown.id)
        self.assertEqual(item["current_method"], None)
        self.assertEqual(item["inferred_method"], "elo_only")
        self.assertTrue(item["can_apply"])
        self.assertEqual(item["reason"], "same_match_same_score_known_method")
        self.assertEqual(item["source_history_ids"], [known.id])

    def test_consistency_repair_preview_keeps_unmatched_rows_manual(self):
        kickoff = self._add_finished_match("m1", 2, 1)
        unknown = self._add_history("m1", kickoff, None, 2.2, 0.4, 0.80)
        self._add_history("m1", kickoff, "elo_only", 2.3, 0.4, 0.82)
        self.session.commit()

        preview = preview_consistency_history_repair([unknown.id], session=self.session)

        self.assertEqual(preview["inferable"], 0)
        self.assertEqual(preview["manual_review"], 1)
        item = preview["items"][0]
        self.assertFalse(item["can_apply"])
        self.assertEqual(item["reason"], "no_same_score_known_method")
        self.assertEqual(item["source_history_ids"], [])

    def test_consistency_repair_dry_run_does_not_update_history(self):
        kickoff = self._add_finished_match("m1", 2, 1)
        unknown = self._add_history("m1", kickoff, None, 2.2, 0.4, 0.80)
        self._add_history("m1", kickoff, "elo_only", 2.2, 0.4, 0.82)
        self.session.commit()

        result = apply_consistency_history_repair([unknown.id], session=self.session)

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["dry_run"])
        self.assertFalse(result["confirm"])
        self.assertEqual(result["requested"], 1)
        self.assertEqual(result["inferable"], 1)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(result["manual_review"], 0)
        self.assertEqual(result["items"][0]["action"], "would_update")
        self.assertIsNone(
            self.session.query(PredictionHistory).filter_by(id=unknown.id).first().prediction_method
        )
        run = loop_run_store.get_run(result["run_id"])
        self.assertEqual(run["status"], "success")
        self.assertEqual(run["job_name"], "world_cup_consistency_repair")
        self.assertTrue(run["result"]["dry_run"])
        self.assertEqual(run["result"]["updated"], 0)

    def test_consistency_repair_requires_confirmation_for_write(self):
        kickoff = self._add_finished_match("m1", 2, 1)
        unknown = self._add_history("m1", kickoff, None, 2.2, 0.4, 0.80)
        self._add_history("m1", kickoff, "elo_only", 2.2, 0.4, 0.82)
        self.session.commit()

        result = apply_consistency_history_repair(
            [unknown.id],
            session=self.session,
            dry_run=False,
            confirm=False,
        )

        self.assertEqual(result["status"], "protected")
        self.assertTrue(result["protected"])
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["items"][0]["action"], "confirmation_required")
        self.assertIsNone(
            self.session.query(PredictionHistory).filter_by(id=unknown.id).first().prediction_method
        )
        run = loop_run_store.get_run(result["run_id"])
        self.assertEqual(run["status"], "success")
        self.assertFalse(run["result"]["dry_run"])
        self.assertFalse(run["result"]["confirm"])
        self.assertTrue(run["result"]["protected"])

    def test_consistency_repair_updates_only_inferable_rows_when_confirmed(self):
        kickoff = self._add_finished_match("m1", 2, 1)
        inferable = self._add_history("m1", kickoff, None, 2.2, 0.4, 0.80)
        manual = self._add_history("m1", kickoff, None, 3.0, 0.4, 0.80)
        self._add_history("m1", kickoff, "elo_only", 2.2, 0.4, 0.82)
        self.session.commit()

        result = apply_consistency_history_repair(
            [inferable.id, manual.id],
            session=self.session,
            dry_run=False,
            confirm=True,
            audit_metadata={"trigger_source": "unit-test", "operator": "alice"},
        )

        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["dry_run"])
        self.assertTrue(result["confirm"])
        self.assertEqual(result["requested"], 2)
        self.assertEqual(result["inferable"], 1)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(result["manual_review"], 1)
        by_id = {item["history_id"]: item for item in result["items"]}
        self.assertEqual(by_id[inferable.id]["action"], "updated")
        self.assertEqual(by_id[inferable.id]["applied_method"], "elo_only")
        self.assertEqual(by_id[manual.id]["action"], "manual_review")
        self.assertEqual(
            self.session.query(PredictionHistory).filter_by(id=inferable.id).first().prediction_method,
            "elo_only",
        )
        self.assertIsNone(
            self.session.query(PredictionHistory).filter_by(id=manual.id).first().prediction_method
        )
        run = loop_run_store.get_run(result["run_id"])
        self.assertEqual(run["status"], "success")
        self.assertFalse(run["result"]["dry_run"])
        self.assertTrue(run["result"]["confirm"])
        self.assertEqual(run["result"]["updated"], 1)
        self.assertEqual(run["result"]["audit_metadata"]["trigger_source"], "unit-test")
        self.assertEqual(run["result"]["audit_metadata"]["operator"], "alice")

    def test_consistency_repair_skips_rows_that_already_have_method(self):
        kickoff = self._add_finished_match("m1", 2, 1)
        known = self._add_history("m1", kickoff, "elo_only", 2.2, 0.4, 0.80)
        self.session.commit()

        result = apply_consistency_history_repair(
            [known.id],
            session=self.session,
            dry_run=False,
            confirm=True,
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["inferable"], 0)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["manual_review"], 0)
        self.assertEqual(result["items"][0]["action"], "skipped")
        self.assertEqual(result["items"][0]["reason"], "already_has_method")

    def test_consistency_report_ignores_comparison_history(self):
        kickoff = self._add_finished_match("m1", 2, 1)
        timestamp = kickoff - timedelta(hours=1)
        self._add_history("m1", kickoff, "hybrid", 2, 1, 0.80, timestamp=timestamp)
        self._add_history(
            "m1",
            kickoff,
            "elo_only",
            1,
            2,
            0.80,
            trigger="manual_comparison",
            timestamp=timestamp,
        )
        self.session.commit()

        report = build_quality_loop_report(session=self.session)

        self.assertEqual(report["consistency_issues"], [])

    def test_excludes_after_kickoff_history(self):
        kickoff = self._add_finished_match("m1", 2, 1)
        self._add_history("m1", kickoff, "elo_odds", 2, 1, 0.82, after_kickoff=True)
        self.session.commit()

        report = build_quality_loop_report(session=self.session)

        self.assertEqual(report["overall"]["samples"], 0)
        self.assertEqual(report["counters"]["history_rows_excluded_after_kickoff"], 1)

    def test_calibrates_confidence_from_usable_bucket(self):
        for idx in range(6):
            match_id = f"m{idx}"
            kickoff = self._add_finished_match(match_id, 2, 1)
            if idx < 3:
                self._add_history(match_id, kickoff, "hybrid", 2, 1, 0.80)
            else:
                self._add_history(match_id, kickoff, "hybrid", 0, 1, 0.80)
        self.session.commit()

        calibrated = calibrate_confidence_from_quality(
            0.80,
            engine_name="hybrid",
            session=self.session,
        )

        # Bucket accuracy is 3/6 = 0.5, blended 50/50 with raw 0.8.
        self.assertAlmostEqual(calibrated, 0.65)

    def test_integrated_weight_suggestion_requires_component_samples(self):
        kickoff = self._add_finished_match("m1", 2, 1)
        self._add_history("m1", kickoff, "elo_odds", 2, 1, 0.80)
        self.session.commit()

        weights = suggest_integrated_engine_weights(0.70, session=self.session)

        self.assertEqual(weights["source"], "rule_default")
        self.assertEqual(weights["reason"], "insufficient_component_samples")
        self.assertEqual(weights["elo_weight"], 0.70)
        self.assertEqual(weights["hybrid_weight"], 0.30)

    def test_integrated_weight_suggestion_uses_lower_brier_engine(self):
        for idx in range(6):
            match_id = f"m{idx}"
            kickoff = self._add_finished_match(match_id, 2, 1)
            self._add_history(match_id, kickoff, "elo_odds", 0, 1, 0.80)
            self._add_history(match_id, kickoff, "hybrid", 2, 1, 0.80)
        self.session.commit()

        weights = suggest_integrated_engine_weights(0.70, session=self.session)

        self.assertEqual(weights["source"], "historical_brier")
        self.assertLess(weights["elo_weight"], 0.70)
        self.assertGreater(weights["hybrid_weight"], 0.30)


if __name__ == "__main__":
    unittest.main()
