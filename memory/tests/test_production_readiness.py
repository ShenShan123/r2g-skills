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


def _routed_policy_interference_report(tmp_path, *, tamper=None):
    """Create a typed post-revision policy-MIR witness with zero observed harm."""
    cases = {}
    source_digests = {}
    for index in range(2):
        case_id = f"policy-case-{index}"
        cases[case_id] = PairedCandidateExecutionReceipt(
            case_id=case_id,
            arm_receipts={
                "NO_MEMORY": _execution(case_id, "no_memory", "no-memory:" + case_id),
                "ALWAYS_MEMORY": _execution(case_id, "structured_memory", "memory:" + case_id),
                "APPLICABILITY_GATED": _execution(case_id, "structured_memory", "memory:" + case_id),
                "CAUSAL_NO_SKILL": _execution(case_id, "structured_memory", "memory:" + case_id),
            },
            candidate_budget=3, case_digest=f"sha256:policy-case-{index}",
            toolchain_digest="sha256:toolchain", oracle_digest="sha256:oracle",
            paired=True, evaluation_only=True, lineage_id=f"policy-lineage-{index}",
            routing_receipt_id=f"routing-{index}", routing_decision="CONSIDER")
        source_digests[case_id] = f"sha256:policy-source-{index}"
    cohort = RtlPairedCohortReceipt(
        campaign_id="policy-mir-campaign", case_receipts=cases,
        source_digests=source_digests, candidate_budget=3,
        toolchain_digest="sha256:toolchain", oracle_digest="sha256:oracle",
        platform_digest="sha256:platform", pdk_digest="sha256:pdk",
        campaign_manifest_digest="sha256:manifest")
    cohort_path = tmp_path / "policy-cohort.json"
    cohort_path.write_text(json.dumps({**cohort.to_dict(),
                                       "receipt_digest": cohort.receipt_digest}))
    policy_mir = {
        "version": "r3-policy-mir-v1", "metric": "routed_policy",
        "baseline_arm": "NO_MEMORY", "policy_arm": "CAUSAL_NO_SKILL",
        "cohort_receipt": str(cohort_path),
        "cohort_receipt_sha256": _sha(cohort_path),
        "cohort_receipt_digest": cohort.receipt_digest,
        "case_count": 2, "known_cases": 2, "unknown_cases": 0,
        "routed_cases": 2, "harmful_cases": 0,
        "routing_receipt_coverage": 1.0, "evaluation_only": True,
        "canonical_memory_mutation": "none",
        "production_integration": "not_attempted",
    }
    if tamper:
        policy_mir.update(tamper)
    path = tmp_path / "policy-interference.json"
    path.write_text(json.dumps({
        "reason": "MEMORY_INTERFERENCE", "canonical_memory_mutation": "none",
        "production_authority_changed": False, "case_count": 2,
        "policy_mir": policy_mir,
    }))
    return path


def _authority_replay_report(tmp_path, **overrides):
    """Build the strict read-only authority-replay envelope expected by P15-B."""
    gates = {
        "rollback_verified": "PASS",
        "registry_verified": "PASS",
        "obligation_coverage": "PASS",
        "cross_lineage_te": "PASS",
        "harmful_rate": "PASS",
        "conformal_coverage": "PASS",
    }
    report = {
        "version": "tehm-rule-authority-replay-v1",
        "eligible": True,
        "authority_replay_status": "PASS",
        "all_gates_established": True,
        "gate_status": gates,
        "database_unchanged": True,
        "authority_database_sha256_before": "sha256:db",
        "authority_database_sha256_after": "sha256:db",
        "read_only": True,
        "decision": "ALLOW_AUTHORITY_REVIEW",
        "promotion_attempted": False,
        "canonical_memory_mutation": "none",
        "production_runtime_imported": False,
        "receipt": {
            "authority_receipt_id": "rule_authority_fixture",
            "receipt_digest": "sha256:authority-receipt",
            "eligible_stored": True,
        },
    }
    report.update(overrides)
    path = tmp_path / "authority-replay.json"
    path.write_text(json.dumps(report))
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


def test_readiness_authority_requires_actual_read_only_replay(tmp_path):
    calibration = _calibration_report(tmp_path)
    interference = _interference_report(tmp_path)
    authority = _authority_replay_report(tmp_path)
    report = build_production_readiness(
        calibration_report=calibration, interference_summary=interference,
        authority_report=authority)
    assert report["readiness"]["gate_status"]["authority_replay"] == "PASS"
    assert report["readiness"]["metrics"]["authority"]["verified"] is True
    # The downstream P9 gate must consume the replayed, content-bound
    # authority receipt rather than report ``authority`` as missing merely
    # because the readiness and production projections are separate objects.
    assert report["readiness"]["production_gate"]["gate_status"]["authority"] == "PASS"
    assert any(ref["name"] == "authority_report" for ref in
               report["readiness"]["production_gate"]["evidence_refs"])


def test_readiness_replays_routed_policy_mir_instead_of_forced_counterfactual(
        tmp_path):
    calibration = _calibration_report(tmp_path)
    interference = _routed_policy_interference_report(tmp_path)
    report = build_production_readiness(
        calibration_report=calibration, interference_summary=interference)
    mir = report["readiness"]["metrics"]["mir"]
    assert mir["source"] == "routed_policy"
    assert mir["policy_arm"] == "CAUSAL_NO_SKILL"
    assert mir["harmful_cases"] == 0
    assert mir["total_cases"] == 2
    # Zero observed harm is still insufficient to establish a zero upper CI
    # with only two independent cases.
    assert mir["upper_ci"] > 0.0
    assert report["readiness"]["gate_status"]["mir_upper_ci"] == "FAIL"


def test_readiness_binds_explicit_mir_threshold_and_replays_it(tmp_path):
    calibration = _calibration_report(tmp_path)
    interference = _routed_policy_interference_report(tmp_path)
    output = tmp_path / "readiness-threshold.json"
    report = build_production_readiness(
        calibration_report=calibration, interference_summary=interference,
        max_mir_upper_ci=0.70, output=output)
    readiness = report["readiness"]
    assert readiness["gate_status"]["mir_upper_ci"] == "PASS"
    assert readiness["metrics"]["mir"]["upper_ci_threshold"] == 0.70
    assert (readiness["production_gate"]["thresholds"]
            ["max_memory_interference_rate"]) == 0.70
    replayed = replay_production_readiness(output)
    assert replayed.to_dict() == readiness


def test_readiness_rejects_malformed_mir_threshold(tmp_path):
    calibration = _calibration_report(tmp_path)
    interference = _interference_report(tmp_path)
    with pytest.raises(ProductionReadinessError, match="MIR upper-CI threshold"):
        build_production_readiness(
            calibration_report=calibration, interference_summary=interference,
            max_mir_upper_ci=1.1)


def test_readiness_rejects_tampered_routed_policy_mir_aggregate(tmp_path):
    calibration = _calibration_report(tmp_path)
    interference = _routed_policy_interference_report(
        tmp_path, tamper={"harmful_cases": 1})
    with pytest.raises(ProductionReadinessError, match="policy MIR aggregate"):
        build_production_readiness(
            calibration_report=calibration, interference_summary=interference)


def test_readiness_rejects_legacy_routed_policy_without_route_semantics(tmp_path):
    calibration = _calibration_report(tmp_path)
    interference = _routed_policy_interference_report(tmp_path)
    report = json.loads(interference.read_text())
    policy_mir = report["policy_mir"]
    cohort_path = Path(policy_mir["cohort_receipt"])
    raw = json.loads(cohort_path.read_text())
    raw.pop("receipt_digest", None)
    first_case = next(iter(raw["case_receipts"].values()))
    first_case.pop("routing_decision", None)
    checked = RtlPairedCohortReceipt.from_dict(raw)
    raw["receipt_digest"] = checked.receipt_digest
    cohort_path.write_text(json.dumps(raw))
    policy_mir["cohort_receipt_sha256"] = _sha(cohort_path)
    policy_mir["cohort_receipt_digest"] = checked.receipt_digest
    interference.write_text(json.dumps(report))
    with pytest.raises(ProductionReadinessError, match="routing decision is missing"):
        build_production_readiness(
            calibration_report=calibration, interference_summary=interference)


@pytest.mark.parametrize("overrides", [
    {"version": "untrusted-summary-v1"},
    {"authority_replay_status": "FAIL"},
    {"database_unchanged": False},
    {"read_only": False},
    {"gate_status": {"rollback_verified": "PASS"}},
    {"receipt": {"authority_receipt_id": "id", "receipt_digest": "sha256:d"}},
])
def test_readiness_rejects_authority_summary_without_complete_replay(
        tmp_path, overrides):
    calibration = _calibration_report(tmp_path)
    interference = _interference_report(tmp_path)
    authority = _authority_replay_report(tmp_path, **overrides)
    report = build_production_readiness(
        calibration_report=calibration, interference_summary=interference,
        authority_report=authority)
    assert report["readiness"]["gate_status"]["authority_replay"] == "FAIL"
    assert report["readiness"]["metrics"]["authority"]["verified"] is False
