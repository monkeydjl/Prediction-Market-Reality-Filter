# backend/tests/test_weekly_weight_update_job.py
"""The weekly learning job must report a rewrite, a skip, and a failure honestly."""
import json
import logging

import pytest
from unittest.mock import MagicMock, patch

from app.core.scheduler import _job_update_weights_weekly


@pytest.fixture
def captured(monkeypatch):
    """Enable the job body and capture its run-ledger result."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "PHASE9_LEARNING_ACTIVATED", True)
    monkeypatch.setattr(settings, "PHASE9_WEEKLY_WEIGHT_UPDATE_INTERVAL_MIN", 60)
    calls = {"finish": []}

    def fake_finish(run_id, status, *, result=None, error=None, exc=None):
        calls["finish"].append({"status": status, "result": result, "error": error})

    with patch("app.core.scheduler._start_run", return_value="run-weights"), \
         patch("app.core.scheduler._finish_run", side_effect=fake_finish):
        yield calls


@pytest.mark.asyncio
async def test_job_reports_only_competitions_that_rewrote_weights(captured):
    """A bare return used to log all three as updated and report all three."""
    outcomes = {
        "nba": {"updated": True, "reason": None, "factors": 4, "samples": 20},
        "mlb": {"updated": False, "reason": "insufficient_samples", "samples": 3},
        "nhl": {"updated": False, "reason": "zero_total_accuracy", "samples": 20},
    }
    with patch("app.kernel.learning_service.KernelLearningService") as MockSvc:
        instance = MockSvc.return_value
        instance.update_weights = MagicMock(side_effect=lambda comp: outcomes[comp])
        await _job_update_weights_weekly()

    assert instance.update_weights.call_count == 3
    assert [call.args[0] for call in instance.update_weights.call_args_list] == [
        "nba", "mlb", "nhl",
    ]
    final = captured["finish"][-1]
    assert final["status"] == "success"
    assert final["result"] == {
        "competitions": ["nba"],
        "skipped": {
            "mlb": "insufficient_samples",
            "nhl": "zero_total_accuracy",
        },
    }


@pytest.mark.asyncio
async def test_job_with_no_rewrites_fails_instead_of_claiming_success(captured):
    """No measurement is not a successful weekly update."""
    with patch("app.kernel.learning_service.KernelLearningService") as MockSvc:
        instance = MockSvc.return_value
        instance.update_weights = MagicMock(return_value={
            "updated": False, "reason": "insufficient_samples", "samples": 0,
        })
        await _job_update_weights_weekly()

    final = captured["finish"][-1]
    assert final["status"] == "failed"
    assert final["result"]["competitions"] == []
    assert final["result"]["skipped"] == {
        "nba": "insufficient_samples",
        "mlb": "insufficient_samples",
        "nhl": "insufficient_samples",
    }
    assert final["error"] == "No competition weights updated"


@pytest.mark.asyncio
async def test_job_records_one_error_without_erasing_an_actual_update(captured):
    """An exception is not the same as the service declining to learn."""
    def update(comp):
        if comp == "nba":
            return {"updated": True, "reason": None, "factors": 4, "samples": 20}
        if comp == "mlb":
            raise RuntimeError("database unavailable")
        return {"updated": False, "reason": "no_factor_samples", "samples": 20}

    with patch("app.kernel.learning_service.KernelLearningService") as MockSvc:
        instance = MockSvc.return_value
        instance.update_weights = MagicMock(side_effect=update)
        await _job_update_weights_weekly()

    final = captured["finish"][-1]
    assert final["status"] == "success"
    assert final["result"]["competitions"] == ["nba"]
    assert final["result"]["skipped"]["nhl"] == "no_factor_samples"
    assert final["result"]["skipped"]["mlb"] == "error:RuntimeError"


@pytest.mark.asyncio
async def test_partial_failure_is_safe_in_result_error_and_logs(captured):
    sensitive = (
        "SELECT secret FROM D:/private/weights.db "
        "Authorization=Bearer fake-api-key ticket=fake-ticket "
        "subprotocol=fake-subprotocol https://upstream.example/private"
    )
    rendered_logs = []

    class RenderedLogHandler(logging.Handler):
        def emit(self, record):
            rendered_logs.append(self.format(record))

    def update(comp):
        if comp == "nba":
            return {"updated": True, "reason": None, "factors": 4, "samples": 20}
        if comp == "mlb":
            raise RuntimeError(sensitive)
        return {"updated": False, "reason": "no_factor_samples", "samples": 20}

    handler = RenderedLogHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger("app.core.scheduler").addHandler(handler)
    try:
        with patch("app.kernel.learning_service.KernelLearningService") as MockSvc:
            MockSvc.return_value.update_weights = MagicMock(side_effect=update)
            await _job_update_weights_weekly()
    finally:
        logging.getLogger("app.core.scheduler").removeHandler(handler)

    final = captured["finish"][-1]
    exported = json.dumps({"finish": final, "logs": rendered_logs})
    assert final["status"] == "success"
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
        "SELECT secret FROM D:/private/weights.db "
        "Authorization=Bearer fake-api-key ticket=fake-ticket"
    )
    with patch("app.kernel.learning_service.KernelLearningService") as MockSvc:
        MockSvc.return_value.update_weights = MagicMock(
            side_effect=RuntimeError(sensitive)
        )
        await _job_update_weights_weekly()

    final = captured["finish"][-1]
    assert final["status"] == "failed"
    assert final["error"] == "No competition weights updated"
    exported = json.dumps(final)
    assert sensitive not in exported
    assert set(final["result"]["skipped"].values()) == {"error:RuntimeError"}
