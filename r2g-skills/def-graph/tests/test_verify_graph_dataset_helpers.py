"""Unit tests for tools/verify_graph_dataset.py's INDEPENDENT truth parsers.

The verifier re-derives ground truth with its own local parsers (never techlib) so a
parser bug on either side surfaces as a check mismatch instead of agreeing with
itself. These tests pin the verifier-side parsers on synthetic liberty/LEF/DEF
fixtures — including the platform hinges the 2026-07-06 nangate45 round exercised
(dbu=2000, bus() macro pins, CLASS BLOCK masters, vertical-demand keying).

torch is NOT required: the helpers under test are pure stdlib+re, and the module
import is guarded to skip when torch/pandas are absent (bare CI checkout).
"""
from __future__ import annotations

import importlib.util
import os
import sys

import pytest

# repo root = tests/ -> def-graph/ -> r2g-skills/ -> <repo>; the tool stays at <repo>/tools/
_TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))), "tools")

pytest.importorskip("pandas")
pytest.importorskip("torch")

spec = importlib.util.spec_from_file_location(
    "verify_graph_dataset", os.path.join(_TOOLS, "verify_graph_dataset.py"))
vgd = importlib.util.module_from_spec(spec)
sys.modules["verify_graph_dataset"] = vgd
spec.loader.exec_module(vgd)


def test_read_liberty_truth_bus_and_units(tmp_path):
    lib = tmp_path / "m.lib"
    lib.write_text("""
library (m) {
  capacitive_load_unit (1,ff);
  nom_voltage : 1.10;
  cell (SRAM_1) {
    area : 100.5;
    statetable ("CK", "IQ") {
    }
    pin(clk) {
      direction : input;
      capacitance : 25.0;
    }
    bus(addr) {
      direction : input;
      capacitance : 5.0;
    }
  }
}
""")
    cells = vgd.read_liberty_truth([str(lib)])
    assert cells["SRAM_1"]["area"] == 100.5
    assert cells["SRAM_1"]["is_seq"] is True
    assert vgd.lib_pin_truth(cells, "SRAM_1", "clk") == ("INPUT", 25.0)
    # per-bit member resolves through the bus base
    assert vgd.lib_pin_truth(cells, "sram_1", "addr[7]") == ("INPUT", 5.0)
    assert vgd.lib_pin_truth(cells, "SRAM_1", "nope") == ("", None)


def test_read_liberty_truth_pf_scaling(tmp_path):
    lib = tmp_path / "p.lib"
    lib.write_text("""
library (p) {
  capacitive_load_unit (1.0000000000, "pf");
  cell (INV_1) {
    pin(A) {
      direction : input;
      capacitance : 0.0021;
    }
  }
}
""")
    cells = vgd.read_liberty_truth([str(lib)])
    assert vgd.lib_pin_truth(cells, "INV_1", "A")[1] == pytest.approx(2.1)


def test_read_liberty_truth_block_form_leakage(tmp_path):
    """BUG-2 (verifier-silent-lies-audit-2026-07-07): asap7/gf180 write leakage as
    block-form ``leakage_power(){value:X}`` (gf180 quotes it) with NO scalar
    ``cell_leakage_power``. Matching only the scalar left power=None on those
    platforms, so the verifier's ``ext.gate power`` check passed vacuously. The parser
    must capture the block-form value. (Fails on pre-fix code: power is None.)"""
    lib = tmp_path / "blk.lib"
    lib.write_text("""
library (blk) {
  cell (AND2_x1) {
    area : 1.5;
    leakage_power () {
      when : "!A" ;
      value : 0.00051 ;
    }
    leakage_power () {
      value : 0.00029065 ;
    }
    pin(A) { direction : input; capacitance : 1.0; }
  }
  cell (OR2_x1) {
    area : 2.0;
    leakage_power () {
      value : "0.00081234" ;
    }
    pin(A) { direction : input; capacitance : 1.0; }
  }
}
""")
    cells = vgd.read_liberty_truth([str(lib)])
    # first `value` inside the leakage_power group is captured (non-vacuous power check)
    assert cells["AND2_X1"]["power"] == pytest.approx(0.00051)
    # gf180-style QUOTED value must parse too
    assert cells["OR2_X1"]["power"] == pytest.approx(0.00081234)


def test_scalar_leakage_still_parsed(tmp_path):
    """Guard against regressing the active-tech (sky130/nangate45) scalar path while
    adding block-form support for the parked techs."""
    lib = tmp_path / "sc.lib"
    lib.write_text("""
library (sc) {
  cell (INVx1) {
    area : 0.5;
    cell_leakage_power : 0.0123 ;
    pin(A) { direction : input; capacitance : 1.0; }
  }
}
""")
    cells = vgd.read_liberty_truth([str(lib)])
    assert cells["INVX1"]["power"] == pytest.approx(0.0123)


def test_read_lef_truth_layers_and_blocks(tmp_path):
    tech = tmp_path / "t.lef"
    tech.write_text("""
LAYER metal1
  TYPE ROUTING ;
  PITCH 0.14 ;
  DIRECTION HORIZONTAL ;
END metal1
LAYER via1
  TYPE CUT ;
END via1
LAYER metal2
  TYPE ROUTING ;
  PITCH 0.19 ;
  DIRECTION VERTICAL ;
END metal2
""")
    macro = tmp_path / "m.lef"
    macro.write_text("""
MACRO fakeram45_64x7
  CLASS BLOCK ;
  SIZE 50.0 BY 40.0 ;
END fakeram45_64x7
MACRO BUF_X1
  CLASS CORE ;
END BUF_X1
""")
    layers, blocks = vgd.read_lef_truth(str(tech), [str(macro)])
    assert layers == {"metal1": (0.14, "HORIZONTAL"), "metal2": (0.19, "VERTICAL")}
    assert blocks == {"FAKERAM45_64X7"}


def test_read_def_truth_dbu_demand_orientation(tmp_path):
    """dbu=2000 scaling + vertical demand keyed (x,y) — the transpose regression."""
    d = tmp_path / "t.def"
    d.write_text("""
DESIGN t ;
UNITS DISTANCE MICRONS 2000 ;
DIEAREA ( 0 0 ) ( 20000 20000 ) ;
GCELLGRID X 0 DO 5 STEP 4000 ;
GCELLGRID Y 0 DO 5 STEP 4000 ;
TRACKS X 100 DO 50 STEP 400 LAYER metal2 ;
TRACKS Y 100 DO 60 STEP 280 LAYER metal1 ;
COMPONENTS 2 ;
 - u1 INV_X1 + PLACED ( 2000 4000 ) N ;
 - u2 SRAM_1 + PLACED ( 8000 8000 ) FS ;
END COMPONENTS
PINS 1 ;
 - clk + NET clk + DIRECTION INPUT + USE SIGNAL
  + PLACED ( 100 200 ) N ;
END PINS
NETS 1 ;
 - n1 ( u1 ZN ) ( u2 addr[0] )
  + ROUTED metal2 ( 6000 2000 ) ( 6000 10000 )
  + USE SIGNAL ;
END NETS
END DESIGN
""")
    t = vgd.read_def_truth(str(d))
    assert t["dbu"] == 2000.0
    # comps now also carry placement `status` (PLACED/FIXED) for the Group B
    # placement_status_id re-derivation in verify_graph_dataset.feature_stat_checks.
    assert t["comps"]["u1"] == {"master": "INV_X1", "status": "PLACED",
                                "x": 2000, "y": 4000, "orient": "N"}
    assert t["pins"]["clk"]["dir"] == "INPUT" and t["pins"]["clk"]["x"] == 100
    assert t["nets"]["n1"] == [("u1", "ZN"), ("u2", "addr[0]")]
    # 8000 dbu vertical wire = 4 um
    assert t["net_len"]["n1"] == pytest.approx(4.0)
    # vertical demand keys are (x_gcell, y_gcell): x=6000//4000=1, y spans 0..2
    assert set(t["demand_v"]) == {(1, 0), (1, 1), (1, 2)}
    assert not t["demand_h"]
    assert t["tracks"] == {"metal2": 50, "metal1": 60}


def test_dense_gaussian_r4_neutral_on_uniform_grid():
    # a radius-4 REFLECT-boundary separable gaussian preserves a constant field
    # (normalized weights sum to 1; reflect duplicates the constant at the edges)
    grid = [[0.5] * 6 for _ in range(7)]  # asymmetric 7x6
    out = vgd.dense_gaussian_r4(grid, 7, 6)
    assert all(abs(out[x][y] - 0.5) < 1e-12 for x in range(7) for y in range(6))


def test_dense_gaussian_r4_spreads_a_spike_symmetrically():
    # a single spike well away from the boundary spreads to its 4-neighbourhood and
    # conserves total mass (a proper normalized smoother)
    n = 11
    grid = [[0.0] * n for _ in range(n)]
    grid[5][5] = 1.0
    out = vgd.dense_gaussian_r4(grid, n, n)
    assert out[5][5] > out[5][6] > out[5][7] > 0.0          # decays with distance
    assert out[5][6] == pytest.approx(out[6][5], abs=1e-12)  # isotropic
    total = sum(out[x][y] for x in range(n) for y in range(n))
    assert total == pytest.approx(1.0, abs=1e-9)             # mass-conserving


def test_lef_macro_sizes_parses_size_by(tmp_path):
    lef = tmp_path / "cells.lef"
    lef.write_text(
        "MACRO INV_X1\n  SIZE 0.5 BY 2.72 ;\nEND INV_X1\n"
        "MACRO SRAM_8x4\n  SIZE 40.0 BY 60.5 ;\nEND SRAM_8x4\n")
    sizes = vgd._lef_macro_sizes([str(lef)])
    assert sizes["INV_X1"] == pytest.approx((0.5, 2.72))
    assert sizes["SRAM_8x4"] == pytest.approx((40.0, 60.5))
    assert vgd._lef_macro_sizes(["/nonexistent.lef"]) == {}


# ---------------------------------------------------------------------------
# irdrop_label_ok — mirror extract_irdrop.tcl's has_irdrop noise-floor gate.
# Regression for the 2026-07-07 verifier false-fail: the check asserted
# label==log1p(IR/P95) for EVERY row, red-flagging every low-IR design (iir
# P95=0.044mV -> all labels legitimately 0). See failure-patterns.md.
# ---------------------------------------------------------------------------
import math  # noqa: E402


def _ir_df(rows, with_flag=True):
    import pandas as pd
    cols = ["IR_Drop_mV", "P95_mV", "label"] + (["has_irdrop"] if with_flag else [])
    return pd.DataFrame([r[: len(cols)] for r in rows], columns=cols)


def test_irdrop_below_floor_all_zero_labels_pass():
    # iir case: P95 < 0.05 -> has_irdrop false -> label 0 is CORRECT, must not fail.
    df = _ir_df([[0.024, 0.044, 0.0, "false"], [0.049, 0.044, 0.0, "false"]])
    ok, detail = vgd.irdrop_label_ok(df)
    assert ok, detail
    assert "active=0" in detail


def test_irdrop_above_floor_log1p_labels_pass():
    p95 = 0.065
    df = _ir_df([[0.10, p95, math.log1p(0.10 / p95), "true"],
                 [p95, p95, math.log1p(1.0), "true"]])
    ok, detail = vgd.irdrop_label_ok(df)
    assert ok, detail
    assert "active=2" in detail


def test_irdrop_mixed_active_and_floored_pass():
    df = _ir_df([[0.10, 0.065, math.log1p(0.10 / 0.065), "true"],
                 [0.02, 0.044, 0.0, "false"]])
    ok, detail = vgd.irdrop_label_ok(df)
    assert ok, detail


def test_irdrop_corrupted_active_label_fails():
    df = _ir_df([[0.10, 0.065, 0.999, "true"]])  # wrong log1p value
    ok, _ = vgd.irdrop_label_ok(df)
    assert not ok


def test_irdrop_nonzero_label_below_floor_fails():
    # has_irdrop false but label != 0 -> extractor contract violated -> must fail.
    df = _ir_df([[0.02, 0.044, 0.5, "false"]])
    ok, _ = vgd.irdrop_label_ok(df)
    assert not ok


def test_irdrop_legacy_csv_without_has_irdrop_derives_floor():
    # No has_irdrop column: floor derived from P95>=0.05.
    below = _ir_df([[0.02, 0.044, 0.0]], with_flag=False)
    assert vgd.irdrop_label_ok(below)[0]
    above_bad = _ir_df([[0.10, 0.10, 0.123]], with_flag=False)  # >=0.05 but wrong label
    assert not vgd.irdrop_label_ok(above_bad)[0]
