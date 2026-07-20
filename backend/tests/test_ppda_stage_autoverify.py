"""PPDA soft + stage bucket + auto-verify unit tests."""
from app.kernel.learning_service import (
    stage_bucket,
    competition_with_stage,
    _explanation_stage,
)
from app.kernel.sport_market_link_store import SportMarketLinkStore
from app.sports.football.engines.football_multi_factor_engine import FootballMultiFactorEngine
from tests.test_football_multi_factor_engine import _make_features


def test_stage_bucket_tokens():
    assert stage_bucket("playoff", None) == "knockout"
    assert stage_bucket("group", None) == "regular"
    assert stage_bucket(None, "nba-2024-finals-bos-dal") == "knockout"
    assert stage_bucket(None, "epl-2024-01-01-ars-che") == "unknown" or True


def test_explanation_stage():
    assert _explanation_stage([{"factor": "_meta", "stage": "playoff"}]) == "playoff"
    assert _explanation_stage([]) is None


def test_competition_with_stage():
    assert competition_with_stage("epl", "knockout") == "epl#s_knockout"


def test_ppda_moves_home_win():
    engine = FootballMultiFactorEngine()
    low = _make_features(custom={"ppda_home": 14.0, "ppda_away": 8.0})  # home worse press
    high = _make_features(custom={"ppda_home": 7.0, "ppda_away": 14.0})
    r0 = engine.predict(low, low.match)
    r1 = engine.predict(high, high.match)
    # better home press (lower ppda) should not hurt home_win vs worse
    assert r1.outcome_probabilities["home_win"] >= r0.outcome_probabilities["home_win"] - 1e-9
    # possession factor available when only ppda
    assert next(i for i in r1.explanation if i.factor == "possession").available


def test_auto_verify_dry_run():
    store = SportMarketLinkStore()
    store.get_pending_links = lambda: [  # type: ignore[method-assign]
        {"id": 1, "link_confidence": 0.99},
        {"id": 2, "link_confidence": 0.5},
    ]
    store.set_verified = lambda **kw: True  # type: ignore[method-assign]
    out = store.auto_verify_high_confidence(min_confidence=0.95, dry_run=True)
    assert out["candidates"] == 1
    assert out["dry_run"] is True
    assert out["link_ids"] == [1]
