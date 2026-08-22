"""Football factor seed + P1-V5 bucket-key behaviour.

The two `hasattr` assertions this file used to end with passed against any
stub, and the seed-sum assertion was written as
`abs(soft - 0.44) < 1e-6 or soft > 0.3` — the exact arm has been false since
the seed grew to 0.46, so only the `> 0.3` arm kept it green. Both are
replaced with assertions over the values the code actually produces.
"""
import pytest

from app.kernel.factor_registry import FactorRegistry
from app.kernel.learning_service import (
    competition_with_bucket,
    competition_with_stage,
    confidence_bucket,
    stage_bucket,
)


class TestFootballSoftFactorSeed:
    """The seed list itself, without touching the DB."""

    def test_seed_names_and_categories_are_exact(self):
        # Pin the whole list: a dropped or renamed soft factor is a silent
        # weight change for every football competition seeded from here.
        assert FactorRegistry._FOOTBALL_MULTI_FACTOR_DEFAULTS == [
            ("form", "recent_form", 0.09),
            ("rest", "rest_days", 0.05),
            ("injury", "injury_impact", 0.05),
            ("h2h", "head_to_head", 0.05),
            ("travel", "travel_timezone", 0.04),
            ("xg", "expected_goals", 0.06),
            ("market_value", "squad_value", 0.04),
            ("possession", "possession_shots", 0.04),
            ("referee", "match_official", 0.02),
            ("altitude", "venue_altitude", 0.02),
        ]

    def test_soft_weights_sum_to_a_pinned_value(self):
        # Softs only — EloOdds keeps elo/odds globally, so this sum must stay
        # well below 1.0 or the seed would crowd out the global engine.
        soft = sum(w for _, _, w in FactorRegistry._FOOTBALL_MULTI_FACTOR_DEFAULTS)
        assert soft == pytest.approx(0.46)
        assert soft < 0.5

    def test_every_factor_id_and_category_is_unique(self):
        ids = [fid for fid, _, _ in FactorRegistry._FOOTBALL_MULTI_FACTOR_DEFAULTS]
        cats = [cat for _, cat, _ in FactorRegistry._FOOTBALL_MULTI_FACTOR_DEFAULTS]
        # A duplicated id would seed the same factor twice under one
        # competition; a duplicated category would double-count one signal.
        assert len(set(ids)) == len(ids)
        assert len(set(cats)) == len(cats)


class TestConfidenceBucket:
    """`confidence_bucket` decides which `#c_` row a prediction reads back."""

    @pytest.mark.parametrize("confidence,expected", [
        (0.0, "low"),
        (0.449, "low"),
        (0.45, "mid"),      # boundary is inclusive on the mid side
        (0.6999, "mid"),
        (0.70, "high"),     # boundary is inclusive on the high side
        (1.0, "high"),
    ])
    def test_maps_confidence_to_bucket(self, confidence, expected):
        assert confidence_bucket(confidence) == expected

    @pytest.mark.parametrize("bad", [None, "not-a-number"])
    def test_unusable_confidence_falls_back_to_mid(self, bad):
        # `kernel_db.get_conditional_calibration_row` calls this with whatever
        # confidence a stored prediction carries. Falling back to the middle
        # bucket keeps a null-confidence row readable instead of raising inside
        # the prediction path.
        assert confidence_bucket(bad) == "mid"


class TestStageBucket:
    """`stage_bucket` over the stage strings the adapters actually emit."""

    @pytest.mark.parametrize("stage,expected", [
        # app/sports/*/**_adapter.py `_DEFAULT_STAGE` / parsed stage
        ("regular_season", "regular"),   # nba, mlb, nhl, epl, league_adapter
        ("regular", "regular"),          # lol
        ("playoff", "knockout"),         # nba/mlb/nhl postseason
        ("group_stage", "regular"),      # ucl, world_cup_adapter
        # app/services/world_cup_match_service.py
        ("final", "knockout"),
        ("semifinal", "knockout"),
        ("quarterfinal", "knockout"),
        ("round_of_16", "knockout"),
        ("unknown", "unknown"),
    ])
    def test_covers_every_stage_the_adapters_emit(self, stage, expected):
        assert stage_bucket(stage) == expected

    def test_knockout_wins_over_regular_when_both_tokens_match(self):
        # "postseason" contains the regular token "season". The knockout scan
        # runs first, which is the only reason this is right — swapping the two
        # blocks would silently file playoff samples under `regular`.
        assert stage_bucket("postseason") == "knockout"

    @pytest.mark.parametrize("match_id", [
        "nba-0022500123", "mlb-745804", "nhl-2024020512", "wc2026-1234", "lol-99",
    ])
    def test_match_id_alone_yields_unknown_not_a_guess(self, match_id):
        # Every adapter builds `<prefix>-<id>` with no team names, so the
        # match_id fallback matches no token. That matters because the token
        # list contains the two-character "rs": an id carrying a team nickname
        # ("dodgers", "lakers") would be filed as `regular` by accident.
        assert stage_bucket(None, match_id) == "unknown"

    def test_stage_takes_precedence_over_the_match_id_fallback(self):
        assert stage_bucket("playoff", "nba-0022500123") == "knockout"


class TestCompositeKeys:
    """The keys `parseCalibrationKey` on the frontend has to split again."""

    def test_confidence_and_stage_keys(self):
        assert competition_with_bucket("epl", "high") == "epl#c_high"
        assert competition_with_stage("epl", "knockout") == "epl#s_knockout"

    def test_the_two_prefixes_cannot_collide(self):
        # `frontend/src/lib/sports-api/calibration-buckets.ts` tries the
        # confidence prefix first and falls through to the stage prefix, so a
        # stage key must not contain the confidence prefix or every `#s_` row
        # would be relabelled as a confidence bucket.
        assert "#c_" not in competition_with_stage("epl", "regular")
        assert "#s_" not in competition_with_bucket("epl", "low")


class TestConditionalCalibrationApi:
    """The two fit methods, called by POST /predictions/calibration/conditional."""

    @pytest.mark.parametrize("name", [
        "update_calibration_by_confidence",
        "update_calibration_by_stage",
    ])
    def test_returns_a_count_per_bucket_without_touching_the_db(self, name):
        from unittest.mock import MagicMock, patch

        from app.kernel.learning_service import KernelLearningService

        session = MagicMock()
        # No prediction/outcome pairs -> every bucket is under min_n, so each
        # one must still be reported, as 0. A missing key would read as "this
        # bucket was not attempted" in the operator panel.
        session.execute.return_value.all.return_value = []
        with patch("app.kernel.learning_service.get_kernel_session",
                   return_value=session):
            service = KernelLearningService(factor_registry=FactorRegistry())
            written = getattr(service, name)("epl", "elo_odds")

        expected = (
            {"low", "mid", "high"} if "confidence" in name
            else {"regular", "knockout", "unknown"}
        )
        assert set(written) == expected
        assert all(v == 0 for v in written.values())
        # A thin bucket must not be written as a calibration row.
        session.add.assert_not_called()
