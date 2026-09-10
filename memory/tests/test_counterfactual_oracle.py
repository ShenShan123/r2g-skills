"""Scoped P13 counterfactual completeness remains non-production."""
from copy import deepcopy

from tehm.evaluation.candidate_executor import CandidateExecutionReceipt
from tehm.evaluation.counterfactual_oracle import (
    COUNTERFACTUAL_CHECKS,
    build_counterfactual_oracle_receipt,
    counterfactual_oracle_complete,
    fixed_constraint_check_verdicts,
)
from tehm.evaluation.production_readiness import _execution_complete


def _reports():
    reports = {
        "route": {"status": "clean"},
        "drc": {"status": "clean"},
        "lvs": {"status": "clean"},
        "rcx": {"status": "complete"},
        "timing": {"tier": "clean"},
    }
    reports["signoff_manifest"] = {
        "reports": {
            "route.json": {"present": True, "sha256": "a" * 64,
                           "status": "clean"},
            "drc.json": {"present": True, "sha256": "b" * 64,
                         "status": "clean"},
            "lvs.json": {"present": True, "sha256": "c" * 64,
                         "status": "clean"},
            "rcx.json": {"present": True, "sha256": "d" * 64,
                         "status": "complete"},
            "timing_check.json": {"present": True, "sha256": "e" * 64,
                                  "tier": "clean"},
        },
        "confirming_run": {"consensus": True},
        "platform_capability": {"strict_signoff_ready": True},
        "constraint": {"final_timing_tier": "clean", "sdc_sha256": "b" * 64},
        "strict_missing": [
            "constraint: fmax_search winner (reports/fmax_search.json status=ok)"
        ],
        "strict_clean": False,
    }
    return reports


def _execution(claim, *, outcome="PASS", functional="PASS"):
    return CandidateExecutionReceipt(
        case_id="case", candidate_id="candidate", source="structured_memory",
        action_digest="sha256:action", candidate_digest="sha256:candidate",
        compile_result="PASS", functional_result=functional,
        signoff_result="UNKNOWN", outcome=outcome, created_regressions=(),
        obligations={}, toolchain_digest="sha256:tool",
        oracle_digest="sha256:oracle", produced_transition_id=None, budget=1,
        metadata={"oracle_available": True,
                  "oracle_metadata": {"counterfactual_oracle": claim}})


def test_fixed_constraint_receipt_is_complete_without_strict_signoff_claim():
    checks = fixed_constraint_check_verdicts(_reports())
    assert set(checks) == set(COUNTERFACTUAL_CHECKS)
    assert set(checks.values()) == {"PASS"}
    claim = build_counterfactual_oracle_receipt(
        checks, evidence_digest="sha256:evidence")
    assert claim["complete"] is True
    assert claim["strict_signoff_claim"] is False
    assert claim["production_eligible"] is False
    execution = _execution(claim)
    assert counterfactual_oracle_complete(execution) is True
    assert _execution_complete(execution) is False


def test_observed_failure_is_complete_but_missing_report_is_not():
    reports = _reports()
    reports["drc"]["status"] = "violations"
    failed = build_counterfactual_oracle_receipt(
        fixed_constraint_check_verdicts(reports),
        evidence_digest="sha256:failed")
    assert failed["complete"] is True
    assert counterfactual_oracle_complete(
        _execution(failed, outcome="FAIL", functional="FAIL")) is True

    reports = _reports()
    reports.pop("lvs")
    unknown = build_counterfactual_oracle_receipt(
        fixed_constraint_check_verdicts(reports),
        evidence_digest="sha256:unknown")
    assert unknown["complete"] is False
    assert counterfactual_oracle_complete(
        _execution(unknown, outcome="UNKNOWN", functional="UNKNOWN")) is False


def test_counterfactual_receipt_tamper_does_not_gain_admission():
    claim = build_counterfactual_oracle_receipt(
        fixed_constraint_check_verdicts(_reports()),
        evidence_digest="sha256:evidence")
    tampered = deepcopy(claim)
    tampered["production_eligible"] = True
    assert counterfactual_oracle_complete(_execution(tampered)) is False
