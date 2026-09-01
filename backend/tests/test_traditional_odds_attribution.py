# backend/tests/test_traditional_odds_attribution.py
"""The odds fetch must not spend quota on a run that cannot attribute anything.

``_match_odds_to_match`` needs team names parsed *out of the match_id*
(``{comp}-{YYYYMMDD}-{HOME}-{AWAY}``). Every production writer builds
``{sport}-{provider_game_id}``, which carries no team name: measured 2026-09-01,
all 18,717 rows in ``kernel_match_fixtures`` bail out. The dated format exists only
in test fixtures, and the one scheduler test that went near this path patched
``_match_odds_to_match`` out entirely, so the real matcher was never exercised.

``fetch_all_sports_odds()`` is a ``/sports`` discovery call plus one
``/sports/{key}/odds`` per sport key, metered against ``x-requests-remaining``, so
the old code bought N+1 paid requests and then reported ``captured=0`` as
``success``.
"""
import pytest
from unittest.mock import AsyncMock, patch

from app.core.scheduler import _job_fetch_traditional_odds, _odds_lookup_key


class TestOddsLookupKey:
    """The shared predicate: can this match_id be attributed at all?"""

    @pytest.mark.parametrize("match_id", [
        "nba-21716138",        # real live id (balldontlie game id)
        "mlb-824514",          # real live id (statsapi game pk)
        "nhl-2026010012",      # real live id (nhle game id)
        "bundesliga-540406",   # real live id (football-data fixture id)
        "epl-537421",
        "lol-abc123",          # lol is not even an Odds API competition
    ])
    def test_real_production_id_formats_are_not_attributable(self, match_id):
        assert _odds_lookup_key(match_id) is None

    @pytest.mark.parametrize("match_id,expected", [
        ("nba-20250101-LAL-BOS", "basketball_nba"),
        ("nba-2024-01-01-lakers-celtics", "basketball_nba"),
        ("wc-2026-06-13-ARG-FRA", "soccer_fifa_world_cup"),
        ("epl-20250101-ARS-CHE", "soccer_epl"),
    ])
    def test_the_dated_format_is_attributable(self, match_id, expected):
        """The rival configuration: the predicate must not just always say None."""
        assert _odds_lookup_key(match_id) == expected

    def test_an_unknown_competition_is_not_attributable(self):
        assert _odds_lookup_key("kabaddi-20250101-AAA-BBB") is None

    @pytest.mark.parametrize("match_id", [
        "nba-20250101--BOS",     # empty home token
        "nba-20250101-LAL-",     # empty away token
        "nba-20250101- - ",      # whitespace-only tokens
    ])
    def test_an_empty_team_token_is_not_attributable(self, match_id):
        """An empty token normalizes to "" and would equal a fixture missing
        home_team/away_team, attributing another fixture's odds to this match."""
        assert _odds_lookup_key(match_id) is None


@pytest.fixture
def job_env(monkeypatch):
    """Enable the job and capture its ledger entry."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "PHASE7_SPORT_MARKET_BRIDGE_ENABLED", True)
    monkeypatch.setattr(settings, "ODDS_API_ENABLED", True)
    monkeypatch.setattr(settings, "PHASE10_REALTIME_PUSH_ENABLED", False)
    calls = {"finish": []}

    def fake_finish(run_id, status, *, result=None, error=None, exc=None):
        calls["finish"].append({"status": status, "result": result, "error": error})

    with patch("app.core.scheduler._start_run", return_value="run-odds"), \
         patch("app.core.scheduler._finish_run", side_effect=fake_finish):
        yield calls


def _patch_stores(match_ids):
    """Patch the two stores the job builds, returning the fetch spy."""
    link_store = AsyncMock()
    link_store.get_matches_with_verified_links = lambda: list(match_ids)
    return link_store


@pytest.mark.asyncio
async def test_no_attributable_match_skips_the_paid_fetch(job_env):
    """The measured live case: linked ids exist, none can be attributed."""
    link_store = _patch_stores(["nba-21716138", "mlb-824514", "bundesliga-540406"])
    fetch = AsyncMock(return_value={})

    with patch("app.kernel.kernel_db.init_kernel_db"), \
         patch("app.kernel.traditional_odds_store.TraditionalOddsStore"), \
         patch(
             "app.kernel.sport_market_link_store.SportMarketLinkStore",
             return_value=link_store,
         ), \
         patch("app.services.odds_api_service.fetch_all_sports_odds", fetch):
        await _job_fetch_traditional_odds()

    # The whole point: no quota spent.
    fetch.assert_not_awaited()
    final = job_env["finish"][-1]
    assert final["status"] == "failed"
    assert final["result"]["matches_total"] == 3
    assert final["result"]["attributable"] == 0
    assert final["result"]["captured"] == 0
    assert final["result"]["skipped_reason"] == "no_match_id_yields_team_tokens"


@pytest.mark.asyncio
async def test_an_attributable_match_still_fetches(job_env):
    """Rival configuration: the guard must not block every run.

    Without this, hardcoding the skip would pass the test above.
    """
    link_store = _patch_stores(["nba-20250101-LAL-BOS"])
    fetch = AsyncMock(return_value={})

    with patch("app.kernel.kernel_db.init_kernel_db"), \
         patch("app.kernel.traditional_odds_store.TraditionalOddsStore"), \
         patch(
             "app.kernel.sport_market_link_store.SportMarketLinkStore",
             return_value=link_store,
         ), \
         patch("app.services.odds_api_service.fetch_all_sports_odds", fetch):
        await _job_fetch_traditional_odds()

    fetch.assert_awaited_once()
    final = job_env["finish"][-1]
    assert final["status"] == "success"
    assert final["result"]["attributable"] == 1


@pytest.mark.asyncio
async def test_a_mixed_set_fetches_and_reports_both_counts(job_env):
    link_store = _patch_stores([
        "nba-21716138",            # not attributable
        "nba-20250101-LAL-BOS",    # attributable
    ])
    fetch = AsyncMock(return_value={})

    with patch("app.kernel.kernel_db.init_kernel_db"), \
         patch("app.kernel.traditional_odds_store.TraditionalOddsStore"), \
         patch(
             "app.kernel.sport_market_link_store.SportMarketLinkStore",
             return_value=link_store,
         ), \
         patch("app.services.odds_api_service.fetch_all_sports_odds", fetch):
        await _job_fetch_traditional_odds()

    fetch.assert_awaited_once()
    final = job_env["finish"][-1]
    assert final["status"] == "success"
    assert final["result"]["matches_total"] == 2
    assert final["result"]["attributable"] == 1
