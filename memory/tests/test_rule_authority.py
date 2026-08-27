"""Database-bound rule promotion authority and evidence firewall."""
from __future__ import annotations

import json

import pytest

from tehm.canonical.capture import ExecutionRecord, capture
from tehm.crystallization.build_rules import crystallize_all
from tehm.lifecycle import (
    apply_production_trial_verdict, enter_shadow, get_status, promote_rule,
    record_rule_authority, rule_content_digest, set_status,
    verify_rule_authority,
)
from tehm.lifecycle.trial_adapter import record_external_trial


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
