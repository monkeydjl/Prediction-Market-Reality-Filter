import fnmatch
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app.core import runtime_stores
from app.core.config import settings
from scripts import backup_stores


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = Path(__file__).resolve().parents[1]
DEPLOY_DIR = REPO_ROOT / "deploy"
COMPOSE_FILE = DEPLOY_DIR / "docker-compose.yml"
DOCKERFILE = DEPLOY_DIR / "Dockerfile"
RUNBOOK = REPO_ROOT / "docs" / "ops" / "RUNBOOK.md"


def _nested_block_items(text: str, key: str) -> list[str]:
    """List items under the first *indented* `key:` mapping in a compose file.

    A line scan, not a YAML parse: PyYAML is not declared in either
    requirements file (locally it exists only as an optuna transitive), so a
    test that imported it would pass here and fail in CI.

    "Indented" is what distinguishes the service's own `volumes:` mount list
    from the top-level `volumes:` declaration at column 0, which shares the
    name and holds volume names rather than mounts.
    """
    lines = text.splitlines()
    items: list[str] = []
    block_indent: int | None = None
    for line in lines:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if block_indent is None:
            if stripped == f"{key}:" and indent > 0:
                block_indent = indent
            continue
        if not stripped:
            continue
        if indent <= block_indent:
            break
        if stripped.startswith("#"):
            continue
        if stripped.startswith("- "):
            items.append(stripped[2:].strip().strip('"'))
    return items


def _compose_environment() -> dict[str, str]:
    """`KEY: value` for every assignment in the service's `environment:` list."""
    out: dict[str, str] = {}
    for item in _nested_block_items(COMPOSE_FILE.read_text(encoding="utf-8"), "environment"):
        name, _, value = item.partition("=")
        out[name.strip()] = value.strip()
    return out


def _compose_mount_targets() -> list[str]:
    """Container-side path of every volume mounted by the service."""
    targets = []
    for item in _nested_block_items(COMPOSE_FILE.read_text(encoding="utf-8"), "volumes"):
        _, _, target = item.partition(":")
        if target:
            targets.append(target.strip())
    return targets


def _unit_exec_start_scripts() -> dict[str, str]:
    """unit filename -> the `scripts/<name>.py` its ExecStart runs.

    Scanned from `deploy/*.service` rather than listed, so a unit added later is
    covered the day it lands. Units that exec something else (the API unit runs
    `uvicorn app.main:app`) contribute nothing.
    """
    out: dict[str, str] = {}
    for unit in sorted(DEPLOY_DIR.glob("*.service")):
        for line in unit.read_text(encoding="utf-8").splitlines():
            if not line.startswith("ExecStart="):
                continue
            match = re.search(r"(scripts/[A-Za-z0-9_]+\.py)", line)
            if match:
                out[unit.name] = match.group(1)
    return out


# Extra argv, env and the marker each script prints once its own `main` is
# reached. `{tmp}` is substituted with a temp directory. The marker is the
# assertion: an exit code cannot express "started", because `healthcheck.py`
# exits 1 for the condition it exists to report. Asserted to cover the unit scan
# exactly, so this table cannot fall behind `deploy/`.
_PROBE_INVOCATION: dict[str, tuple[list[str], dict[str, str], str]] = {
    "scripts/run_scheduler.py": (
        [],
        {"SCHEDULER_ENABLED": "false"},
        "PMRF scheduler worker starting",
    ),
    "scripts/backup_stores.py": (
        ["--output-dir", "{tmp}", "--keep", "1"],
        {},
        "pmrf-backup-",
    ),
    "scripts/healthcheck.py": (
        [],
        {"PMRF_HEALTHCHECK_URL": "http://127.0.0.1:1/api/health"},
        "PMRF healthcheck",
    ),
}


def _redirected_store_env(tmp: str) -> dict[str, str]:
    """Point every path setting inside `tmp`, keeping each one's suffix.

    The suffix matters: `sqlite_state_settings()` selects on `.db`, so renaming
    one would change what the probed script does rather than only where it
    writes.
    """
    env: dict[str, str] = {}
    for name in runtime_stores.path_setting_names():
        suffix = Path(getattr(settings, name, "") or name).suffix
        env[name] = os.path.join(tmp, f"{name.lower()}{suffix}")
    return env


def _dockerfile_created_dirs() -> list[str]:
    """Directories the Dockerfile's `mkdir -p` calls create in the image.

    A `RUN` is one shell line chained with `&&` and wrapped with `\\`, so the
    argument list ends at whichever comes first.
    """
    dirs: list[str] = []
    for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        match = re.search(r"mkdir\s+-p\s+(.*)", line.strip())
        if not match:
            continue
        for token in match.group(1).split():
            if token in ("\\", "&&", "|"):
                break
            dirs.append(token)
    return dirs


def _dockerignore_excludes(rel_path: str) -> bool:
    """Whether `.dockerignore` keeps `rel_path` out of the build context.

    Docker matches each pattern with Go's `filepath.Match` **per path segment**,
    so a single `*` never crosses a `/`. That is the whole point here rather than
    a detail: `backend/*.json` is already in the file and looks like it covers
    `backend/backups/<dir>/event_store.json`. It does not, and a substring check
    would have concluded that it did.

    `**` is not modelled. That can only make this under-report an exclusion, and
    every assertion below asserts that something *is* excluded, so the error
    direction is a loud failure rather than a quiet pass.

    `!` negation *is* modelled, and has to be: Docker resolves a path by the
    **last** matching pattern, so a re-include is invisible to a first-match
    scan. Without this, a `!backend/foo.json` line would leave this helper still
    reporting the file as excluded -- the instrument would contradict the image.
    """
    patterns = [
        line.strip()
        for line in (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    segments = rel_path.split("/")
    prefixes = ["/".join(segments[:i]) for i in range(1, len(segments) + 1)]
    excluded = False
    for pattern in patterns:
        negated = pattern.startswith("!")
        parts = pattern.lstrip("!").rstrip("/").split("/")
        for prefix in prefixes:
            candidate = prefix.split("/")
            if len(candidate) == len(parts) and all(
                fnmatch.fnmatchcase(c, p) for c, p in zip(candidate, parts)
            ):
                excluded = not negated
                break
    return excluded


def _runbook_section(title: str) -> str:
    """One `## ` section of the RUNBOOK, so an assertion cannot match elsewhere.

    Raises rather than falling back to the whole document: a silent fallback
    would weaken every assertion below without saying so.
    """
    text = RUNBOOK.read_text(encoding="utf-8")
    start = text.find(f"\n## {title}\n")
    if start < 0:
        raise AssertionError(f"the '{title}' section is gone from {RUNBOOK.name}")
    nxt = text.find("\n## ", start + 1)
    return text[start:nxt] if nxt > 0 else text[start:]


class DeployUnitTests(unittest.TestCase):
    def test_api_systemd_unit_delegates_scheduler_to_worker(self):
        unit = (DEPLOY_DIR / "prediction-market-reality-filter.service").read_text(
            encoding="utf-8"
        )

        self.assertIn("Environment=SCHEDULER_ENABLED=false", unit)
        self.assertIn("ExecStart=/opt/prediction-market-reality-filter/.venv/bin/uvicorn", unit)

    def test_scheduler_systemd_unit_runs_standalone_worker(self):
        unit = (
            DEPLOY_DIR / "prediction-market-reality-filter-scheduler.service"
        ).read_text(encoding="utf-8")

        self.assertIn("Environment=SCHEDULER_ENABLED=true", unit)
        self.assertIn(
            "ExecStart=/opt/prediction-market-reality-filter/.venv/bin/python "
            "scripts/run_scheduler.py",
            unit,
        )
        self.assertIn("Restart=on-failure", unit)


class ComposeStorePersistenceTests(unittest.TestCase):
    """Every state store must be redirected onto a mounted volume.

    `docker-compose.yml` redirected six of the eight `STATE_STORES` rows into
    `/app/data`. The two it missed — `KERNEL_DB_FILE` (33,882 prediction rows on
    the install where this was measured) and `DOMAIN_RELIABILITY_DB_PATH` — kept
    their in-image defaults, so they landed on the container's writable layer and
    `docker compose down` discarded them. Nothing read this file: `deploy/` had
    no test but the two systemd units, which is why the omission survived.

    Same shape as `test_runtime_stores.py`: the membership decision stays in
    `app.core.runtime_stores`, and this compares the deploy surface against it
    rather than repeating the list.
    """

    def test_the_scan_finds_the_environment_block(self):
        """Guard the denominator: an empty parse makes every assertion vacuous."""
        env = _compose_environment()
        self.assertGreaterEqual(
            len(env), 6,
            f"parsed only {len(env)} environment assignments out of "
            f"{COMPOSE_FILE.name}; the parity assertions below would pass "
            f"against an empty dict",
        )
        self.assertIn("LOOP_DB_FILE", env)

    def test_the_scan_finds_the_mount_list(self):
        targets = _compose_mount_targets()
        self.assertIn(
            "/app/data", targets,
            f"parsed mount targets {targets} out of {COMPOSE_FILE.name}; the "
            f"store paths are checked against this list",
        )

    def test_every_state_store_is_redirected_onto_a_volume(self):
        """The assertion the original defect needed, in both halves.

        A store missing from `environment:` keeps its in-image default; a store
        present but pointed outside every mount is worse, because it looks
        handled.
        """
        env = _compose_environment()
        targets = _compose_mount_targets()
        unpersisted = {}
        for name in runtime_stores.state_setting_names():
            value = env.get(name)
            if value is None:
                unpersisted[name] = "absent from environment:"
            elif not any(value.startswith(f"{target}/") for target in targets):
                unpersisted[name] = f"{value} is not under any mounted volume"
        self.assertEqual(
            unpersisted, {},
            f"these STATE_STORES rows would not survive a container replacement: "
            f"{unpersisted}. Add a `- NAME=/app/data/<file>` line to the "
            f"environment: block in deploy/{COMPOSE_FILE.name}.",
        )

    def test_the_two_stores_the_compose_file_used_to_miss(self):
        """Pin the specific regression by name and by target.

        The parity test above stays green if someone reclassifies these out of
        `STATE_STORES` — a legal partition and a silent return of the defect.
        """
        env = _compose_environment()
        self.assertEqual(env.get("KERNEL_DB_FILE"), "/app/data/kernel_predictions.db")
        self.assertEqual(
            env.get("DOMAIN_RELIABILITY_DB_PATH"), "/app/data/domain_reliability.db"
        )


class UnitExecStartRunsTests(unittest.TestCase):
    """Every `ExecStart=` a unit ships must actually start.

    These two tests used to assert the `ExecStart=` *string*, which is what let
    the real defect through: `python scripts/run_scheduler.py` puts
    `backend/scripts` on `sys.path[0]`, not `backend`, so `from app.core.config
    import settings` raises `ModuleNotFoundError: No module named 'app'` before
    any of the work starts. Measured on the scheduler and backup units: exit 1,
    nothing else in the log. Ten of the app-importing scripts lack the
    `sys.path.insert` guard the other twenty-nine carry, so string equality
    proves the unit says what we typed and nothing about whether it runs.

    Executed as a subprocess with every store redirected into a temp directory
    and `PYTHONPATH` cleared, because a `PYTHONPATH` inherited from the test
    runner would supply the path the unit is missing and the test would pass
    against the defect.
    """

    def _run_unit_script(self, script: str) -> tuple[int, str]:
        argv, extra_env, _marker = _PROBE_INVOCATION[script]
        with tempfile.TemporaryDirectory() as tmp:
            env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
            env.update(_redirected_store_env(tmp))
            env["BACKUP_ENCRYPTION_KEY"] = ""
            env["PYTHONIOENCODING"] = "utf-8"
            env.setdefault("PYTHONUTF8", "1")
            env.update(extra_env)
            result = subprocess.run(
                [sys.executable, script, *[a.format(tmp=tmp) for a in argv]],
                cwd=BACKEND_DIR,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
            )
        return result.returncode, ((result.stdout or "") + (result.stderr or ""))

    def test_every_exec_start_script_is_probed(self):
        """Guard the list: an unprobed unit is an unprobed ExecStart.

        `_PROBE_INVOCATION` is hand-written (each script needs its own harmless
        argv), so it is asserted to be an exact partition of the units' scan.
        """
        scanned = set(_unit_exec_start_scripts().values())
        self.assertEqual(
            scanned, set(_PROBE_INVOCATION),
            f"deploy/*.service ExecStart scripts are {sorted(scanned)} but the "
            f"probe table covers {sorted(_PROBE_INVOCATION)}; add the missing "
            f"script with argv/env that make one run harmless",
        )
        self.assertGreaterEqual(len(scanned), 3)

    def test_each_unit_script_reaches_its_own_main(self):
        """The marker, not the exit code.

        `healthcheck.py` exits 1 when the endpoint is unreachable — which it is
        here, on purpose — so "started" has to be observed from its own output.
        """
        for unit, script in sorted(_unit_exec_start_scripts().items()):
            with self.subTest(unit=unit, script=script):
                _code, output = self._run_unit_script(script)
                self.assertNotIn(
                    "ModuleNotFoundError", output,
                    f"{unit} runs `{script}` and it cannot import its own "
                    f"package:\n{output}",
                )
                self.assertIn(
                    _PROBE_INVOCATION[script][2], output,
                    f"{unit} runs `{script}` and it never reached its own main:"
                    f"\n{output}",
                )


class DockerBackupPathTests(unittest.TestCase):
    """A Docker deployment must actually produce backups.

    Measured before this class existed: `deploy/docker-compose.yml` mounts
    `pmrf_data:/app/backups`, and that was the whole of it. `deploy/Dockerfile`
    created only `/app/logs /app/data /frontend/out`, so the fourth mount point
    was left to Docker, which creates a missing mount target **root-owned** while
    the image drops to `USER app` — the one directory the backup writes into is
    the one directory the runtime user does not own. And nothing invoked the
    script from the compose path at all: `grep backup app/core/scheduler.py` was
    empty, the backup unit and timer are systemd-only, and the RUNBOOK's Docker
    section documented no `docker exec` variant. A Docker install had no backups
    and no way to notice.

    The systemd side is not the same gap and is deliberately left as it was: it
    has a `.timer`, and doubling it up with an in-process job would halve the
    effective retention at `--keep 30`. Hence the enable setting, off by default,
    turned on in the compose file — which is what test 4 pins, because a
    capability nothing switches on is the defect this repo keeps finding.
    """

    def test_the_dockerfile_creates_every_directory_the_compose_file_mounts(self):
        """The `/app/backups` half of the defect.

        Docker will create a missing target, so this is not "the mount fails" —
        it is created root:root 0755 while the process runs as `app`. `/app/logs`
        and `/app/data` avoid that only because the `mkdir -p` precedes the
        `chown -R app:app /app`.
        """
        targets = [t for t in _compose_mount_targets() if t.startswith("/app/")]
        created = _dockerfile_created_dirs()
        self.assertGreaterEqual(
            len(targets), 3,
            f"parsed only {targets} as /app mounts out of {COMPOSE_FILE.name}; "
            f"the assertion below would be vacuous",
        )
        self.assertIn(
            "/app/data", created,
            f"parsed {created} out of {DOCKERFILE.name}; the mkdir scan is wrong",
        )
        missing = [t for t in targets if t not in created]
        self.assertEqual(
            missing, [],
            f"{COMPOSE_FILE.name} mounts {missing} but {DOCKERFILE.name} never "
            f"creates them, so Docker creates them root-owned and the non-root "
            f"`app` user cannot write there. Add them to the `mkdir -p` line "
            f"before the `chown -R app:app`.",
        )

    def test_the_backup_volume_is_mounted_where_the_script_writes(self):
        """Pin the coupling, which no code expresses.

        `create_backup(output_dir=None)` resolves its default from the script's
        own location — `<backend>/backups` — and `backend/` is copied to `/app`.
        Nothing states that the compose mount has to agree, so a rename on either
        side would leave a mounted volume that nothing writes into and archives
        on the container's writable layer, which is the failure the mount exists
        to prevent.
        """
        default_dir = Path(backup_stores.__file__).resolve().parents[1] / "backups"
        in_image = "/app/" + default_dir.relative_to(BACKEND_DIR).as_posix()
        self.assertIn(
            in_image, _compose_mount_targets(),
            f"the backup script writes to {in_image} in the image, but "
            f"{COMPOSE_FILE.name} mounts {_compose_mount_targets()}",
        )

    def test_old_archives_are_not_baked_into_the_image(self):
        """`backend/*.json` and `backend/*.db` do not reach `backend/backups/`.

        Docker matches `.dockerignore` per path segment, so a single `*` never
        crosses a `/`. Measured on this install: 30.69 MB in 11 files entered the
        build context from `backend/backups/`, including two 11.92 MB `v2_loop.db`
        copies and three `event_store.json` snapshots from the June and July
        purges. Those are old production event records, and `COPY backend/ /app/`
        bakes them into an image layer where the operator has no reason to look
        for them.
        """
        self.assertFalse(
            _dockerignore_excludes("backend/app/core/scheduler.py"),
            "the .dockerignore parse excludes application source; the matcher is "
            "wrong and every assertion here is vacuous",
        )
        self.assertTrue(
            _dockerignore_excludes("backend/backups/anything-20260709/v2_loop.db"),
            "`backend/backups/` is not excluded from the build context, so old "
            "store snapshots are copied into the image by `COPY backend/ /app/`. "
            "Add `backend/backups/` to .dockerignore.",
        )


class RuntimeDataFilesReachTheImageTests(unittest.TestCase):
    """A tracked file the app reads at runtime must survive `.dockerignore`.

    `backend/*.json` is there to keep store snapshots out of the image, and it
    also caught `world_cup_team_ids.json` -- which is tracked, shipped with 35
    entries, and read on every `resolve_team_id` call.

    Why the loss is not free: `resolve_team_id` tries the cache, then
    **API-Football**, and only then the 55-entry curated map. So the fallback
    does not prevent the spend, it happens after it. With the file absent, every
    team the curated map could have answered for free costs one live lookup
    first, on every container start.
    """

    RUNTIME_READS = ("backend/world_cup_team_ids.json",)

    def test_the_matcher_still_reports_a_real_exclusion(self):
        """Guard the instrument: negation must not make everything included."""
        self.assertTrue(
            _dockerignore_excludes("backend/event_store.json"),
            "`backend/*.json` no longer excludes store snapshots; the negation "
            "support in _dockerignore_excludes has made this scan vacuous",
        )

    def test_every_runtime_data_file_reaches_the_build_context(self):
        for rel in self.RUNTIME_READS:
            with self.subTest(path=rel):
                self.assertTrue(
                    (REPO_ROOT / rel).exists(),
                    f"{rel} is gone; drop it from RUNTIME_READS or restore it",
                )
                self.assertFalse(
                    _dockerignore_excludes(rel),
                    f"{rel} is read at runtime but excluded from the build "
                    f"context, so the container starts with a cold cache and "
                    f"re-spends API-Football quota. Re-include it with a "
                    f"`!{rel}` line after the `backend/*.json` rule.",
                )

    def test_the_compose_deployment_schedules_a_backup(self):
        """The switch, not just the capability.

        `BACKUP_SCHEDULE_ENABLED` is off by default so a systemd install keeps
        its timer as the only writer. That makes the compose file the only thing
        that turns the job on, so this is where the Docker backup path is either
        reachable or not.
        """
        self.assertEqual(
            _compose_environment().get("BACKUP_SCHEDULE_ENABLED"), "true",
            f"nothing in the compose path runs a backup: the in-process job is "
            f"off by default and deploy/{COMPOSE_FILE.name} does not enable it, "
            f"and the systemd timer does not apply to a container",
        )

    def test_the_runbook_documents_the_docker_backup_path(self):
        """An operator reading only the Docker section must find the backups.

        The `## Backups` section documents the systemd/cron invocation against a
        `/opt/...` venv path that does not exist in a container.

        Matched on the tokens of a command line rather than a `docker compose
        exec` substring: the compose file lives in `deploy/`, so every real
        invocation carries `-f deploy/docker-compose.yml` between the two words.
        The first version of this assertion failed against correct documentation
        for exactly that reason.
        """
        section = _runbook_section("Docker Deployment")
        self.assertIn(
            "BACKUP_SCHEDULE_ENABLED", section,
            "the Docker section does not say how backups happen under Docker",
        )
        commands = [
            " ".join(line.split())
            for block in re.findall(r"```bash\n(.*?)```", section, re.DOTALL)
            for line in block.replace("\\\n", " ").splitlines()
        ]
        self.assertTrue(
            any("exec" in cmd and "scripts/backup_stores.py" in cmd for cmd in commands),
            f"no on-demand backup command is documented for a container; the "
            f"`## Backups` section's `/opt/...` venv path does not exist there. "
            f"Commands found: {commands}",
        )


if __name__ == "__main__":
    unittest.main()
