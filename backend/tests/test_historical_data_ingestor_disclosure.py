"""Historical data ingestion error lists must not leak raw exception strings.

``HistoricalDataIngestor.ingest_season()``, ``backfill_results_from_fixtures()``,
and ``seed_elo_ratings()`` return success dicts with ``errors: list[str]``
fields. Each method has a generic ``except Exception as exc: errors.append(str(exc))``
path that can capture database errors (connection refused, schema mismatch,
constraint violations), network failures (DNS, timeout), file I/O errors (absolute
paths), and other unstable implementation details.

All three results flow directly to API responses through
``POST /api/sport-optimization/ingest`` (line 48-50) and
``POST /api/sport-optimization/backfill-seed`` (line 92-94), making the errors
list a production HTTP response boundary.

The current implementation leaks raw exception text; the safe contract replaces
unstable exception details with fixed messages while preserving structured
error context (operation type, count).
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app


class HistoricalDataIngestorDisclosureTests(unittest.TestCase):
    """Ingestion error lists in API responses must not expose raw exceptions."""

    def setUp(self):
        self.client = TestClient(app)
        self.api_key = "test-write-key-ingestor-12345"
        self.headers = {"X-API-Key": self.api_key}

    @patch.object(settings, "API_WRITE_KEY", "test-write-key-ingestor-12345")
    @patch.object(settings, "PHASE9_ACCURACY_SPRINT_ENABLED", True)
    @patch("app.services.historical_data_ingestor.get_kernel_session")
    def test_ingest_season_hides_database_error(self, mock_get_session):
        """Database error from ingest_season must not reach HTTP response."""
        sensitive = (
            "could not connect to server: Connection refused "
            "at host db-prod.internal.example.com port 5432"
        )
        mock_session = MagicMock()
        mock_session.rollback = MagicMock()
        mock_session.close = MagicMock()
        mock_session.commit.side_effect = Exception(sensitive)
        mock_get_session.return_value = mock_session

        # Patch the fetcher to return valid data so commit() is reached
        with patch(
            "app.services.historical_data_ingestor.fetch_nba_season_games",
            return_value=[],
        ):
            response = self.client.post(
                "/api/sport-optimization/ingest",
                headers=self.headers,
                json={"sport": "nba", "seasons": ["2023-24"]},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("nba-2023-24", body)
        result = body["nba-2023-24"]
        self.assertIn("errors", result)
        # The safe contract: no raw exception text
        self.assertNotIn("Connection refused", response.text)
        self.assertNotIn("db-prod.internal.example.com", response.text)
        self.assertNotIn("port 5432", response.text)
        # Errors list must contain fixed message
        self.assertGreater(len(result["errors"]), 0)
        self.assertEqual(result["errors"][0], "Database operation failed")

    @patch.object(settings, "API_WRITE_KEY", "test-write-key-ingestor-12345")
    @patch.object(settings, "PHASE9_ACCURACY_SPRINT_ENABLED", True)
    @patch("app.services.historical_data_ingestor.get_kernel_session")
    def test_backfill_hides_constraint_violation(self, mock_get_session):
        """Constraint violation from backfill must not reach HTTP response."""
        sensitive = (
            "IntegrityError: (psycopg2.errors.UniqueViolation) "
            'duplicate key value violates unique constraint "kernel_match_results_pkey" '
            "DETAIL: Key (match_id)=(nba-401584530) already exists."
        )
        mock_session = MagicMock()
        mock_session.rollback = MagicMock()
        mock_session.close = MagicMock()
        mock_session.commit.side_effect = Exception(sensitive)
        mock_session.query.return_value.filter.return_value.all.return_value = []
        mock_get_session.return_value = mock_session

        response = self.client.post(
            "/api/sport-optimization/backfill-seed",
            headers=self.headers,
            json={"sport": "nba", "backfill": True, "seed_elo": False},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("backfill", body)
        result = body["backfill"]
        self.assertIn("errors", result)
        self.assertNotIn("IntegrityError", response.text)
        self.assertNotIn("psycopg2", response.text)
        self.assertNotIn("UniqueViolation", response.text)
        self.assertNotIn("kernel_match_results_pkey", response.text)
        self.assertNotIn("nba-401584530", response.text)
        self.assertGreater(len(result["errors"]), 0)
        self.assertEqual(result["errors"][0], "Backfill operation failed")

    @patch.object(settings, "API_WRITE_KEY", "test-write-key-ingestor-12345")
    @patch.object(settings, "PHASE9_ACCURACY_SPRINT_ENABLED", True)
    @patch("app.services.historical_data_ingestor.get_kernel_session")
    def test_seed_elo_hides_file_path(self, mock_get_session):
        """File I/O error from seed_elo must not reach HTTP response."""
        sensitive = (
            "FileNotFoundError: [Errno 2] No such file or directory: "
            "'/home/deploy/.config/app/kernel_elo_params.json'"
        )
        mock_session = MagicMock()
        mock_session.rollback = MagicMock()
        mock_session.close = MagicMock()
        mock_session.commit.side_effect = Exception(sensitive)
        mock_session.query.return_value.filter.return_value.all.return_value = []
        mock_get_session.return_value = mock_session

        response = self.client.post(
            "/api/sport-optimization/backfill-seed",
            headers=self.headers,
            json={"sport": "nba", "backfill": False, "seed_elo": True},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("seed", body)
        result = body["seed"]
        self.assertIn("errors", result)
        self.assertNotIn("FileNotFoundError", response.text)
        self.assertNotIn("/home/deploy", response.text)
        self.assertNotIn("kernel_elo_params.json", response.text)
        self.assertGreater(len(result["errors"]), 0)
        self.assertEqual(result["errors"][0], "Elo seed operation failed")


if __name__ == "__main__":
    unittest.main()
