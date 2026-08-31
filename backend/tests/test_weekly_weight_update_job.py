# backend/tests/test_weekly_weight_update_job.py
"""The weekly learning job must report a rewrite, a skip, and a failure honestly."""
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
    assert "no competition updated" in final["error"]


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
    assert final["result"]["skipped"]["mlb"].startswith("error:database unavailable")
