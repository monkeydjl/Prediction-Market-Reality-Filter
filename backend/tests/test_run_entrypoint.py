"""`backend/run.py` must not publish keyless write endpoints to the network.

Two things are asserted here, and neither can be reached by importing `run.py`:
the bind address and the guard both live under `if __name__ == "__main__"`. So
these tests execute the file the way `start.bat` does, with a stub `uvicorn`
placed first on `PYTHONPATH` that records its keyword arguments instead of
binding a socket. That keeps the real settings load, the real guard, and the real
call site in the measurement, and leaves the port unbound.

The regression: `run.py` hardcoded `host="0.0.0.0"`. `ALLOW_OPEN_WRITES=true` is
documented as local-dev-only and `app/main.py` accepts it as an alternative to
`API_WRITE_KEY`, but nothing enforced the "local" half — so the documented dev
configuration served every mutating endpoint, including the LLM-spending ones, on
every network interface.
"""
import json
import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Written by the stub below; read back as JSON to see what run.py asked for.
_STUB = """\
import json, os, sys

def run(app, **kwargs):
    with open(os.environ["UVICORN_CALL_LOG"], "w", encoding="utf-8") as fh:
        json.dump({"app": app, "kwargs": kwargs}, fh)
    sys.exit(0)
"""


class _RunEntrypoint(unittest.TestCase):
    """Runs `python run.py` with uvicorn stubbed out."""

    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        (root / "uvicorn.py").write_text(textwrap.dedent(_STUB), encoding="utf-8")
        self._stub_dir = root
        self._call_log = root / "call.json"

    def _run(self, **env_overrides: str) -> tuple[int, str, dict | None]:
        """Execute run.py; return (exit code, stdout+stderr, recorded call).

        Every setting the guard reads is passed explicitly, empty string
        included: `_load_env_files` calls `load_dotenv()` without `override`, so
        a name already present in the environment wins over the developer's
        `backend/.env`. Without that the result would depend on an untracked file.
        """
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(
            [str(self._stub_dir), str(BACKEND_DIR), env.get("PYTHONPATH", "")]
        )
        env["UVICORN_CALL_LOG"] = str(self._call_log)
        env["PYTHONIOENCODING"] = "utf-8"
        env.setdefault("PYTHONUTF8", "1")
        env["API_WRITE_KEY"] = ""
        env["ALLOW_OPEN_WRITES"] = "false"
        env["SERVER_RELOAD"] = "false"
        env.pop("SERVER_HOST", None)  # so "unset" means unset, whatever the shell had
        env.update(env_overrides)

        result = subprocess.run(
            [sys.executable, "run.py"],
            cwd=BACKEND_DIR,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        call = None
        if self._call_log.exists():
            call = json.loads(self._call_log.read_text(encoding="utf-8"))
        return result.returncode, (result.stdout or "") + (result.stderr or ""), call


class BindAddressTests(_RunEntrypoint):
    def test_the_stub_is_the_one_that_answers(self):
        """Guard the instrument: if the real uvicorn ran, nothing below is a test.

        A stub that never gets imported would leave `call` at None, and every
        assertion about `kwargs` would read as "no bad value was passed".
        """
        code, output, call = self._run(SERVER_HOST="127.0.0.1")
        self.assertEqual(code, 0, output)
        self.assertIsNotNone(call, f"uvicorn stub was not imported; output:\n{output}")
        assert call is not None
        self.assertEqual(call["app"], "app.main:app")

    def test_the_bind_address_comes_from_the_setting(self):
        """The value must travel from the environment to the uvicorn call.

        A loopback address other than the default is deliberate: 127.0.0.1 would
        also be produced by the shipped default, so it cannot distinguish
        "plumbed through" from "hardcoded to something harmless".
        """
        code, output, call = self._run(SERVER_HOST="127.0.0.9")
        self.assertEqual(code, 0, output)
        assert call is not None
        self.assertEqual(call["kwargs"]["host"], "127.0.0.9")

    @unittest.skipIf(
        "SERVER_HOST" in (BACKEND_DIR / ".env").read_text(encoding="utf-8", errors="replace")
        if (BACKEND_DIR / ".env").exists()
        else False,
        "backend/.env assigns SERVER_HOST, so the shipped default is not observable here",
    )
    def test_the_default_is_loopback(self):
        """With SERVER_HOST unset anywhere, run.py must not reach past this machine."""
        code, output, call = self._run()
        self.assertEqual(code, 0, output)
        assert call is not None
        self.assertEqual(
            call["kwargs"]["host"],
            "127.0.0.1",
            "run.py used to hardcode 0.0.0.0; the default must stay local",
        )

    def test_the_port_and_reload_flag_still_travel(self):
        """Everything else about the call is unchanged by the guard."""
        code, output, call = self._run(SERVER_HOST="127.0.0.1", SERVER_RELOAD="true")
        self.assertEqual(code, 0, output)
        assert call is not None
        self.assertEqual(call["kwargs"]["port"], 8000)
        self.assertIs(call["kwargs"]["reload"], True)


class OpenWriteBindGuardTests(_RunEntrypoint):
    def test_keyless_writes_on_all_interfaces_are_refused(self):
        code, output, call = self._run(SERVER_HOST="0.0.0.0", ALLOW_OPEN_WRITES="true")
        self.assertNotEqual(code, 0, f"run.py started anyway:\n{output}")
        self.assertIsNone(call, "uvicorn was called despite the refusal")
        self.assertIn("SERVER_HOST", output)
        self.assertIn("ALLOW_OPEN_WRITES", output)
        # Both escape routes have to be named, or the operator's only move is to
        # delete the check.
        self.assertIn("127.0.0.1", output)
        self.assertIn("API_WRITE_KEY", output)

    def test_a_routable_address_is_refused_too(self):
        """The defect is reachability, not the literal string 0.0.0.0."""
        code, output, call = self._run(SERVER_HOST="192.168.1.50", ALLOW_OPEN_WRITES="true")
        self.assertNotEqual(code, 0, output)
        self.assertIsNone(call)

    def test_an_unparseable_host_is_refused(self):
        """An unrecognised value is not assumed to be local."""
        code, output, call = self._run(SERVER_HOST="my-desktop.lan", ALLOW_OPEN_WRITES="true")
        self.assertNotEqual(code, 0, output)
        self.assertIsNone(call)

    def test_loopback_with_open_writes_is_allowed(self):
        """This is the documented dev configuration; it must keep working."""
        code, output, call = self._run(SERVER_HOST="127.0.0.1", ALLOW_OPEN_WRITES="true")
        self.assertEqual(code, 0, output)
        assert call is not None
        self.assertEqual(call["kwargs"]["host"], "127.0.0.1")

    def test_a_write_key_permits_any_bind_address(self):
        """With authentication in front of writes, exposure is a choice, not a trap."""
        code, output, call = self._run(
            SERVER_HOST="0.0.0.0",
            ALLOW_OPEN_WRITES="true",
            API_WRITE_KEY="test-only-not-a-real-key",
        )
        self.assertEqual(code, 0, output)
        assert call is not None
        self.assertEqual(call["kwargs"]["host"], "0.0.0.0")

    def test_startup_never_echoes_the_write_key(self):
        """Narrow on purpose: the refusal branch cannot reach a key by construction.

        The guard only fires when `API_WRITE_KEY` is empty, so no refusal message
        can contain one. What this pins is the launcher's own output — a later
        diagnostic line like "starting with key=..." would turn every terminal
        scrollback and CI log into a place the key lives.
        """
        secret = "sentinel-write-key-8fbe12"
        code, output, _ = self._run(
            SERVER_HOST="0.0.0.0", ALLOW_OPEN_WRITES="true", API_WRITE_KEY=secret
        )
        self.assertEqual(code, 0, output)
        self.assertNotIn(secret, output)


class IsLoopbackTests(unittest.TestCase):
    """`_is_loopback` is importable; the guard that calls it is not."""

    @classmethod
    def setUpClass(cls) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "pmrf_run_entrypoint", BACKEND_DIR / "run.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # __name__ != "__main__": nothing starts
        cls.module = module

    def test_local_addresses(self):
        for host in ("127.0.0.1", "127.0.0.9", "localhost", "::1", " 127.0.0.1 "):
            with self.subTest(host=host):
                self.assertTrue(self.module._is_loopback(host))

    def test_reachable_addresses(self):
        for host in ("0.0.0.0", "::", "*", "", "192.168.1.50", "10.0.0.4", "my-host.lan"):
            with self.subTest(host=host):
                self.assertFalse(self.module._is_loopback(host))


if __name__ == "__main__":
    unittest.main()
