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
import json
import sqlite3

import pytest

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


@pytest.mark.parametrize("annotations,gate_ok,allowed", [
    ({"lifecycle_status": "candidate", "lifecycle_untested_candidate": True}, True, True),
    ({"lifecycle_status": "candidate", "candidate_attempt_authorized": True,
      "requires_ab_promotion": True}, True, True),
    ({"lifecycle_status": "candidate"}, True, False),
    ({"lifecycle_status": "candidate", "lifecycle_untested_candidate": True,
      "requires_ab_promotion": True}, True, False),
    ({"lifecycle_status": "shadow"}, True, False),
    ({"lifecycle_status": "promoted", "requires_ab_promotion": True}, True, True),
    ({"lifecycle_status": "candidate", "lifecycle_untested_candidate": True}, False, False),
])
def test_selected_strategy_can_apply_with_same_lifecycle_evidence(
        tmp_path, monkeypatch, capsys, annotations, gate_ok, allowed):
    """Exercise both CLI paths and an actual config edit, not only selection.

    The 207-design reproduction passed selection but --apply rejected the same
    learner candidate. Explicit candidates and unreadable stores must still block.
    """
    (tmp_path / "constraints").mkdir()
    (tmp_path / "reports").mkdir()
    cfg = tmp_path / "constraints" / "config.mk"
    original = "export PLATFORM = sky130hd\nexport CORE_UTILIZATION = 10\n"
    cfg.write_text(original)
    (tmp_path / "reports" / "drc.json").write_text(json.dumps({
        "status": "fail", "total_violations": 7,
        "categories": {"METAL7_ANTENNA": {"count": 7}},
    }))

    def annotate(plan, proj, **kwargs):
        for strategy in plan["strategies"]:
            strategy.update(annotations)
        plan["lifecycle_gate_ok"] = gate_ok
        return plan

    monkeypatch.setattr(dsf, "_annotate_live_gates", annotate)
    monkeypatch.setattr(dsf, "load_indexed_recipe", lambda **kw: (None, {}, None))
    monkeypatch.setattr(dsf, "load_symptom_recipe", lambda **kw: (None, {}))
    monkeypatch.setattr(dsf, "_load_recipes", lambda *a, **kw: None)
    monkeypatch.setattr(dsf, "attach_lessons", lambda plan, **kw: plan)
    monkeypatch.setattr(dsf, "_antenna_precondition", lambda platform: (True, "fixture"))

    args = [str(tmp_path), "--check", "drc"]
    assert dsf.main([*args, "--next"]) == 0
    selection = capsys.readouterr().out
    assert ("antenna_diode_iters" in selection) is allowed
    rc = dsf.main([*args, "--apply", "antenna_diode_iters"])
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    if allowed:
        assert rc == 0
        assert result["status"] == "applied"
        assert "MAX_REPAIR_ANTENNAS_ITER_GRT" in cfg.read_text()
    else:
        assert rc == 5
        assert result["status"] == "lifecycle_blocked"
        assert cfg.read_text() == original
