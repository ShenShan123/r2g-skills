"""Unit tests for the pure fix-strategy ranking model."""
from __future__ import annotations
import pytest
import fix_model as fxm


STATIC = ["antenna_diode_repair", "antenna_density_relief", "lvs_macro_cdl"]


def test_cold_start_returns_static_order():
    ranked = fxm.rank_strategies(None, STATIC)
    assert [r["strategy"] for r in ranked] == STATIC
    assert all(r["provenance"] == "cold-start" for r in ranked)
    assert all(r["attempts"] == 0 for r in ranked)


def test_proven_winner_outranks_untried_outranks_loser():
    entry = {"strategies": {
        "antenna_density_relief": {"attempts": 11, "successes": 9, "failures": 2},
        "lvs_macro_cdl":          {"attempts": 3,  "successes": 0, "failures": 3},
    }, "n_sessions": 14}
    ranked = fxm.rank_strategies(entry, STATIC)
    order = [r["strategy"] for r in ranked]
    # winner (9/11) first, untried diode_repair (0.5 prior) middle, loser (0/3) last
    assert order[0] == "antenna_density_relief"
    assert order[1] == "antenna_diode_repair"      # untried -> neutral prior 0.5
    assert order[2] == "lvs_macro_cdl"             # proven loser, but still present
    assert ranked[2]["score"] < 0.5 < ranked[0]["score"]


def test_smoothing_tames_single_lucky_win():
    entry = {"strategies": {"antenna_diode_repair": {"attempts": 1, "successes": 1, "failures": 0}},
             "n_sessions": 1}
    ranked = fxm.rank_strategies(entry, STATIC)
    win = next(r for r in ranked if r["strategy"] == "antenna_diode_repair")
    # 1/1 -> (1+1)/(1+2)=0.667, only just above the 0.5 untried prior.
    assert win["score"] == pytest.approx(2/3, abs=1e-6)


def test_evidence_and_provenance_surfaced():
    entry = {"strategies": {"antenna_density_relief": {"attempts": 6, "successes": 5,
             "failures": 1, "median_reduction_pct": 0.97}}, "n_sessions": 6}
    ranked = fxm.rank_strategies(entry, STATIC)
    top = next(r for r in ranked if r["strategy"] == "antenna_density_relief")
    assert top["provenance"].startswith("learned(n=6")
    assert top["successes"] == 5 and top["failures"] == 1
    assert "median_reduction_pct" in top


def test_win_gets_half_credit_and_outranks_loser():
    """A reliably-improving 'win' strategy (partial improvements, no full clears
    yet) must earn half credit so it ranks ABOVE an equally-tried pure-loser, and
    is no longer treated as a bare uncredited attempt (bug #7/#11)."""
    entry = {"strategies": {
        # 3 wins, no clears, no failures: (0 + 0.5*3 + 1)/(3 + 2) = 2.5/5 = 0.5.
        "antenna_diode_repair":  {"attempts": 3, "successes": 0, "failures": 0, "wins": 3},
        # 3 failures: (0 + 0 + 1)/(3 + 2) = 0.2.
        "antenna_density_relief": {"attempts": 3, "successes": 0, "failures": 3, "wins": 0},
    }, "n_sessions": 6}
    ranked = fxm.rank_strategies(entry, STATIC)
    by = {r["strategy"]: r for r in ranked}
    assert by["antenna_diode_repair"]["score"] == pytest.approx((0 + 0.5 * 3 + 1) / (3 + 2), abs=1e-6)
    assert by["antenna_density_relief"]["score"] == pytest.approx((0 + 1) / (3 + 2), abs=1e-6)
    assert by["antenna_diode_repair"]["score"] > by["antenna_density_relief"]["score"]
    assert by["antenna_diode_repair"]["wins"] == 3
    order = [r["strategy"] for r in ranked]
    assert order.index("antenna_diode_repair") < order.index("antenna_density_relief")


def test_score_backward_compatible_without_wins_key():
    """Recipes predating the 'wins' counter (no key) must score exactly as
    before — half credit defaults to 0 wins."""
    entry = {"strategies": {
        "antenna_density_relief": {"attempts": 11, "successes": 9, "failures": 2},
    }, "n_sessions": 14}
    ranked = fxm.rank_strategies(entry, STATIC)
    top = next(r for r in ranked if r["strategy"] == "antenna_density_relief")
    assert top["score"] == pytest.approx((9 + 1) / (11 + 2), abs=1e-6)
    assert top.get("wins", 0) == 0


def test_never_drops_a_static_strategy():
    entry = {"strategies": {"antenna_diode_repair": {"attempts": 2, "successes": 2, "failures": 0}},
             "n_sessions": 2}
    ranked = fxm.rank_strategies(entry, STATIC)
    assert set(r["strategy"] for r in ranked) == set(STATIC)


def test_outcome_score_breaks_ties_within_equal_clearance():
    """Win 1 tiebreaker: rank_key = (success_rate_beta, mean_outcome_score). At
    EQUAL Beta clearance, the strategy with the higher mean_outcome_score ranks
    first — even though it is later in the static catalog."""
    STATIC = ["lowprog", "highprog"]
    entry = {"strategies": {
        "lowprog":  {"attempts": 2, "successes": 1, "failures": 1,
                     "mean_outcome_score": 0.40},
        "highprog": {"attempts": 2, "successes": 1, "failures": 1,
                     "mean_outcome_score": 0.90},
    }, "n_sessions": 4}
    ranked = fxm.rank_strategies(entry, STATIC)
    # equal Beta score (both (1+1)/(2+2)=0.5); outcome_score breaks the tie.
    assert ranked[0]["score"] == ranked[1]["score"]
    assert ranked[0]["strategy"] == "highprog"


def test_outcome_score_never_overrides_clearance():
    """Clean-rate dominates: a high mean_outcome_score must NOT lift a worse-
    clearing strategy above a better-clearing one."""
    STATIC = ["weakclear_highprog", "strongclear_lowprog"]
    entry = {"strategies": {
        "weakclear_highprog":  {"attempts": 4, "successes": 1, "failures": 3,
                                "mean_outcome_score": 0.99},
        "strongclear_lowprog": {"attempts": 4, "successes": 3, "failures": 1,
                                "mean_outcome_score": 0.10},
    }, "n_sessions": 8}
    ranked = fxm.rank_strategies(entry, STATIC)
    assert ranked[0]["strategy"] == "strongclear_lowprog"   # 0.667 > 0.333


def test_ranking_byte_identical_when_no_outcome_score():
    """Legacy recipes without mean_outcome_score rank exactly as before (the
    secondary key is a constant 0.0, so catalog position decides ties)."""
    entry = {"strategies": {
        "antenna_diode_repair":   {"attempts": 2, "successes": 1, "failures": 1},
        "antenna_density_relief": {"attempts": 2, "successes": 1, "failures": 1},
    }, "n_sessions": 4}
    ranked = fxm.rank_strategies(entry, STATIC)
    # tie on score; catalog order preserved (diode before density in STATIC).
    assert [r["strategy"] for r in ranked[:2]] == \
        ["antenna_diode_repair", "antenna_density_relief"]


def test_informed_prior_lifts_untried_strategy_toward_pooled_rate():
    # Local recipe has NO data for diode_repair; pooled symptom evidence says it
    # clears ~90%. Informed prior must rank it above the flat-0.5 untried prior.
    STATIC = ["antenna_diode_repair", "antenna_density_relief"]
    pooled = {"antenna_diode_repair": {"successes": 9, "attempts": 10, "wins": 0}}
    ranked = fxm.rank_strategies(None, STATIC, pooled=pooled)
    diode = next(r for r in ranked if r["strategy"] == "antenna_diode_repair")
    relief = next(r for r in ranked if r["strategy"] == "antenna_density_relief")
    assert diode["score"] > 0.7           # ~ (9+1)/(10+2)
    assert relief["score"] == 0.5         # no pooled evidence -> neutral
    assert diode["provenance"].startswith("prior")
