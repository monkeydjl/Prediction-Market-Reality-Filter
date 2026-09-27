"""The staging/production overlay examples must spell out the security keys.

``_load_env_files`` reads base ``.env`` first, then the overlay with
``override=True``. Only keys the overlay names get overridden — anything it
omits keeps whatever development put in ``.env``. So a key missing from
``.env.staging.example`` is not a documentation gap: an operator who copies the
template gets the dev value in staging, silently.
"""
import re
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Keys whose development default is unsafe outside development, so every
# overlay has to state its own value rather than inherit one.
SECURITY_KEYS = (
    "ALLOW_OPEN_WRITES",
    "API_WRITE_KEY",
    "CORS_ALLOWED_ORIGINS",
    "SERVER_RELOAD",
    "LLM_DAILY_COST_CAP_USD",
)

OVERLAY_EXAMPLES = (".env.staging.example", ".env.production.example")

# The two halves of each push channel. `.env.example` sets all five off, and
# neither overlay mentioned any of them, so a production deploy from the
# template had no channel live and no line anywhere saying so.
ALERT_CHANNEL_KEYS = (
    "SENTRY_DSN",
    "SCHEDULER_FAILURE_ALERT_ENABLED",
    "SCHEDULER_FAILURE_ALERT_WEBHOOK_URL",
    "DRIFT_ALERTS_ENABLED",
    "DRIFT_ALERT_WEBHOOK_URL",
)

# Keys an overlay must not assign empty. Overlays load with `override=True`, so
# `KEY=` does not mean "unset, inherit" — it overwrites whatever the base .env
# holds with the empty string. For the two below that silently removes a
# configured channel, which is the exact failure this section documents.
MUST_NOT_BLANK = ("SENTRY_DSN", "SCHEDULER_FAILURE_ALERT_WEBHOOK_URL")

# Flags that gate an operator-visible surface, default off, and are assigned in
# exactly one place: the base ``.env.example``. The production overlay states the
# six it turns on and used to mention none of these, so a deploy copied from it
# rendered those surfaces permanently empty with nothing in the deploy files
# saying why -- and "empty" is also what they look like when there is genuinely
# nothing to show.
OVERLAY_FLAGS_LEFT_OFF = (
    "REVIEW_QUEUE_ENABLED",
    "DECISION_TIMELINE_ENABLED",
    "CONCLUSION_CHALLENGE_ENABLED",
    "EVENT_CHALLENGE_ENABLED",
    "SOURCE_TRUST_REGISTRY_ENABLED",
    "WORLD_CUP_CHALLENGE_ENABLED",
)


def _assignments(path: Path) -> set[str]:
    return {
        line.split("=", 1)[0].strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }


def _value(path: Path, key: str) -> str | None:
    """Last assigned value for ``key``, comment suffix stripped."""
    found = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        name, raw = line.split("=", 1)
        if name.strip() == key:
            found = raw.split("#", 1)[0].strip()
    return found


def _names_in_any_form(path: Path) -> set[str]:
    """Keys the template names, commented or assigned.

    A substring match is not enough. The production template's prose also
    mentions ``REVIEW_QUEUE_ENABLED``, so asserting the name appears anywhere
    stayed green after the commented assignment line was deleted -- the test
    locked nothing. Requiring a ``KEY=`` shape locks the line an operator
    actually uncomments.
    """
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip().lstrip("#").strip()
        if "=" not in stripped:
            continue
        name = stripped.split("=", 1)[0].strip()
        if re.fullmatch(r"[A-Z0-9_]+", name):
            names.add(name)
    return names


class TestEnvOverlayExamples(unittest.TestCase):
    def test_overlays_assign_every_security_key(self):
        for name in OVERLAY_EXAMPLES:
            path = BACKEND_DIR / name
            with self.subTest(overlay=name):
                self.assertTrue(path.exists(), f"{name} is missing")
                missing = [k for k in SECURITY_KEYS if k not in _assignments(path)]
                self.assertEqual(
                    missing,
                    [],
                    f"{name} omits {missing}; the overlay only overrides keys it "
                    f"names, so these silently inherit the development .env value",
                )

    def test_the_templates_do_not_tell_an_operator_to_activate_from_inside(self):
        """The activation instruction has to name the base file or the env var.

        Both templates used to say "copy to .env.<env> and set PMRF_ENV=<env> to
        activate", with `PMRF_ENV=<env>` on their own line five. That does not
        activate anything: `_load_env_files` reads the base `.env`, resolves the
        overlay from what it found, and only then reads the overlay -- so the line
        inside it is read after the decision. An operator following the template
        exactly ran development, and every key the overlay named kept its
        development value.
        """
        for name in OVERLAY_EXAMPLES:
            path = BACKEND_DIR / name
            with self.subTest(overlay=name):
                header = "\n".join(
                    line
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line.lstrip().startswith("#")
                )
                self.assertTrue(
                    "base backend/.env" in header.lower()
                    or "process environment" in header.lower(),
                    f"{name} does not say where PMRF_ENV has to be set; setting "
                    f"it inside this file cannot select this file",
                )

    def test_the_base_template_documents_the_selection(self):
        """``.env.example`` is the file that decides, so it has to say so.

        Checking that "staging" and "production" appear somewhere near the top is
        not a check: those words also occur in the neighbouring lines about what
        the overlays override, so deleting the selection line left the assertion
        green. The three facts an operator needs are asserted individually, each
        one a misconfiguration that used to boot silently.
        """
        lines = (BACKEND_DIR / ".env.example").read_text(encoding="utf-8").splitlines()
        assigned = [i for i, line in enumerate(lines) if line.startswith("PMRF_ENV=")]
        self.assertEqual(len(assigned), 1, "PMRF_ENV is not assigned exactly once")
        header = "\n".join(lines[: assigned[0]]).lower()

        # The legal values come from the resolver, so adding an overlay without
        # documenting it fails here. Asking whether each value appears *somewhere*
        # in the header is answered incidentally, and so is asking for one line
        # that lists all three -- the sentence about a bad value refusing to boot
        # names all three too. The selection line is the one that also says which
        # value applies when PMRF_ENV is unset, which is what an operator reading
        # a template needs and what the resolver's default encodes.
        from app.core.config import ENV_OVERLAYS

        enumerating = [
            line
            for line in lines[: assigned[0]]
            if all(value in line.lower() for value in ENV_OVERLAYS)
            and "default" in line.lower()
        ]
        self.assertTrue(
            enumerating,
            f"no header line enumerates the legal values {sorted(ENV_OVERLAYS)} and "
            f"marks the default; an operator cannot tell which spellings are "
            f"accepted or what applies when PMRF_ENV is unset, and a wrong value "
            f"now refuses to boot",
        )
        self.assertIn(
            "process environment",
            header,
            "the header does not say the selection may live in the process "
            "environment; systemd and Docker both set it that way",
        )
        self.assertIn(
            "cannot select",
            header,
            "the header does not deny that an overlay can select itself, which is "
            "exactly what both overlay templates used to instruct",
        )
        self.assertIn(
            "raises at startup",
            header,
            "the header does not say a bad value or an absent overlay now fails "
            "loudly; both used to fall back to development values silently",
        )

    def test_overlays_ship_a_real_cost_cap(self):
        """Naming the key is not enough — 0 means unlimited.

        A template that says "set a real number" but assigns 0 hands a
        copy-paste operator an uncapped paid key, which is the one value in
        SECURITY_KEYS where the unsafe setting is also the shipped one.
        """
        for name in OVERLAY_EXAMPLES:
            path = BACKEND_DIR / name
            with self.subTest(overlay=name):
                raw = _value(path, "LLM_DAILY_COST_CAP_USD")
                self.assertIsNotNone(raw, f"{name} does not assign the cap")
                self.assertGreater(
                    float(raw),
                    0,
                    f"{name} ships LLM_DAILY_COST_CAP_USD={raw}; 0 disables the "
                    f"guard, so the template would deploy an unlimited cap",
                )

    def test_the_production_overlay_states_its_alert_posture(self):
        """A production template that never mentions alerting ships none.

        All five settings default off and appear only in `.env.example`, so an
        operator who filled in `.env.production` from this template got a deploy
        where a failed job reaches the loop-run ledger, the Prometheus counter
        and `/api/health` — all pull-only — and nothing else. Naming the keys
        here is the half of the fix an operator reads before the first boot; the
        WARNING from `app.main._live_alert_channels` is the half that also
        reaches Docker and systemd, which never read this file.

        Staging is deliberately not held to this: it is pre-prod, an unwatched
        staging failure costs nothing, and requiring the keys there would only
        teach an operator to paste a second DSN.

        A commented `# SENTRY_DSN=...` line satisfies this on purpose. For the
        DSN and the two URLs a real assignment is the *wrong* fix -- an overlay
        loads with `override=True`, so `SENTRY_DSN=` would blank a DSN set in the
        base `.env` (forbidden by the sibling test below). What the template owes
        an operator here is the name and the posture, not a value.
        """
        text = (BACKEND_DIR / ".env.production.example").read_text(encoding="utf-8")
        missing = [k for k in ALERT_CHANNEL_KEYS if k not in text]
        self.assertEqual(
            missing,
            [],
            f".env.production.example never mentions {missing}; every push "
            f"channel defaults off, so a deploy from this template notifies "
            f"nobody and the template does not say so",
        )

    def test_the_production_overlay_states_its_llm_startup_posture(self):
        """A production template that omits the check ships silent degradation.

        ``LLM_STARTUP_CHECK_ENABLED`` defaults to false and the production
        overlay used not to mention it, so an operator who filled
        ``.env.production`` from this template got a deploy where an unreachable
        provider or a rejected key did **not** stop the boot: ``app.main`` skips
        ``validate_primary_llm_startup`` and every LLM-backed answer quietly comes
        back degraded. The template already ships
        ``DECISION_QUALITY_ENABLED=true``, so "runs without an LLM" is not the
        posture it means.

        Asserting the value and not just the mention is the point: a template
        that names the key and assigns false is green on a mention-only check and
        still boots a broken deployment.
        """
        assigned = _value(
            BACKEND_DIR / ".env.production.example", "LLM_STARTUP_CHECK_ENABLED"
        )
        self.assertIsNotNone(
            assigned,
            ".env.production.example does not assign LLM_STARTUP_CHECK_ENABLED; "
            "production then inherits the development default (off) and boots on "
            "a bad LLM credential instead of refusing",
        )
        self.assertEqual(
            assigned.lower(),
            "true",
            "the production template ships the fail-fast value; a deployment that "
            "must boot without an LLM sets it false in its own .env.production",
        )

    def test_the_production_overlay_names_the_flags_it_leaves_off(self):
        """Naming them is the half of the fix an operator reads before the boot.

        The template asserts "the overlays are the product" and then turns on six
        flags. Six more gate a product surface and ship off, assigned only in the
        base ``.env.example`` -- so an operator who filled ``.env.production``
        from this template had no line anywhere telling them the review queue and
        the decision timeline would stay empty, or why.

        The name has to arrive as a ``KEY=`` line, not as prose: the surrounding
        comment block mentions ``REVIEW_QUEUE_ENABLED`` in a sentence, which is
        enough to satisfy a substring check and not enough to be a line an
        operator uncomments.
        """
        names = _names_in_any_form(BACKEND_DIR / ".env.production.example")
        missing = [k for k in OVERLAY_FLAGS_LEFT_OFF if k not in names]
        self.assertEqual(
            missing,
            [],
            f".env.production.example never names {missing}; each one gates a "
            f"surface an operator will look at, and none of them is named in any "
            f"other deploy file either",
        )

    def test_the_production_overlay_does_not_pin_those_flags_off(self):
        """A commented mention is the fix; an assignment is a new bug.

        Overlays load with ``override=True``, so pinning one of these to false
        here would silently cancel an operator who enabled it in the base
        ``backend/.env`` -- the same failure the alert-channel keys above are left
        commented to avoid. The alert block's reasoning applies unchanged: what
        the template owes an operator here is the name and the posture, not a
        value.
        """
        assigned = _assignments(BACKEND_DIR / ".env.production.example")
        pinned = sorted(set(OVERLAY_FLAGS_LEFT_OFF) & assigned)
        self.assertEqual(
            pinned,
            [],
            f".env.production.example assigns {pinned}; an overlay value wins "
            f"over the base .env, so this would switch off a flag an operator "
            f"turned on there",
        )

    def test_no_overlay_blanks_a_configured_channel(self):
        """`KEY=` in an overlay is an override to empty, not an inheritance.

        The templates ship `API_WRITE_KEY=` on purpose — an empty write key is
        fail-closed, so it forces the operator's hand. An empty `SENTRY_DSN`
        fails the other way: it silently switches off a channel the base `.env`
        configured, and the only sign is the neutral "Sentry disabled" line
        `init_sentry` logs either way.
        """
        for name in OVERLAY_EXAMPLES:
            path = BACKEND_DIR / name
            for key in MUST_NOT_BLANK:
                with self.subTest(overlay=name, key=key):
                    raw = _value(path, key)
                    if raw is None:
                        continue  # not assigned: the base .env value carries over
                    self.assertNotEqual(
                        raw.strip("\"'"),
                        "",
                        f"{name} assigns {key} empty, which overrides the base "
                        f".env with nothing. Leave the key out to inherit, or "
                        f"document it in a comment",
                    )


if __name__ == "__main__":
    unittest.main()
