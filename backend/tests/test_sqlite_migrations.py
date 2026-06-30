"""Tests for the shared SQLite column-migration runner."""
import os
import tempfile
import unittest


class TestApplyMigrations(unittest.TestCase):
    def test_adds_missing_column(self):
        from app.utils.sqlite_db import apply_migrations, reading, writing
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.db")
            with writing(db_path) as conn:
                conn.execute("CREATE TABLE demo (id TEXT PRIMARY KEY)")
                apply_migrations(
                    conn, "demo", 2, {"notes": "TEXT DEFAULT ''"}
                )
            with reading(db_path) as conn:
                cols = {row["name"] for row in conn.execute("PRAGMA table_info(demo)")}
                self.assertIn("notes", cols)

    def test_idempotent_skips_existing_column(self):
        from app.utils.sqlite_db import apply_migrations, reading, writing
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.db")
            with writing(db_path) as conn:
                conn.execute("CREATE TABLE demo (id TEXT PRIMARY KEY, notes TEXT)")
                apply_migrations(
                    conn, "demo", 2, {"notes": "TEXT DEFAULT ''"}
                )
            with reading(db_path) as conn:
                cols = {row["name"] for row in conn.execute("PRAGMA table_info(demo)")}
                self.assertEqual(cols, {"id", "notes"})

    def test_empty_migrations_noop(self):
        from app.utils.sqlite_db import apply_migrations, reading, writing
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.db")
            with writing(db_path) as conn:
                conn.execute("CREATE TABLE demo (id TEXT PRIMARY KEY)")
                apply_migrations(conn, "demo", 1, {})
            with reading(db_path) as conn:
                cols = {row["name"] for row in conn.execute("PRAGMA table_info(demo)")}
                self.assertEqual(cols, {"id"})

    def test_records_schema_version(self):
        from app.utils.sqlite_db import apply_migrations, schema_versions, writing
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.db")
            with writing(db_path) as conn:
                conn.execute("CREATE TABLE demo (id TEXT PRIMARY KEY)")
                apply_migrations(conn, "demo", 5, {})
            versions = schema_versions(db_path)
            self.assertEqual(versions.get("demo"), 5)


if __name__ == "__main__":
    unittest.main()
