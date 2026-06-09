"""Cross-platform symptom transfer + platform_specific gating (spec 2026-06-09 §6).

Deterministic fixtures over Tasks 12 (informed prior) + 13 (symptom lookup): a
symptom learned on nangate45 is retrieved for a sky130hd run with the same symptom,
and a platform_specific fix is NOT transferred.
"""
from __future__ import annotations
import importlib
import json

knowledge_db = importlib.import_module("knowledge_db")
symptom = importlib.import_module("symptom")
dsf = importlib.import_module("diagnose_signoff_fix")
fix_model = importlib.import_module("fix_model")


def test_nangate45_symptom_transfers_to_sky130hd(tmp_path):
    sig = {"check": "lvs", "class": "symmetric_matcher", "predicates": {}}
    sid = symptom.symptom_id(sig)
    heur = tmp_path / "heuristics.json"
    heur.write_text(json.dumps({"symptoms": {sid: {
        "check": "lvs", "class": "symmetric_matcher", "predicates": {},
        "platforms_seen": ["nangate45"], "evidence_designs": ["d_nan"],
        "n_sessions": 6,
        "strategies": {"lvs_same_nets_seed": {
            "attempts": 6, "successes": 5, "failures": 1, "wins": 0,
            "platform_specific": False,
            "by_platform": {"nangate45": {"attempts": 6, "successes": 5,
                                          "failures": 1, "wins": 0}}}}}}}))
    lvs = {"status": "fail", "mismatch_class": "symmetric_matcher"}
    # sky130hd run, no sky130 evidence: pooled prior carries nangate45 experience.
    recipe, pooled = dsf.load_symptom_recipe(
        check="lvs", platform="sky130hd", drc={}, lvs=lvs, heuristics=heur)
    ranked = fix_model.rank_strategies(
        recipe, ["lvs_same_nets_seed", "lvs_resolve_unknown"], pooled=pooled)
    top = ranked[0]
    assert top["strategy"] == "lvs_same_nets_seed"
    assert top["score"] > 0.7
    assert top["provenance"].startswith("prior")   # transferred, not local


def test_platform_specific_strategy_not_transferred(tmp_path):
    sig = {"check": "drc", "class": "METAL1_ANTENNA", "predicates": {}}
    sid = symptom.symptom_id(sig)
    heur = tmp_path / "heuristics.json"
    heur.write_text(json.dumps({"symptoms": {sid: {
        "check": "drc", "class": "METAL1_ANTENNA", "predicates": {},
        "platforms_seen": ["nangate45"], "evidence_designs": ["d_nan"],
        "n_sessions": 8,
        "strategies": {"antenna_diode_repair": {
            "attempts": 8, "successes": 8, "failures": 0, "wins": 0,
            "platform_specific": True,        # nangate45 deck only
            "by_platform": {"nangate45": {"attempts": 8, "successes": 8,
                                          "failures": 0, "wins": 0}}}}}}}))
    drc = {"categories": {"METAL1_ANTENNA": {"count": 5}}}
    recipe, pooled = dsf.load_symptom_recipe(
        check="drc", platform="sky130hd", drc=drc, lvs={}, heuristics=heur)
    # platform_specific -> excluded from the cross-platform pooled prior.
    assert "antenna_diode_repair" not in pooled
