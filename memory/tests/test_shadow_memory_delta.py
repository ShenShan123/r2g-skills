"""P13 shadow receipts bind concrete P14 memory deltas."""
from __future__ import annotations

import hashlib

import pytest

from tehm.capability import MemoryDeltaReceipt, memory_delta_from_shadow_update
from tehm.capability.attribution import (
    evaluate_capability_attribution, evaluate_capability_attribution_from_db,
)
from tehm.capability.authority import (
    _memory_delta_binding, record_capability_authority,
    verify_capability_authority,
)
from tehm.capability.policy_snapshot import create_policy_snapshot, record_policy_load
from tehm.capability.registry import register_capability
from tehm.evolution import AppliedShadowUpdateReceipt
from tehm.ids import stable_dumps


def _digest(char: str) -> str:
    return "sha256:" + char * 64


def _receipt(*, created_objects=("knowledge:claim@2", "asset:asset-1"),
             created_relations=("relation-1",), source_after=None,
             raw_after=None, staging_after=None):
    payload = {
        "version": "shadow-update-v0.1",
        "plan_digest": _digest("a"),
        "transition_id": "transition:shadow",
        "campaign_id": "campaign-shadow",
        "update_target": "UPDATE_CAUSAL_KNOWLEDGE",
        "operation": "SPECIALIZE",
        "created_object_ids": tuple(created_objects),
        "created_relation_ids": tuple(created_relations),
        "before_resolution_id": "resolution-before",
        "after_resolution_id": "resolution-after",
        "canonical_rows_changed": False,
        "production_authority_changed": False,
        "source_digest_before": _digest("1"),
        "source_digest_after": source_after or _digest("1"),
        "staging_digest_before": _digest("1"),
        "staging_digest_after": staging_after or _digest("2"),
        "raw_evidence_before_digest": _digest("3"),
        "raw_evidence_after_digest": raw_after or _digest("3"),
        "raw_evidence_preserved": True,
        "staging_discarded": True,
        "canonical_memory_mutation": "none",
        "lifecycle_mutation": "isolated_staging_only",
        "production_runtime_imported": False,
        "metadata": {},
    }
    replay = "sha256:" + hashlib.sha256(
        stable_dumps(payload).encode()).hexdigest()
    return AppliedShadowUpdateReceipt(**payload, replay_digest=replay)


def test_shadow_receipt_derives_typed_memory_delta():
    delta = memory_delta_from_shadow_update(_receipt())
    assert delta.eligible is True
    assert delta.baseline_memory_digest == _digest("1")
    assert delta.candidate_memory_digest == _digest("2")
    assert delta.delta["added_knowledge_ids"] == ["knowledge:claim@2"]
    assert delta.delta["added_asset_ids"] == ["asset:asset-1"]
    assert delta.added_relation_ids == ("relation-1",)


def test_memory_delta_receipt_is_content_addressed_and_replayable():
    receipt = memory_delta_from_shadow_update(_receipt())
    payload = {**receipt.to_dict(), "receipt_digest": receipt.receipt_digest}
    assert MemoryDeltaReceipt.from_dict(payload) == receipt
    payload["changed_ids"] = []
    with pytest.raises(ValueError, match="replay mismatch"):
        MemoryDeltaReceipt.from_dict(payload)


def test_shadow_receipt_ignores_derived_bookkeeping_rows_but_needs_memory_object():
    delta = memory_delta_from_shadow_update(_receipt(
        created_objects=("rule_revision:revision-1", "episode:episode-1"),
        created_relations=()))
    assert delta.eligible is False
    assert "changed_memory_object_required" in delta.reasons


def test_shadow_receipt_rejects_unknown_created_object_type():
    with pytest.raises(ValueError, match="unsupported"):
        memory_delta_from_shadow_update(_receipt(
            created_objects=("unknown:object-1",)))


def test_shadow_receipt_rejects_source_or_raw_evidence_drift():
    with pytest.raises(ValueError, match="source digest changed"):
        memory_delta_from_shadow_update(_receipt(source_after=_digest("4")))
    with pytest.raises(ValueError, match="raw evidence digest changed"):
        memory_delta_from_shadow_update(_receipt(raw_after=_digest("4")))


def test_shadow_receipt_rejects_unbound_or_malformed_staging_digest():
    with pytest.raises(ValueError, match="staging digest is not a sha256 digest"):
        memory_delta_from_shadow_update(_receipt(staging_after="sha256:not-hex"))


def test_shadow_receipt_is_the_c1_attribution_source_of_truth():
    shadow = _receipt()
    source_digest = _digest("1")
    staging_digest = _digest("2")
    attribution = evaluate_capability_attribution(
        capability_id="capability-shadow-c1",
        baseline={"memory_digest": source_digest, "policy_digest": "policy-0",
                  "behavior_digest": "behavior-0"},
        candidate={"memory_digest": staging_digest, "policy_digest": "policy-1",
                   "behavior_digest": "behavior-1", "target_gain": True,
                   "no_regression": True},
        runtime_receipt={"loaded": True, "policy_digest": "policy-1"},
        heldout={"verdict": "PASS", "disjoint_lineage": True,
                 "evidence_id": "heldout-shadow"},
        ablation={"gain_without_memory": False, "gain_with_memory": True},
        shadow_update_receipt=shadow)
    assert attribution.gates["C1"] is True
    assert attribution.detail["shadow_update_receipt"]["receipt_digest"] == (
        shadow.receipt_digest)
    assert attribution.detail["memory_delta"]["delta"]["added_knowledge_ids"] == [
        "knowledge:claim@2"]
    assert attribution.detail["memory_delta"]["receipt_digest"] == (
        memory_delta_from_shadow_update(shadow).receipt_digest)


def test_authority_replay_rederives_shadow_receipt_delta():
    shadow = _receipt()
    delta = memory_delta_from_shadow_update(shadow)
    detail = {
        "baseline": {"memory_digest": delta.baseline_memory_digest},
        "candidate": {"memory_digest": delta.candidate_memory_digest},
        "memory_delta": delta.to_dict(),
        "shadow_update_receipt": {
            **shadow.to_dict(), "receipt_digest": shadow.receipt_digest,
        },
    }
    _, reasons = _memory_delta_binding({"detail": detail})
    assert reasons == []

    tampered = dict(detail["shadow_update_receipt"])
    tampered["staging_digest_after"] = _digest("9")
    _, reasons = _memory_delta_binding({
        "detail": {**detail, "shadow_update_receipt": tampered},
    })
    assert any("shadow_update_receipt_invalid" in reason for reason in reasons)

    tampered_delta = dict(detail["memory_delta"])
    tampered_delta["receipt_digest"] = _digest("f")
    _, reasons = _memory_delta_binding({
        "detail": {**detail, "memory_delta": tampered_delta},
    })
    assert "C1:memory_delta_receipt_invalid:memory delta receipt digest mismatch" in reasons


def test_authority_persists_shadow_receipt_and_replays_it(tmp_tehm):
    conn, _, _ = tmp_tehm
    shadow = _receipt()
    source_digest = _digest("1")
    candidate_digest = _digest("2")
    capability = register_capability(
        conn, mechanism_family="SHADOW_C1_AUTHORITY", applicability={},
        status="candidate")
    baseline = create_policy_snapshot(
        conn, memory_snapshot_id=source_digest, promoted_rules=[])
    candidate = create_policy_snapshot(
        conn, memory_snapshot_id=candidate_digest, promoted_rules=["r1"])
    baseline_load = record_policy_load(
        conn, policy_snapshot_id=baseline.policy_snapshot_id,
        runtime_id="shadow-authority-runtime", receipt={
            "execution_receipt_id": "exec-shadow-baseline",
            "behavior_digest": "behavior-0"})
    record_policy_load(
        conn, policy_snapshot_id=candidate.policy_snapshot_id,
        runtime_id="shadow-authority-runtime", receipt={
            "execution_receipt_id": "exec-shadow-candidate",
            "behavior_digest": "behavior-1"})
    attribution = evaluate_capability_attribution_from_db(
        conn, capability_id=capability.capability_id,
        baseline_memory_digest=source_digest,
        candidate_memory_digest=candidate_digest,
        baseline_policy_snapshot_id=baseline.policy_snapshot_id,
        candidate_policy_snapshot_id=candidate.policy_snapshot_id,
        runtime_id="shadow-authority-runtime",
        baseline_behavior_digest="behavior-0", candidate_behavior_digest="behavior-1",
        target_gain=True, no_regression=True,
        heldout={"verdict": "PASS", "disjoint_lineage": True,
                 "evidence_id": "heldout-shadow-authority"},
        ablation={"policy_snapshot_id": baseline.policy_snapshot_id,
                  "runtime_receipt_id": "exec-shadow-baseline",
                  "policy_load_receipt_id": baseline_load.receipt_id,
                  "behavior_digest": "behavior-0",
                  "gain_without_memory": False, "gain_with_memory": True},
        shadow_update_receipt=shadow, strict_memory_delta=True)
    assert attribution.gates["C1"] is True
    refs = {
        "C1": {"evidence_id": "shadow-c1", "split": "ab", "verdict": "PASS"},
        "C2": {"evidence_id": "shadow-c2", "split": "ab", "verdict": "PASS"},
        "C3": {"evidence_id": "shadow-c3", "split": "ab", "verdict": "PASS"},
        "C4": {"evidence_id": "shadow-c4", "split": "training", "verdict": "PASS",
               "execution_receipt_id": "exec-shadow-candidate"},
        "C5": {"evidence_id": "shadow-c5", "split": "training", "verdict": "PASS"},
        "C6": {"evidence_id": "shadow-c6", "split": "heldout", "verdict": "PASS"},
        "C7": {"evidence_id": "shadow-c7", "split": "heldout", "verdict": "PASS"},
        "C8": {"evidence_id": "shadow-c8", "split": "ab", "verdict": "PASS"},
    }
    authority = record_capability_authority(
        conn, capability_id=capability.capability_id,
        attribution_receipt=attribution, evidence_refs=refs,
        candidate_policy_snapshot_id=candidate.policy_snapshot_id,
        runtime_id="shadow-authority-runtime")
    assert authority.eligible is True, authority.reasons
    assert authority.payload["shadow_update_receipt"]["receipt_digest"] == (
        shadow.receipt_digest)
    assert verify_capability_authority(
        conn, capability.capability_id, authority)["eligible"] is True
    authority.payload["shadow_update_receipt"]["staging_digest_after"] = _digest("9")
    checked = verify_capability_authority(conn, capability.capability_id, authority)
    assert checked["eligible"] is False
    assert "C1:shadow_update_receipt_invalid" in " ".join(checked["reasons"])
