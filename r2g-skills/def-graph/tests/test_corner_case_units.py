"""Targeted corner-case unit tests for the RTL->Graph extraction pipeline.

Complements ``test_corner_case_pipeline.py`` (the end-to-end synthetic-design
run) with focused unit tests for corner cases best exercised in isolation — paths
the real nangate45 designs do NOT reach, so the raw-file cross-check in
``tools/verify_graph_dataset.py`` cannot see them. Several of these guard fixes
landed in the 2026-07-06 nangate45 verification round:

  * ff_bank / latch_bank multibit-sequential detection (was missed; asap7 ships
    27 such libs) — liberty.py.
  * netlist tie-off constants in a concatenation must not become phantom nets —
    netlist_graph.py.
  * congestion per-gcell demand key convention is (x_gcell, y_gcell) for BOTH
    axes (the 2026-07-05 transpose bug) — extract_congestion.py.
  * compute_feature_stats honesty gate (feature-side mirror of the label gate) —
    a raw/truncated CSV is 'invalid', not 'ok'.
"""
from __future__ import annotations

import textwrap

import pytest

from techlib import liberty


# --------------------------------------------------------------------------- #
# liberty: sequential-cell detection corner cases                              #
# --------------------------------------------------------------------------- #
def _lib(tmp_path, txt):
    p = tmp_path / "x.lib"
    p.write_text(textwrap.dedent(txt))
    return liberty.load_liberty_db([str(p)])


def test_ff_and_latch_bank_are_sequential(tmp_path):
    """Multibit flops/latches declare state via ff_bank()/latch_bank(); the plain
    ``ff``/``latch`` token is not followed by ``(`` there, so the old regex missed
    them (asap7 ships 27 ff_bank libs)."""
    db = _lib(tmp_path, """
        library (t) {
          cell (MBFF) {
            area : 10.0;
            ff_bank (IQ, IQN, 2) {
              clocked_on : CK;
            }
            pin (CK) { direction : input; }
          }
          cell (MLATCH) {
            area : 5.0;
            latch_bank (IQ, IQN, 2) {
              enable : G;
            }
            pin (G) { direction : input; }
          }
        }
    """)
    assert db["cells"]["MBFF"]["is_sequential"] is True
    assert db["cells"]["MLATCH"]["is_sequential"] is True


def test_ff_latch_statetable_still_sequential_combinational_not(tmp_path):
    db = _lib(tmp_path, """
        library (t) {
          cell (DFF) {
            area : 4;
            ff (IQ, IQN) {
              clocked_on : CK;
            }
            pin (CK) { direction : input; }
          }
          cell (DLAT) {
            area : 3;
            latch (IQ, IQN) {
              enable : G;
            }
            pin (G) { direction : input; }
          }
          cell (CG) {
            area : 2;
            statetable ("CK E","IQ") {
              table : "-";
            }
            pin (CK) { direction : input; }
          }
          cell (INV) {
            area : 1;
            pin (A) { direction : input; }
            pin (ZN) { direction : output; }
          }
        }
    """)
    assert db["cells"]["DFF"]["is_sequential"] is True
    assert db["cells"]["DLAT"]["is_sequential"] is True
    assert db["cells"]["CG"]["is_sequential"] is True
    assert db["cells"]["INV"]["is_sequential"] is False  # ff_bank fix must not over-match


# --------------------------------------------------------------------------- #
# liberty: pin classification corner cases                                     #
# --------------------------------------------------------------------------- #
def test_inout_and_feedthru_pins_classify_as_11(tmp_path):
    db = _lib(tmp_path, """
        library (t) {
          cell (IOC) {
            area : 1;
            pin (PAD) {
              direction : inout;
            }
            pin (FT) {
              direction : feedthru;
            }
          }
        }
    """)
    assert liberty.classify_pin_type("IOC", "PAD", db) == 11
    assert liberty.classify_pin_type("IOC", "FT", db) == 11


def test_power_ground_pins_classify_12_13(tmp_path):
    db = _lib(tmp_path, """
        library (t) {
          cell (C) {
            area : 1;
            pin (VDD) { direction : input; }
            pin (VSS) { direction : input; }
            pin (A)   { direction : input; }
          }
        }
    """)
    assert liberty.classify_pin_type("C", "VDD", db) == 12
    assert liberty.classify_pin_type("C", "VSS", db) == 13


def test_bus_multidigit_index_resolves_to_bus_attrs(tmp_path):
    """DEF connects bus members per-bit (addr[10]); liberty declares them once at
    the bus() level. The [idx] fallback must resolve any width of index."""
    db = _lib(tmp_path, """
        library (t) {
          cell (RAM) {
            area : 1;
            bus (addr) {
              direction : input;
              capacitance : 4.0;
            }
            bus (dout) {
              direction : output;
            }
          }
        }
    """)
    for member in ("addr[0]", "addr[7]", "addr[10]", "addr[123]"):
        assert liberty.get_pin_direction("RAM", member, db) == "INPUT"
        assert liberty.get_pin_cap_fF("RAM", member, db) == pytest.approx(4.0)
    assert liberty.get_pin_direction("RAM", "dout[9]", db) == "OUTPUT"


def test_pin_cap_units_pf_scales_to_ff(tmp_path):
    """A pf capacitive_load_unit must scale pin caps x1000 into fF."""
    db = _lib(tmp_path, """
        library (t) {
          capacitive_load_unit (1.0, pf);
          cell (C) {
            area : 1;
            pin (A) {
              direction : input;
              capacitance : 0.005;
            }
          }
        }
    """)
    assert liberty.get_pin_cap_fF("C", "A", db) == pytest.approx(5.0)  # 0.005 pf -> 5 fF


# --------------------------------------------------------------------------- #
# cell_types: UNKNOWN fallback for a master not in the liberty                  #
# --------------------------------------------------------------------------- #
def test_cell_type_unknown_fallback(tmp_path):
    from techlib.cell_types import build_runtime_map, cell_type_id
    db = _lib(tmp_path, """
        library (t) {
          cell (AAA) { area : 1; pin (A){direction:input;} }
          cell (BBB) { area : 1; pin (A){direction:input;} }
        }
    """)
    mp = build_runtime_map(db)
    assert mp["AAA"] == 0 and mp["BBB"] == 1  # sorted
    assert mp["UNKNOWN"] == 2
    assert cell_type_id("AAA", mp) == 0
    assert cell_type_id("NOT_IN_LIB", mp) == 2  # -> UNKNOWN


# --------------------------------------------------------------------------- #
# LEF: CUT layers excluded; a VIA re-declaring LAYER doesn't add routing layers #
# --------------------------------------------------------------------------- #
def test_lef_cut_and_via_layers_not_routing(tmp_path):
    from techlib import lef
    tl = tmp_path / "t.lef"
    tl.write_text(textwrap.dedent("""
        LAYER metal1
          TYPE ROUTING ;
          DIRECTION HORIZONTAL ;
          PITCH 0.2 0.2 ;
        END metal1
        LAYER via1
          TYPE CUT ;
        END via1
        LAYER metal2
          TYPE ROUTING ;
          DIRECTION VERTICAL ;
          PITCH 0.2 0.2 ;
        END metal2
        VIA via1_2 DEFAULT
          LAYER metal1 ;
            RECT -0.1 -0.1 0.1 0.1 ;
          LAYER via1 ;
          LAYER metal2 ;
        END via1_2
    """))
    layers = lef.routing_layers(str(tl))
    assert layers == ["metal1", "metal2"]  # via1 (CUT) excluded; VIA block adds nothing
    info = lef.routing_layer_info(str(tl))
    assert set(info.keys()) == {"metal1", "metal2"}
    # HORIZONTAL layer uses the y-pitch (pv[1]); VERTICAL uses x-pitch (pv[0])
    assert info["metal1"]["direction"] == "HORIZONTAL"
    assert info["metal2"]["direction"] == "VERTICAL"


# --------------------------------------------------------------------------- #
# congestion: per-gcell demand key convention is (x_gcell, y_gcell) both axes    #
# --------------------------------------------------------------------------- #
def _congestion():
    import importlib
    return importlib.import_module("extract_congestion")


def test_congestion_demand_key_convention_no_transpose():
    """The 2026-07-05 transpose bug keyed vertical demand (y, x). With an
    ASYMMETRIC grid a transposed key lands in a different (often out-of-range)
    gcell, so this test would catch a regression."""
    ec = _congestion()
    step_x, step_y, dbu = 100, 1000, 1000.0
    # Horizontal wire: y fixed at 2500 (y_gcell=2), x spans 0..350 (x_gcell 0..3)
    dh, dv = {}, {}
    ec.add_route_segment(dh, dv, 0, 2500, 350, 2500, step_x, step_y, dbu)
    assert set(dh.keys()) == {(0, 2), (1, 2), (2, 2), (3, 2)}
    assert dv == {}
    # Vertical wire: x fixed at 250 (x_gcell=2), y spans 0..2500 (y_gcell 0..2)
    dh2, dv2 = {}, {}
    ec.add_route_segment(dh2, dv2, 250, 0, 250, 2500, step_x, step_y, dbu)
    assert set(dv2.keys()) == {(2, 0), (2, 1), (2, 2)}  # (x_gcell, y_gcell), NOT (y, x)
    assert dh2 == {}


def test_congestion_capacity_h_v_from_layer_directions():
    ec = _congestion()
    # one HORIZONTAL + one VERTICAL layer, equal pitch: caps should be symmetric
    layer_info = {"m1": {"pitch": 0.2, "direction": "HORIZONTAL"},
                  "m2": {"pitch": 0.2, "direction": "VERTICAL"}}
    cap_h, cap_v = ec.calculate_grid_capacities(2000, 2000, 1000.0, layer_info)
    # grid 2x2 um, pitch 0.2 -> 10 tracks * 2 um = 20 um each direction
    assert cap_h == pytest.approx(20.0)
    assert cap_v == pytest.approx(20.0)
    # a pure-horizontal layer contributes only to cap_h
    ch2, cv2 = ec.calculate_grid_capacities(2000, 2000, 1000.0,
                                            {"m1": {"pitch": 0.2, "direction": "HORIZONTAL"}})
    assert ch2 > 0 and cv2 == 0


# --------------------------------------------------------------------------- #
# netlist_graph: tie-off constants in a concatenation are not phantom nets      #
# --------------------------------------------------------------------------- #
def test_netlist_constants_in_concat_are_dropped():
    import netlist_graph as ng
    assert ng.extract_signal_names("{1'b0, sig}") == ["sig"]
    assert ng.extract_signal_names("{8'hFF, data[3]}") == ["data[3]"]
    assert ng.extract_signal_names("{2'b10, a, b}") == ["a", "b"]
    # standalone constant still normalizes (whole-expr path unchanged)
    assert ng.extract_signal_names("1'b0") == ["CONST0"]
    assert ng.extract_signal_names("1'b1") == ["CONST1"]
    # a plain net with a bracketed bus index is untouched
    assert ng.extract_signal_names("net_a[3]") == ["net_a[3]"]


def test_netlist_parses_named_connections_and_vocab(tmp_path):
    import netlist_graph as ng
    v = tmp_path / "n.v"
    v.write_text(textwrap.dedent("""
        module top (a, y);
          input a;
          output y;
          wire n1;
          INV_X1 g1 ( .A(a), .ZN(n1) );
          INV_X1 g2 ( .A(n1), .ZN(y) );
        endmodule
    """))
    cells, nets = ng.parse_verilog(str(v))
    assert cells == {"g1": "INV_X1", "g2": "INV_X1"}
    # n1 connects g1.ZN (driver) and g2.A (sink)
    assert set(nets["n1"]) == {("g1", "ZN"), ("g2", "A")}


def test_netlist_macro_gets_shared_macro_id_not_interleaved(tmp_path):
    """netlist_graph's cell-type vocabulary must match the feature stage on a macro
    design: the macro collapses to the shared MACRO id (= N_std + 1) and std-cell ids
    are NOT shifted by it. Regression for the 2026-07-07 env-wiring bug — run_graphs.sh
    exported R2G_SC_LIB_FILES=$LIB_FILES (macro libs folded in) and netlist_graph loaded
    lib_db from that subset, so macros were either interleaved into the sorted std vocab
    (drifting std ids) or dropped to UNKNOWN. The fix builds lib_db from the FULL liberty
    but keys the id space on the STD-CELL-ONLY subset (failure-patterns.md #12/#19)."""
    import os
    import subprocess
    import sys

    torch = pytest.importorskip("torch")
    skill_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ng_py = os.path.join(skill_root, "scripts", "extract", "graph", "netlist_graph.py")

    std = tmp_path / "std.lib"
    std.write_text(textwrap.dedent("""
        library (std) {
          cell (INV_X1) {
            area : 0.5;
          }
          cell (NAND2_X1) {
            area : 0.8;
          }
        }
    """))
    macro = tmp_path / "macro.lib"
    macro.write_text(textwrap.dedent("""
        library (macro) {
          cell (SRAM_8x4) {
            area : 250.0;
          }
        }
    """))
    v = tmp_path / "n.v"
    v.write_text(textwrap.dedent("""
        module top (a, y);
          input a; output y;
          wire n1, n2;
          INV_X1 g_inv ( .A(a), .ZN(n1) );
          NAND2_X1 g_nand ( .A1(n1), .A2(a), .ZN(n2) );
          SRAM_8x4 u_sram ( .clk(a), .rd_out(y) );
        endmodule
    """))
    out = tmp_path / "netlist_graph.pt"
    env = dict(os.environ)
    # exactly what run_graphs.sh now exports: full liberty + std-cell-only subset
    env["R2G_LIB_FILES"] = f"{std} {macro}"
    env["R2G_SC_LIB_FILES"] = str(std)
    env["R2G_PLATFORM"] = "nangate45"
    r = subprocess.run([sys.executable, ng_py, str(v), str(out), "top"],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    data = torch.load(str(out), weights_only=False)
    idof = {data.cell_names[i]: int(data.x[i][0]) for i in range(len(data.cell_names))}
    # std vocab sorted: INV_X1=0, NAND2_X1=1 ; UNKNOWN=2 ; MACRO=3
    assert idof["g_inv"] == 0, "std-cell id drifted (macro interleaved into std vocab)"
    assert idof["g_nand"] == 1, "std-cell id drifted (macro interleaved into std vocab)"
    assert idof["u_sram"] == 3, "macro must map to the shared MACRO id, not UNKNOWN(2) or an interleaved std id"


# --------------------------------------------------------------------------- #
# compute_feature_stats: honesty gate (feature-side mirror of the label gate)    #
# --------------------------------------------------------------------------- #
def test_feature_stats_flags_missing_columns_invalid(tmp_path):
    import compute_feature_stats as cfs
    fd = tmp_path / "features"
    fd.mkdir()
    # a raw/wrong-schema dump at the canonical path: has rows but no schema cols
    (fd / "nodes_gate.csv").write_text("foo,bar\n1,2\n3,4\n")
    res = cfs.summarize(str(fd), "nodes_gate")
    assert res["status"] == "invalid"
    assert "missing required column" in res["reason"]


def test_feature_stats_flags_truncated_rows_invalid(tmp_path):
    import compute_feature_stats as cfs
    fd = tmp_path / "features"
    fd.mkdir()
    # complete header, but a row truncated mid-write (short row -> None fields)
    (fd / "nodes_gate.csv").write_text(
        "graph_id,inst_name,master,cell_type_id,cell_area,cell_power,x_um,y_um,"
        "orientation,orientation_id,placement_status,placement_status_id\n"
        "d,i0,INV_X1,3,0.5,1.5,1.0,1.0,N,0,PLACED,0\n"
        "d,i1,INV_X1\n")  # truncated
    res = cfs.summarize(str(fd), "nodes_gate")
    assert res["status"] == "invalid"
    assert "truncated" in res["reason"] or "missing a required column" in res["reason"]


def test_feature_stats_ok_on_complete_csv(tmp_path):
    import compute_feature_stats as cfs
    fd = tmp_path / "features"
    fd.mkdir()
    (fd / "nodes_gate.csv").write_text(
        "graph_id,inst_name,master,cell_type_id,cell_area,cell_power,x_um,y_um,"
        "orientation,orientation_id,placement_status,placement_status_id\n"
        "d,i0,INV_X1,3,0.5,1.5,1.0,1.0,N,0,PLACED,0\n")
    res = cfs.summarize(str(fd), "nodes_gate")
    assert res["status"] == "ok"
    assert res["rows"] == 1
