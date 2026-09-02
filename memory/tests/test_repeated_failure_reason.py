"""Revision3 P1-R4 repeated-failure detector/adapter tests."""
from __future__ import annotations

import pytest

from tehm.evolution.admission import admit_evolution_reason
from tehm.evolution.reason_derivation import (
    EvolutionReasonDerivationError, derive_repeated_failure_reason,
)
from tehm.evolution.repeated_failure import RepeatedFailureReceipt


def _receipt(**kwargs) -> RepeatedFailureReceipt:
    payload = {
        "campaign_id": "r3-repeated",
        "mechanism_family": "ROUTING_CAPACITY_RECOVERY",
        "compatibility_profile": "flow.orfs.route.v1",
        "failure_family": "route-capacity",
        "failure_transition_ids": ("failure-a", "failure-b"),
        "evidence_lineages": ("lineage-a", "lineage-b"),
        "resolution_ids": ("resolution-a",),
        "oracle_digests": ("sha256:oracle-a", "sha256:oracle-b"),
        "learner_eligible": True,
        "oracle_complete": (True, True),
    }
    payload.update(kwargs)
    return RepeatedFailureReceipt(**payload)


def test_repeated_failure_reason_and_admission_do_not_need_p12_pair():
    repeated = _receipt()
    derivation = derive_repeated_failure_reason(
        repeated, campaign_id="r3-repeated", case_id="failure-case")
    assert derivation is not None
    assert derivation.reason == "REPEATED_FAILURE"
    admission = admit_evolution_reason(
        derivation, campaign_id="r3-repeated", learner_eligible=True,
        repeated_failure=repeated)
    assert admission.admitted is True
    assert "paired_counterfactual" not in admission.required_evidence


def test_repeated_failure_needs_independent_observations_and_complete_witnesses():
    with pytest.raises(ValueError, match="independent"):
        _receipt(evidence_lineages=("lineage-a",), resolution_ids=("resolution-a",))
    with pytest.raises(ValueError, match="align"):
        _receipt(oracle_digests=("sha256:oracle-a", "sha256:oracle-b"),
                 failure_transition_ids=("failure-a",))


def test_repeated_failure_campaign_and_roundtrip_are_content_bound():
    repeated = _receipt()
    payload = {**repeated.to_dict(), "receipt_id": repeated.receipt_id,
               "receipt_digest": repeated.receipt_digest}
    assert RepeatedFailureReceipt.from_dict(payload) == repeated
    with pytest.raises(EvolutionReasonDerivationError, match="campaign"):
        derive_repeated_failure_reason(
            repeated, campaign_id="other", case_id="failure-case")


def test_repeated_failure_typed_receipt_cannot_override_learner_boundary():
    audit = _receipt(learner_eligible=False)
    derivation = derive_repeated_failure_reason(
        audit, campaign_id="r3-repeated", case_id="audit-case")
    blocked = admit_evolution_reason(
        derivation, campaign_id="r3-repeated", learner_eligible=True,
        repeated_failure=audit)
    assert blocked.admitted is False
    assert blocked.blocked_reason == "missing_typed_repeated_failure"
