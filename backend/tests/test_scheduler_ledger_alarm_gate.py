"""What a blocked `loop_runs` write does to the alarms a failing job must raise.

`_start_run` swallows a ledger insert failure into `None` -- correct in itself,
the ledger is best-effort and the job still runs. `_finish_run` then opened with
`if run_id is None: return`, **before** the failure path that increments
`SCHEDULER_FAILED_RUNS`, forwards the exception to Sentry and dispatches the
operator webhook. So the three channels a job failure is supposed to reach were
all gated on a ledger write succeeding.

Measured with a `BEFORE INSERT ... RAISE(ABORT)` trigger on `loop_runs` -- the
only mechanism that fails the write while leaving every read healthy -- driving
the real `_job_event_auto_resolve` over a temp loop DB. Before the fix a job that
ran and failed was **identical to a job that never ran** at every door: no ledger
row, `runs.event_auto_resolve: null` from `loop_status()`, no counter increment,
no Sentry event, no webhook. One `logger.exception` line was the entire trace.

After the fix that scenario increments `SCHEDULER_FAILED_RUNS{job_name="unknown"}`
and fires both channels with `run_id=None`. The healthy rows, the cold-start row
and the blocked-*success* row are unchanged, which is required: a success raises
no alarm, and fabricating an `unknown` success series would read as a job that
succeeded.
"""
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import quality_metrics as quality_metrics_routes
from app.core import scheduler
from app.core.config import settings
from app.memory import loop_run_store
from app.services import scheduler_failure_alert_dispatcher as dispatcher
from app.utils import metrics
from app.utils import sentry as sentry_utils

BLOCK_INSERT = (
    "CREATE TRIGGER block_loop_run_insert BEFORE INSERT ON loop_runs "
    "BEGIN SELECT RAISE(ABORT, 'ledger write blocked'); END"
)
JOB = "event_auto_resolve"
BOOM = "auto-archive exploded"
SENSITIVE_FAILURE = (
    "SELECT secret FROM ledger at D:/private/runtime/kernel.db via "
    "https://upstream.example/private?api_key=fake-api-key "
    "Authorization: Bearer fake-authorization ticket=fake-ticket "
    "subprotocol=fake-subprotocol user=fake-sensitive-input "
    "Traceback (most recent call last)"
)
SENSITIVE_FRAGMENTS = (
    "SELECT secret",
    "D:/private/runtime/kernel.db",
    "upstream.example",
    "fake-api-key",
    "fake-authorization",
    "fake-ticket",
    "fake-subprotocol",
    "fake-sensitive-input",
    "Traceback",
)


def _counter(job_name: str) -> float:
    """Current `SCHEDULER_FAILED_RUNS` value for one job label.

    Read as a delta rather than an absolute: prometheus_client registries are
    process-global, so other tests in the same session contribute to it.
    """
    for family in metrics.SCHEDULER_FAILED_RUNS.collect():
        for sample in family.samples:
            if sample.name.endswith("_created"):
                continue
            if sample.labels.get("job_name") == job_name:
                return float(sample.value)
    return 0.0


def _gauge(job_name: str) -> float | None:
    for family in metrics.SCHEDULER_LAST_SUCCESS.collect():
        for sample in family.samples:
            if sample.labels.get("job_name") == job_name:
                return float(sample.value)
    return None


@pytest.fixture
def loop_db(tmp_path, monkeypatch):
    """A temp loop DB with the real `loop_runs` schema already created.

    Also clears the two process-global caches this path keeps: `_RUN_TO_JOB`
    (run_id -> job_name) and the dispatcher's per-job cooldown map, which would
    otherwise swallow the second alert of the session.
    """
    path = tmp_path / "loop_alarm.db"
    monkeypatch.setattr(settings, "LOOP_DB_FILE", str(path))
    monkeypatch.setattr(settings, "SCHEDULER_FAILURE_ALERT_ENABLED", True)
    monkeypatch.setattr(settings, "SCHEDULER_FAILURE_ALERT_WEBHOOK_URL", "")
    scheduler._RUN_TO_JOB.clear()
    dispatcher._last_dispatched.clear()
    loop_run_store.recent_runs(limit=1)  # creates the schema through the store
    yield str(path)
    scheduler._RUN_TO_JOB.clear()
    dispatcher._last_dispatched.clear()


def _block_ledger_insert(path: str) -> None:
    import sqlite3

    conn = sqlite3.connect(path)
    try:
        conn.execute(BLOCK_INSERT)
        conn.commit()
    finally:
        conn.close()


class _Alarms:
    """The three channels `_finish_run` owns, captured together."""

    def __init__(self) -> None:
        self.sentry: list[dict] = []
        self.alerts: list[dict] = []
        self.failed_before = _counter(JOB)
        self.unknown_before = _counter("unknown")

    @property
    def failed_delta(self) -> float:
        return _counter(JOB) - self.failed_before

    @property
    def unknown_delta(self) -> float:
        return _counter("unknown") - self.unknown_before


def _capture(alarms: _Alarms):
    def _sentry(message, **kw):
        alarms.sentry.append({"message": message, **kw})

    def _dispatch(**kw):
        alarms.alerts.append(kw)

    return _sentry, _dispatch


async def _run_job(alarms: _Alarms, *, job_fails: bool) -> None:
    """Drive the real `_job_event_auto_resolve` with its body forced either way."""
    sentry, dispatch = _capture(alarms)
    archive = (
        {"side_effect": RuntimeError(BOOM)} if job_fails else {"return_value": 0}
    )
    with patch("app.utils.sentry.capture_message", sentry), \
            patch.object(dispatcher, "dispatch_scheduler_failure_alert", dispatch), \
            patch("app.memory.event_store.auto_archive_expired", **archive), \
            patch(
                "app.services.event_resolve_service.auto_resolve_events",
                new=AsyncMock(return_value={"resolved_count": 0, "checked_count": 0}),
            ):
        await scheduler._job_event_auto_resolve()


async def test_a_failed_job_raises_all_three_alarms_with_a_working_ledger(loop_db):
    """The contrast row. Every assertion here must survive the fix unchanged."""
    alarms = _Alarms()

    await _run_job(alarms, job_fails=True)

    assert alarms.failed_delta == 1
    assert alarms.unknown_delta == 0
    assert [c["error"] for c in alarms.sentry] == [
        "Scheduler job failed: RuntimeError"
    ]
    assert [a["job_name"] for a in alarms.alerts] == [JOB]
    row = loop_run_store.last_run(JOB)
    assert row["status"] == "failed"
    assert row["error"] == "Scheduler job failed: RuntimeError"


async def test_a_scheduler_exception_is_safe_at_every_failure_boundary(
    loop_db,
    monkeypatch,
    caplog,
):
    """A runtime failure must retain identity without exporting its message."""
    sentry_exceptions: list[dict] = []
    sentry_messages: list[dict] = []
    sentry_contexts: list[dict] = []
    webhook_payloads: list[dict] = []

    def capture_sdk_exception(*args, **kwargs):
        sentry_exceptions.append({"args": args, "kwargs": kwargs})

    def capture_sdk_message(message, **context):
        sentry_messages.append({"message": message, **context})

    def set_sdk_context(name, context):
        sentry_contexts.append({"name": name, "context": context})

    def urlopen(req, timeout):
        webhook_payloads.append(json.loads(req.data.decode("utf-8")))
        response = MagicMock(status=204)
        response.__enter__.return_value = response
        return response

    monkeypatch.setattr(
        settings,
        "SCHEDULER_FAILURE_ALERT_WEBHOOK_URL",
        "https://hooks.example/scheduler",
    )
    sensitive_exc = RuntimeError(SENSITIVE_FAILURE)
    app = FastAPI()
    app.include_router(quality_metrics_routes.router)

    rendered_logs: list[str] = []

    class RenderedLogHandler(logging.Handler):
        def emit(self, record):
            rendered_logs.append(self.format(record))

    log_handler = RenderedLogHandler()
    log_handler.setFormatter(logging.Formatter("%(message)s"))
    scheduler.logger.addHandler(log_handler)
    dispatcher.logger.addHandler(log_handler)

    try:
        with patch.object(sentry_utils, "_SENTRY_AVAILABLE", True), \
                patch.object(
                    sentry_utils.sentry_sdk,
                    "capture_exception",
                    side_effect=capture_sdk_exception,
                ), \
                patch.object(
                    sentry_utils.sentry_sdk,
                    "capture_message",
                    side_effect=capture_sdk_message,
                ), \
                patch.object(
                    sentry_utils.sentry_sdk,
                    "set_context",
                    side_effect=set_sdk_context,
                ), \
                patch.object(
                    dispatcher.urllib_request,
                    "urlopen",
                    side_effect=urlopen,
                ), \
                patch(
                    "app.memory.event_store.auto_archive_expired",
                    side_effect=sensitive_exc,
                ), \
                patch(
                    "app.services.event_resolve_service.auto_resolve_events",
                    new=AsyncMock(
                        return_value={"resolved_count": 0, "checked_count": 0}
                    ),
                ), \
                caplog.at_level("WARNING"):
            await scheduler._job_event_auto_resolve()
    finally:
        scheduler.logger.removeHandler(log_handler)
        dispatcher.logger.removeHandler(log_handler)

    with patch.object(settings, "SCHEDULER_ENABLED", False):
        response = TestClient(app).get("/quality-metrics/anomalies")

    row = loop_run_store.last_run(JOB)
    anomaly = next(
        item
        for item in response.json()["anomalies"]
        if item["code"] == "scheduler_job_failed"
        and item["detail"]["job_name"] == JOB
    )
    exported = json.dumps(
        {
            "ledger": row,
            "anomaly": anomaly,
            "sentry_exceptions": sentry_exceptions,
            "sentry_messages": sentry_messages,
            "sentry_contexts": sentry_contexts,
            "webhook_payloads": webhook_payloads,
            "logs": rendered_logs,
        },
        default=str,
    )

    assert row["status"] == "failed"
    assert row["error"] == "Scheduler job failed: RuntimeError"
    assert anomaly["code"] == "scheduler_job_failed"
    assert anomaly["detail"]["error"] == "Scheduler job failed: RuntimeError"
    assert sentry_exceptions == []
    assert sentry_contexts == [
        {
            "name": "pmrf",
            "context": {
                "code": "scheduler_job_failed",
                "job_name": JOB,
                "run_id": row["id"],
                "error": "Scheduler job failed: RuntimeError",
                "exc_type": "RuntimeError",
            },
        },
        {
            "name": "pmrf",
            "context": {
                "code": "scheduler_job_failed",
                "severity": "warning",
                "job_name": JOB,
                "run_id": row["id"],
                "error": "Scheduler job failed: RuntimeError",
                "exc_type": "RuntimeError",
            },
        },
    ]
    assert sentry_messages == [
        {
            "message": "scheduler job failed",
            "level": "error",
        },
        {
            "message": f"scheduler job failed: {JOB}",
            "level": "warning",
        },
    ]
    assert webhook_payloads == [
        {
            "code": "scheduler_job_failed",
            "severity": "warning",
            "job_name": JOB,
            "run_id": row["id"],
            "error": "Scheduler job failed: RuntimeError",
            "exc_type": "RuntimeError",
        }
    ]
    for fragment in SENSITIVE_FRAGMENTS:
        assert fragment not in exported
    assert "Traceback" not in exported
    assert "RuntimeError" in exported


async def test_title_translation_failure_is_safe_at_every_boundary(
    loop_db,
    monkeypatch,
):
    rendered_logs: list[str] = []

    class RenderedLogHandler(logging.Handler):
        def emit(self, record):
            rendered_logs.append(self.format(record))

    log_handler = RenderedLogHandler()
    log_handler.setFormatter(logging.Formatter("%(message)s"))
    scheduler.logger.addHandler(log_handler)
    monkeypatch.setattr(
        "app.memory.event_store.list_all_events",
        lambda: [{"record": {"event_title": "unsafe title"}}],
    )
    monkeypatch.setattr(
        "app.services.translation_service.looks_chinese",
        lambda value: False,
    )

    async def fail_translation(value):
        raise RuntimeError(SENSITIVE_FAILURE)

    monkeypatch.setattr(
        "app.services.probability_engine_service.translate_title",
        fail_translation,
    )

    try:
        await scheduler._job_translate_titles()
    finally:
        scheduler.logger.removeHandler(log_handler)

    row = loop_run_store.last_run("translate_titles")
    exported = json.dumps(
        {"ledger": row, "logs": rendered_logs},
        default=str,
    )

    assert row["status"] == "failed"
    assert row["error"] == "Scheduler job failed: RuntimeError"
    for fragment in SENSITIVE_FRAGMENTS:
        assert fragment not in exported
    assert "Traceback" not in exported
    assert "RuntimeError" in exported


async def test_prediction_update_exception_result_is_failed_and_safe_at_every_boundary(
    loop_db,
    monkeypatch,
):
    job_name = "world_cup_prediction_update"
    sentry_messages: list[dict] = []
    webhook_payloads: list[dict] = []
    failed_before = _counter(job_name)
    rendered_logs: list[str] = []

    class RenderedLogHandler(logging.Handler):
        def emit(self, record):
            rendered_logs.append(self.format(record))

    def fail_fixture_sync():
        raise RuntimeError(SENSITIVE_FAILURE)

    log_handler = RenderedLogHandler()
    log_handler.setFormatter(logging.Formatter("%(message)s"))
    prediction_logger = logging.getLogger(
        "app.services.world_cup_prediction_scheduler"
    )
    scheduler.logger.addHandler(log_handler)
    prediction_logger.addHandler(log_handler)
    monkeypatch.setattr(
        "app.services.world_cup_prediction_scheduler.sync_world_cup_fixtures",
        fail_fixture_sync,
    )
    monkeypatch.setattr(
        "app.utils.sentry.capture_message",
        lambda message, **kw: sentry_messages.append({"message": message, **kw}),
    )
    monkeypatch.setattr(
        dispatcher,
        "dispatch_scheduler_failure_alert",
        lambda **kw: webhook_payloads.append(kw),
    )

    try:
        await scheduler._job_world_cup_prediction_update()
    finally:
        scheduler.logger.removeHandler(log_handler)
        prediction_logger.removeHandler(log_handler)

    row = loop_run_store.last_run(job_name)
    exported = json.dumps(
        {
            "ledger": row,
            "sentry": sentry_messages,
            "webhook": webhook_payloads,
            "logs": rendered_logs,
        },
        default=str,
    )

    assert row["status"] == "failed"
    assert row["error"] == "World Cup prediction update failed"
    assert row["result"] == {
        "status": "error",
        "error": "World Cup daily update failed",
        "step": None,
    }
    assert _counter(job_name) - failed_before == 1
    assert [item["error"] for item in sentry_messages] == [
        "World Cup prediction update failed"
    ]
    assert [item["job_name"] for item in webhook_payloads] == [job_name]
    for fragment in SENSITIVE_FRAGMENTS:
        assert fragment not in exported
    assert "Traceback" not in exported
    assert "RuntimeError" in exported


async def test_prediction_update_returned_sync_error_is_safe_at_every_boundary(
    loop_db,
    monkeypatch,
):
    job_name = "world_cup_prediction_update"
    sentry_messages: list[dict] = []
    webhook_payloads: list[dict] = []
    rendered_logs: list[str] = []

    class RenderedLogHandler(logging.Handler):
        def emit(self, record):
            rendered_logs.append(self.format(record))

    log_handler = RenderedLogHandler()
    log_handler.setFormatter(logging.Formatter("%(message)s"))
    prediction_logger = logging.getLogger(
        "app.services.world_cup_prediction_scheduler"
    )
    scheduler.logger.addHandler(log_handler)
    prediction_logger.addHandler(log_handler)
    monkeypatch.setattr(
        "app.services.world_cup_prediction_scheduler.sync_world_cup_fixtures",
        lambda: {"status": "error", "error": SENSITIVE_FAILURE},
    )
    monkeypatch.setattr(
        "app.utils.sentry.capture_message",
        lambda message, **kw: sentry_messages.append({"message": message, **kw}),
    )
    monkeypatch.setattr(
        dispatcher,
        "dispatch_scheduler_failure_alert",
        lambda **kw: webhook_payloads.append(kw),
    )

    try:
        await scheduler._job_world_cup_prediction_update()
    finally:
        scheduler.logger.removeHandler(log_handler)
        prediction_logger.removeHandler(log_handler)

    row = loop_run_store.last_run(job_name)
    exported = json.dumps(
        {"ledger": row, "sentry": sentry_messages,
         "webhook": webhook_payloads, "logs": rendered_logs},
        default=str,
    )
    assert row["status"] == "failed"
    assert row["error"] == "World Cup prediction update failed"
    assert row["result"] == {
        "status": "error",
        "error": "World Cup fixture sync failed",
        "step": "fixture_sync",
    }
    assert [item["error"] for item in sentry_messages] == [
        "World Cup prediction update failed"
    ]
    assert [item["job_name"] for item in webhook_payloads] == [job_name]
    for fragment in SENSITIVE_FRAGMENTS:
        assert fragment not in exported
    assert "Traceback" not in exported


async def test_a_failed_job_still_alarms_when_the_ledger_write_is_blocked(loop_db):
    """The defect. Before the fix all three of these were empty."""
    _block_ledger_insert(loop_db)
    alarms = _Alarms()

    await _run_job(alarms, job_fails=True)

    assert alarms.unknown_delta == 1, "SCHEDULER_FAILED_RUNS never moved"
    assert [c["error"] for c in alarms.sentry] == [
        "Scheduler job failed: RuntimeError"
    ]
    assert [(a["job_name"], a["run_id"]) for a in alarms.alerts] == [("unknown", None)]
    # The row itself is genuinely lost -- nothing was written to recover.
    assert loop_run_store.last_run(JOB) is None


async def test_a_job_that_never_ran_raises_no_alarm(loop_db):
    """The other contrast row: silence must still mean silence."""
    alarms = _Alarms()

    assert alarms.failed_delta == 0
    assert alarms.unknown_delta == 0
    assert alarms.sentry == []
    assert alarms.alerts == []
    assert loop_run_store.last_run(JOB) is None


async def test_a_successful_job_writes_its_row_and_raises_no_alarm(loop_db):
    alarms = _Alarms()
    before = _gauge(JOB)

    await _run_job(alarms, job_fails=False)

    assert alarms.failed_delta == 0
    assert alarms.unknown_delta == 0
    assert alarms.sentry == []
    assert alarms.alerts == []
    assert loop_run_store.last_run(JOB)["status"] == "success"
    assert _gauge(JOB) != before


async def test_a_blocked_ledger_does_not_fabricate_an_unknown_success(loop_db):
    """The deliberate asymmetry: the success gauge stays gated on a real row.

    A failure has an alarm to raise with or without a ledger row. A success does
    not, and the gauge is keyed by job -- with no row the job name is
    unrecoverable, so an `unknown` success series would read as a job that
    succeeded. `_start_run`'s `logger.exception` already reports the lost row.
    """
    _block_ledger_insert(loop_db)
    alarms = _Alarms()
    before = _gauge("unknown")

    await _run_job(alarms, job_fails=False)

    assert alarms.sentry == []
    assert alarms.alerts == []
    assert _gauge("unknown") == before


def test_finish_run_leaves_the_ledger_alone_when_there_is_no_run_id(loop_db):
    """The fix must not start calling the store with a None run_id."""
    with patch.object(loop_run_store, "finish_run") as store_finish:
        scheduler._finish_run(None, "failed", error=BOOM, exc=RuntimeError(BOOM))
        scheduler._finish_run(None, "success", result={"ok": 1})

    assert store_finish.call_args_list == []


def test_finish_run_updates_the_ledger_exactly_once_with_a_run_id(loop_db):
    run_id = scheduler._start_run(JOB)
    assert run_id is not None

    with patch.object(loop_run_store, "finish_run") as store_finish:
        scheduler._finish_run(run_id, "success", result={"ok": 1})

    assert store_finish.call_count == 1
    assert store_finish.call_args.args == (run_id, "success")


def test_finish_run_reports_store_failure_without_exception_text(loop_db):
    rendered_logs: list[str] = []

    class RenderedLogHandler(logging.Handler):
        def emit(self, record):
            rendered_logs.append(self.format(record))

    log_handler = RenderedLogHandler()
    log_handler.setFormatter(logging.Formatter("%(message)s"))
    scheduler.logger.addHandler(log_handler)
    run_id = scheduler._start_run(JOB)
    assert run_id is not None

    try:
        with patch.object(
            loop_run_store,
            "finish_run",
            side_effect=RuntimeError(SENSITIVE_FAILURE),
        ):
            scheduler._finish_run(run_id, "success", result={"ok": 1})
    finally:
        scheduler.logger.removeHandler(log_handler)

    exported = "\n".join(rendered_logs)
    assert "Failed to finish run ledger" in exported
    assert "RuntimeError" in exported
    for fragment in SENSITIVE_FRAGMENTS:
        assert fragment not in exported
    assert "Traceback" not in exported


def test_a_blocked_ledger_insert_is_reported_without_exception_text(
    loop_db,
):
    rendered_logs: list[str] = []

    class RenderedLogHandler(logging.Handler):
        def emit(self, record):
            rendered_logs.append(self.format(record))

    log_handler = RenderedLogHandler()
    log_handler.setFormatter(logging.Formatter("%(message)s"))
    scheduler.logger.addHandler(log_handler)
    _block_ledger_insert(loop_db)

    try:
        run_id = scheduler._start_run(JOB)
    finally:
        scheduler.logger.removeHandler(log_handler)

    exported = "\n".join(rendered_logs)
    assert run_id is None
    assert "Failed to start run ledger" in exported
    assert "IntegrityError" in exported
    assert "ledger write blocked" not in exported
    assert "Traceback" not in exported
    assert JOB not in scheduler._RUN_TO_JOB.values()
