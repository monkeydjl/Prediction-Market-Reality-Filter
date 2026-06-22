"""Tests for event_market_link_store (M0 identity layer).

Covers the SQLite-backed event->market link store: schema auto-creation,
upsert/get round-trip, the fail-closed verified gate, promotion via set_verified,
uniqueness on (event_id, contract_id), and the pending review queue. Each test
points the store at a fresh temp SQLite file via _db_path, so no real v2_loop.db
is touched.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.memory import event_market_link_store as links
from app.utils import sqlite_db


class EventMarketLinkStoreTests(unittest.TestCase):
    def _db(self, tmp):
        return patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db"))

    def test_table_autocreates_on_fresh_db(self):
        with tempfile.TemporaryDirectory() as tmp, self._db(tmp):
            # A read on a brand-new DB must not raise (schema created lazily).
            self.assertEqual(links.list_pending(), [])
            self.assertIsNone(links.get_verified_link("nope"))
            versions = sqlite_db.schema_versions()
        self.assertEqual(versions["event_market_links"], 1)

    def test_upsert_and_get_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp, self._db(tmp):
            stored = links.upsert_link(
                "evt1", market_name="Polymarket", contract_id="c1",
                market_question="Will X happen?", link_method="auto",
                link_confidence=0.9, verified=False,
            )
            self.assertEqual(stored["event_id"], "evt1")
            self.assertEqual(stored["contract_id"], "c1")
            self.assertFalse(stored["verified"])
            got = links.get_link("evt1", "c1")
            self.assertEqual(got["market_question"], "Will X happen?")
            self.assertEqual(got["link_confidence"], 0.9)

    def test_verified_gate_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp, self._db(tmp):
            links.upsert_link("evt1", contract_id="c1", verified=False)
            # Unverified link -> gate returns None (must not be scored).
            self.assertIsNone(links.get_verified_link("evt1"))
            promoted = links.set_verified("evt1", "c1", True)
            self.assertTrue(promoted["verified"])
            verified = links.get_verified_link("evt1")
            self.assertIsNotNone(verified)
            self.assertEqual(verified["contract_id"], "c1")

    def test_upsert_is_unique_per_event_and_contract(self):
        with tempfile.TemporaryDirectory() as tmp, self._db(tmp):
            links.upsert_link("evt1", contract_id="c1", link_confidence=0.5, verified=False)
            links.upsert_link("evt1", contract_id="c1", link_confidence=0.95, verified=True)
            all_links = links.get_links("evt1")
            self.assertEqual(len(all_links), 1)  # updated in place, not duplicated
            self.assertEqual(all_links[0]["link_confidence"], 0.95)
            self.assertTrue(all_links[0]["verified"])

    def test_distinct_contracts_coexist(self):
        with tempfile.TemporaryDirectory() as tmp, self._db(tmp):
            links.upsert_link("evt1", contract_id="c1", verified=True)
            links.upsert_link("evt1", contract_id="c2", verified=False)
            self.assertEqual(len(links.get_links("evt1")), 2)
            # get_verified_link returns the verified one.
            self.assertEqual(links.get_verified_link("evt1")["contract_id"], "c1")

    def test_list_pending_returns_only_unverified(self):
        with tempfile.TemporaryDirectory() as tmp, self._db(tmp):
            links.upsert_link("evtA", contract_id="a", verified=True)
            links.upsert_link("evtB", contract_id="b", verified=False)
            pending = links.list_pending()
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["event_id"], "evtB")

    def test_set_verified_unknown_link_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp, self._db(tmp):
            self.assertIsNone(links.set_verified("ghost", "x", True))


if __name__ == "__main__":
    unittest.main()
