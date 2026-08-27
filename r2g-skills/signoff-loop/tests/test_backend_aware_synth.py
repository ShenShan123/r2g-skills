"""Win 6 — backend-aware synthesis retune recipe.

On a POST-ROUTE timing miss with CLEAN routing, re-pick the ABC mapping strategy
and re-synthesize via the already-paved rerun_from:"synth" path — closing the loop
on real routed WNS instead of the synth-time estimate. The recipe enters as
shadow (requires_ab_promotion) and is never auto-applied in a blind live run; only
the A/B arm (--rank-first) exercises it until it wins an LCB-gated trial.
"""
import diagnose_signoff_fix as dsf


CFG = {"PLATFORM": "sky130hd", "CORE_UTILIZATION": "40"}
TMISS = {"tier": "severe", "wns_ns": -0.5, "clock_period_ns": 2.0}


def test_offered_on_timing_miss_with_clean_routing():
    plan = dsf.build_plan({"status": "clean"}, {}, CFG, check="timing", tcheck=TMISS)
    s = next((x for x in plan["strategies"]
              if x["id"] == "backend_aware_synth_retune"), None)
    assert s is not None
    assert s["rerun_from"] == "synth"
    assert s["recheck"] == "timing"
    assert s.get("requires_ab_promotion") is True
    # re-picks ABC mapping / hierarchy knobs
    assert "ABC_AREA" in s["config_edits"] and "SYNTH_HIERARCHICAL" in s["config_edits"]


def test_not_offered_when_routing_is_dirty():
    """The symptom is timing miss WITH CLEAN routing. If DRC still fails, fix the
    routing first — re-synthesizing won't help and would discard the routed result."""
    drc = {"status": "fail", "total_violations": 12,
           "categories": {"M2_SPACING": {"count": 12}}}
    plan = dsf.build_plan(drc, {}, CFG, check="timing", tcheck=TMISS)
    assert "backend_aware_synth_retune" not in [s["id"] for s in plan["strategies"]]


def test_not_offered_when_timing_clean():
    plan = dsf.build_plan({"status": "clean"}, {}, CFG, check="timing",
                          tcheck={"tier": "clean"})
    assert "backend_aware_synth_retune" not in [s["id"] for s in plan["strategies"]]


# ── shadow gate: never auto-applied in a blind live run ──────────────────────

def test_shadow_strategy_skipped_in_live_auto_pick():
    plan = dsf.build_plan({"status": "clean"}, {}, CFG, check="timing", tcheck=TMISS)
    # No --rank-first: the live loop must NOT auto-pick the shadow recipe; it falls
    # through to a promoted/grandfathered strategy (period_relax).
    auto = dsf._live_auto_strategy(plan, rank_first=None)
    assert auto is not None
    assert auto["id"] != "backend_aware_synth_retune"


def test_shadow_strategy_selectable_by_ab_arm_rank_first():
    plan = dsf.build_plan({"status": "clean"}, {}, CFG, check="timing", tcheck=TMISS)
    auto = dsf._live_auto_strategy(plan, rank_first="backend_aware_synth_retune")
    assert auto is not None and auto["id"] == "backend_aware_synth_retune"
