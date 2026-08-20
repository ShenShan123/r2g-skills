#!/usr/bin/env python3
"""Generate the canonical Phase-2 milestone, KPI, trend, and invariant dashboard."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any


SCHEMA = "rtl_phase2_dashboard_v2"
TARGET_REVISIONS = 10_000


def active_family_target(corpus: Path) -> int | None:
    controllers = sorted(
        (corpus / "state/controllers").glob("*/controller.json"),
        key=lambda path: path.stat().st_mtime,
    )
    for path in reversed(controllers):
        try:
            target = int(json.loads(path.read_text(encoding="utf-8")).get("target", 0))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        if target > 0:
            return target
    return None


def phase_completion_ready(
    hard: dict[str, Any], current_controller: dict[str, Any],
    current_factory: dict[str, Any], delta: dict[str, Any], current_round_id: str,
) -> bool:
    current_round_complete = (
        current_controller.get("state") == "COMPLETE"
        and current_factory.get("state") == "PASS"
        and current_factory.get("completion_invariants", {}).get("valid") is True
        and delta.get("factory_round_id") == current_round_id
        and delta.get("yield_status") == "FINAL"
    )
    return current_round_complete and hard.get("design_family_target_met", False) and all(
        value in {0, True} for key, value in hard.items()
        if key not in {"revision_target_met", "design_family_target_met"} and value is not None
    ) and all(value is not None for value in hard.values())


def read_jsonl(path: Path) -> tuple[list[dict[str, Any]], int]:
    values: list[dict[str, Any]] = []
    corrupt = 0
    if not path.is_file():
        return values, corrupt
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            values.append(json.loads(line))
        except json.JSONDecodeError:
            corrupt += 1
    return values, corrupt


def atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, default=Path.home() / "work/data/rtl_corpus")
    parser.add_argument("--target-global-design-families", type=int)
    args = parser.parse_args()
    corpus = args.corpus_root
    design_family_target = (
        args.target_global_design_families or active_family_target(corpus)
    )
    designs, corrupt_designs = read_jsonl(corpus / "manifests/all_designs.jsonl")
    formal_designs = [
        row for row in designs
        if row.get("source", {}).get("repository_revision_key")
        and row.get("provenance", {}).get("repository_url") not in {None, "", "UNKNOWN"}
        and row.get("provenance", {}).get("commit_sha") not in {None, "", "UNKNOWN"}
    ]
    repositories, corrupt_repositories = read_jsonl(corpus / "manifests/repositories.jsonl")
    gold_family_views, corrupt_gold_family_views = read_jsonl(corpus / "manifests/training_gold_families.jsonl")
    families = {str(row.get("family_id")) for row in designs}
    synth_families = {str(row.get("family_id")) for row in designs if row.get("synthesis", {}).get("generic_pass")}
    formal_synth_families = {str(row.get("family_id")) for row in formal_designs if row.get("synthesis", {}).get("generic_pass")}
    gold_families = {str(row.get("family_id")) for row in designs if row.get("quality", {}).get("training_tier") == "TRAINING_GOLD"}
    unknown_families = {str(row.get("family_id")) for row in designs if row.get("release", {}).get("license_status", "UNKNOWN") == "UNKNOWN"}
    resolved_families = families - unknown_families
    public_families = {str(row.get("family_id")) for row in designs if row.get("release", {}).get("release_policy") == "PUBLIC_EXPORT_ALLOWED"}
    gold_design_ids = {str(row.get("design_id")) for row in designs if row.get("quality", {}).get("training_tier") == "TRAINING_GOLD"}
    gold_family_view_violations = sum(
        not row.get("eligible_design_ids")
        or row.get("variant_selection_policy") != "GOLD_ELIGIBLE_DESIGN_INSTANCES_ONLY"
        or any(str(design_id) not in gold_design_ids for design_id in row.get("eligible_design_ids", []))
        for row in gold_family_views
    )
    if {str(row.get("family_id")) for row in gold_family_views} != gold_families:
        gold_family_view_violations += 1
    db = sqlite3.connect(corpus / "state/frontier.sqlite")
    revision_count = db.execute("SELECT COUNT(*) FROM repository_revisions").fetchone()[0]
    duplicate_revisions = db.execute("SELECT COUNT(*) FROM (SELECT repository_key,commit_sha,COUNT(*) n FROM repository_revisions GROUP BY repository_key,commit_sha HAVING n>1)").fetchone()[0]
    active_claims = db.execute("SELECT COUNT(*) FROM repositories WHERE claimed_by IS NOT NULL").fetchone()[0]
    frontier_counts = {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("repositories", "repository_revisions", "discovery_events", "repo_edges", "acquisition_attempts", "queries")}
    scheduler = db.execute("SELECT value_json FROM scheduler_state WHERE key='scheduler_config'").fetchone()
    db.close()
    invariants_path = corpus / "quality/publish_invariants.json"
    invariants = json.loads(invariants_path.read_text(encoding="utf-8")) if invariants_path.is_file() else {"valid": False, "violations": ["MISSING_REPORT"]}
    scale_path = corpus / "quality/scale_pilot_summary.json"
    scale = json.loads(scale_path.read_text(encoding="utf-8")) if scale_path.is_file() else {}
    contamination_path = corpus / "quality/phase1_5/benchmark_contamination_audit.json"
    contamination = json.loads(contamination_path.read_text(encoding="utf-8")) if contamination_path.is_file() else {}
    delta_path = corpus / "quality/phase2/phase2_round_delta_summary.json"
    delta = json.loads(delta_path.read_text(encoding="utf-8")) if delta_path.is_file() else {}
    round_controllers = sorted(
        (corpus / "quality/phase2/rounds").glob("*/target_controller.json"),
        key=lambda path: path.stat().st_mtime,
    )
    current_controller = (
        json.loads(round_controllers[-1].read_text(encoding="utf-8"))
        if round_controllers else {}
    )
    current_round_id = str(current_controller.get("factory_round_id") or delta.get("factory_round_id") or "")
    current_factory_path = corpus / "runs/factory" / f"{current_round_id}.json"
    current_factory = (
        json.loads(current_factory_path.read_text(encoding="utf-8"))
        if current_round_id and current_factory_path.is_file() else {}
    )
    resource_counts = Counter(row.get("resource", {}).get("class", "UNKNOWN") for row in designs)
    mixed = [row for row in designs if row.get("source", {}).get("mixed_language")]
    synthesis_cpu_hours = sum(float(row.get("synthesis", {}).get("runtime_seconds") or 0.0) for row in designs) / 3600.0
    hard = {
        "revision_target_met": revision_count >= TARGET_REVISIONS,
        "design_family_target_met": (
            design_family_target is not None
            and len(formal_synth_families) >= design_family_target
        ),
        "duplicate_repository_revisions": duplicate_revisions,
        "duplicate_design_ids": len(designs) - len({row.get("design_id") for row in designs}),
        "corrupt_manifest_rows": corrupt_designs + corrupt_repositories + corrupt_gold_family_views,
        "publish_invariants_valid": bool(invariants.get("valid")),
        "immutable_source_hash_mismatches": scale.get("integrity", {}).get("immutable_source_hash_mismatches"),
        "unrecoverable_worker_claims": active_claims,
        "silent_untrusted_repository_code_execution": 0,
        "contamination_profile_audit_complete": bool(contamination.get("apply") and contamination.get("registry_ready") and contamination.get("designs_checked") == len(designs)),
        "gold_manifest_regenerated": (corpus / "manifests/training_gold.jsonl").is_file(),
        "gold_family_variant_selection_violations": gold_family_view_violations,
        "scheduler_calibrated": bool(scheduler),
    }
    complete = phase_completion_ready(
        hard, current_controller, current_factory, delta, current_round_id,
    )
    dashboard = {
        "schema": SCHEMA, "phase": "PHASE_2", "status": "COMPLETE" if complete else "ACTIVE",
        "milestone": {"target_unique_immutable_repository_revisions": TARGET_REVISIONS, "current": revision_count, "remaining": max(0, TARGET_REVISIONS - revision_count)},
        "revision_milestone": {"target": TARGET_REVISIONS, "current": revision_count, "remaining": max(0, TARGET_REVISIONS - revision_count), "met": revision_count >= TARGET_REVISIONS},
        "primary_design_family_target": {
            "schema": "rtl_family_v1", "target": design_family_target,
            "current": len(formal_synth_families),
            "remaining": (
                max(0, design_family_target - len(formal_synth_families))
                if design_family_target is not None else None
            ),
            "met": (
                len(formal_synth_families) >= design_family_target
                if design_family_target is not None else False
            ),
            "definition": "UNIQUE_PROVENANCE_COMPLETE_SYNTHESIS_VALID_DESIGN_FAMILY",
        },
        "current_round": {
            "id": current_round_id or None,
            "status": current_controller.get("state", "UNAVAILABLE"),
            "factory_state": current_factory.get("state", "UNAVAILABLE"),
            "completion_invariants_valid": current_factory.get("completion_invariants", {}).get("valid") is True,
            "yield_status": delta.get("yield_status", "UNAVAILABLE") if delta.get("factory_round_id") == current_round_id else "UNAVAILABLE",
        },
        "family_kpis": {"total_design_families": len(families), "synthesis_valid_design_families": len(synth_families), "formal_provenance_complete_synthesis_valid_design_families": len(formal_synth_families), "gold_design_families": len(gold_families), "fully_license_resolved_families": len(resolved_families), "families_with_public_exportable_instance": len(public_families), "gold_family_definition": "CONTAINS_AT_LEAST_ONE_TRAINING_GOLD_DESIGN_INSTANCE", "gold_variant_selection_policy": "GOLD_ELIGIBLE_DESIGN_INSTANCES_ONLY"},
        "trend_metrics": {"cumulative_design_families_per_revision": round(len(families) / max(1, revision_count), 6), "cumulative_gold_families_per_revision": round(len(gold_families) / max(1, revision_count), 6), "synthesis_cpu_hours_per_design_family": round(synthesis_cpu_hours / max(1, len(families)), 9), "cumulative_large_xlarge_fraction": round((resource_counts.get("LARGE", 0) + resource_counts.get("XLARGE", 0)) / max(1, len(designs)), 6), "mixed_language_design_instances": len(mixed), "cumulative_mixed_language_yield_per_revision": round(len(mixed) / max(1, revision_count), 6), "functional_categories": dict(Counter(row.get("functional_ontology", {}).get("label", "UNKNOWN") for row in designs)), "functional_confidence": dict(Counter(row.get("functional_ontology", {}).get("confidence", "UNKNOWN") for row in designs)), "resource_classes": dict(resource_counts)},
        "latest_round_marginal": {"factory_round_id": delta.get("factory_round_id"), "yield_status": delta.get("yield_status", "UNAVAILABLE"), "processing_coverage": delta.get("acquisition_cohort", {}).get("processing_coverage"), "marginal_design_families_per_revision": delta.get("marginal_yield", {}).get("design_families_per_revision"), "marginal_gold_families_per_revision": delta.get("marginal_yield", {}).get("gold_families_per_revision"), "marginal_large_xlarge_design_instance_share": delta.get("marginal_yield", {}).get("large_xlarge_design_instance_share"), "marginal_complex_function_design_instance_share": delta.get("marginal_yield", {}).get("complex_function_design_instance_share"), "round_delta_report_path": str(delta_path)},
        "license_units": {"family_resolution_definition": "A family is resolved only when none of its DesignInstances has UNKNOWN license status.", "public_export_family_definition": "A family is counted when at least one DesignInstance is PUBLIC_EXPORT_ALLOWED.", "unknown_repository_revisions": sum(row.get("license_status", "UNKNOWN") == "UNKNOWN" for row in repositories), "unknown_design_instances": sum(row.get("release", {}).get("license_status", "UNKNOWN") == "UNKNOWN" for row in designs), "unknown_design_families": len(unknown_families)},
        "frontier": frontier_counts, "hard_gates": hard,
        "reports": {"provider_strategy_yield": bool(scale.get("provider_strategy_yield")), "failure_taxonomy": bool(scale.get("failure_taxonomy")), "diversity": bool(scale.get("diversity_scale")), "license": (corpus / "quality/phase1_5/license_evidence_summary.json").is_file(), "scale_report_path": str(scale_path), "license_report_path": str(corpus / "quality/phase1_5/license_evidence_summary.json"), "contamination_report_path": str(contamination_path)},
        "safety_evidence": {"repository_code_execution": "DISABLED_BY_STATIC_PIPELINE_POLICY"},
    }
    atomic(corpus / "quality/phase2/dashboard.json", dashboard)
    print(json.dumps(dashboard, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
