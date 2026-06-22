import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.utils import sqlite_db


class SQLiteMaintenanceTests(unittest.TestCase):
    def test_maintain_checkpoints_and_checks_integrity(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "loop.db")
            conn = sqlite3.connect(path)
            try:
                conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, name TEXT)")
                conn.execute("INSERT INTO sample (name) VALUES ('ok')")
                conn.commit()
            finally:
                conn.close()

            result = sqlite_db.maintain(path)

        self.assertTrue(result["ok"])
        self.assertEqual(result["integrity"], ["ok"])
        checkpoint = result["checkpoint"]
        self.assertIn("busy", checkpoint)
        self.assertIn("log", checkpoint)
        self.assertIn("checkpointed", checkpoint)

    def test_wal_checkpoint_rejects_unknown_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "loop.db")
            with self.assertRaises(ValueError):
                sqlite_db.wal_checkpoint(path, mode="BAD")

    def test_schema_versions_records_component_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "loop.db")
            with sqlite_db.writing(path) as conn:
                sqlite_db.record_schema_version(conn, "component_a", 2)
                sqlite_db.record_schema_version(conn, "component_b", 1)

            versions = sqlite_db.schema_versions(path)

        self.assertEqual(versions["component_a"], 2)
        self.assertEqual(versions["component_b"], 1)


if __name__ == "__main__":
    unittest.main()
