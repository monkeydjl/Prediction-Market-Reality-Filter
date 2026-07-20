# backend/app/kernel/multi_feature_builder.py
"""MultiFeatureBuilder — FeatureBuilder Protocol proxy with prefix dispatch.

Mirrors MultiAdapter's prefix-dispatch pattern. The PredictionKernel
sees a single FeatureBuilder; internally, calls are routed to the
correct sport-specific builder based on the match_id prefix.

Prefix mapping:
    "wc-", "ucl-", "epl-", ... → FootballFeatureBuilder
    "nba-"                     → BasketballFeatureBuilder

Unknown prefixes fall back to the default builder (first registered).
"""
from __future__ import annotations

from app.kernel.domain import SportIdentity, MatchIdentity, FeatureSet
from app.kernel.market_liquidity import enrich_feature_set_liquidity


class MultiFeatureBuilder:
    """FeatureBuilder Protocol proxy — dispatches by match_id prefix."""

    def __init__(self, builders: dict[str, object]) -> None:
        """Initialize with prefix-to-builder mapping.

        Args:
            builders: {prefix: builder} where prefix is a string like
                "wc-", "nba-". The first builder is used as the default
                for unknown prefixes.
        """
        self._builders = builders
        self._default = next(iter(builders.values()))

    def _select(self, match_id: str) -> object:
        """Select the builder for a given match_id by prefix."""
        for prefix, builder in self._builders.items():
            if match_id.startswith(prefix):
                return builder
        return self._default

    def sport(self) -> SportIdentity:
        return self._default.sport()

    def build(self, match: MatchIdentity, raw: dict) -> FeatureSet:
        features = self._select(match.match_id).build(match, raw)
        # P1-E4 feed: attach prediction-market liquidity when linked
        return enrich_feature_set_liquidity(features)
