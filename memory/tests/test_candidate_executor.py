"""P12 evaluation-only structured candidate execution adapter tests."""
from __future__ import annotations

import pytest

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
