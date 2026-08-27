#!/usr/bin/env python3
"""Promote compact future-lineage shadow evidence, never ORFS RUN trees."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from tehm.sync import canonical_json


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(value))


def promote(source: Path, evidence: Path) -> dict:
    evidence.mkdir(parents=True, exist_ok=True)
    names = (
        "prospective_manifest.raw.json", "manifest.normalized.json",
        "manifest_validation.log", "cases.jsonl", "decision_cases.jsonl",
        "observation_outcomes.json", "observation_outcomes.jsonl",
        "policy.json", "case_binding.json", "replay_evidence.json",
    )
    campaign_names = (
        "shadow_events.jsonl", "snapshot_counts.json",
        "outcome_append_report.json", "joined_outcomes.json", "shadow_metrics.json",
    )
    copied = []
    for name in names:
        src = source / name
        if src.is_file():
            shutil.copy2(src, evidence / name)
            copied.append({"path": name, "sha256": _sha(evidence / name)})
    campaign = source / "campaign"
    for name in campaign_names:
        src = campaign / name
        if src.is_file():
            shutil.copy2(src, evidence / name)
            copied.append({"path": name, "sha256": _sha(evidence / name)})
    decision = source / "decision-campaign" / "decision_gate.json"
    if decision.is_file():
        shutil.copy2(decision, evidence / "decision_gate.json")
        copied.append({"path": "decision_gate.json", "sha256": _sha(evidence / "decision_gate.json")})
    binding = {
        "version": "future-shadow-evidence-binding-v1",
        "source_scratch_root": str(source),
        "evidence_root": str(evidence),
        "copied_files": copied,
        "orfs_run_trees_promoted": False,
        "canonical_memory_mutation": "none",
        "promotion_eligible": False,
        "parametric_view_status": "NOT_IMPLEMENTED",
    }
    _write(evidence / "promotion_report.json", binding)
    return binding


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--evidence-root", type=Path, required=True)
    args = ap.parse_args(argv)
    print(json.dumps(promote(args.source.resolve(), args.evidence_root.resolve()),
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
