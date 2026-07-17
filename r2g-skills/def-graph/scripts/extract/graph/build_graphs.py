#!/usr/bin/env python3
"""Build PyG graph datasets (variants b..f) from the skill's feature/label CSVs.

Consolidated port of the external RTL2Graph ``last_graph`` single-case pipeline
(five near-identical 700-line scripts -> graph_lib.py + the five small variant
builders below), verified against the originals on cordic nangate45. The five
topologies trade granularity for size (N = nodes on cordic nangate45):

  b: gate/net/iopin/pin nodes; gate-pin, pin-net, iopin-net edges   (N=7891)
  c: gate/net/iopin nodes; pins folded into gate-net edges          (N=3233)
  d: gate/iopin/pin nodes; nets folded into pin-clique edges        (N=6243)
  e: iopin/pin nodes; gates AND nets folded into pin-clique edges   (N=4761)
  f: gate/iopin nodes; nets folded into gate-clique edges           (N=1585)

Node features x[10]: x0=node_type (0 gate/1 net/2 iopin/3 pin), x1=graph_id,
x2..x9 = per-type schema (graph_lib.GATE_SCHEMA etc., zero-padded). Node labels
y[6]: y0=node_type, y1=congestion, y2=irdrop, y3=timing, y4=wirelength, y5=RC
ground cap (net node in b/c, broadcast to pin nodes in d/e, dropped in f) — NaN
where a label doesn't apply / didn't join. Variants with folded entities carry
that entity's features/labels on edge_attr/edge_y (edge columns interleaved
fwd/rev — see graph_lib.build_directed_edges).

Every label tensor has a parallel RAW twin (``y_raw``, ``edge_y_raw``,
``rc_edge_y_raw``) that carries the raw physical value (EDA-Schema / CircuitNet
convention: demand/cap ratio, um, ns slack, mV, fF, Ohm) instead of the
normalized log/sqrt target — same shape + layout, so a downstream trainer picks
either convention without a regen (2026-07-14).

RC parasitic edge labels ride a SEPARATE parasitic edge set (rc_edge_index /
rc_edge_type / rc_edge_y[E,3]=[type, coupling_cap_label, equiv_res_label]),
distinct from the physical-topology edge_index: coupling cap on net<->net edges
(driver-pin<->driver-pin where nets are folded), equivalent resistance on
same-net pin<->pin edges. See graph_lib.attach_rc_labels + label-extraction.md.

Output is HETEROGENEOUS by default (2026-07-16): each <variant>_graph.pt is a
torch_geometric HeteroData with per-type node stores (gate/net/iopin/pin) and
(src_type, relation, dst_type) edge stores — the relation is the folded entity
(b physical edges -> "connects"; c/d/e/f -> the edge_schema entity; RC parasitics
-> "rc_coupling"/"rc_resistance"). The verified homogeneous Data above is still
built first as the source of truth; graph_lib.homo_to_hetero re-views it WITHOUT
changing a value, and graph_lib.hetero_to_homo is the exact inverse. --kind homo
restores the legacy homogeneous format; --kind both writes both. See
references/graph-dataset.md ("Heterogeneous graphs").

Usage:
  build_graphs.py --features <dir> --labels <dir> --design <name> \
      --out-dir <dir> [--variants bcdef] [--kind hetero|homo|both] [--graph-id N]

Writes <out-dir>/<variant>_graph.pt + <out-dir>/graph_manifest.json.
Requires torch + torch_geometric (run_graphs.sh probes and skips cleanly).
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd  # noqa: E402

import graph_lib as gl  # noqa: E402
from graph_lib import (  # noqa: E402
    GATE_SCHEMA, IOPIN_SCHEMA, METADATA_SCHEMA, NET_SCHEMA, PIN_SCHEMA,
    LABEL_SPECS, NODE_TYPE_GATE, NODE_TYPE_IO_PIN, NODE_TYPE_NET, NODE_TYPE_PIN,
    Y_SCHEMA_BASE, Y_RAW_SCHEMA_BASE, Y_WIDTH, RC_EDGE_TYPE_COUPLING, RC_EDGE_TYPE_RESISTANCE,
    attach_rc_labels, build_directed_edges, build_feature_views,
    build_gate_label_values, build_net_label_values, build_pin_label_values,
    clique_pairs, load_global_feat, load_label_cache, load_rc_label_cache,
    node_names_for, pad_or_truncate_1d, pad_schema_cols, to_float32_matrix,
)

GATE_COLS = pad_schema_cols(GATE_SCHEMA)
NET_COLS = pad_schema_cols(NET_SCHEMA)
IOPIN_COLS = pad_schema_cols(IOPIN_SCHEMA)
PIN_COLS = pad_schema_cols(PIN_SCHEMA)


def _torch():
    import torch

    return torch


def _data():
    from torch_geometric.data import Data

    return Data


def _x10(blocks, graph_id_int):
    """Stack (node_type, df, cols) blocks into the x[N,10] tensor."""
    torch = _torch()
    num_nodes = sum(len(df) for _, df, _ in blocks)
    x = torch.zeros((num_nodes, 10), dtype=torch.float32)
    off = 0
    for t, df, cols in blocks:
        n = len(df)
        x[off:off + n, 0] = float(t)
        x[off:off + n, 2:10] = to_float32_matrix(df, cols, n)
        off += n
    x[:, 1] = float(graph_id_int)
    return x


def _y5_base(x10):
    torch = _torch()
    y = torch.full((x10.shape[0], Y_WIDTH), float("nan"), dtype=torch.float32)
    y[:, 0] = x10[:, 0]
    return y


def _fill_node_labels(y5, y5_raw, builder, base_df, label_dfs, design_key, start, n):
    """Fill both the normalized (``y5``) and raw (``y5_raw``) label slots for a
    contiguous node block ``[start:start+n]`` from one per-type label builder.
    The raw pass reads each spec's ``raw_column`` (value_col_key='raw_column')."""
    for order, vals in builder(base_df, label_dfs, design_key).items():
        y5[start:start + n, 1 + order] = pad_or_truncate_1d(vals, n)
    for order, vals in builder(base_df, label_dfs, design_key, value_col_key="raw_column").items():
        y5_raw[start:start + n, 1 + order] = pad_or_truncate_1d(vals, n)


def _finish(data, views, graph_key, design_key, feature_root, x_schema, edge_schema=None):
    gate_df, net_df, iopin_df, pin_df = views
    data.feature_graph_key = graph_key
    gf = load_global_feat(feature_root, graph_key)
    if gf is not None:
        data.global_feat = gf
    data.node_name = node_names_for(data.x[:, 0].long(), gate_df, net_df, iopin_df, pin_df)
    data.x_schema = dict(x_schema, global_feat_0_14=METADATA_SCHEMA)
    data.y_schema = dict(Y_SCHEMA_BASE, label_specs=LABEL_SPECS, design_key=design_key)
    data.y_raw_schema = dict(Y_RAW_SCHEMA_BASE, design_key=design_key)
    if edge_schema is not None:
        data.edge_schema = dict(edge_schema, design_key=design_key)
    return data


def _net_clique_rows(net_df, edges_pn, edges_in, endpoint_index_pn, endpoint_index_in):
    """Per signal net, clique edges over its endpoints (pins or gates + iopins).

    ``endpoint_index_pn(inst, pin)`` / ``endpoint_index_in(iopin)`` map an
    edges_pn / edges_in row to a node index (None = not a node in this variant).
    """
    rows = []
    pn_groups = edges_pn.groupby("net_name")
    in_groups = edges_in.groupby("net_name")
    for net_name in net_df["net_name"].tolist():
        endpoints = []
        if net_name in pn_groups.groups:
            part = pn_groups.get_group(net_name)
            for inst_name, pin_name in part[["inst_name", "pin_name"]].drop_duplicates().itertuples(index=False, name=None):
                idx = endpoint_index_pn(inst_name, pin_name)
                if idx is not None:
                    endpoints.append(idx)
        if net_name in in_groups.groups:
            part = in_groups.get_group(net_name)
            for iopin_name in part[["iopin_name"]].drop_duplicates()["iopin_name"].tolist():
                idx = endpoint_index_in(iopin_name)
                if idx is not None:
                    endpoints.append(idx)
        for s, t in clique_pairs(endpoints):
            rows.append({"src": s, "dst": t, "net_name": net_name})
    return pd.DataFrame(rows)


def _edge_block(edge_df, feat_df, key_cols, cols, edge_type_id, label_builder, label_dfs, design_key):
    """attrs + type + y + y_raw tensors for one edge family (features/labels of
    the folded entity looked up by name). ``y`` carries the normalized label,
    ``y_raw`` the raw physical value (same layout)."""
    torch = _torch()
    n = len(edge_df)
    if n == 0:
        return (torch.zeros((0, 8), dtype=torch.float32),
                torch.zeros((0,), dtype=torch.long),
                torch.zeros((0, Y_WIDTH), dtype=torch.float32),
                torch.zeros((0, Y_WIDTH), dtype=torch.float32))
    if feat_df is not None:
        merged = edge_df.merge(feat_df[key_cols + cols], on=key_cols, how="left")
        if len(merged) != n:
            raise ValueError(
                f"edge feature merge on {key_cols} exploded {n} -> {len(merged)} rows "
                f"(duplicate keys in the feature table) — would silently misalign edge_attr")
        edge_df = merged
        attr = to_float32_matrix(edge_df, cols, n)
    else:
        attr = torch.zeros((n, 8), dtype=torch.float32)
    etype = torch.full((n,), edge_type_id, dtype=torch.long)
    y = torch.full((n, Y_WIDTH), float("nan"), dtype=torch.float32)
    y[:, 0] = float(edge_type_id)
    y_raw = torch.full((n, Y_WIDTH), float("nan"), dtype=torch.float32)
    y_raw[:, 0] = float(edge_type_id)
    if label_builder is not None:
        for order, vals in label_builder(edge_df[key_cols], label_dfs, design_key).items():
            y[:, 1 + order] = pad_or_truncate_1d(vals, n)
        for order, vals in label_builder(edge_df[key_cols], label_dfs, design_key, value_col_key="raw_column").items():
            y_raw[:, 1 + order] = pad_or_truncate_1d(vals, n)
    return attr, etype, y, y_raw


# --------------------------------------------------------------------------- #
# Variant builders. Each returns a torch_geometric Data.                      #
# --------------------------------------------------------------------------- #

def build_b(views7, label_dfs, graph_key, design_key, graph_id_int, feature_root, rc=None):
    """gate/net/iopin/pin nodes; gate-pin, pin-net, iopin-net edges (no attrs)."""
    torch, Data = _torch(), _data()
    gate_df, net_df, iopin_df, pin_df, edges_gp, edges_pn, edges_in = views7

    gate_idx = {n: i for i, n in enumerate(gate_df["inst_name"])}
    off = len(gate_idx)
    net_idx = {n: off + i for i, n in enumerate(net_df["net_name"])}
    off += len(net_idx)
    iopin_idx = {n: off + i for i, n in enumerate(iopin_df["iopin_name"])}
    off += len(iopin_idx)
    pin_idx = {(a, b): off + i for i, (a, b) in enumerate(
        pin_df[["inst_name", "pin_name"]].itertuples(index=False, name=None))}

    edges, seen = [], set()

    def add(u, v):
        if u is None or v is None:
            return
        for pair in ((int(u), int(v)), (int(v), int(u))):
            if pair not in seen:
                seen.add(pair)
                edges.append(pair)

    for a, b in edges_gp[["inst_name", "pin_name"]].itertuples(index=False, name=None):
        add(gate_idx.get(a), pin_idx.get((a, b)))
    for a, b, c in edges_pn[["inst_name", "pin_name", "net_name"]].itertuples(index=False, name=None):
        add(pin_idx.get((a, b)), net_idx.get(c))
    for a, b in edges_in[["iopin_name", "net_name"]].itertuples(index=False, name=None):
        add(iopin_idx.get(a), net_idx.get(b))

    edge_index = (torch.tensor(edges, dtype=torch.long).t().contiguous()
                  if edges else torch.empty((2, 0), dtype=torch.long))

    x10 = _x10([(NODE_TYPE_GATE, gate_df, GATE_COLS), (NODE_TYPE_NET, net_df, NET_COLS),
                (NODE_TYPE_IO_PIN, iopin_df, IOPIN_COLS), (NODE_TYPE_PIN, pin_df, PIN_COLS)],
               graph_id_int)
    y5 = _y5_base(x10)
    y5_raw = _y5_base(x10)
    node_type = x10[:, 0].long()
    ng, nn, ni, np_ = len(gate_df), len(net_df), len(iopin_df), len(pin_df)
    _fill_node_labels(y5, y5_raw, build_gate_label_values, gate_df[["inst_name"]], label_dfs, design_key, 0, ng)
    _fill_node_labels(y5, y5_raw, build_net_label_values, net_df[["net_name"]], label_dfs, design_key, ng, nn)
    _fill_node_labels(y5, y5_raw, build_pin_label_values, pin_df[["inst_name", "pin_name"]], label_dfs, design_key, ng + nn + ni, np_)
    assert int(node_type.numel()) == ng + nn + ni + np_

    data = Data(x=x10, edge_index=edge_index)
    data.y = y5
    data.y_raw = y5_raw
    # RC: ground cap on net nodes; coupling net<->net; resistance pin<->pin.
    attach_rc_labels(data, rc or {}, design_key, net_idx=net_idx, pin_idx=pin_idx, iopin_idx=iopin_idx)
    return _finish(data, (gate_df, net_df, iopin_df, pin_df), graph_key, design_key, feature_root,
                   {"x0": "node_type", "x1": "graph_id", "gate_x2_9": GATE_COLS,
                    "net_x2_9": NET_COLS, "iopin_x2_9": IOPIN_COLS, "pin_x2_9": PIN_COLS})


def build_c(views7, label_dfs, graph_key, design_key, graph_id_int, feature_root, rc=None):
    """gate/net/iopin nodes; pins -> gate-net edges carrying pin features."""
    torch, Data = _torch(), _data()
    gate_df, net_df, iopin_df, pin_df, edges_gp, edges_pn, edges_in = views7

    gate_idx = {n: i for i, n in enumerate(gate_df["inst_name"])}
    net_idx = {n: len(gate_idx) + i for i, n in enumerate(net_df["net_name"])}
    iopin_idx = {n: len(gate_idx) + len(net_idx) + i for i, n in enumerate(iopin_df["iopin_name"])}

    x10 = _x10([(NODE_TYPE_GATE, gate_df, GATE_COLS), (NODE_TYPE_NET, net_df, NET_COLS),
                (NODE_TYPE_IO_PIN, iopin_df, IOPIN_COLS)], graph_id_int)
    y5 = _y5_base(x10)
    y5_raw = _y5_base(x10)
    ng, nn = len(gate_df), len(net_df)
    _fill_node_labels(y5, y5_raw, build_gate_label_values, gate_df[["inst_name"]], label_dfs, design_key, 0, ng)
    _fill_node_labels(y5, y5_raw, build_net_label_values, net_df[["net_name"]], label_dfs, design_key, ng, nn)

    pin_edges = edges_pn[["inst_name", "pin_name", "net_name"]].drop_duplicates().copy()
    keep, src, dst = [], [], []
    for i, (inst, _pin, net) in enumerate(pin_edges.itertuples(index=False, name=None)):
        s, t = gate_idx.get(inst), net_idx.get(net)
        if s is None or t is None:
            continue
        keep.append(i)
        src.append(s)
        dst.append(t)
    pin_edges = pin_edges.iloc[keep].reset_index(drop=True) if keep else pin_edges.head(0)

    io_edges = edges_in[["iopin_name", "net_name"]].drop_duplicates().copy()
    ikeep, isrc, idst = [], [], []
    for i, (iop, net) in enumerate(io_edges.itertuples(index=False, name=None)):
        s, t = iopin_idx.get(iop), net_idx.get(net)
        if s is None or t is None:
            continue
        ikeep.append(i)
        isrc.append(s)
        idst.append(t)
    io_edges = io_edges.iloc[ikeep].reset_index(drop=True) if ikeep else io_edges.head(0)

    pin_attr, pin_type, pin_y, pin_y_raw = _edge_block(
        pin_edges, pin_df, ["inst_name", "pin_name"], PIN_COLS, 0,
        build_pin_label_values, label_dfs, design_key)
    io_attr, io_type, io_y, io_y_raw = _edge_block(io_edges, None, ["iopin_name"], None, 1,
                                                   None, label_dfs, design_key)

    edge_index, edge_attr, edge_type, edge_y, edge_y_raw = build_directed_edges(
        src + isrc, dst + idst,
        torch.cat([pin_attr, io_attr]), torch.cat([pin_y, io_y]), torch.cat([pin_type, io_type]),
        base_y_raw=torch.cat([pin_y_raw, io_y_raw]))

    data = Data(x=x10, edge_index=edge_index)
    data.y = y5
    data.y_raw = y5_raw
    data.edge_attr, data.edge_type, data.edge_y = edge_attr, edge_type, edge_y
    data.edge_y_raw = edge_y_raw
    # RC: ground cap on net nodes; coupling net<->net; resistance dropped (no pin nodes).
    attach_rc_labels(data, rc or {}, design_key, net_idx=net_idx, iopin_idx=iopin_idx)
    return _finish(data, (gate_df, net_df, iopin_df, pin_df.head(0)), graph_key, design_key, feature_root,
                   {"x0": "node_type", "x1": "graph_id", "gate_x2_9": GATE_COLS,
                    "net_x2_9": NET_COLS, "iopin_x2_9": IOPIN_COLS},
                   {"edge_type": {0: "pin", 1: "iopin_connection"},
                    "pin_edge_attr_0_7": PIN_COLS, "iopin_edge_attr_0_7": "zeros_no_pin_feature",
                    "edge_y0": "edge_type", "edge_y1_4": "label_order_0_3"})


def build_d(views7, label_dfs, graph_key, design_key, graph_id_int, feature_root, rc=None):
    """gate/iopin/pin nodes; gate-pin edges + per-net pin cliques carrying net features."""
    torch, Data = _torch(), _data()
    gate_df, net_df, iopin_df, pin_df, edges_gp, edges_pn, edges_in = views7

    gate_idx = {n: i for i, n in enumerate(gate_df["inst_name"])}
    iopin_idx = {n: len(gate_idx) + i for i, n in enumerate(iopin_df["iopin_name"])}
    off = len(gate_idx) + len(iopin_idx)
    pin_idx = {(a, b): off + i for i, (a, b) in enumerate(
        pin_df[["inst_name", "pin_name"]].itertuples(index=False, name=None))}

    x10 = _x10([(NODE_TYPE_GATE, gate_df, GATE_COLS), (NODE_TYPE_IO_PIN, iopin_df, IOPIN_COLS),
                (NODE_TYPE_PIN, pin_df, PIN_COLS)], graph_id_int)
    y5 = _y5_base(x10)
    y5_raw = _y5_base(x10)
    ng, np_ = len(gate_df), len(pin_df)
    _fill_node_labels(y5, y5_raw, build_gate_label_values, gate_df[["inst_name"]], label_dfs, design_key, 0, ng)
    _fill_node_labels(y5, y5_raw, build_pin_label_values, pin_df[["inst_name", "pin_name"]], label_dfs, design_key, off, np_)

    gp = edges_gp[["inst_name", "pin_name"]].drop_duplicates()
    gp_src, gp_dst = [], []
    for inst, pin in gp.itertuples(index=False, name=None):
        s, t = gate_idx.get(inst), pin_idx.get((inst, pin))
        if s is None or t is None:
            continue
        gp_src.append(s)
        gp_dst.append(t)

    net_edge_df = _net_clique_rows(net_df, edges_pn, edges_in,
                                   lambda i, p: pin_idx.get((i, p)),
                                   lambda io: iopin_idx.get(io))

    gp_attr = torch.zeros((len(gp_src), 8), dtype=torch.float32)
    gp_type = torch.zeros((len(gp_src),), dtype=torch.long)
    gp_y = torch.full((len(gp_src), Y_WIDTH), float("nan"), dtype=torch.float32)
    gp_y_raw = torch.full((len(gp_src), Y_WIDTH), float("nan"), dtype=torch.float32)
    if gp_src:
        gp_y[:, 0] = 0.0
        gp_y_raw[:, 0] = 0.0
    net_attr, net_type, net_y, net_y_raw = _edge_block(
        net_edge_df, net_df, ["net_name"], NET_COLS, 1,
        build_net_label_values, label_dfs, design_key)

    nsrc = net_edge_df["src"].astype(int).tolist() if not net_edge_df.empty else []
    ndst = net_edge_df["dst"].astype(int).tolist() if not net_edge_df.empty else []
    edge_index, edge_attr, edge_type, edge_y, edge_y_raw = build_directed_edges(
        gp_src + nsrc, gp_dst + ndst,
        torch.cat([gp_attr, net_attr]), torch.cat([gp_y, net_y]), torch.cat([gp_type, net_type]),
        base_y_raw=torch.cat([gp_y_raw, net_y_raw]))

    data = Data(x=x10, edge_index=edge_index)
    data.y = y5
    data.y_raw = y5_raw
    data.edge_attr, data.edge_type, data.edge_y = edge_attr, edge_type, edge_y
    data.edge_y_raw = edge_y_raw
    # RC: no net nodes -> ground cap broadcast to pin nodes; coupling on driver pins;
    # resistance pin<->pin.
    pin_net_map = {(i, p): n for i, p, n in
                   edges_pn[["inst_name", "pin_name", "net_name"]].drop_duplicates().itertuples(index=False, name=None)}
    attach_rc_labels(data, rc or {}, design_key, pin_idx=pin_idx, iopin_idx=iopin_idx, pin_net_map=pin_net_map)
    return _finish(data, (gate_df, net_df.head(0), iopin_df, pin_df), graph_key, design_key, feature_root,
                   {"x0": "node_type", "x1": "graph_id", "gate_x2_9": GATE_COLS,
                    "iopin_x2_9": IOPIN_COLS, "pin_x2_9": PIN_COLS},
                   {"edge_type": {0: "gate_pin", 1: "net"},
                    "gate_pin_edge_attr_0_7": "zeros_no_removed_entity_feature",
                    "net_edge_attr_0_7": NET_COLS,
                    "edge_y0": "edge_type", "edge_y1_4": "label_order_0_3"})


def build_e(views7, label_dfs, graph_key, design_key, graph_id_int, feature_root, rc=None):
    """iopin/pin nodes; gates -> pin cliques (gate features), nets -> pin cliques."""
    torch, Data = _torch(), _data()
    gate_df, net_df, iopin_df, pin_df, edges_gp, edges_pn, edges_in = views7

    iopin_idx = {n: i for i, n in enumerate(iopin_df["iopin_name"])}
    off = len(iopin_idx)
    pin_idx = {(a, b): off + i for i, (a, b) in enumerate(
        pin_df[["inst_name", "pin_name"]].itertuples(index=False, name=None))}

    x10 = _x10([(NODE_TYPE_IO_PIN, iopin_df, IOPIN_COLS), (NODE_TYPE_PIN, pin_df, PIN_COLS)],
               graph_id_int)
    y5 = _y5_base(x10)
    y5_raw = _y5_base(x10)
    np_ = len(pin_df)
    _fill_node_labels(y5, y5_raw, build_pin_label_values, pin_df[["inst_name", "pin_name"]], label_dfs, design_key, off, np_)

    gate_rows = []
    gp_groups = edges_gp.groupby("inst_name")
    for inst_name in gate_df["inst_name"].tolist():
        if inst_name not in gp_groups.groups:
            continue
        part = gp_groups.get_group(inst_name)
        endpoints = [pin_idx.get((inst_name, p))
                     for p in part[["pin_name"]].drop_duplicates()["pin_name"].tolist()]
        for s, t in clique_pairs([e for e in endpoints if e is not None]):
            gate_rows.append({"src": s, "dst": t, "inst_name": inst_name})
    gate_edge_df = pd.DataFrame(gate_rows)

    net_edge_df = _net_clique_rows(net_df, edges_pn, edges_in,
                                   lambda i, p: pin_idx.get((i, p)),
                                   lambda io: iopin_idx.get(io))

    gate_attr, gate_type, gate_y, gate_y_raw = _edge_block(
        gate_edge_df, gate_df, ["inst_name"], GATE_COLS, 0,
        build_gate_label_values, label_dfs, design_key)
    net_attr, net_type, net_y, net_y_raw = _edge_block(
        net_edge_df, net_df, ["net_name"], NET_COLS, 1,
        build_net_label_values, label_dfs, design_key)

    gsrc = gate_edge_df["src"].astype(int).tolist() if not gate_edge_df.empty else []
    gdst = gate_edge_df["dst"].astype(int).tolist() if not gate_edge_df.empty else []
    nsrc = net_edge_df["src"].astype(int).tolist() if not net_edge_df.empty else []
    ndst = net_edge_df["dst"].astype(int).tolist() if not net_edge_df.empty else []
    edge_index, edge_attr, edge_type, edge_y, edge_y_raw = build_directed_edges(
        gsrc + nsrc, gdst + ndst,
        torch.cat([gate_attr, net_attr]), torch.cat([gate_y, net_y]), torch.cat([gate_type, net_type]),
        base_y_raw=torch.cat([gate_y_raw, net_y_raw]))

    data = Data(x=x10, edge_index=edge_index)
    data.y = y5
    data.y_raw = y5_raw
    data.edge_attr, data.edge_type, data.edge_y = edge_attr, edge_type, edge_y
    data.edge_y_raw = edge_y_raw
    # RC: no net nodes -> ground cap broadcast to pin nodes; coupling on driver pins;
    # resistance pin<->pin.
    pin_net_map = {(i, p): n for i, p, n in
                   edges_pn[["inst_name", "pin_name", "net_name"]].drop_duplicates().itertuples(index=False, name=None)}
    attach_rc_labels(data, rc or {}, design_key, pin_idx=pin_idx, iopin_idx=iopin_idx, pin_net_map=pin_net_map)
    return _finish(data, (gate_df.head(0), net_df.head(0), iopin_df, pin_df), graph_key, design_key, feature_root,
                   {"x0": "node_type", "x1": "graph_id", "iopin_x2_9": IOPIN_COLS,
                    "pin_x2_9": PIN_COLS},
                   {"edge_type": {0: "gate", 1: "net"},
                    "gate_edge_attr_0_7": GATE_COLS, "net_edge_attr_0_7": NET_COLS,
                    "edge_y0": "edge_type", "edge_y1_4": "label_order_0_3"})


def build_f(views7, label_dfs, graph_key, design_key, graph_id_int, feature_root, rc=None):
    """gate/iopin nodes; nets -> gate/iopin cliques carrying net features."""
    torch, Data = _torch(), _data()
    gate_df, net_df, iopin_df, pin_df, edges_gp, edges_pn, edges_in = views7

    gate_idx = {n: i for i, n in enumerate(gate_df["inst_name"])}
    iopin_idx = {n: len(gate_idx) + i for i, n in enumerate(iopin_df["iopin_name"])}

    x10 = _x10([(NODE_TYPE_GATE, gate_df, GATE_COLS), (NODE_TYPE_IO_PIN, iopin_df, IOPIN_COLS)],
               graph_id_int)
    y5 = _y5_base(x10)
    y5_raw = _y5_base(x10)
    ng = len(gate_df)
    _fill_node_labels(y5, y5_raw, build_gate_label_values, gate_df[["inst_name"]], label_dfs, design_key, 0, ng)

    def pn_to_gate(inst_name, _pin_name):
        return gate_idx.get(inst_name)

    net_edge_df = _net_clique_rows(net_df, edges_pn, edges_in, pn_to_gate,
                                   lambda io: iopin_idx.get(io))
    net_attr, net_type, net_y, net_y_raw = _edge_block(
        net_edge_df, net_df, ["net_name"], NET_COLS, 0,
        build_net_label_values, label_dfs, design_key)

    nsrc = net_edge_df["src"].astype(int).tolist() if not net_edge_df.empty else []
    ndst = net_edge_df["dst"].astype(int).tolist() if not net_edge_df.empty else []
    edge_index, edge_attr, edge_type, edge_y, edge_y_raw = build_directed_edges(
        nsrc, ndst, net_attr, net_y, net_type, base_y_raw=net_y_raw)

    data = Data(x=x10, edge_index=edge_index)
    data.y = y5
    data.y_raw = y5_raw
    data.edge_attr, data.edge_type, data.edge_y = edge_attr, edge_type, edge_y
    data.edge_y_raw = edge_y_raw
    # RC: neither net nor pin nodes survive -> all RC labels dropped (rc_edge_* empty).
    attach_rc_labels(data, rc or {}, design_key, iopin_idx=iopin_idx)
    return _finish(data, (gate_df, net_df.head(0), iopin_df, pin_df.head(0)), graph_key, design_key, feature_root,
                   {"x0": "node_type", "x1": "graph_id", "gate_x2_9": GATE_COLS,
                    "iopin_x2_9": IOPIN_COLS},
                   {"edge_type": {0: "net"}, "net_edge_attr_0_7": NET_COLS,
                    "edge_y0": "edge_type", "edge_y1_4": "label_order_0_3"})


BUILDERS = {"b": build_b, "c": build_c, "d": build_d, "e": build_e, "f": build_f}


def _nan_frac(t):
    torch = _torch()
    return float(torch.isnan(t).float().mean()) if t.numel() else 0.0


def _hetero_stats(hetero):
    """Per-type node counts + per-relation edge counts of a HeteroData view (the
    default graph_kind). The homo totals in _variant_stats still hold — this is
    the by-store breakdown of the SAME graph, keyed for the manifest."""
    node_types = {nt: int(hetero[nt].x.shape[0]) for nt in hetero.node_types}
    edge_types = {"__".join(et): int(hetero[et].edge_index.shape[1])
                  for et in hetero.edge_types}
    return {"node_types": node_types, "edge_types": edge_types}


def _variant_stats(variant, data):
    torch = _torch()
    nt = data.x[:, 0].long()
    stats = {
        "nodes": int(data.x.shape[0]),
        "edges": int(data.edge_index.shape[1]),
        "nodes_by_type": {str(int(t)): int((nt == t).sum()) for t in nt.unique()},
        "y_nan_frac": {f"y{s}": round(_nan_frac(data.y[:, s]), 4) for s in range(1, Y_WIDTH)},
    }
    if hasattr(data, "y_raw"):
        stats["y_raw_nan_frac"] = {f"y{s}": round(_nan_frac(data.y_raw[:, s]), 4) for s in range(1, Y_WIDTH)}
    if hasattr(data, "edge_y"):
        stats["edge_y_nan_frac"] = {f"y{s}": round(_nan_frac(data.edge_y[:, s]), 4) for s in range(1, Y_WIDTH)}
    if hasattr(data, "edge_y_raw"):
        stats["edge_y_raw_nan_frac"] = {f"y{s}": round(_nan_frac(data.edge_y_raw[:, s]), 4) for s in range(1, Y_WIDTH)}
    if hasattr(data, "rc_edge_index"):
        rt = data.rc_edge_type
        stats["rc_edges"] = int(data.rc_edge_index.shape[1])
        stats["rc_coupling_edges"] = int((rt == RC_EDGE_TYPE_COUPLING).sum()) if rt.numel() else 0
        stats["rc_resistance_edges"] = int((rt == RC_EDGE_TYPE_RESISTANCE).sum()) if rt.numel() else 0
    return stats


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--features", required=True, help="features/ CSV dir (run_features.sh output)")
    ap.add_argument("--labels", required=True, help="labels/ CSV dir (run_labels.sh output)")
    ap.add_argument("--design", required=True, help="DESIGN_NAME == graph_id == labels' Design key")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--variants", default="bcdef")
    ap.add_argument("--kind", default="hetero", choices=["hetero", "homo", "both"],
                    help="output graph_kind (default hetero): 'hetero' writes each "
                         "{v}_graph.pt as a torch_geometric HeteroData (per-type node "
                         "stores + (src,relation,dst) edge stores); 'homo' writes the "
                         "legacy homogeneous Data; 'both' writes hetero {v}_graph.pt "
                         "AND homo {v}_graph_homo.pt. The verified homogeneous Data is "
                         "always the internal source of truth (graph_lib.homo_to_hetero "
                         "is a value-preserving re-view). See graph-dataset.md.")
    ap.add_argument("--graph-id", type=int, default=0, help="x1 value (corpus-level id; default 0)")
    ap.add_argument("--platform", default="",
                    help="build-time platform provenance, stamped into the manifest "
                         "(cell_type_id and every *_type_id are per-platform — "
                         "failure-patterns.md #30)")
    ap.add_argument("--signoff-health", default="",
                    help="reports/signoff_gate.json written by the flow's signoff gate; "
                         "embedded verbatim as the manifest's signoff_health so a dataset "
                         "built on a dirty/unsigned design is self-describing "
                         "(failure-patterns.md #34)")
    args = ap.parse_args()

    torch = _torch()
    variants = []
    for ch in args.variants.lower():
        if ch not in BUILDERS:
            raise SystemExit(f"unknown variant '{ch}' (choose from {''.join(BUILDERS)})")
        if ch not in variants:
            variants.append(ch)

    views7 = build_feature_views(args.features, args.design)
    label_dfs = load_label_cache(args.labels)
    # RC parasitic labels (Y side) — fail-soft: absent/header-only CSVs (no SPEF)
    # leave the RC y-slot / parasitic edges empty.
    rc = load_rc_label_cache(args.labels)
    # Loud guard: any label file the builders can't join (missing Design/key/
    # label columns — e.g. an interrupted extractor left a raw tool dump — or
    # a design_key mismatch) means its y slot is silently all-NaN. Warn AND
    # record it in the manifest so downstream sees the degradation.
    health = gl.label_health(label_dfs, args.design)
    for fname, h in health.items():
        if h["status"] != "ok":
            print(f"WARNING: {fname} {h['status']}: {h['reason']} — "
                  f"its labels will be all-NaN", file=sys.stderr)

    # RC label coverage (a design with no SPEF -> all zero -> status no_rc_labels).
    rc_health = {
        "ground_cap_nets": len(gl.rc_ground_cap_by_net(rc, args.design)),
        "coupling_pairs": len(gl.rc_coupling_rows(rc, args.design)),
        "equiv_res_pairs": len(gl.rc_resistance_rows(rc, args.design)),
        "net_drivers": len(gl.rc_net_driver(rc, args.design)),
    }
    rc_health["status"] = "ok" if rc_health["ground_cap_nets"] > 0 else "no_rc_labels"
    if rc_health["status"] != "ok":
        print("NOTE: no RC labels for this design (no SPEF / RCX not run) — "
              "ground-cap y5 and parasitic edges will be empty", file=sys.stderr)

    # Signoff provenance (failure-patterns.md #34): the gate verdict rides in the
    # manifest so a dataset built on a dirty or never-signed-off design is
    # self-describing. "unrecorded" = the build bypassed the flow runner (direct
    # build_graphs.py invocation) — the verifier treats that fail-closed.
    signoff_health = {"status": "unrecorded"}
    if args.signoff_health and os.path.isfile(args.signoff_health):
        try:
            with open(args.signoff_health, encoding="utf-8") as f:
                signoff_health = json.load(f)
        except Exception as e:  # noqa: BLE001 — an unreadable verdict must not abort the build
            signoff_health = {"status": "unreadable", "error": str(e)}
    if signoff_health.get("status") not in ("pass", "pass_with_caveats"):
        print(f"WARNING: signoff_health={signoff_health.get('status')!r} — this dataset "
              "is not backed by a clean sign-off (see manifest signoff_health)",
              file=sys.stderr)

    os.makedirs(args.out_dir, exist_ok=True)
    # Absolute paths of every {v}_graph*.pt this build writes — the set stale-file
    # cleanup keeps (full-pipeline #6, 2026-07-16).
    written_pt = set()
    manifest = {
        "design": args.design,
        "graph_id": args.graph_id,
        # The dataset default is heterogeneous (2026-07-16): {v}_graph.pt is a
        # torch_geometric HeteroData re-view of the verified homogeneous Data.
        # 'homo' restores the legacy format; 'both' ships both. verify_graph_dataset.py
        # converts hetero->homo at load, so the full homo check surface certifies it.
        "graph_kind": args.kind,
        # Build-time provenance: which platform's libs keyed this dataset. The
        # verifier (and any corpus merge) must trust THIS over the project's
        # mutable config.mk, which later rounds re-point (failure-patterns #30).
        "platform": args.platform or None,
        "features_dir": os.path.abspath(args.features),
        "labels_dir": os.path.abspath(args.labels),
        "x_schema_per_type": {"gate": GATE_COLS, "net": NET_COLS, "iopin": IOPIN_COLS, "pin": PIN_COLS},
        "y_schema": Y_SCHEMA_BASE,
        "y_raw_schema": Y_RAW_SCHEMA_BASE,
        "label_health": health,
        "rc_health": rc_health,
        "signoff_health": signoff_health,
        "variants": {},
        "status": ("ok" if all(h["status"] == "ok" for h in health.values())
                   else "ok_with_label_gaps"),
    }
    for v in variants:
        # The homogeneous Data is ALWAYS built first — it is the verified source of
        # truth (every filter/sort/label-join happens here). The hetero output is a
        # pure value-preserving re-view (graph_lib.homo_to_hetero).
        data = BUILDERS[v](views7, label_dfs, args.design, args.design, args.graph_id, args.features, rc=rc)
        stats = dict(_variant_stats(v, data))
        paths = {}
        if args.kind in ("hetero", "both"):
            hetero = gl.homo_to_hetero(data)
            het_pt = os.path.join(args.out_dir, f"{v}_graph.pt")
            torch.save(hetero, het_pt)
            paths["hetero"] = os.path.abspath(het_pt)
            written_pt.add(os.path.abspath(het_pt))
            stats["hetero"] = _hetero_stats(hetero)
        if args.kind in ("homo", "both"):
            # In 'both' mode the canonical {v}_graph.pt is the hetero default; the
            # homo lands beside it as {v}_graph_homo.pt.
            homo_pt = os.path.join(args.out_dir,
                                   f"{v}_graph.pt" if args.kind == "homo" else f"{v}_graph_homo.pt")
            torch.save(data, homo_pt)
            paths["homo"] = os.path.abspath(homo_pt)
            written_pt.add(os.path.abspath(homo_pt))
        stats["path"] = paths.get("hetero", paths.get("homo"))
        stats["paths"] = paths
        manifest["variants"][v] = stats
        print(f"{v}_graph[{args.kind}]: nodes={data.x.shape[0]} edges={data.edge_index.shape[1]} "
              f"-> {stats['path']}")

    # Remove stale graph files so the manifest commit-point describes EXACTLY what is
    # on disk (full-pipeline #6, 2026-07-16): a rebuild with fewer variants/kinds must
    # not leave prior {v}_graph*.pt behind (rebuild -v b over bcdef; a hetero-only build
    # after --kind both left the {v}_graph_homo.pt siblings). ONLY the [b-f]_graph*.pt
    # family is ours — netlist_graph.pt is owned by the netlist stage. Order: write the
    # new .pt (above) -> delete stale (here) -> atomic manifest (below).
    for stale in sorted(glob.glob(os.path.join(args.out_dir, "[b-f]_graph*.pt"))):
        if os.path.abspath(stale) not in written_pt:
            os.remove(stale)
            print(f"removed stale graph file: {stale}")

    man_path = os.path.join(args.out_dir, "graph_manifest.json")
    tmp = man_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(manifest, f, indent=1)
    os.replace(tmp, man_path)
    print("manifest:", man_path)


if __name__ == "__main__":
    main()
