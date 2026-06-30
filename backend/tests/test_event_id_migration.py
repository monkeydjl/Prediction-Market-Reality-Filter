import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts import migrate_event_ids


def _ids(question: str) -> tuple[str, str]:
    event_id = migrate_event_ids._event_id(question)
    return event_id[:12], event_id


def _event_entry(event_id: str, question: str) -> dict:
    return {
        "event_id": event_id,
        "first_seen": "t0",
        "last_updated": "t1",
        "record": {
            "event_id": event_id,
            "event_title": question,
            "event_summary": "summary",
        },
    }


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _init_loop_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE predictions (
                id TEXT PRIMARY KEY,
                event_id TEXT NOT NULL UNIQUE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE event_market_links (
                id TEXT PRIMARY KEY,
                event_id TEXT NOT NULL,
                contract_id TEXT NOT NULL DEFAULT '',
                UNIQUE(event_id, contract_id)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _event_ids_from_table(path: Path, table: str) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return [
            row[0]
            for row in conn.execute(
                f"SELECT event_id FROM {table} ORDER BY event_id"
            ).fetchall()
        ]
    finally:
        conn.close()


class EventIdMigrationTests(unittest.TestCase):
    def test_dry_run_reports_legacy_references_without_writing(self):
        question = "Will the bill pass before June?"
        old_id, new_id = _ids(question)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            event_store = base / "event_store.json"
            audit = base / "event_audit.jsonl"
            db = base / "v2_loop.db"
            _write_json(event_store, {old_id: _event_entry(old_id, question)})
            audit.write_text(
                json.dumps({"event_id": old_id, "event_title": question}) + "\n",
                encoding="utf-8",
            )
            _init_loop_db(db)
            conn = sqlite3.connect(db)
            try:
                conn.execute(
                    "INSERT INTO predictions (id, event_id) VALUES ('p1', ?)",
                    (old_id,),
                )
                conn.execute(
                    """
                    INSERT INTO event_market_links (id, event_id, contract_id)
                    VALUES ('l1', ?, 'c1')
                    """,
                    (old_id,),
                )
                conn.commit()
            finally:
                conn.close()

            report = migrate_event_ids.migrate_event_ids(
                event_store_path=str(event_store),
                event_audit_path=str(audit),
                loop_db_path=str(db),
            )

            self.assertEqual(
                report["mappings"],
                [{"old_event_id": old_id, "new_event_id": new_id}],
            )
            self.assertEqual(report["event_store_updates"], 1)
            self.assertEqual(report["audit_updates"], 1)
            self.assertEqual(report["prediction_updates"], 1)
            self.assertEqual(report["link_updates"], 1)
            self.assertIn(old_id, _read_json(event_store))
            self.assertEqual(_event_ids_from_table(db, "predictions"), [old_id])

    def test_apply_rewrites_event_store_audit_predictions_and_links(self):
        question = "Will Ethereum reach $2,000 in June?"
        old_id, new_id = _ids(question)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            event_store = base / "event_store.json"
            audit = base / "event_audit.jsonl"
            db = base / "v2_loop.db"
            _write_json(event_store, {old_id: _event_entry(old_id, question)})
            audit.write_text(
                "\n".join([
                    json.dumps({"event_id": old_id, "event_title": question}),
                    "not json",
                ]) + "\n",
                encoding="utf-8",
            )
            _init_loop_db(db)
            conn = sqlite3.connect(db)
            try:
                conn.execute(
                    "INSERT INTO predictions (id, event_id) VALUES ('p1', ?)",
                    (old_id,),
                )
                conn.execute(
                    """
                    INSERT INTO event_market_links (id, event_id, contract_id)
                    VALUES ('l1', ?, 'eth-june')
                    """,
                    (old_id,),
                )
                conn.commit()
            finally:
                conn.close()

            migrate_event_ids.migrate_event_ids(
                event_store_path=str(event_store),
                event_audit_path=str(audit),
                loop_db_path=str(db),
                apply=True,
            )

            data = _read_json(event_store)
            self.assertNotIn(old_id, data)
            self.assertEqual(data[new_id]["event_id"], new_id)
            self.assertEqual(data[new_id]["record"]["event_id"], new_id)
            audit_lines = audit.read_text(encoding="utf-8").splitlines()
            self.assertEqual(json.loads(audit_lines[0])["event_id"], new_id)
            self.assertEqual(audit_lines[1], "not json")
            self.assertEqual(_event_ids_from_table(db, "predictions"), [new_id])
            self.assertEqual(_event_ids_from_table(db, "event_market_links"), [new_id])

    def test_apply_refuses_event_store_target_conflict(self):
        question = "Will the policy pass?"
        old_id, new_id = _ids(question)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            event_store = base / "event_store.json"
            audit = base / "event_audit.jsonl"
            db = base / "v2_loop.db"
            _write_json(
                event_store,
                {
                    old_id: _event_entry(old_id, question),
                    new_id: _event_entry(new_id, question),
                },
            )
            audit.write_text("", encoding="utf-8")
            _init_loop_db(db)

            report = migrate_event_ids.migrate_event_ids(
                event_store_path=str(event_store),
                event_audit_path=str(audit),
                loop_db_path=str(db),
            )
            self.assertTrue(report["conflicts"])
            with self.assertRaises(RuntimeError):
                migrate_event_ids.migrate_event_ids(
                    event_store_path=str(event_store),
                    event_audit_path=str(audit),
                    loop_db_path=str(db),
                    apply=True,
                )

            data = _read_json(event_store)
            self.assertIn(old_id, data)
            self.assertIn(new_id, data)


if __name__ == "__main__":
    unittest.main()
