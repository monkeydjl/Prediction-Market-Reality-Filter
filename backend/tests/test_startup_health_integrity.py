"""A core component that failed at startup must not report healthy.

`lifespan` wrapped six startup steps in `try/except Exception` and logged a
warning. Two of them maintain or initialise core state, and for those the warning
was the *only* trace: the process came up, `/api/health` answered
`200 {"status": "ok"}`, and the finding survived only in log scrollback.

The `maintain_all()` contract is what made this easy to miss. It reports failures
by *returning* `ok=False`, and `lifespan` handled that path correctly by writing a
failed ledger row. But the function can also *raise* -- `sqlite3.DatabaseError`
from a file too damaged for `PRAGMA integrity_check` to parse, `OSError` from the
volume underneath it -- and that path went to the same `logger.warning` as the
optional steps. So the more damaged the database, the healthier the app looked:
corruption `integrity_check` could describe degraded loudly, corruption it could
not parse degraded silently.

A check has three outcomes, not two: passed, failed, and could-not-run. The last
one is not a pass.
"""
import contextlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.memory import loop_run_store  # noqa: E402

HEALTHY_MAINTAIN_ALL = {"ok": True, "failed": [], "stores": {}}


class _FakeScheduler:
    """Stands in for the APScheduler singleton.

    `api_health` degrades on a stopped scheduler as well as on a failed run, so
    without this every test below would pass for the wrong reason.
    """

    def __init__(self, running: bool) -> None:
        self.running = running


@contextlib.contextmanager
def isolated_ledger():
    """Give one test its own loop_runs ledger.

    The session temp loop DB is shared, and `latest_run_per_job` is global, so a
    failed row written by a sibling test would degrade an unrelated health
    assertion here. An own file also makes "survives a restart" mean what it says.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "ledger.db"
        with patch.object(settings, "LOOP_DB_FILE", str(db)):
            loop_run_store._INITIALIZED.discard(str(db))
            yield db


@contextlib.contextmanager
def booted(
    *,
    maintain=None,
    maintain_all=None,
    scheduler_running: bool = True,
    **setting_overrides,
):
    """Drive one real lifespan startup and hand back a live client.

    Only the pieces a fault is injected into are patched; the ledger writes,
    `loop_status` and `/api/health` all run for real, because the whole question
    is whether a startup failure reaches the health probe through storage.
    """
    from app.main import app

    stack = contextlib.ExitStack()
    with stack:
        stack.enter_context(patch.object(settings, "API_WRITE_KEY", "secret"))
        for name, value in setting_overrides.items():
            stack.enter_context(patch.object(settings, name, value))
        stack.enter_context(patch(
            "app.main.sqlite_db.maintain",
            **(maintain or {"return_value": {"ok": True}}),
        ))
        stack.enter_context(patch(
            "app.main.sqlite_db.maintain_all",
            **(maintain_all or {"return_value": HEALTHY_MAINTAIN_ALL}),
        ))
        stack.enter_context(patch(
            "app.core.scheduler.scheduler", _FakeScheduler(scheduler_running)
        ))
        stack.enter_context(patch(
            "app.core.scheduler.scheduler_start_skipped_due_to_lock",
            return_value=False,
        ))
        stack.enter_context(patch("app.main.start_scheduler", lambda: True))
        stack.enter_context(patch("app.main.stop_scheduler", lambda: None))
        with TestClient(app) as client:
            yield client


def failed_jobs(db_path) -> list[str]:
    """Job names whose newest ledger row is `failed`, read back from SQLite."""
    return [
        str(run["job_name"])
        for run in loop_run_store.latest_run_per_job()
        if run.get("status") == "failed"
    ]


def status_by_job(db_path) -> dict[str, str]:
    """Every job's newest row status, `failed` or not.

    `failed_jobs` deliberately answers the question /api/health asks. This one
    answers what the ledger actually holds, which is how a row that is neither
    `success` nor `failed` -- a `running` row nothing will ever finish -- becomes
    visible to a test.
    """
    return {
        str(run["job_name"]): str(run.get("status") or "")
        for run in loop_run_store.latest_run_per_job()
    }


class CoreComponentFailureTests(unittest.TestCase):
    """Each core component, failed by injection, must reach /api/health."""

    def test_healthy_startup_still_returns_200(self):
        """The negative arm, first. Every test below asserts 503; without this
        one, code that always degraded would satisfy all of them."""
        with isolated_ledger() as db:
            with booted() as client:
                resp = client.get("/api/health")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["status"], "ok")
            self.assertEqual(failed_jobs(db), [])

    def test_core_loop_db_maintain_raising_aborts_the_boot(self):
        """The loop DB keeps fail-loud semantics: it backs the ledger every other
        component reports through, so there is nowhere to record its own failure.
        """
        with isolated_ledger():
            with self.assertRaisesRegex(RuntimeError, "loop db"):
                with booted(maintain={"side_effect": RuntimeError("bad loop db")}):
                    pass

    def test_maintain_all_raising_degrades_health_instead_of_warning(self):
        """The measured defect: `maintain_all` raising went to logger.warning, so
        a database too damaged for PRAGMA integrity_check to parse booted clean.
        """
        from app.main import STARTUP_SQLITE_INTEGRITY_JOB

        with isolated_ledger() as db:
            with booted(
                maintain_all={"side_effect": OSError("volume went away")}
            ) as client:
                resp = client.get("/api/health")

            self.assertEqual(resp.status_code, 503)
            body = resp.json()
            self.assertEqual(body["status"], "degraded")
            self.assertIn(STARTUP_SQLITE_INTEGRITY_JOB, body["failed_runs"])
            self.assertIn(STARTUP_SQLITE_INTEGRITY_JOB, failed_jobs(db))

    def test_maintain_all_reporting_not_ok_degrades_health(self):
        """The path that already worked, pinned so the fix does not lose it."""
        from app.main import STARTUP_SQLITE_INTEGRITY_JOB

        with isolated_ledger() as db:
            with booted(maintain_all={"return_value": {
                "ok": False,
                "failed": ["KERNEL_DB_FILE"],
                "stores": {"KERNEL_DB_FILE": {"ok": False, "error": "malformed"}},
            }}) as client:
                resp = client.get("/api/health")

            self.assertEqual(resp.status_code, 503)
            self.assertIn(
                STARTUP_SQLITE_INTEGRITY_JOB, resp.json()["failed_runs"]
            )
            self.assertIn(STARTUP_SQLITE_INTEGRITY_JOB, failed_jobs(db))

    def test_prediction_db_init_raising_degrades_health(self):
        from app.main import STARTUP_PREDICTION_DB_JOB

        with isolated_ledger() as db:
            with patch(
                "app.utils.prediction_db.init_prediction_db",
                side_effect=RuntimeError("no such column"),
            ):
                with booted() as client:
                    resp = client.get("/api/health")

            self.assertEqual(resp.status_code, 503)
            self.assertIn(STARTUP_PREDICTION_DB_JOB, resp.json()["failed_runs"])
            self.assertIn(STARTUP_PREDICTION_DB_JOB, failed_jobs(db))


class OptionalComponentFailureTests(unittest.TestCase):
    """An optional component failing must not take the system down with it.

    The split is the point of the change. Marking everything core would trade a
    false-healthy for a false-unavailable: an unreadable World Cup fact file would
    503 an API whose event loop, predictions and calibration are all fine.
    """

    def test_orphan_prediction_reconcile_failure_leaves_health_ok(self):
        with isolated_ledger() as db:
            with patch(
                "app.services.event_resolve_service.reconcile_predictions",
                side_effect=RuntimeError("reconcile blew up"),
            ):
                with booted() as client:
                    resp = client.get("/api/health")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["status"], "ok")
            self.assertEqual(failed_jobs(db), [])

    def test_world_cup_scoring_failure_leaves_health_ok(self):
        with isolated_ledger() as db:
            with patch(
                "app.services.world_cup_scoring_service.score_all_finished_matches",
                side_effect=RuntimeError("scoring blew up"),
            ):
                with booted() as client:
                    resp = client.get("/api/health")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(failed_jobs(db), [])

    def test_optimization_task_reconcile_failure_leaves_health_ok(self):
        with isolated_ledger() as db:
            with patch(
                "app.services.optimization_task_manager.get_task_manager",
                side_effect=RuntimeError("task manager blew up"),
            ):
                with booted() as client:
                    resp = client.get("/api/health")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(failed_jobs(db), [])

    def test_the_two_component_sets_are_disjoint(self):
        from app.main import (
            CORE_STARTUP_COMPONENTS,
            OPTIONAL_STARTUP_COMPONENTS,
        )

        core = set(CORE_STARTUP_COMPONENTS)
        optional = set(OPTIONAL_STARTUP_COMPONENTS)
        self.assertTrue(core)
        self.assertTrue(optional)
        self.assertEqual(
            core & optional, set(),
            "a component cannot be both core and optional",
        )


class StartupPartitionMatchesTheSourceTests(unittest.TestCase):
    """The declared split has to describe the code, not run beside it.

    Two hand-maintained tuples and a disjointness check would be satisfied by a
    seventh startup step that joined neither -- which is precisely the shape of
    the original defect, where the `maintain_all` raise belonged to the core set
    and was handled by the optional one. So the sets are asserted against a scan
    of `lifespan`'s own `try` blocks: every one must be classified, and the counts
    must match what is declared.
    """

    @staticmethod
    def _lifespan_node():
        import ast

        source = (_BACKEND / "app" / "main.py").read_text(encoding="utf-8-sig")
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan":
                return node
        raise AssertionError("app/main.py no longer defines an async `lifespan`")

    @classmethod
    def _classified_tries(cls):
        """Every `try` in `lifespan`, split by whether it reports through the
        ledger. A core block calls `_record_core_startup_outcome`; an optional one
        does not."""
        import ast

        core, optional = [], []
        for node in ast.walk(cls._lifespan_node()):
            # `node.handlers` excludes the bare `try: yield / finally: stop`
            # around the app's serving phase -- that one guards shutdown, it does
            # not absorb a startup failure.
            if not isinstance(node, ast.Try) or not node.handlers:
                continue
            calls = {
                inner.func.id
                for inner in ast.walk(node)
                if isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
            }
            if "_record_core_startup_outcome" in calls:
                core.append(node)
            else:
                optional.append(node)
        return core, optional

    def test_every_guarded_startup_step_is_declared_in_exactly_one_set(self):
        from app.main import (
            CORE_STARTUP_COMPONENTS,
            OPTIONAL_STARTUP_COMPONENTS,
        )

        core_tries, optional_tries = self._classified_tries()
        # The loop DB is core but has no `try`: it aborts the boot instead of
        # reporting, because it backs the ledger the others report through.
        self.assertEqual(
            len(core_tries), len(CORE_STARTUP_COMPONENTS) - 1,
            "a core startup step is missing its _record_core_startup_outcome "
            "call, or CORE_STARTUP_COMPONENTS is stale",
        )
        self.assertEqual(
            len(optional_tries), len(OPTIONAL_STARTUP_COMPONENTS),
            "a startup step is guarded by try/except but declared in neither "
            "CORE_STARTUP_COMPONENTS nor OPTIONAL_STARTUP_COMPONENTS",
        )

    def test_the_loop_db_check_is_not_wrapped_in_a_try(self):
        """What keeps it fail-loud. Wrapping it would make the ledger's own
        backing store the one failure nothing can report."""
        import ast

        for node in ast.walk(self._lifespan_node()):
            if not isinstance(node, ast.Try):
                continue
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "maintain"
                    and not inner.args
                ):
                    self.fail(
                        "sqlite_db.maintain() is inside a try block in lifespan; "
                        "it must abort the boot"
                    )


class DegradedStateIsDurableTests(unittest.TestCase):
    """Health may not live only in this process's memory.

    A module-level `_degraded = True` would pass every test in the first class
    and lose the finding on the next restart -- which is exactly when an operator
    restarts to "fix" it and gets a green probe over a still-broken store.
    """

    def test_the_failure_is_readable_after_the_lifespan_has_exited(self):
        from app.main import STARTUP_SQLITE_INTEGRITY_JOB, app

        with isolated_ledger() as db:
            with booted(maintain_all={"side_effect": OSError("gone")}):
                pass  # boot, then shut down

            # A fresh client with no lifespan: nothing in memory carries over,
            # so a 503 here can only have come out of the ledger on disk.
            with patch("app.core.scheduler.scheduler", _FakeScheduler(True)), \
                    patch(
                        "app.core.scheduler.scheduler_start_skipped_due_to_lock",
                        return_value=False,
                    ):
                resp = TestClient(app).get("/api/health")

            self.assertEqual(resp.status_code, 503)
            self.assertIn(
                STARTUP_SQLITE_INTEGRITY_JOB, resp.json()["failed_runs"]
            )
            self.assertIn(STARTUP_SQLITE_INTEGRITY_JOB, failed_jobs(db))

    def test_a_healthy_restart_clears_the_degraded_state(self):
        """Recovery has to be reachable. A failed row that nothing ever
        supersedes would pin /api/health at 503 for the life of the install, and
        restoring the store is precisely how the operator fixes it.
        """
        with isolated_ledger() as db:
            with booted(maintain_all={"side_effect": OSError("gone")}) as client:
                self.assertEqual(client.get("/api/health").status_code, 503)

            with booted() as client:  # healthy maintain_all this time
                resp = client.get("/api/health")

            self.assertEqual(resp.status_code, 200)
            self.assertEqual(failed_jobs(db), [])

    def test_retention_does_not_delete_a_startup_failure(self):
        """`delete_terminal_runs_before` exempts each job's newest row. Without
        that, the daily ledger cleanup would erase a startup failure that is
        still the reason health is degraded."""
        from app.main import STARTUP_SQLITE_INTEGRITY_JOB

        with isolated_ledger() as db:
            with booted(maintain_all={"side_effect": OSError("gone")}):
                pass
            self.assertIn(STARTUP_SQLITE_INTEGRITY_JOB, failed_jobs(db))

            # A cutoff far in the future: every terminal row is "old".
            loop_run_store.delete_terminal_runs_before("2999-01-01T00:00:00+00:00")

            self.assertIn(
                STARTUP_SQLITE_INTEGRITY_JOB, failed_jobs(db),
                "retention deleted the row /api/health degrades on",
            )

    def test_a_ledger_write_failure_during_a_core_failure_aborts_the_boot(self):
        """The one case where refusing to start is the only honest option.

        If a core component failed *and* the failure cannot be persisted, there
        is no way left to tell anybody. Coming up would answer 200 over it.
        """
        with isolated_ledger():
            with patch(
                "app.memory.loop_run_store.start_run",
                side_effect=OSError("ledger unwritable"),
            ):
                with self.assertRaises(RuntimeError) as caught:
                    with booted(maintain_all={"side_effect": OSError("gone")}):
                        pass
        self.assertIn("could not be recorded", str(caught.exception))

    def test_an_unwritable_ledger_aborts_a_clean_boot_too(self):
        """A clean boot that cannot write its success row must also refuse.

        The reason is not that the success row itself is precious. The ledger is
        the *only* place a startup or scheduler failure is durably recorded, and
        `/api/health` derives its verdict from it -- so a boot that proves the
        ledger is unwritable has proved the health mechanism is dead. Coming up
        anyway serves 200 forever: every future failure is equally unrecordable,
        and there is no earlier `failed` row to "err toward degraded" onto,
        because writing one is exactly what does not work.
        """
        with isolated_ledger() as db:
            with patch(
                "app.memory.loop_run_store.start_run",
                side_effect=OSError("ledger unwritable"),
            ):
                with self.assertRaises(RuntimeError) as caught:
                    with booted():  # every component healthy
                        pass

            message = str(caught.exception)
            self.assertIn("run ledger", message)
            # The ledger is empty, which is the whole point: a 200 from this boot
            # would have rested on no recorded evidence at all.
            self.assertEqual(status_by_job(db), {})
            # Sanitized like every other startup message: the type, not the text.
            self.assertIn("OSError", message)
            self.assertNotIn("ledger unwritable", message)

    def test_a_half_written_row_aborts_the_boot(self):
        """`start_run` succeeding and `finish_run` failing is the worse shape.

        It leaves a `running` row that nothing will ever finish, and
        `/api/health` degrades only on `status == "failed"` -- so this row is
        indistinguishable from a job still in progress and the probe answers 200.
        The pre-fix code reached this state on a clean boot and logged a warning.
        """
        with isolated_ledger() as db:
            with patch(
                "app.memory.loop_run_store.finish_run",
                side_effect=OSError("ledger unwritable"),
            ):
                with self.assertRaises(RuntimeError):
                    with booted():
                        pass

            # Proves the mechanism the assertion above protects against: the row
            # really is left `running`, and `failed_jobs` -- the exact filter
            # /api/health uses -- cannot see it.
            statuses = status_by_job(db)
            self.assertTrue(statuses, "no row was written at all")
            self.assertNotIn("failed", set(statuses.values()))
            self.assertEqual(failed_jobs(db), [])


class HealthResponseLeaksNothingTests(unittest.TestCase):
    """/api/health must name the component, not the internals.

    It is the one endpoint deliberately reachable without a key (container
    healthchecks and uptime monitors cannot authenticate), so its body is public.
    """

    #: Things an exception string from this startup path would plausibly carry.
    FORBIDDEN = ("SELECT", "PRAGMA", "INSERT", "Traceback", "sqlite3.")

    def _assert_clean(self, text: str, db_path: Path) -> None:
        for needle in self.FORBIDDEN:
            self.assertNotIn(needle, text, f"{needle!r} leaked into /api/health")
        self.assertNotIn("secret", text, "the write key leaked into /api/health")
        # Absolute paths: the ledger and the store directory both sit under a
        # temp root here, and in production under /app/data.
        self.assertNotIn(str(db_path), text)
        self.assertNotIn(str(db_path.parent), text)

    def test_an_unauthenticated_degraded_response_carries_no_internals(self):
        from app.main import STARTUP_SQLITE_INTEGRITY_JOB

        with isolated_ledger() as db:
            with booted(maintain_all={
                "side_effect": OSError(f"unable to open {db}: SELECT failed")
            }) as client:
                resp = client.get("/api/health")

            self.assertEqual(resp.status_code, 503)
            self.assertIn(
                STARTUP_SQLITE_INTEGRITY_JOB, resp.json()["failed_runs"],
                "the response must still say which component failed",
            )
            self._assert_clean(resp.text, db)

    def test_an_authenticated_degraded_response_also_carries_no_internals(self):
        """With a valid key `/api/health` returns each run's `error` column
        verbatim, so the sanitising has to happen where the row is *written*.
        Patching the reader would leave the raw string in storage for the next
        consumer."""
        with isolated_ledger() as db:
            with booted(maintain_all={
                "side_effect": OSError(f"unable to open {db}: SELECT failed")
            }) as client:
                resp = client.get("/api/health", headers={"X-API-Key": "secret"})

            self.assertEqual(resp.status_code, 503)
            self._assert_clean(resp.text, db)

    def test_the_recorded_error_names_the_exception_type_but_not_its_message(self):
        """Enough to triage from, nothing that quotes the upstream string."""
        from app.main import STARTUP_SQLITE_INTEGRITY_JOB

        with isolated_ledger():
            with booted(maintain_all={
                "side_effect": OSError("https://internal.example/db?token=abc")
            }):
                pass
            rows = [
                run for run in loop_run_store.latest_run_per_job()
                if run["job_name"] == STARTUP_SQLITE_INTEGRITY_JOB
            ]
        self.assertEqual(len(rows), 1, rows)
        error = str(rows[0].get("error") or "")
        self.assertIn("OSError", error)
        self.assertNotIn("internal.example", error)
        self.assertNotIn("token=abc", error)


class SchedulerSignalDoesNotRegressTests(unittest.TestCase):
    """The pre-existing degraded conditions must keep working alongside the new
    ones -- three independent signals, one probe."""

    def test_a_stopped_scheduler_still_degrades(self):
        with isolated_ledger():
            with booted(scheduler_running=False) as client:
                resp = client.get("/api/health")
            self.assertEqual(resp.status_code, 503)
            self.assertFalse(resp.json()["scheduler_running"])

    def test_a_stopped_scheduler_is_ok_when_explicitly_disabled(self):
        with isolated_ledger():
            with booted(
                scheduler_running=False, SCHEDULER_ENABLED=False
            ) as client:
                resp = client.get("/api/health")
            self.assertEqual(resp.status_code, 200)

    def test_a_failed_scheduler_job_row_still_degrades(self):
        """A core startup failure and a failed scheduled job are different facts;
        both must surface, and neither may mask the other."""
        with isolated_ledger():
            with booted() as client:
                self.assertEqual(client.get("/api/health").status_code, 200)
                run_id = loop_run_store.start_run("event_discover")
                loop_run_store.finish_run(run_id, "failed", error="boom")
                resp = client.get("/api/health")
            self.assertEqual(resp.status_code, 503)
            self.assertIn("event_discover", resp.json()["failed_runs"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
