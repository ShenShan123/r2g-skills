"""Reason-specific support expansion from learner-partitioned P12 receipts."""
from __future__ import annotations

from dataclasses import replace

import pytest

from tehm.evaluation import (
    CandidateExecutionReceipt, OrfsPairedCohortReceipt,
    PairedCandidateExecutionReceipt,
)
from tehm.evolution import (
    StateShiftSupportExpansionError, StateShiftSupportExpansionReceipt,
    derive_state_shift_support_expansion,
)
from tehm.knowledge import MechanismKnowledge
from tehm.state import ResolvedMemoryState, build_support_envelope, evaluate_state_shift


CAMPAIGN = "state-shift-expansion-test"


def _knowledge() -> MechanismKnowledge:
    return MechanismKnowledge(
        knowledge_id="density-support", version=1,
        mechanism_family="DENSITY_RELIEF", compatibility_profile=None,
        antecedent={"failure": "route_not_observed"},
        intervention={"family": "DENSITY_RELIEF"},
        mediated_effects=({"effect": "route_complete"},),
        expected_outcome={"outcome": "PASS"},
        positive_applicability=({"mechanism_family": "DENSITY_RELIEF"},),
        negative_applicability=(),
        preserved_obligations=("ORFS_ROUTE_PASS",),
        known_failure_modes=(), causal_path_ids=("path-density",),
        evidence_level="L3_REPLICATED_EFFECT",
        support_lineages=("training:a", "training:b"), status="shadow")


def _state(parent: MechanismKnowledge) -> ResolvedMemoryState:
    return ResolvedMemoryState(
        resolution_id="resolution-test", input_memory_digest="sha256:input",
        scope={"target_scope": "flow_feasibility"}, active_rules=(),
        active_causal_paths=parent.causal_path_ids,
        active_knowledge_claims=(parent.object_id,), active_assets=(),
        active_capabilities=(), suppressed=(), unresolved_conflicts=(),
        relation_ids=(), shadow_relation_ids=(),
        resolution_digest="sha256:resolution", resolver_version="test-v1")


def _execution(case_id: str, arm: str, *, memory: bool):
    return CandidateExecutionReceipt(
        case_id=case_id,
        candidate_id=(f"candidate:{case_id}" if memory else f"no-memory:{case_id}"),
        source="structured_memory" if memory else "no_memory",
        action_digest="sha256:action" if memory else "sha256:none",
        compile_result="PASS", functional_result="PASS", signoff_result="PASS",
        outcome="PASS", created_regressions=(), obligations={},
        toolchain_digest="sha256:toolchain", oracle_digest="sha256:oracle",
        produced_transition_id=None,
        candidate_digest="sha256:candidate" if memory else "sha256:none",
        budget=3, metadata={"arm": arm, "oracle_available": True})


def _pair(case_id: str, lineage: str, shift_id: str):
    arms = {
        "NO_MEMORY": _execution(case_id, "NO_MEMORY", memory=False),
        "ALWAYS_MEMORY": _execution(case_id, "ALWAYS_MEMORY", memory=True),
        "APPLICABILITY_GATED": _execution(
            case_id, "APPLICABILITY_GATED", memory=False),
        "CAUSAL_NO_SKILL": _execution(
            case_id, "CAUSAL_NO_SKILL", memory=False),
    }
    return PairedCandidateExecutionReceipt(
        case_id=case_id, arm_receipts=arms, candidate_budget=3,
        case_digest=f"sha256:case-{case_id}",
        toolchain_digest="sha256:toolchain", oracle_digest="sha256:oracle",
        no_skill_reason="STATE_SHIFT", state_shift_receipt_id=shift_id,
        lineage_id=lineage, routing_receipt_id=f"routing:{case_id}",
        routing_decision="NO_SKILL")


def _inputs():
    parent = _knowledge()
    state = _state(parent)
    envelope = build_support_envelope(parent, (), ({
        "transition_id": "transition:a", "split": "training",
        "learner_eligible": True,
        "verification": {"verdict": "PASS", "oracle_complete": True},
        "mechanism_family": "DENSITY_RELIEF",
        "constraint_regime": {"core_utilization": "40"},
    },))
    contexts = {
        case_id: {
            "mechanism_family": "DENSITY_RELIEF",
            "constraint_regime": {"core_utilization": "50"},
            "case_marker": case_id,
        }
        for case_id in ("case:a", "case:b")
    }
    shifts = {
        case_id: evaluate_state_shift(
            context, state, parent, envelope, evidence_refs=(case_id,))
        for case_id, context in contexts.items()
    }
    pairs = {
        case_id: _pair(case_id, f"held-training:{case_id}", shifts[case_id].receipt_id)
        for case_id in contexts
    }
    cohort = OrfsPairedCohortReceipt(
        campaign_id=CAMPAIGN, case_receipts=pairs,
        source_digests={case_id: f"sha256:source-{case_id}" for case_id in pairs},
        source_content_digests={
            case_id: f"sha256:content-{case_id}" for case_id in pairs},
        candidate_budget=3, toolchain_digest="sha256:toolchain",
        oracle_digest="sha256:oracle", platform_digest="sha256:platform",
        pdk_digest="sha256:pdk", campaign_manifest_digest="sha256:manifest")
    partition = [
        {"case_id": case_id, "dataset_split": "training", "role": "training",
         "learner_eligible": True}
        for case_id in contexts
    ]
    return parent, state, envelope, contexts, shifts, cohort, partition


def test_safe_p12_shift_derives_child_and_closes_exact_shift():
    parent, state, envelope, contexts, shifts, cohort, partition = _inputs()
    child, child_envelope, receipt = derive_state_shift_support_expansion(
        campaign_id=CAMPAIGN, proposal_digest="sha256:" + "a" * 64,
        parent_knowledge=parent, parent_envelope=envelope,
        resolved_state=state, cohort=cohort, state_shifts=shifts,
        current_contexts=contexts, learner_partition=partition)

    assert child.object_id == "density-support@2"
    assert child.status == "shadow"
    assert set(child.support_lineages) == {
        "training:a", "training:b", "held-training:case:a",
        "held-training:case:b",
    }
    assert {"constraint_regime": {"core_utilization": "50"}} in \
        child.positive_applicability
    assert receipt.expanded_dimensions == ("constraint_shift",)
    assert receipt.parent_support_envelope_digest == envelope.envelope_digest
    assert receipt.child_support_envelope_digest == child_envelope.envelope_digest
    assert all(evaluate_state_shift(
        contexts[case_id], state, child, child_envelope).transferable
        for case_id in contexts)
    assert StateShiftSupportExpansionReceipt.from_dict(receipt.to_dict()) == receipt


def test_expansion_rejects_non_training_partition():
    parent, state, envelope, contexts, shifts, cohort, partition = _inputs()
    partition[0]["dataset_split"] = "heldout"
    with pytest.raises(
            StateShiftSupportExpansionError,
            match="learner-eligible training cases"):
        derive_state_shift_support_expansion(
            campaign_id=CAMPAIGN, proposal_digest="sha256:" + "a" * 64,
            parent_knowledge=parent, parent_envelope=envelope,
            resolved_state=state, cohort=cohort, state_shifts=shifts,
            current_contexts=contexts, learner_partition=partition)


def test_expansion_rejects_partition_mapping_case_identity_conflict():
    parent, state, envelope, contexts, shifts, cohort, partition = _inputs()
    mapped = {item["case_id"]: dict(item) for item in partition}
    mapped["case:a"]["case_id"] = "case:b"
    with pytest.raises(
            StateShiftSupportExpansionError, match="case identity conflicts"):
        derive_state_shift_support_expansion(
            campaign_id=CAMPAIGN, proposal_digest="sha256:" + "a" * 64,
            parent_knowledge=parent, parent_envelope=envelope,
            resolved_state=state, cohort=cohort, state_shifts=shifts,
            current_contexts=contexts, learner_partition=mapped)


def test_expansion_rejects_failed_or_regressing_target():
    parent, state, envelope, contexts, shifts, cohort, partition = _inputs()
    pair = cohort.case_receipts["case:a"]
    target = replace(
        pair.arm_receipts["ALWAYS_MEMORY"], outcome="FAIL",
        functional_result="FAIL", created_regressions=("timing",))
    pairs = dict(cohort.case_receipts)
    pairs["case:a"] = replace(
        pair, arm_receipts={**pair.arm_receipts, "ALWAYS_MEMORY": target})
    failed = replace(cohort, case_receipts=pairs)
    with pytest.raises(
            StateShiftSupportExpansionError,
            match="target execution is not safe and complete"):
        derive_state_shift_support_expansion(
            campaign_id=CAMPAIGN, proposal_digest="sha256:" + "a" * 64,
            parent_knowledge=parent, parent_envelope=envelope,
            resolved_state=state, cohort=failed, state_shifts=shifts,
            current_contexts=contexts, learner_partition=partition)


def test_expansion_receipt_rejects_digest_tamper():
    parent, state, envelope, contexts, shifts, cohort, partition = _inputs()
    _child, _child_envelope, receipt = derive_state_shift_support_expansion(
        campaign_id=CAMPAIGN, proposal_digest="sha256:" + "a" * 64,
        parent_knowledge=parent, parent_envelope=envelope,
        resolved_state=state, cohort=cohort, state_shifts=shifts,
        current_contexts=contexts, learner_partition=partition)
    payload = receipt.to_dict()
    payload["receipt_digest"] = "sha256:" + "0" * 64
    with pytest.raises(
            StateShiftSupportExpansionError, match="receipt digest mismatch"):
        StateShiftSupportExpansionReceipt.from_dict(payload)
