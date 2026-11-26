"""ASAP7 repair recipes in the DRC plan: eligibility from the current Calibre rule families, chain order, no repeats."""
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "reports"))
import diagnose_signoff_fix as D  # noqa: E402


def drc(*rules, status="fail"):
    return {"status": status, "total_violations": 3 * len(rules),
            "categories": {r: {"count": 3, "description": r} for r in rules}}


def ids(drc_json, **cfg):
    plan = D.build_plan(drc_json, {}, {"PLATFORM": "asap7", **cfg}, check="drc")
    return [s["id"] for s in plan["strategies"]], plan


def test_upper_metal_only():
    assert ids(drc("M5.S.4", "M6.S.5"))[0] == ["asap7_upper_metal_skip"]


def test_gate_with_upper_metal_is_the_combo():
    got, _ = ids(drc("GATE.S.3", "M5.S.4"))
    assert got == ["asap7_gate_pad1_um"]


def test_gate_um_v1_offers_combo_then_v1():
    assert ids(drc("GATE.S.3", "M7.S.4", "V1.S.1"))[0] == ["asap7_gate_pad1_um", "asap7_v1_obs_bloat"]


def test_chain_after_combo_offers_only_whats_left_and_never_repeats():
    after_combo = {"CELL_PAD_IN_SITES_DETAIL_PLACEMENT": "1", "TECH_LEF": D.ASAP7_UM_TECH_LEF}
    assert ids(drc("GATE.S.3", "V1.S.1"), **after_combo)[0] == ["asap7_v1_obs_bloat", "asap7_gate_pad2"]
    after_v1 = {**after_combo, "SC_LEF": D.ASAP7_V1_SC_LEF}
    assert ids(drc("GATE.S.3"), **after_v1)[0] == ["asap7_gate_pad2"]
    got, plan = ids(drc("GATE.S.3"), **{**after_v1, "CELL_PAD_IN_SITES_DETAIL_PLACEMENT": "2"})
    assert got == [] and plan["status"] == "residual" and "GATE" in plan["residual_reason"]


def test_upper_metal_offered_even_when_padding_already_present():
    assert ids(drc("M5.S.4"), CELL_PAD_IN_SITES_DETAIL_PLACEMENT="1")[0] == ["asap7_upper_metal_skip"]


def test_family_without_recipe_is_an_explicit_residual():
    got, plan = ids(drc("M1.S.6"))
    assert got == [] and plan["status"] == "residual" and "OTHER" in plan["residual_reason"]


def test_excluded_recipe_is_not_offered():
    plan = D.build_plan(drc("M5.S.4"), {}, {"PLATFORM": "asap7"}, check="drc", exclude={"asap7_upper_metal_skip"})
    assert plan["strategies"] == []


def test_drc_timeout_stays_a_tooling_residual():
    plan = D.build_plan(drc("GATE.S.3", status="timeout"), {}, {"PLATFORM": "asap7"}, check="drc")
    assert plan["strategies"] == [] and "timeout" in plan["residual_reason"]


def test_other_platforms_unchanged():
    plan = D.build_plan(drc("m3.2"), {}, {"PLATFORM": "sky130hd", "CORE_UTILIZATION": "40"}, check="drc")
    assert [s["id"] for s in plan["strategies"]] == ["density_relief"]


@pytest.mark.parametrize("rel,prefix", [
    ("asap7_tech_asym.lef", "c1cc22f0895272b4"), ("asap7sc7p5t_R_edge_pin_window.lef", "f26bfe2b12d36dc3"),
    ("pdn_m1m2m3.tcl", "6b65d4a7978f4e83"), ("recipes/asap7_tech_um_skip.lef", "b85075d3d0911598"),
    ("recipes/asap7sc7p5t_R_pin_window_obsbloat10.lef", "246c07f581b82297")])
def test_shipped_assets_match_the_recorded_runs(rel, prefix):
    assert hashlib.sha256((D.ASAP7_ASSETS / rel).read_bytes()).hexdigest().startswith(prefix)


def test_recipe_paths_point_at_shipped_files():
    assert Path(D.ASAP7_UM_TECH_LEF).is_file() and Path(D.ASAP7_V1_SC_LEF).is_file()
