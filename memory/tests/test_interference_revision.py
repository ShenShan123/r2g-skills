"""R3-8 typed interference proposal and shadow-plan tests."""
from __future__ import annotations

import pytest

from tehm.evaluation.candidate_executor import P12_ARMS, execute_paired_candidates
from tehm.evolution.interference_revision import (
    MemoryInterferenceEvolutionProposal, MemoryInterferenceRevisionError,
    interference_proposal_to_localized_plan,
    propose_memory_interference_specialization,
)
from tehm.evolution.reason_derivation import derive_memory_interference_reason
from tehm.retrieval.structured_candidate import StructuredRepairCandidate


def _candidate(case_id: str) -> StructuredRepairCandidate:
    return StructuredRepairCandidate(
        candidate_id=f"candidate-{case_id}", resolved_state_id=f"state-{case_id}",
        knowledge_object_id="interference-parent@1", causal_path_ids=("path-r3",),
        asset_id="asset-r3", action_family="AST_REWRITE",
        concrete_action={"domain": "rtl.AST_REWRITE", "transformation_family": "AST_REWRITE",
                         "payload": {"target": "x", "replacement": "y", "count": 1}},
        applicability_receipt_id=f"app-{case_id}", binding_receipt_id=f"bind-{case_id}",
        obligations=("TARGET_PASS",), evidence_level="L3_REPLICATED_EFFECT",
        authority={"eligible": True}, risk={},
        provenance={"evaluation_only": True, "source": "test"})


def _observation(case_id: str, lineage: str):
    candidate = _candidate(case_id)

    def oracle(current, _case, _budget):
        if current is None:
            return {"compile_result": "PASS", "functional_result": "PASS",
                    "signoff_result": "PASS", "toolchain_digest": "sha256:tool",
                    "oracle_digest": "sha256:oracle"}
        return {"compile_result": "PASS", "functional_result": "FAIL",
                "signoff_result": "FAIL", "toolchain_digest": "sha256:tool",
                "oracle_digest": "sha256:oracle", "created_regressions": ["target"]}

    paired = execute_paired_candidates(
        {"case_id": case_id, "toolchain_digest": "sha256:tool"},
        {arm: None if arm == "NO_MEMORY" else candidate for arm in P12_ARMS},
        oracle=oracle, budget=3, routing_decision="CONSIDER", lineage_id=lineage)
    derivation = derive_memory_interference_reason(
        paired, campaign_id="r3-interference-test")
    assert derivation is not None
    return derivation, paired


def test_interference_proposal_replays_typed_pairs_and_builds_specialize_plan():
    observations = [_observation("case-a", "lineage-a"),
                    _observation("case-b", "lineage-b")]
    proposal = propose_memory_interference_specialization(
        observations, knowledge_object_id="interference-parent@1",
        transition_ids=("transition-a", "transition-b"),
        negative_applicability=({"platform": "asap7", "interference": True},),
        evidence_refs=("seed-evidence",), trigger_receipt_ids=("sha256:trigger-a",))
    assert isinstance(proposal, MemoryInterferenceEvolutionProposal)
    assert proposal.operation == "SPECIALIZE"
    assert proposal.evolution_reason == "MEMORY_INTERFERENCE"
    assert proposal.case_ids == ("case-a", "case-b")
    assert proposal.negative_applicability == ({"platform": "asap7", "interference": True},)
    plan = interference_proposal_to_localized_plan(proposal)
    assert plan.operation == "SPECIALIZE"
    assert plan.update_target == "UPDATE_CAUSAL_KNOWLEDGE"
    assert plan.failure_type == "MEMORY_INTERFERENCE"
    assert plan.knowledge_refs == ("interference-parent@1",)
    assert MemoryInterferenceEvolutionProposal.from_dict({
        **proposal.to_dict(), "proposal_id": proposal.proposal_id,
        "proposal_digest": proposal.proposal_digest}) == proposal


def test_interference_proposal_requires_independent_repeated_lineages():
    observation = _observation("case-a", "lineage-a")
    with pytest.raises(MemoryInterferenceRevisionError, match="at least two"):
        propose_memory_interference_specialization(
            [observation], knowledge_object_id="interference-parent@1",
            transition_ids=("transition-a",), negative_applicability=({"x": 1},),
            evidence_refs=("seed",))
    with pytest.raises(MemoryInterferenceRevisionError, match="distinct"):
        propose_memory_interference_specialization(
            [observation, _observation("case-b", "lineage-a")],
            knowledge_object_id="interference-parent@1",
            transition_ids=("transition-a", "transition-b"),
            negative_applicability=({"x": 1},), evidence_refs=("seed",))
