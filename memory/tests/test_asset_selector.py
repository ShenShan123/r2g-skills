"""P7 knowledge-grounded Asset Memory selection firewall tests."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts import MemoryQuery, MemoryRoutingDecision
from tehm.assets import (
    bind_asset_to_repair_context, bind_rtl_asset_to_project,
    build_rtl_asset_proposal, register_asset,
    set_asset_status,
)
from tehm.knowledge import MechanismKnowledge
from tehm.retrieval import asset_selector
from tehm.retrieval.asset_selector import (
    AssetSelectionReceipt, AssetSelectorError,
    select_knowledge_grounded_assets,
)
from tehm.retrieval.structured_candidate import (
    StructuredCandidateError, build_structured_candidate,
)


PROJECTS = Path(__file__).resolve().parent / "fixtures" / "rtl_projects"


def _query() -> MemoryQuery:
    return MemoryQuery(query_plan={
        "mechanism_family": "HANDSHAKE_COMPLETION",
        "compatibility_profile": "rtl.fsm.single_guard.v1",
    })


def _claim() -> MechanismKnowledge:
    return MechanismKnowledge(
        knowledge_id="mk_selector", version=1,
        mechanism_family="HANDSHAKE_COMPLETION",
        compatibility_profile="rtl.fsm.single_guard.v1",
        antecedent={"failure": "completion_not_observed"},
        intervention={"family": "GUARD_RESTORE"}, mediated_effects=(),
        expected_outcome={"outcome": "PASS"}, positive_applicability=({
            "mechanism_family": "HANDSHAKE_COMPLETION",
            "compatibility_profile": "rtl.fsm.single_guard.v1",
        },), negative_applicability=(), preserved_obligations=(),
        known_failure_modes=(), causal_path_ids=("path-selector",),
        evidence_level="L3_REPLICATED_EFFECT", support_lineages=("l1", "l2"),
        status="validated")


def _routing(asset_id: str, state_id: str = "state-selector") -> MemoryRoutingDecision:
    return MemoryRoutingDecision(
        decision="CONSIDER", resolved_state_id=state_id,
        selected_rule_ids=(), selected_path_ids=("path-selector",),
        selected_asset_ids=(asset_id,),
        applicability={"status": "APPLICABLE", "validated_knowledge_count": 1},
        causal_support={"status": "SUPPORTED", "causal_path_ids": ["path-selector"]},
        risk={}, abstain_reasons=(), no_memory_budget=1, memory_budget=1)


def _asset(conn, *, knowledge_ids: tuple[str, ...] = ("mk_selector@1",),
           bound: bool = True) -> str:
    proposal = build_rtl_asset_proposal(
        {"gap_id": "selector-gap"}, name="selector.asset",
        transformation_family="GUARD_STRENGTHEN",
        action_payload_template={
            "domain": "rtl.GUARD_STRENGTHEN", "module": "req_ack_fsm",
            "source_state": "SEND", "target_state": "DONE", "add_condition": "ack",
        }, compatibility_profile="rtl.fsm.single_guard.v1",
        verifier_obligations=("TARGET",), mechanism_knowledge_ids=knowledge_ids)
    raw = proposal.to_dict()
    if bound:
        raw = bind_rtl_asset_to_project(
            raw, PROJECTS / "req_ack_bug",
            expected_mechanism_family="HANDSHAKE_COMPLETION")
    receipt = register_asset(
        conn, asset_type=raw["asset_type"], name=raw["name"], version=raw["version"],
        definition=raw["definition"], input_contract=raw["input_contract"],
        output_contract=raw["output_contract"], verifier_contract=raw["verifier_contract"],
        compatibility=raw["compatibility"], provenance=raw["provenance"])
    set_asset_status(conn, asset_id=receipt.asset_id,
                     target_scope=receipt.target_scope, status="shadow")
    set_asset_status(conn, asset_id=receipt.asset_id,
                     target_scope=receipt.target_scope, status="candidate")
    return receipt.asset_id


def _patch_shadow_state(monkeypatch, asset_id: str, claim: MechanismKnowledge):
    state = SimpleNamespace(
        resolution_id="state-selector", unresolved_conflicts=(),
        active_assets=(asset_id,), active_knowledge_claims=(claim.object_id,),
        active_causal_paths=("path-selector",))
    monkeypatch.setattr(asset_selector, "resolve_current_state",
                        lambda *_args, **_kwargs: state)
    import tehm.retrieval.memory_router as router

    monkeypatch.setattr(router, "_knowledge_for_state", lambda *_args, **_kwargs: (
        [{"claim": claim, "path_ids": ("path-selector",)}], (), (), ()))


def test_strict_selector_requires_knowledge_and_binding_proof(tmp_tehm, monkeypatch):
    conn, _, _ = tmp_tehm
    asset_id = _asset(conn, knowledge_ids=(), bound=True)
    claim = _claim()
    _patch_shadow_state(monkeypatch, asset_id, claim)
    result = select_knowledge_grounded_assets(conn, _query(), routing=_routing(asset_id))
    assert result.assets == ()
    assert result.receipt.decision == "ABSTAIN"
    assert any("knowledge_binding_missing" in reason
               for reason in result.receipt.abstain_reasons)
    assert result.receipt.shadow_only is True


def test_strict_selector_returns_one_bound_asset_without_runtime_mutation(tmp_tehm, monkeypatch):
    conn, _, _ = tmp_tehm
    asset_id = _asset(conn, knowledge_ids=("mk_selector@1",), bound=True)
    claim = _claim()
    _patch_shadow_state(monkeypatch, asset_id, claim)
    before = conn.execute(
        "SELECT COUNT(*) FROM tehm_asset_authority_receipts").fetchone()[0]
    result = select_knowledge_grounded_assets(conn, _query(), routing=_routing(asset_id))
    assert result.receipt.decision == "SELECT"
    assert result.receipt.selected_asset_ids == (asset_id,)
    assert result.assets[0]["asset_id"] == asset_id
    assert result.receipt.binding["assets"][asset_id]["binding_contract"] == "manifest_fix_v1"
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_asset_authority_receipts").fetchone()[0] == before


def test_compatibility_mode_keeps_legacy_unbound_fixture_observable(tmp_tehm, monkeypatch):
    conn, _, _ = tmp_tehm
    asset_id = _asset(conn, knowledge_ids=(), bound=False)
    claim = _claim()
    _patch_shadow_state(monkeypatch, asset_id, claim)
    result = select_knowledge_grounded_assets(
        conn, _query(), routing=_routing(asset_id), compatibility_mode=True)
    assert result.receipt.decision == "SELECT"
    assert result.receipt.binding["compatibility_mode"] is True


def test_selector_receipt_replays_and_production_is_unavailable(tmp_tehm):
    conn, _, _ = tmp_tehm
    receipt = AssetSelectionReceipt(
        decision="NO_SKILL", resolved_state_id="state", routing_receipt_id="routing_test",
        knowledge_object_ids=(), selected_asset_ids=(), applicability={},
        causal_support={}, binding={}, abstain_reasons=("no_skill",), candidate_budget=1)
    replay = AssetSelectionReceipt.from_dict({
        **receipt.to_dict(), "receipt_digest": receipt.receipt_digest})
    assert replay == receipt
    with pytest.raises(AssetSelectorError, match="digest mismatch"):
        AssetSelectionReceipt.from_dict({
            **receipt.to_dict(), "receipt_digest": "sha256:tampered"})
    with pytest.raises(Exception, match="shadow-only"):
        select_knowledge_grounded_assets(conn, _query(), mode="production")


def test_structured_candidate_rechecks_state_knowledge_asset_and_binding(
        tmp_tehm, monkeypatch):
    conn, _, _ = tmp_tehm
    asset_id = _asset(conn, knowledge_ids=("mk_selector@1",), bound=True)
    claim = _claim()
    _patch_shadow_state(monkeypatch, asset_id, claim)
    routing = _routing(asset_id)
    selection = select_knowledge_grounded_assets(conn, _query(), routing=routing)
    binding = bind_asset_to_repair_context(
        selection.assets[0], claim,
        {"design_id": "selector-design", "structural_graph": {
            "nodes": [{"kind": "module", "label": "req_ack_fsm"}]},
            "reports": {"target": "FAIL"}},
        {"selected_binding": {"module": "req_ack_fsm"}})
    candidate = build_structured_candidate(_query(), routing, selection, binding)
    assert candidate.evaluation_only is True
    assert candidate.asset_id == asset_id
    assert candidate.knowledge_object_id == claim.object_id
    assert candidate.causal_path_ids == ("path-selector",)
    assert candidate.candidate_id.startswith("structured_candidate_")
    assert candidate.to_dict()["candidate_digest"] == candidate.candidate_digest
    assert candidate.receipt().evaluation_only is True


def test_structured_candidate_rejects_mismatched_state_or_ineligible_binding(
        tmp_tehm, monkeypatch):
    conn, _, _ = tmp_tehm
    asset_id = _asset(conn, knowledge_ids=("mk_selector@1",), bound=True)
    claim = _claim()
    _patch_shadow_state(monkeypatch, asset_id, claim)
    routing = _routing(asset_id)
    selection = select_knowledge_grounded_assets(conn, _query(), routing=routing)
    bad_binding = {"asset_id": asset_id, "knowledge_id": claim.object_id,
                   "eligible": False, "binding_digest": "sha256:bad",
                   "selected_binding": {"module": "req_ack_fsm"}}
    with pytest.raises(StructuredCandidateError, match="not eligible"):
        build_structured_candidate(_query(), routing, selection, bad_binding)
    bad_routing = MemoryRoutingDecision(
        decision="CONSIDER", resolved_state_id="different-state",
        selected_rule_ids=(), selected_path_ids=("path-selector",),
        selected_asset_ids=(asset_id,), applicability=routing.applicability,
        causal_support=routing.causal_support, risk={}, abstain_reasons=(),
        no_memory_budget=1, memory_budget=1)
    good_binding = {**bad_binding, "eligible": True}
    with pytest.raises(StructuredCandidateError, match="state IDs differ"):
        build_structured_candidate(_query(), bad_routing, selection, good_binding)
