#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path


import sys

_SKILL_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SKILL_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS_DIR))
from skill_env import (
    default_merged_manifest,
    default_out_root,
    default_seed_root,
    workspace_path,
)

MANIFEST = default_merged_manifest()
OUT_CSV = workspace_path("audits/mapped_netlist_duplicate_report.csv")
OUT_JSON = workspace_path("audits/mapped_netlist_duplicate_summary.json")


def rows_from_corpus_indexes() -> list[dict[str, str]]:
    """First-round fallback: the merged manifest is refreshed AFTER this audit,
    so on a fresh corpus audit the per-root indexes directly."""
    rows: list[dict[str, str]] = []
    for root, group in ((default_out_root(), "external_corpus"),
                        (default_seed_root(), "orfs_seed_designs")):
        index = root / "index.csv"
        if not index.exists():
            continue
        for row in csv.DictReader(index.open()):
            if row.get("status") != "success":
                continue
            rows.append({
                "design": row.get("design", ""),
                "source_group": group,
                "mapped_netlist_path": str(root / row.get("design", "") / "mapped_netlist.v"),
            })
    return rows


def normalize_netlist_text(text: str) -> str:
    text = re.sub(r"//.*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Report exact mapped-netlist duplicates across merged success samples.")
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--out-csv", type=Path, default=OUT_CSV)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    args = parser.parse_args()

    if args.manifest.exists():
        rows = list(csv.DictReader(args.manifest.open()))
    else:
        rows = rows_from_corpus_indexes()
    checked = []
    groups: dict[str, list[dict[str, str]]] = {}

    for row in rows:
        mapped_text = (row.get("mapped_netlist_path", "") or "").strip()
        if not mapped_text:
            continue
        mapped_path = Path(mapped_text)
        if not mapped_path.exists() or mapped_path.is_dir():
            continue
        normalized = normalize_netlist_text(mapped_path.read_text(encoding="utf-8", errors="ignore"))
        signature = sha256_text(normalized)
        record = {
            "design": row["design"],
            "source_group": row.get("source_group", ""),
            "mapped_netlist_path": str(mapped_path),
            "signature": signature,
        }
        checked.append(record)
        groups.setdefault(signature, []).append(record)

    dup_groups = {sig: members for sig, members in groups.items() if len(members) > 1}
    out_rows = []
    for signature, members in sorted(dup_groups.items()):
        member_text = ";".join(f"{m['source_group']}:{m['design']}" for m in members)
        for member in members:
            out_rows.append(
                {
                    "design": member["design"],
                    "source_group": member["source_group"],
                    "mapped_netlist_path": member["mapped_netlist_path"],
                    "signature": signature,
                    "group_size": str(len(members)),
                    "group_members": member_text,
                }
            )

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "design",
                "source_group",
                "mapped_netlist_path",
                "signature",
                "group_size",
                "group_members",
            ],
        )
        writer.writeheader()
        writer.writerows(out_rows)

    summary = {
        "checked_mapped_netlists": len(checked),
        "duplicate_group_count": len(dup_groups),
        "duplicate_design_count": len(out_rows),
    }
    args.out_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"wrote {args.out_csv} rows={len(out_rows)}")
    print(f"wrote {args.out_json}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
