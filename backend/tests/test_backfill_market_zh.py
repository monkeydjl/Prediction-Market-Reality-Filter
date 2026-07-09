import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from scripts import backfill_market_zh as backfill


class BackfillMarketZhTests(unittest.TestCase):
    def test_translation_uses_gateway_availability_when_legacy_key_is_empty(self):
        records = [{
            "record": {
                "event_id": "evt-1",
                "event_title": "Will rates fall?",
                "source": {"type": "open_web"},
                "evidence_items": [{"title": "Fed may cut rates"}],
            }
        }]

        async def fake_translate(items):
            items[0]["title_zh"] = "美联储可能降息"
            return items

        with patch("sys.argv", ["backfill_market_zh.py", "--no-network"]), \
                patch.object(backfill.settings, "OPENAI_API_KEY", ""), \
                patch.object(backfill, "has_configured_llm_route", return_value=True), \
                patch.object(backfill, "list_all_events", return_value=records), \
                patch.object(backfill, "translate_articles", new=AsyncMock(side_effect=fake_translate)) as translate, \
                patch.object(backfill, "save_events") as save_events, \
                patch.object(backfill.os.path, "exists", return_value=False):
            asyncio.run(backfill.main())

        translate.assert_awaited_once()
        save_events.assert_called_once()


if __name__ == "__main__":
    unittest.main()
