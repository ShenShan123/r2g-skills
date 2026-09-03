"""Revision3 P15-B production-readiness preflight tests."""
from __future__ import annotations

import hashlib
import json

import pytest

from tehm.evaluation.candidate_executor import (
    CandidateExecutionReceipt, PairedCandidateExecutionReceipt,
)
from tehm.evaluation.no_skill_calibration import (
    NoSkillCalibrationSample, evaluate_no_skill_calibration,
)
from tehm.evaluation.production_readiness import (
    ProductionReadinessError, build_production_readiness,
    replay_production_readiness,
)
from tehm.evaluation.rtl_cohort import RtlPairedCohortReceipt


def _sha(path):
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _execution(case_id, source, candidate_id):
    return CandidateExecutionReceipt(
        case_id=case_id, candidate_id=candidate_id, source=source,
        action_digest="sha256:action", candidate_digest="sha256:candidate",
        compile_result="PASS", functional_result="PASS", signoff_result="PASS",
        outcome="PASS", created_regressions=(), obligations={},
        toolchain_digest="sha256:toolchain", oracle_digest="sha256:oracle",
        produced_transition_id=None, budget=3,
        metadata={"oracle_available": True, "oracle_metadata": {"oracle_complete": True}})


def _calibration_report(tmp_path):
    strata = {"mechanism_family": "handshake", "design": "req_ack",
              "platform": "asap7", "flow_regime": "icarus",
              "model_identity": "oracle-v1", "state_shift_dimension": "none"}
    samples = []
    for index, reason in enumerate(("NO_MATCH", "STATE_SHIFT", "RISK") * 5):
        samples.append(NoSkillCalibrationSample(
            case_id=f"case-{index}", predicted_decision="NO_SKILL",
            expected_decision="NO_SKILL", predicted_reason=reason,
            expected_reason=reason, confidence=0.95, strata=strata,
            routing_receipt_id=f"route-{index}"))
    for index in range(5):
        samples.append(NoSkillCalibrationSample(
            case_id=f"case-{index + 15}", predicted_decision="USE_MEMORY",
            expected_decision="USE_MEMORY", confidence=0.95, strata=strata,
            routing_receipt_id=f"route-use-{index}"))
    calibration = evaluate_no_skill_calibration(samples, minimum_sample_count=20,
                                                 minimum_reason_cases=2)
    cases = {}
    source_digests = {}
    for index in range(20):
        case_id = f"case-{index}"
        cases[case_id] = PairedCandidateExecutionReceipt(
            case_id=case_id,
            arm_receipts={
                "NO_MEMORY": _execution(case_id, "no_memory", "no-memory:" + case_id),
                "ALWAYS_MEMORY": _execution(case_id, "structured_memory", "memory:" + case_id),
                "APPLICABILITY_GATED": _execution(case_id, "structured_memory", "memory:" + case_id),
                "CAUSAL_NO_SKILL": _execution(case_id, "structured_memory", "memory:" + case_id),
            },
            candidate_budget=3, case_digest=f"sha256:case-{index}",
            toolchain_digest="sha256:toolchain", oracle_digest="sha256:oracle",
            paired=True, evaluation_only=True, lineage_id=f"lineage-{index}",
            routing_decision="CONSIDER")
        source_digests[case_id] = f"sha256:source-{index}"
    cohort = RtlPairedCohortReceipt(
        campaign_id="readiness-campaign", case_receipts=cases,
        source_digests=source_digests, candidate_budget=3,
        toolchain_digest="sha256:toolchain", oracle_digest="sha256:oracle",
        platform_digest="sha256:platform", pdk_digest="sha256:pdk",
        campaign_manifest_digest="sha256:manifest")
    cohort_path = tmp_path / "cohort.json"
    cohort_path.write_text(json.dumps({**cohort.to_dict(),
                                       "receipt_digest": cohort.receipt_digest}))
    report_path = tmp_path / "calibration.json"
    report_path.write_text(json.dumps({
        "campaign_id": "readiness-campaign",
        "no_skill_calibration": calibration.to_dict(),
        "evidence_refs": [{"id": "p15-cohort", "path": str(cohort_path),
                           "sha256": _sha(cohort_path)}],
        "canonical_memory_mutation": "none", "production_runtime_imported": False,
    }))
    return report_path


def _interference_report(tmp_path):
    path = tmp_path / "interference.json"
    path.write_text(json.dumps({
        "reason": "MEMORY_INTERFERENCE", "canonical_memory_mutation": "none",
        "production_authority_changed": False, "case_count": 2,
        "pre_revision_outcomes": {"ALWAYS_MEMORY": {
            "FAIL": 0, "PASS": 0, "UNKNOWN": 0, "PARTIAL": 2}},
        "risk_update": {"before": {"memory_interference_cases": 0}},
    }))
    return path


def test_readiness_is_fail_closed_and_replayable(tmp_path):
    calibration = _calibration_report(tmp_path)
    interference = _interference_report(tmp_path)
    output = tmp_path / "readiness.json"
    report = build_production_readiness(
        calibration_report=calibration, interference_summary=interference,
        output=output)
    assert report["receipt"]["eligible"] is False
    assert report["receipt"]["gate_status"] == {
        "multi_lineage": "PASS", "reason_stratified_calibration": "PASS",
        "mir_upper_ci": "FAIL", "repair_pareto": "NOT_ESTABLISHED",
        "anti_forgetting": "NOT_ESTABLISHED", "authority_replay": "NOT_ESTABLISHED",
        "rollback": "NOT_ESTABLISHED",
    }
    replayed = replay_production_readiness(output)
    assert replayed.eligible is False
    assert replayed.production_integration == "not_attempted"


def test_readiness_replay_rejects_input_digest_drift(tmp_path):
    calibration = _calibration_report(tmp_path)
    interference = _interference_report(tmp_path)
    output = tmp_path / "readiness.json"
    build_production_readiness(calibration_report=calibration,
                               interference_summary=interference, output=output)
    calibration.write_text(calibration.read_text() + "\n")
    with pytest.raises(ProductionReadinessError, match="input digest drifted"):
        replay_production_readiness(output)
