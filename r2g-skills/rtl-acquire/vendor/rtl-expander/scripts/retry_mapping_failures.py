#!/usr/bin/env python3
"""Perform one failure-specific second pass over the Phase-1.5 mapping cohort."""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from run_mapping_cohort import liberty_cells, read_jsonl, run_one, sha256


SCHEMA = "rtl_mapping_retry_v1"
ESCALATED_TIMEOUT = {"TINY": 75, "SMALL": 120, "MEDIUM": 180, "LARGE": 240, "XLARGE": 360}


def diagnose_initial(row: dict[str, Any]) -> dict[str, Any]:
    if row.get("reason") == "INTERNAL_CELLS_REMAIN":
        cells = {name: count for name, count in row.get("cell_types", {}).items() if name.startswith("$") or name.startswith("$_")}
        macro_like = any(name.startswith(("$mem", "$paramod", "$blackbox")) for name in cells)
        return {
            "retry_mapping_outcome": "MACRO_OR_INTERNAL_ABSTRACTION" if macro_like else "LOWERING_GAP",
            "diagnostic_evidence": {"internal_cell_types": cells},
        }
    log = Path(row.get("log_path") or "")
    tail = log.read_text(encoding="utf-8", errors="replace")[-12000:] if log.is_file() else ""
    return {
        "retry_mapping_outcome": "YOSYS_ERROR_DIAGNOSED" if tail else "ABSTAIN",
        "diagnostic_evidence": {"log_tail": tail},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, default=Path.home() / "work/data/rtl_corpus")
    parser.add_argument("--liberty", type=Path, default=Path.home() / "work/openroad/OpenROAD-flow-scripts/flow/platforms/nangate45/lib/NangateOpenCellLibrary_typical.lib")
    parser.add_argument("--yosys", default="/opt/OpenROAD/oss-cad-suite/bin/yosys")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    initial = read_jsonl(args.corpus_root / "quality/phase1_5/mapping_cohort.jsonl")
    failures = [row for row in initial if not row.get("mapping_pass")]
    designs = {row["design_id"]: row for row in read_jsonl(args.corpus_root / "manifests/all_designs.jsonl")}
    lib_hash = sha256(args.liberty)
    cells = liberty_cells(args.liberty)
    version = subprocess.run([args.yosys, "-V"], text=True, capture_output=True, timeout=10, check=False).stdout.strip()
    results: list[dict[str, Any]] = []
    timeouts = [row for row in failures if row.get("reason") == "TIMEOUT"]

    def retry(row: dict[str, Any]) -> dict[str, Any]:
        record = designs[row["design_id"]]
        retried = run_one(
            record, args, cells, lib_hash, version, attempt_label="retry_resource_escalation",
            timeout_override=ESCALATED_TIMEOUT.get(row.get("resource_class"), 180),
        )
        return {
            "schema": SCHEMA, "design_id": row["design_id"], "family_id": row["family_id"],
            "initial_mapping_outcome": row.get("reason"),
            "retry_mapping_outcome": "PASS_AFTER_RESOURCE_ESCALATION" if retried.get("mapping_pass") else retried.get("reason"),
            "initial_result": row, "retry_result": retried,
        }

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = [executor.submit(retry, row) for row in timeouts]
        for future in as_completed(futures):
            results.append(future.result())
    for row in failures:
        if row.get("reason") == "TIMEOUT":
            continue
        results.append({
            "schema": SCHEMA, "design_id": row["design_id"], "family_id": row["family_id"],
            "initial_mapping_outcome": row.get("reason"), "initial_result": row,
            **diagnose_initial(row),
        })
    results.sort(key=lambda row: row["design_id"])
    summary = {
        "schema": SCHEMA, "initial_failures": len(failures), "completed": len(results),
        "initial_outcomes": dict(Counter(row["initial_mapping_outcome"] for row in results)),
        "retry_outcomes": dict(Counter(row["retry_mapping_outcome"] for row in results)),
        "bounded_second_pass_complete": len(results) == len(failures),
    }
    out = args.corpus_root / "quality/phase1_5"
    (out / "mapping_retry.jsonl").write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in results), encoding="utf-8")
    (out / "mapping_retry_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
