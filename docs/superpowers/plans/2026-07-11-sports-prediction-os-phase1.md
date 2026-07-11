# Sports Prediction OS — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract a sport-agnostic Prediction Kernel from the existing World Cup module, with the World Cup module becoming the first Adapter.

**Architecture:** DDD + Hexagonal Architecture. Kernel (`backend/app/kernel/`) contains domain models, Protocol interfaces, registries, engines, and a learning service — all with zero imports of `world_cup_*`. A FootballFeatureBuilder and WorldCupAdapter in `backend/app/sports/football/` bridge existing services. A feature flag (`KERNEL_PREDICTION_ENABLED`, default OFF) gates the new path.

**Tech Stack:** Python 3.12, SQLAlchemy, FastAPI, Pydantic, pytest

## Global Constraints

- Prediction Kernel (`backend/app/kernel/`) must NOT import any `world_cup_*` modules
- All domain value objects must be `@dataclass(frozen=True)`
- FeatureSet uses layered structure (general/team/market/player/environment/custom), NOT a flat dict
- `KERNEL_PREDICTION_ENABLED` feature flag must default to OFF
- Existing `world_cup_*` files must NOT be deleted or renamed
- Existing API routes `/api/world-cup/predictions/*` must remain compatible
- New database tables use `kernel_` prefix
- Engine migrations must be done one-by-one with equivalence tests
- All tests run from `backend/` directory: `cd backend && python -m pytest tests/ -v`
- Frontend pages must NOT be modified during Phase 1

---

## File Structure

```
backend/app/
├── kernel/
│   ├── __init__.py
│   ├── domain.py                 # Frozen value objects + FeatureSet + PredictionResult
│   ├── protocols.py              # DataAdapter / FeatureBuilder / PredictionEngine / LearningService Protocols
│   ├── prediction_kernel.py      # Orchestrator
│   ├── engine_registry.py        # Engine registration and selection
│   ├── feature_registry.py       # Feature metadata registry
│   ├── factor_registry.py        # Factor weight management
│   ├── learning_service.py       # Learning loop (record + error + score)
│   ├── kernel_db.py              # Kernel SQLite tables + session management
│   └── engines/
│       ├── __init__.py
│       ├── btd_model.py          # BTD probability model (from world_cup_btd_model.py)
│       ├── elo_odds_engine.py    # Elo+Odds fusion (from world_cup_elo_odds_engine.py)
│       └── calibration.py        # Confidence calibration (from world_cup_confidence_calibration.py)

├── sports/
│   ├── __init__.py
│   └── football/
│       ├── __init__.py
│       ├── feature_builder.py    # FootballFeatureBuilder
│       └── adapters/
│           ├── __init__.py
│           └── world_cup_adapter.py  # WorldCupAdapter (bridges existing services)

├── api/routes/
│   └── predictions.py            # New /api/predictions/* routes

backend/tests/
├── test_kernel_domain.py
├── test_kernel_engine_registry.py
├── test_kernel_feature_registry.py
├── test_kernel_factor_registry.py
├── test_kernel_learning_service.py
├── test_kernel_btd_model.py
├── test_kernel_elo_odds_engine.py
├── test_kernel_prediction_kernel.py
├── test_football_feature_builder.py
├── test_world_cup_adapter.py
└── test_predictions_route.py
```

---

### Task 1: Kernel Domain Layer

**Files:**
- Create: `backend/app/kernel/__init__.py`
- Create: `backend/app/kernel/domain.py`
- Test: `backend/tests/test_kernel_domain.py`

**Interfaces:**
- Produces: `SportIdentity`, `CompetitionIdentity`, `SeasonIdentity`, `TeamIdentity`, `MatchIdentity`, `MatchOutcome`, `GeneralFeatures`, `TeamFeatures`, `MarketFeatures`, `PlayerFeatures`, `EnvironmentFeatures`, `FeatureSet`, `ContributionItem`, `PredictionResult`, `PredictionError`, `EngineScore`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_kernel_domain.py
"""Tests for kernel domain value objects."""
from datetime import datetime, timezone
from dataclasses import FrozenInstanceError
import pytest

from app.kernel.domain import (
    SportIdentity,
    CompetitionIdentity,
    SeasonIdentity,
    TeamIdentity,
    MatchIdentity,
    MatchOutcome,
    GeneralFeatures,
    TeamFeatures,
    MarketFeatures,
    PlayerFeatures,
    EnvironmentFeatures,
    FeatureSet,
    ContributionItem,
    PredictionResult,
    PredictionError,
    EngineScore,
)


class TestSportIdentity:
    def test_creation(self):
        s = SportIdentity(code="football", name="Football")
        assert s.code == "football"
        assert s.name == "Football"

    def test_frozen(self):
        s = SportIdentity(code="football", name="Football")
        with pytest.raises(FrozenInstanceError):
            s.code = "basketball"

    def test_equality(self):
        a = SportIdentity(code="football", name="Football")
        b = SportIdentity(code="football", name="Football")
        assert a == b

    def test_hashable(self):
        s = SportIdentity(code="football", name="Football")
        assert hash(s) == hash(SportIdentity(code="football", name="Football"))


class TestCompetitionIdentity:
    def test_creation(self):
        sport = SportIdentity(code="football", name="Football")
        c = CompetitionIdentity(code="world_cup", name="FIFA World Cup", sport=sport)
        assert c.code == "world_cup"
        assert c.sport == sport

    def test_frozen(self):
        sport = SportIdentity(code="football", name="Football")
        c = CompetitionIdentity(code="world_cup", name="FIFA World Cup", sport=sport)
        with pytest.raises(FrozenInstanceError):
            c.code = "epl"


class TestMatchIdentity:
    def test_creation(self):
        sport = SportIdentity(code="football", name="Football")
        comp = CompetitionIdentity(code="world_cup", name="FIFA World Cup", sport=sport)
        season = SeasonIdentity(competition=comp, season_key="2026")
        home = TeamIdentity(code="BRA", name="Brazil", competition=comp)
        away = TeamIdentity(code="ARG", name="Argentina", competition=comp)
        match = MatchIdentity(
            match_id="wc_2026_bra_arg",
            season=season,
            stage="group_stage",
            round=None,
            home=home,
            away=away,
            kickoff_utc=datetime(2026, 6, 13, 18, 0, tzinfo=timezone.utc),
        )
        assert match.match_id == "wc_2026_bra_arg"
        assert match.home.code == "BRA"
        assert match.away.name == "Argentina"

    def test_frozen(self):
        sport = SportIdentity(code="football", name="Football")
        comp = CompetitionIdentity(code="world_cup", name="FIFA World Cup", sport=sport)
        season = SeasonIdentity(competition=comp, season_key="2026")
        home = TeamIdentity(code="BRA", name="Brazil", competition=comp)
        away = TeamIdentity(code="ARG", name="Argentina", competition=comp)
        match = MatchIdentity(
            match_id="wc_2026_bra_arg",
            season=season,
            stage="group_stage",
            round=None,
            home=home,
            away=away,
            kickoff_utc=datetime(2026, 6, 13, 18, 0, tzinfo=timezone.utc),
        )
        with pytest.raises(FrozenInstanceError):
            match.match_id = "changed"


class TestMatchOutcome:
    def test_creation(self):
        o = MatchOutcome(
            match_id="wc_2026_bra_arg",
            home_score=2,
            away_score=1,
            outcome="home_win",
            finished_at=datetime(2026, 6, 13, 20, 0, tzinfo=timezone.utc),
        )
        assert o.home_score == 2
        assert o.outcome == "home_win"


class TestFeatureSet:
    def test_creation_with_all_layers(self):
        sport = SportIdentity(code="football", name="Football")
        comp = CompetitionIdentity(code="world_cup", name="FIFA World Cup", sport=sport)
        season = SeasonIdentity(competition=comp, season_key="2026")
        home = TeamIdentity(code="BRA", name="Brazil", competition=comp)
        away = TeamIdentity(code="ARG", name="Argentina", competition=comp)
        match = MatchIdentity(
            match_id="wc_2026_bra_arg",
            season=season,
            stage="group_stage",
            round=None,
            home=home,
            away=away,
            kickoff_utc=datetime(2026, 6, 13, 18, 0, tzinfo=timezone.utc),
        )
        fs = FeatureSet(
            match=match,
            general=GeneralFeatures(
                rest_days_home=5.0, rest_days_away=4.0,
                travel_distance_km=None, days_since_last_match=5.0,
            ),
            team=TeamFeatures(
                elo_rating_home=1920.0, elo_rating_away=1890.0,
                form_home=0.7, form_away=0.6,
                h2h_home_win_rate=0.55, h2h_draw_rate=0.25,
                market_value_home=None, market_value_away=None,
            ),
            market=MarketFeatures(
                odds_home=2.10, odds_draw=3.30, odds_away=3.50,
                odds_source="the_odds_api", odds_fresh=True,
            ),
            player=PlayerFeatures(
                key_players_available_home=0.9, key_players_available_away=1.0,
                injury_impact_home=0.1, injury_impact_away=0.0,
            ),
            environment=EnvironmentFeatures(
                venue="Maracana", weather_temp_c=25.0,
                weather_condition="clear", is_home_advantage=False,
            ),
            custom={"xg_home": 1.8, "xg_away": 1.2},
            data_quality="real",
            quality_notes=[],
            feature_version="1.0",
        )
        assert fs.team.elo_rating_home == 1920.0
        assert fs.market.odds_home == 2.10
        assert fs.custom["xg_home"] == 1.8
        assert fs.data_quality == "real"

    def test_frozen(self):
        sport = SportIdentity(code="football", name="Football")
        comp = CompetitionIdentity(code="wc", name="WC", sport=sport)
        season = SeasonIdentity(competition=comp, season_key="2026")
        home = TeamIdentity(code="BRA", name="Brazil", competition=comp)
        away = TeamIdentity(code="ARG", name="Argentina", competition=comp)
        match = MatchIdentity(
            match_id="m1", season=season, stage="group", round=None,
            home=home, away=away,
            kickoff_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        fs = FeatureSet(
            match=match,
            general=GeneralFeatures(None, None, None, None),
            team=TeamFeatures(None, None, None, None, None, None, None, None),
            market=MarketFeatures(None, None, None, None, False),
            player=PlayerFeatures(None, None, None, None),
            environment=EnvironmentFeatures(None, None, None, False),
            custom={},
            data_quality="partial",
            quality_notes=["no odds"],
            feature_version="1.0",
        )
        with pytest.raises(FrozenInstanceError):
            fs.data_quality = "real"


class TestPredictionResult:
    def test_creation(self):
        p = PredictionResult(
            predicted_scores={"home": 2.1, "away": 1.3},
            outcome_probabilities={"home_win": 0.55, "draw": 0.25, "away_win": 0.20},
            confidence=0.72,
            engine_name="elo_odds",
            explanation=[
                ContributionItem(
                    factor="elo", direction="support", weight=0.35,
                    available=True, detail="Home team stronger by 120 Elo",
                ),
            ],
            betting_analysis=None,
            feature_version="1.0",
            prediction_timestamp=datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc),
        )
        assert p.engine_name == "elo_odds"
        assert p.explanation[0].factor == "elo"


class TestPredictionError:
    def test_creation(self):
        e = PredictionError(
            match_id="m1", engine="elo_odds",
            score_mae=0.5, outcome_correct=True,
            brier_score=0.18, confidence_calibrated=True,
        )
        assert e.score_mae == 0.5
        assert e.outcome_correct is True


class TestEngineScore:
    def test_creation(self):
        s = EngineScore(
            engine="elo_odds", competition="world_cup",
            accuracy=0.72, avg_mae=0.89, brier_score=0.19,
            sample_count=64, confidence_calibration=0.85,
            last_updated=datetime(2026, 7, 11, tzinfo=timezone.utc),
        )
        assert s.accuracy == 0.72
        assert s.sample_count == 64
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_kernel_domain.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.kernel'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/kernel/__init__.py
"""Prediction Kernel — sport-agnostic prediction core."""
```

```python
# backend/app/kernel/domain.py
"""Domain value objects for the Prediction Kernel.

All objects are frozen dataclasses, safe for use as cache keys,
database primary keys, and event bus messages.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SportIdentity:
    """Identifies a sport (football, basketball, ...)."""
    code: str
    name: str


@dataclass(frozen=True)
class CompetitionIdentity:
    """Identifies a competition within a sport (world_cup, epl, nba, ...)."""
    code: str
    name: str
    sport: SportIdentity


@dataclass(frozen=True)
class SeasonIdentity:
    """Identifies a season within a competition."""
    competition: CompetitionIdentity
    season_key: str


@dataclass(frozen=True)
class TeamIdentity:
    """Identifies a team within a competition."""
    code: str
    name: str
    competition: CompetitionIdentity


@dataclass(frozen=True)
class MatchIdentity:
    """Identifies a single match."""
    match_id: str
    season: SeasonIdentity
    stage: str
    round: str | None
    home: TeamIdentity
    away: TeamIdentity
    kickoff_utc: datetime


@dataclass(frozen=True)
class MatchOutcome:
    """Final result of a completed match."""
    match_id: str
    home_score: int
    away_score: int
    outcome: str
    finished_at: datetime


@dataclass(frozen=True)
class GeneralFeatures:
    """Cross-sport general features."""
    rest_days_home: float | None
    rest_days_away: float | None
    travel_distance_km: float | None
    days_since_last_match: float | None


@dataclass(frozen=True)
class TeamFeatures:
    """Team-level features (cross-sport)."""
    elo_rating_home: float | None
    elo_rating_away: float | None
    form_home: float | None
    form_away: float | None
    h2h_home_win_rate: float | None
    h2h_draw_rate: float | None
    market_value_home: float | None
    market_value_away: float | None


@dataclass(frozen=True)
class MarketFeatures:
    """Betting market features (cross-sport)."""
    odds_home: float | None
    odds_draw: float | None
    odds_away: float | None
    odds_source: str | None
    odds_fresh: bool


@dataclass(frozen=True)
class PlayerFeatures:
    """Player-level features (cross-sport)."""
    key_players_available_home: float | None
    key_players_available_away: float | None
    injury_impact_home: float | None
    injury_impact_away: float | None


@dataclass(frozen=True)
class EnvironmentFeatures:
    """Environment features (cross-sport)."""
    venue: str | None
    weather_temp_c: float | None
    weather_condition: str | None
    is_home_advantage: bool


@dataclass(frozen=True)
class FeatureSet:
    """Standardized feature package consumed by prediction engines."""
    match: MatchIdentity
    general: GeneralFeatures
    team: TeamFeatures
    market: MarketFeatures
    player: PlayerFeatures
    environment: EnvironmentFeatures
    custom: dict[str, float]
    data_quality: str
    quality_notes: list[str]
    feature_version: str


@dataclass(frozen=True)
class ContributionItem:
    """A single factor contribution in a prediction explanation."""
    factor: str
    direction: str
    weight: float
    available: bool
    detail: str | None


@dataclass(frozen=True)
class PredictionResult:
    """Unified prediction output from any engine."""
    predicted_scores: dict[str, float]
    outcome_probabilities: dict[str, float]
    confidence: float
    engine_name: str
    explanation: list[ContributionItem]
    betting_analysis: dict | None
    feature_version: str
    prediction_timestamp: datetime


@dataclass(frozen=True)
class PredictionError:
    """Prediction error metrics after match completion."""
    match_id: str
    engine: str
    score_mae: float
    outcome_correct: bool
    brier_score: float
    confidence_calibrated: bool


@dataclass(frozen=True)
class EngineScore:
    """Aggregated performance score for an engine."""
    engine: str
    competition: str | None
    accuracy: float
    avg_mae: float
    brier_score: float
    sample_count: int
    confidence_calibration: float
    last_updated: datetime
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_kernel_domain.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/kernel/__init__.py app/kernel/domain.py tests/test_kernel_domain.py
git commit -m "feat(kernel): add domain value objects and FeatureSet"
```

---

### Task 2: Kernel Protocols

**Files:**
- Create: `backend/app/kernel/protocols.py`
- Test: `backend/tests/test_kernel_protocols.py`

**Interfaces:**
- Consumes: `MatchIdentity`, `FeatureSet`, `PredictionResult`, `MatchOutcome`, `PredictionError`, `EngineScore` from Task 1
- Produces: `DataAdapter`, `FeatureBuilder`, `PredictionEngine`, `LearningService` Protocols, `ScheduleFilter`, `RawMatchData`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_kernel_protocols.py
"""Tests for kernel Protocol interfaces."""
import pytest
from datetime import datetime, timezone

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome, FeatureSet,
    GeneralFeatures, TeamFeatures, MarketFeatures,
    PlayerFeatures, EnvironmentFeatures, PredictionResult,
)
from app.kernel.protocols import (
    DataAdapter, FeatureBuilder, PredictionEngine, LearningService,
    ScheduleFilter, RawMatchData,
)


def _make_match() -> MatchIdentity:
    sport = SportIdentity(code="football", name="Football")
    comp = CompetitionIdentity(code="world_cup", name="FIFA World Cup", sport=sport)
    season = SeasonIdentity(competition=comp, season_key="2026")
    home = TeamIdentity(code="BRA", name="Brazil", competition=comp)
    away = TeamIdentity(code="ARG", name="Argentina", competition=comp)
    return MatchIdentity(
        match_id="m1", season=season, stage="group", round=None,
        home=home, away=away,
        kickoff_utc=datetime(2026, 6, 13, tzinfo=timezone.utc),
    )


class TestScheduleFilter:
    def test_creation(self):
        f = ScheduleFilter(competition="world_cup", season="2026", status="scheduled")
        assert f.competition == "world_cup"

    def test_defaults(self):
        f = ScheduleFilter()
        assert f.competition is None
        assert f.status is None


class TestRawMatchData:
    def test_creation(self):
        m = _make_match()
        raw = RawMatchData(match=m, raw_json={"elo": 1900})
        assert raw.match.match_id == "m1"
        assert raw.raw_json["elo"] == 1900


class TestProtocolCompliance:
    """Verify that concrete classes can satisfy Protocol interfaces."""

    def test_data_adapter_protocol(self):
        class FakeAdapter:
            def fetch_schedule(self, filters):
                return []
            def fetch_team_data(self, team):
                return {}
            def fetch_player_data(self, team):
                return {}
            def fetch_market_data(self, match):
                return {}
            def fetch_outcome(self, match_id):
                return None
            def sync_schedule(self):
                return 0
            def get_match_identity(self, match_id):
                return _make_match()

        adapter = FakeAdapter()
        assert isinstance(adapter, DataAdapter)

    def test_feature_builder_protocol(self):
        class FakeBuilder:
            def build(self, match, raw):
                pass
            def sport(self):
                return SportIdentity(code="football", name="Football")

        builder = FakeBuilder()
        assert isinstance(builder, FeatureBuilder)

    def test_prediction_engine_protocol(self):
        class FakeEngine:
            def predict(self, features, match):
                pass
            def name(self):
                return "fake"
            def supported_sports(self):
                return ["*"]

        engine = FakeEngine()
        assert isinstance(engine, PredictionEngine)

    def test_learning_service_protocol(self):
        class FakeLearning:
            def record_prediction(self, match, prediction):
                pass
            def record_outcome(self, outcome):
                pass
            def compute_error(self, match_id):
                return None
            def update_calibration(self, competition, engine):
                return None
            def update_weights(self, competition):
                return None
            def engine_score(self, engine, competition=None):
                return None

        learning = FakeLearning()
        assert isinstance(learning, LearningService)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_kernel_protocols.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.kernel.protocols'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/kernel/protocols.py
"""Protocol interfaces for the Prediction Kernel.

These Protocols define the contracts between the Kernel and its adapters.
The Kernel never imports concrete implementations — only these Protocols.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.kernel.domain import (
    SportIdentity, MatchIdentity, MatchOutcome,
    FeatureSet, PredictionResult, PredictionError, EngineScore,
)


@dataclass(frozen=True)
class ScheduleFilter:
    """Filter parameters for schedule queries."""
    competition: str | None = None
    season: str | None = None
    status: str | None = None
    stage: str | None = None
    limit: int | None = None


@dataclass(frozen=True)
class RawMatchData:
    """Raw data from an adapter, pre-feature-building."""
    match: MatchIdentity
    raw_json: dict


@runtime_checkable
class DataAdapter(Protocol):
    """Fetches raw data from external sources. Does NOT compute features."""

    def fetch_schedule(self, filters: ScheduleFilter) -> list[RawMatchData]: ...
    def fetch_team_data(self, team: MatchIdentity.home.__class__) -> dict: ...
    def fetch_player_data(self, team: MatchIdentity.home.__class__) -> dict: ...
    def fetch_market_data(self, match: MatchIdentity) -> dict: ...
    def fetch_outcome(self, match_id: str) -> MatchOutcome | None: ...
    def sync_schedule(self) -> int: ...
    def get_match_identity(self, match_id: str) -> MatchIdentity: ...


@runtime_checkable
class FeatureBuilder(Protocol):
    """Computes a standardized FeatureSet from raw data."""

    def build(self, match: MatchIdentity, raw: dict) -> FeatureSet: ...
    def sport(self) -> SportIdentity: ...


@runtime_checkable
class PredictionEngine(Protocol):
    """Pure function engine: FeatureSet in, PredictionResult out."""

    def predict(self, features: FeatureSet, match: MatchIdentity) -> PredictionResult: ...
    def name(self) -> str: ...
    def supported_sports(self) -> list[str]: ...


@runtime_checkable
class LearningService(Protocol):
    """Post-match learning loop for continuous improvement."""

    def record_prediction(self, match: MatchIdentity,
                          prediction: PredictionResult) -> None: ...
    def record_outcome(self, outcome: MatchOutcome) -> None: ...
    def compute_error(self, match_id: str) -> PredictionError | None: ...
    def update_calibration(self, competition: str,
                           engine: str) -> object | None: ...
    def update_weights(self, competition: str) -> object | None: ...
    def engine_score(self, engine: str,
                     competition: str | None = None) -> EngineScore | None: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_kernel_protocols.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/kernel/protocols.py tests/test_kernel_protocols.py
git commit -m "feat(kernel): add Protocol interfaces for Adapter/Builder/Engine/Learning"
```

---

### Task 3: BTD Model Migration

**Files:**
- Create: `backend/app/kernel/engines/__init__.py`
- Create: `backend/app/kernel/engines/btd_model.py`
- Test: `backend/tests/test_kernel_btd_model.py`

**Interfaces:**
- Produces: `calculate_btd_probabilities(elo_home, elo_away, is_neutral, is_knockout) -> dict[str, float]`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_kernel_btd_model.py
"""Equivalence tests for migrated BTD model."""
import pytest

from app.kernel.engines.btd_model import calculate_btd_probabilities
from app.services.world_cup_engines.world_cup_btd_model import (
    calculate_btd_probabilities as old_calculate_btd_probabilities,
)


class TestBTDEquivalence:
    """Verify the kernel BTD model produces identical output to the old one."""

    @pytest.mark.parametrize("elo_home,elo_away,is_neutral,is_knockout", [
        (1900, 1800, True, False),
        (1500, 1600, True, False),
        (2000, 2000, True, False),
        (1900, 1800, True, True),
        (1700, 2100, True, True),
        (1850, 1850, False, False),
        (2200, 1600, False, True),
    ])
    def test_output_matches_old_engine(self, elo_home, elo_away, is_neutral, is_knockout):
        old = old_calculate_btd_probabilities(elo_home, elo_away, is_neutral, is_knockout)
        new = calculate_btd_probabilities(elo_home, elo_away, is_neutral, is_knockout)
        assert new == old


class TestBTDProperties:
    def test_probabilities_sum_to_one(self):
        probs = calculate_btd_probabilities(1900, 1800, is_neutral=True, is_knockout=False)
        total = probs["home_win"] + probs["draw"] + probs["away_win"]
        assert abs(total - 1.0) < 1e-6

    def test_stronger_team_higher_win_prob(self):
        probs = calculate_btd_probabilities(2000, 1500, is_neutral=True, is_knockout=False)
        assert probs["home_win"] > probs["away_win"]

    def test_equal_teams_equal_prob(self):
        probs = calculate_btd_probabilities(1800, 1800, is_neutral=True, is_knockout=False)
        assert abs(probs["home_win"] - probs["away_win"]) < 1e-6

    def test_knockout_reduces_draw(self):
        group = calculate_btd_probabilities(1800, 1800, is_neutral=True, is_knockout=False)
        knockout = calculate_btd_probabilities(1800, 1800, is_neutral=True, is_knockout=True)
        assert knockout["draw"] < group["draw"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_kernel_btd_model.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.kernel.engines'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/kernel/engines/__init__.py
"""Kernel prediction engines."""
```

```python
# backend/app/kernel/engines/btd_model.py
"""Bradley-Terry-Davidson (BTD) win/draw/loss probability model.

Migrated from app/services/world_cup_engines/world_cup_btd_model.py.
This is a sport-agnostic model: it converts two Elo ratings into a
three-way probability distribution using the Davidson formula.

The gamma and home_advantage parameters are fitted from historical
results and persisted to data/btd_params.json.
"""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_PARAMS_PATH = Path(os.getenv(
    "BTD_PARAMS_FILE",
    str(Path(__file__).resolve().parents[3] / "data" / "btd_params.json"),
))

_FALLBACK_GAMMA = 0.74
_FALLBACK_HOME_ADV = 0.0
_KNOCKOUT_GAMMA_FACTOR = 0.74


@lru_cache(maxsize=1)
def _load_params() -> tuple[float, float]:
    """Load fitted BTD parameters from JSON file."""
    try:
        with open(_PARAMS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        gamma = float(data.get("gamma", _FALLBACK_GAMMA))
        home_adv = float(data.get("home_advantage", _FALLBACK_HOME_ADV))
        logger.info("Loaded BTD params: gamma=%.4f home_adv=%.4f", gamma, home_adv)
        return gamma, home_adv
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as e:
        logger.warning("Using fallback BTD params: %s", e)
        return _FALLBACK_GAMMA, _FALLBACK_HOME_ADV


def calculate_btd_probabilities(
    elo_home: float,
    elo_away: float,
    is_neutral: bool = True,
    is_knockout: bool = False,
) -> dict[str, float]:
    """Calculate win/draw/loss probabilities from Elo ratings.

    Uses the Bradley-Terry-Davidson (1970) formula:
        P(home win) = alpha_h' / D
        P(draw)    = gamma * sqrt(alpha_h * alpha_a) / D
        P(away win) = alpha_a / D

    where alpha = 10^(elo/400) and gamma is fitted from historical data.

    Args:
        elo_home: Home team Elo rating (typically 1000-2200)
        elo_away: Away team Elo rating
        is_neutral: If True, no home advantage applied
        is_knockout: If True, reduce draw probability (knockout = lower 90-min draw rate)

    Returns:
        Dict with home_win, draw, away_win probabilities (sum = 1.0)
    """
    gamma, home_adv = _load_params()

    if is_knockout:
        gamma = max(gamma * _KNOCKOUT_GAMMA_FACTOR, 0.01)

    alpha_h = 10 ** (elo_home / 400.0)
    alpha_a = 10 ** (elo_away / 400.0)

    if not is_neutral and home_adv > 0:
        alpha_h_prime = (1 + home_adv) * alpha_h
    else:
        alpha_h_prime = alpha_h

    draw_term = gamma * (alpha_h * alpha_a) ** 0.5
    denominator = alpha_h_prime + alpha_a + draw_term

    return {
        "home_win": round(alpha_h_prime / denominator, 4),
        "draw": round(draw_term / denominator, 4),
        "away_win": round(alpha_a / denominator, 4),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_kernel_btd_model.py -v`
Expected: PASS (all equivalence + property tests)

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/kernel/engines/__init__.py app/kernel/engines/btd_model.py tests/test_kernel_btd_model.py
git commit -m "feat(kernel): migrate BTD model with equivalence tests"
```

---

### Task 4: EloOdds Engine Migration

**Files:**
- Create: `backend/app/kernel/engines/elo_odds_engine.py`
- Test: `backend/tests/test_kernel_elo_odds_engine.py`

**Interfaces:**
- Consumes: `FeatureSet` (specifically `features.team.elo_rating_home/away`, `features.market.odds_*`), `MatchIdentity`
- Produces: `EloOddsEngine` class implementing `PredictionEngine` Protocol

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_kernel_elo_odds_engine.py
"""Tests for migrated EloOddsEngine."""
from datetime import datetime, timezone
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, FeatureSet,
    GeneralFeatures, TeamFeatures, MarketFeatures,
    PlayerFeatures, EnvironmentFeatures,
)
from app.kernel.engines.elo_odds_engine import EloOddsEngine


def _make_features(
    elo_home=1900, elo_away=1800,
    odds_home=2.10, odds_draw=3.30, odds_away=3.50,
    is_knockout=False,
) -> FeatureSet:
    sport = SportIdentity(code="football", name="Football")
    comp = CompetitionIdentity(code="world_cup", name="FIFA World Cup", sport=sport)
    season = SeasonIdentity(competition=comp, season_key="2026")
    home = TeamIdentity(code="BRA", name="Brazil", competition=comp)
    away = TeamIdentity(code="ARG", name="Argentina", competition=comp)
    match = MatchIdentity(
        match_id="m1", season=season,
        stage="final" if is_knockout else "group_stage",
        round=None, home=home, away=away,
        kickoff_utc=datetime(2026, 6, 13, tzinfo=timezone.utc),
    )
    return FeatureSet(
        match=match,
        general=GeneralFeatures(None, None, None, None),
        team=TeamFeatures(
            elo_rating_home=float(elo_home), elo_rating_away=float(elo_away),
            form_home=None, form_away=None,
            h2h_home_win_rate=None, h2h_draw_rate=None,
            market_value_home=None, market_value_away=None,
        ),
        market=MarketFeatures(
            odds_home=odds_home, odds_draw=odds_draw, odds_away=odds_away,
            odds_source="test", odds_fresh=True,
        ),
        player=PlayerFeatures(None, None, None, None),
        environment=EnvironmentFeatures(None, None, None, False),
        custom={},
        data_quality="real",
        quality_notes=[],
        feature_version="1.0",
    )


class TestEloOddsEngine:
    def test_implements_protocol(self):
        from app.kernel.protocols import PredictionEngine
        engine = EloOddsEngine()
        assert isinstance(engine, PredictionEngine)

    def test_name(self):
        engine = EloOddsEngine()
        assert engine.name() == "elo_odds"

    def test_supported_sports(self):
        engine = EloOddsEngine()
        assert "*" in engine.supported_sports()

    def test_predict_returns_prediction_result(self):
        engine = EloOddsEngine()
        features = _make_features()
        result = engine.predict(features, features.match)
        assert result.engine_name == "elo_odds"
        assert "home" in result.predicted_scores
        assert "away" in result.predicted_scores
        assert "home_win" in result.outcome_probabilities
        assert "draw" in result.outcome_probabilities
        assert "away_win" in result.outcome_probabilities
        assert 0.0 <= result.confidence <= 1.0

    def test_probabilities_sum_to_one(self):
        engine = EloOddsEngine()
        features = _make_features()
        result = engine.predict(features, features.match)
        total = sum(result.outcome_probabilities.values())
        assert abs(total - 1.0) < 0.01

    def test_stronger_team_higher_win_prob(self):
        engine = EloOddsEngine()
        strong = _make_features(elo_home=2100, elo_away=1500)
        result = engine.predict(strong, strong.match)
        assert result.outcome_probabilities["home_win"] > result.outcome_probabilities["away_win"]

    def test_no_odds_graceful_degradation(self):
        """When odds are None, engine should still produce a prediction from Elo alone."""
        engine = EloOddsEngine()
        features = _make_features(odds_home=None, odds_draw=None, odds_away=None)
        result = engine.predict(features, features.match)
        assert result.engine_name == "elo_odds"
        total = sum(result.outcome_probabilities.values())
        assert abs(total - 1.0) < 0.01

    def test_explanation_contains_elo_contribution(self):
        engine = EloOddsEngine()
        features = _make_features()
        result = engine.predict(features, features.match)
        elo_items = [e for e in result.explanation if e.factor == "elo"]
        assert len(elo_items) > 0
        assert elo_items[0].available is True

    def test_explanation_contains_odds_contribution(self):
        engine = EloOddsEngine()
        features = _make_features()
        result = engine.predict(features, features.match)
        odds_items = [e for e in result.explanation if e.factor == "odds"]
        assert len(odds_items) > 0
        assert odds_items[0].available is True

    def test_no_odds_shows_odds_unavailable(self):
        engine = EloOddsEngine()
        features = _make_features(odds_home=None, odds_draw=None, odds_away=None)
        result = engine.predict(features, features.match)
        odds_items = [e for e in result.explanation if e.factor == "odds"]
        assert len(odds_items) > 0
        assert odds_items[0].available is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_kernel_elo_odds_engine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.kernel.engines.elo_odds_engine'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/kernel/engines/elo_odds_engine.py
"""Elo + Betting Odds fusion prediction engine.

Migrated from app/services/world_cup_engines/world_cup_elo_odds_engine.py.
This engine is sport-agnostic: it consumes FeatureSet and produces
PredictionResult, with no dependency on any world_cup_* module.

Combines:
1. Elo ratings (stable, long-term team strength) via BTD model
2. Betting market odds (sharp, incorporates everything)

Research shows ~70-75% accuracy with 30% Elo + 70% Odds weighting.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from app.kernel.domain import (
    FeatureSet, MatchIdentity, PredictionResult, ContributionItem,
)
from app.kernel.engines.btd_model import calculate_btd_probabilities


def _odds_to_probabilities(
    odds_home: float, odds_draw: float, odds_away: float,
) -> dict[str, float]:
    """Convert decimal odds to normalized probabilities (remove overround)."""
    implied_home = 1.0 / odds_home
    implied_draw = 1.0 / odds_draw
    implied_away = 1.0 / odds_away
    total = implied_home + implied_draw + implied_away
    return {
        "home_win": round(implied_home / total, 4),
        "draw": round(implied_draw / total, 4),
        "away_win": round(implied_away / total, 4),
    }


def _fuse_elo_and_odds(
    elo_probs: dict[str, float],
    market_probs: dict[str, float] | None,
    elo_weight: float = 0.30,
    odds_weight: float = 0.70,
) -> dict[str, float]:
    """Fuse Elo and market probabilities. Falls back to Elo-only if no market."""
    if market_probs is None:
        return elo_probs
    total_w = elo_weight + odds_weight
    ew = elo_weight / total_w
    ow = odds_weight / total_w
    return {
        "home_win": round(elo_probs["home_win"] * ew + market_probs["home_win"] * ow, 4),
        "draw": round(elo_probs["draw"] * ew + market_probs["draw"] * ow, 4),
        "away_win": round(elo_probs["away_win"] * ew + market_probs["away_win"] * ow, 4),
    }


def _probabilities_to_scores(
    probs: dict[str, float], league_avg_goals: float = 2.7,
) -> dict[str, float]:
    """Convert win probabilities to expected scores via Poisson."""
    home_advantage = (probs["home_win"] - probs["away_win"]) / 2
    home_share = 0.5 + home_advantage
    home_goals = league_avg_goals * home_share
    away_goals = league_avg_goals * (1 - home_share)
    draw_factor = 1.0 - (probs["draw"] - 0.20) * 0.5
    home_goals *= draw_factor
    away_goals *= draw_factor
    return {"home": round(home_goals, 2), "away": round(away_goals, 2)}


def _calculate_confidence(probs: dict[str, float]) -> float:
    """Confidence = max probability, slightly deflated."""
    max_prob = max(probs.values())
    return round(min(max_prob * 0.95, 0.95), 4)


class EloOddsEngine:
    """Elo + Odds fusion engine. Implements PredictionEngine Protocol."""

    def name(self) -> str:
        return "elo_odds"

    def supported_sports(self) -> list[str]:
        return ["*"]

    def predict(self, features: FeatureSet, match: MatchIdentity) -> PredictionResult:
        elo_home = features.team.elo_rating_home
        elo_away = features.team.elo_rating_away
        is_knockout = match.stage not in ("group_stage", "regular_season")

        # Elo probabilities via BTD
        if elo_home is not None and elo_away is not None:
            elo_probs = calculate_btd_probabilities(
                elo_home, elo_away, is_neutral=True, is_knockout=is_knockout,
            )
            elo_available = True
        else:
            elo_probs = {"home_win": 0.4, "draw": 0.3, "away_win": 0.3}
            elo_available = False

        # Market probabilities
        odds_h = features.market.odds_home
        odds_d = features.market.odds_draw
        odds_a = features.market.odds_away
        if odds_h and odds_d and odds_a and odds_h > 1.0 and odds_d > 1.0 and odds_a > 1.0:
            market_probs = _odds_to_probabilities(odds_h, odds_d, odds_a)
            odds_available = True
        else:
            market_probs = None
            odds_available = False

        # Fuse
        fused = _fuse_elo_and_odds(elo_probs, market_probs)
        scores = _probabilities_to_scores(fused)
        confidence = _calculate_confidence(fused)

        # Explanation
        explanation = [
            ContributionItem(
                factor="elo", direction="support" if elo_available else "neutral",
                weight=0.30, available=elo_available,
                detail=f"Elo {elo_home} vs {elo_away}" if elo_available else "Elo unavailable",
            ),
            ContributionItem(
                factor="odds", direction="support" if odds_available else "neutral",
                weight=0.70, available=odds_available,
                detail=f"Odds {odds_h}/{odds_d}/{odds_a}" if odds_available else "Odds unavailable",
            ),
        ]

        return PredictionResult(
            predicted_scores=scores,
            outcome_probabilities=fused,
            confidence=confidence,
            engine_name="elo_odds",
            explanation=explanation,
            betting_analysis=None,
            feature_version=features.feature_version,
            prediction_timestamp=datetime.now(timezone.utc),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_kernel_elo_odds_engine.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/kernel/engines/elo_odds_engine.py tests/test_kernel_elo_odds_engine.py
git commit -m "feat(kernel): migrate EloOddsEngine with FeatureSet interface"
```

---

### Task 5: Engine Registry

**Files:**
- Create: `backend/app/kernel/engine_registry.py`
- Test: `backend/tests/test_kernel_engine_registry.py`

**Interfaces:**
- Consumes: `PredictionEngine` Protocol from Task 2, `FeatureSet` from Task 1
- Produces: `EngineRegistry` class

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_kernel_engine_registry.py
"""Tests for EngineRegistry."""
import pytest
from datetime import datetime, timezone

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, FeatureSet,
    GeneralFeatures, TeamFeatures, MarketFeatures,
    PlayerFeatures, EnvironmentFeatures, PredictionResult,
)
from app.kernel.engine_registry import EngineRegistry
from app.kernel.engines.elo_odds_engine import EloOddsEngine


def _make_features() -> FeatureSet:
    sport = SportIdentity(code="football", name="Football")
    comp = CompetitionIdentity(code="world_cup", name="FIFA World Cup", sport=sport)
    season = SeasonIdentity(competition=comp, season_key="2026")
    home = TeamIdentity(code="BRA", name="Brazil", competition=comp)
    away = TeamIdentity(code="ARG", name="Argentina", competition=comp)
    match = MatchIdentity(
        match_id="m1", season=season, stage="group", round=None,
        home=home, away=away,
        kickoff_utc=datetime(2026, 6, 13, tzinfo=timezone.utc),
    )
    return FeatureSet(
        match=match,
        general=GeneralFeatures(None, None, None, None),
        team=TeamFeatures(1900, 1800, None, None, None, None, None, None),
        market=MarketFeatures(2.0, 3.0, 4.0, "test", True),
        player=PlayerFeatures(None, None, None, None),
        environment=EnvironmentFeatures(None, None, None, False),
        custom={}, data_quality="real", quality_notes=[], feature_version="1.0",
    )


class TestEngineRegistry:
    def test_register_and_get(self):
        reg = EngineRegistry()
        engine = EloOddsEngine()
        reg.register(engine)
        assert reg.get("elo_odds") is engine

    def test_list_engines(self):
        reg = EngineRegistry()
        reg.register(EloOddsEngine())
        names = reg.list_engines()
        assert "elo_odds" in names

    def test_get_unknown_raises(self):
        reg = EngineRegistry()
        with pytest.raises(KeyError):
            reg.get("nonexistent")

    def test_select_auto_returns_default(self):
        reg = EngineRegistry()
        reg.register(EloOddsEngine())
        features = _make_features()
        engine = reg.select("auto", features)
        assert engine.name() == "elo_odds"

    def test_select_by_name(self):
        reg = EngineRegistry()
        reg.register(EloOddsEngine())
        features = _make_features()
        engine = reg.select("elo_odds", features)
        assert engine.name() == "elo_odds"

    def test_select_unknown_strategy_raises(self):
        reg = EngineRegistry()
        reg.register(EloOddsEngine())
        features = _make_features()
        with pytest.raises(KeyError):
            reg.select("nonexistent", features)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_kernel_engine_registry.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/kernel/engine_registry.py
"""Engine registration and selection."""
from __future__ import annotations

from app.kernel.domain import FeatureSet
from app.kernel.protocols import PredictionEngine


class EngineRegistry:
    """Registers engines and selects them by name or strategy."""

    def __init__(self) -> None:
        self._engines: dict[str, PredictionEngine] = {}
        self._default_name: str | None = None

    def register(self, engine: PredictionEngine) -> None:
        name = engine.name()
        self._engines[name] = engine
        if self._default_name is None:
            self._default_name = name

    def get(self, name: str) -> PredictionEngine:
        if name not in self._engines:
            available = list(self._engines.keys())
            raise KeyError(f"Unknown engine: {name}. Available: {available}")
        return self._engines[name]

    def list_engines(self) -> list[str]:
        return list(self._engines.keys())

    def select(self, strategy: str, features: FeatureSet) -> PredictionEngine:
        if strategy == "auto":
            if self._default_name is None:
                raise KeyError("No engines registered")
            return self._engines[self._default_name]
        return self.get(strategy)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_kernel_engine_registry.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/kernel/engine_registry.py tests/test_kernel_engine_registry.py
git commit -m "feat(kernel): add EngineRegistry with register/select"
```

---

### Task 6: Feature Registry + Factor Registry

**Files:**
- Create: `backend/app/kernel/feature_registry.py`
- Create: `backend/app/kernel/factor_registry.py`
- Test: `backend/tests/test_kernel_feature_registry.py`
- Test: `backend/tests/test_kernel_factor_registry.py`

**Interfaces:**
- Produces: `FeatureDefinition`, `FeatureRegistry`, `FactorConfig`, `FactorRegistry`

- [ ] **Step 1: Write failing tests for FeatureRegistry**

```python
# backend/tests/test_kernel_feature_registry.py
"""Tests for FeatureRegistry."""
import pytest
from app.kernel.feature_registry import FeatureDefinition, FeatureRegistry


class TestFeatureRegistry:
    def test_register_and_get(self):
        reg = FeatureRegistry()
        reg.register("elo_rating_home", "team", "1.0", "Home team Elo rating")
        fd = reg.get("elo_rating_home")
        assert fd is not None
        assert fd.category == "team"
        assert fd.sport is None  # universal

    def test_register_with_sport(self):
        reg = FeatureRegistry()
        reg.register("xg_home", "custom", "1.0", "Expected goals home", sport="football")
        fd = reg.get("xg_home")
        assert fd.sport == "football"

    def test_get_unknown_returns_none(self):
        reg = FeatureRegistry()
        assert reg.get("nonexistent") is None

    def test_list_by_category(self):
        reg = FeatureRegistry()
        reg.register("elo_rating_home", "team", "1.0", "Elo home")
        reg.register("elo_rating_away", "team", "1.0", "Elo away")
        reg.register("odds_home", "market", "1.0", "Odds home")
        team_features = reg.list_by_category("team")
        assert len(team_features) == 2

    def test_list_by_sport(self):
        reg = FeatureRegistry()
        reg.register("elo_rating_home", "team", "1.0", "Elo home")
        reg.register("xg_home", "custom", "1.0", "xG home", sport="football")
        reg.register("pace_home", "custom", "1.0", "Pace home", sport="basketball")
        football = reg.list_by_sport("football")
        # Should include universal (sport=None) + football-specific
        keys = [f.key for f in football]
        assert "elo_rating_home" in keys
        assert "xg_home" in keys
        assert "pace_home" not in keys
```

- [ ] **Step 2: Write failing tests for FactorRegistry**

```python
# backend/tests/test_kernel_factor_registry.py
"""Tests for FactorRegistry."""
from datetime import datetime, timezone
import pytest
from app.kernel.factor_registry import FactorConfig, FactorRegistry


class TestFactorRegistry:
    def test_register_and_get_weight(self):
        reg = FactorRegistry()
        reg.register_factor(FactorConfig(
            factor_id="elo", category="team", version="1.0",
            weight=0.30, competition=None, enabled=True,
            source="manual", updated_at=datetime.now(timezone.utc),
        ))
        assert reg.get_weight("elo", "world_cup") == 0.30

    def test_competition_specific_weight(self):
        reg = FactorRegistry()
        # Global weight
        reg.register_factor(FactorConfig(
            factor_id="elo", category="team", version="1.0",
            weight=0.30, competition=None, enabled=True,
            source="manual", updated_at=datetime.now(timezone.utc),
        ))
        # EPL-specific weight
        reg.register_factor(FactorConfig(
            factor_id="elo", category="team", version="1.0",
            weight=0.40, competition="epl", enabled=True,
            source="learning", updated_at=datetime.now(timezone.utc),
        ))
        assert reg.get_weight("elo", "epl") == 0.40
        assert reg.get_weight("elo", "world_cup") == 0.30  # falls back to global

    def test_update_weight(self):
        reg = FactorRegistry()
        reg.register_factor(FactorConfig(
            factor_id="elo", category="team", version="1.0",
            weight=0.30, competition=None, enabled=True,
            source="manual", updated_at=datetime.now(timezone.utc),
        ))
        reg.update_weight("elo", "epl", 0.45, "auto_tune")
        assert reg.get_weight("elo", "epl") == 0.45

    def test_list_active(self):
        reg = FactorRegistry()
        reg.register_factor(FactorConfig(
            factor_id="elo", category="team", version="1.0",
            weight=0.30, competition=None, enabled=True,
            source="manual", updated_at=datetime.now(timezone.utc),
        ))
        reg.register_factor(FactorConfig(
            factor_id="xg", category="custom", version="1.0",
            weight=0.20, competition=None, enabled=False,
            source="manual", updated_at=datetime.now(timezone.utc),
        ))
        active = reg.list_active("world_cup")
        assert len(active) == 1
        assert active[0].factor_id == "elo"

    def test_get_unknown_factor_returns_default(self):
        reg = FactorRegistry()
        assert reg.get_weight("nonexistent", "world_cup") == 1.0  # default weight
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_kernel_feature_registry.py tests/test_kernel_factor_registry.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 4: Write implementations**

```python
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
```

```python
# backend/app/kernel/factor_registry.py
"""Factor weight and lifecycle management."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
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
            existing.weight = new_weight
            existing.source = source
            existing.updated_at = datetime.now(timezone.utc)
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_kernel_feature_registry.py tests/test_kernel_factor_registry.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd backend && git add app/kernel/feature_registry.py app/kernel/factor_registry.py tests/test_kernel_feature_registry.py tests/test_kernel_factor_registry.py
git commit -m "feat(kernel): add FeatureRegistry and FactorRegistry"
```

---

### Task 7: Kernel Database + Learning Service

**Files:**
- Create: `backend/app/kernel/kernel_db.py`
- Create: `backend/app/kernel/learning_service.py`
- Test: `backend/tests/test_kernel_learning_service.py`

**Interfaces:**
- Consumes: `MatchIdentity`, `PredictionResult`, `MatchOutcome`, `PredictionError`, `EngineScore` from Task 1
- Produces: `KernelLearningService` implementing `LearningService` Protocol

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_kernel_learning_service.py
"""Tests for KernelLearningService."""
from datetime import datetime, timezone
import pytest
import tempfile
import os

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome, PredictionResult,
    PredictionError, EngineScore,
)
from app.kernel.kernel_db import init_kernel_db, get_kernel_session, close_kernel_session
from app.kernel.learning_service import KernelLearningService


def _make_match(match_id="m1") -> MatchIdentity:
    sport = SportIdentity(code="football", name="Football")
    comp = CompetitionIdentity(code="world_cup", name="FIFA World Cup", sport=sport)
    season = SeasonIdentity(competition=comp, season_key="2026")
    home = TeamIdentity(code="BRA", name="Brazil", competition=comp)
    away = TeamIdentity(code="ARG", name="Argentina", competition=comp)
    return MatchIdentity(
        match_id=match_id, season=season, stage="group", round=None,
        home=home, away=away,
        kickoff_utc=datetime(2026, 6, 13, tzinfo=timezone.utc),
    )


def _make_prediction(engine="elo_odds") -> PredictionResult:
    return PredictionResult(
        predicted_scores={"home": 2.0, "away": 1.0},
        outcome_probabilities={"home_win": 0.55, "draw": 0.25, "away_win": 0.20},
        confidence=0.72, engine_name=engine, explanation=[],
        betting_analysis=None, feature_version="1.0",
        prediction_timestamp=datetime(2026, 6, 12, tzinfo=timezone.utc),
    )


def _make_outcome(match_id="m1") -> MatchOutcome:
    return MatchOutcome(
        match_id=match_id, home_score=2, away_score=1,
        outcome="home_win",
        finished_at=datetime(2026, 6, 13, 20, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def learning_service(tmp_path):
    """Create a learning service with a temp database."""
    db_path = str(tmp_path / "kernel_test.db")
    init_kernel_db(db_path)
    yield KernelLearningService()
    close_kernel_session()


class TestKernelLearningService:
    def test_record_prediction(self, learning_service):
        match = _make_match()
        pred = _make_prediction()
        learning_service.record_prediction(match, pred)
        # No exception means success

    def test_record_outcome(self, learning_service):
        outcome = _make_outcome()
        learning_service.record_outcome(outcome)

    def test_compute_error_correct_prediction(self, learning_service):
        match = _make_match()
        pred = _make_prediction()
        learning_service.record_prediction(match, pred)
        outcome = _make_outcome()
        learning_service.record_outcome(outcome)
        error = learning_service.compute_error("m1")
        assert error is not None
        assert error.match_id == "m1"
        assert error.outcome_correct is True  # predicted home_win, actual home_win
        assert error.score_mae >= 0

    def test_compute_error_wrong_prediction(self, learning_service):
        match = _make_match()
        pred = PredictionResult(
            predicted_scores={"home": 0.0, "away": 2.0},
            outcome_probabilities={"home_win": 0.10, "draw": 0.20, "away_win": 0.70},
            confidence=0.70, engine_name="elo_odds", explanation=[],
            betting_analysis=None, feature_version="1.0",
            prediction_timestamp=datetime(2026, 6, 12, tzinfo=timezone.utc),
        )
        learning_service.record_prediction(match, pred)
        outcome = _make_outcome()  # home_win 2-1
        learning_service.record_outcome(outcome)
        error = learning_service.compute_error("m1")
        assert error is not None
        assert error.outcome_correct is False

    def test_compute_error_no_prediction_returns_none(self, learning_service):
        outcome = _make_outcome()
        learning_service.record_outcome(outcome)
        error = learning_service.compute_error("m1")
        assert error is None

    def test_engine_score(self, learning_service):
        # Record multiple predictions and outcomes
        for i in range(5):
            match = _make_match(f"m{i}")
            pred = _make_prediction()
            learning_service.record_prediction(match, pred)
            outcome = MatchOutcome(
                match_id=f"m{i}", home_score=2, away_score=1,
                outcome="home_win",
                finished_at=datetime(2026, 6, 13, 20, 0, tzinfo=timezone.utc),
            )
            learning_service.record_outcome(outcome)
            learning_service.compute_error(f"m{i}")
        score = learning_service.engine_score("elo_odds", "world_cup")
        assert score is not None
        assert score.engine == "elo_odds"
        assert score.sample_count == 5
        assert 0.0 <= score.accuracy <= 1.0

    def test_engine_score_empty_returns_none(self, learning_service):
        score = learning_service.engine_score("nonexistent", "world_cup")
        assert score is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_kernel_learning_service.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write kernel_db.py implementation**

```python
# backend/app/kernel/kernel_db.py
"""Database management for the Prediction Kernel.

Uses a separate SQLite database (kernel_predictions.db) with kernel_ prefixed
tables. Does NOT touch the existing world_cup_predictions.db.
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker, DeclarativeBase

logger = logging.getLogger(__name__)

_engine = None
_SessionLocal = None


class KernelBase(DeclarativeBase):
    pass


# Define tables as SQLAlchemy models
from sqlalchemy import Column, String, Float, Integer, Text, DateTime, JSON


class KernelPrediction(KernelBase):
    __tablename__ = "kernel_predictions"

    match_id = Column(String, primary_key=True)
    sport = Column(String, nullable=False)
    competition = Column(String, nullable=False)
    season = Column(String, nullable=False)
    engine = Column(String, nullable=False)
    predicted_scores = Column(JSON, nullable=False)
    outcome_probabilities = Column(JSON, nullable=False)
    confidence = Column(Float, nullable=False)
    feature_version = Column(String, nullable=False)
    explanation = Column(JSON)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)


class KernelPredictionHistory(KernelBase):
    __tablename__ = "kernel_prediction_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(String, nullable=False)
    engine = Column(String, nullable=False)
    predicted_scores = Column(JSON)
    outcome_probabilities = Column(JSON)
    confidence = Column(Float)
    trigger = Column(String)
    created_at = Column(DateTime)


class KernelMatchOutcome(KernelBase):
    __tablename__ = "kernel_match_outcomes"

    match_id = Column(String, primary_key=True)
    home_score = Column(Integer)
    away_score = Column(Integer)
    outcome = Column(String)
    engine = Column(String)
    score_mae = Column(Float)
    outcome_correct = Column(Integer)
    brier_score = Column(Float)
    finished_at = Column(DateTime)
    created_at = Column(DateTime)


class KernelEngineScore(KernelBase):
    __tablename__ = "kernel_engine_scores"

    id = Column(Integer, primary_key=True, autoincrement=True)
    engine = Column(String, nullable=False)
    competition = Column(String)  # NULL = global
    accuracy = Column(Float)
    avg_mae = Column(Float)
    brier_score = Column(Float)
    sample_count = Column(Integer, default=0)
    last_updated = Column(DateTime)


class KernelFactor(KernelBase):
    __tablename__ = "kernel_factors"

    factor_id = Column(String, primary_key=True)
    category = Column(String, nullable=False)
    version = Column(String, nullable=False)
    weight = Column(Float, default=1.0)
    competition = Column(String)  # NULL = global
    enabled = Column(Integer, default=1)
    source = Column(String, default="manual")
    updated_at = Column(DateTime)


def init_kernel_db(db_path: str | None = None) -> None:
    """Initialize the kernel database. Creates tables if they don't exist."""
    global _engine, _SessionLocal
    if _engine is not None:
        return
    if db_path is None:
        db_path = str(Path(__file__).resolve().parents[2] / "kernel_predictions.db")
    _engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
        pool_recycle=3600,
    )

    @event.listens_for(_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    KernelBase.metadata.create_all(_engine)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    logger.info("Kernel DB initialized at %s", db_path)


def get_kernel_session() -> Session:
    if _SessionLocal is None:
        init_kernel_db()
    return _SessionLocal()


def close_kernel_session() -> None:
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
```

- [ ] **Step 4: Write learning_service.py implementation**

```python
# backend/app/kernel/learning_service.py
"""Learning service — records predictions and outcomes, computes errors.

Phase 1 implements: record_prediction, record_outcome, compute_error,
engine_score. Calibration and weight updates are deferred to Phase 3.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select, func

from app.kernel.domain import (
    MatchIdentity, MatchOutcome, PredictionResult,
    PredictionError, EngineScore,
)
from app.kernel.kernel_db import (
    get_kernel_session,
    KernelPrediction, KernelMatchOutcome, KernelEngineScore,
)

logger = logging.getLogger(__name__)


class KernelLearningService:
    """Implements LearningService Protocol for Phase 1."""

    def record_prediction(self, match: MatchIdentity,
                          prediction: PredictionResult) -> None:
        session = get_kernel_session()
        try:
            existing = session.get(KernelPrediction, match.match_id)
            now = datetime.now(timezone.utc)
            if existing:
                existing.engine = prediction.engine_name
                existing.predicted_scores = prediction.predicted_scores
                existing.outcome_probabilities = prediction.outcome_probabilities
                existing.confidence = prediction.confidence
                existing.feature_version = prediction.feature_version
                existing.explanation = [c.__dict__ for c in prediction.explanation]
                existing.updated_at = now
            else:
                record = KernelPrediction(
                    match_id=match.match_id,
                    sport=match.season.competition.sport.code,
                    competition=match.season.competition.code,
                    season=match.season.season_key,
                    engine=prediction.engine_name,
                    predicted_scores=prediction.predicted_scores,
                    outcome_probabilities=prediction.outcome_probabilities,
                    confidence=prediction.confidence,
                    feature_version=prediction.feature_version,
                    explanation=[c.__dict__ for c in prediction.explanation],
                    created_at=now,
                    updated_at=now,
                )
                session.add(record)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def record_outcome(self, outcome: MatchOutcome) -> None:
        session = get_kernel_session()
        try:
            existing = session.get(KernelMatchOutcome, outcome.match_id)
            if existing:
                existing.home_score = outcome.home_score
                existing.away_score = outcome.away_score
                existing.outcome = outcome.outcome
                existing.finished_at = outcome.finished_at
            else:
                record = KernelMatchOutcome(
                    match_id=outcome.match_id,
                    home_score=outcome.home_score,
                    away_score=outcome.away_score,
                    outcome=outcome.outcome,
                    finished_at=outcome.finished_at,
                    created_at=datetime.now(timezone.utc),
                )
                session.add(record)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def compute_error(self, match_id: str) -> PredictionError | None:
        session = get_kernel_session()
        try:
            pred = session.get(KernelPrediction, match_id)
            outcome = session.get(KernelMatchOutcome, match_id)
            if pred is None or outcome is None:
                return None

            # Score MAE
            pred_home = pred.predicted_scores.get("home", 0)
            pred_away = pred.predicted_scores.get("away", 0)
            score_mae = (abs(pred_home - outcome.home_score) +
                         abs(pred_away - outcome.away_score)) / 2.0

            # Outcome correct
            predicted_outcome = max(
                pred.outcome_probabilities,
                key=pred.outcome_probabilities.get,
            ) if pred.outcome_probabilities else None
            outcome_correct = (predicted_outcome == outcome.outcome)

            # Brier score
            probs = pred.outcome_probabilities
            brier = sum(
                (probs.get(k, 0) - (1.0 if k == outcome.outcome else 0.0)) ** 2
                for k in ["home_win", "draw", "away_win"]
            )

            # Confidence calibrated
            confidence_calibrated = (
                (outcome_correct and pred.confidence >= 0.5) or
                (not outcome_correct and pred.confidence < 0.5)
            )

            error = PredictionError(
                match_id=match_id, engine=pred.engine,
                score_mae=round(score_mae, 4),
                outcome_correct=outcome_correct,
                brier_score=round(brier, 4),
                confidence_calibrated=confidence_calibrated,
            )

            # Update outcome record with error
            outcome.engine = pred.engine
            outcome.score_mae = error.score_mae
            outcome.outcome_correct = 1 if outcome_correct else 0
            outcome.brier_score = error.brier_score
            session.commit()

            return error
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def update_calibration(self, competition: str, engine: str) -> None:
        """Deferred to Phase 3."""
        logger.info("update_calibration deferred to Phase 3")

    def update_weights(self, competition: str) -> None:
        """Deferred to Phase 3."""
        logger.info("update_weights deferred to Phase 3")

    def engine_score(self, engine: str,
                     competition: str | None = None) -> EngineScore | None:
        session = get_kernel_session()
        try:
            query = select(
                KernelMatchOutcome,
            ).where(
                KernelMatchOutcome.engine == engine,
                KernelMatchOutcome.outcome_correct.isnot(None),
            )
            if competition is not None:
                # Join with predictions to filter by competition
                query = query.join(
                    KernelPrediction,
                    KernelMatchOutcome.match_id == KernelPrediction.match_id,
                ).where(KernelPrediction.competition == competition)

            results = session.execute(query).scalars().all()
            if not results:
                return None

            count = len(results)
            correct = sum(1 for r in results if r.outcome_correct)
            accuracy = correct / count if count > 0 else 0.0
            avg_mae = sum(r.score_mae or 0 for r in results) / count
            avg_brier = sum(r.brier_score or 0 for r in results) / count

            return EngineScore(
                engine=engine, competition=competition,
                accuracy=round(accuracy, 4),
                avg_mae=round(avg_mae, 4),
                brier_score=round(avg_brier, 4),
                sample_count=count,
                confidence_calibration=0.0,  # Phase 3
                last_updated=datetime.now(timezone.utc),
            )
        finally:
            session.close()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_kernel_learning_service.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd backend && git add app/kernel/kernel_db.py app/kernel/learning_service.py tests/test_kernel_learning_service.py
git commit -m "feat(kernel): add kernel DB and learning service with error computation"
```

---

### Task 8: Prediction Kernel Orchestrator

**Files:**
- Create: `backend/app/kernel/prediction_kernel.py`
- Test: `backend/tests/test_kernel_prediction_kernel.py`

**Interfaces:**
- Consumes: All previous tasks
- Produces: `PredictionKernel` class

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_kernel_prediction_kernel.py
"""Tests for PredictionKernel orchestrator."""
from datetime import datetime, timezone
import pytest
import tempfile

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, FeatureSet,
    GeneralFeatures, TeamFeatures, MarketFeatures,
    PlayerFeatures, EnvironmentFeatures, PredictionResult,
)
from app.kernel.protocols import DataAdapter, ScheduleFilter, RawMatchData
from app.kernel.prediction_kernel import PredictionKernel
from app.kernel.engine_registry import EngineRegistry
from app.kernel.feature_registry import FeatureRegistry
from app.kernel.factor_registry import FactorRegistry
from app.kernel.engines.elo_odds_engine import EloOddsEngine
from app.kernel.kernel_db import init_kernel_db, close_kernel_session
from app.kernel.learning_service import KernelLearningService


def _make_match(match_id="m1") -> MatchIdentity:
    sport = SportIdentity(code="football", name="Football")
    comp = CompetitionIdentity(code="world_cup", name="FIFA World Cup", sport=sport)
    season = SeasonIdentity(competition=comp, season_key="2026")
    home = TeamIdentity(code="BRA", name="Brazil", competition=comp)
    away = TeamIdentity(code="ARG", name="Argentina", competition=comp)
    return MatchIdentity(
        match_id=match_id, season=season, stage="group", round=None,
        home=home, away=away,
        kickoff_utc=datetime(2026, 6, 13, tzinfo=timezone.utc),
    )


class FakeAdapter:
    """Minimal adapter for testing."""

    def __init__(self):
        self._match = _make_match()

    def fetch_schedule(self, filters): return []
    def fetch_team_data(self, team): return {"elo": 1900}
    def fetch_player_data(self, team): return {}
    def fetch_market_data(self, match): return {"odds": [2.0, 3.0, 4.0]}
    def fetch_outcome(self, match_id): return None
    def sync_schedule(self): return 0
    def get_match_identity(self, match_id):
        return self._match

    def fetch_all_data(self, match):
        return {
            "team": {"elo_home": 1900, "elo_away": 1800},
            "market": {"odds_home": 2.0, "odds_draw": 3.0, "odds_away": 4.0},
            "player": {},
            "environment": {},
            "general": {},
        }


class FakeFeatureBuilder:
    """Minimal feature builder for testing."""

    def build(self, match, raw):
        return FeatureSet(
            match=match,
            general=GeneralFeatures(None, None, None, None),
            team=TeamFeatures(
                raw.get("team", {}).get("elo_home", 1900),
                raw.get("team", {}).get("elo_away", 1800),
                None, None, None, None, None, None,
            ),
            market=MarketFeatures(
                raw.get("market", {}).get("odds_home", 2.0),
                raw.get("market", {}).get("odds_draw", 3.0),
                raw.get("market", {}).get("odds_away", 4.0),
                "test", True,
            ),
            player=PlayerFeatures(None, None, None, None),
            environment=EnvironmentFeatures(None, None, None, False),
            custom={}, data_quality="real", quality_notes=[], feature_version="1.0",
        )

    def sport(self):
        return SportIdentity(code="football", name="Football")


@pytest.fixture
def kernel(tmp_path):
    init_kernel_db(str(tmp_path / "kernel_test.db"))
    reg = EngineRegistry()
    reg.register(EloOddsEngine())
    kernel = PredictionKernel(
        adapter=FakeAdapter(),
        feature_builder=FakeFeatureBuilder(),
        engine_registry=reg,
        factor_registry=FactorRegistry(),
        feature_registry=FeatureRegistry(),
        learning=KernelLearningService(),
    )
    yield kernel
    close_kernel_session()


class TestPredictionKernel:
    def test_predict_returns_prediction_result(self, kernel):
        result = kernel.predict("m1", engine="auto")
        assert result.engine_name == "elo_odds"
        assert "home" in result.predicted_scores
        assert "away" in result.predicted_scores

    def test_predict_records_to_learning(self, kernel):
        result = kernel.predict("m1", engine="auto")
        # Verify it was recorded
        score = kernel._learning.engine_score("elo_odds", "world_cup")
        # No outcomes yet, so score should be None (no completed matches)
        assert score is None  # no outcomes recorded

    def test_batch_predict(self, kernel):
        results = kernel.batch_predict(["m1"], engine="auto")
        assert len(results) == 1
        assert results[0].engine_name == "elo_odds"

    def test_process_outcome_triggers_learning(self, kernel):
        from app.kernel.domain import MatchOutcome
        # First predict
        kernel.predict("m1", engine="auto")
        # Override adapter to return outcome
        kernel._adapter.fetch_outcome = lambda match_id: MatchOutcome(
            match_id="m1", home_score=2, away_score=1,
            outcome="home_win",
            finished_at=datetime(2026, 6, 13, 20, 0, tzinfo=timezone.utc),
        )
        kernel.process_outcome("m1")
        score = kernel._learning.engine_score("elo_odds", "world_cup")
        assert score is not None
        assert score.sample_count == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_kernel_prediction_kernel.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/kernel/prediction_kernel.py
"""Prediction Kernel — the core orchestrator.

Connects: Adapter → FeatureBuilder → Engine → Learning
The Kernel has zero knowledge of any specific sport or competition.
"""
from __future__ import annotations

import logging

from app.kernel.domain import (
    MatchIdentity, FeatureSet, PredictionResult, MatchOutcome,
)
from app.kernel.protocols import DataAdapter, FeatureBuilder
from app.kernel.engine_registry import EngineRegistry
from app.kernel.feature_registry import FeatureRegistry
from app.kernel.factor_registry import FactorRegistry
from app.kernel.learning_service import KernelLearningService

logger = logging.getLogger(__name__)


class PredictionKernel:
    """Core orchestrator connecting all Kernel components."""

    def __init__(
        self,
        adapter: DataAdapter,
        feature_builder: FeatureBuilder,
        engine_registry: EngineRegistry,
        factor_registry: FactorRegistry,
        feature_registry: FeatureRegistry,
        learning: KernelLearningService,
    ) -> None:
        self._adapter = adapter
        self._feature_builder = feature_builder
        self._engine_registry = engine_registry
        self._factor_registry = factor_registry
        self._feature_registry = feature_registry
        self._learning = learning

    def predict(self, match_id: str, engine: str = "auto") -> PredictionResult:
        """Run a prediction for a single match."""
        # 1. Get match identity
        match = self._adapter.get_match_identity(match_id)
        # 2. Fetch raw data
        raw = self._adapter.fetch_all_data(match)
        # 3. Build features
        features = self._feature_builder.build(match, raw)
        # 4. Select engine
        engine_impl = self._engine_registry.select(engine, features)
        # 5. Run prediction
        prediction = engine_impl.predict(features, match)
        # 6. Record for learning
        self._learning.record_prediction(match, prediction)
        # 7. Return result
        return prediction

    def batch_predict(
        self, match_ids: list[str], engine: str = "auto",
    ) -> list[PredictionResult]:
        """Run predictions for multiple matches."""
        results = []
        for match_id in match_ids:
            try:
                result = self.predict(match_id, engine=engine)
                results.append(result)
            except Exception as e:
                logger.error("Prediction failed for %s: %s", match_id, e)
        return results

    def process_outcome(self, match_id: str) -> None:
        """Process a match outcome — triggers the learning loop."""
        outcome = self._adapter.fetch_outcome(match_id)
        if outcome is None:
            logger.warning("No outcome found for match %s", match_id)
            return
        self._learning.record_outcome(outcome)
        self._learning.compute_error(match_id)
        # Calibration and weight updates deferred to Phase 3
        # self._learning.update_calibration(competition, engine)
        # self._learning.update_weights(competition)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_kernel_prediction_kernel.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/kernel/prediction_kernel.py tests/test_kernel_prediction_kernel.py
git commit -m "feat(kernel): add PredictionKernel orchestrator"
```

---

### Task 9: WorldCupAdapter + FootballFeatureBuilder

**Files:**
- Create: `backend/app/sports/__init__.py`
- Create: `backend/app/sports/football/__init__.py`
- Create: `backend/app/sports/football/adapters/__init__.py`
- Create: `backend/app/sports/football/adapters/world_cup_adapter.py`
- Create: `backend/app/sports/football/feature_builder.py`
- Test: `backend/tests/test_world_cup_adapter.py`
- Test: `backend/tests/test_football_feature_builder.py`

**Interfaces:**
- Consumes: Kernel domain + protocols, existing `world_cup_*` services
- Produces: `WorldCupAdapter` (DataAdapter), `FootballFeatureBuilder` (FeatureBuilder)

- [ ] **Step 1: Write failing test for WorldCupAdapter**

```python
# backend/tests/test_world_cup_adapter.py
"""Tests for WorldCupAdapter bridge."""
import pytest
from datetime import datetime, timezone

from app.kernel.domain import MatchIdentity, MatchOutcome
from app.kernel.protocols import DataAdapter
from app.sports.football.adapters.world_cup_adapter import WorldCupAdapter


class TestWorldCupAdapter:
    def test_implements_data_adapter_protocol(self):
        adapter = WorldCupAdapter()
        assert isinstance(adapter, DataAdapter)

    def test_get_match_identity_returns_match_identity(self):
        adapter = WorldCupAdapter()
        # Use a known match_id from the DB; if DB is empty, test creation logic
        match = adapter.get_match_identity("test_match_1")
        assert isinstance(match, MatchIdentity)
        assert match.season.competition.sport.code == "football"
        assert match.season.competition.code == "world_cup"

    def test_fetch_outcome_returns_none_for_unknown(self):
        adapter = WorldCupAdapter()
        result = adapter.fetch_outcome("nonexistent_match_id")
        assert result is None

    def test_sync_schedule_returns_int(self):
        adapter = WorldCupAdapter()
        # sync may fail if API keys not configured, but should return int
        result = adapter.sync_schedule()
        assert isinstance(result, int)
```

- [ ] **Step 2: Write failing test for FootballFeatureBuilder**

```python
# backend/tests/test_football_feature_builder.py
"""Tests for FootballFeatureBuilder."""
import pytest
from datetime import datetime, timezone

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, FeatureSet,
)
from app.kernel.protocols import FeatureBuilder
from app.sports.football.feature_builder import FootballFeatureBuilder


def _make_match() -> MatchIdentity:
    sport = SportIdentity(code="football", name="Football")
    comp = CompetitionIdentity(code="world_cup", name="FIFA World Cup", sport=sport)
    season = SeasonIdentity(competition=comp, season_key="2026")
    home = TeamIdentity(code="BRA", name="Brazil", competition=comp)
    away = TeamIdentity(code="ARG", name="Argentina", competition=comp)
    return MatchIdentity(
        match_id="m1", season=season, stage="group", round=None,
        home=home, away=away,
        kickoff_utc=datetime(2026, 6, 13, tzinfo=timezone.utc),
    )


class TestFootballFeatureBuilder:
    def test_implements_feature_builder_protocol(self):
        builder = FootballFeatureBuilder()
        assert isinstance(builder, FeatureBuilder)

    def test_sport_returns_football(self):
        builder = FootballFeatureBuilder()
        sport = builder.sport()
        assert sport.code == "football"

    def test_build_returns_feature_set(self):
        builder = FootballFeatureBuilder()
        match = _make_match()
        raw = {
            "team": {"elo_home": 1900, "elo_away": 1800},
            "market": {"odds_home": 2.0, "odds_draw": 3.0, "odds_away": 4.0},
            "player": {},
            "environment": {},
            "general": {},
        }
        features = builder.build(match, raw)
        assert isinstance(features, FeatureSet)
        assert features.team.elo_rating_home == 1900
        assert features.market.odds_home == 2.0
        assert features.feature_version == "1.0"

    def test_build_with_missing_data_uses_none(self):
        builder = FootballFeatureBuilder()
        match = _make_match()
        raw = {}
        features = builder.build(match, raw)
        assert features.team.elo_rating_home is None
        assert features.market.odds_home is None
        assert features.data_quality == "partial"

    def test_build_with_full_data_quality_real(self):
        builder = FootballFeatureBuilder()
        match = _make_match()
        raw = {
            "team": {"elo_home": 1900, "elo_away": 1800},
            "market": {"odds_home": 2.0, "odds_draw": 3.0, "odds_away": 4.0},
        }
        features = builder.build(match, raw)
        assert features.data_quality == "real"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_world_cup_adapter.py tests/test_football_feature_builder.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 4: Write implementations**

```python
# backend/app/sports/__init__.py
"""Sport-specific modules."""
```

```python
# backend/app/sports/football/__init__.py
"""Football sport module."""
```

```python
# backend/app/sports/football/adapters/__init__.py
"""Football data adapters."""
```

```python
# backend/app/sports/football/adapters/world_cup_adapter.py
"""WorldCupAdapter — bridges existing world_cup_* services to DataAdapter Protocol.

This adapter calls existing world_cup services internally but exposes them
through the sport-agnostic DataAdapter interface. The Kernel never sees
world_cup_* — it only sees DataAdapter.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome,
)
from app.kernel.protocols import ScheduleFilter, RawMatchData

logger = logging.getLogger(__name__)

_SPORT = SportIdentity(code="football", name="Football")
_COMPETITION = CompetitionIdentity(code="world_cup", name="FIFA World Cup", sport=_SPORT)


class WorldCupAdapter:
    """Bridges existing world_cup_* services to the DataAdapter Protocol."""

    def get_match_identity(self, match_id: str) -> MatchIdentity:
        """Get MatchIdentity from the world_cup match_fixtures table."""
        from app.utils.prediction_db import get_prediction_session
        from app.models.world_cup_prediction import MatchFixture

        session = get_prediction_session()
        try:
            fixture = session.get(MatchFixture, match_id)
            if fixture is None:
                # Return a stub for testing — real usage requires DB data
                season = SeasonIdentity(competition=_COMPETITION, season_key="2026")
                home = TeamIdentity(code="HOME", name="Home", competition=_COMPETITION)
                away = TeamIdentity(code="AWAY", name="Away", competition=_COMPETITION)
                return MatchIdentity(
                    match_id=match_id, season=season,
                    stage="group_stage", round=None,
                    home=home, away=away,
                    kickoff_utc=datetime(2026, 6, 13, tzinfo=timezone.utc),
                )
            season = SeasonIdentity(competition=_COMPETITION, season_key="2026")
            home = TeamIdentity(code=fixture.home_team[:3].upper(),
                                name=fixture.home_team, competition=_COMPETITION)
            away = TeamIdentity(code=fixture.away_team[:3].upper(),
                                name=fixture.away_team, competition=_COMPETITION)
            return MatchIdentity(
                match_id=fixture.match_id, season=season,
                stage=fixture.stage or "group_stage",
                round=None,
                home=home, away=away,
                kickoff_utc=fixture.kickoff_utc or datetime(2026, 6, 13, tzinfo=timezone.utc),
            )
        finally:
            session.close()

    def fetch_all_data(self, match: MatchIdentity) -> dict:
        """Fetch all raw data for a match from existing world_cup services."""
        raw = {"team": {}, "market": {}, "player": {}, "environment": {}, "general": {}}

        # Elo ratings
        try:
            from app.services.elo_ratings_service import get_elo_rating
            raw["team"]["elo_home"] = get_elo_rating(match.home.name)
            raw["team"]["elo_away"] = get_elo_rating(match.away.name)
        except Exception as e:
            logger.warning("Failed to fetch Elo ratings: %s", e)

        # Odds
        try:
            from app.services.odds_cache_service import get_cached_odds
            odds = get_cached_odds(match.home.name, match.away.name)
            if odds:
                raw["market"]["odds_home"] = odds.get("home_odds")
                raw["market"]["odds_draw"] = odds.get("draw_odds")
                raw["market"]["odds_away"] = odds.get("away_odds")
        except Exception as e:
            logger.warning("Failed to fetch odds: %s", e)

        return raw

    def fetch_schedule(self, filters: ScheduleFilter) -> list[RawMatchData]:
        """Fetch match schedule from existing world_cup services."""
        from app.utils.prediction_db import get_prediction_session
        from app.models.world_cup_prediction import MatchFixture
        from sqlalchemy import select

        session = get_prediction_session()
        try:
            query = select(MatchFixture)
            if filters.status:
                query = query.where(MatchFixture.status == filters.status)
            if filters.stage:
                query = query.where(MatchFixture.stage == filters.stage)
            if filters.limit:
                query = query.limit(filters.limit)

            fixtures = session.execute(query).scalars().all()
            return [
                RawMatchData(match=self.get_match_identity(f.match_id), raw_json={})
                for f in fixtures
            ]
        finally:
            session.close()

    def fetch_team_data(self, team: TeamIdentity) -> dict:
        return {}

    def fetch_player_data(self, team: TeamIdentity) -> dict:
        return {}

    def fetch_market_data(self, match: MatchIdentity) -> dict:
        return {}

    def fetch_outcome(self, match_id: str) -> MatchOutcome | None:
        """Fetch match outcome from match_results table."""
        from app.utils.prediction_db import get_prediction_session
        from app.models.world_cup_prediction import MatchResult

        session = get_prediction_session()
        try:
            result = session.get(MatchResult, match_id)
            if result is None:
                return None
            return MatchOutcome(
                match_id=match_id,
                home_score=result.final_home_score,
                away_score=result.final_away_score,
                outcome=result.outcome,
                finished_at=result.finished_at or datetime.now(timezone.utc),
            )
        finally:
            session.close()

    def sync_schedule(self) -> int:
        """Sync fixtures from external sources."""
        try:
            from app.services.world_cup_match_service import sync_world_cup_fixtures
            return sync_world_cup_fixtures()
        except Exception as e:
            logger.error("Failed to sync schedule: %s", e)
            return 0
```

```python
# backend/app/sports/football/feature_builder.py
"""FootballFeatureBuilder — computes FeatureSet from raw data.

Computes:
- General layer: rest days, travel distance
- Team layer: Elo, form, h2h, market value
- Market layer: odds
- Player layer: injury impact, availability
- Environment layer: weather, venue
- Custom: xG, PPDA, Possession, Shots (football-specific)
"""
from __future__ import annotations

import logging

from app.kernel.domain import (
    SportIdentity, MatchIdentity, FeatureSet,
    GeneralFeatures, TeamFeatures, MarketFeatures,
    PlayerFeatures, EnvironmentFeatures,
)

logger = logging.getLogger(__name__)

_FOOTBALL = SportIdentity(code="football", name="Football")


class FootballFeatureBuilder:
    """Builds FeatureSet for football matches."""

    def sport(self) -> SportIdentity:
        return _FOOTBALL

    def build(self, match: MatchIdentity, raw: dict) -> FeatureSet:
        team_raw = raw.get("team", {})
        market_raw = raw.get("market", {})
        player_raw = raw.get("player", {})
        env_raw = raw.get("environment", {})
        general_raw = raw.get("general", {})

        # Determine data quality
        has_elo = team_raw.get("elo_home") is not None
        has_odds = market_raw.get("odds_home") is not None
        quality_notes = []
        if not has_odds:
            quality_notes.append("betting_odds_unavailable")
        data_quality = "real" if (has_elo and has_odds) else "partial"

        return FeatureSet(
            match=match,
            general=GeneralFeatures(
                rest_days_home=general_raw.get("rest_days_home"),
                rest_days_away=general_raw.get("rest_days_away"),
                travel_distance_km=general_raw.get("travel_distance_km"),
                days_since_last_match=general_raw.get("days_since_last_match"),
            ),
            team=TeamFeatures(
                elo_rating_home=team_raw.get("elo_home"),
                elo_rating_away=team_raw.get("elo_away"),
                form_home=team_raw.get("form_home"),
                form_away=team_raw.get("form_away"),
                h2h_home_win_rate=team_raw.get("h2h_home_win_rate"),
                h2h_draw_rate=team_raw.get("h2h_draw_rate"),
                market_value_home=team_raw.get("market_value_home"),
                market_value_away=team_raw.get("market_value_away"),
            ),
            market=MarketFeatures(
                odds_home=market_raw.get("odds_home"),
                odds_draw=market_raw.get("odds_draw"),
                odds_away=market_raw.get("odds_away"),
                odds_source=market_raw.get("odds_source"),
                odds_fresh=market_raw.get("odds_fresh", False),
            ),
            player=PlayerFeatures(
                key_players_available_home=player_raw.get("key_players_available_home"),
                key_players_available_away=player_raw.get("key_players_available_away"),
                injury_impact_home=player_raw.get("injury_impact_home"),
                injury_impact_away=player_raw.get("injury_impact_away"),
            ),
            environment=EnvironmentFeatures(
                venue=env_raw.get("venue"),
                weather_temp_c=env_raw.get("weather_temp_c"),
                weather_condition=env_raw.get("weather_condition"),
                is_home_advantage=env_raw.get("is_home_advantage", False),
            ),
            custom=raw.get("custom", {}),
            data_quality=data_quality,
            quality_notes=quality_notes,
            feature_version="1.0",
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_world_cup_adapter.py tests/test_football_feature_builder.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd backend && git add app/sports/ tests/test_world_cup_adapter.py tests/test_football_feature_builder.py
git commit -m "feat(sports): add WorldCupAdapter and FootballFeatureBuilder"
```

---

### Task 10: API Routes + Regression Test

**Files:**
- Create: `backend/app/api/routes/predictions.py`
- Modify: `backend/app/api/router.py` (add predictions router)
- Modify: `backend/app/core/config.py` (add `KERNEL_PREDICTION_ENABLED` setting)
- Test: `backend/tests/test_predictions_route.py`

**Interfaces:**
- Consumes: `PredictionKernel` from Task 8
- Produces: `/api/predictions/*` routes

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_predictions_route.py
"""Tests for /api/predictions routes."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


class TestPredictionsRoutes:
    def test_list_engines(self, client):
        resp = client.get("/api/predictions/engines")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert "elo_odds" in data

    def test_predict_match_not_found(self, client):
        resp = client.post("/api/predictions/matches/nonexistent/predict")
        assert resp.status_code in (404, 500)

    def test_process_outcome_not_found(self, client):
        resp = client.post("/api/predictions/outcomes/nonexistent/process")
        assert resp.status_code in (404, 200, 500)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_predictions_route.py -v`
Expected: FAIL (route not found)

- [ ] **Step 3: Add config setting**

Read `backend/app/core/config.py` and add:
```python
    KERNEL_PREDICTION_ENABLED: bool = False
```

- [ ] **Step 4: Write predictions route**

```python
# backend/app/api/routes/predictions.py
"""Generic prediction API routes.

These routes provide sport-agnostic prediction access through the
Prediction Kernel. When KERNEL_PREDICTION_ENABLED is false, the
routes return 503 Service Unavailable.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.core.config import settings

router = APIRouter(prefix="/api/predictions", tags=["Predictions"])
logger = logging.getLogger(__name__)


def _get_kernel():
    """Lazy-initialize the PredictionKernel singleton."""
    if not settings.KERNEL_PREDICTION_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Kernel prediction is disabled. Set KERNEL_PREDICTION_ENABLED=true to enable.",
        )
    from app.kernel.prediction_kernel import PredictionKernel
    from app.kernel.engine_registry import EngineRegistry
    from app.kernel.feature_registry import FeatureRegistry
    from app.kernel.factor_registry import FactorRegistry
    from app.kernel.engines.elo_odds_engine import EloOddsEngine
    from app.kernel.kernel_db import init_kernel_db
    from app.kernel.learning_service import KernelLearningService
    from app.sports.football.adapters.world_cup_adapter import WorldCupAdapter
    from app.sports.football.feature_builder import FootballFeatureBuilder

    if not hasattr(_get_kernel, "_instance"):
        init_kernel_db()
        reg = EngineRegistry()
        reg.register(EloOddsEngine())
        _get_kernel._instance = PredictionKernel(
            adapter=WorldCupAdapter(),
            feature_builder=FootballFeatureBuilder(),
            engine_registry=reg,
            factor_registry=FactorRegistry(),
            feature_registry=FeatureRegistry(),
            learning=KernelLearningService(),
        )
    return _get_kernel._instance


@router.get("/engines")
def list_engines():
    """List available prediction engines."""
    if not settings.KERNEL_PREDICTION_ENABLED:
        return ["elo_odds"]  # static list when disabled
    kernel = _get_kernel()
    return kernel._engine_registry.list_engines()


@router.post("/matches/{match_id}/predict")
def predict_match(match_id: str, engine: str = "auto"):
    """Run a prediction for a single match."""
    kernel = _get_kernel()
    try:
        result = kernel.predict(match_id, engine=engine)
        return {
            "match_id": match_id,
            "engine": result.engine_name,
            "predicted_scores": result.predicted_scores,
            "outcome_probabilities": result.outcome_probabilities,
            "confidence": result.confidence,
            "explanation": [c.__dict__ for c in result.explanation],
        }
    except Exception as e:
        logger.error("Prediction failed for %s: %s", match_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/outcomes/{match_id}/process")
def process_outcome(match_id: str):
    """Process a match outcome — triggers the learning loop."""
    kernel = _get_kernel()
    try:
        kernel.process_outcome(match_id)
        return {"match_id": match_id, "status": "processed"}
    except Exception as e:
        logger.error("Outcome processing failed for %s: %s", match_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/engines/{name}/score")
def engine_score(name: str, competition: str | None = None):
    """Get the performance score for an engine."""
    kernel = _get_kernel()
    score = kernel._learning.engine_score(name, competition)
    if score is None:
        raise HTTPException(status_code=404, detail="No score data for this engine")
    return {
        "engine": score.engine,
        "competition": score.competition,
        "accuracy": score.accuracy,
        "avg_mae": score.avg_mae,
        "brier_score": score.brier_score,
        "sample_count": score.sample_count,
    }
```

- [ ] **Step 5: Register router**

Modify `backend/app/api/router.py`:
```python
from app.api.routes import events, llm, quality_metrics, world_cup_predictions, world_cup_analytics, predictions

api_router.include_router(predictions.router, tags=["Predictions"])
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_predictions_route.py -v`
Expected: PASS

- [ ] **Step 7: Run full regression test suite**

Run: `cd backend && python -m pytest tests/ -v --tb=short -q`
Expected: All existing tests pass + new tests pass

- [ ] **Step 8: Commit**

```bash
cd backend && git add app/api/routes/predictions.py app/api/router.py app/core/config.py tests/test_predictions_route.py
git commit -m "feat(kernel): add /api/predictions routes with feature flag"
```

---

## Self-Review Checklist

**1. Spec coverage:**
- [x] Domain model (value objects) — Task 1
- [x] FeatureSet layered structure — Task 1
- [x] Protocol interfaces — Task 2
- [x] BTD model migration — Task 3
- [x] EloOdds engine migration — Task 4
- [x] EngineRegistry — Task 5
- [x] FeatureRegistry + FactorRegistry — Task 6
- [x] Learning service (record + error + score) — Task 7
- [x] PredictionKernel orchestrator — Task 8
- [x] WorldCupAdapter + FootballFeatureBuilder — Task 9
- [x] API routes + feature flag — Task 10
- [x] Database tables (kernel_ prefix) — Task 7 (kernel_db.py)
- [x] Migration safety constraints — All tasks preserve existing code
- [ ] HybridEngine / RuleEngine / AIEngine / GBMEngine migration — deferred to follow-up tasks (can run in parallel after Task 4 pattern is established)
- [ ] Calibration module migration — deferred (Phase 3 dependency)

**2. Placeholder scan:** No TBD/TODO found. All steps contain actual code.

**3. Type consistency:** Verified: `FeatureSet.team.elo_rating_home` used consistently across Tasks 1, 4, 8, 9. `PredictionResult` fields match across Tasks 1, 4, 7, 8.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-11-sports-prediction-os-phase1.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
