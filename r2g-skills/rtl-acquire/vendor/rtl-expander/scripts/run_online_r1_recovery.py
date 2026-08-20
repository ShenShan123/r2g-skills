#!/usr/bin/env python3
"""Run bounded evidence-strong R1 build recovery as a non-blocking production line."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from exercise_repair_cohort import expand_r1, rows, source_roots
from finalize_recovered_candidates import build_r1_record, structural_evidence
from run_expansion_round import (
    FileLock, assign_families_and_splits, load_benchmark_hashes, load_jsonl,
    source_language, synthesize_design, validate_publish_invariants, write_manifests,
)
from corpus_state import CorpusState


SCHEMA = "rtl_online_r1_recovery_v1"


def sample_key(row: dict) -> str:
    value = "\0".join(str(row.get(key, "")) for key in ("repo_id", "project_key", "top_candidate", "failure_type"))
    return hashlib.sha256(value.encode()).hexdigest()


def atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, default=Path.home() / "work/data/rtl_corpus")
    parser.add_argument("--max-cases", type=int, default=50)
    parser.add_argument("--yosys", default="/opt/OpenROAD/oss-cad-suite/bin/yosys")
    args = parser.parse_args()
    corpus = args.corpus_root
    repository_rows = rows(corpus / "manifests/repositories.jsonl")
    repositories = {row["repo_id"]: row for row in repository_rows}
    roots = source_roots(corpus, repository_rows)
    ledger_path = corpus / "quality/phase2/online_r1_recovery.jsonl"
    previous = {row["sample_key"]: row for row in rows(ledger_path)}
    failures: list[dict] = []
    for path in sorted((corpus / "failures/top_candidates").glob("*.jsonl")):
        failures.extend(row for row in rows(path) if row.get("failure_type") in {"GENERIC_SYNTH_FAIL", "UNRESOLVED_CHILD"})
    pending = sorted(
        (row for row in failures if sample_key(row) not in previous and row.get("repo_id") in roots),
        key=sample_key,
    )[: max(0, args.max_cases)]
    benchmark_hashes, benchmark_ready = load_benchmark_hashes(corpus / "benchmark_registry")
    published: dict[str, dict] = {}
    for failure in pending:
        key = sample_key(failure)
        root = roots[failure["repo_id"]]
        paths, evidence = expand_r1(root, failure)
        result = {"schema": SCHEMA, "sample_key": key, "repo_id": failure.get("repo_id"), "project_key": failure.get("project_key"), "top_candidate": failure.get("top_candidate"), "attempt": 1, "source_rtl_bytes_changed": False, "classification_is_correctness_evidence": False}
        if not evidence:
            result.update({"status": "ABSTAIN", "reason": "NO_UNIQUE_BUILD_CONTEXT_EVIDENCE"})
            previous[key] = result
            continue
        include_dirs = sorted({root, *(path.parent for path in paths)})
        top = str(failure.get("top_candidate"))
        declaration = re.compile(rf"(?im)^\s*module\s+{re.escape(top)}\b|^\s*entity\s+{re.escape(top)}\s+is\b")
        top_language = next((source_language(path) for path in paths if declaration.search(path.read_text(encoding="utf-8", errors="replace"))), "verilog")
        synthesis = synthesize_design(top, top_language, [], paths, include_dirs, corpus / "repairs/phase2" / key / "R1", args.yosys, 120)
        structural_pass, structural_failures = structural_evidence(synthesis)
        candidate = {"sample_key": key, "repo_id": failure["repo_id"], "top_candidate": top, "repair_level": "R1", "evidence": evidence}
        if synthesis.get("generic_pass") and structural_pass:
            try:
                record = build_r1_record(corpus, root, repositories[failure["repo_id"]], failure, candidate, paths, synthesis, benchmark_hashes, benchmark_ready)
                published[record["design_id"]] = record
                result.update({"status": "PUBLISH", "published_design_id": record["design_id"], "evidence": evidence})
            except (KeyError, ValueError) as exc:
                result.update({"status": "QUARANTINE", "reason": "DESIGN_RECORD_GATE_FAILED", "detail": str(exc), "evidence": evidence})
        else:
            result.update({"status": "QUARANTINE", "reason": "R1_VALIDATION_GATE_FAILED", "structural_failures": structural_failures, "synthesis_reason": synthesis.get("reason"), "evidence": evidence})
        previous[key] = result
    if published:
        with FileLock(corpus / "locks/manifest.lock", blocking=True):
            designs = load_jsonl(corpus / "manifests/all_designs.jsonl", "design_id")
            designs.update(published)
            assign_families_and_splits(designs, corpus, SimpleNamespace(split_seed="rtl-corpus-v1", train_percent=90, val_percent=5, organization_aware_split=False))
            validate_publish_invariants(corpus, designs)
            with CorpusState(corpus) as state:
                state.apply_incremental(designs=designs.values())
            write_manifests(corpus, designs)
    atomic(ledger_path, "".join(json.dumps(row, sort_keys=True) + "\n" for row in sorted(previous.values(), key=lambda row: row["sample_key"])))
    summary = {
        "schema": SCHEMA, "attempted_this_run": len(pending),
        "published_this_run": len(published),
        "published_design_ids": sorted(published),
        "cumulative_status": dict(Counter(row["status"] for row in previous.values())),
        "bounded_attempts_per_candidate": 1,
    }
    atomic(corpus / "quality/phase2/online_r1_recovery_summary.json", json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
