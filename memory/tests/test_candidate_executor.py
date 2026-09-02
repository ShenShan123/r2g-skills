"""P12 evaluation-only structured candidate execution adapter tests."""
from __future__ import annotations

import hashlib

import pytest

from tehm.ids import stable_dumps
from tehm.state import RiskReceipt
from tehm.evaluation.candidate_executor import (
    P12_ARMS, CandidateExecutionReceipt, CandidateExecutorError,
    PairedCandidateExecutionReceipt, execute_candidate,
    execute_paired_candidates,
)
from tehm.retrieval.structured_candidate import StructuredRepairCandidate


def _candidate() -> StructuredRepairCandidate:
    return StructuredRepairCandidate(
        candidate_id="structured_candidate_fixture",
        resolved_state_id="state-fixture",
        knowledge_object_id="mk_fixture@1",
        causal_path_ids=("path-fixture",), asset_id="asset-fixture",
        action_family="GUARD_STRENGTHEN",
        concrete_action={"domain": "rtl.GUARD_STRENGTHEN",
                         "transformation_family": "GUARD_STRENGTHEN",
                         "payload": {"module": "top", "add_condition": "ack"}},
        applicability_receipt_id="asset_selection_fixture",
        binding_receipt_id="binding_fixture", obligations=("TARGET_PASS",),
        evidence_level="L3_REPLICATED_EFFECT", authority={"eligible": True},
        risk={}, provenance={"evaluation_only": True, "source": "test"})


def _risk_receipt() -> RiskReceipt:
    payload = {
        "version": "risk-receipt-v0.1",
        "current_resolution_id": "resolution-paired-risk",
        "expected_utility": -0.5,
        "evidence_refs": ["paired-risk-evidence"],
        "risk_model": "typed_expected_utility_v1",
        "reason": "RISK",
    }
    return RiskReceipt(
        current_resolution_id=payload["current_resolution_id"],
        expected_utility=payload["expected_utility"],
        evidence_refs=tuple(payload["evidence_refs"]),
        risk_model=payload["risk_model"], reason=payload["reason"],
        replay_digest="sha256:" + hashlib.sha256(
            stable_dumps(payload).encode()).hexdigest(),
    )


def test_execute_candidate_records_oracle_receipt_without_canonical_write():
    receipt = execute_candidate(
        _candidate(), {"case_id": "case-fixture", "toolchain_digest": "sha256:tool"},
        oracle=lambda _candidate, _case, _budget: {
            "compile_result": "PASS", "functional_result": "PASS",
            "signoff_result": "PASS", "outcome": "PASS",
            "obligations": {"TARGET_PASS": "PASS"},
            "oracle_digest": "sha256:oracle", "produced_transition_id": "transition-1",
        }, budget=3)
    assert isinstance(receipt, CandidateExecutionReceipt)
    assert receipt.outcome == "PASS"
    assert receipt.source == "structured_memory"
    assert receipt.budget == 3
    assert receipt.execution_digest.startswith("sha256:")


def test_execute_candidate_is_unknown_without_oracle_and_rejects_gold_case():
    unknown = execute_candidate(_candidate(), {"case_id": "case-unknown"})
    assert unknown.outcome == "UNKNOWN"
    assert unknown.compile_result == "UNKNOWN"
    assert unknown.produced_transition_id is None
    with pytest.raises(CandidateExecutorError, match="gold-answer"):
        execute_candidate(_candidate(), {"case_id": "case-gold", "fix": {"answer": 1}})


def test_execute_candidate_rejects_oversized_budget_and_bad_oracle():
    with pytest.raises(CandidateExecutorError, match="at most three"):
        execute_candidate(_candidate(), {"case_id": "case-budget"}, budget=4)
    with pytest.raises(CandidateExecutorError, match="oracle result"):
        execute_candidate(_candidate(), {"case_id": "case-oracle"}, oracle=lambda *_: [])


def test_execute_paired_candidates_enforces_four_arms_and_fixed_digests():
    candidate = _candidate()

    def oracle(current, _case, _budget):
        if current is None:
            return {"compile_result": "PASS", "functional_result": "FAIL",
                    "toolchain_digest": "sha256:tool", "oracle_digest": "sha256:oracle"}
        return {"compile_result": "PASS", "functional_result": "PASS",
                "signoff_result": "PASS", "toolchain_digest": "sha256:tool",
                "oracle_digest": "sha256:oracle"}

    bundle = execute_paired_candidates(
        {"case_id": "paired-case", "toolchain_digest": "sha256:tool"},
        {"NO_MEMORY": None, "ALWAYS_MEMORY": candidate,
         "APPLICABILITY_GATED": candidate, "CAUSAL_NO_SKILL": candidate},
        oracle=oracle, budget=3)
    assert isinstance(bundle, PairedCandidateExecutionReceipt)
    assert set(bundle.arm_receipts) == set(P12_ARMS)
    assert bundle.arm_receipts["NO_MEMORY"].source == "no_memory"
    assert bundle.arm_receipts["ALWAYS_MEMORY"].outcome == "PASS"
    replay = PairedCandidateExecutionReceipt.from_dict({
        **bundle.to_dict(), "receipt_digest": bundle.receipt_digest})
    assert replay.to_dict() == bundle.to_dict()


def test_causal_no_skill_refusal_executes_real_no_memory_fallback():
    candidate = _candidate()

    def oracle(current, _case, _budget):
        assert current is None or isinstance(current, StructuredRepairCandidate)
        return {"compile_result": "PASS", "functional_result": "PASS",
                "signoff_result": "PASS", "toolchain_digest": "sha256:tool",
                "oracle_digest": "sha256:oracle"}

    bundle = execute_paired_candidates(
        {"case_id": "paired-fallback", "toolchain_digest": "sha256:tool"},
        {"NO_MEMORY": None, "ALWAYS_MEMORY": candidate,
         "APPLICABILITY_GATED": candidate, "CAUSAL_NO_SKILL": candidate},
        oracle=oracle, routing_decision="NO_SKILL",
        no_skill_reason="STATE_SHIFT", state_shift_receipt_id="shift-receipt",
        routing_receipt_id="routing-receipt")
    causal = bundle.arm_receipts["CAUSAL_NO_SKILL"]
    assert bundle.routing_decision == "NO_SKILL"
    assert causal.source == "no_memory"
    assert causal.metadata["policy_fallback"] is True
    assert causal.metadata["fallback_reason"] == "STATE_SHIFT"
    assert causal.metadata["routing_receipt_id"] == "routing-receipt"
    assert causal.metadata["ignored_candidate_id"] == candidate.candidate_id
    assert bundle.arm_receipts["ALWAYS_MEMORY"].source == "structured_memory"
    assert bundle.arm_receipts["APPLICABILITY_GATED"].source == "structured_memory"


def test_applicability_gate_can_execute_no_memory_fallback_and_no_memory_rejects_candidate():
    def oracle(_current, _case, _budget):
        return {"compile_result": "PASS", "functional_result": "PASS",
                "signoff_result": "PASS", "toolchain_digest": "sha256:tool",
                "oracle_digest": "sha256:oracle"}

    candidate = _candidate()
    bundle = execute_paired_candidates(
        {"case_id": "paired-applicability-fallback",
         "toolchain_digest": "sha256:tool"},
        {"NO_MEMORY": None, "ALWAYS_MEMORY": candidate,
         "APPLICABILITY_GATED": None, "CAUSAL_NO_SKILL": candidate},
        oracle=oracle)
    gated = bundle.arm_receipts["APPLICABILITY_GATED"]
    assert gated.source == "no_memory"
    assert gated.metadata["policy_fallback"] is True
    with pytest.raises(CandidateExecutorError, match="NO_MEMORY arm"):
        execute_paired_candidates(
            {"case_id": "paired-invalid-no-memory"},
            {"NO_MEMORY": candidate, "ALWAYS_MEMORY": candidate,
             "APPLICABILITY_GATED": candidate, "CAUSAL_NO_SKILL": candidate},
            oracle=oracle)


def test_execute_paired_candidates_rejects_digest_drift():
    candidate = _candidate()

    def drifting_oracle(current, _case, _budget):
        suffix = "memory" if current is not None else "base"
        return {"compile_result": "PASS", "functional_result": "PASS",
                "toolchain_digest": "sha256:tool-" + suffix,
                "oracle_digest": "sha256:oracle"}

    with pytest.raises(CandidateExecutorError, match="digest mismatch"):
        execute_paired_candidates(
            {"case_id": "paired-drift"},
            {"NO_MEMORY": None, "ALWAYS_MEMORY": candidate,
             "APPLICABILITY_GATED": candidate, "CAUSAL_NO_SKILL": candidate},
            oracle=drifting_oracle)


def test_paired_receipt_retains_reason_aware_no_skill_metadata():
    candidate = _candidate()

    def oracle(current, _case, _budget):
        return {"compile_result": "PASS", "functional_result": "PASS",
                "signoff_result": "PASS", "toolchain_digest": "sha256:tool",
                "oracle_digest": "sha256:oracle"}

    bundle = execute_paired_candidates(
        {"case_id": "paired-reason", "toolchain_digest": "sha256:tool"},
        {"NO_MEMORY": None, "ALWAYS_MEMORY": candidate,
         "APPLICABILITY_GATED": candidate, "CAUSAL_NO_SKILL": candidate},
        oracle=oracle, no_skill_reason="STATE_SHIFT",
        state_shift_receipt_id="state_shift_receipt", lineage_id="lineage-fixture",
        routing_receipt_id="routing-fixture")
    assert bundle.no_skill_reason == "STATE_SHIFT"
    assert bundle.state_shift_receipt_id == "state_shift_receipt"
    assert bundle.lineage_id == "lineage-fixture"
    assert bundle.routing_receipt_id == "routing-fixture"
    replay = PairedCandidateExecutionReceipt.from_dict(bundle.to_dict())
    assert replay == bundle


def test_paired_receipt_preserves_replayable_risk_witness():
    candidate = _candidate()

    def oracle(_current, _case, _budget):
        return {"compile_result": "PASS", "functional_result": "PASS",
                "signoff_result": "PASS", "toolchain_digest": "sha256:tool",
                "oracle_digest": "sha256:oracle"}

    risk = _risk_receipt()
    bundle = execute_paired_candidates(
        {"case_id": "paired-risk", "toolchain_digest": "sha256:tool"},
        {"NO_MEMORY": None, "ALWAYS_MEMORY": candidate,
         "APPLICABILITY_GATED": candidate, "CAUSAL_NO_SKILL": candidate},
        oracle=oracle, no_skill_reason="RISK", risk_receipt_id=risk.receipt_id,
        risk_receipt=risk.to_dict())
    assert bundle.risk_receipt == risk.to_dict()
    replay = PairedCandidateExecutionReceipt.from_dict({
        **bundle.to_dict(), "receipt_digest": bundle.receipt_digest})
    assert replay == bundle
    tampered = {**bundle.to_dict(),
                "risk_receipt": {**bundle.risk_receipt,
                                 "expected_utility": 0.5}}
    with pytest.raises(CandidateExecutorError, match="risk_receipt"):
        PairedCandidateExecutionReceipt.from_dict(tampered)
