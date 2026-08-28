from pathlib import Path
import unittest


class StartBatContractTest(unittest.TestCase):
    def test_production_start_launches_frontend_3000_and_backend_8000(self) -> None:
        script = (Path(__file__).resolve().parents[2] / "start.bat").read_text(
            encoding="utf-8"
        )

        production_block = script.split(
            "REM ===================== DEVELOPMENT", maxsplit=1
        )[0]

        self.assertIn("Starting backend (:8000) + frontend (:3000)", production_block)
        self.assertIn('start "PMRF backend :8000"', production_block)
        self.assertIn("BACKEND_SERVE_FRONTEND=false", production_block)
        self.assertIn('start "PMRF frontend :3000"', production_block)
        self.assertIn("/D \"%ROOT%\"", production_block)
        # The contract is the port, the directory and the bind address. The
        # interpreter is asserted separately below: pinning it into this string
        # is what made switching to the venv interpreter look like a regression.
        self.assertIn(
            "-m http.server 3000 --directory frontend\\out --bind localhost",
            production_block,
        )
        self.assertIn(
            '"!PY!" -m http.server 3000',
            production_block,
            "the static server must use the interpreter start.bat resolved, not PATH python",
        )
        self.assertIn("Start-Process 'http://localhost:3000'", production_block)
        self.assertNotIn("Start-Process 'http://localhost:8000'", production_block)
