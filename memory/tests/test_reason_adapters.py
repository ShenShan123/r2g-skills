"""Revision3 P2-R6 novelty/conflict typed adapters."""
from __future__ import annotations

import pytest

from tehm.evolution.conflict import ConflictReceipt
from tehm.evolution.novelty import NoveltyReceipt
from tehm.evolution.reason_derivation import (
    EvolutionReasonDerivationError, derive_conflict_reason, derive_novelty_reason,
)
from tehm.evolution.admission import admit_evolution_reason


def test_novelty_receipt_admission_is_typed_and_shadow_only():
    novelty = NoveltyReceipt(
        transition_id="transition-new", campaign_id="r3-adapter",
        mechanism_family="HANDSHAKE_COMPLETION",
        compatibility_profile="rtl.fsm.single_guard.v1",
        status="NOVEL_MECHANISM", path_exists=False,
        lineage_id="lineage-new", learner_eligible=True)
    derivation = derive_novelty_reason(
        novelty, campaign_id="r3-adapter", case_id="novel-case")
    assert derivation is not None and derivation.reason == "NOVELTY"
    admission = admit_evolution_reason(
        derivation, campaign_id="r3-adapter", learner_eligible=True,
        novelty=novelty)
    assert admission.admitted is True
    assert admission.canonical_memory_mutation == "none"
    assert NoveltyReceipt.from_dict({
        **novelty.to_dict(), "receipt_id": novelty.receipt_id,
        "receipt_digest": novelty.receipt_digest}) == novelty


def test_known_or_audit_novelty_does_not_admit():
    known = NoveltyReceipt(
        transition_id="transition-known", campaign_id="r3-adapter",
        mechanism_family="HANDSHAKE_COMPLETION", compatibility_profile=None,
        status="KNOWN_MECHANISM", path_exists=True,
        lineage_id="lineage-known", learner_eligible=True)
    assert derive_novelty_reason(
        known, campaign_id="r3-adapter", case_id="known-case") is None
    audit = NoveltyReceipt(
        transition_id="transition-audit", campaign_id="r3-adapter",
        mechanism_family="HANDSHAKE_COMPLETION", compatibility_profile=None,
        status="NOVEL_MECHANISM", path_exists=False,
        lineage_id="lineage-audit", learner_eligible=False)
    derivation = derive_novelty_reason(
        audit, campaign_id="r3-adapter", case_id="audit-case")
    assert derivation is not None
    blocked = admit_evolution_reason(
        derivation, campaign_id="r3-adapter", learner_eligible=False,
        novelty=audit)
    assert blocked.admitted is False
    assert blocked.blocked_reason == "not_learner_eligible"


def test_conflict_receipt_requires_evidence_and_roundtrips():
    conflict = ConflictReceipt(
        transition_id="transition-conflict", campaign_id="r3-adapter",
        mechanism_family="HANDSHAKE_COMPLETION",
        compatibility_profile="rtl.fsm.single_guard.v1",
        conflict_types=("DEFINITION_CONFLICT",),
        evidence_transition_ids=("transition-other",),
        details={"DEFINITION_CONFLICT": ["transition-other"]},
        lineage_id="lineage-conflict")
    derivation = derive_conflict_reason(
        conflict, campaign_id="r3-adapter", case_id="conflict-case")
    assert derivation is not None and derivation.reason == "CONFLICT"
    admission = admit_evolution_reason(
        derivation, campaign_id="r3-adapter", learner_eligible=True,
        conflict=conflict)
    assert admission.admitted is True
    restored = ConflictReceipt.from_dict({
        **conflict.to_dict(), "receipt_id": conflict.receipt_id,
        "receipt_digest": conflict.receipt_digest})
    assert restored == conflict


def test_conflict_adapter_rejects_missing_lineage_or_evidence():
    no_lineage = ConflictReceipt(
        transition_id="transition-conflict", campaign_id="r3-adapter",
        mechanism_family="HANDSHAKE_COMPLETION", compatibility_profile=None,
        conflict_types=("OUTCOME_CONFLICT",),
        evidence_transition_ids=("transition-other",), details={})
    with pytest.raises(EvolutionReasonDerivationError, match="lineage"):
        derive_conflict_reason(no_lineage, campaign_id="r3-adapter", case_id="case")
    no_evidence = ConflictReceipt(
        transition_id="transition-conflict", campaign_id="r3-adapter",
        mechanism_family="HANDSHAKE_COMPLETION", compatibility_profile=None,
        conflict_types=("OUTCOME_CONFLICT",), evidence_transition_ids=(),
        details={}, lineage_id="lineage-conflict")
    with pytest.raises(EvolutionReasonDerivationError, match="evidence"):
        derive_conflict_reason(no_evidence, campaign_id="r3-adapter", case_id="case")
