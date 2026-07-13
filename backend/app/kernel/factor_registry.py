# backend/app/kernel/factor_registry.py
"""Factor weight and lifecycle management."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone


@dataclass(frozen=True)
class FactorConfig:
    factor_id: str
    category: str
    version: str
    weight: float
    competition: str | None
    enabled: bool
    source: str
    updated_at: datetime


class FactorRegistry:
    """Manages factor weights per competition.

    Supports differentiated weights: e.g., the 'elo' factor can have
    weight 0.30 globally but 0.40 for EPL.
    """

    def __init__(self) -> None:
        # Key: (factor_id, competition) -> FactorConfig
        # competition=None means global default
        self._factors: dict[tuple[str, str | None], FactorConfig] = {}

    def register_factor(self, factor: FactorConfig) -> None:
        key = (factor.factor_id, factor.competition)
        self._factors[key] = factor

    def get_weight(self, factor_id: str, competition: str) -> float:
        """Get weight for a factor in a competition.

        Falls back to global (competition=None) if no competition-specific
        weight exists. Returns 1.0 as default if factor is unknown.
        """
        comp_factor = self._factors.get((factor_id, competition))
        if comp_factor is not None and comp_factor.enabled:
            return comp_factor.weight
        global_factor = self._factors.get((factor_id, None))
        if global_factor is not None and global_factor.enabled:
            return global_factor.weight
        return 1.0

    def update_weight(
        self, factor_id: str, competition: str,
        new_weight: float, source: str,
    ) -> None:
        key = (factor_id, competition)
        existing = self._factors.get(key)
        if existing is not None:
            updated = replace(
                existing,
                weight=new_weight,
                source=source,
                updated_at=datetime.now(timezone.utc),
            )
            self._factors[key] = updated
        else:
            self._factors[key] = FactorConfig(
                factor_id=factor_id, category="unknown", version="1.0",
                weight=new_weight, competition=competition,
                enabled=True, source=source,
                updated_at=datetime.now(timezone.utc),
            )

    def list_active(self, competition: str) -> list[FactorConfig]:
        """List active factors for a competition (global + competition-specific)."""
        result: dict[str, FactorConfig] = {}
        for (fid, comp), factor in self._factors.items():
            if not factor.enabled:
                continue
            if comp is None:
                result[fid] = factor
            elif comp == competition:
                result[fid] = factor
        return list(result.values())
