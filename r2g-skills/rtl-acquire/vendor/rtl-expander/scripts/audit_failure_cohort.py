#!/usr/bin/env python3
"""Build deterministic stratified Phase-1.5 failure audit cohorts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


AUDIT_SCHEMA = "rtl_failure_adjudication_v2"
TARGET_FAILURES = ("GENERIC_SYNTH_FAIL", "PARSE_FAIL")
RECOVERABLE = {
    "BUILD_CONTEXT_RECOVERABLE": "R1",
    "PORTABILITY_RECOVERABLE": "R2",
    "SYNTH_COMPAT_RECOVERABLE": "R3",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return rows


def stable_key(row: dict[str, Any], seed: str) -> str:
    identity = "\0".join(str(row.get(key, "")) for key in ("repo_id", "project_key", "top_candidate", "failure_type"))
    return hashlib.sha256((seed + "\0" + identity).encode()).hexdigest()


def diagnostic_text(row: dict[str, Any]) -> str:
    values = [str(row.get("detail", "")), str(row.get("failure_type", ""))]
    synthesis = row.get("synthesis", {})
    values.extend((str(synthesis.get("reason", "")), str(synthesis.get("log_tail", ""))))
    log_path = synthesis.get("log_path")
    if log_path:
        try:
            values.append(Path(log_path).read_text(encoding="utf-8", errors="replace")[-80_000:])
        except OSError:
            pass
    return "\n".join(values).lower()


def diagnose(row: dict[str, Any]) -> tuple[str, str, list[str]]:
    text = diagnostic_text(row)
    top = str(row.get("top_candidate", "")).lower()
    evidence: list[str] = []
    if re.search(r"(?:^|_)(tb|testbench|sim|formal|harness|verification)(?:_|$)", top):
        return "BAD_TOP", "HIGH", ["top_name_is_verification_like"]
    if "mixed_language_vhdl_top_unsupported" in text or re.search(r"ghdl|encrypted|protected envelope|unsupported language", text):
        return "UNSUPPORTED_TOOLCHAIN", "HIGH", ["frontend_or_language_unsupported"]
    build_patterns = [
        (r"cannot open include|include file .* not found|no such file or directory", "missing_include"),
        (r"module .* not found|referenced in module .* is not part of the design|unknown module|unresolved_child", "missing_module_context"),
        (r"package .* not found|failed to resolve package|unknown package", "package_or_order_context"),
        (r"redefinition of module|duplicate module", "duplicate_definition_context"),
        (r"parameter .* not found|can't find object for defparam", "parameter_context"),
    ]
    for pattern, label in build_patterns:
        if re.search(pattern, text):
            evidence.append(label)
    if evidence:
        return "BUILD_CONTEXT_RECOVERABLE", "HIGH", evidence
    portability = [
        (r"syntax error.*(?:logic|always_ff|always_comb|interface|typedef|enum|struct)", "systemverilog_mode_or_normalization"),
        (r"unsupported.*(?:packed|unpacked|interface|modport|enum|struct)", "portable_construct_lowering"),
        (r"implicit wire|default_nettype", "implicit_net_normalization"),
    ]
    for pattern, label in portability:
        if re.search(pattern, text, re.S):
            return "PORTABILITY_RECOVERABLE", "MEDIUM", [label]
    synth_compat = [
        (r"multiple edge sensitive events|multiple always blocks|multiple drivers", "synthesis_process_compatibility"),
        (r"unsupported cell|can't map|cannot synthesize|non-constant.*loop|latch", "synthesis_construct_compatibility"),
        (r"\$(?:error|fatal|finish|display)|assert|assume|cover", "verification_construct_isolation"),
        (r"tri-state|tribuf|inout", "tristate_lowering_or_isolation"),
    ]
    for pattern, label in synth_compat:
        if re.search(pattern, text):
            return "SYNTH_COMPAT_RECOVERABLE", "MEDIUM", [label]
    if re.search(r"unexpected end of file|missing.*endmodule|unterminated|premature end", text):
        return "INCOMPLETE_SOURCE", "HIGH", ["truncated_or_incomplete_source"]
    if row.get("failure_type") == "PARSE_FAIL":
        return "ABSTAIN", "LOW", ["insufficient_evidence_for_parse_failure_classification"]
    if row.get("failure_type") == "GENERIC_SYNTH_FAIL":
        return "ABSTAIN", "LOW", ["insufficient_evidence_for_synthesis_failure_classification"]
    return "ABSTAIN", "LOW", ["insufficient_evidence_for_tool_failure_classification"]


def stratum(row: dict[str, Any]) -> tuple[str, str, str]:
    languages = "+".join(sorted({str(unit.get("language", "unknown")) for unit in row.get("source_units", [])})) or "unknown"
    project = "manifest" if row.get("project_key") not in {None, "", "__repo__"} else "repo_root"
    label, _, _ = diagnose(row)
    return languages, project, label


def stratified_sample(rows: list[dict[str, Any]], count: int, seed: str) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[stratum(row)].append(row)
    for values in buckets.values():
        values.sort(key=lambda row: stable_key(row, seed))
    selected: list[dict[str, Any]] = []
    keys = sorted(buckets)
    while len(selected) < min(count, len(rows)):
        progressed = False
        for key in keys:
            if buckets[key]:
                selected.append(buckets[key].pop(0))
                progressed = True
                if len(selected) >= min(count, len(rows)):
                    break
        if not progressed:
            break
    return selected


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, default=Path.home() / "work/data/rtl_corpus")
    parser.add_argument("--per-class", type=int, default=100)
    parser.add_argument("--seed", default="rtl_phase1_5_failure_audit_v1")
    args = parser.parse_args()
    failures: list[dict[str, Any]] = []
    for path in sorted((args.corpus_root / "failures/top_candidates").glob("*.jsonl")):
        failures.extend(read_jsonl(path))
    audited: list[dict[str, Any]] = []
    population = Counter(row.get("failure_type", "UNKNOWN") for row in failures)
    for failure_type in TARGET_FAILURES:
        cohort = stratified_sample([row for row in failures if row.get("failure_type") == failure_type], args.per_class, args.seed)
        for row in cohort:
            label, confidence, evidence = diagnose(row)
            audited.append({
                "schema": AUDIT_SCHEMA, "failure_type": failure_type,
                "repo_id": row.get("repo_id"), "repository_name": row.get("repository_name"),
                "project_key": row.get("project_key"), "top_candidate": row.get("top_candidate"),
                "source_units": row.get("source_units", []), "audit_class": label,
                "suggested_repair_level": RECOVERABLE.get(label), "confidence": confidence,
                "evidence": evidence,
                "adjudication_status": "ABSTAIN" if label == "ABSTAIN" else "AUTOMATIC_ADJUDICATION",
                "classification_is_correctness_evidence": False,
                "publication_gate_status": "NOT_EVALUATED",
                "sample_key": stable_key(row, args.seed),
            })
    class_counts = Counter(row["audit_class"] for row in audited)
    per_failure = {
        failure: dict(Counter(row["audit_class"] for row in audited if row["failure_type"] == failure))
        for failure in TARGET_FAILURES
    }
    recoverable = sum(row["audit_class"] in RECOVERABLE for row in audited)
    summary = {
        "schema": AUDIT_SCHEMA, "seed": args.seed, "population": dict(population),
        "sampled": len(audited), "sampled_by_failure": dict(Counter(row["failure_type"] for row in audited)),
        "audit_classes": dict(class_counts), "audit_classes_by_failure": per_failure,
        "triage_suspected_recoverable": recoverable,
        "triage_suspected_recoverable_rate": round(recoverable / max(1, len(audited)), 6),
        "abstained": class_counts.get("ABSTAIN", 0),
        "automatic_adjudication_preferred": True,
        "classification_is_correctness_evidence": False,
        "publication_requires_gates": ["parse", "elaboration", "synthesis", "applicable_equivalence", "functional"],
    }
    out = args.corpus_root / "quality/phase1_5"
    atomic_write(out / "failure_audit_cohort.jsonl", "".join(json.dumps(row, sort_keys=True) + "\n" for row in audited))
    atomic_write(out / "failure_audit_summary.json", json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
