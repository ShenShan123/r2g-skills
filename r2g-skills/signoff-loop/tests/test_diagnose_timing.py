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


def test_fmax_mode_minor_offers_period_relax_before_area_change():
    """Fmax-mode projects own their clock: a minor post-route miss relaxes the
    period (recorded chain) before any CORE_UTILIZATION change (2026-09-29 AIC
    cohort: shake128 stalled at minor after utilization_reduce)."""
    tcheck = {"tier": "minor", "wns_ns": -0.17, "clock_period_ns": 4.53}
    plan = dsf.build_plan({}, {}, {"PLATFORM": "sky130hd", "CORE_UTILIZATION": "20"},
                          check="timing", tcheck=tcheck, fmax_mode=True)
    ids = [s["id"] for s in plan["strategies"]]
    assert "period_relax" in ids
    assert ids.index("period_relax") < ids.index("utilization_reduce")
    pr = plan["strategies"][ids.index("period_relax")]
    assert 4.53 < float(pr["sdc_edits"]["CLOCK_PERIOD"]) < 5.0


def test_fixed_period_minor_never_relaxes_clock():
    tcheck = {"tier": "minor", "wns_ns": -0.17, "clock_period_ns": 10.0}
    plan = dsf.build_plan({}, {}, {"PLATFORM": "sky130hd", "CORE_UTILIZATION": "20"},
                          check="timing", tcheck=tcheck, fmax_mode=False)
    assert "period_relax" not in [s["id"] for s in plan["strategies"]]


def test_is_fmax_mode_requires_ok_winner(tmp_path):
    rep = tmp_path / "reports"
    rep.mkdir()
    assert dsf._is_fmax_mode(tmp_path) is False
    (rep / "fmax_search.json").write_text(json.dumps({"status": "inconclusive"}))
    assert dsf._is_fmax_mode(tmp_path) is False
    (rep / "fmax_search.json").write_text(json.dumps({"status": "ok", "winner": {"period": 4.5}}))
    assert dsf._is_fmax_mode(tmp_path) is True


def test_fmax_mode_period_relax_repeats_within_cap():
    """2026-09-30 AIC v2: chacha20's single relax re-placed worse (40 -> 101
    violators) and the loop spent its iterations on area. In Fmax mode
    period_relax stays available after use, bounded by FMAX_RELAX_CAP x winner."""
    tcheck = {"tier": "minor", "wns_ns": -0.10, "clock_period_ns": 3.894}
    cfg = {"PLATFORM": "sky130hd", "CORE_UTILIZATION": "20"}
    plan = dsf.build_plan({}, {}, cfg, check="timing", tcheck=tcheck, exclude=("period_relax",),
                          fmax_mode=True, fmax_winner=3.66843)
    pr = [s for s in plan["strategies"] if s["id"] == "period_relax"]
    assert pr and pr[0]["repeatable"] is True
    assert 3.894 < float(pr[0]["sdc_edits"]["CLOCK_PERIOD"]) <= 3.66843 * dsf.FMAX_RELAX_CAP


def test_fmax_mode_period_relax_repeat_stops_at_cap_first_relax_unbounded():
    tcheck = {"tier": "minor", "wns_ns": -0.30, "clock_period_ns": 4.30}
    cfg = {"PLATFORM": "sky130hd", "CORE_UTILIZATION": "20"}
    rep = dsf.build_plan({}, {}, cfg, check="timing", tcheck=tcheck, exclude=("period_relax",),
                         fmax_mode=True, fmax_winner=3.66843)
    assert "period_relax" not in [s["id"] for s in rep["strategies"]]    # 4.83 > 1.2 x 3.67
    first = dsf.build_plan({}, {}, cfg, check="timing", tcheck=tcheck,
                           fmax_mode=True, fmax_winner=3.66843)
    assert "period_relax" in [s["id"] for s in first["strategies"]]      # first relax unchanged


def test_fixed_period_period_relax_never_repeats():
    tcheck = {"tier": "severe", "wns_ns": -1.2, "clock_period_ns": 4.0}
    plan = dsf.build_plan({}, {}, {"PLATFORM": "nangate45", "CORE_UTILIZATION": "30"},
                          check="timing", tcheck=tcheck, exclude=("period_relax",))
    assert "period_relax" not in [s["id"] for s in plan["strategies"]]


def test_fmax_winner_reads_ok_search_only(tmp_path):
    rep = tmp_path / "reports"; rep.mkdir()
    assert dsf._fmax_winner(tmp_path) is None
    (rep / "fmax_search.json").write_text(json.dumps({"status": "ok", "winner": {"period": 3.5}}))
    assert dsf._fmax_winner(tmp_path) == 3.5
