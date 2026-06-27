import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.memory import loop_run_store
from app.models.world_cup_prediction import Base, MatchFixture
from app.services.sports_fact_service import (
    WORLD_CUP_TOURNAMENT,
    import_sports_facts,
    load_sports_facts,
)
from app.services.world_cup_result_fact_backfill_service import (
    list_world_cup_result_fact_backfill_runs,
    run_world_cup_result_fact_backfill,
)
from app.utils import sqlite_db


class WorldCupResultFactBackfillServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fact_path = str(Path(self.tmp.name) / "sports_facts.json")
        self.fact_patch = patch.object(settings, "SPORTS_FACT_FILE", self.fact_path)
        self.loop_db_patch = patch.object(
            sqlite_db,
            "loop_db_path",
            return_value=str(Path(self.tmp.name) / "loop.db"),
        )
        self.fact_patch.start()
        self.loop_db_patch.start()
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()
        self.loop_db_patch.stop()
        self.fact_patch.stop()
        self.tmp.cleanup()

    def _add_finished_fixture(self, match_id: str = "m1") -> None:
        self.session.add(
            MatchFixture(
                match_id=match_id,
                fixture_id=match_id,
                home_team="Team A",
                away_team="Team B",
                kickoff_utc=datetime(2026, 6, 20, 18, 0, 0),
                venue="Test Stadium",
                stage="GROUP_STAGE",
                status="finished",
                home_score=2,
                away_score=1,
            )
        )
        self.session.commit()

    def test_dry_run_reports_missing_result_fact_without_writing(self):
        self._add_finished_fixture()

        result = run_world_cup_result_fact_backfill(session=self.session)

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["dry_run"])
        self.assertFalse(result["confirm"])
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["imported"], 0)
        self.assertEqual(result["items"][0]["action"], "would_import")
        self.assertEqual(result["items"][0]["fact"]["source"], "prediction_fixture_db")
        self.assertEqual(result["items"][0]["fact"]["score"], {"home": 2, "away": 1})
        self.assertNotIn("run_id", result)
        self.assertFalse(Path(self.fact_path).exists())
        self.assertEqual(list_world_cup_result_fact_backfill_runs()["count"], 0)

    def test_write_requires_confirmation(self):
        self._add_finished_fixture()

        result = run_world_cup_result_fact_backfill(
            session=self.session,
            dry_run=False,
            confirm=False,
        )

        self.assertEqual(result["status"], "protected")
        self.assertTrue(result["protected"])
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["imported"], 0)
        self.assertEqual(result["items"][0]["action"], "confirmation_required")
        self.assertNotIn("run_id", result)
        self.assertFalse(Path(self.fact_path).exists())

    def test_confirmed_write_imports_missing_result_fact_and_audits(self):
        self._add_finished_fixture()

        result = run_world_cup_result_fact_backfill(
            session=self.session,
            dry_run=False,
            confirm=True,
            audit_metadata={"trigger_source": "unit-test", "operator": "alice"},
        )

        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["dry_run"])
        self.assertTrue(result["confirm"])
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["items"][0]["action"], "imported")
        stored = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT, kind="match_result")
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["match_id"], "m1")
        self.assertEqual(stored[0]["score"], {"home": 2, "away": 1})
        self.assertEqual(stored[0]["source"], "prediction_fixture_db")
        self.assertIn("run_id", result)
        run = loop_run_store.get_run(result["run_id"])
        self.assertEqual(run["status"], "success")
        self.assertEqual(run["job_name"], "world_cup_result_fact_backfill")
        self.assertEqual(run["result"]["candidate_count"], 1)
        self.assertEqual(run["result"]["imported"], 1)
        self.assertEqual(run["result"]["audit_metadata"]["trigger_source"], "unit-test")
        self.assertEqual(run["result"]["audit_metadata"]["operator"], "alice")
        history = list_world_cup_result_fact_backfill_runs()
        self.assertEqual(history["status"], "ok")
        self.assertEqual(history["job_name"], "world_cup_result_fact_backfill")
        self.assertEqual(history["count"], 1)
        self.assertEqual(history["runs"][0]["id"], result["run_id"])
        self.assertEqual(history["runs"][0]["status"], "success")
        self.assertFalse(history["runs"][0]["dry_run"])
        self.assertTrue(history["runs"][0]["confirm"])
        self.assertEqual(history["runs"][0]["candidate_count"], 1)
        self.assertEqual(history["runs"][0]["imported"], 1)
        self.assertEqual(history["runs"][0]["audit_metadata"]["trigger_source"], "unit-test")
        self.assertEqual(history["runs"][0]["audit_metadata"]["operator"], "alice")

    def test_existing_result_fact_skips_fixture(self):
        self._add_finished_fixture()
        import_sports_facts({
            "facts": [{
                "fact_id": "existing:m1",
                "kind": "match_result",
                "tournament": WORLD_CUP_TOURNAMENT,
                "match_id": "m1",
                "status": "finished",
                "score": {"home": 2, "away": 1},
                "source": "official",
                "observed_at": "2026-06-20T21:00:00Z",
            }]
        })

        result = run_world_cup_result_fact_backfill(session=self.session)

        self.assertEqual(result["candidate_count"], 0)
        self.assertEqual(result["existing_fact_matches"], 1)
        self.assertEqual(result["skipped_existing"], 1)
        self.assertEqual(result["items"], [])


if __name__ == "__main__":
    unittest.main()
