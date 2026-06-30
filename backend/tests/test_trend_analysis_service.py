"""
Tests for probability trend analysis (trend_analysis_service) and its exposure
through GET /events/{event_id}/history.

The analyze_trend unit cases lock the summary math and the pattern classifier;
the route test confirms the history endpoint now carries a correct `trend`.
All deterministic - no network or LLM.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import events as events_routes
from app.services import event_audit_service as audit
from app.services.trend_analysis_service import (
    analyze_edge_trajectory,
    analyze_trend,
    list_edge_trajectories,
    rank_fresh_edges,
    rank_movers,
)

TS0 = "2026-06-12T00:00:00+00:00"
NOW_FRESH = "2026-06-12T01:00:00+00:00"   # 1h after TS0 -> FRESH band
NOW_STALE = "2026-06-16T00:00:00+00:00"   # 96h after TS0 -> stale (> EDGE_STALE_HOURS 72)


def _snap(estimated, timestamp=TS0):
    return {"estimated": estimated, "timestamp": timestamp}


def _esnap(estimated, baseline, timestamp=TS0, **extra):
    return {"estimated": estimated, "baseline": baseline, "timestamp": timestamp, **extra}


def _dt(iso: str):
    from datetime import datetime
    return datetime.fromisoformat(iso)


class AnalyzeTrendTests(unittest.TestCase):
    def test_empty_is_insufficient(self):
        t = analyze_trend([])
        self.assertEqual(t["observations"], 0)
        self.assertEqual(t["pattern"], "insufficient_data")
        self.assertEqual(t["direction"], "stable")
        self.assertIsNone(t["first_probability"])

    def test_single_observation(self):
        t = analyze_trend([_snap(50)])
        self.assertEqual(t["observations"], 1)
        self.assertEqual(t["pattern"], "insufficient_data")
        self.assertEqual(t["net_change"], 0.0)
        self.assertEqual(t["direction"], "stable")

    def test_steady_rise_is_trending_up(self):
        t = analyze_trend([_snap(40), _snap(55), _snap(70)])
        self.assertEqual(t["observations"], 3)
        self.assertEqual(t["direction"], "rising")
        self.assertEqual(t["pattern"], "trending_up")
        self.assertEqual(t["net_change"], 30.0)
        self.assertEqual(t["first_probability"], 40.0)
        self.assertEqual(t["latest_probability"], 70.0)
        self.assertEqual(t["min_probability"], 40.0)
        self.assertEqual(t["max_probability"], 70.0)
        self.assertEqual(t["range"], 30.0)
        self.assertEqual(t["volatility"], 15.0)
        self.assertEqual(t["recent_change"], 15.0)

    def test_steady_fall_is_trending_down(self):
        t = analyze_trend([_snap(70), _snap(55), _snap(40)])
        self.assertEqual(t["direction"], "falling")
        self.assertEqual(t["pattern"], "trending_down")
        self.assertEqual(t["net_change"], -30.0)
        self.assertEqual(t["recent_change"], -15.0)

    def test_small_moves_are_stable(self):
        t = analyze_trend([_snap(50), _snap(51), _snap(49.5), _snap(50.5)])
        self.assertEqual(t["direction"], "stable")
        self.assertEqual(t["pattern"], "stable")
        self.assertEqual(t["range"], 1.5)

    def test_reversal_is_flagged(self):
        # Net up (+5) but the latest move is a sharp drop (-15) -> reversing.
        t = analyze_trend([_snap(40), _snap(60), _snap(45)])
        self.assertEqual(t["direction"], "rising")
        self.assertEqual(t["pattern"], "reversing")
        self.assertEqual(t["net_change"], 5.0)
        self.assertEqual(t["recent_change"], -15.0)

    def test_big_swings_small_net_is_volatile(self):
        t = analyze_trend([_snap(50), _snap(65), _snap(48), _snap(63), _snap(51)])
        self.assertEqual(t["direction"], "stable")  # net change only +1
        self.assertEqual(t["pattern"], "volatile")
        self.assertEqual(t["range"], 17.0)

    def test_invalid_estimates_are_skipped(self):
        t = analyze_trend([_snap(40), _snap(None), _snap("x"), _snap(60)])
        self.assertEqual(t["observations"], 2)
        self.assertEqual(t["net_change"], 20.0)
        self.assertEqual(t["direction"], "rising")

    def test_outcome_snapshots_do_not_pollute_trend(self):
        """An outcome snapshot (kind=outcome, estimated=None) appended after a
        probability trajectory must not change the trend. This guards the
        resolution-tracking feature: outcome snapshots mark a settlement, not a
        probability estimate, so they must stay out of the trajectory math."""
        probability_snaps = [_snap(40, TS0), _snap(60, TS0)]
        outcome_snap = {
            "kind": "outcome",
            "estimated": None,
            "timestamp": TS0,
            "outcome": {"actual_outcome": 100.0},
        }
        without = analyze_trend(probability_snaps)
        with_outcome = analyze_trend(probability_snaps + [outcome_snap])
        self.assertEqual(without, with_outcome)

    def test_span_hours_from_timestamps(self):
        t = analyze_trend([
            _snap(50, "2026-06-12T00:00:00+00:00"),
            _snap(50, "2026-06-12T03:00:00+00:00"),
        ])
        self.assertEqual(t["span_hours"], 3.0)


def _record(event_id, estimated):
    return {
        "event_id": event_id,
        "event_title": "Will X happen?",
        "probability": {
            "baseline": 40.0,
            "estimated": estimated,
            "change": round(estimated - 40.0, 2),
            "direction": "rising",
        },
        "credibility": {"score": 50},
        "impact": {"score": 40},
        "value_score": 30,
        "source": {"type": "manual"},
    }


class HistoryRouteTrendTests(unittest.TestCase):
    def test_history_route_includes_trend(self):
        app = FastAPI()
        app.include_router(events_routes.router, prefix="/events")
        client = TestClient(app)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_audit.jsonl")
            with patch.object(audit, "_audit_path", return_value=path):
                audit.record_event(_record("evtTrend", 40.0))
                audit.record_event(_record("evtTrend", 55.0))
                audit.record_event(_record("evtTrend", 70.0))
                resp = client.get("/events/evtTrend/history")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["count"], 3)
        self.assertIn("trend", body)
        trend = body["trend"]
        self.assertEqual(trend["observations"], 3)
        self.assertEqual(trend["direction"], "rising")
        self.assertEqual(trend["pattern"], "trending_up")
        self.assertEqual(trend["net_change"], 30.0)


class RankMoversTests(unittest.TestCase):
    def test_ranks_by_abs_net_change_and_skips_single_observation(self):
        histories = {
            "big": [_snap(40), _snap(80)],     # +40
            "small": [_snap(50), _snap(53)],   # +3
            "down": [_snap(70), _snap(30)],    # -40 (abs ties "big")
            "single": [_snap(50)],             # no move -> skipped
        }
        movers = rank_movers(histories, limit=10)
        ids = [m["event_id"] for m in movers]
        self.assertNotIn("single", ids)
        self.assertEqual(len(movers), 3)
        self.assertEqual(set(ids[:2]), {"big", "down"})  # both abs 40, ranked first
        self.assertEqual(ids[-1], "small")

    def test_respects_limit(self):
        histories = {f"e{i}": [_snap(10), _snap(10 + i)] for i in range(1, 6)}  # +1..+5
        movers = rank_movers(histories, limit=2)
        self.assertEqual([m["event_id"] for m in movers], ["e5", "e4"])

    def test_mover_entry_shape(self):
        movers = rank_movers({"x": [_snap(40), _snap(60)]}, limit=5)
        self.assertEqual(len(movers), 1)
        self.assertEqual(set(movers[0].keys()), {"event_id", "event_title", "trend"})
        self.assertEqual(movers[0]["trend"]["net_change"], 20.0)

    def test_event_title_taken_from_latest_snapshot(self):
        histories = {"x": [
            {"estimated": 40, "timestamp": TS0, "event_title": "Old title"},
            {"estimated": 60, "timestamp": TS0, "event_title": "Newer title"},
        ]}
        movers = rank_movers(histories, limit=5)
        self.assertEqual(movers[0]["event_title"], "Newer title")


class HistoriesByEventTests(unittest.TestCase):
    def test_groups_snapshots_by_event_id_oldest_to_newest(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_audit.jsonl")
            with patch.object(audit, "_audit_path", return_value=path):
                audit.record_event(_record("a", 40.0))
                audit.record_event(_record("b", 50.0))
                audit.record_event(_record("a", 60.0))
                grouped = audit.histories_by_event()
        self.assertEqual(set(grouped.keys()), {"a", "b"})
        self.assertEqual([s["estimated"] for s in grouped["a"]], [40.0, 60.0])
        self.assertEqual(len(grouped["b"]), 1)

    def test_reuses_cached_grouping_when_audit_file_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_audit.jsonl")
            with patch.object(audit, "_audit_path", return_value=path):
                audit.record_event(_record("a", 40.0))
                audit.record_event(_record("a", 60.0))
                with patch.object(audit, "_read_all", wraps=audit._read_all) as read_all:
                    first = audit.histories_by_event()
                    first["a"][0]["estimated"] = 999.0
                    second = audit.histories_by_event()

        self.assertEqual(read_all.call_count, 1)
        self.assertEqual([s["estimated"] for s in second["a"]], [40.0, 60.0])

    def test_cache_invalidates_when_audit_file_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_audit.jsonl")
            with patch.object(audit, "_audit_path", return_value=path):
                audit.record_event(_record("a", 40.0))
                first = audit.histories_by_event()
                audit.record_event(_record("a", 60.0))
                second = audit.histories_by_event()

        self.assertEqual([s["estimated"] for s in first["a"]], [40.0])
        self.assertEqual([s["estimated"] for s in second["a"]], [40.0, 60.0])


class MoversRouteTests(unittest.TestCase):
    def test_movers_route_ranks_events(self):
        app = FastAPI()
        app.include_router(events_routes.router, prefix="/events")
        client = TestClient(app)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_audit.jsonl")
            with patch.object(audit, "_audit_path", return_value=path):
                audit.record_event(_record("big", 40.0))
                audit.record_event(_record("big", 80.0))     # +40
                audit.record_event(_record("small", 50.0))
                audit.record_event(_record("small", 52.0))   # +2
                resp = client.get("/events/movers?limit=10")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["count"], 2)
        self.assertEqual(body["movers"][0]["event_id"], "big")
        self.assertEqual(body["movers"][0]["trend"]["net_change"], 40.0)

    def test_movers_route_is_not_shadowed_by_event_id(self):
        # /movers must resolve to the movers route, not /{event_id} with id="movers".
        app = FastAPI()
        app.include_router(events_routes.router, prefix="/events")
        client = TestClient(app)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_audit.jsonl")
            with patch.object(audit, "_audit_path", return_value=path):
                resp = client.get("/events/movers")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("movers", resp.json())


class AnalyzeEdgeTrajectoryTests(unittest.TestCase):
    def test_no_numeric_snapshots_is_no_data(self):
        out = analyze_edge_trajectory([{"estimated": None, "baseline": 50}], now=_dt(NOW_FRESH))
        self.assertEqual(out["classification"], "no_data")
        self.assertEqual(out["observations"], 0)

    def test_missing_baseline_snapshot_is_skipped(self):
        out = analyze_edge_trajectory(
            [{"estimated": 60, "timestamp": TS0}, _esnap(64, 50)], now=_dt(NOW_FRESH)
        )
        self.assertEqual(out["observations"], 1)  # first snap skipped (no baseline)
        self.assertEqual(out["latest_edge"], 14.0)

    def test_fresh_edge_holding_near_peak(self):
        snaps = [_esnap(55, 50), _esnap(62, 50), _esnap(64, 50)]  # edges 5,12,14
        out = analyze_edge_trajectory(snaps, now=_dt(NOW_FRESH))
        self.assertEqual(out["latest_edge"], 14.0)
        self.assertEqual(out["peak_edge"], 14.0)
        self.assertEqual(out["freshness_band"], "FRESH")
        self.assertEqual(out["classification"], "fresh")

    def test_decaying_edge_shrunk_from_peak(self):
        snaps = [_esnap(70, 50), _esnap(80, 50), _esnap(56, 50)]  # edges 20,30,6
        out = analyze_edge_trajectory(snaps, now=_dt(NOW_FRESH))
        self.assertEqual(out["latest_edge"], 6.0)
        self.assertEqual(out["peak_edge"], 30.0)
        self.assertEqual(out["classification"], "decaying")  # 6 < 0.7*30

    def test_closed_edge_below_materiality(self):
        snaps = [_esnap(60, 50), _esnap(51, 50)]  # edges 10, 1
        out = analyze_edge_trajectory(snaps, now=_dt(NOW_FRESH))
        self.assertEqual(out["classification"], "closed")  # latest 1 < watch_edge 2.0

    def test_stale_when_last_snapshot_old(self):
        snaps = [_esnap(70, 50), _esnap(90, 50)]  # big edge, but old
        out = analyze_edge_trajectory(snaps, now=_dt(NOW_STALE))
        self.assertGreater(out["age_hours"], 72)
        self.assertEqual(out["classification"], "stale")


class RankFreshEdgesTests(unittest.TestCase):
    def test_keeps_only_fresh_ranked_by_edge(self):
        histories = {
            "bigfresh": [_esnap(50, 50), _esnap(90, 50)],   # edge 40, fresh
            "midfresh": [_esnap(50, 50), _esnap(65, 50)],   # edge 15, fresh
            "decayed": [_esnap(90, 50), _esnap(53, 50)],    # 40 -> 3 ... 3>=0.7*40? no -> decaying
            "closed": [_esnap(51, 50)],                     # edge 1 -> closed
        }
        out = rank_fresh_edges(histories, limit=10, now=_dt(NOW_FRESH))
        ids = [e["event_id"] for e in out]
        self.assertEqual(ids, ["bigfresh", "midfresh"])  # only fresh, by |edge| desc
        self.assertEqual(set(out[0].keys()), {"event_id", "event_title", "edge"})

    def test_stale_events_excluded(self):
        histories = {"old": [_esnap(50, 50), _esnap(90, 50)]}  # big edge but old
        self.assertEqual(rank_fresh_edges(histories, now=_dt(NOW_STALE)), [])

    def test_respects_limit(self):
        histories = {f"e{i}": [_esnap(50, 50), _esnap(50 + 5 * i, 50)] for i in range(1, 6)}
        out = rank_fresh_edges(histories, limit=2, now=_dt(NOW_FRESH))
        self.assertEqual([e["event_id"] for e in out], ["e5", "e4"])


class ListEdgeTrajectoriesTests(unittest.TestCase):
    def test_lists_all_classes_with_series_for_monitoring(self):
        histories = {
            "fresh": [_esnap(50, 50), _esnap(70, 50)],
            "decaying": [_esnap(90, 50), _esnap(56, 50)],
            "closed": [_esnap(50, 50), _esnap(41, 40)],
        }
        out = list_edge_trajectories(
            histories,
            limit=10,
            classification="all",
            include_series=True,
            now=_dt(NOW_FRESH),
        )
        self.assertEqual([e["event_id"] for e in out], ["fresh", "decaying", "closed"])
        self.assertEqual(out[0]["edge"]["classification"], "fresh")
        self.assertEqual(out[1]["edge"]["classification"], "decaying")
        self.assertEqual(out[2]["edge"]["classification"], "closed")
        self.assertEqual(out[0]["series"][-1]["edge"], 20.0)


class EdgeRouteTests(unittest.TestCase):
    def test_history_route_includes_edge_block(self):
        app = FastAPI()
        app.include_router(events_routes.router, prefix="/events")
        client = TestClient(app)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_audit.jsonl")
            with patch.object(audit, "_audit_path", return_value=path):
                audit.record_event(_record("evtE", 40.0))  # edge 0 (baseline 40)
                audit.record_event(_record("evtE", 70.0))  # edge 30
                resp = client.get("/events/evtE/history")
        self.assertEqual(resp.status_code, 200)
        edge = resp.json()["edge"]
        self.assertEqual(edge["latest_edge"], 30.0)
        self.assertIn("classification", edge)

    def test_fresh_edges_route_returns_only_material_fresh(self):
        app = FastAPI()
        app.include_router(events_routes.router, prefix="/events")
        client = TestClient(app)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_audit.jsonl")
            with patch.object(audit, "_audit_path", return_value=path):
                audit.record_event(_record("bigedge", 85.0))    # edge 45 -> fresh
                audit.record_event(_record("closededge", 41.0))  # edge 1 -> closed
                resp = client.get("/events/edges/fresh")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        ids = [e["event_id"] for e in body["edges"]]
        self.assertIn("bigedge", ids)
        self.assertNotIn("closededge", ids)

    def test_fresh_edges_route_empty_audit(self):
        app = FastAPI()
        app.include_router(events_routes.router, prefix="/events")
        client = TestClient(app)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_audit.jsonl")
            with patch.object(audit, "_audit_path", return_value=path):
                resp = client.get("/events/edges/fresh")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"count": 0, "edges": []})

    def test_edge_monitor_route_can_return_all_classes_with_series(self):
        app = FastAPI()
        app.include_router(events_routes.router, prefix="/events")
        client = TestClient(app)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_audit.jsonl")
            with patch.object(audit, "_audit_path", return_value=path):
                audit.record_event(_record("bigedge", 85.0))     # edge 45 -> fresh
                audit.record_event(_record("closededge", 41.0))  # edge 1 -> closed
                resp = client.get("/events/edges/fresh?classification=all&include_series=true")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["classification"], "all")
        by_id = {e["event_id"]: e for e in body["edges"]}
        self.assertEqual(by_id["bigedge"]["edge"]["classification"], "fresh")
        self.assertEqual(by_id["closededge"]["edge"]["classification"], "closed")
        self.assertEqual(by_id["bigedge"]["series"][0]["edge"], 45.0)


if __name__ == "__main__":
    unittest.main()
