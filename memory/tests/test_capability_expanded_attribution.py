"""P8 KnowledgeDelta/AssetDelta attribution witness tests."""
from __future__ import annotations

from dataclasses import replace

from contracts import MemoryRoutingDecision
from tehm.capability import (
    AssetDeltaReceipt, KnowledgeDeltaReceipt,
    create_policy_snapshot, evaluate_asset_delta,
    evaluate_capability_attribution, evaluate_capability_attribution_from_db,
    evaluate_knowledge_delta, record_capability_authority, record_policy_load,
    register_capability, validate_expanded_attribution,
    verify_capability_authority,
)
from tehm.evolution.attribution import MemoryFailureAttributionReceipt
from tehm.state import StateResolutionReceipt, resolve_current_state


BASELINE = "sha256:memory-baseline"
CANDIDATE = "sha256:memory-candidate"


def _memory_delta():
    return {
        "version": "memory-delta-v1",
        "baseline_memory_digest": BASELINE,
        "candidate_memory_digest": CANDIDATE,
        "added_knowledge_ids": ["mk_selector@1"],
        "added_asset_ids": ["asset_selector"],
    }


def _expanded():
    routing = MemoryRoutingDecision(
        decision="CONSIDER", resolved_state_id="resolution-p8",
        selected_rule_ids=(), selected_path_ids=("path-p8",),
        selected_asset_ids=("asset_selector",), applicability={"status": "APPLICABLE"},
        causal_support={"status": "SUPPORTED"}, risk={}, abstain_reasons=(),
        no_memory_budget=2, memory_budget=1)
    state = {
        "resolution_id": "resolution-p8",
        "input_memory_digest": BASELINE,
        "resolution_digest": "sha256:resolution-p8",
        "relation_count": 1,
        "unresolved_conflicts": [],
    }
    failure = MemoryFailureAttributionReceipt(
        activation_id=None, transition_id="transition-p8", failure_type="NO_FAILURE",
        recommended_update_layers=("UPDATE_NONE",))
    knowledge = evaluate_knowledge_delta(BASELINE, CANDIDATE, {
        "version": "knowledge-delta-v1",
        "baseline_memory_digest": BASELINE,
        "candidate_memory_digest": CANDIDATE,
        "added_knowledge_ids": ["mk_selector@1"],
        "removed_knowledge_ids": [], "revised_knowledge_ids": [],
    })
    asset = evaluate_asset_delta(BASELINE, CANDIDATE, {
        "version": "asset-delta-v1",
        "baseline_memory_digest": BASELINE,
        "candidate_memory_digest": CANDIDATE,
        "added_asset_ids": ["asset_selector"],
        "removed_asset_ids": [], "revised_asset_ids": [],
    })
    return knowledge, asset, routing, state, failure


def test_typed_deltas_are_content_bound_and_replayable():
    knowledge, asset, *_ = _expanded()
    assert isinstance(knowledge, KnowledgeDeltaReceipt)
    assert isinstance(asset, AssetDeltaReceipt)
    assert KnowledgeDeltaReceipt.from_dict({
        **knowledge.to_dict(), "receipt_digest": knowledge.receipt_digest}) == knowledge
    assert AssetDeltaReceipt.from_dict({
        **asset.to_dict(), "receipt_digest": asset.receipt_digest}) == asset
    invalid = evaluate_knowledge_delta(BASELINE, CANDIDATE, {
        "version": "knowledge-delta-v1", "added_knowledge_ids": ["mk@1"],
        "removed_knowledge_ids": ["mk@1"], "revised_knowledge_ids": [],
    })
    assert invalid.eligible is False
    assert "knowledge:delta_sets_overlap" in invalid.reasons


def test_strict_expanded_attribution_binds_all_p8_witnesses():
    knowledge, asset, routing, state, failure = _expanded()
    expanded, reasons = validate_expanded_attribution(
        baseline_memory_digest=BASELINE, candidate_memory_digest=CANDIDATE,
        knowledge_delta=knowledge, asset_delta=asset,
        routing_receipts=[routing], state_resolution_receipt=state,
        failure_attribution_receipts=[failure], strict=True,
        memory_changed_ids=("mk_selector@1", "asset_selector"))
    assert reasons == ()
    assert expanded["eligible"] is True
    assert expanded["routing_receipts"][0]["routing_receipt_id"] == routing.routing_receipt_id

    receipt = evaluate_capability_attribution(
        capability_id="capability-p8",
        baseline={"memory_digest": BASELINE, "policy_digest": "p0",
                  "behavior_digest": "b0"},
        candidate={"memory_digest": CANDIDATE, "policy_digest": "p1",
                   "behavior_digest": "b1", "target_gain": True,
                   "no_regression": True},
        runtime_receipt={"loaded": True, "policy_digest": "p1"},
        heldout={"verdict": "PASS", "disjoint_lineage": True, "evidence_id": "h1"},
        ablation={"gain_without_memory": False, "gain_with_memory": True},
        memory_delta=_memory_delta(), knowledge_delta=knowledge,
        asset_delta=asset, routing_receipts=[routing],
        state_resolution_receipt=state, failure_attribution_receipts=[failure],
        strict_expanded=True)
    assert receipt.promotable is True
    assert receipt.expanded_eligible is True
    assert receipt.detail["expanded_attribution"]["eligible"] is True


def test_strict_expanded_attribution_fails_closed_on_missing_or_mismatched_witness():
    knowledge, asset, routing, state, failure = _expanded()
    _, reasons = validate_expanded_attribution(
        baseline_memory_digest=BASELINE, candidate_memory_digest=CANDIDATE,
        knowledge_delta=knowledge, asset_delta=asset,
        routing_receipts=[routing], state_resolution_receipt={
            **state, "resolution_id": "other-resolution"},
        failure_attribution_receipts=[failure], strict=True,
        memory_changed_ids=("mk_selector@1", "asset_selector"))
    assert "routing_state_resolution_mismatch" in reasons
    _, missing = validate_expanded_attribution(
        baseline_memory_digest=BASELINE, candidate_memory_digest=CANDIDATE,
        strict=True, memory_changed_ids=())
    assert "knowledge_delta_required" in missing
    assert "asset_delta_required" in missing
    assert "routing_receipts_malformed" in missing

    wrong_knowledge = evaluate_knowledge_delta("sha256:other", CANDIDATE, {
        "version": "knowledge-delta-v1", "added_knowledge_ids": ["mk_selector@1"],
        "removed_knowledge_ids": [], "revised_knowledge_ids": [],
    })
    mismatched = evaluate_capability_attribution(
        capability_id="capability-p8-mismatch",
        baseline={"memory_digest": BASELINE, "policy_digest": "p0",
                  "behavior_digest": "b0"},
        candidate={"memory_digest": CANDIDATE, "policy_digest": "p1",
                   "behavior_digest": "b1", "target_gain": True,
                   "no_regression": True},
        runtime_receipt={"loaded": True, "policy_digest": "p1"},
        heldout={"verdict": "PASS", "disjoint_lineage": True, "evidence_id": "h1"},
        ablation={"gain_without_memory": False, "gain_with_memory": True},
        memory_delta=_memory_delta(), knowledge_delta=wrong_knowledge,
        asset_delta=asset, routing_receipts=[routing],
        state_resolution_receipt=state, failure_attribution_receipts=[failure],
        strict_expanded=True)
    assert mismatched.expanded_eligible is False
    assert "P8:knowledge_delta_memory_binding_mismatch" in mismatched.missing_gates


def test_db_authority_replays_p8_witness_bundle(tmp_tehm):
    conn, _, _ = tmp_tehm
    capability = register_capability(
        conn, mechanism_family="P8", applicability={}, status="candidate")
    baseline = create_policy_snapshot(
        conn, memory_snapshot_id=BASELINE, promoted_rules=["r0"])
    candidate = create_policy_snapshot(
        conn, memory_snapshot_id=CANDIDATE, promoted_rules=["r0", "r1"])
    baseline_load = record_policy_load(
        conn, policy_snapshot_id=baseline.policy_snapshot_id,
        runtime_id="p8-runtime", receipt={
            "execution_receipt_id": "exec-p8-baseline", "behavior_digest": "b0"})
    record_policy_load(
        conn, policy_snapshot_id=candidate.policy_snapshot_id,
        runtime_id="p8-runtime", receipt={
            "execution_receipt_id": "exec-p8-candidate", "behavior_digest": "b1"})
    state = resolve_current_state(
        conn, {"target_scope": "global"}, mode="shadow", persist=True, commit=True)
    state_receipt = StateResolutionReceipt(
        resolution_id=state.resolution_id,
        input_memory_digest=state.input_memory_digest,
        resolution_digest=state.resolution_digest,
        relation_count=len(state.relation_ids),
        unresolved_conflicts=state.unresolved_conflicts)
    knowledge, asset, routing, _, failure = _expanded()
    routing = replace(routing, resolved_state_id=state.resolution_id)
    attribution = evaluate_capability_attribution_from_db(
        conn, capability_id=capability.capability_id,
        baseline_memory_digest=BASELINE, candidate_memory_digest=CANDIDATE,
        baseline_policy_snapshot_id=baseline.policy_snapshot_id,
        candidate_policy_snapshot_id=candidate.policy_snapshot_id,
        runtime_id="p8-runtime", baseline_behavior_digest="b0",
        candidate_behavior_digest="b1", target_gain=True, no_regression=True,
        heldout={"verdict": "PASS", "disjoint_lineage": True, "evidence_id": "h-p8"},
        ablation={"policy_snapshot_id": baseline.policy_snapshot_id,
                  "runtime_receipt_id": "exec-p8-baseline",
                  "policy_load_receipt_id": baseline_load.receipt_id,
                  "behavior_digest": "b0", "gain_without_memory": False,
                  "gain_with_memory": True}, memory_delta=_memory_delta(),
        knowledge_delta=knowledge, asset_delta=asset,
        routing_receipts=[routing], state_resolution_receipt=state_receipt,
        failure_attribution_receipts=[failure], strict_expanded=True)
    assert attribution.promotable is True
    refs = {
        f"C{index}": {"evidence_id": f"p8-{index}",
                       "split": "ab" if index in {1, 2, 3, 8} else
                       "training" if index in {4, 5} else "heldout",
                       "verdict": "PASS"}
        for index in range(1, 9)
    }
    refs["C4"]["execution_receipt_id"] = "exec-p8-candidate"
    authority = record_capability_authority(
        conn, capability_id=capability.capability_id,
        attribution_receipt=attribution, evidence_refs=refs,
        candidate_policy_snapshot_id=candidate.policy_snapshot_id,
        runtime_id="p8-runtime", gates={f"C{index}": True for index in range(1, 9)})
    assert authority.eligible is True, authority.reasons
    checked = verify_capability_authority(
        conn, capability.capability_id, authority)
    assert checked["eligible"] is True, checked
    payload = dict(authority.payload)
    payload["expanded_attribution"] = {
        **payload["expanded_attribution"], "eligible": False}
    tampered = authority.to_dict()
    tampered["payload"] = payload
    assert verify_capability_authority(
        conn, capability.capability_id, tampered)["eligible"] is False
