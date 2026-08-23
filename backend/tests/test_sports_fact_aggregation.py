"""Each real-world occurrence must be counted once, whatever grain reports it.

The defect these cover: a bundle carrying both per-card `discipline` rows and
the per-match `home_red_cards`/`away_red_cards` total reported every card twice,
so "at least 8 red cards" settled YES at confidence 100 on four actual cards.
Every test here states the real-world count in its own name or body, so a future
change that reintroduces summing across grains fails on the number, not on a
shape.
"""

import unittest

from app.services.sports_fact_aggregation import red_card_total, total_goals
from app.services.sports_fact_service import WORLD_CUP_TOURNAMENT


def _card(fact_id: str, match_id: str, **extra):
    fact = {
        "fact_id": fact_id,
        "kind": "discipline",
        "tournament": WORLD_CUP_TOURNAMENT,
        "match_id": match_id,
        "status": "red_card",
        "red_cards": 1,
    }
    fact.update(extra)
    return fact


def _match(fact_id: str, match_id: str, **extra):
    fact = {
        "fact_id": fact_id,
        "kind": "match_result",
        "tournament": WORLD_CUP_TOURNAMENT,
        "match_id": match_id,
        "status": "finished",
    }
    fact.update(extra)
    return fact


class RedCardGrainTests(unittest.TestCase):
    def test_per_card_and_per_match_grain_for_one_match_counts_each_card_once(self):
        # One real match, two real red cards, reported at both grains.
        facts = [
            _card("c1", "m1", team="Home", player="P1", minute="70"),
            _card("c2", "m1", team="Away", player="P2", minute="81"),
            _match("wc2026:match:m1", "m1", red_cards=2),
        ]
        total, counted = red_card_total(facts)
        self.assertEqual(total, 2.0)
        self.assertEqual({fact["fact_id"] for fact in counted}, {"c1", "c2"})

    def test_four_matches_one_card_each_does_not_reach_the_eight_threshold(self):
        # The exact shape that settled YES on 4 cards against a threshold of 8.
        facts = []
        for i in range(1, 5):
            facts.append(_card(f"c{i}", f"m{i}", player=f"P{i}", minute="70"))
            facts.append(_match(f"wc2026:match:m{i}", f"m{i}", red_cards=1))
        total, _counted = red_card_total(facts)
        self.assertEqual(total, 4.0)
        self.assertLess(total, 8.0)

    def test_same_match_from_two_sources_is_not_counted_twice(self):
        # One real match with 2 cards, imported from two feeds: distinct
        # fact_ids, so the store keeps both rows.
        facts = [
            _match("sports:wc:match_result:aaa", "m1", red_cards=2, source="official_csv"),
            _match("sports:wc:match_result:bbb", "m1", red_cards=2, source="api_football"),
        ]
        total, counted = red_card_total(facts)
        self.assertEqual(total, 2.0)
        self.assertEqual(len(counted), 1)

    def test_successive_live_snapshots_of_one_match_take_the_highest(self):
        facts = [
            {
                "fact_id": "s1", "kind": "match_state", "match_id": "m1",
                "tournament": WORLD_CUP_TOURNAMENT, "status": "live", "red_cards": 1,
            },
            {
                "fact_id": "s2", "kind": "match_state", "match_id": "m1",
                "tournament": WORLD_CUP_TOURNAMENT, "status": "live", "red_cards": 2,
            },
            _match("wc2026:match:m1", "m1", red_cards=2),
        ]
        total, _counted = red_card_total(facts)
        self.assertEqual(total, 2.0)

    def test_per_card_rows_win_when_they_exceed_a_stale_match_total(self):
        # A match total observed at half time, with a later card row.
        facts = [
            _match("wc2026:match:m1", "m1", red_cards=1),
            _card("c1", "m1", player="P1", minute="20"),
            _card("c2", "m1", player="P2", minute="88"),
        ]
        total, counted = red_card_total(facts)
        self.assertEqual(total, 2.0)
        self.assertEqual({fact["fact_id"] for fact in counted}, {"c1", "c2"})

    def test_suspension_fact_carrying_red_cards_is_not_counted(self):
        # A suspension is a consequence of a card another fact already reports.
        facts = [
            _card("c1", "m1", player="P1"),
            {
                "fact_id": "susp", "kind": "suspension", "match_id": "m1",
                "tournament": WORLD_CUP_TOURNAMENT, "player": "P1", "red_cards": 1,
            },
        ]
        total, counted = red_card_total(facts)
        self.assertEqual(total, 1.0)
        self.assertEqual([fact["fact_id"] for fact in counted], ["c1"])

    def test_tournament_total_supersedes_a_partial_per_match_tally(self):
        facts = [
            _card("c1", "m1", player="P1"),
            {
                "fact_id": "ts", "kind": "tournament_status",
                "tournament": WORLD_CUP_TOURNAMENT, "status": "complete", "red_cards": 9,
            },
        ]
        total, counted = red_card_total(facts)
        self.assertEqual(total, 9.0)
        self.assertEqual([fact["fact_id"] for fact in counted], ["ts"])

    def test_per_match_tally_wins_over_a_stale_tournament_total(self):
        facts = [
            _match("m1", "m1", red_cards=4),
            _match("m2", "m2", red_cards=5),
            {
                "fact_id": "ts", "kind": "tournament_status",
                "tournament": WORLD_CUP_TOURNAMENT, "status": "in_progress", "red_cards": 3,
            },
        ]
        total, counted = red_card_total(facts)
        self.assertEqual(total, 9.0)
        self.assertEqual({fact["fact_id"] for fact in counted}, {"m1", "m2"})

    def test_facts_without_a_match_id_are_each_counted_once(self):
        # Nothing ties these to a match, so they cannot be reconciled; the
        # operator-imported aggregate still has to count.
        facts = [
            {
                "fact_id": "manual", "kind": "discipline",
                "tournament": WORLD_CUP_TOURNAMENT, "red_cards": 6, "source": "manual",
            },
        ]
        total, counted = red_card_total(facts)
        self.assertEqual(total, 6.0)
        self.assertEqual([fact["fact_id"] for fact in counted], ["manual"])

    def test_no_card_facts_totals_zero_with_no_evidence(self):
        total, counted = red_card_total([_match("m1", "m1", score={"home": 1, "away": 0})])
        self.assertEqual(total, 0.0)
        self.assertEqual(counted, [])

    def test_negative_and_unparsable_card_counts_are_ignored(self):
        facts = [
            _card("bad", "m1", red_cards="not a number"),
            _card("neg", "m2", red_cards=-3),
            _card("ok", "m3"),
        ]
        total, counted = red_card_total(facts)
        self.assertEqual(total, 1.0)
        self.assertEqual([fact["fact_id"] for fact in counted], ["ok"])


class TotalGoalsGrainTests(unittest.TestCase):
    def test_same_match_from_two_sources_counts_its_goals_once(self):
        facts = [
            _match("sports:wc:match_result:aaa", "m1", score={"home": 3, "away": 2}),
            _match("sports:wc:match_result:bbb", "m1", score={"home": 3, "away": 2}),
        ]
        total, counted = total_goals(facts)
        self.assertEqual(total, 5.0)
        self.assertEqual(len(counted), 1)

    def test_distinct_matches_are_added(self):
        facts = [
            _match("m1", "m1", home_goals=2, away_goals=1),
            _match("m2", "m2", home_goals=0, away_goals=0),
            _match("m3", "m3", score={"home": 1, "away": 4}),
        ]
        total, _counted = total_goals(facts)
        self.assertEqual(total, 8.0)

    def test_goalless_match_is_counted_as_evidence_not_skipped(self):
        # `home_goals or home_score or score["home"] or 0` treated a real 0 as
        # missing and fell through; a 0-0 match is a fact, not an absence.
        total, counted = total_goals([_match("m1", "m1", home_goals=0, away_goals=0)])
        self.assertEqual(total, 0.0)
        self.assertEqual([fact["fact_id"] for fact in counted], ["m1"])

    def test_zero_home_goals_does_not_fall_through_to_the_score_mapping(self):
        facts = [_match("m1", "m1", home_goals=0, away_goals=2, score={"home": 5, "away": 5})]
        total, _counted = total_goals(facts)
        self.assertEqual(total, 2.0)

    def test_partial_snapshot_does_not_lower_a_finished_score(self):
        facts = [
            _match("live", "m1", status="live", score={"home": 1, "away": 0}),
            _match("final", "m1", status="finished", score={"home": 3, "away": 2}),
        ]
        total, counted = total_goals(facts)
        self.assertEqual(total, 5.0)
        self.assertEqual([fact["fact_id"] for fact in counted], ["final"])

    def test_match_without_any_score_field_is_not_counted(self):
        total, counted = total_goals([_match("m1", "m1", red_cards=2)])
        self.assertEqual(total, 0.0)
        self.assertEqual(counted, [])

    def test_per_card_discipline_facts_never_contribute_goals(self):
        facts = [_card("c1", "m1"), _match("m1", "m1", home_goals=1, away_goals=1)]
        total, counted = total_goals(facts)
        self.assertEqual(total, 2.0)
        self.assertEqual([fact["fact_id"] for fact in counted], ["m1"])


if __name__ == "__main__":
    unittest.main()
