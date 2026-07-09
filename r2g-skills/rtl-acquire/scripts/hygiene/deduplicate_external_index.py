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
    out_root_path,
)

INDEX = out_root_path("index.csv")
ROOT = default_out_root()
QUARANTINE = ROOT / "_duplicate_quarantine_2026-04-07"
REPORT = data_path("orfs_nangate45_expand/external_duplicate_cleanup_report.csv")


def design_priority(design: str) -> tuple[int, int, str]:
    if design.startswith("iccad2015_"):
        return (0, len(design), design)
    if design.startswith("iccad2017_"):
        return (1, len(design), design)
    if design.startswith("iscas85_"):
        return (2, len(design), design)
    if design.startswith("iscas89_"):
        return (3, len(design), design)
    if design.startswith("hdl_benchmarks_min_iccad_2015_"):
        return (10, len(design), design)
    if design.startswith("hdl_benchmarks_min_iccad_2017_"):
        return (11, len(design), design)
    if design.startswith("hdl_benchmarks_min_iscas85_"):
        return (12, len(design), design)
    if design.startswith("hdl_benchmarks_min_iscas89_"):
        return (13, len(design), design)
    return (50, len(design), design)


def _read_meta(ddir: Path) -> dict:
    meta_path = ddir / "design_meta.json"
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text())
    except Exception:
        return {}


def _read_cell_stats(ddir: Path) -> dict:
    stats_path = ddir / "cell_stats.json"
    if not stats_path.exists():
        return {}
    try:
        return json.loads(stats_path.read_text())
    except Exception:
        return {}


def _source_path_from_meta(meta: dict) -> str:
    rtl_files = meta.get("rtl_files", [])
    if isinstance(rtl_files, list):
        return ";".join(str(p) for p in rtl_files)
    return ""


def _rebuild_index_from_existing_dirs(
    *,
    old_rows: list[dict[str, str]],
    root: Path,
    fieldnames: list[str],
) -> list[dict[str, str]]:
    old_by_design = {row["design"]: row for row in old_rows}
    rebuilt: list[dict[str, str]] = []

    existing_dirs = sorted(
        p for p in root.iterdir() if p.is_dir() and not p.name.startswith("_")
    )
    for ddir in existing_dirs:
        design = ddir.name
        meta = _read_meta(ddir)
        stats = _read_cell_stats(ddir)
        old = old_by_design.get(design, {})
        pt_path = ddir / "netlist_graph.pt"
        if not pt_path.exists():  # legacy 30pt-era per-design name
            pt_path = ddir / f"{design}_1_1_yosys.pt"
        mapped_path = ddir / "mapped_netlist.v"
        status = "success" if pt_path.exists() and mapped_path.exists() else (meta.get("status") or old.get("status") or "synth_failed")
        row = {key: "" for key in fieldnames}
        row["design"] = design
        row["top"] = str(meta.get("top") or stats.get("top_module") or old.get("top", ""))
        row["status"] = status
        row["cells"] = str(stats.get("cells", old.get("cells", "")))
        row["comb_cells"] = str(stats.get("comb_cells", old.get("comb_cells", "")))
        row["seq_cells"] = str(stats.get("seq_cells", old.get("seq_cells", "")))
        row["nets"] = str(stats.get("nets", old.get("nets", "")))
        row["source_path"] = _source_path_from_meta(meta) or old.get("source_path", "")
        row["graph_format"] = str(meta.get("graph_schema_version") or old.get("graph_format", ""))
        row["duplicate_reason"] = str(meta.get("duplicate_reason") or old.get("duplicate_reason", ""))
        row["notes"] = str(meta.get("notes") or old.get("notes", ""))
        rebuilt.append(row)

    existing_designs = {row["design"] for row in rebuilt}
    for row in old_rows:
        if row["design"] in existing_designs:
            continue
        if row.get("status") == "success":
            # Success rows must correspond to an existing kept directory.
            continue
        rebuilt.append({key: row.get(key, "") for key in fieldnames})

    rebuilt.sort(key=lambda r: (r["status"] != "success", r["design"].lower()))
    return rebuilt


def _existing_dir_source_groups(root: Path) -> dict[str, list[Path]]:
    by_source: dict[str, list[Path]] = defaultdict(list)
    for ddir in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("_")):
        meta = _read_meta(ddir)
        source_path = _source_path_from_meta(meta) or f"__nosource__::{ddir.name}"
        by_source[source_path].append(ddir)
    return by_source


def main() -> None:
    parser = argparse.ArgumentParser(description="Deduplicate external benchmark rows by source_path and quarantine duplicate directories.")
    parser.add_argument("--index", type=Path, default=INDEX)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--quarantine", type=Path, default=QUARANTINE)
    parser.add_argument("--report", type=Path, default=REPORT)
    args = parser.parse_args()

    rows = list(csv.DictReader(args.index.open()))
    fieldnames = list(rows[0].keys())
    by_source: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        source_path = (row.get("source_path") or "").strip()
        if not source_path:
            by_source[f"__nosource__::{row['design']}"].append(row)
        else:
            by_source[source_path].append(row)

    kept_rows: list[dict[str, str]] = []
    removed_rows: list[dict[str, str]] = []
    for source_path, group in by_source.items():
        if len(group) == 1:
            kept_rows.extend(group)
            continue
        ordered = sorted(group, key=lambda r: design_priority(r["design"]))
        kept = ordered[0]
        kept_rows.append(kept)
        for row in ordered[1:]:
            row = dict(row)
            row["duplicate_reason"] = f"duplicate_source_path_kept:{kept['design']}"
            removed_rows.append(row)

    args.quarantine.mkdir(parents=True, exist_ok=True)
    for row in removed_rows:
        design = row["design"]
        src = args.root / design
        dst = args.quarantine / design
        if src.exists() and not dst.exists():
            shutil.move(str(src), str(dst))

    # Also deduplicate directories directly from the currently existing output tree.
    dir_removed_rows: list[dict[str, str]] = []
    for source_path, group in _existing_dir_source_groups(args.root).items():
        if len(group) <= 1:
            continue
        ordered = sorted(group, key=lambda p: design_priority(p.name))
        kept_dir = ordered[0]
        for ddir in ordered[1:]:
            dst = args.quarantine / ddir.name
            if ddir.exists() and not dst.exists():
                shutil.move(str(ddir), str(dst))
            dir_removed_rows.append(
                {
                    "design": ddir.name,
                    "source_path": source_path if not source_path.startswith("__nosource__::") else "",
                    "status": "success",
                    "top": _read_meta(kept_dir).get("top", ""),
                    "duplicate_reason": f"duplicate_existing_dir_kept:{kept_dir.name}",
                }
            )

    removed_rows.extend(dir_removed_rows)

    rebuilt_rows = _rebuild_index_from_existing_dirs(
        old_rows=kept_rows,
        root=args.root,
        fieldnames=fieldnames,
    )

    with args.index.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rebuilt_rows)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["design", "source_path", "status", "top", "duplicate_reason"],
        )
        writer.writeheader()
        for row in removed_rows:
            writer.writerow(
                {
                    "design": row["design"],
                    "source_path": row.get("source_path", ""),
                    "status": row.get("status", ""),
                    "top": row.get("top", ""),
                    "duplicate_reason": row.get("duplicate_reason", ""),
                }
            )

    print(f"kept_rows {len(rebuilt_rows)}")
    print(f"removed_duplicates {len(removed_rows)}")
    print(f"wrote {args.report}")


if __name__ == "__main__":
    main()
