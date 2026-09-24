"""The one registered job that recorded nothing, at either door.

`world_cup_live_update` fires every 2 minutes -- 720 runs/day, 98% of all
interval-triggered scheduler runs -- and was the only one of the 22 jobs
registered by `start_scheduler` that called neither `_start_run` nor
`_finish_run` (measured by AST over the real registration: 20 jobs name their
`add_job` id exactly, `translate_titles_startup` deliberately shares
`translate_titles`' ledger name, and this one had no name at all).

Its other door was already dead. The guard `if result.get("matches_checked", 0)
> 0` reads a key `update_live_predictions()` does not return, and neither do
`live_count` or `updated`; the service returns `in_play_count`,
`pre_match_updated` and `newly_finished_scored`. So `.get(..., 0)` was always 0
and the log line never emitted once.

Measured before the fix, driving the real `_job_world_cup_live_update` over a
temp loop DB with the service returning 2 in play / 1 pre-match updated / 3
newly finished scored:

    ledger row:             None
    loop_status runs key:   False
    scheduler log lines:    []

and on a raised exception:

    ledger row:             None
    SCHEDULER_FAILED_RUNS:  (0.0, 0.0)
    Sentry events:          []
    operator webhooks:      []
    scheduler log lines:    ['[Scheduler] Live update failed: live update exploded']

One `logger.exception` was the entire trace of a failure, and a *successful* run
that scored three finished matches was indistinguishable from a job that had
never been registered.
"""
import asyncio
import logging

import pytest

from app.core import scheduler
from app.core.config import settings
from app.memory import loop_run_store
from app.services import scheduler_failure_alert_dispatcher as dispatcher
from app.utils import metrics

JOB = "world_cup_live_update"
BOOM = "live update exploded"

# What the service really returns, per `update_live_predictions()`. `actions`
# repeats the same three counts under other names, so nothing reads it here.
BUSY_RUN = {
    "status": "ok",
    "timestamp": "2026-09-06T02:00:00+00:00",
    "in_play_count": 2,
    "pre_match_updated": 1,
    "newly_finished_scored": 3,
    "actions": {
        "pre_match_predictions": 1,
        "post_match_scoring": 3,
        "in_play_monitoring": 2,
    },
}
QUIET_RUN = {
    "status": "ok",
    "timestamp": "2026-09-06T02:02:00+00:00",
    "in_play_count": 0,
    "pre_match_updated": 0,
    "newly_finished_scored": 0,
    "actions": {
        "pre_match_predictions": 0,
        "post_match_scoring": 0,
        "in_play_monitoring": 0,
    },
}


def _counter(job_name: str) -> float:
    """`SCHEDULER_FAILED_RUNS` for one label, read as a delta.

    prometheus_client registries are process-global, so other tests in the
    session contribute to the absolute value.
    """
    for family in metrics.SCHEDULER_FAILED_RUNS.collect():
        for sample in family.samples:
            if sample.name.endswith("_created"):
                continue
            if sample.labels.get("job_name") == job_name:
                return float(sample.value)
    return 0.0


@pytest.fixture
def loop_db(tmp_path, monkeypatch):
    """A temp loop DB with the real `loop_runs` schema, and clean caches."""
    monkeypatch.setattr(settings, "LOOP_DB_FILE", str(tmp_path / "live_ledger.db"))
    monkeypatch.setattr(settings, "SCHEDULER_FAILURE_ALERT_ENABLED", True)
    monkeypatch.setattr(settings, "SCHEDULER_FAILURE_ALERT_WEBHOOK_URL", "")
    scheduler._RUN_TO_JOB.clear()
    dispatcher._last_dispatched.clear()
    loop_run_store.recent_runs(limit=1)  # creates the schema through the store
    yield
    scheduler._RUN_TO_JOB.clear()
    dispatcher._last_dispatched.clear()


class _Alarms:
    """The three channels `_finish_run` owns, captured together."""

    def __init__(self) -> None:
        self.sentry: list[dict] = []
        self.alerts: list[dict] = []
        self._failed_before = _counter(JOB)

    @property
    def failed_delta(self) -> float:
        return _counter(JOB) - self._failed_before


async def _run_job(monkeypatch, alarms: _Alarms, outcome) -> None:
    """Drive the real job with the real `_start_run` / `_finish_run`.

    `outcome` is either the dict the service returns or an exception to raise.
    Only the service call and the three alarm sinks are replaced.
    """
    async def fake_update():
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(
        "app.services.world_cup_live_update_service.update_live_predictions",
        fake_update,
    )
    monkeypatch.setattr(
        "app.utils.sentry.capture_message",
        lambda message, **kw: alarms.sentry.append({"message": message, **kw}),
    )
    monkeypatch.setattr(
        dispatcher, "dispatch_scheduler_failure_alert",
        lambda **kw: alarms.alerts.append(kw),
    )
    await scheduler._job_world_cup_live_update()


async def test_a_run_that_did_work_is_recorded_in_the_ledger(loop_db, monkeypatch):
    alarms = _Alarms()

    await _run_job(monkeypatch, alarms, BUSY_RUN)

    row = loop_run_store.last_run(JOB)
    assert row is not None, "the job wrote no ledger row"
    assert row["status"] == "success"
    assert row["result"] == {
        "status": "ok",
        "timestamp": BUSY_RUN["timestamp"],
        "in_play_count": 2,
        "pre_match_updated": 1,
        "newly_finished_scored": 3,
    }


async def test_the_summary_reads_the_keys_the_service_actually_returns(loop_db, monkeypatch):
    """The dead-key failure, in the shape it would take in the ledger.

    A summary built from `matches_checked` / `live_count` / `updated` -- the
    names the log guard used -- records three zeros for this run and reads as a
    job with nothing to do, which is exactly what 718 of its 720 daily runs
    genuinely look like. Asserting the *values*, not the keys, is what separates
    them.
    """
    await _run_job(monkeypatch, _Alarms(), BUSY_RUN)

    result = loop_run_store.last_run(JOB)["result"]
    assert [result.get("in_play_count"), result.get("pre_match_updated"),
            result.get("newly_finished_scored")] == [2, 1, 3]


async def test_a_quiet_run_is_recorded_too(loop_db, monkeypatch):
    """Nothing to do is the normal answer, so it must not look like silence.

    718 of the 720 daily runs find no live match. If only a busy run wrote a
    row, an absent row would mean either "no matches in play" or "the job is
    dead", and the ledger is the only place that distinguishes them --
    `/api/health` reports `runs` straight from it.
    """
    await _run_job(monkeypatch, _Alarms(), QUIET_RUN)

    row = loop_run_store.last_run(JOB)
    assert row is not None, "a quiet run left no trace, so a dead job looks quiet"
    assert row["status"] == "success"
    assert row["result"]["in_play_count"] == 0


async def test_a_failed_run_reaches_all_three_alarm_channels(loop_db, monkeypatch):
    alarms = _Alarms()

    await _run_job(monkeypatch, alarms, RuntimeError(BOOM))

    row = loop_run_store.last_run(JOB)
    assert row["status"] == "failed"
    assert row["error"] == "Scheduler job failed: RuntimeError"
    assert alarms.failed_delta == 1, "SCHEDULER_FAILED_RUNS never moved"
    assert [c["error"] for c in alarms.sentry] == [
        "Scheduler job failed: RuntimeError"
    ]
    assert [a["job_name"] for a in alarms.alerts] == [JOB]


async def test_a_failure_does_not_escape_into_the_scheduler(loop_db, monkeypatch):
    """Unchanged behaviour: the job still swallows its own exception.

    APScheduler would log and drop it anyway; what matters is that adding the
    ledger did not turn a handled failure into an unhandled one.
    """
    await _run_job(monkeypatch, _Alarms(), RuntimeError(BOOM))  # must not raise


async def test_the_job_appears_in_the_status_payload(loop_db, monkeypatch):
    """`/api/health` derives its watched set from the ledger's job names.

    With no row the job was absent from `runs` entirely, so its last outcome
    could never make health `degraded`.
    """
    await _run_job(monkeypatch, _Alarms(), RuntimeError(BOOM))

    runs = {row["job_name"]: row for row in loop_run_store.latest_run_per_job()}
    assert JOB in runs
    assert runs[JOB]["status"] == "failed"


async def test_the_ledger_name_is_the_registered_job_id(loop_db, monkeypatch):
    """A mistyped ledger name is invisible: it just adds a stranger to `runs`.

    Both halves are read from the real objects -- the id from
    `start_scheduler`'s own `add_job` call, the name from the row the job
    wrote -- so neither side is a literal typed twice.
    """
    from unittest.mock import MagicMock, patch

    fake = MagicMock()
    fake.running = False
    with patch.object(scheduler, "scheduler", fake), \
            patch.object(settings, "SCHEDULER_LOCK_ENABLED", False):
        scheduler.start_scheduler()
    registered = [
        call.kwargs.get("id")
        for call in fake.add_job.call_args_list
        if call.args and call.args[0] is scheduler._job_world_cup_live_update
    ]
    assert registered == [JOB]

    await _run_job(monkeypatch, _Alarms(), QUIET_RUN)
    assert loop_run_store.last_run(registered[0]) is not None


def test_the_log_line_reports_a_run_that_did_something(loop_db, monkeypatch, caplog):
    """The guard used to read keys the service never returns, so it never fired.

    Kept conditional on purpose: 718 quiet runs a day would be 718 log lines
    saying nothing happened. The ledger row is what covers the quiet case.
    """
    with caplog.at_level(logging.INFO, logger="app.core.scheduler"):
        asyncio.run(_run_job(monkeypatch, _Alarms(), BUSY_RUN))
    busy = [r.getMessage() for r in caplog.records if "Live update" in r.getMessage()]
    assert len(busy) == 1, f"expected one summary line, got {busy}"
    assert "in_play=2" in busy[0]
    assert "pre_match_updated=1" in busy[0]
    assert "newly_scored=3" in busy[0]

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="app.core.scheduler"):
        asyncio.run(_run_job(monkeypatch, _Alarms(), QUIET_RUN))
    quiet = [r.getMessage() for r in caplog.records if "Live update" in r.getMessage()]
    assert quiet == []


# The one job that shares another job's ledger name, and why. The startup
# variant calls the same `_job_translate_titles` body, so it writes rows under
# `translate_titles` -- deliberate: the two are one job on two triggers, and a
# separate name would split its history in `runs`.
_SHARED_LEDGER_NAME = {"translate_titles_startup": "translate_titles"}


def test_every_registered_job_records_a_run():
    """The partition, so the next job cannot arrive unwatched.

    `world_cup_live_update` was the only one of 22 missing, which is exactly why
    a hand-kept list of "jobs with a ledger" would not have caught it. Both
    sides are read from the source -- the ids from `start_scheduler`'s own
    `add_job` calls, the names from the `_start_run` literal inside each job
    body -- and the only allowed mismatch is declared above.

    Read statically rather than by calling `start_scheduler`: half the jobs are
    behind feature flags, so a runtime census would cover whichever subset the
    flags happen to enable and a flag renamed tomorrow would silently shrink it.
    """
    import ast
    from pathlib import Path

    bodies = {
        node.name: node
        for node in ast.walk(
            ast.parse(Path(scheduler.__file__).read_text(encoding="utf-8-sig"))
        )
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    add_jobs = [
        node
        for node in ast.walk(bodies["start_scheduler"])
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_job"
    ]
    assert len(add_jobs) >= 22, f"only {len(add_jobs)} add_job calls found"

    registered = {}
    for node in add_jobs:
        job_id = next(
            (kw.value.value for kw in node.keywords
             if kw.arg == "id" and isinstance(kw.value, ast.Constant)),
            None,
        )
        assert job_id is not None, f"add_job at line {node.lineno} has no literal id"
        assert node.args and isinstance(node.args[0], ast.Name), (
            f"add_job id={job_id!r} does not name a plain function"
        )
        registered[job_id] = node.args[0].id

    def ledger_names(fn_name: str) -> list[str]:
        return [
            node.args[0].value
            for node in ast.walk(bodies[fn_name])
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_start_run"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ]

    assert {
        job_id: ledger_names(fn_name)
        for job_id, fn_name in sorted(registered.items())
    } == {
        job_id: [_SHARED_LEDGER_NAME.get(job_id, job_id)]
        for job_id in sorted(registered)
    }

