#!/usr/bin/env python3
"""Aggregate Phase-1.5 quality, recovery, mapping, and readiness evidence."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any


SCHEMA = "rtl_phase1_5_summary_v1"


def load(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "UNAVAILABLE", "path": str(path)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, default=Path.home() / "work/data/rtl_corpus")
    args = parser.parse_args()
    quality = args.corpus_root / "quality/phase1_5"
    failure = load(quality / "failure_audit_summary.json")
    r1 = load(quality / "repair_exercise_summary.json")
    r23 = load(quality / "repair_r2_r3_summary.json")
    mapping = load(quality / "mapping_cohort_summary.json")
    mapping_retry = load(quality / "mapping_retry_summary.json")
    mixed_vhdl = load(quality / "mixed_language_vhdl_audit_summary.json")
    ontology = load(quality / "function_ontology_summary.json")
    license_report = load(quality / "license_evidence_summary.json")
    counter = load(quality / "candidate_counter_audit.json")
    contamination = load(quality / "benchmark_contamination_audit.json")
    final_repair = load(quality / "repair_final_adjudication_summary.json")
    catalog = load(args.corpus_root / "benchmark_registry/registry_catalog.json")
    profile_id = catalog.get("active_profile")
    profile = load(args.corpus_root / f"benchmark_registry/profiles/{profile_id}.json") if profile_id else {"ready": False}
    db = sqlite3.connect(args.corpus_root / "state/frontier.sqlite")
    scheduler_row = db.execute("SELECT value_json FROM scheduler_state WHERE key='scheduler_config'").fetchone()
    db.close()
    scheduler = json.loads(scheduler_row[0]) if scheduler_row else {"status": "UNAVAILABLE"}
    designs = []
    designs_path = args.corpus_root / "manifests/all_designs.jsonl"
    if designs_path.is_file():
        designs = [json.loads(line) for line in designs_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    repair_recovered = int(r1.get("recovered_candidates", 0)) + int(r23.get("recovered_candidates", 0))
    repair_attempted = int(r1.get("completed", 0)) + int(r23.get("completed", 0))
    repair_published = int(final_repair.get("dispositions", {}).get("PUBLISH", 0))
    repair_quarantined = int(final_repair.get("dispositions", {}).get("QUARANTINE", 0))
    blockers: list[str] = []
    if int(failure.get("sampled", 0)) < 200:
        blockers.append("failure_adjudication_cohort_incomplete")
    if not final_repair.get("all_candidates_terminal"):
        blockers.append("recovered_candidate_final_adjudication_incomplete")
    if int(mapping.get("completed", 0)) < 100:
        blockers.append("mapping_cohort_incomplete")
    if not mapping_retry.get("bounded_second_pass_complete"):
        blockers.append("mapping_failure_second_pass_incomplete")
    if int(mixed_vhdl.get("sampled", 0)) < 20:
        blockers.append("mixed_language_vhdl_audit_incomplete")
    if not profile.get("ready"):
        blockers.append("benchmark_profile_incomplete")
    if not contamination.get("registry_ready") or not contamination.get("apply"):
        blockers.append("benchmark_profile_audit_not_applied")
    if int(counter.get("counter_gap_after", 0)):
        blockers.append("candidate_counter_gap_nonzero")
    summary = {
        "schema": SCHEMA, "status": "COMPLETE" if not blockers else "IN_PROGRESS", "blockers": blockers,
        "phases": {"phase_1": "COMPLETE", "phase_1_5": "COMPLETE" if not blockers else "IN_PROGRESS", "phase_2": "ACTIVE"},
        "failure_audit": failure, "repair_exercises": {
            "r1": r1, "r2_r3": r23, "attempted": repair_attempted,
            "recovered_candidates": repair_recovered,
            "candidate_recovery_rate": round(repair_recovered / max(1, repair_attempted), 6),
            "published": repair_published,
            "publication_success_rate": round(repair_published / max(1, repair_attempted), 6),
            "quarantined": repair_quarantined,
            "recovered_not_published": repair_recovered - repair_published,
        },
        "repair_final_adjudication": final_repair,
        "mapping_cohort": mapping, "mapping_retry": mapping_retry, "mixed_language_vhdl_audit": mixed_vhdl,
        "functional_ontology": ontology, "license_evidence": license_report,
        "candidate_counter_audit": counter, "benchmark_registry": catalog, "benchmark_profile": profile,
        "contamination_audit": {key: value for key, value in contamination.items() if key != "matches"},
        "scheduler": scheduler,
        "gold_premium": {"contamination_gate": "UNLOCKED", "automatic_grant": False, "subject_to_normal_quality_tier_gates": True},
        "corpus_after_phase1_5": {
            "design_instances": len(designs), "design_families": len({row.get("family_id") for row in designs}),
            "split_groups": len({row.get("split_group_id") for row in designs}),
            "training_tiers": dict(Counter(row.get("quality", {}).get("training_tier", "UNKNOWN") for row in designs)),
            "gold_families": len({row.get("family_id") for row in designs if row.get("quality", {}).get("training_tier") == "TRAINING_GOLD"}),
        },
    }
    target = quality / "phase1_5_summary.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "schema": SCHEMA, "status": summary["status"], "blockers": blockers,
        "triage_suspected_recoverable_rate": failure.get("triage_suspected_recoverable_rate"),
        "repair_candidates_recovered_not_published": repair_recovered - repair_published,
        "observed_candidate_recovery_rate": round(repair_recovered / max(1, repair_attempted), 6),
        "publication_success_rate": round(repair_published / max(1, repair_attempted), 6),
        "mapping_completed": mapping.get("completed"),
        "initial_mapping_pass_rate": mapping.get("initial_mapping_pass_rate"),
        "final_mapping_passes": mapping.get("final_mapping_passes"),
        "final_mapping_pass_rate": mapping.get("final_mapping_pass_rate"),
        "misc_ip": ontology.get("misc_ip"), "license_transitions": license_report.get("repository_transitions_cumulative", license_report.get("repository_transitions")),
        "counter_gap_after": counter.get("counter_gap_after"), "benchmark_profile": profile_id,
        "benchmark_registry_ready": profile.get("ready"),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
