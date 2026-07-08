#!/usr/bin/env python3
"""Synthesized-Verilog netlist -> bipartite cell/net PyG graph (.pt).

Port of RTL2Graph ``base_garph`` (verilog_to_net.py + net_to_pt.py), verified
2026-07-05 against OpenDB ground truth on cordic nangate45 (cells/nets counts
exact; per-net connectivity exact on a 26-net/26-inst sample). Differences vs
the original:

  * cell_type_id comes from ``techlib.cell_types.resolve_cell_type_map`` — the
    same deterministic per-platform vocabulary the feature stage uses — instead
    of the original's hardcoded nangate45 map + per-PROCESS dynamic ids (which
    assigned the same id to different cells across designs, breaking corpus
    consistency; sky130 cells all collapsed to UNKNOWN=95 there).
  * cell/net names are stored on the Data object (``cell_names``/``net_names``)
    so rows join back to DEF-side artifacts (names here are the Verilog
    unescaped form; strip backslashes on both sides to join with DEF names).

Graph shape (kept from the original): nodes = sorted cells then sorted nets;
x[N,1] = cell_type_id for cells, -1 for nets; undirected cell-net edges (both
directions), deduplicated per (cell, net) pair.

Parser scope: ORFS yosys mapped netlists (one statement per ';', named port
connections). Constants inside concatenations (``{1'b0, sig}``) would surface
as fake nets — ORFS netlists tie constants through LOGIC0/LOGIC1 cells so this
does not occur in practice (verified: zero literal-constant connections across
the corpus netlists sampled).

Usage: netlist_graph.py <yosys.v> <out.pt> [design_name]
Env:   R2G_PLATFORM, R2G_SC_LIB_FILES (cell-type vocabulary inputs)
"""
from __future__ import annotations

import os
import re
import sys
from collections import defaultdict

_EXTRACT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _EXTRACT_DIR not in sys.path:
    sys.path.insert(0, _EXTRACT_DIR)

from techlib.cell_types import cell_type_id, resolve_cell_type_map  # noqa: E402
from techlib.liberty import load_liberty_db  # noqa: E402

ESCAPED_OR_PLAIN_ID_RE = re.compile(r"\\\S+|[A-Za-z_$][A-Za-z0-9_$.\[\]]*")
CONST_RE = re.compile(r"\d+'[bhdBHD][0-9a-fA-FxXzZ]+|[01]")
# Sized Verilog constant literal (`1'b0`, `8'hFF`, `4'd3`, `2'o1`), tolerant of
# spaces/underscores. Stripped from a concatenation before tokenizing so a
# tie-off constant cannot leak its base fragment (`b0`) as a phantom net.
SIZED_CONST_RE = re.compile(r"\d+\s*'\s*[bBoOdDhH][0-9a-fA-FxXzZ_]+")
INSTANCE_HEADER_RE = re.compile(r"^\s*(\\\S+|[^\s(]+)\s+(\\\S+|[^\s(]+)\s*\((.*)\)\s*$", re.DOTALL)

SKIP_STATEMENT_PREFIXES = {
    "module", "endmodule", "input", "output", "inout", "wire", "reg", "logic",
    "tri", "supply0", "supply1", "assign", "always", "initial", "function",
    "task", "generate", "if", "for", "case", "parameter", "localparam", "`define",
}


def normalize_constant(token):
    token = token.strip().lower().replace(" ", "")
    if token in {"1'b0", "1'h0", "1'd0", "0"}:
        return "CONST0"
    if token in {"1'b1", "1'h1", "1'd1", "1"}:
        return "CONST1"
    return token


def extract_signal_names(expr):
    expr = expr.strip()
    if not expr:
        return []
    if CONST_RE.fullmatch(expr):
        return [normalize_constant(expr)]
    # A concatenation may mix tie-off constants with real signals
    # (`{1'b0, sig}`) — drop the constants so their base fragment (`b0`) is not
    # tokenized as a phantom net. ORFS mapped netlists tie constants through
    # LOGIC0/LOGIC1 cells so this path is defensive (verified: zero literal
    # constants across the sampled corpus netlists), but a hand-written or
    # non-ORFS netlist could otherwise inject fake nets (2026-07-06 audit).
    scan = SIZED_CONST_RE.sub(" ", expr)
    names, seen = [], set()
    for match in ESCAPED_OR_PLAIN_ID_RE.finditer(scan):
        token = match.group(0).strip()
        if token and token not in seen:
            seen.add(token)
            names.append(token)
    if names:
        return names
    return ["EXPR:" + re.sub(r"\s+", "", expr)]


def iter_statements(text):
    buf = []
    for line in text.splitlines():
        if not line.strip():
            continue
        buf.append(line)
        if ";" in line:
            yield "\n".join(buf)
            buf = []


def parse_named_connections(body):
    idx, length = 0, len(body)
    while idx < length:
        if body[idx] != ".":
            idx += 1
            continue
        idx += 1
        pin_start = idx
        while idx < length and body[idx] not in " \t\r\n(":
            idx += 1
        pin_name = body[pin_start:idx].strip()
        while idx < length and body[idx].isspace():
            idx += 1
        if idx >= length or body[idx] != "(":
            continue
        idx += 1
        expr_start = idx
        depth = 1
        while idx < length and depth > 0:
            ch = body[idx]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            idx += 1
        expr = body[expr_start:idx - 1]
        if pin_name:
            yield pin_name, expr


def parse_verilog(verilog_file):
    """Parse a synthesized (mapped) Verilog netlist.

    Returns (cells: {inst -> cell_type}, nets: {net -> [(inst, pin), ...]}).
    """
    cells = {}
    nets = defaultdict(list)
    with open(verilog_file, "r") as f:
        text = f.read()
    text = re.sub(r"//.*?\n", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"\(\*.*?\*\)", "", text, flags=re.DOTALL)

    for statement in iter_statements(text):
        stmt = statement.strip().rstrip(";").strip()
        if not stmt:
            continue
        head = stmt.split(None, 1)[0]
        if head in SKIP_STATEMENT_PREFIXES:
            continue
        match = INSTANCE_HEADER_RE.match(stmt)
        if not match:
            continue
        cell_type, inst_name, body = match.groups()
        cells[inst_name] = cell_type
        for pin_name, expr in parse_named_connections(body):
            for net_name in extract_signal_names(expr):
                nets[net_name].append((inst_name, pin_name))
    return cells, nets


def build_graph(cells, nets, type_map):
    import torch
    from torch_geometric.data import Data

    cell_nodes = set(cells.keys())
    for conns in nets.values():
        for inst, _ in conns:
            cell_nodes.add(inst)
    cell_names = sorted(cell_nodes)
    net_names = sorted(nets.keys())
    cell_id = {c: i for i, c in enumerate(cell_names)}
    net_id = {n: i + len(cell_names) for i, n in enumerate(net_names)}

    edge_pairs = set()
    for net, conns in nets.items():
        nid = net_id[net]
        for inst, _ in conns:
            cid = cell_id[inst]
            edge_pairs.add((cid, nid))
            edge_pairs.add((nid, cid))
    edge_index = (torch.tensor(sorted(edge_pairs), dtype=torch.long).t().contiguous()
                  if edge_pairs else torch.empty((2, 0), dtype=torch.long))

    x = torch.full((len(cell_names) + len(net_names), 1), -1.0, dtype=torch.float)
    for inst, idx in cell_id.items():
        x[idx][0] = float(cell_type_id(cells.get(inst, "UNKNOWN"), type_map))

    data = Data(x=x, edge_index=edge_index)
    data.cell_names = cell_names
    data.net_names = net_names
    data.x_schema = {"x0": "cell_type_id (net nodes = -1)"}
    return data


def main():
    if len(sys.argv) < 3:
        print(f"usage: {os.path.basename(__file__)} <yosys.v> <out.pt> [design_name]", file=sys.stderr)
        sys.exit(1)
    verilog, out_pt = sys.argv[1], sys.argv[2]
    design = sys.argv[3] if len(sys.argv) > 3 else os.path.splitext(os.path.basename(verilog))[0]

    platform = os.environ.get("R2G_PLATFORM", "nangate45")
    # Match the feature stage (nodes_gate.py): build the cell-type map from the FULL
    # liberty (std + per-design macro libs) but key the id space on the STD-CELL-ONLY
    # subset. That way macros collapse to the shared MACRO id (they are known nodes,
    # not UNKNOWN) instead of being interleaved into the sorted std vocabulary — which
    # would drift std-cell ids off the b-f feature graphs on macro designs. Loading
    # lib_db from R2G_SC_LIB_FILES alone dropped every macro cell (-> UNKNOWN); using
    # R2G_SC_LIB_FILES as BOTH source and subset interleaved them. Both are wrong.
    # (failure-patterns.md "Dataset-Extraction Silent-Value Defects" #12/#19)
    lib_files = os.environ.get("R2G_LIB_FILES", "")
    sc_libs = os.environ.get("R2G_SC_LIB_FILES", "")
    lib_src = lib_files.strip() or sc_libs.strip()   # full lib when available
    lib_db = load_liberty_db(lib_src) if lib_src else {"cells": {}}
    sc_lib_list = [t for t in (sc_libs or lib_files).replace(":", " ").split() if t]
    type_map = resolve_cell_type_map(platform, lib_db, sc_lib_list or None)

    print(f"[netlist_graph] parsing {verilog}")
    cells, nets = parse_verilog(verilog)
    print(f"[netlist_graph] cells={len(cells)} nets={len(nets)}")

    import torch

    data = build_graph(cells, nets, type_map)
    data.design = design
    os.makedirs(os.path.dirname(os.path.abspath(out_pt)), exist_ok=True)
    torch.save(data, out_pt)
    print(f"[netlist_graph] nodes={data.num_nodes} edges={data.edge_index.size(1)} -> {out_pt}")


if __name__ == "__main__":
    main()
