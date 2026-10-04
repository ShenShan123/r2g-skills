"""Phase H: mechanism tags and the stage-2 analysis (memory/evaluation/r2g_memory_phaseH_contract_20261002.md)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

MEM = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(MEM / "scripts")]


def test_mechanism_tags():
    from runtime_router import mechanisms
    assert mechanisms({"source": "tehm_rule", "transformation_family": "LLM_EDIT_CORE_UTILIZATION",
                       "memory_trial": True, "witness_selected": ["CORE_UTILIZATION"],
                       "dropped_knobs": ["MIN_ROUTING_LAYER"], "value_tolerated": True}) == \
        ["rule_llm", "trial_candidate", "value_median", "categorical_drop", "v4_tolerance"]
    assert mechanisms({"source": "tehm_rule", "transformation_family": "KNOB_SUBSET_CORE_UTILIZATION"}) == \
        ["rule_knob_subset"]
    assert mechanisms({"source": "tehm_rule", "transformation_family": "COMPONENT_CORE_UTILIZATION",
                       "categorical_selected": ["X"]}) == ["rule_component", "value_categorical"]
    assert mechanisms({"source": "tehm_rule", "transformation_family": "DENSITY_RELIEF"}) == ["rule_r2g"]
    assert mechanisms({"source": "tehm_compose", "composition": {"tested": False}}) == ["compose_untested"]


def _rec(root, arm, lane, rep, order, task, rp=False, **kw):
    d, v = task.split("/")
    out = root / ("%s-%s%s%s" % (arm, lane, "-r2" if rep == 2 else "", "-rp" if rp else ""))
    out.mkdir(parents=True, exist_ok=True)
    rec = {"design": d, "variant": v, "order": order, "task_situation": {}, "llm_calls": 0, "llm_tokens": 0,
           "backend_runs": 1, "fix_iterations": [], "memory_applied": []}
    rec.update(kw)
    (out / ("%02d__%s__%s.json" % (order, d, v))).write_text(json.dumps(rec))


def test_h_analyze_llm_cost_harm_and_mechanisms(tmp_path, monkeypatch):
    import r2g_memory_eval as H
    monkeypatch.setattr(H, "EVAL", tmp_path)
    monkeypatch.setattr(H, "CASES", tmp_path / "cases")
    root = tmp_path / "evaluation-h"
    llm = lambda tok: [{"iter": 1, "physical_clean": True, "applied": {}, "usage": {"total_tokens": tok,
                                                                                  "prompt_tokens": tok - 100,
                                                                                  "completion_tokens": 100}}]
    # task a: memory closes it (SL, decisive rule); NL needs the LLM (1 call, 900 tokens) in both reps
    for rep in (1, 2):
        _rec(root, "NL", "hd", rep, 0, "a/u1", physical_clean=True, closed_by="llm", llm_calls=1,
             llm_tokens=900, llm_assist=llm(900))
    _rec(root, "SL", "hd", 1, 0, "a/u1", physical_clean=True, closed_by="memory_or_catalogue",
         fix_iterations=[{"check": "orfs_stage", "strategy": "tehm_tehm_rule:rule_0123456789ab",
                          "verdict": "cleared", "memory": True}])
    _rec(root, "SL", "hd", 2, 0, "a/u1", physical_clean=True, closed_by="memory_or_catalogue",
         reused_from_rep1=True)
    proj = tmp_path / "cases" / "a__u1__SL"
    proj.mkdir(parents=True)
    (proj / "eval_arm.log").write_text(
        "TEHM mechanisms: tehm_tehm_rule:rule_0123456789ab rule_llm,trial_candidate,value_median\n"
        "[orfs_stage] iter 1: applying tehm_tehm_rule:rule_0123456789ab\n"
        "TEHM strategy tehm_tehm_rule:rule_ffffffffffff dropped: knob policy: CORE_UTILIZATION=95\n")
    # task b: NL closes in both reps, SL in neither -> harm
    for rep in (1, 2):
        _rec(root, "NL", "hd", rep, 1, "b/u1", physical_clean=True, closed_by="llm", llm_calls=2,
             llm_tokens=2000, llm_assist=llm(2000))
        _rec(root, "SL", "hd", rep, 1, "b/u1", physical_clean=False, closed_by=None, llm_calls=3, llm_tokens=3000)
    H.h_analyze(type("A", (), {"out": "evaluation-h"})())
    out = json.loads((root / "analysis.json").read_text())
    nl, sl = out["primary"]["summary"]["NL"], out["primary"]["summary"]["SL"]
    assert (nl["closed"], nl["llm_calls"], nl["calls_per_closure"]) == (4, 6, 1.5)
    assert (sl["closed"], sl["closed_without_llm"], sl["llm_calls"], sl["calls_per_closure"]) == (2, 2, 6, 3.0)
    assert nl["tokens_per_llm_fix"] == 1450.0 and sl["tokens_per_llm_fix"] is None
    assert out["primary"]["harm"] == ["b/u1"] and out["primary"]["success"]["zero_harm"] is False
    m = out["mechanisms"]
    assert m["rule_llm"]["decisions"] == 1 and m["rule_llm"]["decisive_closures"] == ["a/u1"]   # rep 2 not recounted
    assert m["path_diagnose"]["decisive_closures"] == ["a/u1"]
    assert out["safety_gates_fired"]["knob_policy"] == 1


def test_h_analyze_infra_pairing_replacement_and_exclusion(tmp_path, monkeypatch):
    """H-A3: an attempt with a failed call drops its (task, rep) pair from both arms; i2c tasks are reported
    separately. H-A4: a clean replacement attempt replaces the infra-affected one in the primary view."""
    import r2g_memory_eval as H
    monkeypatch.setattr(H, "EVAL", tmp_path)
    monkeypatch.setattr(H, "CASES", tmp_path / "cases")
    root = tmp_path / "evaluation-h"
    failed = [{"iter": 1, "result": "call_failed", "usage": {}}]
    ok = [{"iter": 1, "physical_clean": True, "applied": {}, "usage": {"total_tokens": 700}}]
    _rec(root, "NL", "hd", 1, 0, "c/u1", physical_clean=False, closed_by=None, llm_calls=1, llm_assist=failed)
    _rec(root, "SL", "hd", 1, 0, "c/u1", physical_clean=True, closed_by="memory_or_catalogue")
    _rec(root, "NL", "hd", 1, 1, "i2c_master_top/u89", physical_clean=False, closed_by=None, llm_calls=3)
    _rec(root, "SL", "hd", 1, 1, "i2c_master_top/u89", physical_clean=False, closed_by=None, llm_calls=3)
    H.h_analyze(type("A", (), {"out": "evaluation-h"})())
    out = json.loads((root / "analysis.json").read_text())
    assert out["as_run"]["summary"]["NL"]["attempts"] == 0 and out["as_run"]["summary"]["SL"]["attempts"] == 0
    assert out["all_attempts"]["summary"]["NL"]["attempts"] == 1               # sensitivity keeps it
    assert out["excluded_tasks"]["summary"]["NL"]["attempts"] == 1             # i2c reported separately
    assert out["infra_affected"] == ["run|NL|1|c/u1"]
    _rec(root, "NL", "hd", 1, 0, "c/u1", rp=True, physical_clean=True, closed_by="llm", llm_calls=1,
         llm_tokens=700, llm_assist=ok)
    H.h_analyze(type("A", (), {"out": "evaluation-h"})())
    out = json.loads((root / "analysis.json").read_text())
    prim = out["primary"]["summary"]
    assert prim["NL"]["closed_by_llm"] == 1 and prim["NL"]["tokens_per_llm_fix"] == 700.0
    assert prim["SL"]["closed_without_llm"] == 1 and out["as_run"]["summary"]["NL"]["attempts"] == 0
