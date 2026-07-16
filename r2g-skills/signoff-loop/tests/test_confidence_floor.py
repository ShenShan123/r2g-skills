"""Confidence floor (spec §5.7.2) + decision-8 relaxation order lookup."""
import json

import fix_model
import diagnose_signoff_fix as dsf


def test_pooled_only_strategy_cannot_outrank_local_winner_below_floor():
    local = {"strategies": {"local_win": {"attempts": 2, "successes": 2}},
             "n_sessions": 2}
    pooled = {"pooled_hot": {"attempts": 3, "successes": 3}}   # n=3 < floor 5
    ranked = fix_model.rank_strategies(local, ["local_win", "pooled_hot"],
                                       pooled=pooled, pooled_min_attempts=5)
    assert ranked[0]["strategy"] == "local_win"


def test_pooled_strategy_with_enough_attempts_may_outrank():
    local = {"strategies": {"local_win": {"attempts": 2, "successes": 1}},
             "n_sessions": 2}
    pooled = {"pooled_hot": {"attempts": 9, "successes": 9}}   # n=9 >= 5
    ranked = fix_model.rank_strategies(local, ["local_win", "pooled_hot"],
                                       pooled=pooled, pooled_min_attempts=5)
    assert ranked[0]["strategy"] == "pooled_hot"


def test_default_pooled_min_attempts_is_5():
    assert fix_model.POOLED_MIN_ATTEMPTS == 5


def test_pooled_cannot_displace_exact_winner_it_does_not_beat_on_rate():
    """P1-19 (2026-07-15): an EXACT local recipe (2/2, rate 1.0) must not be displaced
    by a large-but-weaker pooled history (90/100, rate 0.9), even far above the attempt
    floor — match level constrains ranking. The exact winner keeps the top slot; the
    pooled prior is capped just below it (a prior/tiebreaker, not an implicit displacer)."""
    local = {"strategies": {"exact_recipe": {"attempts": 2, "successes": 2}},
             "n_sessions": 2}
    pooled = {"pooled_recipe": {"attempts": 100, "successes": 90}}   # n=100 >> floor
    ranked = fix_model.rank_strategies(local, ["exact_recipe", "pooled_recipe"],
                                       pooled=pooled, pooled_min_attempts=5)
    assert ranked[0]["strategy"] == "exact_recipe"


def _heur(tmp_path):
    sid = dsf.symptom.symptom_id(
        dsf.symptom.canonical_signature("drc", "antenna", None))
    data = {"generation": 1, "recipes": {sid: {
        "crypto/small": {"nangate45": {
            "strategies": {"antenna_diode_repair": {"attempts": 2, "successes": 2,
                                                    "failures": 0, "wins": 0}},
            "n_sessions": 2},
            "*": {"strategies": {"antenna_diode_repair": {"attempts": 4,
                  "successes": 3, "failures": 1, "wins": 0}}, "n_sessions": 4}},
        "*": {"*": {"strategies": {"antenna_diode_repair": {"attempts": 9,
              "successes": 7, "failures": 2, "wins": 0}}, "n_sessions": 9}}}}}
    hp = tmp_path / "heuristics.json"
    hp.write_text(json.dumps(data))
    return hp, sid


def test_indexed_lookup_exact_key_first(tmp_path):
    hp, _ = _heur(tmp_path)
    recipe, pooled, level = dsf.load_indexed_recipe(
        check="drc", platform="nangate45", design_class="crypto/small",
        drc={"status": "fail", "categories": {"antenna": {"count": 3}}},
        lvs={}, heuristics=hp)
    assert level == "exact"
    assert recipe["strategies"]["antenna_diode_repair"]["attempts"] == 2
    # pooled prior comes from the global rollup
    assert pooled["antenna_diode_repair"]["attempts"] == 9


def test_indexed_lookup_relaxes_class_then_platform(tmp_path):
    hp, _ = _heur(tmp_path)
    recipe, _, level = dsf.load_indexed_recipe(
        check="drc", platform="nangate45", design_class="bus_heavy/large",
        drc={"status": "fail", "categories": {"antenna": {"count": 3}}},
        lvs={}, heuristics=hp)
    assert level == "pooled_platform"   # '*' class for nangate45 absent -> global
    assert recipe["strategies"]["antenna_diode_repair"]["attempts"] == 9
