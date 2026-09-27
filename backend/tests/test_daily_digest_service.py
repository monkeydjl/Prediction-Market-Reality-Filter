"""
Tests for the daily intelligence digest builder (daily_digest_service).

build_daily_digest() turns the event audit log + the event store into a
single "what actually happened today" payload: top movers ranked by the
day's net probability move (close vs the previous close, not the all-time
trajectory), the day's headline, and an unchanged/new-event summary.

All deterministic - no network, no LLM. Dates are exercised in UTC.
"""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app.services import event_audit_service as audit
from app.services.daily_digest_service import build_daily_digest

DAY = "2026-06-12"


def _ts(day: str, hour: int, minute: int = 0) -> str:
    return f"{day}T{hour:02d}:{minute:02d}:00+00:00"


def _snap(estimated, timestamp, event_title=None, **extra):
    snap = {"estimated": estimated, "timestamp": timestamp, **extra}
    if event_title:
        snap["event_title"] = event_title
    return snap


def _record(event_id, estimated, ts):
    return {
        "event_id": event_id,
        "event_title": f"Will {event_id} happen?",
        "probability": {
            "baseline": 40.0,
            "estimated": estimated,
            "change": round(estimated - 40.0, 2),
            "direction": "rising" if estimated > 40.0 else "falling",
        },
        "credibility": {"score": 50},
        "impact": {"score": 40},
        "value_score": 30,
        "source": {"type": "manual"},
        "timestamp": ts,
    }


def _entry(event_id, estimated, ts, title_zh=""):
    record = _record(event_id, estimated, ts)
    record["event_title_zh"] = title_zh
    return {"event_id": event_id, "record": record}


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


class BuildDailyDigestTests(unittest.TestCase):
    def test_empty_store_renders_empty_digest(self):
        digest = build_daily_digest(
            store=[], history_loader=lambda: {}, now=_dt(_ts(DAY, 20))
        )
        self.assertEqual(digest["date"], DAY)
        self.assertTrue(digest["empty"])
        self.assertIsNone(digest["headline"])
        self.assertEqual(digest["movers"], [])
        self.assertEqual(digest["count"], 0)

    def test_single_snapshot_event_is_new_not_mover(self):
        store = [_entry("fresh", 62.0, _ts(DAY, 9))]
        histories = {"fresh": [_snap(62.0, _ts(DAY, 9))]}
        digest = build_daily_digest(
            store=store, history_loader=lambda: histories, now=_dt(_ts(DAY, 20))
        )
        self.assertFalse(digest["empty"])
        self.assertEqual(digest["unchanged"]["new_event_ids"], ["fresh"])
        self.assertEqual(digest["movers"], [])
        self.assertIsNone(digest["headline"])

    def test_day_net_is_close_vs_previous_close_not_all_time(self):
        # All-time: 40 -> 90 (+50). Today (06-12): 60 -> 75 (+15).
        # The previous digest close (60, from 06-11) opens the day; the
        # post-window snapshot (06-13) never enters the math.
        histories = {"evt": [
            _snap(40.0, _ts("2026-06-10", 10)),
            _snap(60.0, _ts("2026-06-11", 15)),
            _snap(75.0, _ts(DAY, 10)),
            _snap(90.0, _ts("2026-06-13", 9)),
        ]}
        store = [_entry("evt", 90.0, _ts("2026-06-13", 9))]
        digest = build_daily_digest(
            store=store, history_loader=lambda: histories, now=_dt(_ts(DAY, 20))
        )
        mover = digest["movers"][0]
        self.assertEqual(mover["event_id"], "evt")
        self.assertEqual(mover["day_net"], 15.0)
        self.assertEqual(mover["open"], 60.0)
        self.assertEqual(mover["close"], 75.0)
        self.assertEqual(mover["open_source"], "previous_close")
        self.assertEqual(mover["movement"], "rising")
        # All-time trajectory stays visible as context, but never drives rank.
        self.assertEqual(mover["all_time_net_change"], 50.0)

    def test_single_scan_after_gap_is_measured_against_previous_close(self):
        # Only one scan today, but the previous digest close exists: the
        # 60 -> 75 move published today is real news and must rank.
        histories = {"evt": [
            _snap(60.0, _ts("2026-06-11", 15)),
            _snap(75.0, _ts(DAY, 10)),
        ]}
        store = [_entry("evt", 75.0, _ts(DAY, 10))]
        digest = build_daily_digest(
            store=store, history_loader=lambda: histories, now=_dt(_ts(DAY, 20))
        )
        self.assertEqual(len(digest["movers"]), 1)
        self.assertEqual(digest["movers"][0]["day_net"], 15.0)
        self.assertEqual(digest["movers"][0]["day_observations"], 1)

    def test_first_day_of_a_new_event_can_be_a_mover(self):
        # No pre-day close: the first in-day snapshot opens the day.
        histories = {"new": [
            _snap(50.0, _ts(DAY, 9)),
            _snap(78.0, _ts(DAY, 16)),
        ]}
        store = [_entry("new", 78.0, _ts(DAY, 16))]
        digest = build_daily_digest(
            store=store, history_loader=lambda: histories, now=_dt(_ts(DAY, 20))
        )
        mover = digest["movers"][0]
        self.assertEqual(mover["day_net"], 28.0)
        self.assertEqual(mover["day_observations"], 2)

    def test_ranks_by_abs_day_net_desc_with_deterministic_tiebreak(self):
        histories = {
            "up": [_snap(50.0, _ts(DAY, 9)), _snap(70.0, _ts(DAY, 15))],    # +20
            "down": [_snap(60.0, _ts(DAY, 9)), _snap(40.0, _ts(DAY, 15))],  # -20
            "big": [_snap(40.0, _ts(DAY, 9)), _snap(95.0, _ts(DAY, 15))],   # +55
        }
        store = [_entry(e, 50.0, _ts(DAY, 15)) for e in ("up", "down", "big")]
        digest = build_daily_digest(
            store=store, history_loader=lambda: histories, now=_dt(_ts(DAY, 20))
        )
        ids = [m["event_id"] for m in digest["movers"]]
        self.assertEqual(ids, ["big", "down", "up"])  # 55 > 20(tie) -> id asc

    def test_limit_truncates_movers(self):
        histories = {
            f"e{i}": [_snap(50.0, _ts(DAY, 9)), _snap(50.0 + i, _ts(DAY, 15))]
            for i in range(1, 6)  # day nets +1..+5
        }
        store = [_entry(e, 55.0, _ts(DAY, 15)) for e in histories]
        digest = build_daily_digest(
            store=store, history_loader=lambda: histories, now=_dt(_ts(DAY, 20)),
            limit=2,
        )
        self.assertEqual(digest["count"], 2)
        self.assertEqual([m["event_id"] for m in digest["movers"]], ["e5", "e4"])

    def test_headline_is_top_mover_with_title(self):
        histories = {"top": [_snap(40.0, _ts(DAY, 9)), _snap(88.0, _ts(DAY, 15))]}
        store = [_entry("top", 88.0, _ts(DAY, 15), title_zh="xxx会发生吗")]
        digest = build_daily_digest(
            store=store, history_loader=lambda: histories, now=_dt(_ts(DAY, 20))
        )
        self.assertEqual(digest["headline"]["event_id"], "top")
        self.assertEqual(digest["headline"]["event_title"], "xxx会发生吗")

    def test_tiny_moves_are_unchanged_not_movers(self):
        histories = {"calm": [
            _snap(50.0, _ts(DAY, 9)),
            _snap(50.3, _ts(DAY, 15)),  # +0.3 < 0.5 materiality
        ]}
        store = [_entry("calm", 50.3, _ts(DAY, 15))]
        digest = build_daily_digest(
            store=store, history_loader=lambda: histories, now=_dt(_ts(DAY, 20))
        )
        self.assertEqual(digest["movers"], [])
        # First seen today and barely moving: the informative label is "new",
        # not "quiet" (quiet means an established event with nothing happening).
        self.assertIn("calm", digest["unchanged"]["new_event_ids"])

    def test_established_event_with_tiny_move_is_quiet(self):
        histories = {"calm": [
            _snap(50.0, _ts("2026-06-11", 15)),
            _snap(50.3, _ts(DAY, 15)),  # +0.3 vs previous close
        ]}
        store = [_entry("calm", 50.3, _ts(DAY, 15))]
        digest = build_daily_digest(
            store=store, history_loader=lambda: histories, now=_dt(_ts(DAY, 20))
        )
        self.assertEqual(digest["movers"], [])
        self.assertIn("calm", digest["unchanged"]["quiet_event_ids"])
        self.assertNotIn("calm", digest["unchanged"]["new_event_ids"])
        # A day summarised by quiet events is NOT "empty": the caller renders
        # the quiet summary instead of an empty-state banner beside it.
        self.assertFalse(digest["empty"])

    def test_all_quiet_day_is_not_empty(self):
        histories = {
            f"e{i}": [
                _snap(50.0, _ts("2026-06-11", 15)),
                _snap(50.0 + i * 0.1, _ts(DAY, 15)),
            ]
            for i in range(3)
        }
        store = [_entry(e, 50.0, _ts(DAY, 15)) for e in histories]
        digest = build_daily_digest(
            store=store, history_loader=lambda: histories, now=_dt(_ts(DAY, 20))
        )
        self.assertEqual(digest["movers"], [])
        self.assertEqual(len(digest["unchanged"]["quiet_event_ids"]), 3)
        self.assertFalse(digest["empty"])

    def test_move_of_exactly_materiality_is_a_directional_mover(self):
        # The mover cut and the direction label must agree at the boundary:
        # +/-0.5 exactly is a mover AND rising/falling, never a mover row
        # whose movement reads "stable".
        histories = {
            "up_edge": [_snap(50.0, _ts(DAY, 9)), _snap(50.5, _ts(DAY, 15))],    # +0.5
            "down_edge": [_snap(50.0, _ts(DAY, 9)), _snap(49.5, _ts(DAY, 15))],  # -0.5
            "under": [_snap(50.0, _ts(DAY, 9)), _snap(50.4, _ts(DAY, 15))],      # +0.4
        }
        store = [_entry(e, 50.0, _ts(DAY, 15)) for e in histories]
        digest = build_daily_digest(
            store=store, history_loader=lambda: histories, now=_dt(_ts(DAY, 20))
        )
        by_id = {m["event_id"]: m for m in digest["movers"]}
        self.assertEqual(set(by_id), {"up_edge", "down_edge"})
        self.assertEqual(by_id["up_edge"]["day_net"], 0.5)
        self.assertEqual(by_id["up_edge"]["movement"], "rising")
        self.assertEqual(by_id["down_edge"]["movement"], "falling")
        # Nothing in the mover table may read as stable.
        self.assertNotIn("stable", [m["movement"] for m in digest["movers"]])

    def test_store_is_not_read_when_nothing_is_in_window(self):
        # A quiet calendar day must not pay for the durable store parse.
        def _explode():
            raise AssertionError("store must not be read when there is no in-window data")

        out_of_window = {"evt": [_snap(40.0, _ts("2026-06-01", 9))]}
        with patch("app.services.daily_digest_service.list_all_events", _explode):
            digest = build_daily_digest(
                store=None, history_loader=lambda: out_of_window,
                now=_dt(_ts(DAY, 20)),
            )
        self.assertTrue(digest["empty"])
        self.assertEqual(digest["movers"], [])

    def test_store_is_read_once_when_there_is_something_to_title(self):
        calls = []

        def _loader():
            calls.append(1)
            return [{"event_id": "evt", "record": {"event_title_zh": "标题"}}]

        histories = {"evt": [_snap(40.0, _ts("2026-06-11", 9)), _snap(60.0, _ts(DAY, 9))]}
        with patch("app.services.daily_digest_service.list_all_events", _loader):
            digest = build_daily_digest(
                store=None, history_loader=lambda: histories, now=_dt(_ts(DAY, 20))
            )
        self.assertEqual(len(calls), 1)
        self.assertEqual(digest["movers"][0]["event_title"], "标题")

    def test_outcome_snapshots_do_not_pollute_day_net(self):
        histories = {"evt": [
            _snap(40.0, _ts(DAY, 9)),
            _snap(60.0, _ts(DAY, 12)),
            {"kind": "outcome", "estimated": None, "timestamp": _ts(DAY, 13),
             "outcome": {"actual_outcome": 100.0}},
            _snap(65.0, _ts(DAY, 14)),
        ]}
        store = [_entry("evt", 65.0, _ts(DAY, 14))]
        digest = build_daily_digest(
            store=store, history_loader=lambda: histories, now=_dt(_ts(DAY, 20))
        )
        mover = digest["movers"][0]
        self.assertEqual(mover["day_net"], 25.0)
        self.assertEqual(mover["day_observations"], 3)

    def test_snapshots_without_timestamps_are_skipped(self):
        histories = {"evt": [
            {"estimated": 40.0},  # no timestamp -> unusable
            _snap(70.0, _ts(DAY, 15)),
        ]}
        store = [_entry("evt", 70.0, _ts(DAY, 15))]
        digest = build_daily_digest(
            store=store, history_loader=lambda: histories, now=_dt(_ts(DAY, 20))
        )
        # Single usable point -> new event, not a mover.
        self.assertEqual(digest["movers"], [])
        self.assertIn("evt", digest["unchanged"]["new_event_ids"])

    def test_explicit_date_overrides_now_window(self):
        histories = {"evt": [_snap(40.0, _ts("2026-06-10", 9)), _snap(80.0, _ts("2026-06-10", 15))]}
        store = [_entry("evt", 80.0, _ts("2026-06-10", 15))]
        digest = build_daily_digest(
            store=store, history_loader=lambda: histories,
            now=_dt(_ts(DAY, 20)), date="2026-06-10",
        )
        self.assertEqual(digest["date"], "2026-06-10")
        self.assertEqual(digest["movers"][0]["day_net"], 40.0)

    def test_accepts_a_datetime_date_object(self):
        # The API layer hands over a datetime.date, not a string.
        from datetime import date as _date
        histories = {"evt": [
            _snap(40.0, _ts("2026-06-09", 9)),
            _snap(80.0, _ts("2026-06-10", 15)),
        ]}
        digest = build_daily_digest(
            store=[], history_loader=lambda: histories,
            now=_dt(_ts(DAY, 20)), date=_date(2026, 6, 10),
        )
        self.assertEqual(digest["date"], "2026-06-10")
        mover = digest["movers"][0]
        self.assertEqual(mover["open"], 40.0)
        self.assertEqual(mover["close"], 80.0)
        self.assertEqual(mover["day_net"], 40.0)

    def test_reads_real_audit_file_when_loader_not_given(self):
        # record_event always stamps "now", so write the audit file directly
        # with fixed timestamps; the point of this case is the default loader
        # wiring (real _read_all + grouping + cache), not the writer.
        import json
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_audit.jsonl")
            lines = [
                json.dumps(_snap(40.0, _ts(DAY, 9), event_id="a", event_title="Will a happen?")),
                json.dumps(_snap(65.0, _ts(DAY, 15), event_id="a", event_title="Will a happen?")),
            ]
            Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
            with patch.object(audit, "_audit_path", return_value=path):
                audit.invalidate_history_cache()
                digest = build_daily_digest(
                    store=[], now=_dt(_ts(DAY, 20))
                )
        self.assertEqual(digest["movers"][0]["event_id"], "a")
        self.assertEqual(digest["movers"][0]["day_net"], 25.0)
        # Title falls back to the audit snapshot's own title.
        self.assertEqual(digest["movers"][0]["event_title"], "Will a happen?")


class DigestRouteTests(unittest.TestCase):
    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.routes import events as events_routes
        app = FastAPI()
        app.include_router(events_routes.router, prefix="/events")
        return TestClient(app)

    def test_digest_route_returns_payload(self):
        client = self._client()
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_audit.jsonl")
            with patch.object(audit, "_audit_path", return_value=path):
                resp = client.get("/events/digest")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        for key in ("date", "generated_at", "count", "headline", "movers",
                    "unchanged", "empty"):
            self.assertIn(key, body)
        self.assertTrue(body["empty"])

    def test_digest_route_is_not_shadowed_by_event_id(self):
        client = self._client()
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_audit.jsonl")
            with patch.object(audit, "_audit_path", return_value=path):
                resp = client.get("/events/digest")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("movers", resp.json())

    def test_digest_route_rejects_bad_date(self):
        client = self._client()
        resp = client.get("/events/digest", params={"date": "12/06/2026"})
        self.assertEqual(resp.status_code, 422)

    def test_digest_route_rejects_calendar_invalid_date(self):
        # A value that satisfies ^\d{4}-\d{2}-\d{2}$ but is not a real calendar
        # date must be a 422 at the boundary, never a 500 raised deep inside
        # the service (datetime.strptime on 2026-02-30).
        client = self._client()
        for bad in ("2026-02-30", "2026-13-01", "2026-00-10"):
            resp = client.get("/events/digest", params={"date": bad})
            self.assertEqual(resp.status_code, 422, f"{bad} -> {resp.status_code}")

    def test_digest_openapi_schema_declares_fields(self):
        # The endpoint must publish a real contract. A bare FlexibleResponse
        # would leave the 200 schema field-less, which is exactly the shape the
        # generated-types allowlist excludes.
        client = self._client()
        schema = client.get("/openapi.json").json()
        op = schema["paths"]["/events/digest"]["get"]["responses"]["200"]
        ref = op["content"]["application/json"]["schema"]["$ref"]
        model = schema["components"]["schemas"][ref.rsplit("/", 1)[-1]]
        for field in ("date", "generated_at", "count", "headline", "movers", "unchanged", "empty"):
            self.assertIn(field, model["properties"], f"{field} missing from the digest contract")

    def test_digest_model_is_in_frontend_export_allowlist(self):
        from app.models import _frontend_export
        self.assertIn("DailyDigestResponse", _frontend_export.__all__)

    def test_digest_route_rejects_bad_limit(self):
        client = self._client()
        resp = client.get("/events/digest", params={"limit": 0})
        self.assertEqual(resp.status_code, 422)

    def test_digest_route_reads_audit_and_store(self):
        import json
        client = self._client()
        fake_events = [
            {"event_id": "m", "record": {"event_title": "M?", "event_title_zh": "M会吗"}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_audit.jsonl")
            lines = [
                json.dumps({"estimated": 50.0, "timestamp": _ts("2026-06-11", 15),
                            "event_id": "m", "event_title": "M?"}),
                json.dumps({"estimated": 80.0, "timestamp": _ts(DAY, 12),
                            "event_id": "m", "event_title": "M?"}),
            ]
            Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
            with patch.object(audit, "_audit_path", return_value=path), \
                 patch("app.services.daily_digest_service.list_all_events",
                       return_value=fake_events):
                audit.invalidate_history_cache()
                resp = client.get("/events/digest", params={"date": DAY})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["movers"][0]["event_id"], "m")
        self.assertEqual(body["movers"][0]["day_net"], 30.0)
        self.assertEqual(body["headline"]["event_title"], "M会吗")


if __name__ == "__main__":
    unittest.main()
