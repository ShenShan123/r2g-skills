"""Tests for the PyG graph-dataset stage (scripts/extract/graph/, 2026-07-05).

Two tiers:
  * pure-pandas tests (view filtering, clique helper) — always run;
  * tensor tests — skipped without torch/torch_geometric (the stage itself
    skips cleanly at run_graphs.sh level on such machines).

The port was equivalence-tested against the RTL2Graph originals on real cordic
nangate45 data (all five variants: node tensors + edge sets identical). The one
INTENTIONAL divergence is pinned here: edge_attr/edge_type/edge_y alignment —
the originals concatenated [forwards | reverses] while repeating attr rows
pairwise, misaligning attrs for every edge past the first (measured 171/3001
aligned on cordic c_graph; the port scores 3001/3001).
"""
from __future__ import annotations

import csv
import os

import pytest

# The graph stage is the skill's only pandas consumer (everything else is pure
# stdlib) — skip the whole module cleanly where it isn't installed, exactly as
# run_graphs.sh itself skips.
pd = pytest.importorskip("pandas")

import graph_lib as gl  # noqa: E402

_FLOW = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "scripts", "flow")
_RUN_GRAPHS = os.path.join(_FLOW, "run_graphs.sh")


# --------------------------------------------------------------------------- #
# Synthetic mini-design CSV fixture (features + labels).                       #
# --------------------------------------------------------------------------- #

def _write_csv(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


@pytest.fixture()
def mini_csvs(tmp_path):
    feat = tmp_path / "features"
    lab = tmp_path / "labels"
    feat.mkdir()
    lab.mkdir()
    g = "mini"

    _write_csv(feat / "nodes_gate.csv",
               ["graph_id", "inst_name", "master", *gl.GATE_SCHEMA],
               [[g, "g1", "INV_X1", 0, 1.0, 2.0, 10.0, 20.0, 0, 0],
                [g, "g2", "INV_X2", 1, 1.5, 2.5, 30.0, 40.0, 0, 0],
                [g, "f1", "FILLCELL_X1", 86, 1.0, 0.0, 50.0, 60.0, 0, 0]])
    _write_csv(feat / "nodes_net.csv",
               ["graph_id", "net_name", *gl.NET_SCHEMA],
               [[g, "n1", 0, 2, 3, 1, 2, 0, 2, 12.5],
                [g, "nclk", 3, 5, 6, 1, 5, 0, 3, 99.0]])
    _write_csv(feat / "nodes_iopin.csv",
               ["graph_id", "iopin_name", "net_name", "net_type_id", *gl.IOPIN_SCHEMA],
               [[g, "in_port", "n1", 0, 0.0, 5.0, 1.0, 0],
                [g, "clk", "nclk", 3, 0.0, 9.0, 1.0, 0]])
    _write_csv(feat / "nodes_pin.csv",
               ["graph_id", "inst_name", "pin_name", *gl.PIN_SCHEMA],
               [[g, "g1", "A", 0, 1.5],
                [g, "g1", "ZN", 4, 1.5],
                [g, "g2", "A", 0, 1.5],
                [g, "g2", "CK", 5, 0.5]])
    _write_csv(feat / "edges_gate_pin.csv",
               ["graph_id", "inst_name", "pin_name"],
               [[g, "g1", "A"], [g, "g1", "ZN"], [g, "g2", "A"], [g, "g2", "CK"]])
    _write_csv(feat / "edges_pin_net.csv",
               ["graph_id", "inst_name", "pin_name", "net_name", "net_type_id"],
               [[g, "g1", "ZN", "n1", 0], [g, "g2", "A", "n1", 0],
                [g, "g1", "A", "n1", 0],
                [g, "g2", "CK", "nclk", 3]])
    _write_csv(feat / "edges_iopin_net.csv",
               ["graph_id", "iopin_name", "net_name", "net_type_id"],
               [[g, "in_port", "n1", 0], [g, "clk", "nclk", 3]])
    _write_csv(feat / "metadata.csv",
               ["graph_id", *gl.METADATA_SCHEMA],
               [[g, 3, 2, 2, 1.5, 100.0, 100.0, 10000.0, 1000, 0.55, 40, 0, 5.0,
                 "met1:100", 1.8, 100000000]])

    _write_csv(lab / "cell_congestion.csv",
               ["Design", "Cell", "cell_type", "cell_congestion", "label"],
               [[g, "g1", "INV_X1", 0.04, 0.2]])  # g2 missing -> NaN
    _write_csv(lab / "ir_drop.csv",
               ["Design", "Cell", "X", "Y", "Voltage_V", "IR_Drop_mV", "P95_mV", "label", "has_irdrop"],
               [[g, "g1", 1, 2, 1.79, 10.0, 9.0, 0.7, "true"],
                [g, "g1", 1, 2, 1.78, 20.0, 9.0, 0.9, "true"]])  # dup Cell -> max
    _write_csv(lab / "timing_features.csv",
               ["Design", "Cell", "Cell_Slack_ns", "Path_Delay_ns", "label", "in_sta_path"],
               [[g, "g1", 5.0, 5.0, 1.79, "true"], [g, "g2", 8.0, 2.0, 1.10, "true"]])
    _write_csv(lab / "wirelength.csv",
               ["Design", "Net", "NetType", "WireLength_um", "label", "mask_wl"],
               [[g, "n1", "SIGNAL", 12.5, 2.6, "true"]])
    return str(feat), str(lab)


# --------------------------------------------------------------------------- #
# Pure-pandas tier.                                                            #
# --------------------------------------------------------------------------- #

def test_views_filter_physical_cells_and_non_signal_nets(mini_csvs):
    feat, _ = mini_csvs
    gate_df, net_df, iopin_df, pin_df, edges_gp, edges_pn, edges_in = gl.build_feature_views(feat, "mini")
    assert gate_df["inst_name"].tolist() == ["g1", "g2"]          # FILL dropped
    assert net_df["net_name"].tolist() == ["n1"]                  # clock net dropped
    assert iopin_df["iopin_name"].tolist() == ["in_port"]         # clock port dropped
    # g2/CK rides the clock net only -> dropped from pins AND gate-pin edges
    assert pin_df[["inst_name", "pin_name"]].values.tolist() == [
        ["g1", "A"], ["g1", "ZN"], ["g2", "A"]]
    assert edges_gp[["inst_name", "pin_name"]].values.tolist() == [
        ["g1", "A"], ["g1", "ZN"], ["g2", "A"]]
    assert edges_in["iopin_name"].tolist() == ["in_port"]


def test_clique_pairs_dedup_and_order():
    assert gl.clique_pairs([3, 1, 3, 2]) == [(1, 2), (1, 3), (2, 3)]
    assert gl.clique_pairs([7]) == []


# --------------------------------------------------------------------------- #
# Tensor tier (torch + torch_geometric required; pandas tier above still runs #
# on machines without them).                                                   #
# --------------------------------------------------------------------------- #

try:
    import torch
    import torch_geometric  # noqa: F401
    _HAS_TORCH = True
except ImportError:
    torch = None
    _HAS_TORCH = False

pytestmark_tensor = pytest.mark.skipif(not _HAS_TORCH, reason="torch/torch_geometric not installed")


def _build(variant, mini_csvs):
    import build_graphs as bg

    feat, lab = mini_csvs
    views7 = gl.build_feature_views(feat, "mini")
    label_dfs = gl.load_label_cache(lab)
    return bg.BUILDERS[variant](views7, label_dfs, "mini", "mini", 0, feat)


def _load_pt(path):
    """Load a built {v}_graph.pt as a homogeneous Data (the default output is
    HeteroData, so convert via graph_lib.hetero_to_homo)."""
    from torch_geometric.data import HeteroData
    obj = torch.load(path, weights_only=False)
    return gl.hetero_to_homo(obj) if isinstance(obj, HeteroData) else obj


@pytestmark_tensor
def test_directed_edges_interleave_alignment():
    attr = torch.tensor([[1.0], [2.0], [3.0]])
    y = torch.zeros((3, gl.Y_WIDTH))
    t = torch.tensor([0, 0, 1])
    ei, ea, et, ey, ey_raw = gl.build_directed_edges([0, 1, 2], [5, 6, 7], attr, y, t)
    assert ei.tolist() == [[0, 5, 1, 6, 2, 7], [5, 0, 6, 1, 7, 2]]
    # every fwd/rev pair shares its base attr + type
    assert ea.view(-1).tolist() == [1.0, 1.0, 2.0, 2.0, 3.0, 3.0]
    assert et.tolist() == [0, 0, 0, 0, 1, 1]
    # no base_y_raw supplied -> edge_y_raw is all-NaN, same shape as edge_y
    assert ey_raw.shape == ey.shape
    assert bool(torch.isnan(ey_raw).all())


@pytestmark_tensor
def test_directed_edges_raw_interleaves_like_labels():
    attr = torch.tensor([[1.0], [2.0]])
    y = torch.tensor([[0.0, 1.0], [0.0, 2.0]])           # normalized (2 cols for brevity)
    y = torch.cat([y, torch.zeros((2, gl.Y_WIDTH - 2))], dim=1)
    y_raw = torch.tensor([[0.0, 10.0], [0.0, 20.0]])
    y_raw = torch.cat([y_raw, torch.zeros((2, gl.Y_WIDTH - 2))], dim=1)
    t = torch.tensor([0, 1])
    _ei, _ea, _et, ey, ey_raw = gl.build_directed_edges(
        [0, 1], [2, 3], attr, y, t, base_y_raw=y_raw)
    # both interleave fwd/rev pairwise, in lockstep with edge_index
    assert ey[:, 1].tolist() == [1.0, 1.0, 2.0, 2.0]
    assert ey_raw[:, 1].tolist() == [10.0, 10.0, 20.0, 20.0]


@pytestmark_tensor
def test_b_graph_nodes_features_and_label_joins(mini_csvs):
    data = _build("b", mini_csvs)
    nt = data.x[:, 0].long()
    assert [int((nt == t).sum()) for t in range(4)] == [2, 1, 1, 3]
    assert data.node_name[:4] == ["g1", "g2", "n1", "in_port"]
    # gate features by position (g1 row): cell_type_id..placement_status_id + pad
    assert data.x[0, 2:10].tolist() == [0.0, 1.0, 2.0, 10.0, 20.0, 0.0, 0.0, 0.0]
    # labels: g1 congestion 0.2; g2 missing -> NaN; irdrop dup Cell -> max 0.9
    assert float(data.y[0, 1]) == pytest.approx(0.2)
    assert torch.isnan(data.y[1, 1])
    assert float(data.y[0, 2]) == pytest.approx(0.9)
    # net wirelength label on the net node
    assert float(data.y[2, 4]) == pytest.approx(2.6)
    # pin timing label inherits the cell's label (g1/A at index 4)
    assert data.node_name[4] == "g1/A"
    assert float(data.y[4, 3]) == pytest.approx(1.79)
    # edges: only gate-pin / pin-net / iopin-net type pairs, symmetric
    pairs = set(map(tuple, data.edge_index.t().tolist()))
    assert all((b, a) in pairs for a, b in pairs)
    tp = {(int(nt[a]), int(nt[b])) for a, b in pairs}
    assert tp <= {(0, 3), (3, 0), (3, 1), (1, 3), (2, 1), (1, 2)}


@pytestmark_tensor
def test_b_graph_y_raw_carries_raw_physical_values(mini_csvs):
    """data.y_raw mirrors data.y slot-for-slot but with the RAW physical value
    (EDA-Schema convention) from the extractor's raw column, not the log/sqrt."""
    data = _build("b", mini_csvs)
    assert hasattr(data, "y_raw") and data.y_raw.shape == data.y.shape
    # g1 (row 0): congestion raw = cell_congestion 0.04; irdrop raw = max(10,20)=20
    assert float(data.y_raw[0, 1]) == pytest.approx(0.04)
    assert float(data.y_raw[0, 2]) == pytest.approx(20.0)
    # g2 (row 1): congestion missing -> raw NaN too
    assert torch.isnan(data.y_raw[1, 1])
    # net n1 (row 2): wirelength raw = WireLength_um 12.5 (y label was log1p 2.6)
    assert float(data.y_raw[2, 4]) == pytest.approx(12.5)
    # pin g1/A (row 4): timing raw = cell worst slack Cell_Slack_ns 5.0
    assert data.node_name[4] == "g1/A"
    assert float(data.y_raw[4, 3]) == pytest.approx(5.0)
    # y (normalized) unchanged
    assert float(data.y[2, 4]) == pytest.approx(2.6)


@pytestmark_tensor
def test_c_graph_pin_edges_carry_pin_features_aligned(mini_csvs):
    data = _build("c", mini_csvs)
    nt = data.x[:, 0].long()
    assert [int((nt == t).sum()) for t in [0, 1, 2]] == [2, 1, 1]
    pin_feat = {"g1": {(0.0, 1.5), (4.0, 1.5)}, "g2": {(0.0, 1.5)}}
    checked = 0
    for k in range(data.edge_index.shape[1]):
        if int(data.edge_type[k]) != 0:
            continue
        s = int(data.edge_index[0, k])
        if int(nt[s]) != 0:
            continue
        a = (float(data.edge_attr[k, 0]), float(data.edge_attr[k, 1]))
        assert a in pin_feat[data.node_name[s]], f"edge {k} attr {a} not a pin of {data.node_name[s]}"
        checked += 1
    assert checked == 3  # (g1,ZN,n1) (g1,A,n1) (g2,A,n1) forward edges
    # pin timing label rides edge_y slot 3
    ey = data.edge_y[data.edge_type == 0]
    assert not torch.isnan(ey[:, 3]).any()


@pytestmark_tensor
def test_f_graph_net_cliques_over_gates(mini_csvs):
    data = _build("f", mini_csvs)
    nt = data.x[:, 0].long()
    assert [int((nt == t).sum()) for t in [0, 2]] == [2, 1]
    # n1 touches g1, g2, in_port -> clique of 3 endpoints = 3 base edges = 6 directed
    assert data.edge_index.shape[1] == 6
    # every edge carries n1's net features (hpwl at slot 7)
    assert torch.allclose(data.edge_attr[:, 7], torch.full((6,), 12.5))
    # and n1's wirelength label at edge_y slot 4
    assert torch.allclose(data.edge_y[:, 4], torch.full((6,), 2.6))


@pytestmark_tensor
def test_manifest_written_with_stats(mini_csvs, tmp_path, monkeypatch, capsys):
    import sys as _sys

    import build_graphs as bg

    feat, lab = mini_csvs
    out = tmp_path / "dataset"
    monkeypatch.setattr(_sys, "argv", [
        "build_graphs.py", "--features", feat, "--labels", lab,
        "--design", "mini", "--out-dir", str(out), "--variants", "bf"])
    bg.main()
    import json
    from torch_geometric.data import HeteroData
    man = json.load(open(out / "graph_manifest.json"))
    assert man["status"] == "ok" and set(man["variants"]) == {"b", "f"}
    assert man["graph_kind"] == "hetero"                       # default is hetero
    assert man["variants"]["b"]["nodes"] == 7                  # homo total preserved
    assert os.path.isfile(out / "b_graph.pt") and os.path.isfile(out / "f_graph.pt")
    # {v}_graph.pt is a HeteroData; the manifest's per-view hetero breakdown matches
    b = torch.load(out / "b_graph.pt", weights_only=False)
    assert isinstance(b, HeteroData)
    assert man["variants"]["b"]["hetero"]["node_types"] == {
        nt: int(b[nt].x.shape[0]) for nt in b.node_types}


@pytestmark_tensor
def test_design_key_mismatch_warns_loudly(mini_csvs, tmp_path, monkeypatch, capsys):
    import sys as _sys

    import build_graphs as bg

    feat, lab = mini_csvs
    monkeypatch.setattr(_sys, "argv", [
        "build_graphs.py", "--features", feat, "--labels", lab,
        "--design", "mini", "--out-dir", str(tmp_path / "d2"), "--variants", "b"])
    # break one label file's Design key
    df = pd.read_csv(os.path.join(lab, "wirelength.csv"))
    df["Design"] = "other_design"
    df.to_csv(os.path.join(lab, "wirelength.csv"), index=False)
    bg.main()
    err = capsys.readouterr().err
    assert "no rows for Design='mini'" in err and "wirelength.csv" in err


@pytestmark_tensor
def test_netlist_graph_parser_and_vocab(tmp_path):
    import netlist_graph as ng

    src = (
        "module top (a, y, clk);\n"
        "input a, clk; output y;\n"
        "wire n1; wire \\esc.w[3] ;\n"
        "NAND2_X1 g1 (.A(a), .B(n1), .ZN(\\esc.w[3] ));\n"
        "DFF_X1 \\ff[2] (.D(\\esc.w[3] ), .CK(clk), .Q(y), .QN());\n"
        "BUF_X1\n  g3 (\n    .A(a),\n    .Z(n1)\n  );\n"
        "endmodule\n"
    )
    v = tmp_path / "mini.v"
    v.write_text(src)
    cells, nets = ng.parse_verilog(str(v))
    assert cells == {"g1": "NAND2_X1", "\\ff[2]": "DFF_X1", "g3": "BUF_X1"}
    assert {i for i, _ in nets["n1"]} == {"g1", "g3"}
    assert {i for i, _ in nets["\\esc.w[3]"]} == {"g1", "\\ff[2]"}
    type_map = {"NAND2_X1": 15, "DFF_X1": 71, "BUF_X1": 6, "UNKNOWN": 95}
    data = ng.build_graph(cells, nets, type_map)
    # 3 cells + 5 nets (a, clk, y, n1, esc.w[3]); bipartite + symmetric
    assert data.num_nodes == 8
    assert data.cell_names == sorted(cells)
    ei = set(map(tuple, data.edge_index.t().tolist()))
    assert all((b, a) in ei for a, b in ei)
    ncell = len(data.cell_names)
    assert all((a < ncell) != (b < ncell) for a, b in ei)
    assert float(data.x[data.cell_names.index("g1"), 0]) == 15.0
    assert float(data.x[ncell, 0]) == -1.0


# --------------------------------------------------------------------------- #
# Label-health guard + duplicate-key guards (2026-07-05 irdrop incident).      #
# --------------------------------------------------------------------------- #

def test_label_health_flags_raw_and_mismatched_files():
    raw = pd.DataFrame({"Instance": ["i1"], "Terminal": ["VPWR"], "Voltage": [1.8]})
    good_cell = pd.DataFrame({"Design": ["d"], "Cell": ["g1"], "label": [0.1]})
    other_design = pd.DataFrame({"Design": ["other"], "Net": ["n1"], "label": [0.1]})
    dfs = {"cell_congestion.csv": good_cell, "ir_drop.csv": raw,
           "timing_features.csv": good_cell, "wirelength.csv": other_design}
    h = gl.label_health(dfs, "d")
    assert h["cell_congestion.csv"]["status"] == "ok"
    assert h["timing_features.csv"]["status"] == "ok"
    assert h["ir_drop.csv"]["status"] == "unusable"
    assert "Design" in h["ir_drop.csv"]["reason"]
    assert h["wirelength.csv"]["status"] == "no_rows_for_design"


def test_label_health_missing_label_column():
    df = pd.DataFrame({"Design": ["d"], "Cell": ["g1"], "Voltage": [1.8]})
    good = pd.DataFrame({"Design": ["d"], "Cell": ["g1"], "Net": ["n1"], "label": [0.1]})
    dfs = {"cell_congestion.csv": df, "ir_drop.csv": good,
           "timing_features.csv": good, "wirelength.csv": good}
    h = gl.label_health(dfs, "d")
    assert h["cell_congestion.csv"]["status"] == "unusable"
    assert "label" in h["cell_congestion.csv"]["reason"]


def test_label_health_flags_all_nan_label():
    """BUG-1 (verifier-silent-lies-audit-2026-07-07): a label file with the right
    columns + rows for the design but EVERY value NaN (a broken join or a partial
    dump) would make the y slot 100% NaN. label_health used to check only column/row
    PRESENCE, so it reported 'ok' and the dataset shipped green through the manifest
    AND the (NaN-vacuous) verifier. It must now report 'all_nan'. (Fails on pre-fix
    code: status was 'ok'.)"""
    nan_lbl = pd.DataFrame({"Design": ["d", "d"], "Cell": ["g1", "g2"],
                            "label": [float("nan"), float("nan")]})
    good = pd.DataFrame({"Design": ["d"], "Cell": ["g1"], "Net": ["n1"], "label": [0.1]})
    dfs = {"cell_congestion.csv": nan_lbl, "ir_drop.csv": good,
           "timing_features.csv": good, "wirelength.csv": good}
    h = gl.label_health(dfs, "d")
    assert h["cell_congestion.csv"]["status"] == "all_nan"
    assert h["cell_congestion.csv"]["status"] != "ok"


def test_label_health_all_zero_label_is_ok():
    """Complement to the all-NaN gate: a legitimately DEGENERATE label is all-ZERO
    (e.g. combinational timing, low-IR irdrop) — non-NaN — and must stay 'ok', so the
    all_nan gate never false-flags a valid design."""
    gate_zeros = pd.DataFrame({"Design": ["d", "d"], "Cell": ["g1", "g2"], "label": [0.0, 0.0]})
    net_zeros = pd.DataFrame({"Design": ["d", "d"], "Net": ["n1", "n2"], "label": [0.0, 0.0]})
    dfs = {"cell_congestion.csv": gate_zeros, "ir_drop.csv": gate_zeros,
           "timing_features.csv": gate_zeros, "wirelength.csv": net_zeros}
    h = gl.label_health(dfs, "d")
    assert all(v["status"] == "ok" for v in h.values()), h


@pytestmark_tensor
def test_duplicate_label_keys_raise_loudly():
    base = pd.DataFrame({"inst_name": ["g1", "g2"]})
    dup_cong = pd.DataFrame({"Design": ["d", "d"], "Cell": ["g1", "g1"],
                             "label": [0.1, 0.9]})
    ok_ir = pd.DataFrame({"Design": ["d"], "Cell": ["g1"], "label": [0.2]})
    dfs = {"cell_congestion.csv": dup_cong, "ir_drop.csv": ok_ir}
    with pytest.raises(ValueError, match="duplicate 'Cell'.*cell_congestion"):
        gl.build_gate_label_values(base, dfs, "d")


@pytestmark_tensor
def test_duplicate_pin_label_keys_raise_loudly():
    base = pd.DataFrame({"inst_name": ["g1"], "pin_name": ["A"]})
    tf = pd.DataFrame({"Design": ["d", "d"], "Pin": ["g1/A", "g1/A"],
                       "label": [1.0, 2.0]})
    with pytest.raises(ValueError, match="duplicate 'Pin'"):
        gl.build_pin_label_values(base, {"timing_features.csv": tf}, "d")


@pytestmark_tensor
def test_ir_drop_per_pdn_node_dups_still_grouped_not_raised():
    # ir_drop.csv legitimately has one row per PDN node; groupby-max must keep
    # absorbing them (no false-positive from the new duplicate-key guard).
    base = pd.DataFrame({"inst_name": ["g1"]})
    cong = pd.DataFrame({"Design": ["d"], "Cell": ["g1"], "label": [0.3]})
    ir = pd.DataFrame({"Design": ["d", "d"], "Cell": ["g1", "g1"],
                       "label": [0.7, 0.9]})
    vals = gl.build_gate_label_values(base, {"cell_congestion.csv": cong,
                                             "ir_drop.csv": ir}, "d")
    assert float(vals[1][0]) == pytest.approx(0.9)


@pytestmark_tensor
def test_manifest_flags_label_gap_and_y_goes_nan(mini_csvs, tmp_path, monkeypatch, capsys):
    import json
    import sys as _sys

    import build_graphs as bg

    feat, lab = mini_csvs
    # Simulate the 2026-07-05 incident: a killed irdrop stage left the RAW
    # PDNSim dump at the canonical path.
    with open(os.path.join(lab, "ir_drop.csv"), "w") as f:
        f.write("Instance,Terminal,Layer,X location,Y location,Voltage\n"
                "g1,VPWR,li1,1.0,2.0,1.79\n")
    out = tmp_path / "gap_ds"
    monkeypatch.setattr(_sys, "argv", [
        "build_graphs.py", "--features", feat, "--labels", lab,
        "--design", "mini", "--out-dir", str(out), "--variants", "b"])
    bg.main()
    err = capsys.readouterr().err
    assert "ir_drop.csv unusable" in err
    man = json.load(open(out / "graph_manifest.json"))
    assert man["status"] == "ok_with_label_gaps"
    assert man["label_health"]["ir_drop.csv"]["status"] == "unusable"
    assert man["label_health"]["wirelength.csv"]["status"] == "ok"
    data = _load_pt(out / "b_graph.pt")   # hetero default -> reconstruct homo
    gate_mask = data.x[:, 0] == 0
    assert bool(torch.isnan(data.y[gate_mask, 2]).all())      # irdrop slot NaN
    assert not bool(torch.isnan(data.y[gate_mask, 1]).all())  # congestion intact


# --------------------------------------------------------------------------- #
# Heterogeneous graphs (the 2026-07-16 default graph_kind).                    #
# --------------------------------------------------------------------------- #

def _edge_multiset(data):
    """Order-free signature of a homo graph's directed edges + their attr/type/y
    (for round-trip comparison, since hetero regroups edge order by relation)."""
    ei = data.edge_index
    et = getattr(data, "edge_type", None)
    ea = getattr(data, "edge_attr", None)
    ey = getattr(data, "edge_y", None)
    rows = []
    for k in range(ei.shape[1]):
        key = [int(ei[0, k]), int(ei[1, k])]
        if et is not None:
            key.append(int(et[k]))
        if ea is not None:
            key.append(tuple(round(float(v), 5) for v in ea[k].tolist()))
        if ey is not None:
            key.append(tuple(("nan" if v != v else round(float(v), 5)) for v in ey[k].tolist()))
        rows.append(tuple(key))
    return sorted(rows)


def _tensor_eq(a, b):
    if a is None and b is None:
        return True
    if a is None or b is None or a.shape != b.shape:
        return False
    return bool(torch.equal(torch.nan_to_num(a, nan=-1e30), torch.nan_to_num(b, nan=-1e30)))


@pytestmark_tensor
@pytest.mark.parametrize("variant", ["b", "c", "d", "e", "f"])
def test_homo_to_hetero_round_trip_exact(variant, mini_csvs):
    """homo -> hetero -> homo preserves every value: x, y, y_raw, node_name,
    the edge set (+ attr/type/y) and the RC parasitic edge set."""
    homo = _build(variant, mini_csvs)
    het = gl.homo_to_hetero(homo)
    back = gl.hetero_to_homo(het)
    assert _tensor_eq(homo.x, back.x)
    assert _tensor_eq(getattr(homo, "y", None), getattr(back, "y", None))
    assert _tensor_eq(getattr(homo, "y_raw", None), getattr(back, "y_raw", None))
    assert list(getattr(homo, "node_name", [])) == list(getattr(back, "node_name", []))
    assert homo.edge_index.shape[1] == back.edge_index.shape[1]
    assert _edge_multiset(homo) == _edge_multiset(back)
    assert homo.rc_edge_index.shape[1] == back.rc_edge_index.shape[1]


@pytestmark_tensor
def test_hetero_node_and_edge_stores(mini_csvs):
    """The b-view HeteroData splits nodes by type (node_type col dropped -> x is
    graph_id+8 feats, y is 5 label slots) and edges into (src, relation, dst)
    stores; folded view e disambiguates gate vs net cliques on pin<->pin."""
    from torch_geometric.data import HeteroData
    b = gl.homo_to_hetero(_build("b", mini_csvs))
    assert isinstance(b, HeteroData)
    assert set(b.node_types) == {"gate", "net", "iopin", "pin"}
    assert b["gate"].x.shape[1] == 9 and b["gate"].y.shape[1] == 5
    # b physical edges use the "connects" relation, symmetric both ways
    rels = {tuple(et) for et in b.edge_types}
    assert ("gate", "connects", "pin") in rels and ("pin", "connects", "gate") in rels
    assert b.graph_kind == "hetero"
    # view e folds BOTH gates and nets onto pin<->pin edges -> two distinct relations
    e = gl.homo_to_hetero(_build("e", mini_csvs))
    e_rels = {tuple(et) for et in e.edge_types}
    assert ("pin", "gate", "pin") in e_rels and ("pin", "net", "pin") in e_rels


@pytestmark_tensor
def test_main_kind_homo_and_both(mini_csvs, tmp_path, monkeypatch):
    """--kind homo writes a homogeneous {v}_graph.pt; --kind both writes hetero
    {v}_graph.pt + homo {v}_graph_homo.pt."""
    import sys as _sys
    from torch_geometric.data import Data, HeteroData
    import build_graphs as bg

    feat, lab = mini_csvs
    homo_out = tmp_path / "homo_ds"
    monkeypatch.setattr(_sys, "argv", [
        "build_graphs.py", "--features", feat, "--labels", lab, "--design", "mini",
        "--out-dir", str(homo_out), "--variants", "b", "--kind", "homo"])
    bg.main()
    assert isinstance(torch.load(homo_out / "b_graph.pt", weights_only=False), Data)

    both_out = tmp_path / "both_ds"
    monkeypatch.setattr(_sys, "argv", [
        "build_graphs.py", "--features", feat, "--labels", lab, "--design", "mini",
        "--out-dir", str(both_out), "--variants", "b", "--kind", "both"])
    bg.main()
    assert isinstance(torch.load(both_out / "b_graph.pt", weights_only=False), HeteroData)
    assert isinstance(torch.load(both_out / "b_graph_homo.pt", weights_only=False), Data)


# --------------------------------------------------------------------------- #
# Atomic/invalidating regeneration (full-pipeline #6, 2026-07-16).             #
# --------------------------------------------------------------------------- #

def _build_variants(feat, lab, out, variants, monkeypatch, kind=None):
    import sys as _sys
    import build_graphs as bg
    argv = ["build_graphs.py", "--features", feat, "--labels", lab, "--design", "mini",
            "--out-dir", str(out), "--variants", variants]
    if kind:
        argv += ["--kind", kind]
    monkeypatch.setattr(_sys, "argv", argv)
    bg.main()


@pytestmark_tensor
def test_rebuild_shrink_removes_stale_variant_pt(mini_csvs, tmp_path, monkeypatch):
    """A rebuild with FEWER variants deletes the dropped variants' {v}_graph.pt so the
    manifest commit-point describes exactly what is on disk (no orphaned c..f graphs)."""
    import json
    feat, lab = mini_csvs
    out = tmp_path / "shrink_ds"
    _build_variants(feat, lab, out, "bcf", monkeypatch)
    assert (out / "b_graph.pt").exists() and (out / "c_graph.pt").exists() \
        and (out / "f_graph.pt").exists()
    _build_variants(feat, lab, out, "b", monkeypatch)   # rebuild only b
    assert (out / "b_graph.pt").exists()
    assert not (out / "c_graph.pt").exists()
    assert not (out / "f_graph.pt").exists()
    man = json.load(open(out / "graph_manifest.json"))
    assert set(man["variants"]) == {"b"}


@pytestmark_tensor
def test_rebuild_kind_shrink_removes_homo_pt(mini_csvs, tmp_path, monkeypatch):
    """A hetero-only rebuild after --kind both removes the stale {v}_graph_homo.pt."""
    feat, lab = mini_csvs
    out = tmp_path / "kind_ds"
    _build_variants(feat, lab, out, "b", monkeypatch, kind="both")
    assert (out / "b_graph.pt").exists() and (out / "b_graph_homo.pt").exists()
    _build_variants(feat, lab, out, "b", monkeypatch, kind="hetero")
    assert (out / "b_graph.pt").exists()
    assert not (out / "b_graph_homo.pt").exists()


@pytestmark_tensor
def test_netlist_graph_pt_not_touched_by_cleanup(mini_csvs, tmp_path, monkeypatch):
    """Only the [b-f]_graph*.pt family is cleaned — netlist_graph.pt (a different
    stage's artifact) must survive a variant rebuild."""
    feat, lab = mini_csvs
    out = tmp_path / "keep_netlist"
    _build_variants(feat, lab, out, "bc", monkeypatch)
    (out / "netlist_graph.pt").write_bytes(b"stub")   # owned by the netlist stage
    _build_variants(feat, lab, out, "b", monkeypatch)
    assert not (out / "c_graph.pt").exists()
    assert (out / "netlist_graph.pt").exists()


def test_run_graphs_benign_skip_leaves_manifest(tmp_path):
    """A BENIGN skip (missing torch venv) keeps the documented exit-0 "SKIPs cleanly"
    contract and leaves any existing dataset/graph_manifest.json untouched."""
    import json
    import subprocess
    proj = tmp_path / "proj"
    ds = proj / "dataset"
    ds.mkdir(parents=True)
    green = {"design": "mini", "status": "ok", "graph_kind": "hetero",
             "variants": {"b": {}}, "platform": "nangate45"}
    json.dump(green, open(ds / "graph_manifest.json", "w"))
    env = dict(os.environ, R2G_GRAPH_PYTHON="/nonexistent/python_no_torch")
    r = subprocess.run(["bash", _RUN_GRAPHS, str(proj), "nangate45"],
                       capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0, (r.returncode, r.stderr)
    man = json.load(open(ds / "graph_manifest.json"))
    assert man["status"] == "ok"                        # untouched


@pytestmark_tensor
def test_run_graphs_gate_block_invalidates_manifest(tmp_path):
    """An INVALIDATING skip (signoff-gate BLOCK — dirty DRC) supersedes a stale-green
    dataset/graph_manifest.json with status=blocked_unsigned and exits non-zero (7)."""
    import json
    import subprocess
    import sys
    proj = tmp_path / "proj"
    run = proj / "backend" / "RUN_2026-07-01_00-00-00" / "results"
    run.mkdir(parents=True)
    (run / "6_final.def").write_text("VERSION 5.8 ;\n")
    rep = proj / "reports"
    rep.mkdir(parents=True)
    json.dump({"status": "fail", "total_violations": 5}, open(rep / "drc.json", "w"))
    json.dump({"status": "clean", "mismatch_count": 0}, open(rep / "lvs.json", "w"))
    ds = proj / "dataset"
    ds.mkdir(parents=True)
    json.dump({"design": "mini", "status": "ok", "graph_kind": "hetero",
               "variants": {"b": {}}, "platform": "nangate45"},
              open(ds / "graph_manifest.json", "w"))
    env = dict(os.environ, R2G_GRAPH_PYTHON=sys.executable)   # torch present -> reaches gate
    r = subprocess.run(["bash", _RUN_GRAPHS, str(proj), "nangate45"],
                       capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 7, (r.returncode, r.stderr)
    man = json.load(open(ds / "graph_manifest.json"))
    assert man["status"] == "blocked_unsigned"
    assert man["superseded"]["status"] == "ok"
    assert man.get("reason")
