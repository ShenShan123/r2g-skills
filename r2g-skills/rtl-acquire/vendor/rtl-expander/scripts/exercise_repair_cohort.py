#!/usr/bin/env python3
"""Exercise evidence-backed R1 recovery without publishing candidate designs."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from frontier import canonical_repository_identity, default_frontier_path
from run_expansion_round import RTL_SUFFIXES, source_language, synthesize_design


SCHEMA = "rtl_repair_exercise_v1"


def rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]


def failure_index(corpus: Path) -> dict[tuple[str, str, str], dict[str, Any]]:
    result: dict[tuple[str, str, str], dict[str, Any]] = {}
    for path in sorted((corpus / "failures/top_candidates").glob("*.jsonl")):
        for row in rows(path):
            result[(str(row.get("repo_id")), str(row.get("project_key")), str(row.get("top_candidate")))] = row
    return result


def source_roots(corpus: Path, repositories: list[dict[str, Any]]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    db = sqlite3.connect(default_frontier_path(corpus))
    for row in repositories:
        try:
            key = canonical_repository_identity(row.get("repository_url", ""))["repository_key"]
        except ValueError:
            continue
        value = db.execute(
            "SELECT source_path FROM repository_revisions WHERE repository_key=? AND lower(commit_sha)=lower(?)",
            (key, row.get("commit_sha", "")),
        ).fetchone()
        if value:
            result[row["repo_id"]] = Path(value[0])
    db.close()
    return result


def expand_r1(root: Path, failure: dict[str, Any]) -> tuple[list[Path], list[str]]:
    initial = [root / unit["path"] for unit in failure.get("source_units", []) if (root / unit["path"]).is_file()]
    chosen = set(initial)
    evidence: list[str] = []
    log_text = ""
    log_path = failure.get("synthesis", {}).get("log_path")
    if log_path:
        try:
            log_text = Path(log_path).read_text(encoding="utf-8", errors="replace")[-100_000:]
        except OSError:
            pass
    missing_modules = set(re.findall(r"(?:Module|module) [`'\\]*([A-Za-z_$][\w$]*)[`'\\]* (?:referenced.*not part|not found)", log_text))
    missing_includes = set(re.findall(r"(?:include file|Can't open include file) ['\"`]?([^'\"`\s]+)", log_text, re.I))
    candidates = [path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in RTL_SUFFIXES]
    for path in candidates[:5000]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        declared = set(re.findall(r"(?im)^\s*module\s+([A-Za-z_$][\w$]*)", text))
        if declared & missing_modules:
            chosen.add(path)
            evidence.append(f"module_definition:{path.relative_to(root)}")
        if path.name in {Path(value).name for value in missing_includes}:
            chosen.add(path)
            evidence.append(f"include_resolution:{path.relative_to(root)}")
    return sorted(chosen), sorted(set(evidence))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, default=Path.home() / "work/data/rtl_corpus")
    parser.add_argument("--max-cases", type=int, default=25)
    parser.add_argument("--yosys", default="/opt/OpenROAD/oss-cad-suite/bin/yosys")
    args = parser.parse_args()
    audits = rows(args.corpus_root / "quality/phase1_5/failure_audit_cohort.jsonl")
    repositories = rows(args.corpus_root / "manifests/repositories.jsonl")
    roots = source_roots(args.corpus_root, repositories)
    failures = failure_index(args.corpus_root)
    selected = [row for row in audits if row.get("suggested_repair_level") == "R1" and row.get("confidence") == "HIGH"][:args.max_cases]
    results: list[dict[str, Any]] = []
    for index, audit in enumerate(selected, 1):
        key = (str(audit.get("repo_id")), str(audit.get("project_key")), str(audit.get("top_candidate")))
        failure = failures.get(key)
        root = roots.get(str(audit.get("repo_id")))
        if not failure or not root:
            results.append({"schema": SCHEMA, "sample_key": audit["sample_key"], "status": "SOURCE_UNAVAILABLE"})
            continue
        paths, evidence = expand_r1(root, failure)
        print(f"[repair {index}/{len(selected)}] {audit['top_candidate']} +{max(0, len(paths)-len(failure.get('source_units', [])))} units", flush=True)
        if not evidence:
            results.append({
                "schema": SCHEMA, "sample_key": audit["sample_key"], "repo_id": audit["repo_id"],
                "top_candidate": audit["top_candidate"], "repair_level": "R1",
                "status": "EVIDENCE_INSUFFICIENT", "evidence": [],
            })
            continue
        include_dirs = sorted({root, *(path.parent for path in paths)})
        top_language = next((source_language(path) for path in paths if re.search(rf"(?im)^\s*(?:module|entity)\s+{re.escape(audit['top_candidate'])}\b", path.read_text(encoding="utf-8", errors="replace"))), "verilog")
        output = args.corpus_root / "repairs/phase1_5" / audit["sample_key"] / "R1"
        synthesis = synthesize_design(audit["top_candidate"], top_language, [], paths, include_dirs, output, args.yosys, 90)
        results.append({
            "schema": SCHEMA, "sample_key": audit["sample_key"], "repo_id": audit["repo_id"],
            "top_candidate": audit["top_candidate"], "repair_level": "R1",
            "status": "RECOVERED_CANDIDATE" if synthesis.get("generic_pass") else "RETRY_FAILED",
            "evidence": evidence, "source_units_before": len(failure.get("source_units", [])),
            "source_units_after": len(paths), "synthesis": synthesis,
            "publication_status": "NOT_PUBLISHED_REQUIRES_FULL_VALIDATION",
        })
    target = args.corpus_root / "quality/phase1_5/repair_exercise.jsonl"
    target.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in results), encoding="utf-8")
    summary = {
        "schema": SCHEMA, "selected": len(selected), "completed": len(results),
        "status": dict(Counter(row["status"] for row in results)),
        "recovered_candidates": sum(row["status"] == "RECOVERED_CANDIDATE" for row in results),
        "published": 0,
    }
    (args.corpus_root / "quality/phase1_5/repair_exercise_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
