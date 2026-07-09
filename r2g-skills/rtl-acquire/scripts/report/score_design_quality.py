#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path


import sys

_SKILL_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SKILL_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS_DIR))
from skill_env import (
    data_path,
    default_out_root,
    workspace_path,
)

DEFAULT_MANIFEST = data_path("expanded_raw_graph_manifest_2026-04-02.csv")
DEFAULT_EXTERNAL_ROOT = default_out_root()
DEFAULT_EXTERNAL_INDEX = DEFAULT_EXTERNAL_ROOT / "index.csv"
DEFAULT_OUT_CSV = workspace_path("quality/design_quality_scores.csv")
DEFAULT_OUT_MD = workspace_path("quality/design_quality_scores.md")
DEFAULT_OUT_JSON = workspace_path("quality/design_quality_scores.json")


def load_manifest(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def size_bucket(cells: int) -> str:
    if cells >= 10000:
        return "large"
    if cells >= 1000:
        return "medium"
    return "small"


def entropy_from_hist(hist: dict[str, int]) -> float:
    total = sum(hist.values())
    if total <= 0:
        return 0.0
    acc = 0.0
    for count in hist.values():
        p = count / total
        if p > 0:
            acc -= p * math.log2(p)
    return acc


def dominant_share(hist: dict[str, int]) -> float:
    total = sum(hist.values())
    if total <= 0:
        return 1.0
    return max(hist.values()) / total


def derived_complexity_score(
    *,
    bucket: str,
    cell_entropy: float,
    dominant_gate_share: float,
    unique_types: int,
) -> float:
    size_bonus = 0.6 if bucket == "large" else 0.3 if bucket == "medium" else 0.0
    score = (
        0.45 * min(cell_entropy, 6.0)
        + 0.03 * min(unique_types, 64)
        + 1.5 * max(0.0, 1.0 - dominant_gate_share)
        + size_bonus
    )
    return round(min(score, 6.0), 4)


def cosine_similarity(a: dict[str, int], b: dict[str, int]) -> float:
    if not a or not b:
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for key, value in a.items():
        norm_a += value * value
        if key in b:
            dot += value * b[key]
    for value in b.values():
        norm_b += value * value
    if norm_a <= 0 or norm_b <= 0:
        return 0.0
    return float(dot / math.sqrt(norm_a * norm_b))


def decide_design_action(score: float, bucket: str, low_fidelity: bool, fix_ratio: float) -> str:
    if low_fidelity or fix_ratio >= 0.3:
        return "reject"
    if bucket == "large" and score >= 0.2:
        return "keep"
    if bucket == "large" and score >= 0.08:
        return "conditional"
    if score >= 0.35:
        return "keep"
    if score >= 0.15:
        return "conditional"
    return "reject"


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute per-design quality and action labels for external successes.")
    parser.add_argument("--manifest-csv", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--external-root", type=Path, default=DEFAULT_EXTERNAL_ROOT)
    parser.add_argument("--external-index", type=Path, default=DEFAULT_EXTERNAL_INDEX)
    parser.add_argument("--out-csv", type=Path, default=DEFAULT_OUT_CSV)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_OUT_MD)
    parser.add_argument("--out-json", type=Path, default=DEFAULT_OUT_JSON)
    args = parser.parse_args()

    manifest_rows = [
        row
        for row in load_manifest(args.external_index)
        if row.get("status") == "success"
    ]

    global_hist = Counter()
    per_design_hist: dict[str, dict[str, int]] = {}
    meta_by_design: dict[str, dict] = {}
    stats_by_design: dict[str, dict] = {}
    for row in manifest_rows:
        design = row.get("design", "")
        if not design:
            continue
        stats = load_json(args.external_root / design / "cell_stats.json")
        meta = load_json(args.external_root / design / "design_meta.json")
        hist = stats.get("cell_histogram", {})
        if isinstance(hist, dict):
            hist = {str(k): int(v) for k, v in hist.items()}
        else:
            hist = {}
        per_design_hist[design] = hist
        stats_by_design[design] = stats
        meta_by_design[design] = meta
        global_hist.update(hist)

    rare_types = set()
    if global_hist:
        counts = sorted(global_hist.values())
        threshold = counts[max(0, int(len(counts) * 0.2) - 1)]
        rare_types = {cell for cell, count in global_hist.items() if count <= threshold}

    out_rows: list[dict[str, str]] = []
    action_counts = Counter()
    for row in manifest_rows:
        design = row["design"]
        stats = stats_by_design.get(design, {})
        meta = meta_by_design.get(design, {})
        hist = per_design_hist.get(design, {})
        cells = int(stats.get("cells", 0) or 0)
        bucket = str(stats.get("design_bucket") or size_bucket(cells))
        cell_entropy = float(entropy_from_hist(hist))
        dom_share = float(stats.get("graph_dominant_gate_share", dominant_share(hist)) or 0.0)
        unique_types = len(hist)
        raw_complexity = stats.get("graph_complexity_score")
        if raw_complexity is None:
            complexity = derived_complexity_score(
                bucket=bucket,
                cell_entropy=cell_entropy,
                dominant_gate_share=dom_share,
                unique_types=unique_types,
            )
        else:
            complexity = float(raw_complexity or 0.0)
        rare_share = (
            sum(v for k, v in hist.items() if k in rare_types) / cells
            if cells > 0 and hist
            else 0.0
        )
        redundancy = cosine_similarity(hist, dict(global_hist))
        fix_actions = int(stats.get("fix_actions", meta.get("fix_actions", 0)) or 0)
        fix_ratio = float(stats.get("fix_ratio", meta.get("fix_ratio", 0.0)) or 0.0)
        low_fidelity = bool(stats.get("low_fidelity", meta.get("low_fidelity", False)))
        novelty = min(1.0, rare_share * 2.0 + min(complexity / 6.0, 0.6))
        score = float(
            max(
                0.0,
                (0.45 * novelty)
                + (0.25 * max(0.0, 1.0 - dom_share))
                + (0.15 * min(cell_entropy / 6.0, 1.0))
                + (0.15 * (0.4 if bucket == "large" else 0.2 if bucket == "medium" else 0.0))
                - (0.5 * redundancy)
                - (0.5 * fix_ratio),
            )
        )
        action = decide_design_action(score, bucket, low_fidelity, fix_ratio)
        action_counts[action] += 1
        out_rows.append(
            {
                "design": design,
                "source_group": row.get("source_group", "") or "external_benchmarks_nangate45_expand",
                "cells": str(cells),
                "bucket": bucket,
                "graph_complexity_score": f"{complexity:.4f}",
                "cell_entropy": f"{cell_entropy:.4f}",
                "dominant_cell_share": f"{dom_share:.4f}",
                "unique_cell_types": str(unique_types),
                "rare_cell_share": f"{rare_share:.4f}",
                "redundancy_score": f"{redundancy:.4f}",
                "design_quality_score": f"{score:.4f}",
                "design_action": action,
                "fix_actions": str(fix_actions),
                "fix_ratio": f"{fix_ratio:.4f}",
                "low_fidelity": str(low_fidelity),
            }
        )

    out_rows.sort(key=lambda item: (-float(item["design_quality_score"]), item["design"]))
    fieldnames = [
        "design",
        "source_group",
        "cells",
        "bucket",
        "graph_complexity_score",
        "cell_entropy",
        "dominant_cell_share",
        "unique_cell_types",
        "rare_cell_share",
        "redundancy_score",
        "design_quality_score",
        "design_action",
        "fix_actions",
        "fix_ratio",
        "low_fidelity",
    ]
    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(out_rows)

    summary = {
        "count": len(out_rows),
        "action_counts": dict(action_counts),
        "largest_quality_scores": out_rows[:10],
    }
    args.out_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    with args.out_md.open("w", encoding="utf-8") as fh:
        fh.write("# Design Quality Scores\n\n")
        fh.write(f"- count: {len(out_rows)}\n")
        for action in ("keep", "conditional", "reject"):
            fh.write(f"- {action}: {action_counts.get(action, 0)}\n")
        fh.write("\n| design | bucket | score | action | complexity | dominant | low_fidelity |\n")
        fh.write("|---|---|---:|---|---:|---:|---|\n")
        for row in out_rows[:50]:
            fh.write(
                f"| {row['design']} | {row['bucket']} | {row['design_quality_score']} | {row['design_action']} | "
                f"{row['graph_complexity_score']} | {row['dominant_cell_share']} | {row['low_fidelity']} |\n"
            )

    print(f"wrote {args.out_csv}")
    print(f"wrote {args.out_md}")
    print(f"wrote {args.out_json}")


if __name__ == "__main__":
    main()
