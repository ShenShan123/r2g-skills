"""Ordinary production ORFS pair capture; A/B evidence stays separate."""
from __future__ import annotations

import json

import pytest

from tehm.adapters.orfs_pair import build_orfs_pair_record
from tehm.canonical.capture import capture


def _project(root, name, *, util, rc, failed_stage=None, route=None, drc=None, ppa=None):
    project = root / name
    (project / "constraints").mkdir(parents=True)
    (project / "reports").mkdir()
    run = project / "backend" / f"RUN_{name}"
    run.mkdir(parents=True)
    (project / "constraints" / "config.mk").write_text(
        f"export DESIGN_NAME = demo\nexport PLATFORM = sky130hd\n"
        f"export CORE_UTILIZATION = {util}\n")
    (run / "run-meta.json").write_text(json.dumps({
        "run_tag": f"RUN_{name}", "make_status": rc,
        "config_mk": str(project / "constraints/config.mk")}))
    stages = [{"stage": "synth", "status": 0}]
    if failed_stage:
        stages.append({"stage": failed_stage, "status": rc})
    else:
        stages += [{"stage": "route", "status": 0}, {"stage": "finish", "status": 0}]
    (run / "stage_log.jsonl").write_text(
        "".join(json.dumps(x) + "\n" for x in stages))
    for key, value in (("route", route), ("drc", drc), ("ppa", ppa)):
        if value is not None:
            (project / "reports" / f"{key}.json").write_text(json.dumps(value))
    return project


def test_real_pair_builds_complete_positive_transition(tmp_tehm, tmp_path):
    conn, store, _ = tmp_tehm
    before = _project(tmp_path, "dense", util=75, rc=2, failed_stage="cts")
    after = _project(
        tmp_path, "safe", util=20, rc=0,
        route={"status": "complete"}, drc={"status": "clean"},
        ppa={"summary": {"timing": {"setup_wns": -0.4}}})
    record = build_orfs_pair_record(
        before, after, lineage_id="orfs:gcd",
        config_edits={"CORE_UTILIZATION": "20"})
    receipt = capture(conn, store, record)
    assert receipt.outcome == "PASS"
    assert record.observation_delta["original_failure"] == "REMOVED"
    assert record.verification["verdict"] == "PASS"
    assert record.observation_delta["experiment_kind"] == "REPAIR"
    assert record.episode["terminal_status"] == "VERIFIED_REPAIR"
    assert record.verification["evidence_refs"]
    assert conn.execute("SELECT COUNT(*) FROM tehm_transitions").fetchone()[0] == 1


def test_clean_before_after_is_observation_not_verified_repair(tmp_path):
    before = _project(tmp_path, "clean_before", util=50, rc=0,
                      route={"status": "complete"}, drc={"status": "clean"},
                      ppa={"summary": {"timing": {"setup_wns": 0.1},
                                        "area": {"design_area_um2": 100.0}}})
    after = _project(tmp_path, "clean_after", util=40, rc=0,
                     route={"status": "complete"}, drc={"status": "clean"},
                     ppa={"summary": {"timing": {"setup_wns": 0.2},
                                       "area": {"design_area_um2": 100.0}}})
    record = build_orfs_pair_record(
        before, after, lineage_id="orfs:clean", config_edits={"CORE_UTILIZATION": "40"})
    assert record.observation_delta["original_failure"] == "UNKNOWN"
    assert record.observation_delta["experiment_kind"] == "OBSERVATION"
    assert record.observation_delta["utility_verdict"] == "PARETO_SAFE"
    assert record.episode["terminal_status"] == "VERIFIED_OBSERVATION"


def test_semantic_oracle_distinguishes_complete_physical_pair(tmp_path):
    """A source-bound semantic failure can coexist with physical PASS arms."""
    before = _project(tmp_path, "semantic_before", util=70, rc=0,
                      route={"status": "clean"}, drc={"status": "clean"},
                      ppa={"summary": {"timing": {"setup_wns": 0.1}}})
    after = _project(tmp_path, "semantic_after", util=60, rc=0,
                     route={"status": "clean"}, drc={"status": "clean"},
                     ppa={"summary": {"timing": {"setup_wns": 0.1}}})
    spec = {
        "version": "orfs-semantic-oracle-v1",
        "kind": "config_numeric_bound",
        "config_key": "CORE_UTILIZATION",
        "operator": "le",
        "threshold": 65,
    }
    record = build_orfs_pair_record(
        before, after, lineage_id="orfs:semantic",
        config_edits={"CORE_UTILIZATION": "60"}, semantic_oracle=spec)
    assert record.observation_delta["original_failure"] == "REMOVED"
    assert record.observation_delta["experiment_kind"] == "REPAIR"
    assert record.verification["verdict"] == "PASS"
    assert record.verification["semantic_oracle"]["before"]["verdict"] == "FAIL"
    assert record.verification["semantic_oracle"]["after"]["verdict"] == "PASS"


def test_semantic_presence_oracle_binds_intervention_to_config(tmp_path):
    """A routing intervention is a source-bound semantic fail-to-pass witness."""
    before = _project(tmp_path, "routing_before", util=50, rc=0,
                      route={"status": "clean"}, drc={"status": "clean"},
                      ppa={"summary": {"timing": {"setup_wns": 0.1}}})
    after = _project(tmp_path, "routing_after", util=50, rc=0,
                     route={"status": "clean"}, drc={"status": "clean"},
                     ppa={"summary": {"timing": {"setup_wns": 0.1}}})
    (after / "constraints" / "config.mk").write_text(
        (after / "constraints" / "config.mk").read_text()
        + "export ROUTING_LAYER_ADJUSTMENT = 0.05\n")
    spec = {
        "version": "orfs-semantic-oracle-v1",
        "kind": "config_presence",
        "config_key": "ROUTING_LAYER_ADJUSTMENT",
        "expected_present": True,
    }
    record = build_orfs_pair_record(
        before, after, lineage_id="orfs:routing-presence",
        config_edits={"ROUTING_LAYER_ADJUSTMENT": "0.05"},
        semantic_oracle=spec)
    assert record.observation_delta["original_failure"] == "REMOVED"
    assert record.verification["semantic_oracle"]["before"]["verdict"] == "FAIL"
    assert record.verification["semantic_oracle"]["after"]["verdict"] == "PASS"


def test_semantic_presence_oracle_rejects_non_boolean_contract(tmp_path):
    before = _project(tmp_path, "presence_before", util=50, rc=0,
                      route={"status": "clean"}, drc={"status": "clean"},
                      ppa={"summary": {"timing": {"setup_wns": 0.1}}})
    after = _project(tmp_path, "presence_after", util=50, rc=0,
                     route={"status": "clean"}, drc={"status": "clean"},
                     ppa={"summary": {"timing": {"setup_wns": 0.1}}})
    with pytest.raises(ValueError, match="expected_present"):
        build_orfs_pair_record(
            before, after, lineage_id="orfs:routing-presence",
            config_edits={}, semantic_oracle={
                "version": "orfs-semantic-oracle-v1",
                "kind": "config_presence",
                "config_key": "ROUTING_LAYER_ADJUSTMENT",
                "expected_present": "yes",
            })


def test_pair_refuses_missing_production_evidence(tmp_path):
    before = tmp_path / "before"
    after = tmp_path / "after"
    (before / "constraints").mkdir(parents=True)
    (after / "constraints").mkdir(parents=True)
    with pytest.raises(ValueError, match="production ORFS"):
        build_orfs_pair_record(before, after, lineage_id="x",
                               config_edits={"CORE_UTILIZATION": "20"})


def test_unknown_after_is_not_promoted_to_pass(tmp_path):
    before = _project(tmp_path, "dense", util=75, rc=2, failed_stage="route")
    after = _project(tmp_path, "unknown", util=20, rc=0)
    # run-meta claims rc=0 and therefore the adapter may derive route completion
    # from the complete stage log: that is real production evidence, not guessing.
    record = build_orfs_pair_record(before, after, lineage_id="x",
                                    config_edits={"CORE_UTILIZATION": "20"})
    assert record.verification["verdict"] == "PASS"


def test_diversity_flow_rc_receipt_recovers_failed_run_meta(tmp_path):
    before = _project(tmp_path, "dense", util=75, rc=2, failed_stage="place")
    after = _project(tmp_path, "safe", util=20, rc=0,
                     route={"status": "clean"})
    (before / "backend" / "RUN_dense" / "run-meta.json").unlink()
    (before / "campaign-run-receipt.json").write_text(json.dumps({
        "flow_rc": 2, "completed": True,
        "config_sha256": "abc", "log_sha256": "def"}))
    record = build_orfs_pair_record(
        before, after, lineage_id="orfs:gcd",
        config_edits={"CORE_UTILIZATION": "20"})
    assert record.before["failure_signature"]["predicates"]["flow_returncode"] == 2
    assert record.verification["verdict"] == "PASS"
