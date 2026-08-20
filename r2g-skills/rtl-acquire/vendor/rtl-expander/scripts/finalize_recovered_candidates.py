#!/usr/bin/env python3
"""Finalize recovered Phase-1.5 candidates as published DesignInstances or quarantine."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from exercise_repair_cohort import expand_r1, failure_index, rows, source_roots
from functional_ontology import classify as classify_function
from run_expansion_round import (
    EQUIV_SCHEMA, FAMILY_SCHEMA, PIPELINE_SCHEMA, SCHEMA, FileLock,
    assign_families_and_splits, atomic_write_json, dependency_closure, digest,
    extract_semantic_facts, functional_assets, load_benchmark_hashes, load_jsonl,
    normalized_hash, parse_design_units, quality_scores, resource_class,
    source_language, source_unit_records, stable_id, synthesize_design, utc_now,
    validate_publish_invariants, write_jsonl, write_manifests,
)


FINAL_SCHEMA = "rtl_repair_final_adjudication_v1"


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def recovered_rows(corpus: Path) -> list[dict[str, Any]]:
    quality = corpus / "quality/phase1_5"
    values: list[dict[str, Any]] = []
    for path in (quality / "repair_exercise.jsonl", quality / "repair_r2_r3_exercise.jsonl"):
        values.extend(row for row in rows(path) if row.get("status") == "RECOVERED_CANDIDATE")
    return sorted(values, key=lambda row: row["sample_key"])


def structural_evidence(synthesis: dict[str, Any]) -> tuple[bool, list[str]]:
    log = Path(synthesis.get("log_path") or "")
    text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
    failures: list[str] = []
    if not synthesis.get("generic_pass"):
        failures.append("GENERIC_SYNTHESIS_NOT_PASS")
    if "Found and reported 0 problems" not in text:
        failures.append("YOSYS_CHECK_PASS_NOT_RECORDED")
    if re.search(r"Warning: Wire .* is used but has no driver", text):
        failures.append("UNDRIVEN_SIGNAL")
    if re.search(r"ERROR:|multiple conflicting drivers", text, re.I):
        failures.append("STRUCTURAL_ERROR")
    return not failures, failures


def build_r1_record(
    corpus: Path, root: Path, repo: dict[str, Any], failure: dict[str, Any], candidate: dict[str, Any],
    paths: list[Path], synthesis: dict[str, Any], benchmark_hashes: set[str], benchmark_ready: bool,
) -> dict[str, Any]:
    top = str(candidate["top_candidate"])
    units, edges, _, duplicates = parse_design_units(paths)
    if top not in units or duplicates or units[top].get("testbench_constructs") or not units[top].get("ports"):
        raise ValueError("top legality or duplicate-unit gate failed")
    closure = dependency_closure(top, edges)
    exact_hash, norm_hash = normalized_hash(paths, root)
    facts = extract_semantic_facts(top, closure, units, edges, paths)
    design_id = stable_id("d", PIPELINE_SCHEMA, candidate["repo_id"], failure.get("project_key"), top, norm_hash)
    facts_path = corpus / "recovered_designs" / design_id / "semantic_facts.json"
    atomic_write_json(facts_path, facts)
    source_units = source_unit_records(paths, root)
    languages = sorted({unit["language"] for unit in source_units})
    functional_confidence, functional_evidence = functional_assets(root, paths)
    generic_hash = synthesis["generic_netlist_hash"]
    hierarchy_hash = digest(json.dumps({name: sorted(edges.get(name, set())) for name in sorted(closure)}, sort_keys=True).encode())
    contaminated = any(value and value.lower() in benchmark_hashes for value in (exact_hash, norm_hash, generic_hash))
    documentation = repo.get("documentation", {"document_count": 0})
    license_status = repo.get("license_status", "UNKNOWN")
    release_policy = repo.get("release_policy", "QUARANTINE")
    timestamp = utc_now()
    record: dict[str, Any] = {
        "schema": SCHEMA, "design_id": design_id,
        "family_id": stable_id("f", FAMILY_SCHEMA, generic_hash or hierarchy_hash or norm_hash),
        "variant_id": stable_id("v", design_id, "default"),
        "revision_id": stable_id("rev", candidate["repo_id"], repo.get("commit_sha", "UNKNOWN")),
        "identity": {"repository_name": repo.get("repository_name", Path(root).name), "project_key": failure.get("project_key")},
        "provenance": {
            "repository_url": repo.get("repository_url", "UNKNOWN"), "commit_sha": repo.get("commit_sha", "UNKNOWN"),
            "repo_id": candidate["repo_id"], "source_provider": "git", "license_files": repo.get("license_files", []),
            "acquisition_timestamp": timestamp, "documentation_present": bool(documentation.get("document_count", 0)),
        },
        "release": {"license_status": license_status, "release_policy": release_policy, "license_files": repo.get("license_files", []), "license_evidence": repo.get("license_evidence", {})},
        "build": {
            "top_module": top, "top_evidence": ["R1_BUILD_CONTEXT_RECOVERY", *candidate.get("evidence", [])], "top_score": 100,
            "source_files": [str(path.relative_to(root)) for path in paths], "compile_source_files": [str(path.relative_to(root)) for path in paths],
            "include_dirs": sorted({".", *(str(path.parent.relative_to(root)) for path in paths)}), "defines": [], "packages": [],
            "parameters": {}, "dependency_modules": sorted(closure), "unresolved_dependencies": [],
        },
        "source": {
            "source_languages": languages, "languages": languages, "source_units": source_units, "mixed_language": len(languages) > 1,
            "original_root": str(root), "source_storage": "IMMUTABLE_REPOSITORY_REVISION",
            "canonical_elaboration_representation": synthesis.get("generic_netlist"), "canonical_synthesis_view": synthesis.get("generic_netlist"),
            "transformation": "T0_NATIVE",
        },
        "documentation": documentation, "semantic_facts_path": str(facts_path), "rtl_semantics": facts,
        "resource": {"class": resource_class(facts), "timeout_seconds": 120},
        "validation": {"static_scan": True, "parse": True, "elaborate": True, "structural_check": True, "warnings": []},
        "repair": {
            "required": True, "level": "R1", "rules": candidate.get("evidence", []), "source_rtl_bytes_changed": False,
            "equivalence": {"result": "NOT_APPLICABLE", "reason": "SOURCE_RTL_UNCHANGED_BUILD_RECOVERY", "equivalence_schema": EQUIV_SCHEMA, "mode": "NONE", "parameter_assumptions": {}, "blackbox_matching": "EXACT_INTERFACE", "macro_abstraction": "NONE", "reset_assumptions": "NONE"},
            "sample_key": candidate["sample_key"],
        },
        "conversion_equivalence": {"result": "NOT_APPLICABLE", "equivalence_schema": EQUIV_SCHEMA, "mode": "NONE", "parameter_assumptions": {}, "blackbox_matching": "EXACT_INTERFACE", "macro_abstraction": "NONE", "reset_assumptions": "NONE"},
        "synthesis": synthesis,
        "verification": {"functional_confidence": functional_confidence, "evidence_paths": functional_evidence, "executed": False},
        "dedup": {"source_hash": exact_hash, "normalized_hash": norm_hash, "hierarchy_hash": hierarchy_hash, "generic_netlist_hash": generic_hash, "family_cluster_method": "rtl_family_v1_multi_evidence"},
        "contamination": {"audit_status": "FAIL" if contaminated else "PASS" if benchmark_ready else "NOT_RUN", "benchmark_contaminated": contaminated, "benchmark_name": None, "benchmark_profile": "rtl_benchmark_profile_v1"},
        "quality": {}, "created_at": timestamp,
    }
    record["functional_ontology"] = classify_function(record)
    eq, grade, tv, tier, eq_components, tv_components = quality_scores(record)
    if release_policy not in {"PUBLIC_EXPORT_ALLOWED", "INTERNAL_TRAINING_ONLY"} and tier == "TRAINING_GOLD":
        tier = "TRAINING_SILVER"
    record["quality"] = {"engineering_quality": eq, "engineering_grade": grade, "engineering_quality_components": eq_components, "engineering_quality_schema": "rtl_eq_v1", "functional_schema": "rtl_fc_v1", "training_value": tv, "training_value_components": tv_components, "training_value_schema": "rtl_tv_v1", "training_tier": tier, "quality_flags": []}
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, default=Path.home() / "work/data/rtl_corpus")
    parser.add_argument("--yosys", default="/opt/OpenROAD/oss-cad-suite/bin/yosys")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    corpus = args.corpus_root
    candidates = recovered_rows(corpus)
    repositories = rows(corpus / "manifests/repositories.jsonl")
    repos = {row["repo_id"]: row for row in repositories}
    roots = source_roots(corpus, repositories)
    failures = failure_index(corpus)
    benchmark_hashes, benchmark_ready = load_benchmark_hashes(corpus / "benchmark_registry")
    published: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    for candidate in candidates:
        key = (str(candidate.get("repo_id")), str(next((row.get("project_key") for row in rows(corpus / "quality/phase1_5/failure_audit_cohort.jsonl") if row.get("sample_key") == candidate["sample_key"]), "")), str(candidate.get("top_candidate")))
        failure = failures.get(key)
        root = roots.get(candidate["repo_id"])
        evidence: dict[str, Any] = {"classification_is_correctness_evidence": False}
        disposition = "QUARANTINE"
        reason = "VALIDATION_EVIDENCE_INSUFFICIENT"
        design_id = None
        if failure and root and candidate.get("repair_level") == "R1":
            paths, expansion = expand_r1(root, failure)
            original_hashes_unchanged = all(
                (root / unit["path"]).is_file() and digest((root / unit["path"]).read_bytes()) == unit["sha256"]
                for unit in failure.get("source_units", [])
            )
            include_dirs = sorted({root, *(path.parent for path in paths)})
            top = candidate["top_candidate"]
            top_language = next((source_language(path) for path in paths if re.search(rf"(?im)^\s*module\s+{re.escape(top)}\b", path.read_text(encoding="utf-8", errors="replace"))), "verilog")
            synthesis = synthesize_design(top, top_language, [], paths, include_dirs, corpus / "repairs/phase1_5" / candidate["sample_key"] / "final_validation", args.yosys, 120)
            structural_pass, structural_failures = structural_evidence(synthesis)
            evidence.update({"original_source_hashes_unchanged": original_hashes_unchanged, "expanded_source_units": [str(path.relative_to(root)) for path in paths], "expansion_evidence": expansion, "parse_pass": bool(synthesis.get("generic_pass")), "elaboration_pass": bool(synthesis.get("generic_pass")), "structural_pass": structural_pass, "structural_failures": structural_failures, "generic_synthesis_pass": bool(synthesis.get("generic_pass")), "equivalence": "NOT_APPLICABLE_SOURCE_RTL_UNCHANGED"})
            if original_hashes_unchanged and structural_pass:
                try:
                    record = build_r1_record(corpus, root, repos[candidate["repo_id"]], failure, candidate, paths, synthesis, benchmark_hashes, benchmark_ready)
                    published.append(record)
                    design_id = record["design_id"]
                    disposition, reason = "PUBLISH", "ALL_R1_BUILD_RECOVERY_GATES_PASS"
                except (KeyError, ValueError) as exc:
                    evidence["record_build_error"] = str(exc)
        elif candidate.get("repair_level") == "R2":
            structural_pass, structural_failures = structural_evidence(candidate.get("synthesis", {}))
            evidence.update({"deterministic_transformation": candidate.get("transformation", {}).get("status") == "CONVERTED", "parse_pass": bool(candidate.get("synthesis", {}).get("generic_pass")), "elaboration_pass": bool(candidate.get("synthesis", {}).get("generic_pass")), "generic_synthesis_pass": bool(candidate.get("synthesis", {}).get("generic_pass")), "structural_pass": structural_pass, "structural_failures": structural_failures, "equivalence": candidate.get("equivalence", {}).get("result", "UNAVAILABLE")})
            reason = "R2_EQUIVALENCE_UNAVAILABLE_AND_STRUCTURAL_GATE_FAILED" if not structural_pass else "R2_EQUIVALENCE_UNAVAILABLE"
        decision = {"schema": FINAL_SCHEMA, "sample_key": candidate["sample_key"], "repo_id": candidate.get("repo_id"), "top_candidate": candidate.get("top_candidate"), "repair_level": candidate.get("repair_level"), "final_disposition": disposition, "reason": reason, "published_design_id": design_id, "evidence": evidence, "decided_at": utc_now()}
        decisions.append(decision)
        if disposition != "PUBLISH":
            atomic_write_json(corpus / "quarantine/repair_phase1_5" / candidate["sample_key"] / "decision.json", decision)
    if args.apply:
        with FileLock(corpus / "locks/manifest.lock", blocking=True):
            designs = load_jsonl(corpus / "manifests/all_designs.jsonl", "design_id")
            designs.update({record["design_id"]: record for record in published})
            split_args = SimpleNamespace(split_seed="rtl-corpus-v1", train_percent=90, val_percent=5, organization_aware_split=False)
            assign_families_and_splits(designs, corpus, split_args)
            validate_publish_invariants(corpus, designs)
            write_manifests(corpus, designs)
    atomic_text(corpus / "quality/phase1_5/repair_final_adjudication.jsonl", "".join(json.dumps(row, sort_keys=True) + "\n" for row in decisions))
    summary = {"schema": FINAL_SCHEMA, "candidates": len(decisions), "dispositions": dict(Counter(row["final_disposition"] for row in decisions)), "published_design_ids": [row["published_design_id"] for row in decisions if row["published_design_id"]], "all_candidates_terminal": len(decisions) == 4 and all(row["final_disposition"] in {"PUBLISH", "QUARANTINE", "REJECT"} for row in decisions), "apply": args.apply}
    atomic_write_json(corpus / "quality/phase1_5/repair_final_adjudication_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
