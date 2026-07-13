# backend/app/kernel/feature_registry.py
"""Feature metadata registry."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FeatureDefinition:
    key: str
    category: str
    version: str
    description: str
    sport: str | None
    enabled: bool


class FeatureRegistry:
    """Registry for feature metadata.

    Engines and FeatureBuilders query the registry to discover available
    features, rather than hardcoding string keys.
    """

    def __init__(self) -> None:
        self._features: dict[str, FeatureDefinition] = {}

    def register(
        self, key: str, category: str, version: str,
        description: str, sport: str | None = None,
    ) -> None:
        self._features[key] = FeatureDefinition(
            key=key, category=category, version=version,
            description=description, sport=sport, enabled=True,
        )

    def get(self, key: str) -> FeatureDefinition | None:
        return self._features.get(key)

    def list_by_category(self, category: str) -> list[FeatureDefinition]:
        return [f for f in self._features.values() if f.category == category]

    def list_by_sport(self, sport: str) -> list[FeatureDefinition]:
        """List features available for a sport: universal (sport=None) + sport-specific."""
        return [
            f for f in self._features.values()
            if f.sport is None or f.sport == sport
        ]
