# backend/tests/test_reoptimize_monthly_job.py
"""The monthly re-optimization job must measure something before reporting success.

It used to call ``optimize_sync(sport, train_matches=[], test_matches=[])`` with a
"For now" comment. ``BacktestRunner`` answers an empty match list with
``sample_count=0`` and every metric ``0.0``, so all ``PHASE9_OPTIMIZATION_TRIALS``
trials tied at 0.0, ``study.best_trial`` was whichever ran first, and that trial's
weights were persisted as the sport's best candidate and reported as a completed
run.
"""
import json
import logging

import pytest
from unittest.mock import MagicMock, patch

from app.core.scheduler import _job_reoptimize_monthly


def _match(i, home="Lakers", away="Celtics"):
    return {
        "match_id": f"nba-{i}", "home_team": home, "away_team": away,
        "home_score": 100 + i, "away_score": 95, "season": 2024,
        "is_playoff": False,
    }


@pytest.fixture
def captured():
    """Patch the job's collaborators; capture what optimize_sync received."""
    calls = {"optimize": [], "finish": []}

    def fake_optimize(sport, *, n_trials, train_matches, test_matches):
        calls["optimize"].append({
            "sport": sport, "n_trials": n_trials,
            "train": len(train_matches), "test": len(test_matches),
        })
        return {
            "best_score": 0.66, "saved_candidate": {"id": 1},
            "not_persisted_reason": None,
        }

    def fake_finish(run_id, status, *, result=None, error=None, exc=None):
        calls["finish"].append({"status": status, "result": result, "error": error})

    with patch("app.core.scheduler._start_run", return_value="run-1"), \
         patch("app.core.scheduler._finish_run", side_effect=fake_finish), \
         patch("app.kernel.kernel_db.init_kernel_db"), \
         patch(
             "app.kernel.parameter_optimizer.ParameterOptimizer"
         ) as MockOpt:
        MockOpt.return_value.optimize_sync = MagicMock(side_effect=fake_optimize)
        yield calls


@pytest.mark.asyncio
async def test_job_loads_matches_instead_of_passing_empty_lists(captured):
    """Each sport must be optimized against a real train/test split."""
    with patch(
        "app.kernel.backtest.match_loader.load_sport_matches_for_backtest",
        return_value=[_match(i) for i in range(50)],
    ):
        await _job_reoptimize_monthly()

    assert len(captured["optimize"]) == 3, captured["optimize"]
    for call in captured["optimize"]:
        # The defect passed 0 and 0 here. Assert both are positive *and* that
        # they partition the corpus, so a loader wired to the wrong split still
        # fails rather than passing on "non-zero".
        assert call["train"] > 0, call
        assert call["test"] > 0, call
        assert call["train"] + call["test"] == 50, call
    assert [c["sport"] for c in captured["optimize"]] == ["nba", "mlb", "nhl"]
    assert captured["finish"][-1]["status"] == "success"


@pytest.mark.asyncio
async def test_job_skips_a_sport_with_too_few_matches(captured):
    """Below the route's own minimum, don't run a search at all."""
    with patch(
        "app.kernel.backtest.match_loader.load_sport_matches_for_backtest",
        return_value=[_match(i) for i in range(4)],
    ):
        await _job_reoptimize_monthly()

    assert captured["optimize"] == []
    final = captured["finish"][-1]
    # A run that optimized nothing is not a success: an empty kernel DB used to
    # be indistinguishable from a completed monthly re-optimization.
    assert final["status"] == "failed"
    assert final["result"]["sports"] == []
    assert set(final["result"]["skipped"]) == {"nba", "mlb", "nhl"}
    assert all(
        v.startswith("insufficient_matches:4")
        for v in final["result"]["skipped"].values()
    ), final["result"]["skipped"]


@pytest.mark.asyncio
async def test_a_sport_that_persisted_nothing_is_not_counted_as_completed(captured):
    """``not_persisted_reason`` must decide the outcome, not the call returning."""
    def refusing(sport, *, n_trials, train_matches, test_matches):
        captured["optimize"].append({"sport": sport, "n_trials": n_trials,
                                     "train": len(train_matches),
                                     "test": len(test_matches)})
        if sport == "mlb":
            return {"best_score": 0.0, "saved_candidate": None,
                    "not_persisted_reason": "zero_samples"}
        return {"best_score": 0.7, "saved_candidate": {"id": 2},
                "not_persisted_reason": None}

    with patch(
        "app.kernel.backtest.match_loader.load_sport_matches_for_backtest",
        return_value=[_match(i) for i in range(50)],
    ), patch(
        "app.kernel.parameter_optimizer.ParameterOptimizer"
    ) as MockOpt:
        MockOpt.return_value.optimize_sync = MagicMock(side_effect=refusing)
        await _job_reoptimize_monthly()

    final = captured["finish"][-1]
    assert final["result"]["sports"] == ["nba", "nhl"]
    assert final["result"]["skipped"] == {"mlb": "zero_samples"}
    # Two sports did succeed, so the run as a whole still counts as one.
    assert final["status"] == "success"


@pytest.mark.asyncio
async def test_partial_failure_is_safe_in_result_error_and_logs(captured):
    sensitive = (
        "SELECT secret FROM D:/private/optimizer.db "
        "Authorization=Bearer fake-api-key ticket=fake-ticket "
        "subprotocol=fake-subprotocol https://upstream.example/private"
    )
    rendered_logs = []

    class RenderedLogHandler(logging.Handler):
        def emit(self, record):
            rendered_logs.append(self.format(record))

    def optimize(sport, **_kwargs):
        if sport == "mlb":
            raise RuntimeError(sensitive)
        return {"best_score": 0.7, "saved_candidate": {"id": 2},
                "not_persisted_reason": None}

    handler = RenderedLogHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger("app.core.scheduler").addHandler(handler)
    try:
        with patch(
            "app.kernel.backtest.match_loader.load_sport_matches_for_backtest",
            return_value=[_match(i) for i in range(50)],
        ), patch("app.kernel.parameter_optimizer.ParameterOptimizer") as MockOpt:
            MockOpt.return_value.optimize_sync = MagicMock(side_effect=optimize)
            await _job_reoptimize_monthly()
    finally:
        logging.getLogger("app.core.scheduler").removeHandler(handler)

    final = captured["finish"][-1]
    exported = json.dumps({"finish": final, "logs": rendered_logs})
    assert final["status"] == "success"
    assert final["result"]["sports"] == ["nba", "nhl"]
    assert final["result"]["skipped"]["mlb"] == "error:RuntimeError"
    for fragment in (
        "SELECT secret", "D:/private", "fake-api-key", "fake-ticket",
        "fake-subprotocol", "upstream.example", "Traceback",
    ):
        assert fragment not in exported
    assert "RuntimeError" in exported


@pytest.mark.asyncio
async def test_all_failures_use_a_fixed_scheduler_error(captured):
    sensitive = (
        "SELECT secret FROM D:/private/optimizer.db "
        "Authorization=Bearer fake-api-key ticket=fake-ticket"
    )
    with patch(
        "app.kernel.backtest.match_loader.load_sport_matches_for_backtest",
        return_value=[_match(i) for i in range(50)],
    ), patch("app.kernel.parameter_optimizer.ParameterOptimizer") as MockOpt:
        MockOpt.return_value.optimize_sync = MagicMock(
            side_effect=RuntimeError(sensitive)
        )
        await _job_reoptimize_monthly()

    final = captured["finish"][-1]
    assert final["status"] == "failed"
    assert final["error"] == "No sport re-optimized"
    exported = json.dumps(final)
    assert sensitive not in exported
    assert set(final["result"]["skipped"].values()) == {"error:RuntimeError"}
