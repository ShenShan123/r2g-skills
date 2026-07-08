"""Timing fix catalog (spec §5.7.4): period_relax + utilization_reduce."""
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
