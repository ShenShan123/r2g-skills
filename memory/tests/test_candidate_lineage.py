"""P14 structured-candidate lineage witness tests."""
from __future__ import annotations

from dataclasses import replace

import pytest

from contracts import MemoryRoutingDecision
from tehm.assets.receipts import RuntimeBindingReceipt
from tehm.capability import CandidateLineageError, build_candidate_lineage
from tehm.evaluation.candidate_executor import execute_candidate
from tehm.retrieval.asset_selector import AssetSelection, AssetSelectionReceipt
from tehm.retrieval.structured_candidate import StructuredRepairCandidate


def _bundle():
    routing = MemoryRoutingDecision(
        decision="CONSIDER", resolved_state_id="state-lineage",
        selected_rule_ids=(), selected_path_ids=("path-lineage",),
        selected_asset_ids=("asset-lineage",), applicability={"status": "APPLICABLE"},
        causal_support={"status": "SUPPORTED"}, risk={}, abstain_reasons=(),
        no_memory_budget=2, memory_budget=1)
    selection_receipt = AssetSelectionReceipt(
        decision="SELECT", resolved_state_id="state-lineage",
        routing_receipt_id=routing.routing_receipt_id,
        knowledge_object_ids=("mk-lineage@1",),
        selected_asset_ids=("asset-lineage",), applicability={"status": "APPLICABLE"},
        causal_support={"causal_path_ids": ["path-lineage"]}, binding={},
        candidate_budget=1)
    selection = AssetSelection(
        assets=({"asset_id": "asset-lineage"},), receipt=selection_receipt)
    binding = RuntimeBindingReceipt(
        asset_id="asset-lineage", knowledge_id="mk-lineage@1",
        target_design="design-lineage", candidate_entities=("module:top",),
        selected_binding={"module": "top"}, structural_evidence=("graph:1",),
        failure_evidence=("trace:1",), ambiguity_count=0, eligible=True,
        reason="unique", binding_digest="sha256:binding-lineage")
    candidate = StructuredRepairCandidate(
        candidate_id="candidate-lineage", resolved_state_id="state-lineage",
        knowledge_object_id="mk-lineage@1", causal_path_ids=("path-lineage",),
        asset_id="asset-lineage", action_family="GUARD_STRENGTHEN",
        concrete_action={"payload": {"module": "top"}},
        applicability_receipt_id=selection_receipt.selection_receipt_id,
        binding_receipt_id=binding.binding_receipt_id, obligations=("TARGET_PASS",),
        evidence_level="L3_REPLICATED_EFFECT", authority={"eligible": True}, risk={},
        provenance={
            "source": "test", "evaluation_only": True,
            "routing_receipt_id": routing.routing_receipt_id,
            "asset_selection_receipt_id": selection_receipt.selection_receipt_id,
            "binding_digest": binding.binding_digest,
        })
    execution = execute_candidate(
        candidate, {"case_id": "case-lineage", "toolchain_digest": "sha256:tool"})
    return candidate, routing, selection, binding, execution


def test_candidate_lineage_binds_structured_candidate_to_execution():
    candidate, routing, selection, binding, execution = _bundle()
    lineage = build_candidate_lineage(
        candidate=candidate, routing=routing, asset_selection=selection,
        runtime_binding=binding, execution=execution)
    assert lineage.eligible is True
    assert lineage.candidate_id == candidate.candidate_id
    assert lineage.candidate_digest == candidate.candidate_digest
    assert lineage.execution_receipt_digest == execution.execution_digest
    assert lineage.asset_selection_receipt_id == selection.receipt.selection_receipt_id
    assert lineage.receipt_digest.startswith("sha256:")
    replay = type(lineage).from_dict({
        **lineage.to_dict(), "receipt_digest": lineage.receipt_digest})
    assert replay == lineage
    serialized = build_candidate_lineage(
        candidate=candidate.to_dict(), routing=routing.to_dict(),
        asset_selection=selection.to_dict(),
        runtime_binding=binding.to_dict(), execution=execution.to_dict())
    assert serialized == lineage


def test_candidate_lineage_fails_closed_on_execution_candidate_drift():
    candidate, routing, selection, binding, execution = _bundle()
    bad_execution = {**execution.to_dict(), "candidate_digest": "sha256:drift"}
    try:
        build_candidate_lineage(
            candidate=candidate, routing=routing, asset_selection=selection,
            runtime_binding=binding, execution=bad_execution)
    except CandidateLineageError as exc:
        assert "candidate digest mismatch" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("candidate lineage accepted execution drift")


@pytest.mark.parametrize("changes", [
    {"decision": "NO_SKILL", "memory_budget": 0, "selected_asset_ids": ()},
    {"memory_budget": 0}, {"selected_path_ids": ()},
])
def test_lineage_rejects_route_without_memory_authorization_or_paths(changes):
    candidate, routing, selection, binding, execution = _bundle()
    routing = replace(routing, **changes)
    with pytest.raises(CandidateLineageError, match="authorize memory|paths differ"):
        build_candidate_lineage(candidate=candidate, routing=routing,
            asset_selection=selection, runtime_binding=binding, execution=execution)


@pytest.mark.parametrize("changes", [
    {"candidate_budget": 0}, {"causal_support": {"causal_path_ids": []}},
])
def test_lineage_rejects_selection_without_budget_or_paths(changes):
    candidate, routing, selection, binding, execution = _bundle()
    selection = replace(selection, receipt=replace(selection.receipt, **changes))
    with pytest.raises(CandidateLineageError, match="budget is exhausted|paths differ"):
        build_candidate_lineage(candidate=candidate, routing=routing,
            asset_selection=selection, runtime_binding=binding, execution=execution)
