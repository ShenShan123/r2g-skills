"""Candidate construction must consume the same affirmative routing decision.

These receipts are unit fixtures, not database authority or execution evidence.
"""
from dataclasses import replace

import pytest

from tehm.retrieval.structured_candidate import (
    StructuredCandidateError, build_structured_candidate,
)
from test_candidate_lineage import _bundle


def _inputs():
    _, routing, selection, binding, _ = _bundle()
    asset = {
        "asset_id": "asset-lineage",
        "definition": {"action": {
            "domain": "rtl.GUARD_STRENGTHEN",
            "transformation_family": "GUARD_STRENGTHEN", "payload": {}}},
        "verifier_contract": {"obligations": ["TARGET_PASS"]},
    }
    return routing, replace(selection, assets=(asset,)), binding


def _rebind(selection, routing, **changes):
    return replace(selection, receipt=replace(
        selection.receipt, routing_receipt_id=routing.routing_receipt_id, **changes))


def test_affirmative_route_builds_candidate_with_matching_receipt():
    routing, selection, binding = _inputs()
    candidate = build_structured_candidate(None, routing, selection, binding)
    assert candidate.provenance["routing_receipt_id"] == routing.routing_receipt_id
    assert candidate.causal_path_ids == routing.selected_path_ids


@pytest.mark.parametrize("decision", ["NO_SKILL", "ABSTAIN", "INAPPLICABLE"])
def test_rejected_route_cannot_be_resurrected_by_selected_asset(decision):
    routing, selection, binding = _inputs()
    rejected = replace(routing, decision=decision, selected_asset_ids=(),
                       selected_path_ids=(), memory_budget=0, no_memory_budget=3)
    with pytest.raises(StructuredCandidateError, match="does not authorize"):
        build_structured_candidate(None, rejected, _rebind(selection, rejected), binding)


def test_zero_memory_budget_cannot_construct_memory_candidate():
    routing, selection, binding = _inputs()
    routing = replace(routing, memory_budget=0, no_memory_budget=3)
    with pytest.raises(StructuredCandidateError, match="does not authorize"):
        build_structured_candidate(None, routing, _rebind(selection, routing), binding)


def test_same_state_different_route_rejects_stale_selection():
    routing, selection, binding = _inputs()
    changed = replace(routing, risk={"risk_penalty": 0.5})
    assert changed.resolved_state_id == selection.receipt.resolved_state_id
    with pytest.raises(StructuredCandidateError, match="different routing receipt"):
        build_structured_candidate(None, changed, selection, binding)


@pytest.mark.parametrize("route_paths,support_paths", [
    ((), ["path-lineage"]), (("path-lineage",), []),
    (("path-other",), ["path-lineage"]),
])
def test_path_support_cannot_be_borrowed_from_only_one_receipt(route_paths, support_paths):
    routing, selection, binding = _inputs()
    routing = replace(routing, selected_path_ids=route_paths)
    selection = _rebind(selection, routing, causal_support={"causal_path_ids": support_paths})
    with pytest.raises(StructuredCandidateError, match="causal path agreement"):
        build_structured_candidate(None, routing, selection, binding)


def test_selected_asset_must_be_selected_by_router():
    routing, selection, binding = _inputs()
    routing = replace(routing, selected_asset_ids=("another-asset",))
    with pytest.raises(StructuredCandidateError, match="not selected by routing"):
        build_structured_candidate(None, routing, _rebind(selection, routing), binding)


def test_definition_must_match_selected_asset_identity():
    routing, selection, binding = _inputs()
    selection = replace(selection, assets=({**selection.assets[0], "asset_id": "wrong"},))
    with pytest.raises(StructuredCandidateError, match="definition does not match"):
        build_structured_candidate(None, routing, selection, binding)


def test_selection_cannot_spend_zero_candidate_budget():
    routing, selection, binding = _inputs()
    selection = _rebind(selection, routing, candidate_budget=0)
    with pytest.raises(StructuredCandidateError, match="budget is exhausted"):
        build_structured_candidate(None, routing, selection, binding)
