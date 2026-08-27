"""Real-ORFS staging evidence enters only the v4 causal shadow lane."""
from __future__ import annotations

import sqlite3
import json
from tehm.causal.orfs import _control_record

from tehm.canonical.capture import ExecutionRecord, capture
from tehm.causal.orfs import (
    build_orfs_causal_shadow, build_orfs_controlled_replication)
from tehm.causal.replication import evaluate_replicated_effect
from tehm.causal.authority import evaluate_causal_rule_evidence


def _record(lineage: str) -> ExecutionRecord:
    return ExecutionRecord(
        record_id=f"orfs-shadow:{lineage}",
        domain="flow.signoff",
        project_id=lineage,
        design_id=lineage,
        lineage_id=lineage,
        repository_ref=f"orfs-source:{lineage}",
        before={
            "config": {"DESIGN_NAME": lineage, "PLATFORM": "sky130hs",
                       "CORE_UTILIZATION": "50"},
            "reports": {"route": {"status": "fail"}},
            "failure_signature": {"check": "route", "class": "route"},
        },
        action={
            "domain": "flow.CONFIG_DELTA",
            "transformation_family": "DENSITY_RELIEF",
            "payload": {"config_edits": {"CORE_UTILIZATION": "40"},
                        "rerun_from": "floorplan", "recheck": "route"},
        },
        after={
            "config": {"DESIGN_NAME": lineage, "PLATFORM": "sky130hs",
                       "CORE_UTILIZATION": "40"},
            "reports": {"route": {"status": "clean"}},
        },
        observation_delta={
            "original_failure": "REMOVED", "first_divergence": {"before": 1, "after": 0},
            "failing_tests": {"before": 1, "after": 0},
            "created_regressions": [], "newly_observed_failures": [],
            "experiment_kind": "REPAIR", "utility_verdict": "PARETO_SAFE",
        },
        verification={
            "verdict": "PASS", "oracle_type": "TARGET_TEST",
            "scope": "signoff:route", "confidence_tier": "T",
            "obligation_coverage": 1.0,
            "evidence_refs": [f"orfs:{lineage}:before", f"orfs:{lineage}:after"],
            "tool_versions": {"orfs": "test"}, "oracle_complete": True,
        },
        episode={"episode_id": f"episode:{lineage}",
                 "mechanism_family": "DENSITY_RELIEF",
                 "lineage_id": lineage, "terminal_status": "VERIFIED_REPAIR"},
    )


def test_orfs_staging_becomes_l1_shadow_without_canonical_mutation(tmp_tehm, tmp_path):
    conn, store, _ = tmp_tehm
    for lineage in ("orfs:a", "orfs:b"):
        receipt = capture(
            conn, store, _record(lineage),
            dataset_campaign_id="orfs-shadow-test", dataset_split="training",
            dataset_learner_eligible=True)
        assert receipt.outcome == "PASS"
    before_transitions = conn.execute(
        "SELECT COUNT(*) FROM tehm_transitions").fetchone()[0]
    source_db = tmp_path / "source.sqlite"
    backup = sqlite3.connect(source_db)
    conn.backup(backup)
    backup.close()
    conn.close()

    report = build_orfs_causal_shadow(
        source_db, campaign_id="orfs-shadow-test",
        output_dir=tmp_path / "derived")
    assert report["schema_version"] == "tehm-v4"
    assert report["fragments"]
    assert len(report["fragments"]) == 2
    assert len(report["paths"]) == 1
    assert report["transition_count_preserved"] is True
    assert report["canonical_memory_mutation"] == "none"
    assert report["replication"][0]["eligible"] is False
    assert report["replication"][0]["evidence_level"] == "L1_EXECUTED_INTERVENTION"
    assert report["rule_evidence"][0]["eligible"] is False
    assert "controlled_intervention_support_missing" in report["rule_evidence"][0]["reason"]
    assert report["source_counts"]["transitions"] == before_transitions


def _completed_orfs_project(root, name, utilization):
    project = root / name
    (project / "constraints").mkdir(parents=True)
    (project / "reports").mkdir()
    run = project / "backend" / f"RUN_{name}"
    run.mkdir(parents=True)
    (project / "constraints" / "config.mk").write_text(
        f"export DESIGN_NAME = {name}\nexport PLATFORM = sky130hs\n"
        f"export CORE_UTILIZATION = {utilization}\n")
    (run / "run-meta.json").write_text(json.dumps({
        "run_tag": f"RUN_{name}", "make_status": 0,
        "config_mk": str(project / "constraints" / "config.mk")}))
    (run / "stage_log.jsonl").write_text(
        json.dumps({"stage": "route", "status": 0}) + "\n")
    for report, payload in {
        "route": {"status": "clean"}, "drc": {"status": "clean"},
        "lvs": {"status": "clean"}, "timing_check": {"tier": "clean"},
        "ppa": {"summary": {"timing": {"setup_wns": 1.0},
                              "area": {"design_area_um2": 100.0}}},
    }.items():
        (project / "reports" / f"{report}.json").write_text(json.dumps(payload))
    # The replication gate requires an auditable execution/run witness for
    # every independent lineage; the adapter derives it from this run tag.
    return project


def test_real_orfs_pairs_upgrade_to_l3_without_promotion(tmp_tehm, tmp_path):
    conn, _, _ = tmp_tehm
    source_db = tmp_path / "source.sqlite"
    destination = sqlite3.connect(source_db)
    conn.backup(destination)
    destination.close()
    conn.close()
    pairs = []
    for design in ("and32", "toggle32"):
        before = _completed_orfs_project(tmp_path, f"{design}_before", 50)
        after = _completed_orfs_project(tmp_path, f"{design}_after", 40)
        pairs.append({
            "before_project": str(before), "after_project": str(after),
            "lineage_id": f"orfs-test:{design}",
            "config_edits": {"CORE_UTILIZATION": "40"},
        })
    report = build_orfs_controlled_replication(
        source_db, pairs=pairs, campaign_id="orfs-l3-test",
        output_dir=tmp_path / "controlled")
    assert report["pair_count"] == 2
    assert all(item["intervention"]["validity_status"] ==
               "VALID_CONTROLLED_PAIR" for item in report["pairs"])
    assert report["path"]["evidence_level"] == "L3_REPLICATED_EFFECT"
    assert report["replication"]["eligible"] is True
    assert report["rule_evidence"]["eligible"] is True
    assert report["promotion_eligible"] is False


def test_control_preserves_an_executed_baseline_failure():
    control = _control_record(_record("orfs:failed-baseline"))
    assert control.verification["verdict"] == "FAIL"
    assert control.observation_delta["original_failure"] == "PRESENT"
    assert control.observation_delta["failing_tests"] == {"before": 1, "after": 1}


def test_l3_replication_requires_distinct_run_witnesses(tmp_tehm, tmp_path):
    conn, _, _ = tmp_tehm
    source_db = tmp_path / "source.sqlite"
    destination = sqlite3.connect(source_db)
    conn.backup(destination)
    destination.close()
    conn.close()
    pairs = []
    for design in ("and32", "toggle32"):
        before = _completed_orfs_project(tmp_path, f"{design}_before", 50)
        after = _completed_orfs_project(tmp_path, f"{design}_after", 40)
        pairs.append({
            "before_project": str(before), "after_project": str(after),
            "lineage_id": f"orfs-test:{design}",
            "config_edits": {"CORE_UTILIZATION": "40"},
        })
    report = build_orfs_controlled_replication(
        source_db, pairs=pairs, campaign_id="orfs-l3-run-witness-test",
        output_dir=tmp_path / "controlled")
    derived = sqlite3.connect(report["derived_db"])
    derived.row_factory = sqlite3.Row
    try:
        # Simulate an old/imported transition whose provenance omitted its
        # run identity.  L2 coverage remains present, but L3 must abstain.
        derived.execute("UPDATE tehm_transitions SET provenance_json='{}'")
        derived.commit()
        receipt = evaluate_replicated_effect(
            derived, report["path"]["path_id"],
            campaign_id="orfs-l3-run-witness-test")
        authority = evaluate_causal_rule_evidence(
            derived, report["path"]["path_id"],
            campaign_id="orfs-l3-run-witness-test",
            required_level="L2_CONTROLLED_INTERVENTION",
            min_lineages=2)
    finally:
        derived.close()
    assert receipt.eligible is False
    assert receipt.reason == "requires_distinct_run_witnesses"
    assert receipt.unique_runs == ()
    assert authority.eligible is False
    assert "replication_witness_incomplete:requires_distinct_run_witnesses" in authority.reason
