#!/usr/bin/env python3
"""Refresh the merged netlist-graph corpus manifest.

Merges the success rows of every corpus root (the external expansion corpus +
the optional ORFS seed-design corpus) into ONE manifest keyed on the shared
`netlist_graph.pt` format emitted by def-graph's netlist_graph.py. The 30pt
base-dataset union is retired (rtl-acquire-ingestion-2026-07-09.md amendment);
the manifest IS the corpus definition — no external base is merged in.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

_SKILL_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SKILL_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS_DIR))
from skill_env import (  # noqa: E402
    default_merged_manifest,
    default_out_root,
    default_seed_root,
    workspace_path,
)

GRAPH_PT_NAME = "netlist_graph.pt"
DEFAULT_GRAPH_FORMAT = "netlist_graph_v1"
ORFS_ROOT = default_seed_root()
EXT_ROOT = default_out_root()
DEFAULT_PUBLISH_ELIGIBLE = workspace_path("manifests/publish_eligible_designs.csv")
OUT_CSV = default_merged_manifest()
OUT_MD = OUT_CSV.with_suffix(".md")


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _refresh_success_only_index() -> int:
    idx = EXT_ROOT / "index.csv"
    out = EXT_ROOT / "index_success_only.csv"
    if not idx.exists():
        return 0
    rows = list(csv.DictReader(idx.open()))
    if not rows:
        return 0
    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows([r for r in rows if r["status"] == "success"])
    return sum(1 for r in rows if r["status"] == "success")


def _load_publish_eligible(path: Path) -> set[str]:
    if not path.exists():
        return set()
    rows = list(csv.DictReader(path.open()))
    return {r["design"] for r in rows
            if str(r.get("publish_eligible", "")).strip().lower() == "true" and r.get("design")}


def _graph_pt(design_dir: Path, design: str) -> Path | None:
    """The design's netlist graph; tolerates the legacy per-design name so a
    partially-migrated corpus is still enumerable (rows keep their real path)."""
    for name in (GRAPH_PT_NAME, f"{design}_1_1_yosys.pt"):
        p = design_dir / name
        if p.exists():
            return p
    return None


def _index_rows(root: Path, index_name: str, source_group: str,
                keep: set[str] | None) -> list[dict[str, str]]:
    index_path = root / index_name
    if not index_path.exists():
        return []
    rows: list[dict[str, str]] = []
    for r in csv.DictReader(index_path.open()):
        if r.get("status") != "success":
            continue
        if keep is not None and r["design"] not in keep:
            continue
        ddir = root / r["design"]
        meta_path = ddir / "design_meta.json"
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        pt_path = _graph_pt(ddir, r["design"])
        mapped = ddir / "mapped_netlist.v"
        if pt_path is None or not mapped.exists():
            continue
        rows.append(
            {
                "design": r["design"],
                "source_group": source_group,
                "status": "success",
                "top": r.get("top", ""),
                "pt_path": str(pt_path),
                "mapped_netlist_path": str(mapped),
                "source_path": r.get("source_path", "") or ";".join(meta.get("rtl_files", [])),
                "graph_format": (r.get("graph_format", "")
                                 or meta.get("graph_schema_version", "")
                                 or DEFAULT_GRAPH_FORMAT),
                "notes": r.get("notes", ""),
            }
        )
    return rows


def _build_rows(*, publish_eligible_external: set[str] | None = None) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    rows += _index_rows(ORFS_ROOT, "index.csv", "orfs_seed_designs", keep=None)
    rows += _index_rows(EXT_ROOT, "index_success_only.csv", "external_corpus",
                        keep=publish_eligible_external)
    rows.sort(key=lambda r: (r["source_group"], r["design"].lower()))
    return rows


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Refresh merged netlist-graph corpus manifest.")
    parser.add_argument("--publish-eligible-csv", type=Path, default=DEFAULT_PUBLISH_ELIGIBLE)
    parser.add_argument("--use-publish-eligible", action="store_true")
    args = parser.parse_args()

    success_count = _refresh_success_only_index()
    publish_eligible_external = (_load_publish_eligible(args.publish_eligible_csv)
                                 if args.use_publish_eligible else None)
    rows = _build_rows(publish_eligible_external=publish_eligible_external)
    fieldnames = [
        "design",
        "source_group",
        "status",
        "top",
        "pt_path",
        "mapped_netlist_path",
        "source_path",
        "graph_format",
        "notes",
    ]
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(_norm(row["design"]), []).append(row)
    overlaps = {k: v for k, v in grouped.items() if len(v) > 1}
    counts = Counter(r["source_group"] for r in rows)

    with OUT_MD.open("w") as f:
        f.write("# Netlist-Graph Corpus Manifest\n\n")
        f.write(f"- total_success_samples: {len(rows)}\n")
        f.write(f"- external_success_only_rows: {success_count}\n")
        if publish_eligible_external is not None:
            f.write(f"- external_publish_eligible_rows: {len(publish_eligible_external)}\n")
        for k in sorted(counts):
            f.write(f"- {k}: {counts[k]}\n")
        f.write(f"- exact_normalized_name_overlap_groups: {len(overlaps)}\n")
        if overlaps:
            for k, group in sorted(overlaps.items()):
                members = ", ".join(f"{r['source_group']}:{r['design']}" for r in group)
                f.write(f"  - {k}: {members}\n")
        else:
            f.write("- no exact normalized-name overlap detected across merged success samples\n")
        f.write("\n## Files\n")
        f.write(f"- manifest_csv: {OUT_CSV}\n")
        f.write(f"- orfs_seed_index: {ORFS_ROOT / 'index.csv'}\n")
        f.write(f"- external_success_index: {EXT_ROOT / 'index_success_only.csv'}\n")
        if publish_eligible_external is not None:
            f.write(f"- external_publish_eligible_csv: {args.publish_eligible_csv}\n")

    print(f"wrote {OUT_CSV}")
    print(f"wrote {OUT_MD}")
    print(f"merged_success_samples {len(rows)}")


if __name__ == "__main__":
    main()
