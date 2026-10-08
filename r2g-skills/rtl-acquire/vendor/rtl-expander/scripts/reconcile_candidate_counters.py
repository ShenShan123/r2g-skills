#!/usr/bin/env python3
"""Reconcile historical candidate counters from immutable outcome ledgers."""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any


SCHEMA = "rtl_candidate_counter_audit_v1"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return rows


def key(row: dict[str, Any], accepted: bool) -> tuple[str, str] | None:
    if accepted:
        return str(row.get("identity", {}).get("project_key")), str(row.get("build", {}).get("top_module"))
    top = row.get("top_candidate")
    return (str(row.get("project_key")), str(top)) if top else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, default=Path.home() / "work/data/rtl_corpus")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    manifests = args.corpus_root / "manifests"
    repositories = read_jsonl(manifests / "repositories.jsonl")
    designs = read_jsonl(manifests / "all_designs.jsonl")
    failures: list[dict[str, Any]] = []
    for path in sorted((args.corpus_root / "failures/top_candidates").glob("*.jsonl")):
        failures.extend(read_jsonl(path))
    outcomes: dict[str, set[tuple[str, str]]] = defaultdict(set)
    accepted: dict[str, set[tuple[str, str]]] = defaultdict(set)
    rejected: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for row in designs:
        repo_id = str(row.get("provenance", {}).get("repo_id"))
        if value := key(row, True):
            outcomes[repo_id].add(value)
            accepted[repo_id].add(value)
    for row in failures:
        repo_id = str(row.get("repo_id"))
        if value := key(row, False):
            outcomes[repo_id].add(value)
            rejected[repo_id].add(value)
    corrections: list[dict[str, Any]] = []
    for row in repositories:
        repo_id = row["repo_id"]
        reconstructed = len(outcomes.get(repo_id, set()))
        reported = int(row.get("top_candidate_attempts", 0) or 0)
        if reconstructed <= reported:
            continue
        correction = {
            "schema": SCHEMA, "repo_id": repo_id, "reported_attempts": reported,
            "reconstructed_outcomes": reconstructed, "delta": reconstructed - reported,
            "accepted": len(accepted.get(repo_id, set())), "rejected": len(rejected.get(repo_id, set())),
            "basis": "immutable_design_and_failure_ledgers",
        }
        corrections.append(correction)
        if args.apply:
            row["top_candidate_attempts"] = reconstructed
            row["top_candidates_total"] = reconstructed
            row["top_candidates_accepted"] = len(accepted.get(repo_id, set()))
            row["top_candidates_rejected"] = len(rejected.get(repo_id, set()))
            row["candidate_counter_audit"] = correction
    if args.apply and corrections:
        temporary = manifests / f".repositories.jsonl.tmp.{os.getpid()}"
        temporary.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in sorted(repositories, key=lambda row: row["repo_id"])), encoding="utf-8")
        os.replace(temporary, manifests / "repositories.jsonl")
    summary = {
        "schema": SCHEMA, "apply": args.apply, "corrected_repositories": len(corrections),
        "counter_gap_before": sum(row["delta"] for row in corrections),
        "counter_gap_after": 0 if args.apply else sum(row["delta"] for row in corrections),
        "corrections": corrections,
    }
    target = args.corpus_root / "quality/phase1_5/candidate_counter_audit.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, target)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
