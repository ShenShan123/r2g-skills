#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from itertools import combinations
from pathlib import Path


import sys

_SKILL_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SKILL_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS_DIR))
from skill_env import (
    default_out_root,
    out_root_path,
    workspace_path,
)

INDEX = out_root_path("index.csv")
ROOT = default_out_root()
OUT_CSV = workspace_path("audits/near_duplicate_audit_2026-04-07.csv")
OUT_JSON = workspace_path("audits/near_duplicate_audit_summary_2026-04-07.json")


def canonical_source_identity(path_text: str) -> str:
    normalized = str(Path(path_text).resolve()).replace("\\", "/")
    for marker in ("hdl-benchmarks-min/", "vtr-verilog-to-routing-min/"):
        if marker in normalized:
            return normalized.split(marker, 1)[1]
    return normalized


def normalize_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def source_stem(path_text: str) -> str:
    return normalize_text(Path(path_text).stem)


def load_rows() -> list[dict[str, str]]:
    rows = [r for r in csv.DictReader(INDEX.open()) if r["status"] == "success"]
    enriched = []
    for row in rows:
        ddir = ROOT / row["design"]
        meta = {}
        meta_path = ddir / "design_meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
            except Exception:
                meta = {}
        enriched.append(
            {
                **row,
                "canonical_source": canonical_source_identity(row.get("source_path", "") or ""),
                "source_stem": source_stem(row.get("source_path", "") or row["design"]),
                "norm_design": normalize_text(row["design"]),
            }
        )
    return enriched


def same_counts(a: dict[str, str], b: dict[str, str]) -> bool:
    return all(
        (a.get(k, ""), b.get(k, ""))[0] == (a.get(k, ""), b.get(k, ""))[1]
        for k in ("cells", "comb_cells", "seq_cells", "nets")
    )


def strong_near_duplicate(a: dict[str, str], b: dict[str, str]) -> tuple[bool, str]:
    if a["canonical_source"] and a["canonical_source"] == b["canonical_source"]:
        return True, "exact_source_duplicate"
    if a["top"] == b["top"] and same_counts(a, b):
        if a["source_stem"] == b["source_stem"]:
            return True, "strong_near_same_top_counts_same_stem"
        if a["norm_design"] == b["norm_design"]:
            return True, "strong_near_same_top_counts_same_design_norm"
    return False, ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit exact and strong near-duplicate external benchmark samples.")
    parser.add_argument("--out-csv", type=Path, default=OUT_CSV)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    args = parser.parse_args()

    rows = load_rows()
    out_rows: list[dict[str, str]] = []
    summary = defaultdict(int)
    seen_pairs: set[tuple[str, str, str]] = set()

    by_top = defaultdict(list)
    for row in rows:
        by_top[row["top"]].append(row)

    for top_rows in by_top.values():
        for a, b in combinations(top_rows, 2):
            is_dup, kind = strong_near_duplicate(a, b)
            if not is_dup:
                continue
            pair_key = tuple(sorted((a["design"], b["design"]))) + (kind,)
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            summary[kind] += 1
            out_rows.append(
                {
                    "duplicate_kind": kind,
                    "design_a": a["design"],
                    "design_b": b["design"],
                    "top": a["top"],
                    "cells": a["cells"],
                    "comb_cells": a["comb_cells"],
                    "seq_cells": a["seq_cells"],
                    "nets": a["nets"],
                    "source_a": a["source_path"],
                    "source_b": b["source_path"],
                    "canonical_source_a": a["canonical_source"],
                    "canonical_source_b": b["canonical_source"],
                }
            )

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "duplicate_kind",
                "design_a",
                "design_b",
                "top",
                "cells",
                "comb_cells",
                "seq_cells",
                "nets",
                "source_a",
                "source_b",
                "canonical_source_a",
                "canonical_source_b",
            ],
        )
        writer.writeheader()
        writer.writerows(sorted(out_rows, key=lambda r: (r["duplicate_kind"], r["top"], r["design_a"], r["design_b"])))

    payload = {
        "success_rows_scanned": len(rows),
        "pair_count": len(out_rows),
        "kind_counts": dict(sorted(summary.items())),
    }
    args.out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote {args.out_csv} rows={len(out_rows)}")
    print(f"wrote {args.out_json}")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
