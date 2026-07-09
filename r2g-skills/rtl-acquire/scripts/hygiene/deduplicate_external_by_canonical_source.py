#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import defaultdict
from pathlib import Path


import sys

_SKILL_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SKILL_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS_DIR))
from skill_env import (
    data_path,
    default_out_root,
)

ROOT = default_out_root()
QUARANTINE = ROOT / "_duplicate_quarantine_2026-04-07"
REPORT = data_path("orfs_nangate45_expand/external_canonical_source_cleanup_report.csv")


def canonical_source_identity(path_text: str) -> str:
    normalized = str(Path(path_text).resolve()).replace("\\", "/")
    for marker in ("hdl-benchmarks-min/", "vtr-verilog-to-routing-min/"):
        if marker in normalized:
            return normalized.split(marker, 1)[1]
    return normalized


def design_priority(design: str) -> tuple[int, int, str]:
    if design.startswith("iccad2015_"):
        return (0, len(design), design)
    if design.startswith("iccad2017_"):
        return (1, len(design), design)
    if design.startswith("iscas85_"):
        return (2, len(design), design)
    if design.startswith("iscas89_"):
        return (3, len(design), design)
    if design.startswith("koios_"):
        return (4, len(design), design)
    if design.startswith("hdl_benchmarks_min_iccad_2015_"):
        return (10, len(design), design)
    if design.startswith("hdl_benchmarks_min_iccad_2017_"):
        return (11, len(design), design)
    if design.startswith("hdl_benchmarks_min_iscas85_"):
        return (12, len(design), design)
    if design.startswith("hdl_benchmarks_min_iscas89_"):
        return (13, len(design), design)
    if design.startswith("vtr_verilog_to_routing_min_"):
        return (14, len(design), design)
    return (5, len(design), design)


def read_meta(ddir: Path) -> dict:
    meta_path = ddir / "design_meta.json"
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text())
    except Exception:
        return {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Deduplicate external benchmark directories by canonical source identity.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--quarantine", type=Path, default=QUARANTINE)
    parser.add_argument("--report", type=Path, default=REPORT)
    args = parser.parse_args()

    groups: dict[str, list[tuple[Path, dict]]] = defaultdict(list)
    for ddir in sorted(p for p in args.root.iterdir() if p.is_dir() and not p.name.startswith("_")):
        meta = read_meta(ddir)
        rtl_files = meta.get("rtl_files", [])
        if not isinstance(rtl_files, list) or not rtl_files:
            continue
        source = canonical_source_identity(str(rtl_files[0]))
        groups[source].append((ddir, meta))

    args.quarantine.mkdir(parents=True, exist_ok=True)
    removed_rows: list[dict[str, str]] = []
    for source, members in groups.items():
        if len(members) <= 1:
            continue
        ordered = sorted(members, key=lambda item: design_priority(item[0].name))
        kept_dir, kept_meta = ordered[0]
        for ddir, meta in ordered[1:]:
            dst = args.quarantine / ddir.name
            if ddir.exists() and not dst.exists():
                shutil.move(str(ddir), str(dst))
            removed_rows.append(
                {
                    "design": ddir.name,
                    "source_path": source,
                    "kept_design": kept_dir.name,
                    "top": str(meta.get("top", "")),
                    "status": str(meta.get("status", "")),
                    "duplicate_reason": f"canonical_source_duplicate_kept:{kept_dir.name}",
                }
            )

    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["design", "source_path", "kept_design", "top", "status", "duplicate_reason"],
        )
        writer.writeheader()
        writer.writerows(removed_rows)

    print(f"removed_duplicates {len(removed_rows)}")
    print(f"wrote {args.report}")


if __name__ == "__main__":
    main()
