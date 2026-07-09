import unittest
from unittest.mock import patch

from app.core.scheduler import _run_world_cup_bundle_import


class WorldCupSchedulerImportModeTests(unittest.TestCase):
    def test_football_data_mode_imports_football_data_standings(self):
        with patch(
            "app.services.football_data_source.import_world_cup_football_data_standings",
            return_value={"provider": "football_data", "imported": 48},
        ) as mock_import:
            result = _run_world_cup_bundle_import("football_data", replace=True)

        mock_import.assert_called_once_with(replace=True)
        self.assertEqual(result["provider"], "football_data")
        self.assertEqual(result["imported"], 48)


if __name__ == "__main__":
    unittest.main()
