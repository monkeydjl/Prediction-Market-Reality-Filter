import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.world_cup_prediction import Base, MatchFixture
from app.services.world_cup_result_consistency_service import (
    audit_world_cup_result_consistency,
)


class WorldCupResultConsistencyServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def _add_fixture(
        self,
        match_id: str,
        *,
        status: str = "finished",
        home_score: int | None = 2,
        away_score: int | None = 1,
    ) -> MatchFixture:
        fixture = MatchFixture(
            match_id=match_id,
            fixture_id=match_id,
            home_team=f"Home {match_id}",
            away_team=f"Away {match_id}",
            kickoff_utc=datetime(2026, 6, 20, 18, 0, 0),
            venue="Test Stadium",
            stage="group_stage",
            status=status,
            home_score=home_score,
            away_score=away_score,
        )
        self.session.add(fixture)
        self.session.commit()
        return fixture

    def _fact(
        self,
        match_id: str,
        *,
        status: str = "finished",
        home: int | str = 2,
        away: int | str = 1,
        observed_at: str = "2026-06-20T21:00:00Z",
        fact_id: str | None = None,
    ) -> dict:
        return {
            "fact_id": fact_id or f"wc2026:match:{match_id}",
            "kind": "match_result",
            "match_id": match_id,
            "status": status,
            "score": {"home": home, "away": away},
            "source": "test",
            "observed_at": observed_at,
        }

    def test_consistent_finished_match_has_no_issues(self):
        self._add_fixture("m1")

        result = audit_world_cup_result_consistency(
            session=self.session,
            facts=[self._fact("m1", status="FT", home="2", away="1")],
        )

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["source"], "provided_facts")
        self.assertIsNone(result["fact_store"])
        self.assertEqual(result["fact_count"], 1)
        self.assertEqual(result["fixture_count"], 1)
        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["issue_count"], 0)
        self.assertEqual(result["issues"], [])

    def test_reports_fixture_missing_in_prediction_db(self):
        result = audit_world_cup_result_consistency(
            session=self.session,
            facts=[self._fact("m1")],
        )

        self.assertEqual(result["issue_count"], 1)
        issue = result["issues"][0]
        self.assertEqual(issue["type"], "fixture_missing_in_prediction_db")
        self.assertEqual(issue["severity"], "warn")
        self.assertEqual(issue["match_id"], "m1")
        self.assertIsNone(issue["fixture"])
        self.assertEqual(issue["fact"]["score"], {"home": 2, "away": 1})

    def test_reports_result_fact_missing_for_finished_fixture(self):
        self._add_fixture("m1")

        result = audit_world_cup_result_consistency(
            session=self.session,
            facts=[],
        )

        self.assertEqual(result["issue_count"], 1)
        issue = result["issues"][0]
        self.assertEqual(issue["type"], "result_fact_missing_for_finished_fixture")
        self.assertEqual(issue["match_id"], "m1")
        self.assertIsNone(issue["fact"])
        self.assertEqual(issue["fixture"]["status"], "finished")

    def test_reports_status_mismatch(self):
        self._add_fixture("m1", status="scheduled", home_score=None, away_score=None)

        result = audit_world_cup_result_consistency(
            session=self.session,
            facts=[self._fact("m1", status="finished")],
        )

        self.assertEqual(result["issue_count"], 1)
        issue = result["issues"][0]
        self.assertEqual(issue["type"], "status_mismatch")
        self.assertEqual(issue["fact"]["status"], "finished")
        self.assertEqual(issue["fixture"]["status"], "scheduled")

    def test_reports_score_mismatch(self):
        self._add_fixture("m1", status="finished", home_score=1, away_score=1)

        result = audit_world_cup_result_consistency(
            session=self.session,
            facts=[self._fact("m1", status="finished", home=2, away=1)],
        )

        self.assertEqual(result["issue_count"], 1)
        issue = result["issues"][0]
        self.assertEqual(issue["type"], "score_mismatch")
        self.assertEqual(issue["severity"], "error")
        self.assertEqual(issue["fact"]["score"], {"home": 2, "away": 1})
        self.assertEqual(issue["fixture"]["score"], {"home": 1, "away": 1})

    def test_uses_latest_result_fact_per_match(self):
        self._add_fixture("m1", status="finished", home_score=2, away_score=1)

        result = audit_world_cup_result_consistency(
            session=self.session,
            facts=[
                self._fact(
                    "m1",
                    home=0,
                    away=0,
                    observed_at="2026-06-20T20:00:00Z",
                    fact_id="older",
                ),
                self._fact(
                    "m1",
                    home=2,
                    away=1,
                    observed_at="2026-06-20T21:00:00Z",
                    fact_id="newer",
                ),
            ],
        )

        self.assertEqual(result["issue_count"], 0)

    def test_limits_returned_issues_without_hiding_total_count(self):
        result = audit_world_cup_result_consistency(
            session=self.session,
            facts=[self._fact("m1"), self._fact("m2")],
            limit=1,
        )

        self.assertEqual(result["issue_count"], 2)
        self.assertEqual(result["returned_issue_count"], 1)
        self.assertEqual(len(result["issues"]), 1)

    def test_includes_fact_store_status_for_stored_fact_source(self):
        result = audit_world_cup_result_consistency(session=self.session)

        self.assertEqual(result["source"], "stored_sports_facts")
        self.assertIsNotNone(result["fact_store"])
        self.assertIn("configured_path", result["fact_store"])
        self.assertIn("exists", result["fact_store"])


if __name__ == "__main__":
    unittest.main()
