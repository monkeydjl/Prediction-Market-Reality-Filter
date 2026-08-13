"""Freeze the NOT NULL / index / unique-constraint shape of the kernel_ tables.

Sibling of tests/test_world_cup_prediction_model_shape.py; same hazard.
``app/kernel/kernel_db.py`` uses SQLAlchemy 2.0 ``Mapped[T]`` /
``mapped_column()``, where nullability follows the annotation, and the two
spellings disagree by default: ``Column(String)`` is nullable,
``mapped_column()`` under a non-Optional ``Mapped[str]`` is not. Dropping a
``| None`` therefore emits different DDL, and nothing else would notice —
``init_kernel_db`` calls ``metadata.create_all``, so a fresh test database just
adopts whatever shape the models declare while an existing
``kernel_predictions.db`` keeps the old one and starts rejecting writes.

Unique constraints are asserted too because several stores rely on them for
idempotency (``ON CONFLICT``-style upserts in the market-link, settlement and
traditional-odds paths); losing one turns a retry into duplicate rows rather
than an error.
"""

from app.kernel.kernel_db import KernelBase

# Regenerate deliberately (and say why in the commit) if a column's nullability
# is genuinely meant to change — the tables are never migrated.
EXPECTED_NOT_NULL: dict[str, tuple[str, ...]] = {
    "kernel_calibration": (
        "avg_accuracy", "avg_confidence", "competition", "engine", "id",
        "intercept", "last_updated", "sample_count", "slope",
    ),
    "kernel_club_elo_cache": ("elo_rating", "fetched_at", "team_name"),
    "kernel_elo_ratings": (
        "competition", "elo_rating", "sport", "team_name", "updated_at",
    ),
    "kernel_engine_scores": ("engine", "id"),
    "kernel_factors": ("category", "factor_id", "id", "version"),
    "kernel_futures_links": (
        "competition", "contract_id", "id", "season", "source", "team",
    ),
    "kernel_futures_snapshots": ("captured_at", "id", "implied_prob", "link_id"),
    "kernel_market_calibrations": (
        "avg_brier", "avg_signed_error", "competition", "direction_accuracy",
        "engine", "id", "intercept", "last_updated", "sample_count", "slope",
    ),
    "kernel_market_settlements": (
        "competition", "engine", "id", "mapped_outcome", "match_finished_at",
        "match_id", "processed_at", "status",
    ),
    "kernel_market_snapshots": ("id", "implied_prob", "link_id"),
    "kernel_match_fixtures": (
        "away_team", "competition", "home_team", "match_id", "season",
    ),
    "kernel_match_outcomes": ("match_id",),
    "kernel_match_results": ("match_id",),
    "kernel_optimized_params": (
        "accuracy", "brier_score", "competition", "elo_params", "factor_weights",
        "id", "mae", "sample_count", "score", "sport",
    ),
    "kernel_prediction_history": ("engine", "id", "match_id"),
    "kernel_predictions": (
        "competition", "confidence", "engine", "feature_version", "match_id",
        "outcome_probabilities", "predicted_scores", "season", "sport",
    ),
    "kernel_sport_edges": (
        "adjusted_edge", "captured_at", "id", "liquidity_factor",
        "mapped_outcome", "market_prob", "match_id", "model_prob", "raw_edge",
        "sources_count", "stale", "trust",
    ),
    "kernel_sport_market_links": (
        "contract_id", "id", "implied_prob", "link_confidence", "link_method",
        "mapped_outcome", "match_id", "outcome_label", "source", "verified",
    ),
    "kernel_traditional_odds_snapshots": (
        "captured_at", "competition", "decimal_odds", "id", "implied_prob",
        "mapped_outcome", "match_id",
    ),
}

EXPECTED_INDEXES: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "kernel_futures_links": (
        ("ix_kernel_futures_links_competition", ("competition",)),
        ("ix_kernel_futures_links_season", ("season",)),
    ),
    "kernel_futures_snapshots": (("ix_futures_snapshots_link_id", ("link_id",)),),
    "kernel_market_settlements": (
        ("ix_kernel_market_settlements_match_id", ("match_id",)),
    ),
    "kernel_market_snapshots": (
        ("ix_kernel_market_snapshots_link_id", ("link_id",)),
    ),
    "kernel_optimized_params": (
        ("ix_kernel_optimized_params_competition", ("competition",)),
        ("ix_kernel_optimized_params_sport", ("sport",)),
    ),
    "kernel_sport_edges": (
        ("ix_kernel_sport_edges_match_id", ("match_id",)),
        (
            "ix_kernel_sport_edges_match_outcome_captured",
            ("match_id", "mapped_outcome", "captured_at"),
        ),
    ),
    "kernel_sport_market_links": (
        ("ix_kernel_sport_market_links_contract_id", ("contract_id",)),
        ("ix_kernel_sport_market_links_match_id", ("match_id",)),
        ("ix_kernel_sport_market_links_verified", ("verified",)),
    ),
    "kernel_traditional_odds_snapshots": (
        ("ix_kernel_traditional_odds_snapshots_match_id", ("match_id",)),
    ),
}

EXPECTED_UNIQUE: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "kernel_calibration": (
        ("uq_calibration_engine_competition", ("engine", "competition")),
    ),
    "kernel_factors": (("uq_factor_id_competition", ("factor_id", "competition")),),
    "kernel_futures_links": (
        (
            "uq_futures_links_comp_season_team_source",
            ("competition", "season", "team", "source"),
        ),
    ),
    "kernel_market_calibrations": (
        ("uq_market_calibration_engine_competition", ("engine", "competition")),
    ),
    "kernel_market_settlements": (
        ("uq_market_settlement_match_outcome", ("match_id", "mapped_outcome")),
    ),
    "kernel_sport_market_links": (
        ("uq_sport_market_link", ("match_id", "contract_id", "outcome_label")),
    ),
    "kernel_traditional_odds_snapshots": (
        (
            "uq_traditional_odds_match_outcome_time",
            ("match_id", "mapped_outcome", "captured_at"),
        ),
    ),
}

EXPECTED_PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "kernel_calibration": ("id",),
    "kernel_club_elo_cache": ("team_name",),
    "kernel_elo_ratings": ("team_name",),
    "kernel_engine_scores": ("id",),
    "kernel_factors": ("id",),
    "kernel_futures_links": ("id",),
    "kernel_futures_snapshots": ("id",),
    "kernel_market_calibrations": ("id",),
    "kernel_market_settlements": ("id",),
    "kernel_market_snapshots": ("id",),
    "kernel_match_fixtures": ("match_id",),
    "kernel_match_outcomes": ("match_id",),
    "kernel_match_results": ("match_id",),
    "kernel_optimized_params": ("id",),
    "kernel_prediction_history": ("id",),
    "kernel_predictions": ("match_id",),
    "kernel_sport_edges": ("id",),
    "kernel_sport_market_links": ("id",),
    "kernel_traditional_odds_snapshots": ("id",),
}


class TestKernelTableShape:
    def test_table_set_is_frozen(self) -> None:
        assert set(KernelBase.metadata.tables) == set(EXPECTED_NOT_NULL)

    def test_every_table_keeps_the_kernel_prefix(self) -> None:
        # Kernel tables live in their own DB and must not collide with the
        # world-cup schema; the prefix is the convention that guarantees it.
        assert all(name.startswith("kernel_") for name in KernelBase.metadata.tables)

    def test_not_null_columns_are_frozen(self) -> None:
        actual = {
            name: tuple(sorted(c.name for c in table.columns if not c.nullable))
            for name, table in KernelBase.metadata.tables.items()
        }
        assert actual == {k: tuple(sorted(v)) for k, v in EXPECTED_NOT_NULL.items()}

    def test_indexes_are_frozen(self) -> None:
        actual = {}
        for name, table in KernelBase.metadata.tables.items():
            found = tuple(sorted(
                (index.name or "", tuple(c.name for c in index.columns))
                for index in table.indexes
            ))
            if found:
                actual[name] = found
        assert actual == {
            k: tuple(sorted(v)) for k, v in EXPECTED_INDEXES.items()
        }

    def test_unique_constraints_are_frozen(self) -> None:
        actual = {}
        for name, table in KernelBase.metadata.tables.items():
            found = tuple(sorted(
                (constraint.name or "", tuple(c.name for c in constraint.columns))
                for constraint in table.constraints
                if type(constraint).__name__ == "UniqueConstraint"
            ))
            if found:
                actual[name] = found
        assert actual == {k: tuple(sorted(v)) for k, v in EXPECTED_UNIQUE.items()}

    def test_primary_keys_are_frozen(self) -> None:
        actual = {
            name: tuple(c.name for c in table.primary_key.columns)
            for name, table in KernelBase.metadata.tables.items()
        }
        assert actual == EXPECTED_PRIMARY_KEYS
