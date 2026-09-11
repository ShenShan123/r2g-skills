"""StateShift anti-forgetting evidence is derived, not caller asserted."""
from __future__ import annotations

import hashlib
import copy
from dataclasses import replace

import pytest

from scripts.build_p13_state_shift_anti_forgetting_evidence import (
    P13StateShiftAntiForgettingError,
    _heldout_audit,
)
from tehm.evolution import derive_state_shift_support_expansion
from test_state_shift_expansion import CAMPAIGN, _inputs


def _registration(tmp_path, context):
    rtl = tmp_path / "heldout.v"
    rtl.write_text("module heldout(input a, output y); assign y=a; endmodule\n")
    digest = hashlib.sha256(rtl.read_bytes()).hexdigest()
    context = {**context, "structural_signature": {
        "design_name": "heldout",
        "rtl_sha256": [digest],
    }}
    return {
        "version": "p13-state-shift-heldout-registration-v1",
        "campaign_id": CAMPAIGN,
        "case_id": "heldout:one",
        "lineage_id": "heldout:independent",
        "dataset_split": "heldout",
        "learner_eligible": False,
        "execution_attempted": False,
        "expected_decision": "NO_SKILL",
        "expected_reason": "STATE_SHIFT",
        "current_context": context,
        "source_inputs": [{"path": str(rtl), "sha256": digest}],
        "evaluation_only": True,
        "canonical_memory_mutation": "none",
        "production_runtime_imported": False,
        "production_integration": "not_attempted",
        "memory_docs_submitted": False,
    }, rtl


def test_heldout_audit_proves_abstention_without_executing_memory(tmp_path):
    parent, state, envelope, contexts, shifts, cohort, partition = _inputs()
    child, child_envelope, receipt = derive_state_shift_support_expansion(
        campaign_id=CAMPAIGN, proposal_digest="sha256:" + "a" * 64,
        parent_knowledge=parent, parent_envelope=envelope,
        resolved_state=state, cohort=cohort, state_shifts=shifts,
        current_contexts=contexts, learner_partition=partition)
    dimensions = copy.deepcopy(child_envelope.dimensions)
    dimensions["structural"]["values"] = [{
        "design_name": "training", "rtl_sha256": ["training-digest"],
    }]
    child_envelope = replace(child_envelope, dimensions=dimensions)
    registration, path = _registration(tmp_path, contexts["case:a"])

    audit = _heldout_audit(
        registration, path, campaign_id=CAMPAIGN, child=child,
        child_envelope=child_envelope, resolved_state=state.to_dict(),
        forbidden_lineages=set(receipt.case_lineages.values()))

    assert audit["passed"] is True
    assert audit["routing_decision"] == "NO_SKILL"
    assert audit["no_skill_reason"] == "STATE_SHIFT"
    assert audit["memory_action_executed"] is False
    assert "structural_shift" in audit["state_shift"]["shifted_dimensions"]


def test_heldout_audit_rejects_learner_eligible_registration(tmp_path):
    parent, state, envelope, contexts, shifts, cohort, partition = _inputs()
    child, child_envelope, receipt = derive_state_shift_support_expansion(
        campaign_id=CAMPAIGN, proposal_digest="sha256:" + "a" * 64,
        parent_knowledge=parent, parent_envelope=envelope,
        resolved_state=state, cohort=cohort, state_shifts=shifts,
        current_contexts=contexts, learner_partition=partition)
    dimensions = copy.deepcopy(child_envelope.dimensions)
    dimensions["structural"]["values"] = [{
        "design_name": "training", "rtl_sha256": ["training-digest"],
    }]
    child_envelope = replace(child_envelope, dimensions=dimensions)
    registration, path = _registration(tmp_path, contexts["case:a"])
    registration["learner_eligible"] = True

    with pytest.raises(P13StateShiftAntiForgettingError,
                       match="evaluation firewall"):
        _heldout_audit(
            registration, path, campaign_id=CAMPAIGN, child=child,
            child_envelope=child_envelope, resolved_state=state.to_dict(),
            forbidden_lineages=set(receipt.case_lineages.values()))
