"""Revision2 P15 reason-aware NO_SKILL calibration tests."""
from __future__ import annotations

import pytest

from tehm.evaluation.no_skill_calibration import (
    NoSkillCalibrationError, NoSkillCalibrationReceipt, NoSkillCalibrationSample,
    evaluate_no_skill_calibration, wilson_interval,
)
from tehm.retrieval.production_gate import evaluate_production_gate


def _samples(*, confidence=True, strata=True):
    rows = []
    reasons = ("NO_MATCH", "STATE_SHIFT", "RISK")
    for index in range(6):
        reason = reasons[index % len(reasons)]
        kwargs = {
            "confidence": 0.9 if confidence else None,
            "strata": {"mechanism_family": "density", "design": "aes",
                        "platform": "sky130", "flow_regime": "route",
                        "model_identity": "oracle-v1", "state_shift_dimension": "none"}
            if strata else {},
        }
        rows.append(NoSkillCalibrationSample(
            case_id=f"no-skill-{index}", predicted_decision="NO_SKILL",
            expected_decision="NO_SKILL", predicted_reason=reason,
            expected_reason=reason, **kwargs))
    for index in range(14):
        kwargs = {
            "confidence": 0.9 if confidence else None,
            "strata": {"mechanism_family": "density", "design": "aes",
                        "platform": "sky130", "flow_regime": "route",
                        "model_identity": "oracle-v1", "state_shift_dimension": "none"}
            if strata else {},
        }
        rows.append(NoSkillCalibrationSample(
            case_id=f"use-memory-{index}", predicted_decision="USE_MEMORY",
            expected_decision="USE_MEMORY", **kwargs))
    return rows


def test_wilson_interval_is_explicit_and_unknown_safe():
    interval = wilson_interval(0, 5)
    assert interval["point"] == 0.0
    assert interval["upper"] > 0.0
    assert wilson_interval(0, 0)["lower"] is None
    with pytest.raises(NoSkillCalibrationError):
        wilson_interval(1, 0)
    with pytest.raises(NoSkillCalibrationError, match="only 95%"):
        wilson_interval(1, 2, confidence=0.90)


def test_reason_aware_report_has_two_levels_strata_and_replay_digest():
    receipt = evaluate_no_skill_calibration(_samples())
    assert receipt.eligible is True
    assert receipt.status == "PASS"
    assert receipt.overall["precision"]["lower"] < 1.0
    assert receipt.overall["recall"]["lower"] < 1.0
    assert set(receipt.per_reason) == {"NO_MATCH", "STATE_SHIFT", "RISK"}
    assert receipt.per_reason["STATE_SHIFT"]["recall"]["point"] == 1.0
    assert receipt.reason_confusion_matrix["NO_MATCH"]["NO_MATCH"] == 2
    assert receipt.strata["platform"]["sky130"]["cases"] == 20
    assert receipt.strata_coverage["platform"] == 1.0
    assert receipt.calibration_error == pytest.approx(0.1)
    payload = {**receipt.to_dict(), "receipt_digest": receipt.receipt_digest}
    assert NoSkillCalibrationReceipt.from_dict(payload) == receipt


def test_missing_reason_denominator_or_confidence_is_not_established():
    missing_reason = _samples()[:-1]
    # Remove every RISK oracle label while retaining typed validity by moving
    # those cases to the already represented NO_MATCH bucket.
    for row in missing_reason:
        if row.expected_reason == "RISK":
            index = missing_reason.index(row)
            missing_reason[index] = NoSkillCalibrationSample(
                case_id=row.case_id, predicted_decision="NO_SKILL",
                expected_decision="NO_SKILL", predicted_reason="NO_MATCH",
                expected_reason="NO_MATCH", confidence=0.9)
    report = evaluate_no_skill_calibration(missing_reason, minimum_reason_cases=3)
    assert report.eligible is False
    assert "minimum_reason_cases:RISK" in report.missing

    no_confidence = evaluate_no_skill_calibration(_samples(confidence=False))
    assert no_confidence.status == "NOT_ESTABLISHED"
    assert "confidence_coverage" in no_confidence.missing
    assert no_confidence.calibration_error is None


def test_sample_contract_rejects_untagged_no_skill_and_duplicate_ids():
    with pytest.raises(NoSkillCalibrationError, match="requires a reason"):
        NoSkillCalibrationSample("case", "NO_SKILL", "NO_SKILL")
    rows = _samples()
    rows[-1] = NoSkillCalibrationSample(
        case_id=rows[0].case_id, predicted_decision="USE_MEMORY",
        expected_decision="USE_MEMORY", confidence=0.8)
    with pytest.raises(NoSkillCalibrationError, match="duplicate case"):
        evaluate_no_skill_calibration(rows)


def test_production_gate_uses_structured_lower_ci_and_mir_upper_ci():
    report = evaluate_no_skill_calibration(_samples())
    evidence = {
        "baseline_harmful_activation_rate": 0.4,
        "memory_harmful_activation_rate": 0.1,
        "no_skill_calibration": report.to_dict(),
        "paired_cases": 100,
        "memory_interference_cases": 0,
        "memory_interference_rate": 0.0,
        "candidate_diversity": 0.8,
        "authority_verified": True,
        "authority_receipt_id": "authority",
        "authority_receipt_digest": "sha256:authority",
        "rollback_verified": True,
        "rollback_receipt_id": "rollback",
        "rollback_receipt_digest": "sha256:rollback",
        "evidence_refs": [{"id": "p15", "sha256": "sha256:p15"}],
    }
    receipt = evaluate_production_gate(
        evidence, max_memory_interference_rate=0.05,
        min_no_skill_precision=0.60, min_no_skill_recall=0.60)
    assert receipt.gate_status["no_skill_calibration"] == "PASS"
    assert receipt.gate_status["candidate_pool"] == "PASS"
    assert receipt.metrics["no_skill_precision_lower_ci"] == pytest.approx(
        report.overall["precision"]["lower"])
    assert receipt.metrics["memory_interference_ci"]["upper"] < 0.05


def test_production_gate_rejects_structured_report_without_p15_evidence():
    report = evaluate_no_skill_calibration(_samples(confidence=False))
    evidence = {
        "no_skill_calibration": report.to_dict(),
        "evidence_refs": [{"id": "p15", "sha256": "sha256:p15"}],
    }
    receipt = evaluate_production_gate(evidence)
    assert receipt.gate_status["no_skill_calibration"] == "NOT_ESTABLISHED"
