"""Revision2 P15 reason-aware NO_SKILL calibration tests."""
from __future__ import annotations

from dataclasses import replace

import pytest

from contracts import MemoryRoutingDecision
from tehm.evaluation.no_skill_calibration import (
    NoSkillCalibrationError, NoSkillCalibrationReceipt, NoSkillCalibrationSample,
    build_no_skill_calibration_samples, derive_no_skill_oracle_label,
    evaluate_no_skill_calibration,
    mcnemar_regression_test, wilson_interval,
)
from tehm.evaluation.candidate_executor import execute_paired_candidates
from tehm.retrieval.structured_candidate import StructuredRepairCandidate
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
            "routing_receipt_id": f"routing-{index}",
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
            "routing_receipt_id": f"routing-use-{index}",
        }
        rows.append(NoSkillCalibrationSample(
            case_id=f"use-memory-{index}", predicted_decision="USE_MEMORY",
            expected_decision="USE_MEMORY", **kwargs))
    return rows


def _route(decision, *, reason=None):
    return MemoryRoutingDecision(
        decision=decision, resolved_state_id="state",
        selected_rule_ids=("rule",) if decision in {"APPLY", "CONSIDER"} else (),
        selected_path_ids=("path",) if decision in {"APPLY", "CONSIDER"} else (),
        selected_asset_ids=(), applicability={"status": "APPLICABLE"},
        causal_support={"status": "SUPPORTED"}, risk={}, abstain_reasons=(),
        no_memory_budget=1, memory_budget=1 if decision in {"APPLY", "CONSIDER"} else 0,
        no_skill_reason=reason)


def _oracle_candidate() -> StructuredRepairCandidate:
    return StructuredRepairCandidate(
        candidate_id="calibration-deriver-candidate",
        resolved_state_id="state", knowledge_object_id="knowledge@1",
        causal_path_ids=("path",), asset_id="asset", action_family="AST_REWRITE",
        concrete_action={"domain": "rtl.AST_REWRITE",
                         "transformation_family": "AST_REWRITE",
                         "payload": {"target": "x", "replacement": "x", "count": 1}},
        applicability_receipt_id="app", binding_receipt_id="binding",
        obligations=("TARGET",), evidence_level="L3_REPLICATED_EFFECT",
        authority={"eligible": True}, risk={}, provenance={"evaluation_only": True})


def _oracle_pair(case_id, baseline_outcome, memory_outcome):
    def oracle(candidate, _case, _budget):
        outcome = memory_outcome if candidate is not None else baseline_outcome
        return {"compile_result": "PASS", "functional_result": outcome,
                "signoff_result": "PASS" if outcome == "PASS" else "FAIL",
                "outcome": outcome, "oracle_digest": "sha256:deriver-oracle"}
    return execute_paired_candidates(
        {"case_id": case_id, "toolchain_digest": "sha256:deriver-toolchain",
         "oracle_digest": "sha256:deriver-oracle"},
        {"NO_MEMORY": None, "ALWAYS_MEMORY": _oracle_candidate(),
         "APPLICABILITY_GATED": _oracle_candidate(),
         "CAUSAL_NO_SKILL": _oracle_candidate()},
        oracle=oracle, budget=3, routing_decision="CONSIDER",
        lineage_id=f"lineage-{case_id}", routing_receipt_id=f"route-{case_id}")


def test_oracle_label_deriver_uses_paired_outcomes_not_router_reason():
    useful = _oracle_pair("useful", "FAIL", "PASS")
    harmful = _oracle_pair("harmful", "PASS", "FAIL")
    no_match = _oracle_pair("no-match", "FAIL", "FAIL")
    assert derive_no_skill_oracle_label(useful)["expected_decision"] == "USE_MEMORY"
    assert derive_no_skill_oracle_label(harmful)["expected_reason"] == "RISK"
    assert derive_no_skill_oracle_label(no_match)["expected_reason"] == "NO_MATCH"
    incomplete = _oracle_pair("incomplete", "UNKNOWN", "PASS")
    with pytest.raises(NoSkillCalibrationError, match="incomplete"):
        derive_no_skill_oracle_label(incomplete)


def test_wilson_interval_is_explicit_and_unknown_safe():
    interval = wilson_interval(0, 5)
    assert interval["point"] == 0.0
    assert interval["upper"] > 0.0
    assert wilson_interval(0, 0)["lower"] is None
    with pytest.raises(NoSkillCalibrationError):
        wilson_interval(1, 0)
    with pytest.raises(NoSkillCalibrationError, match="only 95%"):
        wilson_interval(1, 2, confidence=0.90)


def test_mcnemar_regression_test_is_paired_and_unknown_safe():
    assert mcnemar_regression_test(8, 0)["significant_regression"] is True
    assert mcnemar_regression_test(0, 8)["significant_regression"] is False
    assert mcnemar_regression_test(0, 0) == {
        "regression_cases": 0, "improvement_cases": 0,
        "discordant_cases": 0, "p_value": 1.0, "alpha": 0.05,
        "significant_regression": False,
    }
    with pytest.raises(NoSkillCalibrationError):
        mcnemar_regression_test(-1, 0)


def test_route_adapter_requires_bound_receipts_and_independent_labels():
    use = _route("CONSIDER")
    abstain = _route("NO_SKILL", reason="STATE_SHIFT")
    routes = {
        "use": {**use.to_dict(), "routing_receipt_id": use.routing_receipt_id},
        "abstain": abstain,
    }
    paired = {
        "use": {"routing_receipt_id": use.routing_receipt_id},
        "abstain": {"routing_receipt_id": abstain.routing_receipt_id},
    }
    strata = {"mechanism_family": "density", "design": "aes", "platform": "sky130",
              "flow_regime": "route", "model_identity": "oracle-v1",
              "state_shift_dimension": "constraint"}
    labels = {
        "use": {"expected_decision": "USE_MEMORY", "confidence": 0.9, "strata": strata},
        "abstain": {"expected_decision": "NO_SKILL", "expected_reason": "STATE_SHIFT",
                    "confidence": 0.9, "strata": strata},
    }
    rows = build_no_skill_calibration_samples(paired, routes, labels)
    assert [row.predicted_decision for row in rows] == ["NO_SKILL", "USE_MEMORY"]
    assert rows[0].predicted_reason == "STATE_SHIFT"
    assert rows[1].routing_receipt_id == use.routing_receipt_id

    with pytest.raises(NoSkillCalibrationError, match="does not match decision"):
        build_no_skill_calibration_samples(
            {"use": {"routing_receipt_id": "routing-tampered"},
             "abstain": paired["abstain"]}, routes, labels)
    with pytest.raises(NoSkillCalibrationError, match="outside P15"):
        build_no_skill_calibration_samples(
            {"use": {"routing_receipt_id": _route("ABSTAIN").routing_receipt_id},
             "abstain": paired["abstain"]},
            {"use": _route("ABSTAIN"), "abstain": abstain}, labels)


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
    assert receipt.routing_receipt_coverage == 1.0
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

    no_route = evaluate_no_skill_calibration([
        replace(row, routing_receipt_id=None) for row in _samples()])
    assert "routing_receipt_coverage" in no_route.missing


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
