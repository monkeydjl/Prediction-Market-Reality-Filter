"""No shipped deploy path may publish the API on a public interface.

Every other entrypoint in this repo binds loopback and says why:

* `app/core/config.py` -- `SERVER_HOST` defaults to `127.0.0.1`, and
  `test_run_entrypoint.py` exists because `run.py` once hardcoded `0.0.0.0`
  ("the documented dev configuration served every mutating endpoint, including
  the LLM-spending ones, on every network interface").
* `deploy/docker-compose.yml` publishes `127.0.0.1:8000:8000`, with a comment
  saying the container must not be exposed directly because it terminates no TLS.
* `deploy/nginx.conf.example` proxies to `http://127.0.0.1:8000`, and
  `deploy/Caddyfile.example` to `127.0.0.1:8000` under a comment stating "the
  FastAPI backend listens on 127.0.0.1:8000".

`deploy/prediction-market-reality-filter.service` passed `--host 0.0.0.0`. A
systemd install has no network namespace, so unlike the container that is every
interface on the host: plain HTTP with no TLS, `/metrics` and `/api/health`
unauthenticated, and every write endpoint one missing `API_WRITE_KEY` away. The
two proxies shipped next to it already assume loopback, so the exposure bought
nothing the documented deploy uses.

The test reads `--host` out of the unit rather than asserting the whole
`ExecStart=` string: `DeployUnitTests` asserts a prefix that stops before the
flags, which is how the bind address stayed unread.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = REPO_ROOT / "deploy"
RUNBOOK = REPO_ROOT / "docs" / "ops" / "RUNBOOK.md"

# Addresses that mean "every interface" on the host.
_PUBLIC_BINDS = ("0.0.0.0", "::", "*")


def _host_flags(text: str) -> list[str]:
    """Every `--host <value>` in a unit file, in order."""
    return re.findall(r"--host[ =]([^\s]+)", text)


class SystemdBindTests(unittest.TestCase):
    def test_no_unit_binds_a_public_interface(self):
        units = sorted(DEPLOY_DIR.glob("*.service"))
        self.assertGreaterEqual(len(units), 4, [u.name for u in units])
        for unit in units:
            with self.subTest(unit=unit.name):
                for host in _host_flags(unit.read_text(encoding="utf-8")):
                    self.assertNotIn(
                        host,
                        _PUBLIC_BINDS,
                        f"{unit.name} binds {host}; a systemd install has no "
                        "network namespace, so that is every interface on the "
                        "host with no TLS in front. The nginx and Caddy examples "
                        "in deploy/ both proxy to 127.0.0.1:8000.",
                    )

    def test_the_api_unit_still_binds_something(self):
        """Guard the scan: a unit with no --host at all would pass vacuously."""
        unit = (DEPLOY_DIR / "prediction-market-reality-filter.service").read_text(
            encoding="utf-8"
        )
        hosts = _host_flags(unit)
        self.assertEqual(hosts, ["127.0.0.1"], f"expected one loopback bind, got {hosts}")

    def test_the_scan_would_notice_a_public_bind(self):
        """Guard the instrument against a regex that matches nothing."""
        self.assertEqual(
            _host_flags("ExecStart=/x/uvicorn app.main:app --host 0.0.0.0 --port 8000"),
            ["0.0.0.0"],
        )
        self.assertEqual(_host_flags("ExecStart=/x/uvicorn --host=:: --port 8000"), ["::"])


class ProxyTargetAgreementTests(unittest.TestCase):
    """The unit's bind and the proxies' upstream must be the same address.

    Two published claims about one thing: the proxy examples say where the
    backend listens, the unit decides where it listens. They disagreed.
    """

    def test_every_proxy_example_targets_the_address_the_unit_binds(self):
        unit = (DEPLOY_DIR / "prediction-market-reality-filter.service").read_text(
            encoding="utf-8"
        )
        bound = _host_flags(unit)[0]
        for name in ("nginx.conf.example", "Caddyfile.example"):
            with self.subTest(proxy=name):
                text = (DEPLOY_DIR / name).read_text(encoding="utf-8")
                targets = set(re.findall(r"(?:proxy_pass\s+http://|reverse_proxy\s+)"
                                        r"([0-9.]+):8000", text))
                self.assertTrue(targets, f"{name} names no upstream on port 8000")
                self.assertEqual(
                    targets,
                    {bound},
                    f"{name} proxies to {sorted(targets)} but the systemd unit "
                    f"binds {bound}",
                )


class RunbookExposureTests(unittest.TestCase):
    """The RUNBOOK must say what to do when the proxy is on another host."""

    def test_the_runbook_states_the_bind_and_the_override(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        for needle in (
            # The bind the unit actually uses, so the doc and the unit agree.
            "--host 127.0.0.1",
            # A runnable rule, not the word "firewall" -- that word already
            # appears in this file's prose, so asserting it proves nothing.
            "ufw allow from",
            "ufw deny 8000/tcp",
            # How to confirm what is listening after a change.
            "ss -ltnp",
        ):
            with self.subTest(needle=needle):
                self.assertIn(
                    needle,
                    text,
                    f"docs/ops/RUNBOOK.md never mentions {needle!r}; an operator "
                    "who moves the proxy to another host has no guidance on what "
                    "widening the bind costs",
                )


if __name__ == "__main__":
    unittest.main()
