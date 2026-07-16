"""Shared library for the PyG graph-dataset stage (RTL2Graph integration, 2026-07-05).

Consolidates the five near-duplicate ``last_graph/py/*_graph/augment_base_graph_with_
features.py`` scripts from the external RTL2Graph pipeline into one module. The
single-case data path was verified against ODB/OpenROAD ground truth on cordic
(nangate45) and aes_core (sky130hd) before porting — see
references/graph-dataset.md and the 2026-07-05 entries in failure-patterns.md.

Inputs are the SKILL's own stage outputs (which supersede RTL2Graph's stale
feature_test_v3/label_test copies — those still carried the sky130 quote-bug,
nangate-only num_layer, and fakeram-key bugs the skill fixed earlier):

  * ``<project>/features/*.csv``  (run_features.sh — the ML X side)
  * ``<project>/labels/*.csv``    (run_labels.sh   — the ML Y side)

Both stages key rows by DEF-escaped names and ``graph_id``/``Design`` =
DESIGN_NAME, so everything joins by name here. The schema below is the former
``design_mapping.csv`` (now code, not a loose CSV).

torch / torch_geometric are imported lazily so that importing this module (e.g.
for schema access or tests of pure-pandas helpers) does not require them.
"""
from __future__ import annotations

import os
from typing import Optional

import pandas as pd

NODE_TYPE_GATE = 0
NODE_TYPE_NET = 1
NODE_TYPE_IO_PIN = 2
NODE_TYPE_PIN = 3

# --- Feature schema: which CSV columns land in x[2:10], per node type -------
# (formerly the "0-N" rows of design_mapping.csv; max 8 slots, zero-padded)
GATE_SCHEMA = [
    "cell_type_id", "cell_area", "cell_power", "x_um", "y_um",
    "orientation_id", "placement_status_id",
]
NET_SCHEMA = [
    "net_type_id", "fanout", "pin_count", "num_drivers", "num_sinks",
    "connects_macro_flag", "num_layer", "hpwl_um",
]
IOPIN_SCHEMA = ["pin_x_um", "pin_y_um", "nearest_tap_distance_um", "pin_direction_id"]
PIN_SCHEMA = ["pin_type_id", "sum_pin_cap_fF"]
METADATA_SCHEMA = [
    "num_cells", "num_nets", "num_ios", "avg_fanout", "die_width", "die_height",
    "core_area", "dbu_unit", "PLACE_DENSITY", "CORE_UTILIZATION", "ABC_AREA",
    "C_total", "tracks_per_layer", "V_nom", "freq_Hz",
]

# --- Label specs: y slot 1+order per node type, from the labels stage's ------
# canonical CSVs. Each label carries BOTH a normalized ``column`` (log/sqrt-domain
# training target, in ``data.y``) AND the ``raw_column`` physical value (EDA-Schema /
# CircuitNet convention, in the parallel ``data.y_raw``) — so a downstream trainer
# can pick either convention without a regen. The raw columns are exactly the
# reference RTL2Graph "EDA-Schema style" labels (2026-07-14 alignment).
LABEL_SPECS = [
    {"node_type": NODE_TYPE_GATE, "order": 0, "file": "cell_congestion.csv", "column": "label", "raw_column": "cell_congestion"},
    {"node_type": NODE_TYPE_GATE, "order": 1, "file": "ir_drop.csv", "column": "label", "raw_column": "IR_Drop_mV"},
    # Timing raw twin uses Path_Delay_ns (= clk_period - worst_slack, floored at 0),
    # the finite pre-log1p value: y3 == log1p(y_raw3) exactly. Cell_Slack_ns is the
    # literal slack but is the string "INF" for cells off every STA path (parsed to
    # +inf, poisoning the tensor) — kept in the CSV for raw-slack consumers, not here.
    {"node_type": NODE_TYPE_PIN, "order": 2, "file": "timing_features.csv", "column": "label", "raw_column": "Path_Delay_ns"},
    {"node_type": NODE_TYPE_NET, "order": 3, "file": "wirelength.csv", "column": "label", "raw_column": "WireLength_um"},
]

# y tensor width = 1 (node_type) + 5 label orders. Orders 0-3 are the tool labels
# in LABEL_SPECS (congestion/irdrop/timing/wirelength); order 4 (y5) is the RC
# ground-cap label, placed by attach_rc_labels (net node in b/c, broadcast to pin
# nodes in d/e, dropped in f) rather than by the generic LABEL_SPECS folding — so
# it is deliberately NOT in LABEL_SPECS (which also folds net labels onto d/e/f
# clique edge_y, which is exactly where ground cap must NOT go). edge_y shares the
# width for schema symmetry; its y5 is always NaN (ground cap is never an edge label).
Y_WIDTH = 6
GROUND_CAP_Y = 5  # column index of the ground-cap label in the y tensor

Y_SCHEMA_BASE = {
    "y0": "node_type",
    "y1": "congestion_label (gate)",
    "y2": "irdrop_label (gate)",
    "y3": "timing_label (pin; per-cell min pin slack -> log1p path delay)",
    "y4": "wirelength_label (net; log1p um)",
    "y5": "ground_cap_label (net; log1p fF — on net node b/c, broadcast to pin nodes d/e, dropped f)",
}

# ``data.y_raw`` mirrors ``data.y`` slot-for-slot but carries the RAW physical
# value (EDA-Schema convention) instead of the normalized target. Same layout,
# same NaN-where-inapplicable rule; y_raw[:,0] copies node_type for symmetry.
# edge_y_raw / rc_edge_y_raw are the folded-edge / parasitic-edge analogues.
Y_RAW_SCHEMA_BASE = {
    "y0": "node_type",
    "y1": "congestion_raw (gate; demand/capacity ratio)",
    "y2": "irdrop_raw (gate; IR drop mV)",
    "y3": "timing_raw (pin; per-cell path delay ns = clk_period - worst_slack, floored 0)",
    "y4": "wirelength_raw (net; routed length um)",
    "y5": "ground_cap_raw (net; SPEF ground cap fF — same placement as y5 label)",
}


def _torch():
    import torch  # deferred: only the tensor-assembly paths need it

    return torch


def pad_schema_cols(cols: list[str], width: int = 8) -> list[str]:
    out = list(cols)
    while len(out) < width:
        out.append(f"_pad{len(out)}")
    return out[:width]


def ensure_feature_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    work = df.copy()
    for col in columns:
        if col not in work.columns:
            work[col] = 0
    return work


def clique_pairs(node_ids: list[int]) -> list[tuple[int, int]]:
    nodes = sorted(set(int(x) for x in node_ids))
    out: list[tuple[int, int]] = []
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            out.append((nodes[i], nodes[j]))
    return out


def load_feature_df(feature_root: str, file_name: str, graph_key: str,
                    usecols: Optional[list[str]] = None) -> pd.DataFrame:
    """One feature CSV, filtered to ``graph_id == graph_key`` when present."""
    path = os.path.join(feature_root, file_name)
    df = pd.read_csv(path, usecols=usecols)
    if "graph_id" in df.columns and graph_key:
        df = df[df["graph_id"].astype(str) == str(graph_key)].copy()
    return df.reset_index(drop=True)


def _unique(cols: list[str]) -> list[str]:
    out: list[str] = []
    for c in cols:
        if c not in out:
            out.append(c)
    return out


def build_feature_views(feature_root: str, graph_key: str):
    """Load + filter + sort the seven feature CSVs into consistent views.

    Filtering (verified semantics, identical across the b..f variants):
      * gates: drop FILL/TAP physical cells; keep only gates with >=1 signal pin
      * nets/iopins/edges: signal nets only (``net_type_id == 0`` — power/ground/
        clock/reset/scan nets are excluded, so the CLOCK TREE IS NOT in the graph)
      * pins: only (inst, pin) pairs on signal nets
    Every view is mergesort-sorted by its name key so downstream positional
    tensor assembly is deterministic and joins by name align by position.
    """
    gate_df = load_feature_df(feature_root, "nodes_gate.csv", graph_key,
                              _unique(["graph_id", "inst_name", "master", *GATE_SCHEMA]))
    net_df = load_feature_df(feature_root, "nodes_net.csv", graph_key,
                             _unique(["graph_id", "net_name", "net_type_id", *NET_SCHEMA]))
    iopin_df = load_feature_df(feature_root, "nodes_iopin.csv", graph_key,
                               _unique(["graph_id", "iopin_name", "net_name", "net_type_id", *IOPIN_SCHEMA]))
    pin_df = load_feature_df(feature_root, "nodes_pin.csv", graph_key,
                             _unique(["graph_id", "inst_name", "pin_name", *PIN_SCHEMA]))
    edges_gp = load_feature_df(feature_root, "edges_gate_pin.csv", graph_key,
                               ["graph_id", "inst_name", "pin_name"])
    edges_pn = load_feature_df(feature_root, "edges_pin_net.csv", graph_key,
                               ["graph_id", "inst_name", "pin_name", "net_name", "net_type_id"])
    edges_in = load_feature_df(feature_root, "edges_iopin_net.csv", graph_key,
                               ["graph_id", "iopin_name", "net_name", "net_type_id"])

    gate_df = gate_df[~gate_df["master"].str.contains("FILL|TAP", case=False, regex=True, na=False)].copy()
    pin_df = pin_df[pin_df["inst_name"] != "PIN"].copy()
    net_df = net_df[net_df["net_type_id"] == 0].copy()
    iopin_df = iopin_df[iopin_df["net_type_id"] == 0].copy()
    edges_pn = edges_pn[edges_pn["net_type_id"] == 0].copy()
    edges_in = edges_in[edges_in["net_type_id"] == 0].copy()

    signal_pin_pairs = edges_pn[["inst_name", "pin_name"]].drop_duplicates()
    if signal_pin_pairs.empty:
        raise ValueError(f"no signal pin-net edges found in {feature_root}")
    pin_df = pin_df.merge(signal_pin_pairs, on=["inst_name", "pin_name"], how="inner")

    keep_gates = set(pin_df["inst_name"].tolist())
    keep_nets = set(edges_pn["net_name"].tolist()) | set(edges_in["net_name"].tolist())
    keep_iopins = set(edges_in["iopin_name"].tolist())

    gate_df = gate_df[gate_df["inst_name"].isin(keep_gates)].copy()
    net_df = net_df[net_df["net_name"].isin(keep_nets)].copy()
    iopin_df = iopin_df[iopin_df["iopin_name"].isin(keep_iopins)].copy()

    pin_keys = pin_df[["inst_name", "pin_name"]].drop_duplicates()
    edges_gp = edges_gp.merge(pin_keys, on=["inst_name", "pin_name"], how="inner")
    edges_gp = edges_gp[edges_gp["inst_name"].isin(set(gate_df["inst_name"].tolist()))].drop_duplicates()

    edges_pn = edges_pn.merge(pin_keys, on=["inst_name", "pin_name"], how="inner")
    edges_pn = edges_pn[edges_pn["net_name"].isin(set(net_df["net_name"].tolist()))].drop_duplicates()

    edges_in = edges_in[
        edges_in["iopin_name"].isin(set(iopin_df["iopin_name"].tolist()))
        & edges_in["net_name"].isin(set(net_df["net_name"].tolist()))
    ].drop_duplicates()

    gate_df = ensure_feature_columns(gate_df, pad_schema_cols(GATE_SCHEMA)).sort_values(
        ["inst_name"], kind="mergesort").reset_index(drop=True)
    net_df = ensure_feature_columns(net_df, pad_schema_cols(NET_SCHEMA)).sort_values(
        ["net_name"], kind="mergesort").reset_index(drop=True)
    iopin_df = ensure_feature_columns(iopin_df, pad_schema_cols(IOPIN_SCHEMA)).sort_values(
        ["iopin_name"], kind="mergesort").reset_index(drop=True)
    pin_df = ensure_feature_columns(pin_df, pad_schema_cols(PIN_SCHEMA)).sort_values(
        ["inst_name", "pin_name"], kind="mergesort").reset_index(drop=True)
    edges_gp = edges_gp.sort_values(["inst_name", "pin_name"], kind="mergesort").reset_index(drop=True)
    edges_pn = edges_pn.sort_values(["inst_name", "pin_name", "net_name"], kind="mergesort").reset_index(drop=True)
    edges_in = edges_in.sort_values(["iopin_name", "net_name"], kind="mergesort").reset_index(drop=True)

    return gate_df, net_df, iopin_df, pin_df, edges_gp, edges_pn, edges_in


# --- Label loading + per-entity value builders --------------------------------

def load_label_df(label_root: str, file_name: str) -> pd.DataFrame:
    path = os.path.join(label_root, file_name)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"label file not found: {path}")
    return pd.read_csv(path)


def load_label_cache(label_root: str) -> dict[str, pd.DataFrame]:
    cache: dict[str, pd.DataFrame] = {}
    for spec in LABEL_SPECS:
        if spec["file"] not in cache:
            cache[spec["file"]] = load_label_df(label_root, spec["file"])
    return cache


# Join-key columns each label builder requires (first match wins for pins).
_LABEL_KEY_COLS = {
    NODE_TYPE_GATE: ("Cell",),
    NODE_TYPE_NET: ("Net",),
    NODE_TYPE_PIN: ("Pin", "Cell"),
}


def label_health(label_dfs: dict[str, pd.DataFrame], design_key: str) -> dict[str, dict]:
    """Per-label-file usability check, mirroring what build_*_label_values
    silently require before joining. The builders deliberately stay fail-soft
    (a broken label file yields NaN y, never a crashed graph build) — this
    projection makes that degradation LOUD and machine-readable instead of
    invisible (the 2026-07-05 irdrop incident: a raw-format ir_drop.csv made
    y2 100% NaN across all variants with manifest status 'ok')."""
    health: dict[str, dict] = {}
    for spec in LABEL_SPECS:
        df = label_dfs[spec["file"]]
        if "Design" not in df.columns:
            status, reason = "unusable", (
                f"no 'Design' column — raw/unprocessed csv? (columns: {list(df.columns)[:6]})")
        elif spec["column"] not in df.columns:
            status, reason = "unusable", f"label column '{spec['column']}' missing"
        elif not any(k in df.columns for k in _LABEL_KEY_COLS[spec["node_type"]]):
            status, reason = "unusable", (
                f"join-key column missing (need one of {_LABEL_KEY_COLS[spec['node_type']]})")
        elif df[df["Design"] == design_key].empty:
            status, reason = "no_rows_for_design", (
                f"no rows for Design={design_key!r} (keys present: "
                f"{sorted(df['Design'].astype(str).unique()[:3])}...)")
        elif not pd.to_numeric(
                df.loc[df["Design"] == design_key, spec["column"]],
                errors="coerce").notna().any():
            # Column + rows present but EVERY value is NaN/non-numeric for this
            # design -> the y slot would be 100% NaN. This gate previously checked
            # only column/row PRESENCE, so an all-NaN join (a name-escaping or
            # extraction regression, or a partial dump) still reported 'ok' and
            # shipped fully green through the manifest AND verify_graph_dataset.py
            # (whose value checks are NaN-vacuous). A legitimately degenerate label
            # is all-ZERO (e.g. combinational timing, low-IR irdrop), which is
            # non-NaN and still reads 'ok'. See docs/superpowers/plans/
            # verifier-silent-lies-audit-2026-07-07.md BUG-1.
            status, reason = "all_nan", (
                f"label column '{spec['column']}' is entirely NaN/non-numeric for "
                f"Design={design_key!r} — its y slot would be all-NaN (raw dump / "
                f"broken join?)")
        else:
            status, reason = "ok", ""
        health[spec["file"]] = {"status": status, "reason": reason}
    return health


def _assert_unique_keys(m: pd.DataFrame, key_col: str, context: str) -> None:
    """A left-join against duplicated keys EXPLODES the row count, and the
    downstream pad_or_truncate_1d/to_float32_matrix would then silently
    truncate — misaligning every value after the first duplicate. Fail loud
    instead: duplicates here mean an extractor bug (all label/feature writers
    emit unique keys; ir_drop's legitimate per-PDN-node dups are groupby-maxed
    before this point)."""
    dup_mask = m[key_col].duplicated()
    if dup_mask.any():
        example = m.loc[dup_mask, key_col].iloc[0]
        raise ValueError(
            f"duplicate '{key_col}' rows in {context} (e.g. {example!r}) — "
            f"joining would silently misalign values; dedup upstream")


def _merged_label_values(base_df, m, key_col, base_col, col, context=""):
    _assert_unique_keys(m, key_col, context or "label data")
    torch = _torch()
    merged = base_df[[base_col]].merge(m, left_on=base_col, right_on=key_col, how="left")
    return torch.tensor(merged[col].fillna(float("nan")).to_numpy(), dtype=torch.float32)


def build_gate_label_values(base_df, label_dfs, design_key, value_col_key="column"):
    """Gate node labels {order: tensor}. ``value_col_key`` selects the source
    column per spec: "column" (normalized -> data.y) or "raw_column" (raw
    physical -> data.y_raw). A spec without that column is skipped (slot NaN)."""
    out = {}
    for spec in LABEL_SPECS:
        if spec["node_type"] != NODE_TYPE_GATE:
            continue
        col = spec.get(value_col_key)
        if not col:
            continue
        df = label_dfs[spec["file"]]
        if "Design" not in df.columns:
            continue
        df = df[df["Design"] == design_key].copy()
        if col not in df.columns or "Cell" not in df.columns:
            continue
        m = df[["Cell", col]].copy()
        m[col] = pd.to_numeric(m[col], errors="coerce")
        if spec["file"] == "ir_drop.csv":
            # PDNSim can emit several rows per instance (one per PDN node) —
            # keep the worst-case drop.
            m = m.groupby("Cell", as_index=False)[col].max()
        out[spec["order"]] = _merged_label_values(base_df, m, "Cell", "inst_name", col,
                                                  context=spec["file"])
    return out


def build_net_label_values(base_df, label_dfs, design_key, value_col_key="column"):
    out = {}
    for spec in LABEL_SPECS:
        if spec["node_type"] != NODE_TYPE_NET:
            continue
        col = spec.get(value_col_key)
        if not col:
            continue
        df = label_dfs[spec["file"]]
        if "Design" not in df.columns:
            continue
        df = df[df["Design"] == design_key].copy()
        if col not in df.columns or "Net" not in df.columns:
            continue
        m = df[["Net", col]].copy()
        m[col] = pd.to_numeric(m[col], errors="coerce")
        out[spec["order"]] = _merged_label_values(base_df, m, "Net", "net_name", col,
                                                  context=spec["file"])
    return out


def build_pin_label_values(base_df, label_dfs, design_key, value_col_key="column"):
    """Pin labels join by inst/pin when the CSV has a ``Pin`` column, else by Cell
    (timing_features.csv is per-cell: every pin of a cell inherits its label).
    ``value_col_key`` selects normalized ("column") vs raw ("raw_column")."""
    torch = _torch()
    out = {}
    for spec in LABEL_SPECS:
        if spec["node_type"] != NODE_TYPE_PIN:
            continue
        col = spec.get(value_col_key)
        if not col:
            continue
        df = label_dfs[spec["file"]]
        if "Design" not in df.columns:
            continue
        df = df[df["Design"] == design_key].copy()
        if col not in df.columns:
            continue
        if "Pin" in df.columns:
            m = df[["Pin", col]].copy()
            m[col] = pd.to_numeric(m[col], errors="coerce")
            _assert_unique_keys(m, "Pin", spec["file"])
            keys = base_df[["inst_name", "pin_name"]].copy()
            keys["Pin"] = keys["inst_name"].astype(str) + "/" + keys["pin_name"].astype(str)
            merged = keys[["Pin"]].merge(m, on="Pin", how="left")
            out[spec["order"]] = torch.tensor(
                merged[col].fillna(float("nan")).to_numpy(), dtype=torch.float32)
        elif "Cell" in df.columns:
            m = df[["Cell", col]].copy()
            m[col] = pd.to_numeric(m[col], errors="coerce")
            out[spec["order"]] = _merged_label_values(base_df, m, "Cell", "inst_name", col,
                                                      context=spec["file"])
    return out


# --- Tensor helpers ------------------------------------------------------------

def to_float32_matrix(df: pd.DataFrame, columns: list[str], rows: int):
    torch = _torch()
    if df.empty:
        return torch.zeros((rows, len(columns)), dtype=torch.float32)
    for c in columns:
        if c not in df.columns:
            df[c] = 0
    mat = df[columns].copy()
    for c in columns:
        mat[c] = pd.to_numeric(mat[c], errors="coerce").fillna(0)
    arr = mat.to_numpy(dtype="float32", copy=False)
    if arr.shape[0] >= rows:
        arr = arr[:rows]
    else:
        pad = torch.zeros((rows - arr.shape[0], arr.shape[1]), dtype=torch.float32)
        return torch.cat([torch.from_numpy(arr), pad], dim=0)
    return torch.from_numpy(arr)


def pad_or_truncate_1d(values, length: int):
    torch = _torch()
    if length <= 0:
        return torch.empty((0,), dtype=torch.float32)
    v = values.view(-1).to(torch.float32)
    if int(v.numel()) == length:
        return v
    if int(v.numel()) > length:
        return v[:length]
    pad = torch.full((length - int(v.numel()),), float("nan"), dtype=torch.float32)
    return torch.cat([v, pad], dim=0)


def build_directed_edges(base_src, base_dst, base_attr, base_y, base_type, base_y_raw=None):
    """Duplicate every undirected base edge into both directions, repeating
    edge_attr / edge_type / edge_y / edge_y_raw rows pairwise.

    Edge columns are INTERLEAVED — [fwd0, rev0, fwd1, rev1, ...] — so the
    pairwise-repeated attr/type/y rows align with their edges. (The RTL2Graph
    originals concatenated [all forwards | all reverses] while still repeating
    attrs pairwise, misaligning edge_attr/edge_type/edge_y with edge_index for
    every edge past the first — bug #5 of the 2026-07-05 audit, fixed here.)

    Returns (edge_index, edge_attr, edge_type, edge_y, edge_y_raw). ``edge_y_raw``
    (raw physical folded labels) mirrors ``edge_y``; all-NaN when base_y_raw is
    None (the caller supplies no raw labels for that edge family).
    """
    torch = _torch()
    if not base_src:
        width = int(base_attr.shape[1]) if base_attr.ndim == 2 else 0
        return (
            torch.empty((2, 0), dtype=torch.long),
            torch.zeros((0, width), dtype=torch.float32),
            torch.empty((0,), dtype=torch.long),
            torch.zeros((0, Y_WIDTH), dtype=torch.float32),
            torch.zeros((0, Y_WIDTH), dtype=torch.float32),
        )
    src = torch.tensor(base_src, dtype=torch.long)
    dst = torch.tensor(base_dst, dtype=torch.long)
    fwd = torch.stack([src, dst], dim=0)
    rev = torch.stack([dst, src], dim=0)
    edge_index = torch.stack([fwd, rev], dim=2).reshape(2, -1)
    edge_attr = torch.repeat_interleave(base_attr, 2, dim=0)
    edge_type = torch.repeat_interleave(base_type, 2)
    edge_y = torch.repeat_interleave(base_y, 2, dim=0)
    if base_y_raw is None:
        edge_y_raw = torch.full_like(edge_y, float("nan"))
    else:
        edge_y_raw = torch.repeat_interleave(base_y_raw, 2, dim=0)
    return edge_index, edge_attr, edge_type, edge_y, edge_y_raw


def load_global_feat(feature_root: str, graph_key: str):
    torch = _torch()
    md = load_feature_df(feature_root, "metadata.csv", graph_key)
    if md.empty:
        return None
    vals = []
    for k in METADATA_SCHEMA:
        if k not in md.columns:
            vals.append(0.0)
            continue
        v = pd.to_numeric(md.iloc[0][k], errors="coerce")
        vals.append(float(0 if pd.isna(v) else v))
    return torch.tensor(vals, dtype=torch.float32)


def _normalize_name_value(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    text = str(value)
    return "" if text == "nan" else text


def node_names_for(node_type, gate_df, net_df, iopin_df, pin_df) -> list[str]:
    """One display/join name per node: gate=inst, net=net, iopin=port,
    pin=inst/pin — in the (verified) block-positional node order."""
    total = int(node_type.numel())
    names = [""] * total

    def fill(type_id, values):
        idx = (node_type == type_id).nonzero(as_tuple=False).view(-1).tolist()
        for pos, val in zip(idx, values):
            names[int(pos)] = _normalize_name_value(val)

    fill(NODE_TYPE_GATE, gate_df["inst_name"].tolist() if "inst_name" in gate_df.columns else [])
    fill(NODE_TYPE_NET, net_df["net_name"].tolist() if "net_name" in net_df.columns else [])
    fill(NODE_TYPE_IO_PIN, iopin_df["iopin_name"].tolist() if "iopin_name" in iopin_df.columns else [])
    if {"inst_name", "pin_name"} <= set(pin_df.columns):
        pin_full = [
            f"{_normalize_name_value(i)}/{_normalize_name_value(p)}".strip("/")
            for i, p in zip(pin_df["inst_name"], pin_df["pin_name"])
        ]
    else:
        pin_full = []
    fill(NODE_TYPE_PIN, pin_full)
    return names


# --- RC parasitic labels (Y side; extract_rc.py -> per-view attachment) --------
# Three CSVs from the label stage, joined here onto the graph's y / parasitic-edge
# tensors. Ground cap is a NET label (y5); coupling cap is a net-PAIR edge label;
# equivalent resistance is a pin-PAIR (same-net) edge label. The parasitic edges
# live on their OWN edge set (rc_edge_index / rc_edge_type / rc_edge_y), separate
# from the physical-topology edge_index (they are "not the physical topology").
RC_GROUND_CAP_FILE = "net_ground_cap.csv"
RC_COUPLING_FILE = "coupling_cap.csv"
RC_EQUIV_RES_FILE = "equiv_res.csv"
RC_DRIVER_FILE = "net_driver.csv"
RC_EDGE_TYPE_COUPLING = 0
RC_EDGE_TYPE_RESISTANCE = 1


def load_rc_label_cache(label_root: str) -> dict[str, "pd.DataFrame"]:
    """Load the RC label CSVs, fail-soft (missing/unreadable -> empty frame). A
    design with no SPEF has header-only CSVs -> empty frames -> no RC labels."""
    cache: dict[str, pd.DataFrame] = {}
    for fn in (RC_GROUND_CAP_FILE, RC_COUPLING_FILE, RC_EQUIV_RES_FILE, RC_DRIVER_FILE):
        path = os.path.join(label_root, fn)
        try:
            cache[fn] = pd.read_csv(path, dtype=str) if os.path.isfile(path) else pd.DataFrame()
        except Exception:
            cache[fn] = pd.DataFrame()
    return cache


def _rc_rows(rc, fname, design_key):
    df = rc.get(fname)
    if df is None or df.empty or "Design" not in df.columns:
        return None
    sub = df[df["Design"].astype(str) == str(design_key)]
    return sub if not sub.empty else None


def _num_or_nan(sub, col):
    """Coerce a column to numeric, or an all-NaN series if the column is absent
    (legacy CSV without the raw metric) — so raw values stay row-aligned."""
    if col in sub.columns:
        return pd.to_numeric(sub[col], errors="coerce")
    return pd.Series([float("nan")] * len(sub), index=sub.index)


def rc_ground_cap_by_net(rc, design_key) -> dict:
    """{net: (label, raw_fF)} — normalized ground-cap label + raw SPEF ground cap
    (raw NaN on a legacy CSV lacking ``ground_cap_fF``)."""
    sub = _rc_rows(rc, RC_GROUND_CAP_FILE, design_key)
    out = {}
    if sub is None or not {"Net", "label"} <= set(sub.columns):
        return out
    labs = pd.to_numeric(sub["label"], errors="coerce")
    raws = _num_or_nan(sub, "ground_cap_fF")
    for net, lab, raw in zip(sub["Net"], labs, raws):
        if lab == lab:  # not NaN
            out[str(net)] = (float(lab), float(raw))
    return out


def rc_net_driver(rc, design_key) -> dict[str, tuple[str, str]]:
    """{net_name: (inst, pin)} — inst == 'PIN' for a top-level port driver."""
    sub = _rc_rows(rc, RC_DRIVER_FILE, design_key)
    out: dict[str, tuple[str, str]] = {}
    if sub is None or not {"Net", "DrvInst", "DrvPin"} <= set(sub.columns):
        return out
    for net, di, dp in zip(sub["Net"], sub["DrvInst"], sub["DrvPin"]):
        out[str(net)] = (str(di), str(dp))
    return out


def rc_coupling_rows(rc, design_key):
    """[(net1, net2, label, raw_fF)] cross-net coupling-cap edges (raw NaN on a
    legacy CSV lacking ``coupling_cap_fF``)."""
    sub = _rc_rows(rc, RC_COUPLING_FILE, design_key)
    rows = []
    if sub is None or not {"Net1", "Net2", "label"} <= set(sub.columns):
        return rows
    labs = pd.to_numeric(sub["label"], errors="coerce")
    raws = _num_or_nan(sub, "coupling_cap_fF")
    for n1, n2, lab, raw in zip(sub["Net1"], sub["Net2"], labs, raws):
        if lab == lab:
            rows.append((str(n1), str(n2), float(lab), float(raw)))
    return rows


def rc_resistance_rows(rc, design_key):
    """[((inst1,pin1),(inst2,pin2), label, raw_ohm)] same-net pin-pair
    equiv-resistance edges (raw NaN on a legacy CSV lacking ``equiv_res_ohm``)."""
    sub = _rc_rows(rc, RC_EQUIV_RES_FILE, design_key)
    rows = []
    if sub is None or not {"Inst1", "Pin1", "Inst2", "Pin2", "label"} <= set(sub.columns):
        return rows
    labs = pd.to_numeric(sub["label"], errors="coerce")
    raws = _num_or_nan(sub, "equiv_res_ohm")
    for i1, p1, i2, p2, lab, raw in zip(sub["Inst1"], sub["Pin1"], sub["Inst2"], sub["Pin2"], labs, raws):
        if lab == lab:
            rows.append(((str(i1), str(p1)), (str(i2), str(p2)), float(lab), float(raw)))
    return rows


def build_parasitic_edges(coupling, resistance):
    """Assemble the parasitic edge tensors from resolved node-index edge lists.

    coupling/resistance: lists of (src_idx, dst_idx, label, raw). Returns
    symmetrized (rc_edge_index[2,E], rc_edge_type[E], rc_edge_y[E,3],
    rc_edge_y_raw[E,3]) with y columns [type, coupling, equiv_res] (off-type
    column = NaN); rc_edge_y is the normalized (log1p) label, rc_edge_y_raw the
    raw physical value (fF / Ohm). Edges are interleaved fwd/rev so the
    pairwise-repeated type/y rows align with edge_index (same convention as
    build_directed_edges)."""
    torch = _torch()
    src, dst, etype, yrows, yraw = [], [], [], [], []
    nan = float("nan")
    for s, t, lab, raw in coupling:
        if s is None or t is None or s == t:
            continue
        src.append(int(s)); dst.append(int(t)); etype.append(RC_EDGE_TYPE_COUPLING)
        yrows.append((float(RC_EDGE_TYPE_COUPLING), lab, nan))
        yraw.append((float(RC_EDGE_TYPE_COUPLING), raw, nan))
    for s, t, lab, raw in resistance:
        if s is None or t is None or s == t:
            continue
        src.append(int(s)); dst.append(int(t)); etype.append(RC_EDGE_TYPE_RESISTANCE)
        yrows.append((float(RC_EDGE_TYPE_RESISTANCE), nan, lab))
        yraw.append((float(RC_EDGE_TYPE_RESISTANCE), nan, raw))
    if not src:
        return (torch.empty((2, 0), dtype=torch.long),
                torch.empty((0,), dtype=torch.long),
                torch.zeros((0, 3), dtype=torch.float32),
                torch.zeros((0, 3), dtype=torch.float32))
    s_t = torch.tensor(src, dtype=torch.long)
    d_t = torch.tensor(dst, dtype=torch.long)
    fwd = torch.stack([s_t, d_t], dim=0)
    rev = torch.stack([d_t, s_t], dim=0)
    edge_index = torch.stack([fwd, rev], dim=2).reshape(2, -1)
    edge_type = torch.repeat_interleave(torch.tensor(etype, dtype=torch.long), 2)
    edge_y = torch.repeat_interleave(torch.tensor(yrows, dtype=torch.float32), 2, dim=0)
    edge_y_raw = torch.repeat_interleave(torch.tensor(yraw, dtype=torch.float32), 2, dim=0)
    return edge_index, edge_type, edge_y, edge_y_raw


def attach_rc_labels(data, rc, design_key, *, net_idx=None, pin_idx=None,
                     iopin_idx=None, pin_net_map=None):
    """Place the RC labels onto ``data`` per the view's available node types.

    Endpoint-resolution rule (see references/label-extraction.md): a net-endpoint
    resolves to a net NODE if present, else the net's driver PIN node, else the
    label is dropped. Ground cap: net node (net_idx) -> broadcast to pin nodes
    (pin_idx + pin_net_map) -> dropped. Coupling (net-pair): net<->net -> driver
    pin<->driver pin -> dropped. Resistance (pin-pair, same net): only where pin
    nodes exist. Always attaches rc_edge_* (possibly empty) so the schema is
    uniform across designs/views."""
    ground = rc_ground_cap_by_net(rc, design_key)   # {net: (label, raw)}
    driver = rc_net_driver(rc, design_key)
    y = data.y
    y_raw = getattr(data, "y_raw", None)   # parallel raw-label tensor (if present)

    def _set_gcap(row_idx, lab, raw):
        y[row_idx, GROUND_CAP_Y] = lab
        if y_raw is not None:
            y_raw[row_idx, GROUND_CAP_Y] = raw

    # --- ground cap -> y[:, GROUND_CAP_Y] (+ y_raw) ---
    if net_idx is not None:
        for net, (lab, raw) in ground.items():
            idx = net_idx.get(net)
            if idx is not None:
                _set_gcap(idx, lab, raw)
    elif pin_idx is not None and pin_net_map is not None:
        for key, pidx in pin_idx.items():
            net = pin_net_map.get(key)
            if net is not None:
                pair = ground.get(net)
                if pair is not None:
                    _set_gcap(pidx, pair[0], pair[1])
    # else (f: no net & no pin nodes): ground cap dropped -> y5 stays NaN

    def net_to_node(net):
        if net_idx is not None:
            return net_idx.get(net)
        if pin_idx is None:  # no pin nodes (f) -> coupling dropped
            return None
        drv = driver.get(net)
        if drv is None:
            return None
        di, dp = drv
        if di == "PIN":
            return iopin_idx.get(dp) if iopin_idx is not None else None
        return pin_idx.get((di, dp))

    def pin_to_node(key):
        inst, pin = key
        if inst == "PIN":
            return iopin_idx.get(pin) if iopin_idx is not None else None
        return pin_idx.get((inst, pin)) if pin_idx is not None else None

    coupling_edges = []
    if net_idx is not None or pin_idx is not None:
        for n1, n2, lab, raw in rc_coupling_rows(rc, design_key):
            s, t = net_to_node(n1), net_to_node(n2)
            if s is not None and t is not None and s != t:
                coupling_edges.append((s, t, lab, raw))

    resistance_edges = []
    if pin_idx is not None:
        for k1, k2, lab, raw in rc_resistance_rows(rc, design_key):
            s, t = pin_to_node(k1), pin_to_node(k2)
            if s is not None and t is not None and s != t:
                resistance_edges.append((s, t, lab, raw))

    rc_ei, rc_et, rc_ey, rc_ey_raw = build_parasitic_edges(coupling_edges, resistance_edges)
    data.rc_edge_index = rc_ei
    data.rc_edge_type = rc_et
    data.rc_edge_y = rc_ey
    data.rc_edge_y_raw = rc_ey_raw
    data.rc_edge_schema = {
        "rc_edge_type": {RC_EDGE_TYPE_COUPLING: "coupling_cap (net-pair)",
                         RC_EDGE_TYPE_RESISTANCE: "equiv_res (pin-pair, same net)"},
        "rc_edge_y0": "rc_edge_type",
        "rc_edge_y1": "coupling_cap_label (log1p fF; net M-N)",
        "rc_edge_y2": "equiv_res_label (log1p Ohm; two pins on one net)",
        "rc_edge_y_raw1": "coupling_cap_raw (fF; net M-N)",
        "rc_edge_y_raw2": "equiv_res_raw (Ohm; two pins on one net)",
        "design_key": design_key,
    }
    return data


# --- Heterogeneous re-view (homo Data <-> HeteroData) --------------------------
# The dataset DEFAULT is a torch_geometric HeteroData (2026-07-16, generalizing
# the external RTL2Graph ``generate_hetero_bgraph.py`` from the b-graph to all
# five views b..f). The verified block-positional homogeneous ``Data`` is still
# the internal source of truth (every filter/sort/label-join is done there, then
# re-viewed) — homo_to_hetero() is a pure re-view that changes NO value, and
# hetero_to_homo() is its exact inverse (used by the round-trip test AND, via an
# INDEPENDENT reimplementation, by tools/verify_graph_dataset.py so the full
# homo verification surface transitively certifies the hetero graphs).
#
# Node stores drop the redundant node_type column (x0/y0 — the store key IS the
# type) and keep [graph_id, 8 feats] (x) / the 5 label slots (y, y_raw). Edges
# are grouped into relations (src_type, relation, dst_type): the relation is the
# folded entity from the view's edge_schema (b-view physical edges -> "connects";
# c pin-fold -> "pin"/"iopin_connection"; d/e/f -> "gate_pin"/"net"/"gate").
# Because view e folds BOTH gates and nets onto pin<->pin edges, (src,dst) alone
# is ambiguous — the folded entity in the relation triple disambiguates. RC
# parasitic edges become their own relations ("rc_coupling"/"rc_resistance") with
# a 2-wide [coupling_label, equiv_res_label] edge_y (+ raw twin). See
# references/graph-dataset.md ("Heterogeneous graphs").

HETERO_NODE_TYPES = {
    NODE_TYPE_GATE: "gate",
    NODE_TYPE_NET: "net",
    NODE_TYPE_IO_PIN: "iopin",
    NODE_TYPE_PIN: "pin",
}
HETERO_NODE_TYPE_IDS = {v: k for k, v in HETERO_NODE_TYPES.items()}
# b-view physical edges carry no folded entity / edge_type -> a single relation.
B_EDGE_RELATION = "connects"
RC_EDGE_RELATIONS = {
    RC_EDGE_TYPE_COUPLING: "rc_coupling",
    RC_EDGE_TYPE_RESISTANCE: "rc_resistance",
}
RC_EDGE_RELATION_IDS = {v: k for k, v in RC_EDGE_RELATIONS.items()}


def _edge_relation_name(edge_schema, etype_id):
    """Relation label for a physical/folded edge. b-view edges (no edge_type)
    use ``connects``; folded views name the relation after the folded entity in
    their ``edge_schema['edge_type']`` map (first token, e.g. "pin", "net",
    "gate_pin")."""
    if etype_id is None:
        return B_EDGE_RELATION
    if edge_schema:
        m = edge_schema.get("edge_type") or {}
        name = m.get(etype_id, m.get(str(etype_id)))
        if name:
            return str(name).strip().split()[0]
    return f"etype{int(etype_id)}"


def _hetero_present_types(node_type):
    torch = _torch()
    return [t for t in sorted(HETERO_NODE_TYPES)
            if int((node_type == t).sum()) > 0]


def _hetero_local_maps(node_type, present, n_total):
    """Per present node type: (global-index tensor, global->local remap table)."""
    torch = _torch()
    masks, local_of = {}, {}
    for t in present:
        m = (node_type == t).nonzero(as_tuple=False).view(-1)
        masks[t] = m
        tbl = torch.full((n_total,), -1, dtype=torch.long)
        tbl[m] = torch.arange(m.numel(), dtype=torch.long)
        local_of[t] = tbl
    return masks, local_of


def _split_edges_to_hetero(h, data, node_type, local_of, present):
    """Group data.edge_index columns into (src, relation, dst) edge stores,
    slicing edge_attr/edge_type/edge_y/edge_y_raw per group. edge_y/edge_y_raw
    drop the redundant edge_type column 0 (-> 5-wide)."""
    torch = _torch()
    ei = getattr(data, "edge_index", None)
    if ei is None or ei.shape[1] == 0:
        return
    edge_attr = getattr(data, "edge_attr", None)
    edge_type = getattr(data, "edge_type", None)
    edge_y = getattr(data, "edge_y", None)
    edge_y_raw = getattr(data, "edge_y_raw", None)
    edge_schema = getattr(data, "edge_schema", None)
    src_t = node_type[ei[0]]
    dst_t = node_type[ei[1]]
    for st in present:
        for dt in present:
            base = (src_t == st) & (dst_t == dt)
            if not bool(base.any()):
                continue
            if edge_type is None:
                groups = [(None, base)]
            else:
                groups = [(int(eid), base & (edge_type == int(eid)))
                          for eid in edge_type[base].unique().tolist()]
            for eid, mask in groups:
                cols = mask.nonzero(as_tuple=False).view(-1)
                if cols.numel() == 0:
                    continue
                rel = _edge_relation_name(edge_schema, eid)
                store = h[HETERO_NODE_TYPES[st], rel, HETERO_NODE_TYPES[dt]]
                store.edge_index = torch.stack(
                    [local_of[st][ei[0][cols]], local_of[dt][ei[1][cols]]], dim=0
                ).contiguous()
                if edge_attr is not None:
                    store.edge_attr = edge_attr[cols].contiguous()
                if edge_type is not None:
                    store.edge_type = edge_type[cols].contiguous()
                if edge_y is not None:
                    store.edge_y = edge_y[cols][:, 1:].contiguous()
                if edge_y_raw is not None:
                    store.edge_y_raw = edge_y_raw[cols][:, 1:].contiguous()


def _split_rc_edges_to_hetero(h, data, node_type, local_of, present):
    """Group rc_edge_index into rc_coupling / rc_resistance relation stores. The
    2-wide [coupling_label, equiv_res_label] slice rides as rc_edge_y (+ raw);
    the schema is always recorded so a consumer can discover it even when empty."""
    torch = _torch()
    sch = getattr(data, "rc_edge_schema", None)
    if sch is not None:
        h.rc_edge_schema = sch
    rc_ei = getattr(data, "rc_edge_index", None)
    rc_type = getattr(data, "rc_edge_type", None)
    if rc_ei is None or rc_ei.shape[1] == 0 or rc_type is None:
        return
    rc_y = getattr(data, "rc_edge_y", None)
    rc_y_raw = getattr(data, "rc_edge_y_raw", None)
    src_t = node_type[rc_ei[0]]
    dst_t = node_type[rc_ei[1]]
    for st in present:
        for dt in present:
            base = (src_t == st) & (dst_t == dt)
            if not bool(base.any()):
                continue
            for rid in rc_type[base].unique().tolist():
                rid = int(rid)
                cols = (base & (rc_type == rid)).nonzero(as_tuple=False).view(-1)
                if cols.numel() == 0:
                    continue
                rel = RC_EDGE_RELATIONS.get(rid, f"rc_type{rid}")
                store = h[HETERO_NODE_TYPES[st], rel, HETERO_NODE_TYPES[dt]]
                store.edge_index = torch.stack(
                    [local_of[st][rc_ei[0][cols]], local_of[dt][rc_ei[1][cols]]], dim=0
                ).contiguous()
                store.rc_edge_type = rc_type[cols].contiguous()
                if rc_y is not None:
                    store.rc_edge_y = rc_y[cols][:, 1:].contiguous()
                if rc_y_raw is not None:
                    store.rc_edge_y_raw = rc_y_raw[cols][:, 1:].contiguous()


def homo_to_hetero(data):
    """Re-view a homogeneous b..f ``Data`` as a torch_geometric ``HeteroData``,
    changing NO value. See the module note above and hetero_to_homo() (inverse)."""
    torch = _torch()
    from torch_geometric.data import HeteroData

    h = HeteroData()
    x = data.x
    node_type = x[:, 0].long()
    n_total = int(node_type.numel())
    present = _hetero_present_types(node_type)
    masks, local_of = _hetero_local_maps(node_type, present, n_total)

    y = getattr(data, "y", None)
    y_raw = getattr(data, "y_raw", None)
    node_name = getattr(data, "node_name", None)
    x_schema = getattr(data, "x_schema", None) or {}
    x_schema_per_type = {}
    for t in present:
        name = HETERO_NODE_TYPES[t]
        m = masks[t]
        h[name].x = x[m][:, 1:].contiguous()          # drop node_type col0
        if y is not None:
            h[name].y = y[m][:, 1:].contiguous()      # drop node_type col0
        if y_raw is not None:
            h[name].y_raw = y_raw[m][:, 1:].contiguous()
        if node_name is not None:
            h[name].node_name = [node_name[int(i)] for i in m.tolist()]
        feat_cols = x_schema.get(f"{name}_x2_9")
        if feat_cols:
            x_schema_per_type[name] = ["graph_id", *feat_cols]

    _split_edges_to_hetero(h, data, node_type, local_of, present)
    _split_rc_edges_to_hetero(h, data, node_type, local_of, present)

    # graph-level provenance / schema (kept verbatim so hetero_to_homo restores it)
    h.graph_kind = "hetero"
    h.graph_id = float(x[0, 1].item()) if n_total else 0.0
    h.feature_graph_key = getattr(data, "feature_graph_key", "")
    gf = getattr(data, "global_feat", None)
    if gf is not None:
        h.global_feat = gf
    for attr in ("x_schema", "y_schema", "y_raw_schema", "edge_schema"):
        v = getattr(data, attr, None)
        if v is not None:
            setattr(h, attr, v)
    if x_schema_per_type:
        h.x_schema_per_type = x_schema_per_type
    return h


def hetero_to_homo(h):
    """Exact inverse of homo_to_hetero: reassemble the block-positional
    homogeneous ``Data``.

    Node stores are concatenated in canonical type-id order (gate, net, iopin,
    pin), which reproduces the homo node order because every view lays its blocks
    out in ascending type id. Edge order is regrouped-by-relation (NOT preserved),
    but each edge keeps its own attr/type/y row, and the downstream verifier keys
    on node position + per-edge endpoints, never on edge order."""
    torch = _torch()
    from torch_geometric.data import Data

    node_type_names = set(h.node_types)
    order = [t for t in sorted(HETERO_NODE_TYPES)
             if HETERO_NODE_TYPES[t] in node_type_names]

    base_off, off = {}, 0
    xs, ys, yraws, names = [], [], [], []
    has_y, has_yraw = True, True
    for t in order:
        store = h[HETERO_NODE_TYPES[t]]
        xt = store.x
        n = int(xt.shape[0])
        base_off[t] = off
        off += n
        col0 = torch.full((n, 1), float(t), dtype=torch.float32)
        xs.append(torch.cat([col0, xt.float()], dim=1))
        sy = getattr(store, "y", None)
        if sy is not None:
            ys.append(torch.cat([col0, sy.float()], dim=1))
        else:
            has_y = False
        syr = getattr(store, "y_raw", None)
        if syr is not None:
            yraws.append(torch.cat([col0, syr.float()], dim=1))
        else:
            has_yraw = False
        sn = getattr(store, "node_name", None)
        if sn is not None:
            names.extend(list(sn))

    x = torch.cat(xs, dim=0) if xs else torch.zeros((0, 10), dtype=torch.float32)
    data = Data(x=x)
    if has_y and ys:
        data.y = torch.cat(ys, dim=0)
    if has_yraw and yraws:
        data.y_raw = torch.cat(yraws, dim=0)
    if names:
        data.node_name = names

    rc_rels = set(RC_EDGE_RELATIONS.values())
    e_src, e_dst, e_attr, e_type, e_y, e_yraw = [], [], [], [], [], []
    have_attr = have_type = have_y = have_yraw = False
    rc_src, rc_dst, rc_type, rc_y, rc_yraw = [], [], [], [], []
    have_rc_y = have_rc_yraw = False
    for (sname, rel, dname) in h.edge_types:
        store = h[sname, rel, dname]
        ei = getattr(store, "edge_index", None)
        if ei is None or ei.shape[1] == 0:
            continue
        st, dt = HETERO_NODE_TYPE_IDS[sname], HETERO_NODE_TYPE_IDS[dname]
        n = int(ei.shape[1])
        g_src = ei[0] + base_off[st]
        g_dst = ei[1] + base_off[dt]
        if rel in rc_rels:
            rct = getattr(store, "rc_edge_type", None)
            if rct is None:
                rct = torch.full((n,), RC_EDGE_RELATION_IDS.get(rel, 0), dtype=torch.long)
            rc_src.append(g_src)
            rc_dst.append(g_dst)
            rc_type.append(rct)
            col0 = rct.view(-1, 1).float()
            ry = getattr(store, "rc_edge_y", None)
            if ry is not None:
                have_rc_y = True
                rc_y.append(torch.cat([col0, ry.float()], dim=1))
            ryr = getattr(store, "rc_edge_y_raw", None)
            if ryr is not None:
                have_rc_yraw = True
                rc_yraw.append(torch.cat([col0, ryr.float()], dim=1))
        else:
            e_src.append(g_src)
            e_dst.append(g_dst)
            ety = getattr(store, "edge_type", None)
            col0 = (ety.view(-1, 1).float() if ety is not None
                    else torch.zeros((n, 1), dtype=torch.float32))
            at = getattr(store, "edge_attr", None)
            if at is not None:
                have_attr = True
                e_attr.append(at.float())
            else:
                e_attr.append(torch.zeros((n, 8), dtype=torch.float32))
            if ety is not None:
                have_type = True
                e_type.append(ety)
            else:
                e_type.append(torch.zeros((n,), dtype=torch.long))
            ey = getattr(store, "edge_y", None)
            if ey is not None:
                have_y = True
                e_y.append(torch.cat([col0, ey.float()], dim=1))
            eyr = getattr(store, "edge_y_raw", None)
            if eyr is not None:
                have_yraw = True
                e_yraw.append(torch.cat([col0, eyr.float()], dim=1))

    if e_src:
        data.edge_index = torch.stack([torch.cat(e_src), torch.cat(e_dst)], dim=0)
        if have_attr:
            data.edge_attr = torch.cat(e_attr, dim=0)
        if have_type:
            data.edge_type = torch.cat(e_type, dim=0)
        if have_y:
            data.edge_y = torch.cat(e_y, dim=0)
        if have_yraw:
            data.edge_y_raw = torch.cat(e_yraw, dim=0)
    else:
        data.edge_index = torch.empty((2, 0), dtype=torch.long)

    if rc_src:
        data.rc_edge_index = torch.stack([torch.cat(rc_src), torch.cat(rc_dst)], dim=0)
        data.rc_edge_type = torch.cat(rc_type)
        if have_rc_y:
            data.rc_edge_y = torch.cat(rc_y, dim=0)
        if have_rc_yraw:
            data.rc_edge_y_raw = torch.cat(rc_yraw, dim=0)
    else:
        data.rc_edge_index = torch.empty((2, 0), dtype=torch.long)
        data.rc_edge_type = torch.empty((0,), dtype=torch.long)
        data.rc_edge_y = torch.zeros((0, 3), dtype=torch.float32)
        data.rc_edge_y_raw = torch.zeros((0, 3), dtype=torch.float32)

    data.feature_graph_key = getattr(h, "feature_graph_key", "")
    gf = getattr(h, "global_feat", None)
    if gf is not None:
        data.global_feat = gf
    for attr in ("x_schema", "y_schema", "y_raw_schema", "edge_schema", "rc_edge_schema"):
        v = getattr(h, attr, None)
        if v is not None:
            setattr(data, attr, v)
    return data
