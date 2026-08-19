"""Timing fix catalog, including the A/B-gated Sky130HD setup-margin action."""
import json

import diagnose_signoff_fix as dsf


def test_timing_severe_offers_period_relax_first():
    tcheck = {"tier": "severe", "wns_ns": -1.2, "clock_period_ns": 4.0}
    plan = dsf.build_plan({}, {}, {"PLATFORM": "nangate45",
                                   "CORE_UTILIZATION": "30"},
                          check="timing", tcheck=tcheck)
    ids = [s["id"] for s in plan["strategies"]]
    assert ids[0] == "period_relax"
    assert "utilization_reduce" in ids
    pr = plan["strategies"][0]
    # relaxed period = old period - WNS (slack-absorbing), rounded up 5%
    assert float(pr["sdc_edits"]["CLOCK_PERIOD"]) >= 5.2


def test_timing_clean_offers_nothing():
    plan = dsf.build_plan({}, {}, {"PLATFORM": "nangate45"},
                          check="timing", tcheck={"tier": "clean"})
    assert plan["strategies"] == []


def test_timing_minor_excludes_period_relax():
    # minor tier auto-fixes via existing flow; only utilization relief offered
    plan = dsf.build_plan({}, {}, {"PLATFORM": "nangate45",
                                   "CORE_UTILIZATION": "30"},
                          check="timing", tcheck={"tier": "minor",
                                                  "wns_ns": -0.02})
    ids = [s["id"] for s in plan["strategies"]]
    assert "period_relax" not in ids


def test_sky130hd_small_clean_route_miss_offers_setup_margin_first():
    plan = dsf.build_plan(
        {}, {},
        {"PLATFORM": "sky130hd", "CORE_UTILIZATION": "30"},
        check="timing", tcheck={"tier": "minor", "wns_ns": -0.018},
        route={"status": "clean", "total_violations": 0})
    strategy = plan["strategies"][0]
    assert strategy["id"] == "setup_slack_margin"
    assert strategy["config_edits"] == {"SETUP_SLACK_MARGIN": "0.2"}
    assert strategy["rerun_from"] == "floorplan"
    assert strategy["requires_ab_promotion"] is True
    assert "CLOCK_PERIOD" not in strategy.get("sdc_edits", {})


def test_setup_margin_not_offered_outside_validated_scope():
    cases = [
        ({"status": "fail"}, {"PLATFORM": "sky130hd"}, -0.018),
        ({"status": "clean"}, {"PLATFORM": "sky130hs"}, -0.018),
        ({"status": "clean"}, {"PLATFORM": "sky130hd"}, -0.25),
        ({"status": "clean"}, {"PLATFORM": "sky130hd",
                                "SETUP_SLACK_MARGIN": "0.2"}, -0.018),
    ]
    for route, cfg, wns in cases:
        plan = dsf.build_plan({}, {}, cfg, check="timing",
                              tcheck={"tier": "minor", "wns_ns": wns}, route=route)
        assert "setup_slack_margin" not in [s["id"] for s in plan["strategies"]]


def test_explicit_dirty_route_overrides_stale_clean_drc_for_setup_margin():
    plan = dsf.build_plan(
        {"status": "clean"}, {}, {"PLATFORM": "sky130hd"},
        check="timing", tcheck={"tier": "minor", "wns_ns": -0.018},
        route={"status": "fail", "total_violations": 8})
    assert "setup_slack_margin" not in [s["id"] for s in plan["strategies"]]


def test_setup_margin_candidate_is_not_blindly_auto_applied():
    plan = dsf.build_plan(
        {}, {}, {"PLATFORM": "sky130hd"},
        check="timing", tcheck={"tier": "minor", "wns_ns": -0.01},
        route={"status": "clean", "total_violations": 0})
    selected = dsf._live_auto_strategy(plan)
    assert selected is not None
    assert selected["id"] != "setup_slack_margin"
    forced = dsf._live_auto_strategy(plan, rank_first="setup_slack_margin")
    assert forced is not None and forced["id"] == "setup_slack_margin"
