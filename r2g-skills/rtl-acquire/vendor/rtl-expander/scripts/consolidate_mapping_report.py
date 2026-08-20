#!/usr/bin/env python3
"""Merge immutable initial mapping outcomes with the bounded retry ledger."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path


SCHEMA = "rtl_mapping_cohort_report_v2"


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def atomic(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, default=Path.home() / "work/data/rtl_corpus")
    args = parser.parse_args()
    quality = args.corpus_root / "quality/phase1_5"
    initial = rows(quality / "mapping_cohort.jsonl")
    retry = rows(quality / "mapping_retry.jsonl")
    retry_by_design = {row["design_id"]: row for row in retry}
    initial_passes = sum(bool(row.get("mapping_pass")) for row in initial)
    final_outcomes: Counter[str] = Counter()
    for row in initial:
        if row.get("mapping_pass"):
            final_outcomes["PASS_INITIAL"] += 1
            continue
        outcome = retry_by_design.get(row["design_id"], {}).get("retry_mapping_outcome", "SECOND_PASS_MISSING")
        if outcome == "TIMEOUT":
            outcome = "MAPPING_TIMEOUT_AFTER_ESCALATION"
        final_outcomes[outcome] += 1
    final_passes = initial_passes + final_outcomes.get("PASS_AFTER_RESOURCE_ESCALATION", 0)
    report = {
        "schema": SCHEMA, "families": len(initial),
        "initial_mapping_passes": initial_passes,
        "initial_mapping_pass_rate": round(initial_passes / max(1, len(initial)), 6),
        "final_mapping_passes": final_passes,
        "final_mapping_pass_rate": round(final_passes / max(1, len(initial)), 6),
        "initial_outcomes": dict(Counter("PASS" if row.get("mapping_pass") else row.get("reason", "FAIL") for row in initial)),
        "final_outcomes": dict(final_outcomes),
        "bounded_retry_complete": len(retry) == sum(not row.get("mapping_pass") for row in initial),
    }
    atomic(quality / "mapping_effective_summary.json", report)
    summary_path = quality / "mapping_cohort_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary.pop("mapping_pass_rate", None)
    summary.update({key: report[key] for key in ("initial_mapping_passes", "initial_mapping_pass_rate", "final_mapping_passes", "final_mapping_pass_rate", "initial_outcomes", "final_outcomes")})
    atomic(summary_path, summary)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
