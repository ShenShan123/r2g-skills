"""Tests for diagnose_signoff_fix.py: signoff (DRC/LVS) violation→fix-plan logic."""
from __future__ import annotations

import diagnose_signoff_fix as d


def _drc(status, count=0, cats=None):
    return {"status": status, "total_violations": count, "categories": cats or {}}


def _antenna_cats(n=7, layer="METAL7_ANTENNA"):
    return {layer: {"count": n, "description": ""}}


def test_clean_drc_yields_no_strategies():
    plan = d.build_plan(_drc("clean"), {}, {}, check="drc")
    assert plan["status"] == "clean"
    assert plan["strategies"] == []


def test_antenna_fail_yields_three_ordered_strategies():
    plan = d.build_plan(_drc("fail", 7, _antenna_cats()), {}, {"CORE_UTILIZATION": "10"}, check="drc")
    ids = [s["id"] for s in plan["strategies"]]
    assert ids == ["antenna_diode_iters", "antenna_route_effort", "antenna_density_relief"]
    assert plan["dominant_category"] == "METAL7_ANTENNA"
    # density relief computes a concrete lowered utilization
    relief = plan["strategies"][2]["config_edits"]
    assert relief["CORE_UTILIZATION"] == "5"


def test_applied_strategy_is_filtered_out():
    cfg = {"CORE_ANTENNACELL": "ANTENNA_X1", "MAX_REPAIR_ANTENNAS_ITER_GRT": "10",
           "MAX_REPAIR_ANTENNAS_ITER_DRT": "10", "CORE_UTILIZATION": "10"}
    plan = d.build_plan(_drc("fail", 7, _antenna_cats()), {}, cfg, check="drc")
    ids = [s["id"] for s in plan["strategies"]]
    assert "antenna_diode_iters" not in ids
    assert ids[0] == "antenna_route_effort"


def test_exhausted_antenna_is_residual():
    cfg = {"CORE_ANTENNACELL": "ANTENNA_X1", "MAX_REPAIR_ANTENNAS_ITER_GRT": "10",
           "MAX_REPAIR_ANTENNAS_ITER_DRT": "10", "DETAILED_ROUTE_ARGS": "-droute_end_iteration 10",
           "CORE_UTILIZATION": "5"}
    plan = d.build_plan(_drc("fail", 7, _antenna_cats()), {}, cfg, check="drc")
    assert plan["status"] == "residual"
    assert plan["strategies"] == []


def test_non_antenna_drc_is_unhandled_residual():
    plan = d.build_plan(_drc("fail", 3, {"M2.SP.1": {"count": 3}}), {}, {}, check="drc")
    assert "non-antenna" in plan["residual_reason"]
    assert plan["strategies"] == []


def test_stuck_drc_is_out_of_scope():
    plan = d.build_plan(_drc("stuck"), {}, {}, check="drc")
    assert plan["strategies"] == []
    assert "out_of_v1_scope" in plan["residual_reason"]


def test_lvs_unknown_yields_resolve_strategy():
    plan = d.build_plan({}, {"status": "unknown", "mismatch_count": None}, {}, check="lvs")
    assert [s["id"] for s in plan["strategies"]] == ["lvs_resolve_unknown"]


def test_lvs_cpp_crash_is_residual():
    lvs = {"status": "fail", "log_info": {"errors": ["...sort_circuit::gen_log_entry SIGSEGV"]}}
    plan = d.build_plan({}, lvs, {}, check="lvs")
    assert plan["strategies"] == []
    assert "klayout_cpp_crash" in plan["residual_reason"]


def test_lvs_macro_emits_operator_only_strategy():
    lvs = {"status": "fail", "log_info": {"errors": ["Netlists don't match"]}}
    cfg = {"VERILOG_FILES": "/x/fakeram45_64x32.v /x/top.v"}
    plan = d.build_plan({}, lvs, cfg, check="lvs")
    s = plan["strategies"][0]
    assert s["id"] == "lvs_macro_cdl"
    assert s["auto_apply"] is False
    assert "operator_note" in s
