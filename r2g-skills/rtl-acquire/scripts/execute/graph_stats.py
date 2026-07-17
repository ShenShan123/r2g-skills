#!/usr/bin/env python3
"""Compute cell_stats.json for one expanded design from its netlist_graph.pt.

Runs under $R2G_GRAPH_PYTHON (the torch venv) — the same interpreter the graph
conversion itself needs, so this never adds a dependency the stage didn't have.

Sources, in convergence order:
  * node/edge counts + degree/label-entropy quality metrics: the .pt itself
    (netlist_graph.py stores x[N,1]=cell_type_id for cells, -1 for nets).
  * comb/seq split: def-graph's techlib.liberty (a cell is sequential iff its
    liberty master has a clock pin) — the SAME parser both dataset stages use.
    Falls back to a documented master-name heuristic when liberty is not
    resolvable; cell_stats.json records which source was used.

Usage: graph_stats.py --pt <netlist_graph.pt> --netlist <mapped.v> --out <cell_stats.json>
Env:   R2G_SC_LIB_FILES (optional, for the liberty-based seq split)
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path

import torch

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_SCRIPTS = SCRIPT_DIR.parent
if str(SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SKILL_SCRIPTS))

from skill_env import def_graph_dir  # noqa: E402

# ORFS yosys netlist instance: `MASTER instname ( .port(net), ... );`
INSTANCE_RE = re.compile(r"^\s*(\\\S+|[A-Za-z_][\w$]*)\s+(\\\S+|[A-Za-z_][\w$.\[\]]*)\s*\(", re.M)
NON_INSTANCE_KEYWORDS = {
    "module", "endmodule", "input", "output", "inout", "wire", "reg", "logic",
    "assign", "parameter", "localparam", "specify", "endspecify",
}
# Fallback when liberty is unavailable: canonical sequential-master prefixes
# (nangate45 DFF*/SDFF*/DLH/DLL/CLKGATE; generic FF/latch spellings elsewhere).
SEQ_NAME_RE = re.compile(r"^(S?DFF|DLH|DLL|CLKGATE|LATCH|.*_FF)", re.I)


def _load_seq_masters():
    """Return (seq_norm_keys, normalizer, source). Sequential iff liberty clock pin."""
    lib_files = (os.environ.get("R2G_SC_LIB_FILES") or "").split()
    if not lib_files:
        return set(), None, "name_heuristic"
    extract_dir = def_graph_dir() / "scripts" / "extract"
    if str(extract_dir) not in sys.path:
        sys.path.insert(0, str(extract_dir))
    try:
        from techlib.liberty import load_liberty_db, norm_cell_key  # noqa: PLC0415
        db = load_liberty_db(lib_files)
    except Exception:
        return set(), None, "name_heuristic"
    seq = set()
    for key, info in (db.get("cells") or {}).items():
        pins = info.get("pins", {}) if isinstance(info, dict) else {}
        if any(isinstance(p, dict) and p.get("clock") for p in pins.values()):
            seq.add(key)
    return seq, norm_cell_key, ("liberty" if seq else "name_heuristic")


def _count_instances(netlist: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    text = netlist.read_text(encoding="utf-8", errors="ignore")
    for m in INSTANCE_RE.finditer(text):
        master = m.group(1)
        if master.lower() in NON_INSTANCE_KEYWORDS:
            continue
        counts[master] = counts.get(master, 0) + 1
    return counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pt", type=Path, required=True)
    ap.add_argument("--netlist", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    data = torch.load(args.pt, weights_only=False)
    x = data.x.reshape(-1)
    cells = int((x >= 0).sum().item())
    nets = int((x < 0).sum().item())
    num_nodes = int(data.num_nodes or 0)
    edge_index = data.edge_index
    stats: dict[str, object] = {
        "cells": cells,
        "nets": nets,
        "graph_file": args.pt.name,
        "graph_schema_version": "netlist_graph_v1",
    }

    seq_masters, normalizer, seq_source = _load_seq_masters()
    inst_counts = _count_instances(args.netlist)
    if seq_source == "liberty" and normalizer is not None:
        seq_cells = sum(n for m, n in inst_counts.items() if normalizer(m) in seq_masters)
    else:
        seq_cells = sum(n for m, n in inst_counts.items() if SEQ_NAME_RE.match(m))
    total_insts = sum(inst_counts.values())
    stats["seq_cells"] = seq_cells
    stats["comb_cells"] = max(0, total_insts - seq_cells)
    stats["seq_split_source"] = seq_source
    # Producer/consumer contract (2026-07-16 full-pipeline issue 11): the quality
    # scorer's entropy/unique-types/rare-share/redundancy metrics all read
    # cell_histogram — which was computed here (inst_counts) but never EMITTED,
    # so every design silently scored zero entropy/redundancy and redundant
    # designs mis-scored as keep. Emit the per-master histogram.
    stats["cell_histogram"] = {str(k): int(v) for k, v in sorted(inst_counts.items())}

    # Degree/label-entropy quality metrics (ported from the source skill's
    # compute_graph_quality_metrics; consumed by report/score_design_quality.py).
    if num_nodes > 0 and edge_index is not None:
        deg = torch.bincount(edge_index.reshape(-1), minlength=num_nodes).float()
        avg_degree = float(deg.mean().item())
        degree_std = float(deg.std(unbiased=False).item()) if num_nodes > 1 else 0.0
        degree_cv = degree_std / avg_degree if avg_degree > 0 else 0.0
        positive = deg[deg > 0]
        if positive.numel() > 0:
            probs = positive / positive.sum()
            degree_entropy = float((-(probs * probs.log2())).sum().item())
        else:
            degree_entropy = 0.0
        gate_entropy, dominant_share, unique_labels = 0.0, 1.0, 0
        labels = x.to(torch.int64)
        gate_labels = labels[labels >= 0]
        if gate_labels.numel() > 0:
            unique, counts = torch.unique(gate_labels, return_counts=True)
            probs = counts.float() / counts.sum().float()
            gate_entropy = float((-(probs * probs.log2())).sum().item())
            dominant_share = float(probs.max().item())
            unique_labels = int(unique.numel())
        complexity = (
            min(gate_entropy, 6.0) * 0.35
            + min(degree_entropy, 8.0) * 0.25
            + min(degree_cv, 3.0) * 0.20
            + min(math.log2(max(unique_labels, 1)), 6.0) * 0.20
        )
        stats.update(
            {
                "graph_avg_degree": round(avg_degree, 4),
                "graph_degree_cv": round(degree_cv, 4),
                "graph_degree_entropy": round(degree_entropy, 4),
                "graph_gate_label_entropy": round(gate_entropy, 4),
                "graph_dominant_gate_share": round(dominant_share, 4),
                "graph_unique_gate_labels": unique_labels,
                "graph_complexity_score": round(complexity, 4),
            }
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    print(json.dumps({"cells": cells, "nets": nets, "seq_cells": seq_cells}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
