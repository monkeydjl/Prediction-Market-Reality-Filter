import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = REPO_ROOT / "deploy"


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


if __name__ == "__main__":
    unittest.main()
