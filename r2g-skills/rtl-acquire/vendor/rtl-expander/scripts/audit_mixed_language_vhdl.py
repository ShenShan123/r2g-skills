#!/usr/bin/env python3
"""Automatically adjudicate a deterministic mixed-language VHDL failure cohort."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from audit_failure_cohort import diagnostic_text, read_jsonl


SCHEMA = "rtl_mixed_language_vhdl_adjudication_v1"
TARGET = "MIXED_LANGUAGE_VHDL_TOP_UNSUPPORTED"


def stable_key(row: dict[str, Any], seed: str) -> str:
    identity = "\0".join(str(row.get(key, "")) for key in ("repo_id", "project_key", "top_candidate"))
    return hashlib.sha256((seed + "\0" + identity).encode()).hexdigest()


def adjudicate(row: dict[str, Any]) -> tuple[str, str, list[str]]:
    languages = {str(unit.get("language", "unknown")) for unit in row.get("source_units", [])}
    text = diagnostic_text(row)
    if "vhdl" not in languages or not ({"verilog", "systemverilog"} & languages):
        return "BUILD_CONFIGURATION", "MEDIUM", ["failure_label_and_source_language_set_disagree"]
    if "mixed_language_vhdl_top_unsupported" in text or "vhdl top" in text:
        return "FRONTEND_LIMITATION", "HIGH", ["canonical_frontend_rejects_vhdl_top_with_verilog_systemverilog_closure"]
    if "ghdl" in text and ("not found" in text or "unavailable" in text):
        return "FRONTEND_LIMITATION", "HIGH", ["ghdl_frontend_unavailable"]
    if "unsupported language direction" in text:
        return "UNSUPPORTED_LANGUAGE_DIRECTION", "HIGH", ["explicit_unsupported_language_direction"]
    if "package" in text or "library" in text or "work." in text:
        return "BUILD_CONFIGURATION", "MEDIUM", ["vhdl_library_or_package_context"]
    return "ABSTAIN", "LOW", ["insufficient_diagnostic_evidence"]


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, default=Path.home() / "work/data/rtl_corpus")
    parser.add_argument("--count", type=int, default=25)
    parser.add_argument("--seed", default="rtl_mixed_language_vhdl_adjudication_v1")
    args = parser.parse_args()
    failures: list[dict[str, Any]] = []
    for path in sorted((args.corpus_root / "failures/top_candidates").glob("*.jsonl")):
        failures.extend(row for row in read_jsonl(path) if row.get("failure_type") == TARGET)
    failures.sort(key=lambda row: stable_key(row, args.seed))
    selected = failures[: min(args.count, len(failures))]
    results: list[dict[str, Any]] = []
    for row in selected:
        label, confidence, evidence = adjudicate(row)
        results.append({
            "schema": SCHEMA, "sample_key": stable_key(row, args.seed),
            "repo_id": row.get("repo_id"), "project_key": row.get("project_key"),
            "top_candidate": row.get("top_candidate"), "source_units": row.get("source_units", []),
            "adjudication": label, "confidence": confidence, "evidence": evidence,
            "adjudication_status": "ABSTAIN" if label == "ABSTAIN" else "AUTOMATIC_ADJUDICATION",
            "classification_is_correctness_evidence": False,
            "publication_gate_status": "NOT_EVALUATED",
        })
    counts = Counter(row["adjudication"] for row in results)
    summary = {
        "schema": SCHEMA, "population": len(failures), "sampled": len(results),
        "adjudication": dict(counts), "abstained": counts.get("ABSTAIN", 0),
        "systematic_frontend_fix_worth_exercising": counts.get("FRONTEND_LIMITATION", 0) >= max(1, len(results) // 2),
        "classification_is_correctness_evidence": False,
        "publication_requires_gates": ["parse", "elaboration", "synthesis", "applicable_equivalence", "functional"],
    }
    out = args.corpus_root / "quality/phase1_5"
    atomic_write(out / "mixed_language_vhdl_audit.jsonl", "".join(json.dumps(row, sort_keys=True) + "\n" for row in results))
    atomic_write(out / "mixed_language_vhdl_audit_summary.json", json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
