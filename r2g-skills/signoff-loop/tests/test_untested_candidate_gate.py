"""A learner's own untested candidate must not switch off a cold-start strategy.

deck_driven_antenna_diode is a catalog strategy with no requires_ab_promotion: it
auto-applies while it has no lifecycle row. Its first success made learn() enqueue
a 'learner_diff' candidate for that symptom+class, and the live gate then refused
it on every later design -- where nothing runs A/B (the agent), one success turned
the fix off (2026-10-24 n45 reproduction: 9 antenna designs stopped at
no_auto_strategy). An untested learner candidate carries no verdict, so for such a
strategy it reads like no row; shadow, A/B-judged candidates, and strategies that
DO require promotion keep the strict rule.
"""
import sqlite3

import diagnose_signoff_fix as dsf
import recipe_lifecycle


def _s(**kw):
    return {"id": "deck_driven_antenna_diode", "auto_apply": True, **kw}


def test_untested_learner_candidate_does_not_block_cold_start_strategy():
    s = _s(lifecycle_status="candidate", lifecycle_untested_candidate=True)
    assert dsf._live_auto_strategy({"strategies": [s]})["id"] == s["id"]


def test_tested_candidate_and_shadow_still_block():
    assert dsf._live_auto_strategy({"strategies": [_s(lifecycle_status="candidate")]}) is None
    assert dsf._live_auto_strategy({"strategies": [_s(lifecycle_status="shadow")]}) is None


def test_promotion_gated_strategy_stays_gated():
    s = _s(lifecycle_status="candidate", lifecycle_untested_candidate=True,
           requires_ab_promotion=True)
    assert dsf._live_auto_strategy({"strategies": [s]}) is None


def _db(provenance, trials=0):
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE recipe_status (symptom_id, design_class, platform, strategy, status, provenance)")
    c.execute("CREATE TABLE ab_trials (symptom_id, design_class, platform, strategy)")
    key = ("sym", "logic/medium", "nangate45", "deck_driven_antenna_diode")
    c.execute("INSERT INTO recipe_status VALUES (?,?,?,?,?,?)", (*key, "candidate", provenance))
    for _ in range(trials):
        c.execute("INSERT INTO ab_trials VALUES (?,?,?,?)", key)
    return c


def _untested(c):
    return dsf._untested_learner_candidate(
        c, recipe_lifecycle, symptom_id="sym", design_class="logic/medium",
        platform="nangate45", strategy="deck_driven_antenna_diode")


def test_only_a_never_trialled_learner_row_counts_as_untested():
    assert _untested(_db("learner_diff"))
    assert not _untested(_db("learner_diff", trials=1))
    assert not _untested(_db("ab_trial:7"))
