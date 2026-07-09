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
    default_out_root,
)

ROOT = default_out_root()
INDEX = ROOT / "index.csv"


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild external benchmark index from the actual output directories.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--index", type=Path, default=INDEX)
    args = parser.parse_args()

    fieldnames = [
        "design",
        "top",
        "synth_variant",
        "status",
        "cells",
        "comb_cells",
        "seq_cells",
        "nets",
        "source_path",
        "graph_format",
        "duplicate_reason",
        "notes",
    ]

    rows: list[dict[str, str]] = []
    for ddir in sorted(p for p in args.root.iterdir() if p.is_dir() and not p.name.startswith("_")):
        design = ddir.name
        meta = read_json(ddir / "design_meta.json")
        stats = read_json(ddir / "cell_stats.json")
        pt_path = ddir / "netlist_graph.pt"
        if not pt_path.exists():  # legacy 30pt-era per-design name
            pt_path = ddir / f"{design}_1_1_yosys.pt"
        mapped_path = ddir / "mapped_netlist.v"
        if pt_path.exists() and mapped_path.exists():
            status = "success"
        elif mapped_path.exists():
            status = "graph_failed"
        else:
            status = meta.get("status") or "synth_failed"
        rtl_files = meta.get("rtl_files", [])
        source_path = ";".join(str(p) for p in rtl_files) if isinstance(rtl_files, list) else ""
        rows.append(
            {
                "design": design,
                "top": str(meta.get("top") or stats.get("top_module") or ""),
                "synth_variant": str(meta.get("synth_variant", "")),
                "status": status,
                "cells": str(stats.get("cells", "")),
                "comb_cells": str(stats.get("comb_cells", "")),
                "seq_cells": str(stats.get("seq_cells", "")),
                "nets": str(stats.get("nets", "")),
                "source_path": source_path,
                "graph_format": str(meta.get("graph_schema_version", "")),
                "duplicate_reason": "",
                "notes": str(meta.get("notes", "")),
            }
        )

    rows.sort(key=lambda r: (r["status"] != "success", r["design"].lower()))
    with args.index.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    success = sum(1 for r in rows if r["status"] == "success")
    failed = len(rows) - success
    print(f"wrote {args.index}")
    print(f"rows {len(rows)}")
    print(f"success {success}")
    print(f"failed {failed}")


if __name__ == "__main__":
    main()
