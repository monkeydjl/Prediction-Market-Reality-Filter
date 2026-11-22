"""HTTP tests for /quality-metrics/domain-reliability endpoint (LATER #2)."""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import fake_domain_reliability_stat as _fake_stat


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
        fake_stats = [_fake_stat(
            category="prediction_market",
            sample_count=10, correct_count=7, credibility_sum=8.0,
        )]
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
        fake_stats = [_fake_stat(domain="x.com")]
        with patch("app.memory.domain_reliability_store.get_stats", return_value=fake_stats):
            response = self.client.get("/api/quality-metrics/domain-reliability")
        data = response.json()
        self.assertIsNone(data["domains"][0]["reliability_score"])

    def test_endpoint_insufficient_flag(self):
        fake_stats = [_fake_stat(
            domain="x.com", sample_count=2, correct_count=1, credibility_sum=1.0,
        )]
        with patch("app.memory.domain_reliability_store.get_stats", return_value=fake_stats):
            response = self.client.get("/api/quality-metrics/domain-reliability")
        data = response.json()
        self.assertTrue(data["domains"][0]["insufficient_samples"])

    def test_endpoint_invalid_min_samples(self):
        response = self.client.get("/api/quality-metrics/domain-reliability?min_samples=-1")
        self.assertEqual(response.status_code, 422)

    def test_endpoint_total_rows(self):
        fake_stats = [
            _fake_stat(domain="a.com", category="pm", sample_count=1,
                       correct_count=1, credibility_sum=0.5),
            _fake_stat(domain="a.com", category="_all", sample_count=1,
                       correct_count=1, credibility_sum=0.5),
        ]
        with patch("app.memory.domain_reliability_store.get_stats", return_value=fake_stats):
            response = self.client.get("/api/quality-metrics/domain-reliability")
        data = response.json()
        self.assertEqual(data["total_rows"], 2)
        self.assertEqual(data["total_domains"], 1)

    def test_endpoint_stable_json_types(self):
        """Null scores must be JSON null, not string 'N/A'."""
        fake_stats = [_fake_stat(domain="x.com")]
        with patch("app.memory.domain_reliability_store.get_stats", return_value=fake_stats):
            response = self.client.get("/api/quality-metrics/domain-reliability")
        data = response.json()
        # Must be null, not "N/A"
        self.assertIsNone(data["domains"][0]["reliability_score"])
        self.assertIsNone(data["domains"][0]["credibility_avg"])


class TestDomainReliabilityEndpointBrier(unittest.TestCase):
    """Q3: the Brier aggregate must be weighted by the GRADEABLE subset."""

    def setUp(self):
        self.client = TestClient(app)

    def _get(self, fake_stats):
        with patch("app.memory.domain_reliability_store.get_stats",
                   return_value=fake_stats):
            response = self.client.get("/api/quality-metrics/domain-reliability")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_brier_aggregate_is_weighted_by_graded_samples_not_rows(self):
        """One row with 1 graded sample must not outweigh one with 9.

        Row-averaging the two means would give (0.9 + 0.1) / 2 = 0.5; the
        sample-weighted mean is (0.9 + 0.9) / 10 = 0.18.
        """
        data = self._get([
            _fake_stat(domain="a.com", sample_count=1, correct_count=0,
                       brier_sum=0.9, brier_count=1),
            _fake_stat(domain="b.com", sample_count=9, correct_count=9,
                       brier_sum=0.9, brier_count=9),
        ])
        self.assertEqual(data["graded_samples"], 10)
        self.assertEqual(data["total_samples"], 10)
        self.assertAlmostEqual(data["brier_mean"], 0.18, places=6)
        self.assertAlmostEqual(data["brier_skill_score"], 0.82, places=6)

    def test_ungraded_samples_are_not_averaged_in_as_perfect(self):
        """A row with samples but no frozen estimate must not read as Brier 0.

        This is the whole reason brier_count is stored apart from sample_count:
        dividing by sample_count would report 0.5/10 = 0.05 -- an EXCELLENT
        grade -- for a domain where only one event was ever gradeable.
        """
        data = self._get([
            _fake_stat(domain="a.com", sample_count=10, correct_count=5,
                       brier_sum=0.5, brier_count=1),
        ])
        self.assertEqual(data["total_samples"], 10)
        self.assertEqual(data["graded_samples"], 1)
        self.assertAlmostEqual(data["brier_mean"], 0.5, places=6)
        self.assertAlmostEqual(data["brier_skill_score"], 0.5, places=6)

    def test_no_graded_sample_reports_null_not_zero(self):
        data = self._get([
            _fake_stat(domain="a.com", sample_count=4, correct_count=2),
        ])
        self.assertEqual(data["graded_samples"], 0)
        self.assertIsNone(data["brier_mean"])
        self.assertIsNone(data["brier_skill_score"])

    def test_prior_metric_is_reported(self):
        """The consumer of these stats picks a metric; the report must say which."""
        from app.core.config import settings

        with patch.object(settings, "DOMAIN_RELIABILITY_PRIOR_METRIC", "brier"):
            data = self._get([])
        self.assertEqual(data["prior_metric"], "brier")

    def test_prior_metric_defaults_to_hit_rate(self):
        data = self._get([])
        self.assertEqual(data["prior_metric"], "hit_rate")


class TestDomainReliabilityContract(unittest.TestCase):
    """The named models and the producers must agree key for key (audit 53.160).

    This route published no ``response_model`` until 53.160, so its eight keys
    were invisible to the contract layer and the frontend hand-wrote three of
    them. Naming the models is only half the repair: ``DomainReliabilityRow`` is
    registered as a *root*, which makes pydantic2ts close its generated
    interface (``additionalProperties: false``). A closed model that declares
    fewer keys than the producer writes therefore drops the extras **without a
    sound** -- no error, no warning, just a thinner row than the UI was
    promised. Asserting the key *sets* against the producers is what makes that
    silent direction loud, and it fails in both directions at once.
    """

    def setUp(self):
        self.client = TestClient(app)

    def test_row_model_declares_exactly_the_producer_keys(self):
        from app.models.event import DomainReliabilityRow

        # The conftest helper projects through the store's own _row_to_stat,
        # so this is the producer's real output shape, not a copy of it.
        produced = set(_fake_stat())
        declared = set(DomainReliabilityRow.model_fields)
        self.assertEqual(
            sorted(produced), sorted(declared),
            "DomainReliabilityRow and _row_to_stat disagree. produced-only= "
            "keys the closed model would silently drop; declared-only= keys "
            f"the endpoint would serve as validation errors. "
            f"produced-only={sorted(produced - declared)} "
            f"declared-only={sorted(declared - produced)}",
        )

    def test_envelope_model_declares_exactly_the_route_keys(self):
        from app.models.event import DomainReliabilityResponse

        with patch("app.memory.domain_reliability_store.get_stats",
                   return_value=[_fake_stat()]):
            response = self.client.get("/api/quality-metrics/domain-reliability")
        self.assertEqual(response.status_code, 200)
        served = set(response.json())
        declared = set(DomainReliabilityResponse.model_fields)
        # An empty store still writes all eight keys -- the same envelope with
        # zeros and nulls -- so the same assertion holds on that path too.
        with patch("app.memory.domain_reliability_store.get_stats",
                   return_value=[]):
            empty = set(self.client.get(
                "/api/quality-metrics/domain-reliability").json())
        self.assertEqual(served, empty,
                         "the empty-store body must be the same envelope")
        self.assertEqual(
            sorted(served), sorted(declared),
            "the route body and DomainReliabilityResponse disagree. "
            f"served-only={sorted(served - declared)} "
            f"declared-only={sorted(declared - served)}",
        )

    def test_element_keys_reach_the_body_closed(self):
        """The row that reaches the wire carries every key the producer wrote.

        Judged against the producer's own output, not against
        ``model_fields``: comparing the body to the model would also pass a
        symmetric edit that drops the field from both, which is precisely the
        silent direction this file exists to make loud. This assertion knows
        nothing about the model, so the two guards cannot fail together.
        """
        with patch("app.memory.domain_reliability_store.get_stats",
                   return_value=[_fake_stat(sample_count=4, correct_count=3)]):
            row = self.client.get(
                "/api/quality-metrics/domain-reliability").json()["domains"][0]
        produced = _fake_stat(sample_count=4, correct_count=3)
        self.assertEqual(
            sorted(row), sorted(produced),
            f"the wire dropped keys the producer wrote: "
            f"{sorted(set(produced) - set(row))}",
        )
        self.assertEqual(row["wrong_count"], 1)
        self.assertEqual(row["reliability_score"], 0.75)


if __name__ == "__main__":
    unittest.main()
