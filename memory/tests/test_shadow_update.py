"""P13 localized updates stay isolated from canonical and runtime authority."""
from __future__ import annotations

from pathlib import Path

import pytest

from tehm.canonical.capture import capture
from tehm.evolution import (
    AppliedShadowUpdateReceipt,
    LocalizedUpdatePlan,
    ShadowUpdateError,
    apply_localized_update_shadow,
)
from tehm.rtl.rtl_evidence import build_rtl_execution_record
from tehm.rtl.rtl_oracle import IcarusOracle


PROJECTS = Path(__file__).resolve().parent / "fixtures" / "rtl_projects"


def _capture(tmp_tehm, name: str, *, oracle=None) -> str:
    conn, store, _ = tmp_tehm
    record = build_rtl_execution_record(PROJECTS / name, oracle=oracle, store=store)
    return capture(
        conn, store, record, dataset_campaign_id="live",
        dataset_split="training", dataset_learner_eligible=True).transition_id


def _plan(transition_id: str, target: str, operation: str, *, refs=(),
          state_resolution_id=None, learner_eligible=True) -> LocalizedUpdatePlan:
    return LocalizedUpdatePlan(
        transition_id=transition_id, campaign_id="live",
        learner_eligible=learner_eligible, priority="P1_HIGH", value_score=0.8,
        update_target=target, candidate_targets=(target,), operation=operation,
        failure_type="STATE_RESOLUTION_FAILURE", evidence_refs=tuple(refs),
        state_resolution_id=state_resolution_id, rationale="P13 shadow test",
    )


def test_relation_update_is_applied_then_discarded(tmp_tehm):
    conn, _, _ = tmp_tehm
    first = _capture(tmp_tehm, "req_ack_bug")
    second = _capture(tmp_tehm, "req_ack_bug2")
    before = conn.execute(
        "SELECT COUNT(*) FROM tehm_memory_relations").fetchone()[0]
    plan = _plan(first, "UPDATE_STATE_RELATION", "INVALIDATE", refs=(first,))
    receipt = apply_localized_update_shadow(plan, conn, {
        "relation": {
            "source_type": "transition", "source_id": first,
            "relation_type": "INVALIDATES", "target_type": "transition",
            "target_id": second, "evidence_refs": [first],
        }
    })
    assert receipt.created_relation_ids
    assert receipt.before_resolution_id != receipt.after_resolution_id
    assert receipt.staging_discarded is True
    assert receipt.canonical_memory_mutation == "none"
    assert receipt.lifecycle_mutation == "isolated_staging_only"
    assert receipt.canonical_rows_changed is False
    assert receipt.production_authority_changed is False
    assert receipt.source_digest_before == receipt.source_digest_after
    assert receipt.raw_evidence_before_digest == receipt.raw_evidence_after_digest
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_memory_relations").fetchone()[0] == before


def test_retain_shadow_update_is_a_deterministic_noop(tmp_tehm):
    conn, _, _ = tmp_tehm
    transition_id = _capture(tmp_tehm, "req_ack_bug")
    plan = _plan(transition_id, "UPDATE_NONE", "RETAIN", learner_eligible=False)
    first = apply_localized_update_shadow(plan, conn)
    second = apply_localized_update_shadow(plan, conn)
    assert first.to_dict() == second.to_dict()
    assert first.created_object_ids == ()
    assert first.created_relation_ids == ()
    assert first.before_resolution_id == first.after_resolution_id


def test_causal_update_crystallizes_only_in_shadow(tmp_tehm):
    oracle = IcarusOracle()
    if not oracle.available:
        pytest.skip("Icarus unavailable")
    conn, _, _ = tmp_tehm
    first = _capture(tmp_tehm, "req_ack_bug", oracle=oracle)
    second = _capture(tmp_tehm, "req_ack_bug2", oracle=oracle)
    before = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("tehm_rules", "tehm_rule_revisions", "tehm_memory_events")
    }
    plan = _plan(
        first, "UPDATE_CAUSAL_KNOWLEDGE", "ADD", refs=(first, second))
    receipt = apply_localized_update_shadow(
        plan, conn, {"transition_ids": [first, second]})
    assert any(item.startswith("rule:") for item in receipt.created_object_ids)
    assert any(item.startswith("rule_revision:")
               for item in receipt.created_object_ids)
    assert {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in before
    } == before


def test_shadow_asset_update_never_promotes_or_persists(tmp_tehm):
    conn, _, _ = tmp_tehm
    transition_id = _capture(tmp_tehm, "req_ack_bug")
    plan = _plan(transition_id, "UPDATE_ASSET", "ADD", refs=(transition_id,))
    receipt = apply_localized_update_shadow(plan, conn, {
        "asset": {
            "asset_type": "DIAGNOSTIC_EXTRACTOR", "name": "shadow.asset",
            "version": "0.1", "definition": {"kind": "diagnostic"},
            "input_contract": {"state": "object"},
            "output_contract": {"diagnostic": "object"},
            "verifier_contract": {"independent": True},
            "compatibility": {"mechanism_family": "RTL_REPAIR"},
            "provenance": {"source": "P13-test"},
        }
    })
    assert len(receipt.created_object_ids) == 1
    assert receipt.created_object_ids[0].startswith("asset:")
    assert conn.execute("SELECT COUNT(*) FROM tehm_assets").fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_asset_authority_receipts").fetchone()[0] == 0


def test_shadow_capability_update_is_candidate_only_and_discarded(tmp_tehm):
    conn, _, _ = tmp_tehm
    transition_id = _capture(tmp_tehm, "req_ack_bug")
    plan = _plan(transition_id, "UPDATE_CAPABILITY", "ADD", refs=(transition_id,))
    receipt = apply_localized_update_shadow(plan, conn, {
        "capability": {
            "mechanism_family": "RTL_REPAIR",
            "applicability": {"compatibility_profile": "rtl.fsm.single_guard.v1"},
            "required_rules": [], "required_assets": [],
            "obligations": {"TARGET": "PASS"}, "budget": {"max_runs": 1},
        }
    })
    assert receipt.created_object_ids[0].startswith("capability:")
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_capabilities").fetchone()[0] == 0


def test_shadow_update_rejects_non_learner_and_tampered_receipt(tmp_tehm):
    _conn, _, _ = tmp_tehm
    transition_id = _capture(tmp_tehm, "req_ack_bug")
    # LocalizedUpdatePlan itself rejects a mutating audit-only proposal.
    with pytest.raises(ValueError, match="audit-only evidence"):
        _plan(
            transition_id, "UPDATE_STATE_RELATION", "INVALIDATE",
            refs=(transition_id,), learner_eligible=False)


def test_shadow_receipt_replays_and_rejects_digest_tampering(tmp_tehm):
    conn, _, _ = tmp_tehm
    first = _capture(tmp_tehm, "req_ack_bug")
    second = _capture(tmp_tehm, "req_ack_bug2")
    plan = _plan(first, "UPDATE_STATE_RELATION", "INVALIDATE", refs=(first,))
    receipt = apply_localized_update_shadow(plan, conn, {
        "relation": {
            "source_type": "transition", "source_id": first,
            "relation_type": "INVALIDATES", "target_type": "transition",
            "target_id": second, "evidence_refs": [first],
        }
    })
    assert AppliedShadowUpdateReceipt.from_dict(receipt.to_dict()) == receipt
    tampered = {**receipt.to_dict(), "after_resolution_id": "tampered"}
    with pytest.raises(ShadowUpdateError, match="replay digest mismatch|receipt digest"):
        AppliedShadowUpdateReceipt.from_dict(tampered)
