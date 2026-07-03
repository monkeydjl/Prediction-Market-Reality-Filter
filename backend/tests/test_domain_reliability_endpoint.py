"""HTTP tests for /quality-metrics/domain-reliability endpoint (LATER #2)."""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app


class TestDomainReliabilityEndpoint(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_endpoint_empty_db_returns_200(self):
        with patch("app.memory.domain_reliability_store.get_stats", return_value=[]):
            response = self.client.get("/api/quality-metrics/domain-reliability")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("domains", data)
        self.assertIn("total_domains", data)
        self.assertIn("total_rows", data)

    def test_endpoint_returns_stats(self):
        fake_stats = [{
            "domain": "reuters.com", "category": "prediction_market",
            "sample_count": 10, "correct_count": 7, "wrong_count": 3,
            "credibility_sum": 8.0, "reliability_score": 0.7,
            "credibility_avg": 0.8, "insufficient_samples": False,
            "first_seen": "2026-01-01T00:00:00Z", "last_updated": "2026-07-01T00:00:00Z",
        }]
        with patch("app.memory.domain_reliability_store.get_stats", return_value=fake_stats):
            response = self.client.get("/api/quality-metrics/domain-reliability")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["domains"]), 1)
        self.assertEqual(data["total_domains"], 1)
        self.assertEqual(data["total_rows"], 1)

    def test_endpoint_filter_domain(self):
        with patch("app.memory.domain_reliability_store.get_stats", return_value=[]) as mock:
            self.client.get("/api/quality-metrics/domain-reliability?domain=reuters.com")
        mock.assert_called_once_with(domain="reuters.com", category=None, min_samples=0)

    def test_endpoint_filter_category(self):
        with patch("app.memory.domain_reliability_store.get_stats", return_value=[]) as mock:
            self.client.get("/api/quality-metrics/domain-reliability?category=prediction_market")
        mock.assert_called_once_with(domain=None, category="prediction_market", min_samples=0)

    def test_endpoint_min_samples_filter(self):
        with patch("app.memory.domain_reliability_store.get_stats", return_value=[]) as mock:
            self.client.get("/api/quality-metrics/domain-reliability?min_samples=10")
        mock.assert_called_once_with(domain=None, category=None, min_samples=10)

    def test_endpoint_reliability_score_null(self):
        fake_stats = [{
            "domain": "x.com", "category": "_all",
            "sample_count": 0, "correct_count": 0, "wrong_count": 0,
            "credibility_sum": 0.0, "reliability_score": None,
            "credibility_avg": None, "insufficient_samples": True,
            "first_seen": "2026-01-01T00:00:00Z", "last_updated": "2026-07-01T00:00:00Z",
        }]
        with patch("app.memory.domain_reliability_store.get_stats", return_value=fake_stats):
            response = self.client.get("/api/quality-metrics/domain-reliability")
        data = response.json()
        self.assertIsNone(data["domains"][0]["reliability_score"])

    def test_endpoint_insufficient_flag(self):
        fake_stats = [{
            "domain": "x.com", "category": "_all",
            "sample_count": 2, "correct_count": 1, "wrong_count": 1,
            "credibility_sum": 1.0, "reliability_score": 0.5,
            "credibility_avg": 0.5, "insufficient_samples": True,
            "first_seen": "2026-01-01T00:00:00Z", "last_updated": "2026-07-01T00:00:00Z",
        }]
        with patch("app.memory.domain_reliability_store.get_stats", return_value=fake_stats):
            response = self.client.get("/api/quality-metrics/domain-reliability")
        data = response.json()
        self.assertTrue(data["domains"][0]["insufficient_samples"])

    def test_endpoint_invalid_min_samples(self):
        response = self.client.get("/api/quality-metrics/domain-reliability?min_samples=-1")
        self.assertEqual(response.status_code, 422)

    def test_endpoint_total_rows(self):
        fake_stats = [
            {"domain": "a.com", "category": "pm", "sample_count": 1,
             "correct_count": 1, "wrong_count": 0, "credibility_sum": 0.5,
             "reliability_score": 1.0, "credibility_avg": 0.5,
             "insufficient_samples": True,
             "first_seen": "2026-01-01T00:00:00Z", "last_updated": "2026-07-01T00:00:00Z"},
            {"domain": "a.com", "category": "_all", "sample_count": 1,
             "correct_count": 1, "wrong_count": 0, "credibility_sum": 0.5,
             "reliability_score": 1.0, "credibility_avg": 0.5,
             "insufficient_samples": True,
             "first_seen": "2026-01-01T00:00:00Z", "last_updated": "2026-07-01T00:00:00Z"},
        ]
        with patch("app.memory.domain_reliability_store.get_stats", return_value=fake_stats):
            response = self.client.get("/api/quality-metrics/domain-reliability")
        data = response.json()
        self.assertEqual(data["total_rows"], 2)
        self.assertEqual(data["total_domains"], 1)

    def test_endpoint_stable_json_types(self):
        """Null scores must be JSON null, not string 'N/A'."""
        fake_stats = [{
            "domain": "x.com", "category": "_all",
            "sample_count": 0, "correct_count": 0, "wrong_count": 0,
            "credibility_sum": 0.0, "reliability_score": None,
            "credibility_avg": None, "insufficient_samples": True,
            "first_seen": "2026-01-01T00:00:00Z", "last_updated": "2026-07-01T00:00:00Z",
        }]
        with patch("app.memory.domain_reliability_store.get_stats", return_value=fake_stats):
            response = self.client.get("/api/quality-metrics/domain-reliability")
        data = response.json()
        # Must be null, not "N/A"
        self.assertIsNone(data["domains"][0]["reliability_score"])
        self.assertIsNone(data["domains"][0]["credibility_avg"])


if __name__ == "__main__":
    unittest.main()
