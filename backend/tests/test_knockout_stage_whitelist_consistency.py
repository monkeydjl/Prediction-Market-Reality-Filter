# backend/tests/test_knockout_stage_whitelist_consistency.py
"""Cross-module guards for the ``_KNOCKOUT_STAGES`` whitelists.

Four modules define a *named* ``_KNOCKOUT_STAGES``, and the copies are *not*
identical:

| module | members | compared against |
|---|---|---|
| ``world_cup_prediction_pipeline`` | 4 | ``_normalize_stage(stage)`` (canonical form) |
| ``kernel.engines.elo_odds_engine`` | 6 | raw ``stage.lower().strip()`` |
| ``kernel.engines.gbm_engine`` | 8 | raw ``stage.lower()`` |
| ``kernel.engines.situational_adjust`` | 7 | raw, with ``" " -> "_"`` |

``conclusion_challenge_world_cup_adapter`` keeps a differently *named* set for
the very same ``match.stage`` (``_HIGH_RISK_STAGES`` -- a risk judgement, not an
engine switch), and ``sports.football.engines.football_multi_factor_engine``
*imports* the ``elo_odds_engine`` one rather than copying it. One inline copy
remains (``world_cup_verified_result_correction_service._is_knockout_stage``).
The full census, and why the member counts legitimately differ, is in
``docs/reviews/system-health-audit-2026-09-26.md`` §26-§28.

``gbm_engine`` and the challenge adapter are guarded here even though neither
was a named constant before §28 fixed them: both read the *same* object the
engines do, and both tested only the underscore aliases
(``quarter_final`` / ``semi_final``) -- spellings
``world_cup_match_service.parse_fixture`` never writes -- so QF and SF silently
fell out of the knockout branch (draw allowed; risk rated ``medium``).

This module pins the relations that must hold for that divergence to stay
*benign*: every canonical knockout stage the pipeline can emit has to be
recognised by every engine -- and rated high-risk by the challenge adapter --
and every name in the pipeline's own set has to be producible by its
``_STAGE_MAP`` (a member nothing can ever produce is dead code that silently
never fires).

Note what the partition assertion below does *not* leave open: ``third_place``
is pinned to the non-knockout side of the pipeline, so moving it is a visible
change to this test rather than a silent drift. ``situational_adjust``
disagrees (it counts third-place as a knockout match); that disagreement is
recorded in the report and is a deliberate modelling call, not a bug fix.
"""

import unittest

from app.kernel.engines.elo_odds_engine import (
    _KNOCKOUT_STAGES as ELO_ODDS_KNOCKOUT_STAGES,
)
from app.kernel.engines.gbm_engine import (
    _KNOCKOUT_STAGES as GBM_KNOCKOUT_STAGES,
)
from app.kernel.engines.situational_adjust import (
    _KNOCKOUT_STAGES as SITUATIONAL_KNOCKOUT_STAGES,
)
from app.services.conclusion_challenge_world_cup_adapter import (
    _HIGH_RISK_STAGES as CHALLENGE_HIGH_RISK_STAGES,
)
from app.services.world_cup_prediction_pipeline import (
    _KNOCKOUT_STAGES as PIPELINE_KNOCKOUT_STAGES,
    _STAGE_MAP as PIPELINE_STAGE_MAP,
)

_KERNEL_ENGINE_STAGE_SETS = {
    "elo_odds_engine": ELO_ODDS_KNOCKOUT_STAGES,
    "gbm_engine": GBM_KNOCKOUT_STAGES,
    "situational_adjust": SITUATIONAL_KNOCKOUT_STAGES,
}


class KnockoutStageWhitelistTests(unittest.TestCase):
    def test_pipeline_knockout_names_are_producible_by_the_stage_map(self):
        """No dead members: a name the normalizer can never emit never matches.

        ``is_knockout`` is computed as ``_normalize_stage(stage) in
        _KNOCKOUT_STAGES``, so a member that is not one of ``_STAGE_MAP``'s
        canonical outputs is unreachable -- the flag stays False for every
        input and nothing raises.
        """
        reachable = set(PIPELINE_STAGE_MAP.values())
        self.assertLessEqual(set(PIPELINE_KNOCKOUT_STAGES), reachable)

    def test_stage_map_values_partition_into_knockout_and_group_stages(self):
        """Every canonical form is either a knockout round or a documented
        non-knockout stage.

        This is what catches a mistyped alias *target*: mapping a new spelling
        onto ``"quarterfinals"`` would add a seventh canonical form that is
        neither, and ``is_knockout`` would quietly stay False for it.
        """
        non_knockout = set(PIPELINE_STAGE_MAP.values()) - set(PIPELINE_KNOCKOUT_STAGES)
        self.assertEqual(non_knockout, {"group_stage", "third_place"})

    def test_kernel_engines_recognise_every_canonical_knockout_stage(self):
        """The engines test the raw stage string, so their sets also carry alias
        spellings -- but they must remain supersets of the canonical forms.

        Dropping a canonical member from an engine's copy would silently flip
        that round to the "safe" non-knockout default (draw allowed).
        """
        for name, stages in _KERNEL_ENGINE_STAGE_SETS.items():
            self.assertLessEqual(
                set(PIPELINE_KNOCKOUT_STAGES),
                set(stages),
                f"{name} does not recognise every canonical knockout stage",
            )

    def test_challenge_adapter_rates_every_canonical_knockout_stage_high_risk(self):
        """The challenge adapter reads the same ``match.stage`` as the engines.

        It is the second consumer that used to test only the underscore
        aliases, so a quarter-final or semi-final was rated ``medium`` -- the
        same "the producer never emits this spelling" bug, in a non-engine
        file. Guarded here rather than in its own test module because the
        relation it must satisfy is the same one.
        """
        self.assertLessEqual(
            set(PIPELINE_KNOCKOUT_STAGES),
            set(CHALLENGE_HIGH_RISK_STAGES),
            "the challenge adapter does not rate every canonical knockout "
            "stage as high risk",
        )


if __name__ == "__main__":
    unittest.main()
