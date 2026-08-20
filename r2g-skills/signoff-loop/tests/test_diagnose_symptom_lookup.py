"""Symptom-keyed recipe lookup + pooled prior in diagnose_signoff_fix (spec 2026-06-09)."""
from __future__ import annotations
import json


def test_symptom_lookup_returns_recipe_and_pooled_prior(tmp_path):
    import diagnose_signoff_fix as dsf
    import symptom
    heur = tmp_path / "heuristics.json"
    sig = {"check": "lvs", "class": "symmetric_matcher", "predicates": {}}
    sid = symptom.symptom_id(sig)
    heur.write_text(json.dumps({"symptoms": {sid: {
        "check": "lvs", "class": "symmetric_matcher", "predicates": {},
        "platforms_seen": ["nangate45"], "evidence_designs": ["d1"],
        "n_sessions": 5,
        "strategies": {"lvs_same_nets_seed": {
            "attempts": 5, "successes": 4, "failures": 1, "wins": 0,
            "by_platform": {"nangate45": {"attempts": 5, "successes": 4,
                                          "failures": 1, "wins": 0}}}}}}}))
    lvs = {"status": "fail", "mismatch_class": "symmetric_matcher"}
    # sky130hd has NO by_platform data -> recipe entry empty, but pooled prior present.
    recipe, pooled = dsf.load_symptom_recipe(
        check="lvs", platform="sky130hd", drc={}, lvs=lvs, heuristics=heur)
    assert recipe is None
    assert pooled["lvs_same_nets_seed"]["successes"] == 4
    # nangate45 path returns the platform recipe.
    recipe_n, _ = dsf.load_symptom_recipe(
        check="lvs", platform="nangate45", drc={}, lvs=lvs, heuristics=heur)
    assert recipe_n["strategies"]["lvs_same_nets_seed"]["successes"] == 4


def test_plan_attaches_matching_lesson(tmp_path, monkeypatch):
    import diagnose_signoff_fix as dsf
    import search_failures
    md = tmp_path / "failure-patterns.md"
    md.write_text("# F\n\n## Sym\n<!-- r2g-lesson:\nid: l-sym\nstatus: active\n"
                  'trigger: {check: lvs, class: symmetric_matcher, platform: "*"}\n-->\n'
                  "Tool artifact; stop.\n")
    monkeypatch.setattr(search_failures, "_PATTERNS_PATH", md)
    plan = {"status": "fail", "strategies": [{"id": "lvs_same_nets_seed"}]}
    dsf.attach_lessons(plan, check="lvs", vclass="symmetric_matcher",
                       platform="sky130hd")
    assert plan["lessons"] and plan["lessons"][0]["id"] == "l-sym"
    assert "stop" in plan["lessons"][0]["prose"].lower()


def test_timing_lookup_uses_timing_tier_as_recipe_class(tmp_path):
    import diagnose_signoff_fix as dsf
    import symptom
    heur = tmp_path / "heuristics.json"
    sid = symptom.symptom_id(
        symptom.canonical_signature("timing", "minor", {}))
    node = {"strategies": {"setup_slack_margin": {
        "attempts": 2, "successes": 2, "failures": 0, "wins": 0}}}
    heur.write_text(json.dumps({
        "recipes": {sid: {
            "logic/small": {"sky130hd": node},
            "*": {"*": node},
        }},
        "symptoms": {sid: {
            "n_sessions": 2,
            "strategies": {"setup_slack_margin": {
                "attempts": 2, "successes": 2, "failures": 0, "wins": 0,
                "by_platform": {"sky130hd": {
                    "attempts": 2, "successes": 2,
                    "failures": 0, "wins": 0}}}},
        }},
    }))
    tcheck = {"tier": "minor", "wns_ns": -0.01}
    recipe, pooled, level = dsf.load_indexed_recipe(
        check="timing", platform="sky130hd", design_class="logic/small",
        drc={}, lvs={}, tcheck=tcheck, heuristics=heur)
    assert level == "exact"
    assert recipe["strategies"]["setup_slack_margin"]["successes"] == 2
    assert pooled["setup_slack_margin"]["attempts"] == 2
    symptom_recipe, _ = dsf.load_symptom_recipe(
        check="timing", platform="sky130hd", drc={}, lvs={},
        tcheck=tcheck, heuristics=heur)
    assert symptom_recipe["strategies"]["setup_slack_margin"]["attempts"] == 2
    assert dsf._current_vclass("timing", {}, {}, tcheck) == "minor"
