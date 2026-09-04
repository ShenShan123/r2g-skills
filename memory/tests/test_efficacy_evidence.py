"""Typed pre/post efficacy evidence replay tests."""
from __future__ import annotations

import json

import pytest

from tehm.evaluation.candidate_executor import (
    CandidateExecutionReceipt, PairedCandidateExecutionReceipt,
)
from tehm.evaluation.efficacy_evidence import (
    EfficacyEvidenceError, build_efficacy_evidence,
    replay_efficacy_evidence,
)
from tehm.evaluation.rtl_cohort import RtlPairedCohortReceipt


def _execution(case_id, source, candidate_id, outcome, *, fallback=False,
               decision="CONSIDER"):
    return CandidateExecutionReceipt(
        case_id=case_id, candidate_id=candidate_id, source=source,
        action_digest=f"sha256:action-{candidate_id}",
        candidate_digest=f"sha256:candidate-{candidate_id}",
        compile_result="PASS", functional_result=outcome,
        signoff_result=outcome, outcome=outcome, created_regressions=(),
        obligations={}, toolchain_digest="sha256:toolchain",
        oracle_digest="sha256:oracle", produced_transition_id=None,
        budget=3,
        metadata={"oracle_available": True,
                  "oracle_metadata": {"oracle_complete": True},
                  "policy_fallback": fallback,
                  "routing_decision": decision})


def _cohort(tmp_path, *, after=False):
    cases = {}
    sources = {}
    decision = "INAPPLICABLE" if after else "CONSIDER"
    policy_source = "no_memory" if after else "structured_memory"
    policy_outcome = "PASS" if after else "FAIL"
    for index in range(2):
        case_id = f"eff-case-{index}"
        policy_id = f"memory-{case_id}"
        fallback = after
        cases[case_id] = PairedCandidateExecutionReceipt(
            case_id=case_id,
            arm_receipts={
                "NO_MEMORY": _execution(case_id, "no_memory", f"no-{case_id}", "PASS",
                                         decision=decision),
                "ALWAYS_MEMORY": _execution(case_id, "structured_memory", f"always-{case_id}", "FAIL",
                                             decision=decision),
                "APPLICABILITY_GATED": _execution(
                    case_id, policy_source, policy_id, policy_outcome,
                    fallback=fallback, decision=decision),
                "CAUSAL_NO_SKILL": _execution(
                    case_id, policy_source, policy_id, policy_outcome,
                    fallback=fallback, decision=decision),
            },
            candidate_budget=3, case_digest=f"sha256:case-{index}-{after}",
            toolchain_digest="sha256:toolchain", oracle_digest="sha256:oracle",
            paired=True, evaluation_only=True, lineage_id=f"lineage-{index}",
            routing_receipt_id=f"routing-{case_id}", routing_decision=decision)
        sources[case_id] = f"sha256:source-{index}"
    cohort = RtlPairedCohortReceipt(
        campaign_id="efficacy-campaign", case_receipts=cases,
        source_digests=sources, candidate_budget=3,
        toolchain_digest="sha256:toolchain", oracle_digest="sha256:oracle",
        platform_digest="sha256:platform", pdk_digest="sha256:pdk",
        campaign_manifest_digest=("sha256:after" if after else "sha256:before"))
    path = tmp_path / ("after.json" if after else "before.json")
    path.write_text(json.dumps({**cohort.to_dict(), "receipt_digest": cohort.receipt_digest}))
    return path


def test_efficacy_evidence_derives_harm_reduction(tmp_path):
    before = _cohort(tmp_path, after=False)
    after = _cohort(tmp_path, after=True)
    report = build_efficacy_evidence(
        before_cohort=before, after_cohort=after,
        policy_arm="CAUSAL_NO_SKILL")
    metrics = replay_efficacy_evidence(report, base=tmp_path)
    assert metrics["paired_cases"] == 2
    assert metrics["baseline_harmful_activation_rate"] == 1.0
    assert metrics["memory_harmful_activation_rate"] == 0.0
    assert metrics["harm_reduction_observed"] is True


def test_efficacy_evidence_rejects_metric_tamper(tmp_path):
    before = _cohort(tmp_path, after=False)
    after = _cohort(tmp_path, after=True)
    report = build_efficacy_evidence(
        before_cohort=before, after_cohort=after,
        policy_arm="CAUSAL_NO_SKILL")
    tampered = json.loads(json.dumps(report))
    tampered["metrics"]["memory_harmful_activation_rate"] = 0.5
    with pytest.raises(EfficacyEvidenceError, match="metrics drifted"):
        replay_efficacy_evidence(tampered, base=tmp_path)
