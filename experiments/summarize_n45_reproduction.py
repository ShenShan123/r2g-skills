#!/usr/bin/env python3
"""Compare independent Nangate45 reproductions with frozen first-run evidence."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def load(path: Path):
    return json.loads(path.read_text())


def save(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")


def summarize(root: Path) -> dict:
    manifest = load(root / "cohort_manifest.json")
    records = []
    for first in manifest["records"]:
        complete = root / "jobs" / first["task_id"] / "complete.json"
        if not complete.exists():
            records.append(dict(first, reproduction_status="pending"))
            continue
        result = load(complete)
        second = sorted(set(result.get("physical_result", {}).get("normalized_failure_signature", [])))
        original = sorted(set(first["first_run_signatures"]))
        status = result["status"]
        if status == "physical_failure_pending_reproduction" and second == original:
            decision = "admitted_matching_failure"
        elif status == "baseline_clean_single_run":
            decision = "not_reproduced_clean"
        elif status == "physical_failure_pending_reproduction":
            decision = "signature_changed_requires_third_run"
        else:
            decision = "inconclusive_reproduction"
        records.append(
            dict(
                first,
                reproduction_status=status,
                second_run_signatures=second,
                second_run_elapsed_seconds=result.get("elapsed_seconds"),
                admission_decision=decision,
            )
        )

    counts: dict[str, int] = {}
    for record in records:
        key = record.get("admission_decision", "pending")
        counts[key] = counts.get(key, 0) + 1
    report = {
        "schema": "nangate45-reproduction-results-v1",
        "cohort_size": len(records),
        "completed": sum(record.get("reproduction_status") != "pending" for record in records),
        "decision_counts": counts,
        "records": records,
    }
    reports = root / "reports"
    save(reports / "reproduction_results.json", report)
    with (reports / "reproduction_results.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "task_id", "top_module", "repo_url", "mapped_cells", "reproduction_class",
                "size_band", "first_run_signatures", "second_run_signatures",
                "reproduction_status", "admission_decision", "first_run_elapsed_seconds",
                "second_run_elapsed_seconds",
            ],
        )
        writer.writeheader()
        for record in records:
            writer.writerow({key: record.get(key) for key in writer.fieldnames})
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", type=Path, required=True)
    args = parser.parse_args()
    report = summarize(args.campaign.resolve())
    print(json.dumps({key: report[key] for key in ("cohort_size", "completed", "decision_counts")}, indent=2))


if __name__ == "__main__":
    main()
