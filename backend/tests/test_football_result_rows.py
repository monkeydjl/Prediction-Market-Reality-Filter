"""P1-E9: football scores reached the fixture row but never a result row.

Measured on the live kernel DB before the fix (read-only URI, 2026-08-30):

  competition   fixtures   scored fixtures   kernel_match_results rows
  epl                760               380                           0
  ligue1             612               306                           0
  bundesliga         306               306                           0
  ucl                189               189                           0
  laliga             380                 0                           0
  seriea             380                 0                           0
  mlb               7701              6803                        6803
  nba               3965              3965                        3965
  nhl               4424              3014                        3014

1181 finished football fixtures held real scores against **zero** result rows,
while the three binary sports had one row per scored fixture. Three production
readers join ``kernel_match_results`` and so came back empty for football:

* ``fetch_outcome`` in all three football adapters -- checked through the
  production door on six real finished fixtures (``ucl-552096`` 5-4,
  ``epl-538155`` 2-1, ``epl-538156`` 0-3, ``epl-538157`` 1-2, ``epl-538158``
  1-1, ``epl-538159`` 2-0): ``KernelMatchResult=None`` and
  ``MatchOutcome=None`` for every one, so no football prediction could settle.
* ``team_form_from_kernel`` -- ``None`` for every club, which also removes the
  ``form_home``/``form_away``, ``rest_days_*`` and the xG goals-per-game proxy,
  because it is the only fallback for ``home_stats`` on the club tracks.
* ``h2h_meetings_from_kernel`` -- 0 meetings on every pairing.

``enrich_situational_features`` run on four real club fixtures produced only
schedule-density keys: no form, no rest, no xG, no h2h. Cost through the real
``FootballFeatureBuilder`` + ``FootballMultiFactorEngine`` (Elo 1900/1700
measured, no odds):

  state                                  confidence   data_completeness   home_win
  Elo only (today's club reality)            0.5878              0.0        0.5903
  + form                                     0.5840           0.0818        0.5553
  + h2h                                      0.5971           0.0818        0.5822
  + xG goals proxy                           0.5866           0.0818        0.5607
  + rest                                     0.5903           0.0818        0.5682
  all four restored                          0.6182           0.3545        0.5345

so the dead join costs -0.0304 confidence, holds ``data_completeness`` at
**0.0**, and moves ``home_win`` by 5.58pp.

The copy mechanism already existed -- ``backfill_results_from_fixtures`` -- with
its scope hardcoded to ``["nba", "mlb", "nhl"]`` in four places. Two hazards
made "add football to the list" wrong on its own:

* ``_binary_outcome`` had no draw branch and returned ``away_win`` for a level
  score. 287 of the 1181 finished football fixtures are draws (24.3%: epl 104,
  bundesliga 75, ligue1 75, ucl 33), so every one would have been stored as the
  wrong side winning.
* the loop copied any *scored* fixture and then forced ``status="finished"``,
  but ``football_data_client.parse_fixture`` reads ``score.fullTime``, which
  Football-Data.org also fills while a match is IN_PLAY. Copying that publishes
  a partial score as final. Zero scored fixtures are currently non-finished in
  any sport, so requiring finality narrows a reachable window rather than
  dropping live rows.

``seed_elo_ratings`` is deliberately *not* widened: ``seed_elo_from_games``
scores a game as ``home_score > away_score`` with no third bucket, and football
Elo already comes from ClubElo as a measured source.
"""
from __future__ import annotations

import ast
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.historical_data_ingestor import (
    BACKFILLABLE_COMPETITIONS,
    ELO_SEEDABLE_COMPETITIONS,
    FINAL_FIXTURE_STATUS,
    CompetitionMeta,
    HistoricalDataIngestor,
    _SPORT_META,
)
from app.sports._shared.match_outcome import (
    OUTCOME_AWAY_WIN,
    OUTCOME_DRAW,
    OUTCOME_HOME_WIN,
    outcome_from_scores,
)

#: Package whose ``save_fixture`` call sites decide what football writes into
#: ``KernelMatchFixture.competition``. Scanned rather than restated.
_FOOTBALL_ADAPTERS = Path("app/sports/football/adapters")


class MatchOutcomeRuleTests(unittest.TestCase):
    """The draw rule is a parameter, so both callers state which one they use."""

    def test_a_level_score_is_a_draw_when_the_competition_allows_one(self) -> None:
        self.assertEqual(
            outcome_from_scores(1, 1, allow_draw=True), OUTCOME_DRAW,
        )

    def test_a_level_score_is_an_away_win_when_it_does_not(self) -> None:
        # Not an oversight being preserved: seed_elo_from_games scores
        # home_score > away_score, so the binary sports' replay has no third
        # bucket. Asserting the exact token stops a well-meaning change from
        # passing allow_draw=True everywhere.
        self.assertEqual(
            outcome_from_scores(1, 1, allow_draw=False), OUTCOME_AWAY_WIN,
        )

    def test_the_draw_rule_does_not_touch_decided_scores(self) -> None:
        for allow in (True, False):
            with self.subTest(allow_draw=allow):
                self.assertEqual(
                    outcome_from_scores(3, 1, allow_draw=allow), OUTCOME_HOME_WIN,
                )
                self.assertEqual(
                    outcome_from_scores(0, 2, allow_draw=allow), OUTCOME_AWAY_WIN,
                )

    def test_zero_zero_is_the_football_case_that_used_to_read_as_away_win(self) -> None:
        self.assertEqual(outcome_from_scores(0, 0, allow_draw=True), OUTCOME_DRAW)
        self.assertNotEqual(
            outcome_from_scores(0, 0, allow_draw=True),
            outcome_from_scores(0, 0, allow_draw=False),
        )

    def test_allow_draw_is_keyword_only(self) -> None:
        # The two call sites disagree about the rule, so a positional third
        # argument would let one silently inherit the other's.
        with self.assertRaises(TypeError):
            outcome_from_scores(1, 1, True)  # type: ignore[misc]


class TheTwoScopesArePartitionedTests(unittest.TestCase):
    """Both scopes are derived from one table, so they cannot drift apart."""

    def test_the_declaration_is_not_empty_and_holds_both_kinds(self) -> None:
        # Denominator guard: every assertion below is vacuous against an empty
        # or single-kind table.
        self.assertGreaterEqual(len(_SPORT_META), 9)
        kinds = {meta.draws for meta in _SPORT_META.values()}
        self.assertEqual(kinds, {True, False}, "table must hold both draw rules")
        for code, meta in _SPORT_META.items():
            with self.subTest(code=code):
                self.assertIsInstance(meta, CompetitionMeta)

    def test_backfillable_is_exactly_the_declared_table(self) -> None:
        self.assertEqual(BACKFILLABLE_COMPETITIONS, frozenset(_SPORT_META))

    def test_elo_seedable_is_exactly_the_non_draw_half(self) -> None:
        expected = frozenset(
            code for code, meta in _SPORT_META.items() if not meta.draws
        )
        self.assertEqual(ELO_SEEDABLE_COMPETITIONS, expected)

    def test_the_two_scopes_partition_the_table_on_the_draw_rule(self) -> None:
        drawing = BACKFILLABLE_COMPETITIONS - ELO_SEEDABLE_COMPETITIONS
        self.assertEqual(
            drawing,
            frozenset(code for code, meta in _SPORT_META.items() if meta.draws),
        )
        self.assertTrue(drawing, "some competition must allow draws")
        self.assertTrue(
            ELO_SEEDABLE_COMPETITIONS <= BACKFILLABLE_COMPETITIONS,
            "a seedable competition must also be backfillable",
        )

    def test_football_is_backfillable_but_never_seedable(self) -> None:
        football = {
            code for code, meta in _SPORT_META.items() if meta.sport == "football"
        }
        self.assertTrue(football, "no football competition is declared")
        self.assertTrue(football <= BACKFILLABLE_COMPETITIONS)
        self.assertEqual(
            football & ELO_SEEDABLE_COMPETITIONS,
            set(),
            "football Elo comes from ClubElo; seeding would overwrite measured "
            "ratings with self-computed ones",
        )
        for code in football:
            with self.subTest(code=code):
                self.assertTrue(_SPORT_META[code].draws)

    def test_the_three_binary_sports_kept_both_capabilities(self) -> None:
        for code in ("nba", "mlb", "nhl"):
            with self.subTest(code=code):
                self.assertIn(code, BACKFILLABLE_COMPETITIONS)
                self.assertIn(code, ELO_SEEDABLE_COMPETITIONS)
                self.assertFalse(_SPORT_META[code].draws)

    def test_finality_is_the_status_the_adapters_write(self) -> None:
        self.assertEqual(FINAL_FIXTURE_STATUS, "finished")


class EveryCompetitionFootballWritesIsBackfillableTests(unittest.TestCase):
    """Scan the adapters' own call sites instead of restating the six codes.

    A seventh league added to ``league_adapter`` gets a ``LeagueConfig`` and a
    ``save_fixture`` call; nothing else would notice that its results are
    unreachable, which is exactly how the first six got here.
    """

    @staticmethod
    def _adapter_sources() -> dict[str, ast.Module]:
        trees = {}
        for path in sorted(_FOOTBALL_ADAPTERS.glob("*.py")):
            trees[path.name] = ast.parse(path.read_text(encoding="utf-8"))
        return trees

    def test_the_scan_finds_the_call_sites_it_is_supposed_to_check(self) -> None:
        trees = self._adapter_sources()
        self.assertGreaterEqual(len(trees), 5, "adapter package did not parse")
        calls = [
            node
            for tree in trees.values()
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "save_fixture"
        ]
        self.assertGreaterEqual(
            len(calls), 3, "expected save_fixture calls in epl/ucl/league adapters",
        )

    def test_every_literal_competition_passed_to_save_fixture_is_declared(self) -> None:
        found: set[str] = set()
        for name, tree in self._adapter_sources().items():
            for node in ast.walk(tree):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "save_fixture"
                ):
                    continue
                if len(node.args) < 2:
                    continue
                second = node.args[1]
                if isinstance(second, ast.Constant) and isinstance(second.value, str):
                    found.add(second.value)
        self.assertTrue(found, "no literal competition code found at a call site")
        missing = found - BACKFILLABLE_COMPETITIONS
        self.assertEqual(
            missing,
            set(),
            f"save_fixture writes {sorted(missing)} but the backfill cannot reach them",
        )

    def test_every_league_config_code_is_declared(self) -> None:
        # league_adapter passes self._config.code, which no literal scan sees.
        codes: set[str] = set()
        tree = ast.parse(
            (_FOOTBALL_ADAPTERS / "league_adapter.py").read_text(encoding="utf-8"),
        )
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "LeagueConfig"
            ):
                continue
            for kw in node.keywords:
                if kw.arg == "code" and isinstance(kw.value, ast.Constant):
                    codes.add(str(kw.value.value))
        self.assertGreaterEqual(
            len(codes), 4, "expected laliga/bundesliga/seriea/ligue1 configs",
        )
        missing = codes - BACKFILLABLE_COMPETITIONS
        self.assertEqual(
            missing,
            set(),
            f"LeagueConfig declares {sorted(missing)} with no backfill scope",
        )

    def test_the_scan_and_the_declaration_agree_on_the_football_half(self) -> None:
        declared = {
            code for code, meta in _SPORT_META.items() if meta.sport == "football"
        }
        scanned: set[str] = set()
        for _name, tree in self._adapter_sources().items():
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "save_fixture"
                    and len(node.args) >= 2
                    and isinstance(node.args[1], ast.Constant)
                ):
                    scanned.add(str(node.args[1].value))
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "LeagueConfig"
                ):
                    for kw in node.keywords:
                        if kw.arg == "code" and isinstance(kw.value, ast.Constant):
                            scanned.add(str(kw.value.value))
        self.assertEqual(
            scanned,
            declared,
            "the adapters' competition codes and the declared football half "
            "must be the same set",
        )


class _IsolatedKernelDb(unittest.TestCase):
    """Base class: every DB assertion below runs against a temp kernel DB.

    ``init_kernel_db`` is given the path explicitly rather than through a
    setting, so the engine cannot be derived from a production default, and the
    resolved path is asserted to sit under the temp dir before anything writes.
    """

    def setUp(self) -> None:
        from app.kernel import kernel_db

        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._tmp.name) / "kernel_test.db")
        kernel_db.close_kernel_db()
        kernel_db.init_kernel_db(self.db_path)
        engine = kernel_db._engine
        self.assertIsNotNone(engine, "init_kernel_db left the engine unset")
        resolved = Path(str(engine.url.database)).resolve()  # type: ignore[union-attr]
        self.assertEqual(
            resolved,
            Path(self.db_path).resolve(),
            f"kernel engine is not on the temp DB: {resolved}",
        )
        self.ingestor = HistoricalDataIngestor()

    def tearDown(self) -> None:
        from app.kernel import kernel_db

        kernel_db.close_kernel_db()
        self._tmp.cleanup()

    def add_fixture(
        self,
        match_id: str,
        competition: str,
        home_score: int | None,
        away_score: int | None,
        *,
        status: str = "finished",
        home_team: str = "Home FC",
        away_team: str = "Away FC",
        kickoff: datetime | None = None,
    ) -> None:
        from app.kernel.kernel_db import KernelMatchFixture, get_kernel_session

        now = datetime.now(timezone.utc)
        session = get_kernel_session()
        try:
            session.add(
                KernelMatchFixture(
                    match_id=match_id,
                    competition=competition,
                    season="2025-26",
                    home_team=home_team,
                    away_team=away_team,
                    kickoff_utc=kickoff or (now - timedelta(days=7)),
                    stage="regular_season",
                    status=status,
                    home_score=home_score,
                    away_score=away_score,
                    created_at=now,
                    updated_at=now,
                ),
            )
            session.commit()
        finally:
            session.close()

    def result_row(self, match_id: str):  # noqa: ANN201 - SQLAlchemy row
        from app.kernel.kernel_db import KernelMatchResult, get_kernel_session

        session = get_kernel_session()
        try:
            return session.get(KernelMatchResult, match_id)
        finally:
            session.close()


class ADrawIsStoredAsADrawTests(_IsolatedKernelDb):
    """The 24.3% of finished football fixtures that ended level."""

    def test_a_football_draw_backfills_as_draw(self) -> None:
        self.add_fixture("epl-d1", "epl", 1, 1)
        out = self.ingestor.backfill_results_from_fixtures(sport="epl")
        self.assertEqual(out["results"], 1)
        self.assertEqual(out["errors"], [])
        row = self.result_row("epl-d1")
        self.assertIsNotNone(row)
        self.assertEqual(row.outcome, OUTCOME_DRAW)

    def test_a_goalless_football_draw_backfills_as_draw(self) -> None:
        self.add_fixture("epl-d0", "epl", 0, 0)
        self.ingestor.backfill_results_from_fixtures(sport="epl")
        self.assertEqual(self.result_row("epl-d0").outcome, OUTCOME_DRAW)

    def test_football_wins_keep_the_shared_tokens(self) -> None:
        self.add_fixture("epl-h", "epl", 3, 1)
        self.add_fixture("epl-a", "epl", 0, 2)
        self.ingestor.backfill_results_from_fixtures(sport="epl")
        self.assertEqual(self.result_row("epl-h").outcome, OUTCOME_HOME_WIN)
        self.assertEqual(self.result_row("epl-a").outcome, OUTCOME_AWAY_WIN)
        self.assertEqual(self.result_row("epl-h").home_score, 3)
        self.assertEqual(self.result_row("epl-a").away_score, 2)

    def test_every_football_competition_stores_a_draw_as_a_draw(self) -> None:
        football = sorted(
            code for code, meta in _SPORT_META.items() if meta.sport == "football"
        )
        self.assertEqual(len(football), 6, "expected six football competitions")
        for code in football:
            self.add_fixture(f"{code}-lvl", code, 2, 2)
        out = self.ingestor.backfill_results_from_fixtures()
        self.assertEqual(out["errors"], [])
        for code in football:
            with self.subTest(competition=code):
                row = self.result_row(f"{code}-lvl")
                self.assertIsNotNone(row, f"{code} produced no result row")
                self.assertEqual(row.outcome, OUTCOME_DRAW)

    def test_a_binary_sport_still_stores_a_level_score_as_away_win(self) -> None:
        # The asymmetry is the point: NHL/NBA/MLB rows feed a binary Elo replay.
        self.add_fixture("nhl-lvl", "nhl", 2, 2)
        self.ingestor.backfill_results_from_fixtures(sport="nhl")
        self.assertEqual(self.result_row("nhl-lvl").outcome, OUTCOME_AWAY_WIN)

    def test_the_backfill_is_idempotent_for_football(self) -> None:
        self.add_fixture("ucl-d", "ucl", 1, 1)
        first = self.ingestor.backfill_results_from_fixtures(sport="ucl")
        second = self.ingestor.backfill_results_from_fixtures(sport="ucl")
        self.assertEqual(first["results"], 1)
        self.assertEqual(second["results"], 0)
        self.assertEqual(second["updated"], 0)
        self.assertEqual(self.result_row("ucl-d").outcome, OUTCOME_DRAW)

    def test_a_stale_wrong_outcome_is_corrected_on_re_run(self) -> None:
        # What an operator who ran a pre-fix backfill would be left holding.
        from app.kernel.kernel_db import KernelMatchResult, get_kernel_session

        self.add_fixture("epl-stale", "epl", 1, 1)
        now = datetime.now(timezone.utc)
        session = get_kernel_session()
        try:
            session.add(
                KernelMatchResult(
                    match_id="epl-stale",
                    home_score=1,
                    away_score=1,
                    outcome=OUTCOME_AWAY_WIN,
                    finished_at=now,
                    created_at=now,
                ),
            )
            session.commit()
        finally:
            session.close()
        out = self.ingestor.backfill_results_from_fixtures(sport="epl")
        self.assertEqual(out["updated"], 1)
        self.assertEqual(self.result_row("epl-stale").outcome, OUTCOME_DRAW)


class TheDefaultScopeReachesFootballTests(_IsolatedKernelDb):
    """``sport=None`` used to mean "the three binary sports"."""

    def test_no_argument_covers_football_and_the_binary_sports(self) -> None:
        self.add_fixture("epl-def", "epl", 2, 0)
        self.add_fixture("nba-def", "nba", 101, 99)
        out = self.ingestor.backfill_results_from_fixtures()
        self.assertEqual(out["errors"], [])
        self.assertIsNotNone(self.result_row("epl-def"))
        self.assertIsNotNone(self.result_row("nba-def"))
        self.assertIn("epl", out["sports"])
        self.assertEqual(
            set(out["sports"]),
            set(BACKFILLABLE_COMPETITIONS),
            "the default scope must report every declared competition",
        )

    def test_an_undeclared_competition_is_reported_not_silently_skipped(self) -> None:
        out = self.ingestor.backfill_results_from_fixtures(sport="kabaddi")
        self.assertEqual(out["results"], 0)
        self.assertEqual(out["errors"], ["Unknown sport: kabaddi"])


class OnlyAFinishedFixtureBecomesAResultTests(_IsolatedKernelDb):
    """``parse_fixture`` fills fullTime during IN_PLAY."""

    def test_an_in_play_score_is_not_copied(self) -> None:
        self.add_fixture("epl-live", "epl", 1, 0, status="in_play")
        out = self.ingestor.backfill_results_from_fixtures(sport="epl")
        self.assertEqual(out["results"], 0)
        self.assertIsNone(
            self.result_row("epl-live"),
            "a partial score must not be published as a final result",
        )

    def test_the_same_fixture_is_copied_once_it_is_final(self) -> None:
        from app.kernel.kernel_db import KernelMatchFixture, get_kernel_session

        self.add_fixture("epl-live2", "epl", 1, 1, status="in_play")
        self.assertEqual(
            self.ingestor.backfill_results_from_fixtures(sport="epl")["results"], 0,
        )
        session = get_kernel_session()
        try:
            fix = session.get(KernelMatchFixture, "epl-live2")
            fix.home_score = 2
            fix.away_score = 1
            fix.status = "finished"
            session.commit()
        finally:
            session.close()
        out = self.ingestor.backfill_results_from_fixtures(sport="epl")
        self.assertEqual(out["results"], 1)
        row = self.result_row("epl-live2")
        self.assertEqual((row.home_score, row.away_score), (2, 1))
        self.assertEqual(row.outcome, OUTCOME_HOME_WIN)

    def test_a_scored_fixture_with_no_status_is_not_copied(self) -> None:
        self.add_fixture("epl-nostatus", "epl", 3, 0, status=None)
        self.assertEqual(
            self.ingestor.backfill_results_from_fixtures(sport="epl")["results"], 0,
        )
        self.assertIsNone(self.result_row("epl-nostatus"))

    def test_a_finished_fixture_without_scores_is_not_copied(self) -> None:
        self.add_fixture("epl-noscore", "epl", None, None)
        self.assertEqual(
            self.ingestor.backfill_results_from_fixtures(sport="epl")["results"], 0,
        )
        self.assertIsNone(self.result_row("epl-noscore"))

    def test_the_finality_filter_does_not_change_the_binary_sports(self) -> None:
        # Measured: zero scored fixtures in any sport are non-finished today, so
        # the narrowed filter must copy the binary rows exactly as before.
        self.add_fixture("mlb-f", "mlb", 5, 3)
        self.add_fixture("nhl-f", "nhl", 2, 1)
        self.add_fixture("nba-f", "nba", 110, 100)
        out = self.ingestor.backfill_results_from_fixtures()
        self.assertEqual(out["results"], 3)
        for mid in ("mlb-f", "nhl-f", "nba-f"):
            with self.subTest(match_id=mid):
                self.assertEqual(self.result_row(mid).outcome, OUTCOME_HOME_WIN)


class EloSeedingStaysBinaryTests(_IsolatedKernelDb):
    """Widening the copy must not widen the replay."""

    def elo_rows(self) -> list[str]:
        from app.kernel.kernel_db import KernelEloRating, get_kernel_session

        session = get_kernel_session()
        try:
            return [row.competition for row in session.query(KernelEloRating).all()]
        finally:
            session.close()

    def test_seeding_a_football_competition_is_refused_with_a_reason(self) -> None:
        self.add_fixture("epl-seed", "epl", 1, 1)
        self.ingestor.backfill_results_from_fixtures(sport="epl")
        out = self.ingestor.seed_elo_ratings(sport="epl")
        self.assertEqual(out["teams"], 0)
        self.assertEqual(len(out["errors"]), 1)
        self.assertIn("binary-only", out["errors"][0])
        self.assertIn("epl", out["errors"][0])
        self.assertEqual(
            self.elo_rows(), [], "no Elo row may be written for a draw competition",
        )

    def test_the_default_seed_scope_never_touches_football(self) -> None:
        self.add_fixture("epl-seed2", "epl", 2, 2)
        self.ingestor.backfill_results_from_fixtures()
        out = self.ingestor.seed_elo_ratings()
        self.assertEqual(out["errors"], [])
        self.assertEqual(
            set(out["sports"]) & {"epl", "laliga", "seriea", "bundesliga", "ligue1", "ucl"},
            set(),
        )
        self.assertNotIn("epl", self.elo_rows())

    def test_every_declared_draw_competition_is_refused(self) -> None:
        drawing = sorted(BACKFILLABLE_COMPETITIONS - ELO_SEEDABLE_COMPETITIONS)
        self.assertTrue(drawing)
        for code in drawing:
            with self.subTest(competition=code):
                out = self.ingestor.seed_elo_ratings(sport=code)
                self.assertEqual(out["teams"], 0)
                self.assertTrue(
                    any("binary-only" in err for err in out["errors"]),
                    f"{code} was not refused: {out['errors']}",
                )
        self.assertEqual(self.elo_rows(), [])


class TheThreeDeadReadersComeBackTests(_IsolatedKernelDb):
    """The measured defect: all three joins came back empty for football.

    Each test asserts the empty state *before* the backfill and the populated
    state after, so it cannot pass by finding data that was never missing.
    """

    def seed_a_finished_season(self) -> None:
        """Two clubs, three prior meetings, one of them level."""
        base = datetime(2026, 1, 4, 15, 0, tzinfo=timezone.utc)
        self.add_fixture(
            "epl-h1", "epl", 2, 1, home_team="Arsenal FC", away_team="Everton FC",
            kickoff=base,
        )
        self.add_fixture(
            "epl-h2", "epl", 1, 1, home_team="Everton FC", away_team="Arsenal FC",
            kickoff=base + timedelta(days=30),
        )
        self.add_fixture(
            "epl-h3", "epl", 3, 0, home_team="Arsenal FC", away_team="Everton FC",
            kickoff=base + timedelta(days=60),
        )

    def test_fetch_outcome_returns_none_before_and_an_outcome_after(self) -> None:
        from app.kernel.kernel_db import KernelMatchResult
        from app.sports.football.adapters._shared import (
            build_match_outcome,
            query_result,
        )

        self.add_fixture("epl-set", "epl", 1, 1)
        self.assertIsNone(
            build_match_outcome(query_result("epl-set", KernelMatchResult)),
            "precondition: the result row must be missing before the backfill",
        )
        self.ingestor.backfill_results_from_fixtures(sport="epl")
        outcome = build_match_outcome(query_result("epl-set", KernelMatchResult))
        self.assertIsNotNone(outcome, "settlement is still unreachable")
        self.assertEqual(outcome.match_id, "epl-set")
        self.assertEqual((outcome.home_score, outcome.away_score), (1, 1))
        self.assertEqual(outcome.outcome, OUTCOME_DRAW)

    def test_club_form_is_none_before_and_populated_after(self) -> None:
        from app.sports.football.club_form import team_form_from_kernel

        self.seed_a_finished_season()
        before = datetime(2026, 6, 1, tzinfo=timezone.utc)
        self.assertIsNone(
            team_form_from_kernel("Arsenal FC", competition="epl", before=before),
            "precondition: club form must be dead before the backfill",
        )
        self.ingestor.backfill_results_from_fixtures(sport="epl")
        form = team_form_from_kernel("Arsenal FC", competition="epl", before=before)
        self.assertIsNotNone(form, "club form is still dead")
        self.assertEqual(form["played"], 3)
        self.assertEqual(form["wins"], 2)
        self.assertEqual(form["draws"], 1)
        self.assertEqual(form["losses"], 0)
        self.assertEqual(form["data_source"], "kernel_match_results")
        self.assertIsNotNone(
            form["goals_per_game"], "the xG goals proxy reads this field",
        )

    def test_h2h_has_no_meetings_before_and_three_after(self) -> None:
        from app.sports.football.club_form import h2h_meetings_from_kernel

        self.seed_a_finished_season()
        before = datetime(2026, 6, 1, tzinfo=timezone.utc)
        self.assertEqual(
            h2h_meetings_from_kernel(
                "Arsenal FC", "Everton FC", competition="epl", before=before,
            ),
            [],
            "precondition: h2h must be empty before the backfill",
        )
        self.ingestor.backfill_results_from_fixtures(sport="epl")
        meetings = h2h_meetings_from_kernel(
            "Arsenal FC", "Everton FC", competition="epl", before=before,
        )
        self.assertEqual(len(meetings), 3)
        # From Arsenal's perspective as the current home side: W, D, W.
        self.assertEqual(
            sorted((m.home_goals, m.away_goals) for m in meetings),
            [(1, 1), (2, 1), (3, 0)],
        )

    def test_the_enricher_fills_form_rest_and_the_xg_proxy_after_backfill(self) -> None:
        import datetime as _dt

        from app.kernel.domain import (
            CompetitionIdentity,
            MatchIdentity,
            SeasonIdentity,
            TeamIdentity,
        )
        from app.sports.football.adapters._shared import enrich_situational_features

        self.seed_a_finished_season()
        competition = CompetitionIdentity(code="epl", name="EPL", sport="football")
        season = SeasonIdentity(competition=competition, season_key="2025-26")
        match = MatchIdentity(
            match_id="epl-next",
            season=season,
            stage="regular_season",
            round=None,
            home=TeamIdentity(code="ARS", name="Arsenal FC", competition=competition),
            away=TeamIdentity(code="EVE", name="Everton FC", competition=competition),
            kickoff_utc=_dt.datetime(2026, 6, 1, tzinfo=_dt.timezone.utc),
        )

        def blank() -> dict:
            return {
                "team": {}, "market": {}, "general": {},
                "custom": {}, "environment": {}, "player": {},
            }

        before_raw = blank()
        enrich_situational_features(before_raw, match)
        self.assertIsNone(
            before_raw["team"].get("form_home"),
            "precondition: form must be absent before the backfill",
        )
        self.assertIsNone(before_raw["team"].get("h2h_home_win_rate"))
        self.assertIsNone(before_raw["custom"].get("xg_home"))

        self.ingestor.backfill_results_from_fixtures(sport="epl")
        after_raw = blank()
        enrich_situational_features(after_raw, match)
        self.assertIsNotNone(after_raw["team"].get("form_home"), "form still absent")
        self.assertIsNotNone(after_raw["team"].get("form_away"))
        self.assertIsNotNone(
            after_raw["team"].get("h2h_home_win_rate"), "h2h still absent",
        )
        self.assertIsNotNone(after_raw["general"].get("rest_days_home"))
        self.assertIsNotNone(
            after_raw["custom"].get("xg_home"), "the goals proxy still absent",
        )
        # P1-F5/E14: whatever the value is, it must still name its own origin.
        self.assertEqual(after_raw["custom"].get("xg_source"), "goals_proxy")

    def test_the_engine_gains_completeness_it_could_not_reach_before(self) -> None:
        import datetime as _dt

        from app.kernel.domain import (
            CompetitionIdentity,
            MatchIdentity,
            SeasonIdentity,
            TeamIdentity,
        )
        from app.sports.football.adapters._shared import enrich_situational_features
        from app.sports.football.engines.football_multi_factor_engine import (
            FootballMultiFactorEngine,
        )
        from app.sports.football.feature_builder import FootballFeatureBuilder

        self.seed_a_finished_season()
        competition = CompetitionIdentity(code="epl", name="EPL", sport="football")
        season = SeasonIdentity(competition=competition, season_key="2025-26")
        match = MatchIdentity(
            match_id="epl-next2",
            season=season,
            stage="regular_season",
            round=None,
            home=TeamIdentity(code="ARS", name="Arsenal FC", competition=competition),
            away=TeamIdentity(code="EVE", name="Everton FC", competition=competition),
            kickoff_utc=_dt.datetime(2026, 6, 1, tzinfo=_dt.timezone.utc),
        )
        builder = FootballFeatureBuilder()
        engine = FootballMultiFactorEngine()

        def completeness() -> float:
            raw = {
                "team": {
                    "elo_home": 1900.0,
                    "elo_away": 1700.0,
                    "elo_source": "measured/measured",
                },
                "market": {}, "general": {},
                "custom": {}, "environment": {}, "player": {},
            }
            enrich_situational_features(raw, match)
            result = engine.predict(builder.build(match, raw), match)
            breakdown = (result.betting_analysis or {}).get(
                "confidence_breakdown",
            ) or {}
            return float(breakdown.get("data_completeness") or 0.0)

        before = completeness()
        self.ingestor.backfill_results_from_fixtures(sport="epl")
        after = completeness()
        self.assertEqual(
            before, 0.0, "precondition: club fixtures reached zero completeness",
        )
        self.assertGreater(
            after, before, "the restored evidence changed nothing in the engine",
        )


class SaveFixtureWritesTheResultRowTests(_IsolatedKernelDb):
    """Sync-time write, so the gap does not reopen on the next schedule sync."""

    @staticmethod
    def parsed(
        match_id: str,
        home_score: int | None,
        away_score: int | None,
        status: str,
    ) -> dict:
        return {
            "match_id": match_id,
            "home_team": "Arsenal FC",
            "away_team": "Everton FC",
            "kickoff_utc": datetime(2026, 1, 4, 15, 0, tzinfo=timezone.utc),
            "stage": "regular_season",
            "status": status,
            "venue": "Emirates Stadium",
            "home_score": home_score,
            "away_score": away_score,
        }

    def test_a_finished_draw_gets_a_result_row_at_sync_time(self) -> None:
        from app.sports.football.adapters._shared import save_fixture

        save_fixture(self.parsed("epl-sync1", 1, 1, "finished"), "epl", "2025-26")
        row = self.result_row("epl-sync1")
        self.assertIsNotNone(row, "sync still leaves the result row missing")
        self.assertEqual(row.outcome, OUTCOME_DRAW)
        self.assertEqual((row.home_score, row.away_score), (1, 1))
        self.assertIsNotNone(row.finished_at)

    def test_an_in_play_score_does_not_get_a_result_row(self) -> None:
        from app.sports.football.adapters._shared import save_fixture

        save_fixture(self.parsed("epl-sync2", 1, 0, "in_play"), "epl", "2025-26")
        self.assertIsNone(self.result_row("epl-sync2"))

    def test_a_scheduled_fixture_gets_no_result_row(self) -> None:
        from app.sports.football.adapters._shared import save_fixture

        save_fixture(self.parsed("epl-sync3", None, None, "scheduled"), "epl", "2025-26")
        self.assertIsNone(self.result_row("epl-sync3"))

    def test_a_later_sync_upgrades_the_in_play_row_to_a_final_result(self) -> None:
        from app.sports.football.adapters._shared import save_fixture

        save_fixture(self.parsed("epl-sync4", 1, 0, "in_play"), "epl", "2025-26")
        self.assertIsNone(self.result_row("epl-sync4"))
        save_fixture(self.parsed("epl-sync4", 1, 2, "finished"), "epl", "2025-26")
        row = self.result_row("epl-sync4")
        self.assertIsNotNone(row)
        self.assertEqual((row.home_score, row.away_score), (1, 2))
        self.assertEqual(row.outcome, OUTCOME_AWAY_WIN)

    def test_a_corrected_score_rewrites_the_outcome(self) -> None:
        from app.sports.football.adapters._shared import save_fixture

        save_fixture(self.parsed("epl-sync5", 2, 1, "finished"), "epl", "2025-26")
        self.assertEqual(self.result_row("epl-sync5").outcome, OUTCOME_HOME_WIN)
        save_fixture(self.parsed("epl-sync5", 2, 2, "finished"), "epl", "2025-26")
        self.assertEqual(self.result_row("epl-sync5").outcome, OUTCOME_DRAW)

    def test_the_fixture_row_is_still_written_exactly_as_before(self) -> None:
        from app.kernel.kernel_db import KernelMatchFixture, get_kernel_session
        from app.sports.football.adapters._shared import save_fixture

        save_fixture(self.parsed("epl-sync6", 3, 0, "finished"), "epl", "2025-26")
        session = get_kernel_session()
        try:
            fix = session.get(KernelMatchFixture, "epl-sync6")
        finally:
            session.close()
        self.assertIsNotNone(fix)
        self.assertEqual(fix.competition, "epl")
        self.assertEqual(fix.status, "finished")
        self.assertEqual((fix.home_score, fix.away_score), (3, 0))
        self.assertEqual(fix.venue, "Emirates Stadium")


# --- /backfill-seed validates each step against its own scope ----------------
# pytest-style below, matching tests/test_sport_optimization_routes.py, because
# the route needs the TestClient + settings fixtures.

_ROUTE = "/api/sport-optimization/backfill-seed"
_WRITE_KEY = "test-e9-write-key"


@pytest.fixture
def route_client(monkeypatch):
    from fastapi.testclient import TestClient

    from app.core.config import settings
    from app.main import app

    monkeypatch.setattr(settings, "PHASE9_ACCURACY_SPRINT_ENABLED", True)
    monkeypatch.setattr(settings, "API_WRITE_KEY", _WRITE_KEY)
    return TestClient(app)


@pytest.fixture
def route_headers():
    return {"X-API-Key": _WRITE_KEY}


def test_route_accepts_a_football_backfill(route_client, route_headers):
    with patch(
        "app.api.routes.sport_optimization.HistoricalDataIngestor",
    ) as ingestor_cls:
        instance = ingestor_cls.return_value
        instance.backfill_results_from_fixtures.return_value = {
            "results": 380, "updated": 0, "sports": {}, "errors": [],
        }
        resp = route_client.post(
            _ROUTE,
            json={"sport": "epl", "backfill": True, "seed_elo": False},
            headers=route_headers,
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["backfill"]["results"] == 380
    instance.backfill_results_from_fixtures.assert_called_once_with(sport="epl")


def test_route_refuses_a_football_elo_seed(route_client, route_headers):
    with patch(
        "app.api.routes.sport_optimization.HistoricalDataIngestor",
    ) as ingestor_cls:
        resp = route_client.post(
            _ROUTE,
            json={"sport": "epl", "backfill": False, "seed_elo": True},
            headers=route_headers,
        )
        # Refused before any work is attempted, so nothing can be half-applied.
        ingestor_cls.return_value.seed_elo_ratings.assert_not_called()
    assert resp.status_code == 400
    body = resp.json()["detail"]
    assert "binary-only" in body
    assert "epl" in body


def test_route_refuses_the_default_both_steps_for_football(route_client, route_headers):
    with patch(
        "app.api.routes.sport_optimization.HistoricalDataIngestor",
    ) as ingestor_cls:
        instance = ingestor_cls.return_value
        resp = route_client.post(
            _ROUTE, json={"sport": "ucl"}, headers=route_headers,
        )
        # BackfillSeedRequest defaults both steps to True; the seed half is out
        # of scope for football, so the whole request is rejected rather than
        # backfilling and then silently skipping the seed.
        instance.backfill_results_from_fixtures.assert_not_called()
        instance.seed_elo_ratings.assert_not_called()
    assert resp.status_code == 400


def test_route_still_accepts_a_binary_sport_for_both_steps(route_client, route_headers):
    with patch(
        "app.api.routes.sport_optimization.HistoricalDataIngestor",
    ) as ingestor_cls:
        instance = ingestor_cls.return_value
        instance.backfill_results_from_fixtures.return_value = {
            "results": 1, "updated": 0, "sports": {}, "errors": [],
        }
        instance.seed_elo_ratings.return_value = {
            "teams": 30, "sports": {}, "errors": [],
        }
        resp = route_client.post(
            _ROUTE, json={"sport": "nba"}, headers=route_headers,
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["seed"]["teams"] == 30


def test_route_still_rejects_an_undeclared_sport(route_client, route_headers):
    resp = route_client.post(
        _ROUTE,
        json={"sport": "kabaddi", "backfill": True, "seed_elo": False},
        headers=route_headers,
    )
    assert resp.status_code == 400
    assert "kabaddi" in resp.json()["detail"]


def test_the_cli_offers_every_backfillable_competition():
    """seed_sport_elo.py --sport choices are derived, not restated."""
    source = Path("scripts/seed_sport_elo.py").read_text(encoding="utf-8")
    assert "BACKFILLABLE_COMPETITIONS" in source, (
        "the CLI must take its choices from the ingestor declaration"
    )
    tree = ast.parse(source)
    literal_choice_lists = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.keyword)
        and node.arg == "choices"
        and isinstance(node.value, ast.List)
        and all(isinstance(elt, ast.Constant) for elt in node.value.elts)
    ]
    assert not literal_choice_lists, (
        "a hardcoded choices list would drift from BACKFILLABLE_COMPETITIONS"
    )
