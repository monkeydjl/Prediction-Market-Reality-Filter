# backend/app/kernel/factor_registry.py
"""Factor weight and lifecycle management."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable

from app.kernel.kernel_db import get_kernel_session, KernelFactor


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
    """Manages factor weights per competition with DB persistence.

    Supports differentiated weights: e.g., the 'elo' factor can have
    weight 0.30 globally but 0.40 for EPL. Weights are persisted to the
    KernelFactor table and loaded on construction.
    """

    def __init__(self, session_factory: Callable | None = None) -> None:
        self._session_factory = session_factory or get_kernel_session
        # Key: (factor_id, competition | None) -> FactorConfig
        # competition=None means global default
        self._factors: dict[tuple[str, str | None], FactorConfig] = {}
        self._load_from_db()
        if not self._factors:
            self._init_default_factors()

    def _load_from_db(self) -> None:
        """Load all factors from KernelFactor table."""
        session = self._session_factory()
        try:
            rows = session.query(KernelFactor).all()
            for row in rows:
                key = (row.factor_id, row.competition)
                self._factors[key] = FactorConfig(
                    factor_id=row.factor_id,
                    category=row.category,
                    version=row.version,
                    # weight and source are nullable columns whose defaults are
                    # applied Python-side on insert, so a row written any other
                    # way can hold NULL. Fall back to those same defaults rather
                    # than putting None into a float/str field of FactorConfig.
                    weight=row.weight if row.weight is not None else 1.0,
                    competition=row.competition,
                    enabled=bool(row.enabled),
                    source=row.source or "manual",
                    updated_at=row.updated_at or datetime.now(timezone.utc),
                )
        finally:
            session.close()

    def reload_from_db(self) -> None:
        """Clear in-memory factors and reload from KernelFactor table."""
        self._factors.clear()
        self._load_from_db()
        if not self._factors:
            self._init_default_factors()

    def _init_default_factors(self) -> None:
        """Register elo (0.30) and odds (0.70) as global defaults if DB is empty."""
        now = datetime.now(timezone.utc)
        defaults = [
            FactorConfig("elo", "elo_rating", "1.0", 0.30, None, True, "default", now),
            FactorConfig("odds", "market_odds", "1.0", 0.70, None, True, "default", now),
        ]
        session = self._session_factory()
        try:
            for fc in defaults:
                row = KernelFactor(
                    factor_id=fc.factor_id, category=fc.category,
                    version=fc.version, weight=fc.weight,
                    competition=fc.competition, enabled=1,
                    source=fc.source, updated_at=now,
                )
                session.add(row)
                self._factors[(fc.factor_id, fc.competition)] = fc
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def register_factor(self, factor: FactorConfig) -> None:
        """Register a factor in memory (does not persist to DB)."""
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
        """Update weight in memory and persist to KernelFactor table."""
        key = (factor_id, competition)
        existing = self._factors.get(key)
        now = datetime.now(timezone.utc)

        if existing is not None:
            updated = replace(
                existing, weight=new_weight, source=source, updated_at=now,
            )
            self._factors[key] = updated
        else:
            # Try to get category from global default
            global_fc = self._factors.get((factor_id, None))
            category = global_fc.category if global_fc else "unknown"
            version = global_fc.version if global_fc else "1.0"
            self._factors[key] = FactorConfig(
                factor_id=factor_id, category=category, version=version,
                weight=new_weight, competition=competition,
                enabled=True, source=source, updated_at=now,
            )

        # Persist to DB
        session = self._session_factory()
        try:
            row = session.query(KernelFactor).filter_by(
                factor_id=factor_id, competition=competition,
            ).first()
            if row is not None:
                row.weight = new_weight
                row.source = source
                row.updated_at = now
            else:
                fc = self._factors[key]
                row = KernelFactor(
                    factor_id=fc.factor_id, category=fc.category,
                    version=fc.version, weight=new_weight,
                    competition=competition, enabled=1,
                    source=source, updated_at=now,
                )
                session.add(row)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_active(self, competition: str) -> list[FactorConfig]:
        """List active factors for a competition (global + competition-specific).

        Competition-specific entries take priority over global ones so a
        global factor registered after a competition-specific one does not
        overwrite the more specific weight.
        """
        result: dict[str, FactorConfig] = {}
        for (fid, comp), factor in self._factors.items():
            if not factor.enabled:
                continue
            if comp == competition:
                result[fid] = factor  # competition-specific always wins
            elif comp is None and fid not in result:
                result[fid] = factor  # global only if no competition-specific yet
        return list(result.values())

    # Extra football multi-factor seeds only (form/rest/injury/h2h).
    # Does NOT seed competition-specific elo/odds so EloOddsEngine keeps
    # global 0.30 / 0.70. FootballMultiFactorEngine uses its own defaults
    # for elo/odds and these seeds for the situational factors.
    # Soft multi-factor seeds only (no competition-specific elo/odds).
    # Weights are registry starting points for learning; FootballMultiFactorEngine
    # still prefers its own profile/default for elo/odds and missing keys.
    _FOOTBALL_MULTI_FACTOR_DEFAULTS: list[tuple[str, str, float]] = [
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

    _FOOTBALL_COMPETITIONS = frozenset({
        "wc", "world_cup", "ucl", "epl", "laliga",
        "bundesliga", "seriea", "serie_a", "ligue1", "ligue_1",
    })

    @staticmethod
    def _norm_competition(competition: str) -> str:
        c = (competition or "").lower().strip()
        aliases = {
            "seriea": "serie_a",
            "serie_a": "serie_a",
            "ligue1": "ligue_1",
            "ligue_1": "ligue_1",
            "worldcup": "world_cup",
        }
        return aliases.get(c, c)

    def get_competition_weight(
        self, factor_id: str, competition: str,
    ) -> float | None:
        """Return competition-specific weight only; None if not set/disabled.

        Unlike get_weight(), does not fall back to global defaults. Used by
        FootballMultiFactorEngine so global elo=0.30 / odds=0.70 are not
        mistaken for multi-factor weights.
        """
        competition = self._norm_competition(competition)
        fc = self._factors.get((factor_id, competition))
        if fc is not None and fc.enabled:
            return fc.weight
        return None

    def ensure_competition_factors(self, competition: str) -> None:
        """Seed default factors for a competition if none exist.

        For "nba": seeds elo(0.45), home_court(0.15), rest(0.15), form(0.25).
        For football competitions (wc/world_cup/ucl/epl/...): seeds only
        multi-factor softs (form/rest/.../altitude) so EloOdds global elo/odds stay intact.
        For unknown competitions: no-op (returns immediately).
        """
        competition = self._norm_competition(competition)

        if competition == "nba":
            defaults = [
                ("elo", "elo_rating", 0.35),
                ("home_court", "home_advantage", 0.12),
                ("rest", "rest_days", 0.12),
                ("form", "recent_form", 0.18),
                ("net_rating", "net_rating", 0.10),
                ("travel", "travel_timezone", 0.07),
                ("injury", "injury_impact", 0.06),
            ]
        elif competition == "mlb":
            defaults = [
                ("elo", "elo_rating", 0.26),
                ("home_court", "home_advantage", 0.10),
                ("rest", "rest_days", 0.08),
                ("form", "recent_form", 0.11),
                ("starting_pitcher", "pitcher_matchup", 0.20),
                ("park", "park_factor", 0.07),
                ("bullpen", "bullpen", 0.07),
                ("weather", "weather", 0.06),
                ("platoon", "platoon_split", 0.05),
            ]
        elif competition == "nhl":
            defaults = [
                ("elo", "elo_rating", 0.30),
                ("home_court", "home_advantage", 0.13),
                ("rest", "rest_days", 0.12),
                ("form", "recent_form", 0.17),
                ("goalie", "goalie_matchup", 0.14),
                ("travel", "travel_timezone", 0.07),
                ("attack_share", "attack_share", 0.07),
            ]
        elif competition in self._FOOTBALL_COMPETITIONS:
            defaults = list(self._FOOTBALL_MULTI_FACTOR_DEFAULTS)
        else:
            return  # Unknown competition — no defaults

        # Merge missing soft seeds only — never overwrite existing weights
        missing = [
            (fid, cat, w)
            for fid, cat, w in defaults
            if (fid, competition) not in self._factors
        ]
        if not missing:
            return

        now = datetime.now(timezone.utc)
        session = self._session_factory()
        try:
            for factor_id, category, weight in missing:
                fc = FactorConfig(
                    factor_id=factor_id, category=category,
                    version="1.0", weight=weight,
                    competition=competition, enabled=True,
                    source="default", updated_at=now,
                )
                self._factors[(factor_id, competition)] = fc
                row = KernelFactor(
                    factor_id=fc.factor_id, category=fc.category,
                    version=fc.version, weight=fc.weight,
                    competition=fc.competition, enabled=1,
                    source=fc.source, updated_at=now,
                )
                session.add(row)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
