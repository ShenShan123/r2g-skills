"""CLI-level replay of the DB-bound rule-authority receipt."""
from __future__ import annotations

from test_rule_authority import _candidate_with_trial, _full_evidence

from tehm import db as tehm_db
from tehm.lifecycle import record_rule_authority
from scripts.replay_rule_authority import replay


def test_replay_rule_authority_rederives_all_gates(tmp_tehm, sample_record_dict):
    conn, rule_id, status_version, trial_id = _candidate_with_trial(
        tmp_tehm, sample_record_dict)
    receipt = record_rule_authority(
        conn, rule_id=rule_id, target_scope="drc",
        evidence=_full_evidence(conn, rule_id), trial_id=trial_id,
        expected_status_version=status_version)
    database = tmp_tehm[2] / "tehm.sqlite"
    conn.close()

    report = replay(database, authority_receipt_id=receipt.authority_receipt_id)

    assert report["eligible"] is True
    assert report["decision"] == "ALLOW_AUTHORITY_REVIEW"
    assert report["all_gates_established"] is True
    assert all(value == "PASS" for value in report["gate_status"].values())
    assert report["database_unchanged"] is True
    assert report["promotion_attempted"] is False
    assert report["canonical_memory_mutation"] == "none"


def test_replay_rule_authority_rejects_weak_storage_boolean(
        tmp_tehm, sample_record_dict):
    conn, rule_id, status_version, trial_id = _candidate_with_trial(
        tmp_tehm, sample_record_dict)
    receipt = record_rule_authority(
        conn, rule_id=rule_id, target_scope="drc",
        evidence=_full_evidence(conn, rule_id), trial_id=trial_id,
        expected_status_version=status_version)
    database = tmp_tehm[2] / "tehm.sqlite"
    conn.close()
    tamper = tehm_db.connect(database)
    tamper.execute("PRAGMA ignore_check_constraints=ON")
    tamper.execute(
        "UPDATE tehm_rule_authority_receipts SET eligible='false' "
        "WHERE authority_receipt_id=?", (receipt.authority_receipt_id,))
    tamper.commit()
    tamper.close()

    report = replay(database, authority_receipt_id=receipt.authority_receipt_id)

    assert report["eligible"] is False
    assert report["decision"] == "DENY_CANONICAL_IMPORT"
    assert set(report["gate_status"].values()) == {"NOT_ESTABLISHED"}
    assert report["authority_replay_status"] == "FAIL"
    assert "eligible_malformed" in report["reasons"]
    assert report["database_unchanged"] is True
