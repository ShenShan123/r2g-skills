"""State-resolution rebasing remains explicit and shadow-only."""
from __future__ import annotations

import pytest

from tehm.evolution import (
    LocalizedUpdatePlan,
    StateResolutionRebaseError,
    StateResolutionRebaseReceipt,
    rebase_localized_update_plan,
    state_semantic_digest,
)
from tehm.knowledge import MechanismKnowledge, register_knowledge
from tehm.state import resolve_current_state


def _claim() -> MechanismKnowledge:
    return MechanismKnowledge(
        knowledge_id="state-rebase-parent", version=1,
        mechanism_family="DENSITY_RELIEF", compatibility_profile=None,
        antecedent={"failure": "density"},
        intervention={"family": "FLOW_CONFIG"},
        mediated_effects=({"effect": "density_reduced"},),
        expected_outcome={"outcome": "PASS"},
        positive_applicability=({"mechanism_family": "DENSITY_RELIEF"},),
        negative_applicability=(), preserved_obligations=("route",),
        known_failure_modes=(), causal_path_ids=("causal-path-rebase",),
        evidence_level="L2_CONTROLLED_INTERVENTION",
        support_lineages=("lineage-a", "lineage-b"), status="shadow",
    )


def _plan(parent: MechanismKnowledge) -> LocalizedUpdatePlan:
    return LocalizedUpdatePlan(
        transition_id="transition-a", campaign_id="evolution-campaign",
        learner_eligible=True, priority="P1_HIGH", value_score=0.8,
        update_target="UPDATE_CAUSAL_KNOWLEDGE",
        candidate_targets=("UPDATE_CAUSAL_KNOWLEDGE",), operation="REVISE",
        failure_type="STATE_SHIFT", state_resolution_id="resolution-original",
        knowledge_refs=(parent.object_id,), evidence_refs=("transition-a",),
        rationale="bind admitted StateShift plan to deterministic source",
        shadow_only=True,
    )


def test_state_rebase_derives_new_plan_without_changing_admitted_plan(tmp_tehm):
    conn, _, _ = tmp_tehm
    parent = _claim()
    register_knowledge(
        conn, parent, target_scope="flow_feasibility", evidence_refs=[{
            "evidence_type": "manual_review", "evidence_id": "rebase-seed",
            "split": "training", "lineage_id": "lineage-a",
            "evidence_level": parent.evidence_level,
        }])
    state = resolve_current_state(
        conn, {"mechanism_family": "DENSITY_RELIEF",
               "target_scope": "flow_feasibility"},
        mode="shadow", persist=False)
    plan = _plan(parent)
    receipt, rebased = rebase_localized_update_plan(
        plan, state,
        source_database_digest="sha256:" + "1" * 64,
        evidence_refs=("sha256:" + "2" * 64,),
    )
    assert plan.state_resolution_id == "resolution-original"
    assert rebased.state_resolution_id == state.resolution_id
    assert rebased.plan_digest != plan.plan_digest
    assert receipt.admitted_plan_digest == plan.plan_digest
    assert receipt.source_semantic_digest == state_semantic_digest(state)
    assert receipt.receipt_digest in rebased.evidence_refs
    assert receipt.evaluation_only is True
    assert receipt.canonical_memory_mutation == "none"
    assert receipt.production_runtime_imported is False

    payload = receipt.to_dict()
    assert StateResolutionRebaseReceipt.from_dict(payload) == receipt
    payload["source_resolution_id"] = "resolution-tampered"
    with pytest.raises(StateResolutionRebaseError, match="(ID|digest) mismatch"):
        StateResolutionRebaseReceipt.from_dict(payload)


def test_state_rebase_rejects_missing_planned_parent(tmp_tehm):
    conn, _, _ = tmp_tehm
    parent = _claim()
    state = resolve_current_state(
        conn, {"target_scope": "flow_feasibility"},
        mode="shadow", persist=False)
    plan = _plan(parent)
    with pytest.raises(StateResolutionRebaseError,
                       match="lacks planned Knowledge parent"):
        rebase_localized_update_plan(
            plan, state,
            source_database_digest="sha256:" + "1" * 64,
            evidence_refs=("sha256:" + "2" * 64,),
        )
