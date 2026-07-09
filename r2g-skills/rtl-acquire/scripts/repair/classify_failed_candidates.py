#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from pathlib import Path


import sys

_SKILL_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SKILL_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS_DIR))
from skill_env import (
    out_root_path,
    workspace_path,
)

INDEX = out_root_path("index.csv")
OUT_RETRY = workspace_path("failures/failed_candidates_retry.csv")
OUT_EXCLUDE = workspace_path("failures/failed_candidates_exclude.csv")
OUT_RETRY_CANDIDATES = workspace_path("failures/failed_candidates_retry_candidates.csv")


def is_high_value_retry(source_path: str, notes: str) -> bool:
    source = (source_path or "").lower()
    text = (notes or "").lower()
    if "hdl-benchmarks-min/iscas89/verilog/s38584a.v" in source and "module `\\delta'" in text:
        return True
    if source.endswith("/common/adder.v") and "module `\\add_add_module'" in text:
        return True
    if source.endswith("/common/dff.v") and "module `top' not found" in text:
        return True
    return False


def classify(source_path: str, notes: str) -> tuple[str, str]:
    text = (notes or "").lower()
    if any(k in text for k in ("single_port_ram", "dual_port_ram", "fakeram", "sram")):
        return "exclude", "ram_or_macro_dependency"
    source = (source_path or "").lower()
    if ("invalid name for macro definition" in text or "%%" in text) and "vtr-verilog-to-routing-min/vtr_flow/benchmarks/arithmetic/adder_trees/verilog/adder_tree.v" in source:
        return "retry", "template_materialization_candidate"
    if "invalid name for macro definition" in text or "%%" in text:
        return "exclude", "template_placeholder"
    if "async reset" in text:
        return "exclude", "semantic_reset_issue"
    if "re-definition of module `$abstract" in text:
        return "retry", "frontend_sv2v_abstract_redefinition"
    if "unrecognized format character" in text or ("system task" in text and "$display" in text):
        return "retry", "simulation_system_task_sanitize"
    if is_high_value_retry(source_path, notes):
        if "module `" in text and "not part of the design" in text:
            return "retry", "missing_module_or_top_mismatch"
        if "not found" in text or "top" in text:
            return "retry", "possible_top_issue"
    if "module `" in text and "not part of the design" in text:
        return "exclude", "low_value_failure"
    if "syntax error" in text or "frontend" in text or "slang" in text:
        return "exclude", "low_value_failure"
    if "not found" in text or "top" in text:
        return "exclude", "low_value_failure"
    return "exclude", "low_value_failure"


def infer_source(source_path: str) -> str:
    text = (source_path or "").lower()
    if "hdl-benchmarks" in text:
        return "downloads_hdl_benchmarks"
    if "vtr-verilog-to-routing" in text:
        return "downloads_vtr"
    if "openroad-flow-scripts" in text:
        return "orfs_local"
    return "retry"


def retry_rank(design: str) -> tuple[int, int, str]:
    lowered = (design or "").lower()
    is_long_prefix = int(lowered.startswith("hdl_benchmarks_min_") or lowered.startswith("vtr_verilog_to_routing_min_"))
    return (is_long_prefix, len(design or ""), design or "")


def main() -> None:
    parser = argparse.ArgumentParser(description="Split failed external candidates into retry vs exclude lists.")
    parser.add_argument("--index", type=Path, default=INDEX)
    parser.add_argument("--out-retry", type=Path, default=OUT_RETRY)
    parser.add_argument("--out-exclude", type=Path, default=OUT_EXCLUDE)
    parser.add_argument("--out-retry-candidates", type=Path, default=OUT_RETRY_CANDIDATES)
    args = parser.parse_args()

    rows = list(csv.DictReader(args.index.open()))
    success_sources = {
        row.get("source_path", "")
        for row in rows
        if row.get("status") == "success" and row.get("source_path", "")
    }
    retry_rows_by_source: dict[str, dict] = {}
    retry_candidate_rows_by_source: dict[str, dict] = {}
    exclude_rows = []

    for row in rows:
        if row["status"] == "success":
            continue
        source_path = row.get("source_path", "")
        if source_path in success_sources:
            exclude_rows.append(
                {
                    "design": row["design"],
                    "status": row["status"],
                    "source_path": source_path,
                    "classification": "exclude",
                    "reason": "duplicate_of_success_source",
                    "notes": row.get("notes", ""),
                }
            )
            continue
        bucket, reason = classify(row.get("source_path", ""), row.get("notes", ""))
        out_row = {
            "design": row["design"],
            "status": row["status"],
            "source_path": source_path,
            "classification": bucket,
            "reason": reason,
            "notes": row.get("notes", ""),
        }
        if bucket == "retry":
            existing = retry_rows_by_source.get(source_path)
            if existing and retry_rank(existing["design"]) <= retry_rank(row["design"]):
                continue
            retry_rows_by_source[source_path] = out_row
            retry_candidate_rows_by_source[source_path] = {
                "source": infer_source(source_path),
                "design": row["design"],
                "priority": "high",
                "expected_top": row.get("top", "") or "top",
                "source_path": source_path,
                "notes": f"retry:{reason}; {row.get('notes', '')}".strip(),
            }
        else:
            exclude_rows.append(out_row)

    retry_rows = sorted(retry_rows_by_source.values(), key=lambda row: row["design"])
    retry_candidate_rows = sorted(retry_candidate_rows_by_source.values(), key=lambda row: row["design"])

    for path, data in ((args.out_retry, retry_rows), (args.out_exclude, exclude_rows)):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["design", "status", "source_path", "classification", "reason", "notes"])
            writer.writeheader()
            writer.writerows(data)
        print(f"wrote {path} rows={len(data)}")

    args.out_retry_candidates.parent.mkdir(parents=True, exist_ok=True)
    with args.out_retry_candidates.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["source", "design", "priority", "expected_top", "source_path", "notes"],
        )
        writer.writeheader()
        writer.writerows(retry_candidate_rows)
    print(f"wrote {args.out_retry_candidates} rows={len(retry_candidate_rows)}")


if __name__ == "__main__":
    main()
