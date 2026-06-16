"""
Tests for historical event matching (historical_matching_service) and its
GET /events/{event_id}/similar endpoint.

find_similar is deterministic, so the ranking, exclusions, and exact Jaccard
values are locked directly. The route test goes through the real event store
(temp path) to confirm wiring + the 404.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import events as events_routes
from app.memory import event_store as store
from app.services.historical_matching_service import find_similar


def _entry(event_id, title, estimated=60.0):
    """Minimal event-store entry shape for find_similar (not store-validated)."""
    return {
        "event_id": event_id,
        "last_updated": "2026-06-13T00:00:00+00:00",
        "record": {
            "event_title": title,
            "probability": {
                "estimated": estimated,
                "change": round(estimated - 50, 2),
                "direction": "rising",
            },
        },
    }


class FindSimilarTests(unittest.TestCase):
    QUERY = "Will the Federal Reserve raise interest rates in June?"

    def _entries(self):
        return [
            _entry("self", self.QUERY),
            _entry("fed-jul", "Will the Federal Reserve raise interest rates in July?"),
            _entry("fed-cut", "Will the Fed cut rates next year?"),
            _entry("apple", "Will Apple release a new iPhone?"),
        ]

    def test_ranks_by_overlap_and_excludes_self_and_unrelated(self):
        similar = find_similar(self.QUERY, self._entries(), limit=10, exclude_event_id="self")
        ids = [s["event_id"] for s in similar]
        self.assertEqual(ids[0], "fed-jul")     # highest overlap
        self.assertIn("fed-cut", ids)           # shares "rates"
        self.assertNotIn("apple", ids)          # zero overlap -> excluded
        self.assertNotIn("self", ids)           # query event excluded

    def test_exact_similarity_values(self):
        similar = find_similar(self.QUERY, self._entries(), limit=10, exclude_event_id="self")
        by_id = {s["event_id"]: s for s in similar}
        self.assertEqual(by_id["fed-jul"]["similarity"], 0.714)  # 5/7
        self.assertEqual(by_id["fed-cut"]["similarity"], 0.1)    # 1/10

    def test_empty_query_returns_empty(self):
        self.assertEqual(find_similar("", self._entries(), limit=5), [])

    def test_respects_limit(self):
        similar = find_similar(self.QUERY, self._entries(), limit=1, exclude_event_id="self")
        self.assertEqual([s["event_id"] for s in similar], ["fed-jul"])

    def test_precedent_fields(self):
        top = find_similar(self.QUERY, self._entries(), limit=10, exclude_event_id="self")[0]
        self.assertEqual(set(top.keys()), {
            "event_id", "event_title", "similarity",
            "estimated_probability", "change", "direction", "last_updated",
        })


class CjkFindSimilarTests(unittest.TestCase):
    """Regression for Unicode tokenize: CJK titles used to tokenize to an empty
    set (ASCII-only regex) and never matched. They now split into single chars
    so Chinese events with shared characters produce overlap."""

    def test_cjk_titles_with_shared_chars_match(self):
        query = "比特币年底会突破十万美元吗"
        # Shares 比特币 (and others) with the query.
        entries = [_entry("cjk-stored", "比特币今年能涨到十万吗")]
        similar = find_similar(query, entries, limit=5)
        self.assertEqual(len(similar), 1)
        self.assertEqual(similar[0]["event_id"], "cjk-stored")
        self.assertGreater(similar[0]["similarity"], 0.0)

    def test_cjk_titles_no_shared_chars_excluded(self):
        query = "比特币年底会突破十万美元吗"
        entries = [_entry("cjk-unrelated", "美联储下个月会加息吗")]
        similar = find_similar(query, entries, limit=5)
        self.assertEqual(similar, [])


def _entry_with_entities(event_id, title, entities):
    """An entry whose record carries semantics.entities."""
    entry = _entry(event_id, title)
    entry["record"]["semantics"] = {"entities": entities}
    return entry


class EntityAwareFindSimilarTests(unittest.TestCase):
    """The max(title_jaccard, entity_jaccard) merge: a shared entity set
    rescues a weak title match, and vice versa."""

    def test_shared_entities_match_despite_different_titles(self):
        # Completely different titles; identical entities.
        query = "Will the central bank hike?"
        entries = [
            _entry_with_entities(
                "stored", "Rate decision coming soon", ["Federal Reserve", "Jerome Powell"]
            ),
        ]
        similar = find_similar(
            query, entries, limit=5,
            query_entities=["Federal Reserve", "Jerome Powell"],
        )
        self.assertEqual(len(similar), 1)
        self.assertEqual(similar[0]["event_id"], "stored")

    def test_title_match_still_works_without_entities(self):
        # No entities on either side -> pure title matching (backward compat).
        query = "Will the Fed raise rates in June?"
        entries = [_entry("fed-jul", "Will the Fed raise rates in July?")]
        similar = find_similar(query, entries, limit=5)
        self.assertEqual(len(similar), 1)

    def test_no_overlap_in_either_signal_excluded(self):
        query = "Will Bitcoin reach $100k?"
        entries = [
            _entry_with_entities(
                "stored", "Will the court rule on the tariff?", ["Supreme Court"]
            ),
        ]
        similar = find_similar(
            query, entries, limit=5, query_entities=["Bitcoin"],
        )
        self.assertEqual(similar, [])

    def test_max_merge_uses_stronger_signal(self):
        # Weak title overlap (1 shared token) but full entity overlap -> entity
        # wins, similarity reflects entities (1.0).
        query = "Will Bitcoin rise?"
        entries = [
            _entry_with_entities(
                "stored", "Price target reached", ["Bitcoin"],
            ),
        ]
        similar = find_similar(
            query, entries, limit=5, query_entities=["Bitcoin"],
        )
        self.assertEqual(len(similar), 1)
        self.assertEqual(similar[0]["similarity"], 1.0)


def _full_record(event_id, title):
    """Store-valid EventRecord with a custom title."""
    return {
        "event_id": event_id,
        "event_title": title,
        "event_summary": "summary",
        "probability": {"baseline": 50.0, "estimated": 60.0, "change": 10.0, "direction": "rising"},
        "credibility": {"score": 60, "level": "MEDIUM", "confidence": 0.6,
                        "news_quality": 0.5, "evidence_strength": 0.4, "source_count": 3},
        "impact": {"score": 55, "level": "MEDIUM", "drivers": ["strong_evidence"]},
        "risk": {"level": "LOW", "flags": []},
        "evidence": {"direction": "support", "strength": 0.4, "conflict": 0.1,
                     "freshness": 0.7, "resolution_relevance": 0.5},
        "source": {"type": "manual"},
        "value_score": 30,
        "intelligence_report": {"headline": "h", "why_it_matters": "w",
                                "probability_assessment": "p", "recommended_action": "a"},
    }


class SimilarRouteTests(unittest.TestCase):
    def test_similar_route_ranks_and_excludes(self):
        app = FastAPI()
        app.include_router(events_routes.router, prefix="/events")
        client = TestClient(app)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_store.json")
            with patch.object(store, "_store_path", return_value=path):
                store.save_events([
                    _full_record("fed-jun", "Will the Federal Reserve raise interest rates in June?"),
                    _full_record("fed-jul", "Will the Federal Reserve raise interest rates in July?"),
                    _full_record("apple", "Will Apple release a new iPhone?"),
                ])
                found = client.get("/events/fed-jun/similar")
                missing = client.get("/events/none/similar")
        self.assertEqual(found.status_code, 200)
        body = found.json()
        ids = [s["event_id"] for s in body["similar"]]
        self.assertEqual(ids[0], "fed-jul")
        self.assertNotIn("apple", ids)
        self.assertNotIn("fed-jun", ids)
        self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
