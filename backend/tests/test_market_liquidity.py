"""Tests for match-level liquidity → FeatureSet.custom (P1-E4 feed)."""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.kernel.domain import (
    CompetitionIdentity,
    EnvironmentFeatures,
    FeatureSet,
    GeneralFeatures,
    MarketFeatures,
    MatchIdentity,
    PlayerFeatures,
    SeasonIdentity,
    SportIdentity,
    TeamFeatures,
    TeamIdentity,
)
from app.kernel.market_liquidity import (
    compute_match_liquidity_factor,
    enrich_feature_set_liquidity,
    inject_liquidity_into_custom,
    liquidity_factor_from_amount,
)
from app.kernel.multi_feature_builder import MultiFeatureBuilder


def test_liquidity_factor_from_amount_scales():
    assert liquidity_factor_from_amount(5_000, floor=10_000) == 0.5
    assert liquidity_factor_from_amount(20_000, floor=10_000) == 1.0


def test_compute_returns_none_without_links():
    with patch(
        "app.kernel.sport_market_link_store.SportMarketLinkStore"
    ) as store_cls:
        store_cls.return_value.get_verified_links.return_value = []
        assert compute_match_liquidity_factor("epl-1") is None


def test_compute_uses_max_snapshot_liquidity():
    from app.core.config import settings as _settings

    links = [{"id": 1}, {"id": 2}]
    snaps = {
        1: {"liquidity": 2_000, "implied_prob": 0.5},
        2: {"liquidity": 8_000, "implied_prob": 0.4},
    }

    with (
        patch(
            "app.kernel.sport_market_link_store.SportMarketLinkStore"
        ) as link_cls,
        patch(
            "app.kernel.market_snapshot_store.MarketSnapshotStore"
        ) as snap_cls,
        patch.object(_settings, "DIAGNOSIS_LIQUIDITY_FLOOR", 10_000.0),
    ):
        link_cls.return_value.get_verified_links.return_value = links
        snap_cls.return_value.get_latest_snapshot.side_effect = (
            lambda *, link_id: snaps.get(link_id)
        )
        factor = compute_match_liquidity_factor("nba-9")
        # max 8000 / floor 10000
        assert factor == 0.8


def test_inject_preserves_explicit_liquidity():
    out = inject_liquidity_into_custom(
        {"liquidity_factor": 0.25, "other": 1},
        "epl-1",
    )
    assert out["liquidity_factor"] == 0.25
    assert out["other"] == 1


def test_inject_adds_when_missing():
    with patch(
        "app.kernel.market_liquidity.compute_match_liquidity_factor",
        return_value=0.42,
    ):
        out = inject_liquidity_into_custom({}, "epl-1")
        assert out["liquidity_factor"] == 0.42
        assert out["liquidity_source"] == "sport_market_snapshots"


def test_enrich_feature_set_replaces_custom():
    sport = SportIdentity(code="football", name="Football")
    comp = CompetitionIdentity(code="epl", name="EPL", sport=sport)
    season = SeasonIdentity(competition=comp, season_key="2025-26")
    home = TeamIdentity(code="A", name="A", competition=comp)
    away = TeamIdentity(code="B", name="B", competition=comp)
    match = MatchIdentity(
        match_id="epl-99",
        season=season,
        stage="regular_season",
        round=None,
        home=home,
        away=away,
        kickoff_utc=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    features = FeatureSet(
        match=match,
        general=GeneralFeatures(None, None, None, None),
        team=TeamFeatures(None, None, None, None, None, None, None, None),
        market=MarketFeatures(1.9, 3.5, 4.0, "test", True),
        player=PlayerFeatures(None, None, None, None),
        environment=EnvironmentFeatures(None, None, None, True),
        custom={},
        data_quality="partial",
        quality_notes=[],
        feature_version="1.0",
    )
    with patch(
        "app.kernel.market_liquidity.compute_match_liquidity_factor",
        return_value=0.55,
    ):
        enriched = enrich_feature_set_liquidity(features)
        assert enriched.custom["liquidity_factor"] == 0.55
        assert features.custom == {}


def test_odds_dispersion_inject_no_snaps():
    with patch(
        "app.kernel.traditional_odds_store.TraditionalOddsStore"
    ) as store_cls:
        store_cls.return_value.get_snapshots.return_value = []
        from app.kernel.market_liquidity import inject_odds_dispersion_from_store

        out = inject_odds_dispersion_from_store({}, "epl-1")
        assert "odds_dispersion" not in out


def test_nba_feature_builder_injects_liquidity():
    from app.sports.basketball.feature_builder import BasketballFeatureBuilder

    sport = SportIdentity(code="basketball", name="Basketball")
    comp = CompetitionIdentity(code="nba", name="NBA", sport=sport)
    season = SeasonIdentity(competition=comp, season_key="2025-26")
    home = TeamIdentity(code="BOS", name="Boston", competition=comp)
    away = TeamIdentity(code="LAL", name="Lakers", competition=comp)
    match = MatchIdentity(
        match_id="nba-1",
        season=season,
        stage="regular_season",
        round=None,
        home=home,
        away=away,
        kickoff_utc=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    with patch(
        "app.kernel.market_liquidity.compute_match_liquidity_factor",
        return_value=0.33,
    ):
        fs = BasketballFeatureBuilder().build(match, {"team": {"elo_home": 1500}})
        assert fs.custom.get("liquidity_factor") == 0.33


def test_multi_feature_builder_enriches_liquidity():
    sport = SportIdentity(code="football", name="Football")
    inner = MagicMock()
    sport_id = sport
    inner.sport.return_value = sport_id

    sport2 = SportIdentity(code="football", name="Football")
    comp = CompetitionIdentity(code="epl", name="EPL", sport=sport2)
    season = SeasonIdentity(competition=comp, season_key="2025-26")
    home = TeamIdentity(code="A", name="A", competition=comp)
    away = TeamIdentity(code="B", name="B", competition=comp)
    match = MatchIdentity(
        match_id="epl-1",
        season=season,
        stage="regular_season",
        round=None,
        home=home,
        away=away,
        kickoff_utc=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    base = FeatureSet(
        match=match,
        general=GeneralFeatures(None, None, None, None),
        team=TeamFeatures(None, None, None, None, None, None, None, None),
        market=MarketFeatures(None, None, None, None, False),
        player=PlayerFeatures(None, None, None, None),
        environment=EnvironmentFeatures(None, None, None, False),
        custom={},
        data_quality="partial",
        quality_notes=[],
        feature_version="1.0",
    )
    inner.build.return_value = base

    with patch(
        "app.kernel.market_liquidity.compute_match_liquidity_factor",
        return_value=0.7,
    ):
        mfb = MultiFeatureBuilder({"epl-": inner})
        out = mfb.build(match, {})
        assert out.custom.get("liquidity_factor") == 0.7
        inner.build.assert_called_once()
