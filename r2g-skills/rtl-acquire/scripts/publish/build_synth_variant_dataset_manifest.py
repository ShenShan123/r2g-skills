#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


import sys

_SKILL_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SKILL_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS_DIR))
from skill_env import (
    data_path,
    default_out_root,
    default_seed_root,
)

DEFAULT_CANONICAL_ROOT = default_out_root()
DEFAULT_VARIANT_ROOT = data_path("corpus_area_variant")
DEFAULT_ORFS_ROOT = default_seed_root()
DEFAULT_ORFS_VARIANT_ROOT = data_path("orfs_seed_designs_area_variant")
DEFAULT_OUT_CSV = data_path("netlist_graph_synth_variant_manifest.csv")
DEFAULT_OUT_MD = data_path("netlist_graph_synth_variant_manifest.md")


def design_pt(design_dir: Path, design: str) -> Path | None:
    for name in ("netlist_graph.pt", f"{design}_1_1_yosys.pt"):
        p = design_dir / name
        if p.exists():
            return p
    return None


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def success_rows(index_path: Path) -> list[dict[str, str]]:
    if not index_path.exists():
        return []
    return [row for row in csv.DictReader(index_path.open(encoding="utf-8")) if row.get("status") == "success"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--canonical-root", type=Path, default=DEFAULT_CANONICAL_ROOT)
    parser.add_argument("--variant-root", type=Path, default=DEFAULT_VARIANT_ROOT)
    parser.add_argument("--orfs-root", type=Path, default=DEFAULT_ORFS_ROOT)
    parser.add_argument("--orfs-variant-root", type=Path, default=DEFAULT_ORFS_VARIANT_ROOT)
    parser.add_argument("--out-csv", type=Path, default=DEFAULT_OUT_CSV)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_OUT_MD)
    args = parser.parse_args()

    rows: list[dict[str, str]] = []
    sections = [
        (args.canonical_root, "external_corpus", "yosys_abc_area0"),
        (args.orfs_root, "orfs_seed_designs", "yosys_abc_area0"),
        (args.variant_root, "external_corpus_area", "yosys_abc_area1"),
        (args.orfs_variant_root, "orfs_seed_designs_area", "yosys_abc_area1"),
    ]
    for root, group, default_variant in sections:
        for row in success_rows(root / "index.csv"):
            design_dir = root / row["design"]
            meta = load_json(design_dir / "design_meta.json")
            pt_path = design_pt(design_dir, row["design"])
            mapped_path = design_dir / "mapped_netlist.v"
            if pt_path is None:
                continue
            rows.append(
                {
                    "design": row["design"],
                    "source_group": group,
                    "synth_variant": str(meta.get("synth_variant") or default_variant),
                    "status": "success",
                    "top": row.get("top", ""),
                    "pt_path": str(pt_path),
                    "mapped_netlist_path": str(mapped_path),
                    "source_path": row.get("source_path", ""),
                    "graph_format": (row.get("graph_format", "")
                                     or str(meta.get("graph_schema_version", ""))
                                     or "netlist_graph_v1"),
                    "notes": row.get("notes", ""),
                }
            )

    rows.sort(key=lambda r: (r["source_group"], r["design"].lower()))
    fieldnames = [
        "design",
        "source_group",
        "synth_variant",
        "status",
        "top",
        "pt_path",
        "mapped_netlist_path",
        "source_path",
        "graph_format",
        "notes",
    ]
    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    counts = {}
    for row in rows:
        counts[row["source_group"]] = counts.get(row["source_group"], 0) + 1

    with args.out_md.open("w", encoding="utf-8") as fh:
        fh.write("# Synth Variant Dataset Manifest\n\n")
        fh.write(f"- total_success_samples: {len(rows)}\n")
        for key in sorted(counts):
            fh.write(f"- {key}: {counts[key]}\n")
        fh.write(f"- manifest_csv: {args.out_csv}\n")
        fh.write(f"- canonical_root: {args.canonical_root}\n")
        fh.write(f"- variant_root: {args.variant_root}\n")
        fh.write(f"- orfs_root: {args.orfs_root}\n")
        fh.write(f"- orfs_variant_root: {args.orfs_variant_root}\n")

    print(f"wrote {args.out_csv}")
    print(f"wrote {args.out_md}")
    print(f"merged_success_samples {len(rows)}")


if __name__ == "__main__":
    main()
