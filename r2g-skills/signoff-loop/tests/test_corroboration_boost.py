"""Cross-platform corroboration boost in the fix-strategy ranking model.

A recipe corroborated across MULTIPLE distinct platforms outranks an equal-
strength single-platform/single-design fluke — but ONLY as a tiebreaker: a
clearly higher-score recipe never loses to a more-corroborated weaker one. The
mission word is 'transfer'; this serves it (a fix learned on N platforms is
trusted ahead of a one-design lucky win)."""
from __future__ import annotations
import fix_model as fxm


STATIC = ["corroborated", "fluke"]


def test_corroborated_beats_single_platform_fluke_at_equal_score():
    # Both strategies have the SAME Beta score ((1+1)/(2+2)=0.5) and the same
    # (absent) outcome_score; the one corroborated across 3 platforms must win
    # on the platform_count tiebreaker.
    entry = {"strategies": {
        "corroborated": {"attempts": 2, "successes": 1, "failures": 1,
                         "platform_count": 3},
        "fluke":        {"attempts": 2, "successes": 1, "failures": 1,
                         "platform_count": 1},
    }, "n_sessions": 4}
    ranked = fxm.rank_strategies(entry, STATIC)
    assert ranked[0]["score"] == ranked[1]["score"]      # genuine tie on score
    assert ranked[0]["strategy"] == "corroborated"
    assert ranked[0]["platform_count"] == 3
    assert ranked[1]["platform_count"] == 1


def test_higher_score_still_wins_despite_lower_platform_count():
    # Guard: corroboration is a TIEBREAKER, never a score override. The clearly
    # stronger clearer (3/4=0.667) outranks a weaker (1/4=0.333) one even though
    # the weaker one is corroborated across more platforms.
    STATIC2 = ["weak_corroborated", "strong_fluke"]
    entry = {"strategies": {
        "weak_corroborated": {"attempts": 4, "successes": 1, "failures": 3,
                              "platform_count": 5},
        "strong_fluke":      {"attempts": 4, "successes": 3, "failures": 1,
                              "platform_count": 1},
    }, "n_sessions": 8}
    ranked = fxm.rank_strategies(entry, STATIC2)
    assert ranked[0]["strategy"] == "strong_fluke"       # 0.667 > 0.333
    assert ranked[0]["score"] > ranked[1]["score"]


def test_cold_start_returns_static_order_with_zero_platform_count():
    ranked = fxm.rank_strategies(None, STATIC)
    assert [r["strategy"] for r in ranked] == STATIC
    assert all(r["platform_count"] == 0 for r in ranked)
    assert all(r["provenance"] == "cold-start" for r in ranked)


def test_platform_count_defaults_to_zero_when_unknown():
    # Legacy recipe without the field: platform_count present and 0 (so the
    # tiebreaker is a constant and catalog order decides equal-score ties).
    entry = {"strategies": {
        "corroborated": {"attempts": 2, "successes": 1, "failures": 1},
        "fluke":        {"attempts": 2, "successes": 1, "failures": 1},
    }, "n_sessions": 4}
    ranked = fxm.rank_strategies(entry, STATIC)
    assert all(r["platform_count"] == 0 for r in ranked)
    # tie on score AND platform_count -> catalog order preserved.
    assert [r["strategy"] for r in ranked] == STATIC


def test_pooled_strategy_carries_platform_count():
    # A pooled-only (untried-locally) strategy surfaces its corroboration count
    # so --explain can report it and ties among pooled priors break by it.
    STATIC3 = ["a", "b"]
    pooled = {
        "a": {"attempts": 6, "successes": 3, "wins": 0, "platform_count": 4},
        "b": {"attempts": 6, "successes": 3, "wins": 0, "platform_count": 1},
    }
    ranked = fxm.rank_strategies(None, STATIC3, pooled=pooled)
    by = {r["strategy"]: r for r in ranked}
    assert by["a"]["platform_count"] == 4
    assert by["b"]["platform_count"] == 1
    assert ranked[0]["strategy"] == "a"      # equal pooled score, more platforms
