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
# canonical CSVs. All four use the extractor's log-domain ``label`` column.
LABEL_SPECS = [
    {"node_type": NODE_TYPE_GATE, "order": 0, "file": "cell_congestion.csv", "column": "label"},
    {"node_type": NODE_TYPE_GATE, "order": 1, "file": "ir_drop.csv", "column": "label"},
    {"node_type": NODE_TYPE_PIN, "order": 2, "file": "timing_features.csv", "column": "label"},
    {"node_type": NODE_TYPE_NET, "order": 3, "file": "wirelength.csv", "column": "label"},
]

Y_SCHEMA_BASE = {
    "y0": "node_type",
    "y1": "congestion_label (gate)",
    "y2": "irdrop_label (gate)",
    "y3": "timing_label (pin; per-cell min pin slack -> log1p path delay)",
    "y4": "wirelength_label (net; log1p um)",
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


def build_gate_label_values(base_df, label_dfs, design_key):
    out = {}
    for spec in LABEL_SPECS:
        if spec["node_type"] != NODE_TYPE_GATE:
            continue
        df = label_dfs[spec["file"]]
        if "Design" not in df.columns:
            continue
        df = df[df["Design"] == design_key].copy()
        col = spec["column"]
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


def build_net_label_values(base_df, label_dfs, design_key):
    out = {}
    for spec in LABEL_SPECS:
        if spec["node_type"] != NODE_TYPE_NET:
            continue
        df = label_dfs[spec["file"]]
        if "Design" not in df.columns:
            continue
        df = df[df["Design"] == design_key].copy()
        col = spec["column"]
        if col not in df.columns or "Net" not in df.columns:
            continue
        m = df[["Net", col]].copy()
        m[col] = pd.to_numeric(m[col], errors="coerce")
        out[spec["order"]] = _merged_label_values(base_df, m, "Net", "net_name", col,
                                                  context=spec["file"])
    return out


def build_pin_label_values(base_df, label_dfs, design_key):
    """Pin labels join by inst/pin when the CSV has a ``Pin`` column, else by Cell
    (timing_features.csv is per-cell: every pin of a cell inherits its label)."""
    torch = _torch()
    out = {}
    for spec in LABEL_SPECS:
        if spec["node_type"] != NODE_TYPE_PIN:
            continue
        df = label_dfs[spec["file"]]
        if "Design" not in df.columns:
            continue
        df = df[df["Design"] == design_key].copy()
        col = spec["column"]
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


def build_directed_edges(base_src, base_dst, base_attr, base_y, base_type):
    """Duplicate every undirected base edge into both directions, repeating
    edge_attr / edge_type / edge_y rows pairwise.

    Edge columns are INTERLEAVED — [fwd0, rev0, fwd1, rev1, ...] — so the
    pairwise-repeated attr/type/y rows align with their edges. (The RTL2Graph
    originals concatenated [all forwards | all reverses] while still repeating
    attrs pairwise, misaligning edge_attr/edge_type/edge_y with edge_index for
    every edge past the first — bug #5 of the 2026-07-05 audit, fixed here.)
    """
    torch = _torch()
    if not base_src:
        width = int(base_attr.shape[1]) if base_attr.ndim == 2 else 0
        return (
            torch.empty((2, 0), dtype=torch.long),
            torch.zeros((0, width), dtype=torch.float32),
            torch.empty((0,), dtype=torch.long),
            torch.zeros((0, 5), dtype=torch.float32),
        )
    src = torch.tensor(base_src, dtype=torch.long)
    dst = torch.tensor(base_dst, dtype=torch.long)
    fwd = torch.stack([src, dst], dim=0)
    rev = torch.stack([dst, src], dim=0)
    edge_index = torch.stack([fwd, rev], dim=2).reshape(2, -1)
    edge_attr = torch.repeat_interleave(base_attr, 2, dim=0)
    edge_type = torch.repeat_interleave(base_type, 2)
    edge_y = torch.repeat_interleave(base_y, 2, dim=0)
    return edge_index, edge_attr, edge_type, edge_y


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
