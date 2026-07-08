"""Diagnose-replay rank-1 regression gate.

A single, deterministic assertion that the signoff-fix ranker still surfaces the
RIGHT top strategy for a fixed symptom. Seeds recipes such that one strategy is an
UNAMBIGUOUS score winner (high clean-rate) and the other a clear loser, so the
rank-1 outcome is stable under Workstream D's corroboration tiebreaker (which can
only reorder strategies of EQUAL (score, mean_outcome_score) — it can never lift a
lower-score recipe above a higher-score one). If a future ranking change regresses
rank-1, this fails LOUDLY.

The optional R2G_REPLAY_MIN_SCORE env var lets an operator assert a floor on the
winner's score without editing the test.
"""
from __future__ import annotations

import os

import diagnose_signoff_fix as dsf


# sky130hd antenna DRC -> static catalog [antenna_diode_iters, antenna_density_relief].
_CFG = {"PLATFORM": "sky130hd", "CORE_UTILIZATION": "40"}
_DRC = {"status": "fail", "total_violations": 12,
        "categories": {"M1_ANTENNA": {"count": 12}}}

# antenna_density_relief is the UNAMBIGUOUS winner: 9/10 clears vs 1/10 for the
# diode_iters strategy. (9+1)/(10+2)=0.833 >> (1+1)/(10+2)=0.167 — no tiebreaker can
# flip a gap this large, so rank-1 is deterministic under the new sort key.
_EXPECTED_RANK1 = "antenna_density_relief"
_RECIPES = {"strategies": {
    "antenna_density_relief": {"attempts": 10, "successes": 9, "failures": 1},
    "antenna_diode_iters":    {"attempts": 10, "successes": 1, "failures": 9},
}, "n_sessions": 10}


def test_replay_rank1_is_stable():
    plan = dsf.build_plan(_DRC, {}, _CFG, check="drc", recipes=_RECIPES)
    # Both rank-1 accessors must agree on the winner.
    assert plan["strategies"][0]["id"] == _EXPECTED_RANK1
    assert plan["ranking"][0]["strategy"] == _EXPECTED_RANK1

    # Optional operator-set floor on the winner's score (no-op unless exported).
    floor = os.environ.get("R2G_REPLAY_MIN_SCORE")
    if floor is not None:
        assert plan["ranking"][0]["score"] >= float(floor), (
            f"rank-1 score {plan['ranking'][0]['score']:.3f} below floor {floor}")


def test_replay_winner_outscores_loser():
    """Guard the premise of the gate: the winner's score genuinely dominates, so the
    rank-1 assertion is testing real evidence, not a catalog-order accident."""
    plan = dsf.build_plan(_DRC, {}, _CFG, check="drc", recipes=_RECIPES)
    by_id = {r["strategy"]: r for r in plan["ranking"]}
    assert by_id["antenna_density_relief"]["score"] > by_id["antenna_diode_iters"]["score"]
