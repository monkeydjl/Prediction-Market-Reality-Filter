"""The two proxy examples must not disagree silently about rate limiting.

`nginx.conf.example` ships a live limit: `limit_req_zone ... rate=10r/s` at the
http level plus `limit_req zone=pmrf_limit burst=20 nodelay` inside `location
/api/`. `Caddyfile.example` shipped its `rate_limit` block commented out, so an
operator who picked Caddy got proxy-layer rate limiting only if they noticed the
comment and acted on it. That asymmetry was not stated anywhere.

Leaving the block commented is defensible -- `rate_limit` is a third-party module
and an uncommented directive would stop Caddy from starting. What is not
defensible is the guidance that was there, which was wrong in three ways at once:

  1. "Caddy 2.7+ has built-in rate limiting" -- it does not, at any version.
     `rate_limit` comes from github.com/mholt/caddy-ratelimit and needs
     `xcaddy build --with github.com/mholt/caddy-ratelimit`. An operator who
     believed the comment and uncommented the block got
     "unrecognized directive: rate_limit" and a Caddy that refuses to start.
  2. "For older versions use caddy-ratelimit plugin" -- backwards. The module is
     required for *every* version, not a fallback for old ones.
  3. `key {remotehost}` -- not a Caddy placeholder. The per-client-IP key is
     `{remote_host}` (`{client_ip}` on newer Caddy). So even after installing the
     module, the copied block would not key per client.

The tests below pin the corrected guidance rather than the prose around it: a
needle like "plugin" or "rate limit" appears in both files' comments already and
would have passed against the broken version.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

DEPLOY_DIR = Path(__file__).resolve().parent.parent.parent / "deploy"
NGINX = DEPLOY_DIR / "nginx.conf.example"
CADDY = DEPLOY_DIR / "Caddyfile.example"


def _text(path: Path) -> str:
    assert path.exists(), f"{path.name} is gone from deploy/"
    return path.read_text(encoding="utf-8")


class NginxKeepsItsLiveLimitTests(unittest.TestCase):
    """The asserted asymmetry only means something if nginx's limit is real."""

    def test_the_zone_is_declared_and_applied(self):
        text = _text(NGINX)
        self.assertRegex(
            text, r"limit_req_zone\s+\$binary_remote_addr\s+zone=(\w+):",
            "nginx.conf.example no longer declares a limit_req_zone; the Caddy "
            "comparison below is comparing against nothing",
        )
        zone = re.search(r"limit_req_zone\s+\$binary_remote_addr\s+zone=(\w+):", text)
        assert zone is not None
        self.assertRegex(
            text, re.compile(rf"^\s*limit_req\s+zone={zone.group(1)}\b", re.MULTILINE),
            "the zone is declared but never applied in a location block",
        )

    def test_the_applied_limit_is_not_commented_out(self):
        applied = [
            ln for ln in _text(NGINX).splitlines()
            if "limit_req " in ln and not ln.strip().startswith("#")
        ]
        self.assertTrue(applied, "every limit_req line in nginx.conf.example is commented out")


class CaddyStatesWhatItNeedsTests(unittest.TestCase):
    def test_the_module_requirement_is_named_with_its_build_command(self):
        """The directive needs a custom binary; the file has to say so."""
        text = _text(CADDY)
        self.assertIn(
            "xcaddy build --with github.com/mholt/caddy-ratelimit", text,
            "Caddyfile.example mentions rate_limit without the build command "
            "that makes the directive exist. An operator who uncomments the "
            "block gets 'unrecognized directive: rate_limit' and no Caddy.",
        )

    def test_the_file_does_not_claim_the_directive_is_built_in(self):
        text = _text(CADDY).lower()
        for claim in ("built-in rate limiting", "built in rate limiting"):
            with self.subTest(claim=claim):
                self.assertNotIn(
                    claim, text,
                    "rate_limit is a third-party module at every Caddy version; "
                    "claiming it is built in sends the operator to a config that "
                    "will not start",
                )

    def test_the_documented_key_is_a_real_placeholder(self):
        """`{remotehost}` is not a Caddy placeholder; `{remote_host}` is."""
        text = _text(CADDY)
        self.assertNotIn(
            "{remotehost}", text,
            "not a Caddy placeholder -- a copied block would not key per client",
        )
        if "rate_limit" in text:
            self.assertTrue(
                "{remote_host}" in text or "{client_ip}" in text,
                "the rate_limit example has no per-client-IP key",
            )

    def test_the_gap_against_nginx_is_stated(self):
        """An operator choosing between the two files must see the difference.

        Asserted on the nginx file being named in the rate-limiting guidance,
        because that is the comparison the operator cannot make on their own --
        not on a bare word like "nginx", which appears elsewhere in the file.
        """
        text = _text(CADDY)
        start = text.find("Rate limiting")
        self.assertGreater(start, 0, "the Rate limiting section is gone")
        section = text[start:start + 1400]
        self.assertIn(
            "nginx.conf.example", section,
            "the Rate limiting comment does not tell the operator that "
            "nginx.conf.example ships a live 10r/s limit and this file does not",
        )


if __name__ == "__main__":
    unittest.main()
