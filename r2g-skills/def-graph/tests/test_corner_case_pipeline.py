"""End-to-end corner-case verification of the RTL->Graph dataset conversion.

Drives the REAL feature workers + label extractors + PyG graph builder over the
hand-computable synthetic fixture in ``fixtures/corner_synth.py`` and asserts
every output against independently hand-derived ground truth. This is the
integration counterpart to ``tools/verify_graph_dataset.py`` (which cross-checks a
real built dataset against raw liberty/LEF/DEF) — it exercises corner cases the
real nangate45 designs don't contain and proves the stages COMPOSE correctly
through their production entry points.

Built 2026-07-06 during the nangate45 RTL->Graph verification round. See the
fixture module docstring for the full list of corner cases and the exact
topology. Every expected number here was derived by hand from the fixture, not
copied from a prior run — a regression in any extractor flips an assertion.
"""
from __future__ import annotations

import os
import sys

import pytest

pytest.importorskip("pandas")
pytest.importorskip("torch")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures"))
import corner_synth as cs  # noqa: E402


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    workdir = tmp_path_factory.mktemp("corner_e2e")
    return cs.build(str(workdir), with_graph=True, variants="bcdef")


# ---------------------------------------------------------------------------- #
# nodes_gate: cell_type_id vocabulary, area/power, orientation/status ids       #
# ---------------------------------------------------------------------------- #
def test_cell_type_ids_std_sorted_plus_macro(built):
    g = cs.rows_by(built["nodes_gate.csv"], "inst_name")
    # std cells sorted: DFF_X1=0 FA_X1=1 FILLCELL_X1=2 INV_X1=3 MUX2_X1=4
    # NAND2_X1=5 TAPCELL_X1=6 ; UNKNOWN=7 ; MACRO (SRAM_8x4)=8
    assert g["i_dff"]["cell_type_id"] == "0"
    assert g["i_fa"]["cell_type_id"] == "1"
    assert g["i_fill"]["cell_type_id"] == "2"
    assert g["i_inv"]["cell_type_id"] == "3"
    assert g["i_mux"]["cell_type_id"] == "4"
    assert g["i_nand"]["cell_type_id"] == "5"
    assert g["i_tap"]["cell_type_id"] == "6"
    # SRAM comes from the macro lib -> the shared MACRO id (N_std+1 = 8), NOT UNKNOWN(7)
    assert g["i_sram"]["cell_type_id"] == "8"


def test_gate_area_power_orient_status(built):
    g = cs.rows_by(built["nodes_gate.csv"], "inst_name")
    assert g["i_dff"]["cell_area"] == "4.523000"
    assert g["i_dff"]["cell_power"] == "5.000000"
    assert g["i_sram"]["cell_area"] == "250.000000"
    # orientation ids: N=0 S=1 FN=4 FS=5 ; status PLACED=0 FIXED=1
    assert (g["i_nand"]["orientation"], g["i_nand"]["orientation_id"]) == ("S", "1")
    assert (g["i_dff"]["orientation"], g["i_dff"]["orientation_id"]) == ("FN", "4")
    assert (g["i_fa"]["orientation"], g["i_fa"]["orientation_id"]) == ("FS", "5")
    assert (g["i_tap"]["placement_status"], g["i_tap"]["placement_status_id"]) == ("FIXED", "1")


# ---------------------------------------------------------------------------- #
# nodes_net: driver/sink (chip-perspective PIN, bus dir), macro flag, layers     #
# ---------------------------------------------------------------------------- #
def test_net_driver_sink_counts(built):
    n = cs.rows_by(built["nodes_net.csv"], "net_name")
    # chip INPUT port drives, its input-pin loads sink:
    assert (n["clk_net"]["num_drivers"], n["clk_net"]["num_sinks"]) == ("1", "2")
    assert (n["n_din"]["num_drivers"], n["n_din"]["num_sinks"]) == ("1", "2")
    # real gate output drives, input pins sink:
    assert (n["n_i1"]["num_drivers"], n["n_i1"]["num_sinks"]) == ("1", "2")
    # reset port drives its single mux-select sink:
    assert (n["rstn_net"]["num_drivers"], n["rstn_net"]["num_sinks"]) == ("1", "1")
    # TWO drivers (mux Z + sram rd_out[0], an OUTPUT bus pin) into the chip OUTPUT sink:
    assert (n["n_dout"]["num_drivers"], n["n_dout"]["num_sinks"]) == ("2", "1")


def test_net_connects_macro_flag(built):
    n = cs.rows_by(built["nodes_net.csv"], "net_name")
    assert n["n_mac"]["connects_macro_flag"] == "1"    # touches SRAM addr_in
    assert n["n_dout"]["connects_macro_flag"] == "1"   # touches SRAM rd_out
    assert n["clk_net"]["connects_macro_flag"] == "1"  # touches SRAM clk
    assert n["n_i1"]["connects_macro_flag"] == "0"     # pure std-cell net
    assert n["n_din"]["connects_macro_flag"] == "0"


def test_net_num_layer(built):
    n = cs.rows_by(built["nodes_net.csv"], "net_name")
    assert n["n_i1"]["num_layer"] == "3"   # metal1 + metal2 + metal3
    assert n["n_i2"]["num_layer"] == "1"   # two metal1 statements
    assert n["n_dout"]["num_layer"] == "1"


def test_net_type_ids(built):
    n = cs.rows_by(built["nodes_net.csv"], "net_name")
    assert n["clk_net"]["net_type_id"] == "3"   # clock (SDC clock port)
    assert n["rstn_net"]["net_type_id"] == "4"  # reset (name)
    assert n["n_din"]["net_type_id"] == "0"     # signal


# ---------------------------------------------------------------------------- #
# nodes_pin: pin_type_id classification corner cases                            #
# ---------------------------------------------------------------------------- #
def test_pin_type_ids(built):
    p = {(r["inst_name"], r["pin_name"]): r["pin_type_id"] for r in built["nodes_pin.csv"]}
    assert p[("i_dff", "CK")] == "5"                 # clock
    assert p[("i_sram", "clk")] == "5"               # clock (macro clk pin)
    assert p[("i_inv", "A")] == "0"                  # input, name bucket A
    assert p[("i_fa", "B")] == "1"                   # input, name bucket B
    assert p[("i_fa", "CI")] == "2"                  # input, name bucket C
    assert p[("i_dff", "D")] == "3"                  # input, generic
    assert p[("i_inv", "ZN")] == "4"                 # output
    # THE select-on-direction guard: mux S is an INPUT select -> 10;
    # fa S is an OUTPUT (sum) -> 4, NOT 10.
    assert p[("i_mux", "S")] == "10"
    assert p[("i_fa", "S")] == "4"
    # bus-pin direction resolution: addr_in[3] input(name A)->0, rd_out[0] output->4
    assert p[("i_sram", "addr_in[3]")] == "0"
    assert p[("i_sram", "rd_out[0]")] == "4"


def test_pin_sum_load_cap_excludes_output_maxcap(built):
    p = {(r["inst_name"], r["pin_name"]): r for r in built["nodes_pin.csv"]}
    # clk_net input loads: DFF CK cap 2.0 + SRAM clk cap 3.0 = 5.0 (PIN has none,
    # and no OUTPUT max_capacitance leaks in).
    assert float(p[("i_dff", "CK")]["sum_pin_cap_fF"]) == pytest.approx(5.0)
    # n_din input loads: INV A 1.0 + FA CI 1.4 = 2.4
    assert float(p[("i_inv", "A")]["sum_pin_cap_fF"]) == pytest.approx(2.4)


# ---------------------------------------------------------------------------- #
# nodes_iopin + edges_iopin_net                                                 #
# ---------------------------------------------------------------------------- #
def test_iopin_direction_and_net_type(built):
    io = cs.rows_by(built["nodes_iopin.csv"], "iopin_name")
    assert (io["clk_i"]["pin_direction"], io["clk_i"]["pin_direction_id"]) == ("INPUT", "0")
    assert (io["dout_o"]["pin_direction"], io["dout_o"]["pin_direction_id"]) == ("OUTPUT", "1")
    assert io["clk_i"]["net_type_id"] == "3"    # clock
    assert io["rstn_i"]["net_type_id"] == "4"   # reset
    # tap distance is a real (positive) distance to the single FIXED tap cell
    assert float(io["din_i"]["nearest_tap_distance_um"]) > 0


# ---------------------------------------------------------------------------- #
# metadata (global features)                                                     #
# ---------------------------------------------------------------------------- #
def test_metadata_global_features(built):
    md = built["metadata.csv"][0]
    assert md["num_cells"] == "8"
    assert md["num_nets"] == "9"
    assert md["num_ios"] == "4"
    assert md["dbu_unit"] == "1000"
    assert float(md["die_width"]) == pytest.approx(20.0)
    assert float(md["die_height"]) == pytest.approx(20.0)
    assert md["V_nom"] == "1.10"
    assert md["freq_Hz"] == "500000000"          # 1/2ns
    # tracks_per_layer must be NUMERIC (the 2026-07-06 string->0 fix)
    float(md["tracks_per_layer"])


# ---------------------------------------------------------------------------- #
# wirelength labels (RECT stripping + Manhattan centerline + log1p)             #
# ---------------------------------------------------------------------------- #
def test_wirelength_values_and_rect_stripped(built):
    import math
    w = cs.rows_by(built["wirelength.csv"], "Net")
    # n_i2: seg (3000,1000)->(1000,1000)=2000 ; RECT (-50 -50 50 50) STRIPPED ;
    #       (1000,1000)->(1000,5000)=4000 ; total 6000 DBU / 1000 = 6.0 um.
    # Un-stripped, the RECT offsets would inject a huge phantom segment.
    assert float(w["n_i2"]["WireLength_um"]) == pytest.approx(6.0)
    # n_i1: metal1 2000 + metal2 4000 + metal3 2000 = 8000 -> 8.0 um
    assert float(w["n_i1"]["WireLength_um"]) == pytest.approx(8.0)
    # n_mac diagonal metal2 (1000,5000)->(12000,12000): |11000|+|7000| = 18.0
    assert float(w["n_mac"]["WireLength_um"]) == pytest.approx(18.0)
    for r in built["wirelength.csv"]:
        assert float(r["label"]) == pytest.approx(math.log1p(float(r["WireLength_um"])))
        assert r["mask_wl"] == "true"   # all fixture nets are SIGNAL


# ---------------------------------------------------------------------------- #
# congestion labels — the 2-vector method (label_raw = raw, label = smoothed)    #
# ---------------------------------------------------------------------------- #
def test_congestion_two_vector_raw_and_smoothed(built):
    """The congestion extractor (Congestion_Parse.py port, commit c9b9e3a) emits
    a 2-vector per cell, each averaged over the cell's bbox GCells:
        cell_congestion = mean(gaussian_util)         (smoothed utilization)
        label           = mean(sqrt(gaussian_util))   (== ref node_label[1])
        label_raw       = mean(sqrt(util))            (== ref node_label[0], raw)
    No cell LEF is supplied by this fixture, so every cell falls back to its single
    ORIGIN GCell -> the bbox is one GCell -> label == sqrt(cell_congestion) exactly.

    i_fill sits at (9000,9000) -> GCell (4,4) at STEP 2000, which no routed wire
    crosses, so its RAW congestion (label_raw) is EXACTLY 0. Its SMOOTHED
    cell_congestion is small-but-nonzero because the scipy-matched Gaussian
    (sigma=1.0, radius=int(4*sigma+0.5)=4) spreads congestion in from routed
    GCells up to 4 cells away — WIDER than the retired 3x3 (radius-1) kernel, whose
    locality this test previously (wrongly) assumed. The merge that changed the
    kernel must re-run this guardrail; see failure-patterns.md
    "Congestion 2-vector method (radius-4 Gaussian)"."""
    import math
    c = cs.rows_by(built["cell_congestion.csv"], "Cell")
    assert len(built["cell_congestion.csv"]) == 8  # one row per placed component
    for r in built["cell_congestion.csv"]:
        # single-GCell fallback => label is the sqrt of the smoothed value
        assert float(r["label"]) == pytest.approx(math.sqrt(float(r["cell_congestion"])), abs=1e-6)
    # i_fill's own GCell carries no routed demand -> RAW congestion is exactly 0 ...
    assert float(c["i_fill"]["label_raw"]) == pytest.approx(0.0, abs=1e-12)
    # ... but the radius-4 Gaussian spreads a small SMOOTHED value into it (>0).
    assert 0.0 < float(c["i_fill"]["cell_congestion"]) < 0.1


# ---------------------------------------------------------------------------- #
# graph topology (variant node/edge counts + clock/fill exclusion + symmetry)    #
# ---------------------------------------------------------------------------- #
def _load(built, variant):
    import torch
    return torch.load(os.path.join(built["dataset"], f"{variant}_graph.pt"), weights_only=False)


# Hand-derived from the fixture (see the module docstring for the full working):
#   signal graph = 6 gates, 7 signal nets, 2 iopins, 17 gate-pins on signal nets.
#   gate cliques (over each gate's signal pins): inv C(2,2)=1, nand C(3,2)=3,
#     dff C(2,2)=1, fa C(5,2)=10, mux C(3,2)=3, sram C(2,2)=1  -> 19
#   net cliques  (over each net's pin+iopin endpoints): n_din 3, n_i1 3, n_i2 3,
#     n_q 3, n_nand2 1, n_mac 1, n_dout 3                       -> 17
#   gate-pin edges = 17 ; pin-net edges = 17 ; iopin-net edges = 2
# Every count below is (undirected base) * 2 for the bidirectional edge_index.
EXPECTED = {
    # variant: (nodes, edges, node_type_ids_present)
    "b": (32, 72, {0, 1, 2, 3}),   # gate+net+iopin+pin ; 36 base = 17+17+2
    "c": (15, 38, {0, 1, 2}),      # gate+net+iopin ; 19 base = 17 pin + 2 iopin edges
    "d": (25, 68, {0, 2, 3}),      # gate+iopin+pin ; 34 base = 17 gate-pin + 17 net-clique
    "e": (19, 72, {2, 3}),         # iopin+pin ; 36 base = 19 gate-clique + 17 net-clique
    "f": (8,  34, {0, 2}),         # gate+iopin ; 17 base net-cliques over gates/iopins
}


def _adjacency(d):
    adj = {}
    ei = d.edge_index
    for k in range(ei.shape[1]):
        u, v = int(ei[0, k]), int(ei[1, k])
        adj.setdefault(d.node_name[u], set()).add(d.node_name[v])
    return adj


@pytest.mark.parametrize("variant", ["b", "c", "d", "e", "f"])
def test_variant_node_edge_counts(built, variant):
    man = built["manifest"]["variants"][variant]
    nodes, edges, types = EXPECTED[variant]
    assert man["nodes"] == nodes, f"{variant}: node count"
    assert man["edges"] == edges, f"{variant}: edge count"
    assert {int(t) for t in man["nodes_by_type"]} == types, f"{variant}: node types"


@pytest.mark.parametrize("variant", ["b", "c", "d", "e", "f"])
def test_variant_edges_symmetric_and_valid(built, variant):
    d = _load(built, variant)
    n = int(d.x.shape[0])
    ei = d.edge_index
    assert int(ei.max()) < n and int(ei.min()) >= 0, "edge index out of range"
    adj = _adjacency(d)
    for w, outs in adj.items():
        for u in outs:
            assert w in adj.get(u, set()), f"{variant}: edge {w}->{u} not symmetric"


@pytest.mark.parametrize("variant", ["b", "c", "d", "e", "f"])
def test_variant_excludes_clocktree_and_physical(built, variant):
    """The clock + reset nets, the pins exclusive to them, and FILL/TAP cells are
    absent from EVERY view (whether the entity is a node or folded into edges)."""
    names = set(_load(built, variant).node_name)
    for absent in ("clk_net", "rstn_net", "i_tap", "i_fill", "clk_i", "rstn_i"):
        assert absent not in names, f"{variant}: {absent!r} must never be a node"


# ---- folded-entity feature/label carrying, per view ------------------------ #
def _net_feature_vec(built, net_name):
    """The NET_SCHEMA feature row for a net (independently asserted correct in
    test_net_* above) — the value a folded-net edge_attr must reproduce."""
    from graph_lib import NET_SCHEMA
    r = cs.rows_by(built["nodes_net.csv"], "net_name")[net_name]
    return [float(r[c]) for c in NET_SCHEMA]


def _find_edge(d, name_a, name_b):
    ei = d.edge_index
    for k in range(ei.shape[1]):
        if {d.node_name[int(ei[0, k])], d.node_name[int(ei[1, k])]} == {name_a, name_b}:
            return k
    return None


def test_c_folds_pins_into_gate_net_edges(built):
    """View c: pins are NOT nodes; each becomes a gate<->net edge carrying the
    pin's [pin_type_id, sum_pin_cap_fF] on edge_attr."""
    d = _load(built, "c")
    assert not any("/" in nm for nm in d.node_name), "c must have no pin nodes"
    k = _find_edge(d, "i_inv", "n_i1")     # folds pin i_inv/ZN (output -> pin_type 4)
    assert k is not None
    assert float(d.edge_attr[k][0]) == pytest.approx(4.0)     # pin_type_id
    assert float(d.edge_attr[k][1]) == pytest.approx(2.1)     # net-level sum load cap


def test_d_folds_nets_into_pin_cliques_with_net_feats_and_wl(built):
    """View d: nets are NOT nodes; each becomes a clique over its pins/iopins
    carrying the NET features + the wirelength label (y4)."""
    import math
    d = _load(built, "d")
    assert 1 not in {int(t) for t in d.x[:, 0].long().tolist()}, "d must have no net nodes"
    # n_i1 connects pins i_inv/ZN, i_nand/A1, i_mux/A -> a clique edge between two of them
    k = _find_edge(d, "i_inv/ZN", "i_nand/A1")
    assert k is not None
    assert [round(float(x), 3) for x in d.edge_attr[k]] == \
        [round(v, 3) for v in _net_feature_vec(built, "n_i1")]
    w = cs.rows_by(built["wirelength.csv"], "Net")["n_i1"]
    assert float(d.edge_y[k][4]) == pytest.approx(math.log1p(float(w["WireLength_um"])), abs=1e-4)


def test_e_folds_gates_and_nets_into_pin_cliques(built):
    """View e: gates AND nets fold into pin cliques; only iopin+pin nodes remain.
    A net-clique edge carries net features + wirelength; a gate-clique edge carries
    the gate congestion label (y1)."""
    import math
    d = _load(built, "e")
    assert {int(t) for t in d.x[:, 0].long().tolist()} == {2, 3}
    # net n_i1 clique edge carries its net features + wirelength label
    k = _find_edge(d, "i_inv/ZN", "i_nand/A1")
    assert k is not None
    assert [round(float(x), 3) for x in d.edge_attr[k]] == \
        [round(v, 3) for v in _net_feature_vec(built, "n_i1")]
    w = cs.rows_by(built["wirelength.csv"], "Net")["n_i1"]
    assert float(d.edge_y[k][4]) == pytest.approx(math.log1p(float(w["WireLength_um"])), abs=1e-4)
    # gate i_fa clique edge (fa pins A & CO are on different nets, both fa pins):
    kg = _find_edge(d, "i_fa/A", "i_fa/CO")
    assert kg is not None, "expected a clique edge among i_fa's pins"
    cong = cs.rows_by(built["cell_congestion.csv"], "Cell")["i_fa"]
    assert float(d.edge_y[kg][1]) == pytest.approx(float(cong["label"]), abs=1e-4)  # y1 congestion


def test_f_folds_nets_into_gate_cliques_with_net_feats_and_wl(built):
    """View f: gate/iopin nodes only; each net becomes a clique over the gates it
    connects, edge_attr = NET features, edge_y4 = wirelength label."""
    import math
    d = _load(built, "f")
    assert {int(t) for t in d.x[:, 0].long().tolist()} == {0, 2}
    k = _find_edge(d, "i_inv", "i_nand")   # both on n_i1
    assert k is not None
    assert [round(float(x), 3) for x in d.edge_attr[k]] == \
        [round(v, 3) for v in _net_feature_vec(built, "n_i1")]
    w = cs.rows_by(built["wirelength.csv"], "Net")["n_i1"]
    assert float(d.edge_y[k][4]) == pytest.approx(math.log1p(float(w["WireLength_um"])), abs=1e-4)


def test_b_graph_topology_excludes_clocktree_and_physical(built):
    d = _load(built, "b")
    names = set(d.node_name)
    # clock + reset nets, their exclusive pins, and FILL/TAP cells are NOT nodes
    for absent in ("clk_net", "rstn_net", "i_tap", "i_fill", "i_dff/CK",
                   "i_sram/clk", "i_mux/S", "clk_i", "rstn_i"):
        assert absent not in names, f"{absent!r} must be excluded from the graph"
    # signal entities ARE present
    for present in ("i_inv", "i_sram", "n_i1", "din_i", "dout_o", "i_inv/ZN"):
        assert present in names

    # adjacency by name
    ei = d.edge_index
    adj = {}
    for k in range(ei.shape[1]):
        u, v = int(ei[0, k]), int(ei[1, k])
        adj.setdefault(d.node_name[u], set()).add(d.node_name[v])
    # undirected: every edge appears both ways
    for w, outs in adj.items():
        for u in outs:
            assert w in adj.get(u, set()), f"edge {w}->{u} not symmetric"
    # gate<->pin<->net wiring is exactly right
    assert adj["i_inv"] == {"i_inv/A", "i_inv/ZN"}
    assert adj["i_inv/ZN"] == {"i_inv", "n_i1"}
    assert adj["n_i1"] == {"i_inv/ZN", "i_nand/A1", "i_mux/A"}
    assert adj["din_i"] == {"n_din"}


def test_manifest_flags_label_gaps_but_status_ok_ish(built):
    man = built["manifest"]
    # Build-time platform provenance stamp (failure-patterns.md #30): the
    # verifier trusts THIS over the mutable, round-re-pointed config.mk.
    assert man["platform"] == "nangate45"
    # ir_drop + timing stubs have no rows for this design -> flagged, and the
    # manifest downgrades to ok_with_label_gaps (never a silent 'ok').
    assert man["status"] == "ok_with_label_gaps"
    assert man["label_health"]["ir_drop.csv"]["status"] != "ok"
    assert man["label_health"]["wirelength.csv"]["status"] == "ok"


def test_b_graph_wirelength_label_joins_to_nets(built):
    import math
    import torch
    d = _load(built, "b")
    nt = d.x[:, 0].long()
    # y4 = wirelength label lives on NET nodes (type 1); join by name must land
    w = cs.rows_by(built["wirelength.csv"], "Net")
    net_pos = [(i, d.node_name[i]) for i in range(len(d.node_name)) if int(nt[i]) == 1]
    assert net_pos, "no net nodes"
    for i, name in net_pos:
        expected = math.log1p(float(w[name]["WireLength_um"]))
        assert float(d.y[i, 4]) == pytest.approx(expected, abs=1e-4), f"net {name} wl label"
    # y4 on non-net nodes must be NaN (label only applies to nets)
    gate_pos = [i for i in range(len(d.node_name)) if int(nt[i]) == 0]
    assert all(math.isnan(float(d.y[i, 4])) for i in gate_pos)
