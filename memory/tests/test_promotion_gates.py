"""Promotion receipts distinguish unestablished gates from measured failures."""
from __future__ import annotations

from tehm.lifecycle.promotion_gates import (
    evaluate_capability_promotion_gates,
    evaluate_promotion_gates,
)


def test_empty_rule_authority_reports_six_unestablished_gates():
    result = evaluate_promotion_gates({}, strict=True)
    assert result["eligible"] is False
    assert result["all_gates_established"] is False
    assert result["not_established"] == [
        "rollback_verified", "registry_verified", "obligation_coverage",
        "cross_lineage_te", "harmful_rate", "conformal_coverage"]
    assert result["failed"] == []
    assert set(result["gate_status"].values()) == {"NOT_ESTABLISHED"}


def test_rule_authority_reports_failed_gate_separately_from_missing():
    result = evaluate_promotion_gates(
        {"rollback_verified": True, "registry_verified": True,
         "obligation_coverage": 1.0, "cross_lineage_te": 1.0,
         "harmful_rate": 0.25}, strict=True)
    assert result["gate_status"]["harmful_rate"] == "FAIL"
    assert "harmful_rate" in result["failed"]
    assert "harmful_rate" not in result["missing"]
    assert result["gate_status"]["conformal_coverage"] == "NOT_ESTABLISHED"


def test_empty_capability_authority_reports_unestablished_c1_c8():
    result = evaluate_capability_promotion_gates({}, strict=True)
    assert result["eligible"] is False
    assert result["not_established"] == [f"C{i}" for i in range(1, 9)]
    assert result["failed"] == []


def test_orfs_authority_receipt_legacy_fixture_preserves_unestablished_gate_state(tmp_path):
    """Legacy fixture overrides preserve NOT_ESTABLISHED instead of becoming False."""
    from scripts.build_orfs_authority_receipt import build_receipt
    from tehm.batch_lane import write_external_observations
    from tehm import db as tehm_db

    observations = tmp_path / "observations.jsonl"
    write_external_observations(observations, [{
        "case_id": "support", "classification": "INCOMPLETE_EXTERNAL_ONLY",
        "learner_eligible": False,
    }])
    staging = tmp_path / "staging.sqlite"
    canonical = tmp_path / "canonical.sqlite"
    for path in (staging, canonical):
        conn = tehm_db.connect(path)
        tehm_db.ensure_schema(conn)
        conn.close()

    receipt = build_receipt(
        observations=observations, staging_db=staging,
        canonical_db=canonical, campaign_id="campaign",
        rule_id="rule", target_scope="route", case_ids=["support"],
        gate_inputs={"obligation_coverage": 1.0})
    evaluation = receipt["gate_evaluation"]
    assert evaluation["gate_status"]["obligation_coverage"] == "PASS"
    assert set(evaluation["not_established"]) == {
        "rollback_verified", "registry_verified", "cross_lineage_te",
        "harmful_rate", "conformal_coverage"}
    assert evaluation["failed"] == []
    assert receipt["decision"] == "DENY_CANONICAL_IMPORT"


def test_orfs_authority_receipt_derives_observation_measurements(tmp_path):
    from scripts.build_orfs_authority_receipt import build_receipt
    from tehm.batch_lane import write_external_observations
    from tehm import db as tehm_db

    observations = tmp_path / "observations.jsonl"
    write_external_observations(observations, [{
        "case_id": "support-uart", "lineage_id": "uart-lineage",
        "split": "support", "classification": "ELIGIBLE_POSITIVE",
        "learner_eligible": True,
        "record": {
            "verification": {"obligation_coverage": 1.0},
            "observation_delta": {"utility_verdict": "HARMFUL"},
        },
    }])
    staging = tmp_path / "staging.sqlite"
    canonical = tmp_path / "canonical.sqlite"
    for path in (staging, canonical):
        conn = tehm_db.connect(path)
        tehm_db.ensure_schema(conn)
        conn.close()

    receipt = build_receipt(
        observations=observations, staging_db=staging,
        canonical_db=canonical, campaign_id="campaign",
        rule_id="rule", target_scope="route", case_ids=["support-uart"])
    evaluation = receipt["gate_evaluation"]
    assert evaluation["gate_status"]["obligation_coverage"] == "PASS"
    assert evaluation["gate_status"]["harmful_rate"] == "FAIL"
    assert evaluation["gate_status"]["cross_lineage_te"] == "FAIL"
    assert set(evaluation["not_established"]) == {
        "rollback_verified", "registry_verified", "conformal_coverage"}
    assert receipt["gate_derivation"]["source"] == "external_observation_receipts"


def test_orfs_authority_receipt_treats_pareto_safe_as_measured_non_harmful(tmp_path):
    from scripts.build_orfs_authority_receipt import build_receipt
    from tehm.batch_lane import write_external_observations
    from tehm import db as tehm_db

    observations = tmp_path / "observations.jsonl"
    write_external_observations(observations, [{
        "case_id": "support-safe", "lineage_id": "safe-lineage",
        "split": "support", "classification": "ELIGIBLE_POSITIVE",
        "learner_eligible": True,
        "record": {
            "verification": {"obligation_coverage": 1.0},
            "observation_delta": {"utility_verdict": "PARETO_SAFE"},
        },
    }])
    staging = tmp_path / "staging.sqlite"
    canonical = tmp_path / "canonical.sqlite"
    for path in (staging, canonical):
        conn = tehm_db.connect(path)
        tehm_db.ensure_schema(conn)
        conn.close()

    receipt = build_receipt(
        observations=observations, staging_db=staging,
        canonical_db=canonical, campaign_id="campaign",
        rule_id="rule", target_scope="route", case_ids=["support-safe"])
    evaluation = receipt["gate_evaluation"]
    assert evaluation["gate_status"]["harmful_rate"] == "PASS"
    assert receipt["gate_derivation"]["utility_verdicts"] == ["PARETO_SAFE"]


def test_orfs_authority_receipt_excludes_non_support_rows_from_learner_gates(tmp_path):
    from scripts.build_orfs_authority_receipt import build_receipt
    from tehm.batch_lane import write_external_observations
    from tehm import db as tehm_db

    observations = tmp_path / "observations.jsonl"
    write_external_observations(observations, [{
        "case_id": "heldout-positive", "lineage_id": "heldout-lineage",
        "split": "heldout", "classification": "ELIGIBLE_POSITIVE",
        "learner_eligible": True,
        "record": {
            "verification": {"obligation_coverage": 1.0},
            "observation_delta": {"utility_verdict": "PARETO_SAFE"},
        },
    }])
    staging = tmp_path / "staging.sqlite"
    canonical = tmp_path / "canonical.sqlite"
    for path in (staging, canonical):
        conn = tehm_db.connect(path)
        tehm_db.ensure_schema(conn)
        conn.close()

    receipt = build_receipt(
        observations=observations, staging_db=staging,
        canonical_db=canonical, campaign_id="campaign",
        rule_id="rule", target_scope="route", case_ids=["heldout-positive"])
    evaluation = receipt["gate_evaluation"]
    assert evaluation["not_established"] == [
        "rollback_verified", "registry_verified", "obligation_coverage",
        "cross_lineage_te", "harmful_rate", "conformal_coverage"]
    assert receipt["gate_derivation"]["eligible_positive_case_ids"] == []
