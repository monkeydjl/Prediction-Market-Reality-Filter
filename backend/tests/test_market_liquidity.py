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
    group_liquidity_factor,
    inject_liquidity_into_custom,
    liquidity_factor_from_amount,
)
from app.kernel.multi_feature_builder import MultiFeatureBuilder


def test_liquidity_factor_from_amount_scales():
    assert liquidity_factor_from_amount(5_000, floor=10_000) == 0.5


# --- group_liquidity_factor: one rule, no drift between its two callers ---


def test_group_factor_unmeasured_venue_is_not_penalized_when_mixed():
    """The mixed case, which both callers previously got wrong.

    One venue publishes no depth, another publishes $100. Taking max over the
    measured subset scored the group as though it were a $100 market: 0.01 at the
    10k floor, where an unmeasured venue *alone* scored None (no penalty).
    Learning that some other venue is thin taught us nothing about the first.
    """
    assert group_liquidity_factor([None, 100.0], floor=10_000.0) is None
    assert group_liquidity_factor([None], floor=10_000.0) is None
    # A zero or unparseable depth is unmeasured too, not a measurement of zero.
    assert group_liquidity_factor([0.0, 100.0], floor=10_000.0) is None
    assert group_liquidity_factor(["", 100.0], floor=10_000.0) is None


def test_group_factor_monotonic_in_added_unmeasured_venues():
    """Adding a venue of unknown depth must never lower the factor."""
    thin_only = group_liquidity_factor([100.0], floor=10_000.0)
    plus_unmeasured = group_liquidity_factor([100.0, None], floor=10_000.0)
    assert thin_only == 0.01
    # None renders as "no penalty" at both call sites, i.e. >= any measured ramp.
    assert plus_unmeasured is None


def test_group_factor_all_measured_is_the_max_ramp():
    """Regression endpoint: with every venue measured, this is the old rule."""
    assert group_liquidity_factor([2_000.0, 8_000.0], floor=10_000.0) == 0.8
    assert group_liquidity_factor([100.0], floor=10_000.0) == 0.01
    assert group_liquidity_factor([50_000.0], floor=10_000.0) == 1.0
    assert group_liquidity_factor([1_000.0, 3_000.0], floor=5_000.0) == 0.6


def test_group_factor_empty_and_degenerate_floor():
    assert group_liquidity_factor([], floor=10_000.0) is None
    assert group_liquidity_factor([100.0], floor=0.0) == 1.0


def test_edge_detector_and_feature_feed_agree_on_the_same_group():
    """The test that would have caught the drift this helper exists to prevent.

    market_liquidity's docstring claimed its semantics mirror
    EdgeDetectorService._compute_liquidity_factor, and nothing checked it. When
    the edge path's mixed case was fixed first, the same [unmeasured, $100] group
    scored 1.0 there and 0.01 here — a 100x disagreement between two functions
    documented as mirrors.

    This must call the two *entry points*, not the shared helper twice with
    different floors. That weaker version passes even when a caller stops using
    the helper altogether, which is precisely the failure it claims to cover —
    verified by re-introducing the real drift and watching it stay green.

    Each side renders "do not penalize" differently — the edge factor is
    multiplied so it uses 1.0, the feed omits the key so it returns None — so
    agreement is asserted on *whether that verdict was reached*, not on equal
    numbers. The floors stay deliberately different (5000 for edge scoring,
    10000 for the diagnosis pipeline).
    """
    from app.core.config import settings as _settings
    from app.kernel.edge_detector_service import (
        _EDGE_LIQUIDITY_FLOOR,
        EdgeDetectorService,
    )

    groups = [
        [None, 100.0],
        [None],
        [100.0],
        [0.0, 5_000.0],
        [2_000.0, 8_000.0],
    ]

    # "Declined" cannot be read off the factor value: at the edge floor of 5000 a
    # genuinely deep group saturates the ramp at 1.0, which is the same number
    # the edge side uses to render "do not penalize". Ask the rule for its
    # verdict, then assert each caller rendered *that* verdict.
    for group in groups:
        declined = group_liquidity_factor(group, floor=1.0) is None
        # Edge side: build the (link, snapshot) shape it consumes.
        links_with_snaps = [
            ({"id": i}, {"liquidity": liq, "implied_prob": 0.5})
            for i, liq in enumerate(group, start=1)
        ]
        edge_factor = EdgeDetectorService._compute_liquidity_factor(
            EdgeDetectorService.__new__(EdgeDetectorService), links_with_snaps
        )

        # Feed side: same group through the store-backed entry point.
        snaps = {
            i: {"liquidity": liq, "implied_prob": 0.5}
            for i, liq in enumerate(group, start=1)
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
            link_cls.return_value.get_verified_links.return_value = [
                {"id": i} for i in snaps
            ]
            snap_cls.return_value.get_latest_snapshot.side_effect = (
                lambda *, link_id: snaps.get(link_id)
            )
            feed_factor = compute_match_liquidity_factor("nba-drift")

        # The feed renders "declined" as None; a measured group must yield a
        # number. That direction is unambiguous.
        assert (feed_factor is None) == declined, (
            f"group {group}: feed={feed_factor}, rule declined={declined}"
        )
        if declined:
            # The edge side multiplies, so declining must render as exactly 1.0 —
            # no penalty. A measured-subset max would have produced 0.02 here.
            assert edge_factor == 1.0, (
                f"group {group}: edge penalized an unmeasured venue "
                f"with {edge_factor}"
            )
        else:
            # Measured group: the edge side must apply its own ramp at its own
            # floor, which is the number the shared rule gives for that floor.
            expected = group_liquidity_factor(group, floor=_EDGE_LIQUIDITY_FLOOR)
            assert edge_factor == expected, (
                f"group {group}: edge={edge_factor}, shared rule={expected}"
            )


def test_link_without_snapshot_counts_as_unmeasured_not_dropped():
    """A link with no snapshot is unmeasured, not absent.

    This path used to ``continue`` past such a link, letting a measured venue
    decide the factor alone — while the edge detector read a missing snapshot as
    unmeasured and declined to penalize. Traditional-odds links never receive a
    snapshot at all (their synthetic contract_id cannot be priced against the
    Polymarket gamma API), so this was the common case, not a rare one.
    """
    from app.core.config import settings as _settings

    links = [{"id": 1}, {"id": 2}]
    snaps = {2: {"liquidity": 100, "implied_prob": 0.4}}  # link 1 has no snapshot

    with (
        patch("app.kernel.sport_market_link_store.SportMarketLinkStore") as link_cls,
        patch("app.kernel.market_snapshot_store.MarketSnapshotStore") as snap_cls,
        patch.object(_settings, "DIAGNOSIS_LIQUIDITY_FLOOR", 10_000.0),
    ):
        link_cls.return_value.get_verified_links.return_value = links
        snap_cls.return_value.get_latest_snapshot.side_effect = (
            lambda *, link_id: snaps.get(link_id)
        )
        # Old behavior: link 1 dropped, factor = min(100/10000, 1) = 0.01.
        assert compute_match_liquidity_factor("nba-10") is None


def test_mixed_measured_and_unmeasured_omits_the_key_entirely():
    """End to end: the FeatureSet must carry no liquidity_factor in the mixed case.

    Omission is the rendering of "do not penalize" on this side, so the assertion
    is on the key's absence rather than on a value. odds_quality and
    market_quality_damp both skip the term when the key is missing; a 0.01 would
    have damped confidence by ~9.8%.
    """
    from app.core.config import settings as _settings

    links = [{"id": 1}, {"id": 2}]
    snaps = {
        1: {"liquidity": None, "implied_prob": 0.5},
        2: {"liquidity": 100, "implied_prob": 0.4},
    }

    with (
        patch("app.kernel.sport_market_link_store.SportMarketLinkStore") as link_cls,
        patch("app.kernel.market_snapshot_store.MarketSnapshotStore") as snap_cls,
        patch.object(_settings, "DIAGNOSIS_LIQUIDITY_FLOOR", 10_000.0),
    ):
        link_cls.return_value.get_verified_links.return_value = links
        snap_cls.return_value.get_latest_snapshot.side_effect = (
            lambda *, link_id: snaps.get(link_id)
        )
        out = inject_liquidity_into_custom({"other": 1}, "nba-11")

    assert "liquidity_factor" not in out
    assert "liquidity_source" not in out
    assert out["other"] == 1
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
