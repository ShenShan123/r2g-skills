"""P13 shadow receipts bind concrete P14 memory deltas."""
from __future__ import annotations

import hashlib

import pytest

from tehm.capability import memory_delta_from_shadow_update
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
