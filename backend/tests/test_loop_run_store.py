import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.memory import loop_run_store as runs
from app.utils import sqlite_db


class LoopRunStoreTests(unittest.TestCase):
    def test_start_finish_and_last_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                run_id = runs.start_run("event_discover")
                running = runs.get_run(run_id)
                self.assertEqual(running["status"], "running")
                self.assertEqual(running["job_name"], "event_discover")

                finished = runs.finish_run(
                    run_id,
                    "success",
                    result={"count": 3},
                )

                self.assertEqual(finished["status"], "success")
                self.assertEqual(finished["result"]["count"], 3)
                self.assertIsNotNone(finished["finished_at"])
                self.assertGreaterEqual(finished["duration_ms"], 0)
                self.assertEqual(runs.last_run("event_discover")["id"], run_id)
                self.assertEqual(sqlite_db.schema_versions()["loop_runs"], 1)

    def test_failed_run_records_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                run_id = runs.start_run("event_auto_resolve")
                finished = runs.finish_run(run_id, "failed", error="boom")

                self.assertEqual(finished["status"], "failed")
                self.assertEqual(finished["error"], "boom")
                self.assertEqual(finished["result"], {})


if __name__ == "__main__":
    unittest.main()
