"""The staging/production overlay examples must spell out the security keys.

``_load_env_files`` reads base ``.env`` first, then the overlay with
``override=True``. Only keys the overlay names get overridden — anything it
omits keeps whatever development put in ``.env``. So a key missing from
``.env.staging.example`` is not a documentation gap: an operator who copies the
template gets the dev value in staging, silently.
"""
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
