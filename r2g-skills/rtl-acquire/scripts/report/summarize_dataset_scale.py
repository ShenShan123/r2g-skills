#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path

import torch


import sys

_SKILL_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SKILL_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS_DIR))
from skill_env import (
    data_path,
    default_merged_manifest,
)

MANIFEST_CSV = default_merged_manifest()
OUT_CSV = data_path("netlist_graph_corpus_scale_report.csv")
OUT_MD = data_path("netlist_graph_corpus_scale_report.md")
OUT_JSON = data_path("netlist_graph_corpus_scale_summary.json")


def size_bucket(num_nodes: int) -> str:
    if num_nodes < 1_000:
        return "small"
    if num_nodes < 10_000:
        return "medium"
    return "large"


def pct(part: int, total: int) -> str:
    if total <= 0:
        return "0.0%"
    return f"{(100.0 * part / total):.1f}%"


def safe_mean(values: list[float | int]) -> float:
    return float(statistics.fmean(values)) if values else 0.0


def safe_median(values: list[float | int]) -> float:
    return float(statistics.median(values)) if values else 0.0


def safe_p90(values: list[float | int]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, math.ceil(0.9 * len(ordered)) - 1)
    return float(ordered[idx])


def fmt_num(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.1f}"


def maybe_load_cell_stats(design_dir: Path) -> dict:
    cell_stats = design_dir / "cell_stats.json"
    if not cell_stats.exists():
        return {}
    try:
        return json.loads(cell_stats.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_graph_stats(pt_path: Path) -> tuple[int, int]:
    data = torch.load(pt_path, map_location="cpu", weights_only=False)
    num_nodes = int(data.x.shape[0]) if hasattr(data, "x") else 0
    num_edges = int(data.edge_index.shape[1]) if hasattr(data, "edge_index") else 0
    return num_nodes, num_edges


def source_family(design: str, source_group: str) -> str:
    lowered = design.lower()
    if source_group in ("orfs_nangate45_expand", "orfs_seed_designs"):
        return "orfs"
    for prefix, label in (
        ("iccad2015_", "iccad2015"),
        ("iccad2017_", "iccad2017"),
        ("iscas85_", "iscas85"),
        ("iscas89_", "iscas89"),
        ("koios_", "koios"),
    ):
        if lowered.startswith(prefix):
            return label
    return "other_external"


def main() -> None:
    argparse.ArgumentParser(
        description="Summarize node/edge scale for the configured merged graph manifest."
    ).parse_args()
    if not MANIFEST_CSV.exists():
        # First round: the merged manifest is refreshed AFTER this report.
        print(f"HINT: no merged manifest yet at {MANIFEST_CSV} — skipping scale report.")
        return
    rows = list(csv.DictReader(MANIFEST_CSV.open(encoding="utf-8")))
    per_design: list[dict[str, str | int | float]] = []

    for row in rows:
        pt_path = Path(row["pt_path"])
        if not pt_path.exists():
            continue
        num_nodes, num_edges = load_graph_stats(pt_path)
        design_dir = pt_path.parent
        stats = maybe_load_cell_stats(design_dir)
        cells = stats.get("cells", "")
        comb_cells = stats.get("comb_cells", "")
        seq_cells = stats.get("seq_cells", "")
        nets = stats.get("nets", "")
        bucket = size_bucket(num_nodes)
        per_design.append(
            {
                "design": row["design"],
                "source_group": row["source_group"],
                "source_family": source_family(row["design"], row["source_group"]),
                "top": row["top"],
                "size_bucket": bucket,
                "graph_nodes": num_nodes,
                "graph_edges": num_edges,
                "graph_avg_degree": round((2.0 * num_edges / num_nodes), 4) if num_nodes else 0.0,
                "cells": cells,
                "comb_cells": comb_cells,
                "seq_cells": seq_cells,
                "nets": nets,
                "pt_path": row["pt_path"],
                "mapped_netlist_path": row["mapped_netlist_path"],
                "source_path": row["source_path"],
            }
        )

    per_design.sort(key=lambda r: int(r["graph_nodes"]), reverse=True)

    fieldnames = [
        "design",
        "source_group",
        "source_family",
        "top",
        "size_bucket",
        "graph_nodes",
        "graph_edges",
        "graph_avg_degree",
        "cells",
        "comb_cells",
        "seq_cells",
        "nets",
        "pt_path",
        "mapped_netlist_path",
        "source_path",
    ]
    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(per_design)

    total = len(per_design)
    bucket_counts = Counter(str(r["size_bucket"]) for r in per_design)
    family_counts = Counter(str(r["source_family"]) for r in per_design)
    source_group_counts = Counter(str(r["source_group"]) for r in per_design)
    bucket_by_source_group: dict[str, Counter] = defaultdict(Counter)
    for row in per_design:
        bucket_by_source_group[str(row["source_group"])][str(row["size_bucket"])] += 1

    node_values = [int(r["graph_nodes"]) for r in per_design]
    edge_values = [int(r["graph_edges"]) for r in per_design]
    cell_values = [int(r["cells"]) for r in per_design if str(r["cells"]).isdigit()]
    net_values = [int(r["nets"]) for r in per_design if str(r["nets"]).isdigit()]

    summary = {
        "total_designs": total,
        "size_bucket_thresholds": {
            "small": "graph_nodes < 1000",
            "medium": "1000 <= graph_nodes < 10000",
            "large": "graph_nodes >= 10000",
        },
        "size_bucket_counts": dict(bucket_counts),
        "source_group_counts": dict(source_group_counts),
        "source_family_counts": dict(family_counts),
        "graph_nodes": {
            "min": min(node_values) if node_values else 0,
            "median": safe_median(node_values),
            "mean": safe_mean(node_values),
            "p90": safe_p90(node_values),
            "max": max(node_values) if node_values else 0,
        },
        "graph_edges": {
            "min": min(edge_values) if edge_values else 0,
            "median": safe_median(edge_values),
            "mean": safe_mean(edge_values),
            "p90": safe_p90(edge_values),
            "max": max(edge_values) if edge_values else 0,
        },
        "cells": {
            "count_with_stats": len(cell_values),
            "min": min(cell_values) if cell_values else 0,
            "median": safe_median(cell_values),
            "mean": safe_mean(cell_values),
            "p90": safe_p90(cell_values),
            "max": max(cell_values) if cell_values else 0,
        },
        "nets": {
            "count_with_stats": len(net_values),
            "min": min(net_values) if net_values else 0,
            "median": safe_median(net_values),
            "mean": safe_mean(net_values),
            "p90": safe_p90(net_values),
            "max": max(net_values) if net_values else 0,
        },
        "largest_designs": [
            {
                "design": str(r["design"]),
                "source_group": str(r["source_group"]),
                "graph_nodes": int(r["graph_nodes"]),
                "graph_edges": int(r["graph_edges"]),
                "cells": int(r["cells"]) if str(r["cells"]).isdigit() else None,
                "nets": int(r["nets"]) if str(r["nets"]).isdigit() else None,
            }
            for r in per_design[:10]
        ],
    }
    OUT_JSON.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    with OUT_MD.open("w", encoding="utf-8") as f:
        f.write("# Expanded Raw Graph Dataset Size Report\n\n")
        f.write("This report is generated from the merged success manifest and actual `.pt` graph files.\n\n")
        f.write("## Overview\n\n")
        f.write(f"- total_designs: {total}\n")
        f.write("- size_bucket_rule:\n")
        f.write("  - small: graph_nodes < 1000\n")
        f.write("  - medium: 1000 <= graph_nodes < 10000\n")
        f.write("  - large: graph_nodes >= 10000\n")
        f.write(f"- report_csv: {OUT_CSV}\n")
        f.write(f"- report_json: {OUT_JSON}\n")
        f.write(f"- manifest_csv: {MANIFEST_CSV}\n\n")

        f.write("## Size Bucket Summary\n\n")
        f.write("| bucket | count | share |\n")
        f.write("|---|---:|---:|\n")
        for bucket in ("small", "medium", "large"):
            count = bucket_counts.get(bucket, 0)
            f.write(f"| {bucket} | {count} | {pct(count, total)} |\n")

        f.write("\n## Source Group x Size Bucket\n\n")
        f.write("| source_group | small | medium | large | total |\n")
        f.write("|---|---:|---:|---:|---:|\n")
        for group in sorted(source_group_counts):
            counts = bucket_by_source_group[group]
            group_total = sum(counts.values())
            f.write(
                f"| {group} | {counts.get('small', 0)} | {counts.get('medium', 0)} | {counts.get('large', 0)} | {group_total} |\n"
            )

        f.write("\n## Source Family Counts\n\n")
        f.write("| source_family | count |\n")
        f.write("|---|---:|\n")
        for family, count in sorted(family_counts.items()):
            f.write(f"| {family} | {count} |\n")

        f.write("\n## Graph Statistics\n\n")
        f.write("| metric | min | median | mean | p90 | max |\n")
        f.write("|---|---:|---:|---:|---:|---:|\n")
        f.write(
            f"| graph_nodes | {fmt_num(summary['graph_nodes']['min'])} | {fmt_num(summary['graph_nodes']['median'])} | {fmt_num(summary['graph_nodes']['mean'])} | {fmt_num(summary['graph_nodes']['p90'])} | {fmt_num(summary['graph_nodes']['max'])} |\n"
        )
        f.write(
            f"| graph_edges | {fmt_num(summary['graph_edges']['min'])} | {fmt_num(summary['graph_edges']['median'])} | {fmt_num(summary['graph_edges']['mean'])} | {fmt_num(summary['graph_edges']['p90'])} | {fmt_num(summary['graph_edges']['max'])} |\n"
        )
        if cell_values:
            f.write(
                f"| cells | {fmt_num(summary['cells']['min'])} | {fmt_num(summary['cells']['median'])} | {fmt_num(summary['cells']['mean'])} | {fmt_num(summary['cells']['p90'])} | {fmt_num(summary['cells']['max'])} |\n"
            )
        if net_values:
            f.write(
                f"| nets | {fmt_num(summary['nets']['min'])} | {fmt_num(summary['nets']['median'])} | {fmt_num(summary['nets']['mean'])} | {fmt_num(summary['nets']['p90'])} | {fmt_num(summary['nets']['max'])} |\n"
            )

        f.write("\n## Top 10 Largest Designs\n\n")
        f.write("| rank | design | source_group | graph_nodes | graph_edges | cells | nets |\n")
        f.write("|---:|---|---|---:|---:|---:|---:|\n")
        for idx, row in enumerate(summary["largest_designs"], start=1):
            cells = "" if row["cells"] is None else str(row["cells"])
            nets = "" if row["nets"] is None else str(row["nets"])
            f.write(
                f"| {idx} | {row['design']} | {row['source_group']} | {row['graph_nodes']} | {row['graph_edges']} | {cells} | {nets} |\n"
            )

    print(f"wrote {OUT_CSV}")
    print(f"wrote {OUT_MD}")
    print(f"wrote {OUT_JSON}")
    print(f"dataset_total_designs {total}")


if __name__ == "__main__":
    main()
