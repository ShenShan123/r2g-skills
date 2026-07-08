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


@pytestmark_tensor
def test_directed_edges_interleave_alignment():
    attr = torch.tensor([[1.0], [2.0], [3.0]])
    y = torch.zeros((3, 5))
    t = torch.tensor([0, 0, 1])
    ei, ea, et, ey = gl.build_directed_edges([0, 1, 2], [5, 6, 7], attr, y, t)
    assert ei.tolist() == [[0, 5, 1, 6, 2, 7], [5, 0, 6, 1, 7, 2]]
    # every fwd/rev pair shares its base attr + type
    assert ea.view(-1).tolist() == [1.0, 1.0, 2.0, 2.0, 3.0, 3.0]
    assert et.tolist() == [0, 0, 0, 0, 1, 1]


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
    man = json.load(open(out / "graph_manifest.json"))
    assert man["status"] == "ok" and set(man["variants"]) == {"b", "f"}
    assert man["variants"]["b"]["nodes"] == 7
    assert os.path.isfile(out / "b_graph.pt") and os.path.isfile(out / "f_graph.pt")


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
    data = torch.load(out / "b_graph.pt", weights_only=False)
    gate_mask = data.x[:, 0] == 0
    assert bool(torch.isnan(data.y[gate_mask, 2]).all())      # irdrop slot NaN
    assert not bool(torch.isnan(data.y[gate_mask, 1]).all())  # congestion intact
