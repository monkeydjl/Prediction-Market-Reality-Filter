import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.memory import event_store
from app.services.event_category_service import backfill_event_categories


def _record(event_id: str, title: str, category: str | None = "unknown") -> dict:
    record = {
        "event_id": event_id,
        "event_title": title,
        "event_summary": "summary",
        "probability": {
            "baseline": 50.0,
            "estimated": 55.0,
            "change": 5.0,
            "direction": "rising",
        },
        "credibility": {
            "score": 60,
            "level": "MEDIUM",
            "confidence": 0.6,
            "news_quality": 0.5,
            "evidence_strength": 0.4,
            "source_count": 3,
        },
        "impact": {"score": 55, "level": "MEDIUM", "drivers": []},
        "risk": {"level": "LOW", "flags": []},
        "evidence": {
            "direction": "supports",
            "strength": 0.4,
            "conflict": 0.1,
            "freshness": 0.7,
            "resolution_relevance": 0.5,
        },
        "source": {"type": "prediction_market", "platform": "Polymarket"},
        "value_score": 50,
        "intelligence_report": {
            "headline": "h",
            "why_it_matters": "w",
            "probability_assessment": "p",
            "recommended_action": "a",
        },
    }
    if category is not None:
        record["legacy_analysis"] = {"base_rate_category": category}
    return record


class EventCategoryBackfillTests(unittest.TestCase):
    def test_dry_run_reports_unknown_events_that_can_be_classified(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_store.json")
            with patch.object(event_store, "_store_path", return_value=path):
                event_store.save_events([
                    _record("btc", "Will the price of Bitcoin be above $58,000 on July 4?"),
                    _record("known", "Will OpenAI release GPT-5?", "ai_release"),
                ])

                result = backfill_event_categories(dry_run=True)
                btc = event_store.get_event("btc")["record"]

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["updated_count"], 1)
        self.assertEqual(result["skipped_known_count"], 1)
        self.assertEqual(result["updates"][0]["event_id"], "btc")
        self.assertEqual(result["updates"][0]["new_category"], "crypto_price_btc")
        self.assertEqual(btc["legacy_analysis"]["base_rate_category"], "unknown")

    def test_write_mode_updates_only_missing_or_unknown_categories(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_store.json")
            with patch.object(event_store, "_store_path", return_value=path):
                event_store.save_events([
                    _record("btc", "Will the price of Bitcoin be above $58,000 on July 4?"),
                    _record("policy", "Department of Education abolished by July 4, 2026", None),
                    _record("known", "Will OpenAI release GPT-5?", "ai_release"),
                ])

                result = backfill_event_categories(dry_run=False)
                btc = event_store.get_event("btc")["record"]
                policy = event_store.get_event("policy")["record"]
                known = event_store.get_event("known")["record"]

        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["dry_run"])
        self.assertEqual(result["updated_count"], 2)
        self.assertEqual(btc["legacy_analysis"]["base_rate_category"], "crypto_price_btc")
        self.assertEqual(policy["legacy_analysis"]["base_rate_category"], "policy_general")
        self.assertEqual(known["legacy_analysis"]["base_rate_category"], "ai_release")


if __name__ == "__main__":
    unittest.main()
