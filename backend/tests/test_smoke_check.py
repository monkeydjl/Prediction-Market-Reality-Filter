import unittest

from scripts import smoke_check


class SmokeCheckTests(unittest.TestCase):
    def test_validate_event_payload_accepts_backend_categories(self):
        payload = {
            "total": 2,
            "events": [
                {"event_id": "rates", "category": "monetary", "record": {"event_title": "BOE rates"}},
                {"event_id": "crypto", "category": "crypto", "record": {"event_title": "HYPE Up or Down"}},
            ],
        }

        smoke_check.validate_event_payload(payload)

    def test_validate_event_payload_rejects_generic_categories(self):
        payload = {
            "events": [
                {"event_id": "generic", "category": "general", "record": {"event_title": "Generic"}},
            ],
        }

        with self.assertRaisesRegex(AssertionError, "generic category"):
            smoke_check.validate_event_payload(payload)

    def test_validate_category_counts_rejects_source_platforms(self):
        with self.assertRaisesRegex(AssertionError, "source category"):
            smoke_check.validate_category_counts({"counts": {"Limitless": 3}})


if __name__ == "__main__":
    unittest.main()
