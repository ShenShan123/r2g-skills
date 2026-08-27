"""Database-bound rule promotion authority and evidence firewall."""
from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from tehm import db as tehm_db
from tehm.canonical.capture import ExecutionRecord, capture
from tehm.crystallization.build_rules import crystallize_all
from tehm.lifecycle import (
    apply_production_trial_verdict, build_trial_authority_evidence,
    build_external_observation_authority_evidence,
    enter_shadow, get_status, promote_rule, record_rule_authority,
    rule_content_digest, set_status,
    verify_rule_authority,
)
from tehm.activation.pipeline import ActivationRecord
from tehm.activation.update import persist_activation
from tehm.lifecycle.trial_adapter import record_external_trial
from tehm.batch_lane import write_external_observations
from tehm.artifact_store import ArtifactStore


def _candidate_with_trial(tmp_tehm, sample_record_dict):
    conn, store, _ = tmp_tehm
    base = json.loads(json.dumps(sample_record_dict))
    for index in range(3):
        record = json.loads(json.dumps(base))
        record["record_id"] = f"authority_{index}"
        record["lineage_id"] = f"authority_lineage_{index}"
        record["episode"] = {
            "episode_id": f"authority_episode_{index}",
            "lineage_id": record["lineage_id"],
            "step_index": 0,
            "terminal_status": "VERIFIED_REPAIR",
        }
        record["action"]["payload"]["config_edits"] = {
            "PLACE_DENSITY_LB_ADDON": f"0.1{index + 4}"
        }
        record["before"]["config"]["PLACE_DENSITY_LB_ADDON"] = "0.10"
        record["after"]["config"]["PLACE_DENSITY_LB_ADDON"] = f"0.1{index + 4}"
        record["observation_delta"]["first_divergence"]["before"] = 10 + index
        capture(conn, store, ExecutionRecord.from_dict(record))
    rule_id = crystallize_all(conn)[0]["rule_id"]
    enter_shadow(conn, rule_id=rule_id, target_scope="drc")
    set_status(conn, rule_id=rule_id, target_scope="drc", status="candidate")
    status_version = get_status(
        conn, rule_id=rule_id, target_scope="drc")["status_version"]
    trial = record_external_trial(
        conn, rule_id=rule_id, target_scope="drc", verdict="win",
        metrics={"arms_differ": True, "obligation_coverage": 1.0,
                 "created_regressions": []},
        status_version=status_version, trial_uuid="authority-trial",
        arm_a_run_id="a", arm_b_run_id="b")
    return conn, rule_id, status_version, trial["trial_id"]


def _full_evidence(conn, rule_id: str) -> dict:
    digest = rule_content_digest(conn, rule_id)
    return {
        "rollback_verified": [{
            "evidence_id": "rollback-1", "split": "ab", "lineage_id": "l0",
            "verdict": "PASS", "payload": {"verified": True},
        }],
        "registry_verified": [{
            "evidence_id": "registry-1", "split": "ab", "lineage_id": "l0",
            "verdict": "PASS", "payload": {
                "status": "candidate", "status_version": 2,
                "rule_content_digest": digest,
            },
        }],
        "obligation_coverage": [{
            "evidence_id": "obligation-1", "split": "ab", "lineage_id": "l0",
            "verdict": "PASS", "payload": {"obligation_coverage": 1.0},
        }],
        "cross_lineage_te": [
            {"evidence_id": "te-1", "split": "heldout", "lineage_id": "l1",
             "verdict": "PASS", "payload": {"te_pass": True}},
            {"evidence_id": "te-2", "split": "heldout", "lineage_id": "l2",
             "verdict": "PASS", "payload": {"te_pass": True}},
        ],
        "harmful_rate": [
            {"evidence_id": "utility-1", "split": "heldout", "lineage_id": "l1",
             "verdict": "PASS", "payload": {"utility_verdict": "NEUTRAL"}},
            {"evidence_id": "utility-2", "split": "heldout", "lineage_id": "l2",
             "verdict": "PASS", "payload": {"utility_verdict": "PARETO_SAFE"}},
        ],
        "conformal_coverage": [{
            "evidence_id": "calibration-1", "split": "calibration",
            "lineage_id": "cal", "verdict": "PASS",
            "payload": {"covered": 8, "total": 8},
        }],
    }


def test_rule_authority_derives_six_gates_and_promotes(tmp_tehm, sample_record_dict):
    conn, rule_id, version, trial_id = _candidate_with_trial(
        tmp_tehm, sample_record_dict)
    receipt = record_rule_authority(
        conn, rule_id=rule_id, target_scope="drc", evidence=_full_evidence(conn, rule_id),
        trial_id=trial_id, expected_status_version=version)
    assert receipt.eligible is True
    assert all(value == "PASS" for value in receipt.gate_status.values())
    assert verify_rule_authority(conn, receipt)["eligible"] is True
    assert apply_production_trial_verdict(
        conn, rule_id=rule_id, target_scope="drc", verdict="win",
        obligation_coverage=1.0, created_regressions=[], arms_differ=True,
        expected_status_version=version, authority_receipt=receipt) == "promoted"
    assert get_status(conn, rule_id=rule_id, target_scope="drc")["status"] == "promoted"


def test_trial_authority_projection_replays_activation_witnesses(
        tmp_tehm, sample_record_dict):
    conn, rule_id, version, trial_id = _candidate_with_trial(
        tmp_tehm, sample_record_dict)
    rollback = {
        "version": "test-rollback-v1",
        "source_before_digest": "sha256:before",
        "source_after_restore_digest": "sha256:before",
        "verified": True,
    }
    persist_activation(
        conn,
        ActivationRecord(
            activation_id="act-authority-projection",
            rule_id=rule_id, target_state_id="target-authority-projection",
            obligation_coverage=1.0, rollback_receipt=rollback,
            outcome="PASS", verification_status="PASS", trial_uuid="authority-trial"),
    )
    metrics = {
        "arms_differ": True, "obligation_coverage": 1.0,
        "created_regressions": [],
        "pairs": [{
            "activation_id": "act-authority-projection",
            "subject_lineage": "authority-lineage",
            "repeat": 1, "obligation_coverage": 1.0,
            "created_regressions": [], "rollback_receipt": rollback,
        }],
    }
    conn.execute("UPDATE tehm_trials SET metrics_json=? WHERE trial_id=?",
                 (json.dumps(metrics, sort_keys=True), trial_id))
    conn.commit()
    evidence = build_trial_authority_evidence(
        conn, trial_id=trial_id, rule_id=rule_id, target_scope="drc")
    assert evidence["rollback_verified"][0]["payload"]["verified"] is True
    assert evidence["obligation_coverage"][0]["payload"]["coverage"] == 1.0
    assert evidence["registry_verified"][0]["verdict"] == "PASS"
    assert evidence["harmful_rate"] == []
    receipt = record_rule_authority(
        conn, rule_id=rule_id, target_scope="drc", evidence=evidence,
        trial_id=trial_id, expected_status_version=version)
    assert receipt.checks["rollback_verified"] is True
    assert receipt.checks["registry_verified"] is True
    assert receipt.checks["obligation_coverage"] is True
    assert receipt.gate_status["harmful_rate"] == "NOT_ESTABLISHED"
    assert receipt.gate_status["cross_lineage_te"] == "NOT_ESTABLISHED"

    # A pair JSON change without the corresponding persisted activation row is
    # not a new measurement; the source-bound projector rejects the mismatch.
    tampered = dict(metrics)
    tampered["pairs"] = [dict(metrics["pairs"][0],
                               rollback_receipt={**rollback, "verified": False})]
    conn.execute("UPDATE tehm_trials SET metrics_json=? WHERE trial_id=?",
                 (json.dumps(tampered, sort_keys=True), trial_id))
    conn.commit()
    with pytest.raises(ValueError, match="rollback_witness_mismatch"):
        build_trial_authority_evidence(
            conn, trial_id=trial_id, rule_id=rule_id, target_scope="drc")


def test_promote_rule_consumes_only_verified_receipt(tmp_tehm, sample_record_dict):
    conn, rule_id, version, trial_id = _candidate_with_trial(
        tmp_tehm, sample_record_dict)
    receipt = record_rule_authority(
        conn, rule_id=rule_id, target_scope="drc", evidence=_full_evidence(conn, rule_id),
        trial_id=trial_id, expected_status_version=version)
    promote_rule(conn, receipt)
    assert get_status(conn, rule_id=rule_id, target_scope="drc")["status"] == "promoted"


def test_strict_trial_rejects_forged_gate_map_without_authority_receipt(
        tmp_tehm, sample_record_dict):
    conn, rule_id, version, _ = _candidate_with_trial(tmp_tehm, sample_record_dict)
    assert apply_production_trial_verdict(
        conn, rule_id=rule_id, target_scope="drc", verdict="win",
        obligation_coverage=1.0, created_regressions=[], arms_differ=True,
        expected_status_version=version,
        promotion_gates={name: True for name in (
            "rollback_verified", "registry_verified", "obligation_coverage",
            "cross_lineage_te", "harmful_rate", "conformal_coverage")}) is None
    assert get_status(conn, rule_id=rule_id, target_scope="drc")["status"] == "candidate"


def test_authority_missing_and_failed_gates_are_distinguished(
        tmp_tehm, sample_record_dict):
    conn, rule_id, version, trial_id = _candidate_with_trial(
        tmp_tehm, sample_record_dict)
    evidence = _full_evidence(conn, rule_id)
    evidence.pop("conformal_coverage")
    evidence["cross_lineage_te"] = [{
        "evidence_id": "te-singleton", "split": "heldout", "lineage_id": "only",
        "verdict": "PASS", "payload": {"te_pass": True},
    }]
    receipt = record_rule_authority(
        conn, rule_id=rule_id, target_scope="drc", evidence=evidence,
        trial_id=trial_id, expected_status_version=version)
    assert receipt.eligible is False
    assert receipt.gate_status["cross_lineage_te"] == "FAIL"
    assert receipt.gate_status["conformal_coverage"] == "NOT_ESTABLISHED"
    assert "cross_lineage_te" in receipt.failed
    assert "conformal_coverage" in receipt.not_established


def test_authority_rechecks_immutable_evidence_and_status(
        tmp_tehm, sample_record_dict):
    conn, rule_id, version, trial_id = _candidate_with_trial(
        tmp_tehm, sample_record_dict)
    receipt = record_rule_authority(
        conn, rule_id=rule_id, target_scope="drc", evidence=_full_evidence(conn, rule_id),
        trial_id=trial_id, expected_status_version=version)
    conn.execute(
        "UPDATE tehm_rule_authority_evidence SET payload_json=? "
        "WHERE rule_id=? AND gate_name='rollback_verified'",
        (json.dumps({"verified": False}), rule_id))
    conn.commit()
    checked = verify_rule_authority(conn, receipt)
    assert checked["eligible"] is False
    assert any("evidence:rollback_verified" in reason
               for reason in checked["reasons"])

    # A receipt cannot be replayed after its candidate status version changes.
    conn.execute(
        "UPDATE tehm_rule_authority_evidence SET payload_json=? "
        "WHERE rule_id=? AND gate_name='rollback_verified'",
        (json.dumps({"verified": True}), rule_id))
    conn.commit()
    set_status(conn, rule_id=rule_id, target_scope="drc", status="quarantined")
    checked = verify_rule_authority(conn, receipt)
    assert checked["eligible"] is False
    assert "candidate_status_not_current" in checked["reasons"]


def test_forbidden_gate_split_fails_closed(tmp_tehm, sample_record_dict):
    conn, rule_id, version, trial_id = _candidate_with_trial(
        tmp_tehm, sample_record_dict)
    evidence = _full_evidence(conn, rule_id)
    evidence["conformal_coverage"][0]["split"] = "training"
    receipt = record_rule_authority(
        conn, rule_id=rule_id, target_scope="drc", evidence=evidence,
        trial_id=trial_id, expected_status_version=version)
    assert receipt.eligible is False
    assert receipt.gate_status["conformal_coverage"] == "FAIL"
    assert "conformal_coverage:invalid_evidence_split" in receipt.reasons


def test_external_observation_projection_binds_staging_transition(
        tmp_tehm, sample_record_dict):
    """External calibration evidence must resolve to one audit transition."""
    authority_conn, _, tmp_root = tmp_tehm
    _, rule_id, status_version, trial_id = _candidate_with_trial(
        tmp_tehm, sample_record_dict)
    staging_db = tmp_root / "external-campaign" / "staging" / "tehm.sqlite"
    staging_db.parent.mkdir(parents=True)
    staging_conn = tehm_db.connect(staging_db)
    tehm_db.ensure_schema(staging_conn)
    record = json.loads(json.dumps(sample_record_dict))
    record["record_id"] = "external-calibration-record"
    record["lineage_id"] = "external-calibration-lineage"
    record["episode"]["episode_id"] = "external-calibration-episode"
    record["episode"]["lineage_id"] = record["lineage_id"]
    record["observation_delta"]["experiment_kind"] = "REPAIR"
    record["observation_delta"]["utility_verdict"] = "NEUTRAL"
    record["verification"]["conformal"] = {
        "covered": 9, "total": 10, "method": "split_conformal_test"
    }
    # Normalise omitted dataclass defaults exactly as capture() will persist.
    normalised = asdict(ExecutionRecord.from_dict(record))
    capture(
        staging_conn, ArtifactStore(staging_db.parent / "artifacts"),
        ExecutionRecord.from_dict(normalised),
        dataset_campaign_id="external-campaign",
        dataset_split="calibration", dataset_learner_eligible=False)
    staging_conn.close()

    observations = tmp_root / "external-campaign" / "observations.jsonl"
    write_external_observations(observations, [{
        "receipt_id": "external-receipt-1",
        "case_id": "external-calibration-case",
        "lineage_id": normalised["lineage_id"],
        "split": "calibration",
        "classification": "ELIGIBLE_POSITIVE",
        "learner_eligible": False,
        "before": {"complete": True},
        "after": {"complete": True},
        "record": normalised,
    }])
    evidence = build_external_observation_authority_evidence(
        authority_conn, observations_path=observations, staging_db=staging_db,
        campaign_id="external-campaign", case_ids=["external-calibration-case"])
    assert evidence["harmful_rate"][0]["payload"]["utility_verdict"] == "NEUTRAL"
    assert evidence["harmful_rate"][0]["payload"]["harmful"] is False
    assert evidence["conformal_coverage"][0]["payload"]["coverage"] == 0.9
    assert evidence["conformal_coverage"][0]["payload"]["transition_id"]
    assert evidence["rollback_verified"] == []
    assert evidence["cross_lineage_te"] == []
    authority_receipt = record_rule_authority(
        authority_conn, rule_id=rule_id, target_scope="drc", evidence=evidence,
        trial_id=trial_id, expected_status_version=status_version)
    assert authority_receipt.gate_status["harmful_rate"] == "PASS"
    assert authority_receipt.gate_status["conformal_coverage"] == "PASS"
    assert authority_receipt.gate_status["rollback_verified"] == "NOT_ESTABLISHED"

    bad_row = json.loads(observations.read_text().splitlines()[0])
    bad_row["learner_eligible"] = True
    write_external_observations(observations, [bad_row])
    with pytest.raises(ValueError, match="learner_firewall_violation"):
        build_external_observation_authority_evidence(
            authority_conn, observations_path=observations, staging_db=staging_db,
            campaign_id="external-campaign", case_ids=["external-calibration-case"])


def test_authority_evidence_and_receipt_write_atomically_on_conflict(
        tmp_tehm, sample_record_dict):
    conn, rule_id, version, trial_id = _candidate_with_trial(
        tmp_tehm, sample_record_dict)
    evidence = _full_evidence(conn, rule_id)
    first = record_rule_authority(
        conn, rule_id=rule_id, target_scope="drc", evidence=evidence,
        trial_id=trial_id, expected_status_version=version)
    conflicting = _full_evidence(conn, rule_id)
    conflicting["rollback_verified"][0]["payload"] = {"verified": False}
    with pytest.raises(ValueError, match="immutable"):
        record_rule_authority(
            conn, rule_id=rule_id, target_scope="drc", evidence=conflicting,
            trial_id=trial_id, expected_status_version=version)
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_rule_authority_receipts").fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_rule_authority_evidence").fetchone()[0] == 8
    assert first.authority_receipt_id
