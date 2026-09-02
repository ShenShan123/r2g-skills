"""Revision3 P1-R3 counterexample detector/adapter tests."""
from __future__ import annotations

import pytest

from tehm.evaluation.candidate_executor import execute_candidate
from tehm.evolution.admission import admit_evolution_reason
from tehm.evolution.counterexample import (
    CounterexampleReceipt, detect_counterexample,
)
from tehm.evolution.reason_derivation import (
    EvolutionReasonDerivationError, derive_counterexample_reason,
)
from tehm.retrieval.structured_candidate import StructuredRepairCandidate


def _candidate() -> StructuredRepairCandidate:
    return StructuredRepairCandidate(
        candidate_id="counterexample-candidate", resolved_state_id="state-send",
        knowledge_object_id="knowledge-handshake@1", causal_path_ids=("path-handshake",),
        asset_id="asset-guard", action_family="GUARD_STRENGTHEN",
        concrete_action={"domain": "rtl", "transformation_family": "GUARD_STRENGTHEN",
                         "payload": {"module": "req_ack_fsm", "add_condition": "ack"}},
        applicability_receipt_id="applicable-1", binding_receipt_id="binding-1",
        obligations=("RTL_TARGET_TEST_PASS",), evidence_level="L3",
        authority={"eligible": True}, risk={},
        provenance={"source": "counterexample-test", "evaluation_only": True})


def _execution(candidate: StructuredRepairCandidate, *, observed_outcome="FAIL",
               observed_effects=({"effect": "stuck_state"},)):
    return execute_candidate(
        candidate, {"case_id": "counterexample-case"},
        oracle=lambda _candidate, _case, _budget: {
            "compile_result": "PASS", "functional_result": observed_outcome,
            "signoff_result": observed_outcome, "outcome": observed_outcome,
            "toolchain_digest": "sha256:toolchain",
            "oracle_digest": "sha256:oracle",
            "metadata": {"oracle_complete": True,
                         "observed_outcome": {"outcome": observed_outcome},
                         "observed_effects": list(observed_effects)},
        }, budget=3)


def _witnesses(candidate: StructuredRepairCandidate, execution) -> tuple[dict, dict]:
    return (
        {"receipt_id": candidate.applicability_receipt_id, "status": "APPLICABLE"},
        {"receipt_id": candidate.binding_receipt_id, "status": "BOUND",
         "candidate_digest": candidate.candidate_digest,
         "action_digest": execution.action_digest},
    )


def test_counterexample_requires_prediction_binding_and_real_oracle():
    candidate = _candidate()
    execution = _execution(candidate)
    applicability, binding = _witnesses(candidate, execution)
    receipt = detect_counterexample(
        candidate, execution,
        prediction={"expected_outcome": {"outcome": "PASS"},
                    "predicted_effects": ({"effect": "legal_transition"},)},
        applicability=applicability, binding=binding,
        campaign_id="r3-counterexample", lineage_id="lineage-1")
    # The candidate FAIL is only useful because it contradicts explicit typed
    # prediction and oracle-mediated observed effects.
    assert receipt is not None
    assert set(receipt.contradiction_types) == {
        "OUTCOME_CONTRADICTION", "MEDIATED_EFFECT_CONTRADICTION"}
    derivation = derive_counterexample_reason(
        receipt, campaign_id="r3-counterexample", case_id="counterexample-case")
    admission = admit_evolution_reason(
        derivation, campaign_id="r3-counterexample", learner_eligible=True,
        counterexample=receipt)
    assert admission.admitted is True
    assert admission.canonical_memory_mutation == "none"


def test_counterexample_does_not_follow_from_candidate_fail_alone():
    candidate = _candidate()
    execution = execute_candidate(
        candidate, {"case_id": "counterexample-case"},
        oracle=lambda *_: {"compile_result": "PASS", "functional_result": "FAIL",
                           "signoff_result": "FAIL", "outcome": "FAIL",
                           "metadata": {"oracle_complete": True}}, budget=3)
    applicability, binding = _witnesses(candidate, execution)
    with pytest.raises(ValueError, match="observed_outcome"):
        detect_counterexample(
            candidate, execution,
            prediction={"expected_outcome": {"outcome": "PASS"},
                        "predicted_effects": ({"effect": "legal_transition"},)},
            applicability=applicability, binding=binding,
            campaign_id="r3-counterexample", lineage_id="lineage-1")


def test_matching_prediction_is_not_a_counterexample_and_binding_is_rechecked():
    candidate = _candidate()
    execution = _execution(candidate, observed_outcome="FAIL",
                           observed_effects=({"effect": "stuck_state"},))
    applicability, binding = _witnesses(candidate, execution)
    assert detect_counterexample(
        candidate, execution,
        prediction={"expected_outcome": {"outcome": "FAIL"},
                    "predicted_effects": ({"effect": "stuck_state"},)},
        applicability=applicability, binding=binding,
        campaign_id="r3-counterexample", lineage_id="lineage-1") is None
    bad_binding = {**binding, "action_digest": "sha256:wrong"}
    with pytest.raises(ValueError, match="binding"):
        detect_counterexample(
            candidate, execution,
            prediction={"expected_outcome": {"outcome": "PASS"},
                        "predicted_effects": ({"effect": "legal_transition"},)},
            applicability=applicability, binding=bad_binding,
            campaign_id="r3-counterexample", lineage_id="lineage-1")


def test_counterexample_receipt_roundtrip_and_campaign_binding():
    candidate = _candidate()
    execution = _execution(candidate)
    applicability, binding = _witnesses(candidate, execution)
    receipt = detect_counterexample(
        candidate, execution,
        prediction={"expected_outcome": {"outcome": "PASS"},
                    "predicted_effects": ({"effect": "legal_transition"},)},
        applicability=applicability, binding=binding,
        campaign_id="r3-counterexample", lineage_id="lineage-1")
    payload = {**receipt.to_dict(), "receipt_id": receipt.receipt_id,
               "receipt_digest": receipt.receipt_digest}
    assert CounterexampleReceipt.from_dict(payload) == receipt
    with pytest.raises(EvolutionReasonDerivationError, match="campaign or case"):
        derive_counterexample_reason(
            receipt, campaign_id="other", case_id="counterexample-case")
