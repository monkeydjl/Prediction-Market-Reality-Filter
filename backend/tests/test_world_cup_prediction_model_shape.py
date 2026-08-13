"""Freeze the NOT NULL / index shape of the World Cup prediction tables.

Why this exists: `app/models/world_cup_prediction.py` uses SQLAlchemy 2.0
``Mapped[T]`` / ``mapped_column()``, where nullability is inferred from the
annotation. ``Column(String(64))`` defaults to ``nullable=True``, but
``mapped_column()`` under a non-Optional ``Mapped[str]`` defaults to
``nullable=False``. So dropping a ``| None`` from an annotation silently emits
different DDL — no test would otherwise notice, because SQLite files created
fresh by the test suite just adopt the new shape. These tables are created with
``Base.metadata.create_all`` and never migrated, so a flip would break existing
production databases on write, not on deploy.

``elo_ratings`` / ``odds_cache`` hang off the same ``Base`` from their own
service modules and are asserted here too, so the count also catches a table
quietly disappearing from the metadata.
"""

import app.services.elo_ratings_service  # noqa: F401  registers elo_ratings
import app.services.odds_cache_service  # noqa: F401  registers odds_cache
from app.models.world_cup_prediction import Base

# Column names emitted as NOT NULL, per table. Regenerate deliberately (and say
# why in the commit) if a column's nullability is genuinely meant to change.
EXPECTED_NOT_NULL: dict[str, tuple[str, ...]] = {
    "ai_analysis_history": (
        "analysis_text", "confidence", "created_at", "id", "match_id",
        "predicted_away_score", "predicted_home_score",
    ),
    "ai_optimized_predictions": (
        "created_at", "id", "match_id", "optimized_away_score",
        "optimized_away_win_prob", "optimized_confidence", "optimized_draw_prob",
        "optimized_home_score", "optimized_home_win_prob", "original_away_score",
        "original_away_win_prob", "original_confidence", "original_draw_prob",
        "original_engine", "original_home_score", "original_home_win_prob",
    ),
    "elo_ratings": ("elo_rating", "last_updated", "source", "team_name"),
    "engine_calibration": (
        "based_on_matches", "calibration_params", "created_at", "engine_name",
        "id", "is_active", "version",
    ),
    "match_fixtures": (
        "away_team", "fixture_id", "home_team", "kickoff_utc", "match_id", "stage",
    ),
    "match_predictions": (
        "away_win_prob", "confidence", "draw_prob", "home_win_prob",
        "last_updated", "match_id", "predicted_away_score", "predicted_home_score",
    ),
    "match_results": (
        "final_away_score", "final_home_score", "finished_at", "match_id", "outcome",
    ),
    "odds_cache": (
        "away_odds", "cached_at", "draw_odds", "home_odds", "match_key", "source",
    ),
    "prediction_accuracy": ("id", "matches_evaluated"),
    "prediction_history": (
        "away_win_prob", "confidence", "draw_prob", "home_win_prob", "id",
        "match_id", "predicted_away_score", "predicted_home_score", "timestamp",
    ),
    "team_market_values": (
        "avg_player_value", "num_players", "scraped_at", "team_name",
        "total_market_value",
    ),
    "team_sentiment": (
        "article_count", "confidence", "news_sentiment", "overall_sentiment",
        "reddit_sentiment", "scraped_at", "team_name",
    ),
}

# Indexed columns, per table. Losing one is a silent performance regression.
EXPECTED_INDEXED: dict[str, tuple[str, ...]] = {
    "ai_analysis_history": ("match_id",),
    "ai_optimized_predictions": ("match_id",),
    "engine_calibration": ("engine_name",),
    "match_fixtures": ("fixture_id", "kickoff_utc", "stage"),
    "prediction_history": ("match_id", "timestamp"),
    "team_market_values": ("scraped_at",),
    "team_sentiment": ("scraped_at",),
}


class TestWorldCupTableShape:
    def test_table_set_is_frozen(self) -> None:
        assert set(Base.metadata.tables) == set(EXPECTED_NOT_NULL)

    def test_not_null_columns_are_frozen(self) -> None:
        actual = {
            name: tuple(sorted(c.name for c in table.columns if not c.nullable))
            for name, table in Base.metadata.tables.items()
        }
        expected = {k: tuple(sorted(v)) for k, v in EXPECTED_NOT_NULL.items()}
        assert actual == expected

    def test_indexed_columns_are_frozen(self) -> None:
        actual = {}
        for name, table in Base.metadata.tables.items():
            cols = sorted(
                {c.name for index in table.indexes for c in index.columns}
            )
            if cols:
                actual[name] = tuple(cols)
        expected = {k: tuple(sorted(v)) for k, v in EXPECTED_INDEXED.items()}
        assert actual == expected

    def test_primary_keys_are_frozen(self) -> None:
        actual = {
            name: tuple(c.name for c in table.primary_key.columns)
            for name, table in Base.metadata.tables.items()
        }
        assert actual == {
            "ai_analysis_history": ("id",),
            "ai_optimized_predictions": ("id",),
            "elo_ratings": ("team_name",),
            "engine_calibration": ("id",),
            "match_fixtures": ("match_id",),
            "match_predictions": ("match_id",),
            "match_results": ("match_id",),
            "odds_cache": ("match_key",),
            "prediction_accuracy": ("id",),
            "prediction_history": ("id",),
            "team_market_values": ("team_name",),
            "team_sentiment": ("team_name",),
        }
