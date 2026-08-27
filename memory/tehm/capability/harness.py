"""Strict C2 capability-attribution campaign harness.

The harness binds the existing C1-C8 evaluator to two immutable policy
snapshots and an explicit frozen-control manifest.  It produces an audit
receipt only; it does not mutate lifecycle status or infer gains from rule
counts.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from tehm.ids import stable_dumps

from .attribution import (
    CapabilityAttributionReceipt, evaluate_capability_attribution_from_db,
)


@dataclass(frozen=True)
class CapabilityCampaignReceipt:
    capability_id: str
    control_digest: str
    controls_match: bool
    attribution: CapabilityAttributionReceipt
    promotable: bool

    def to_dict(self) -> dict:
        return {
            "capability_id": self.capability_id,
            "control_digest": self.control_digest,
            "controls_match": self.controls_match,
            "attribution": self.attribution.to_dict(),
            "promotable": self.promotable,
        }


def evaluate_capability_campaign(
    conn,
    *,
    capability_id: str,
    baseline_memory_digest: str,
    candidate_memory_digest: str,
    baseline_policy_snapshot_id: str,
    candidate_policy_snapshot_id: str,
    runtime_id: str,
    baseline_behavior_digest: str,
    candidate_behavior_digest: str,
    target_gain: bool,
    no_regression: bool,
    heldout: dict,
    ablation: dict,
    baseline_controls: dict,
    candidate_controls: dict,
) -> CapabilityCampaignReceipt:
    """Evaluate attribution with an exact frozen-control comparison."""
    if not capability_id:
        raise ValueError("capability_id is required")
    if not isinstance(baseline_controls, dict) or not baseline_controls:
        raise ValueError("baseline_controls must be a non-empty dict")
    if not isinstance(candidate_controls, dict) or not candidate_controls:
        raise ValueError("candidate_controls must be a non-empty dict")
    controls_match = stable_dumps(baseline_controls) == stable_dumps(candidate_controls)
    control_digest = "sha256:" + hashlib.sha256(
        stable_dumps(baseline_controls).encode()).hexdigest()
    attribution = evaluate_capability_attribution_from_db(
        conn, capability_id=capability_id,
        baseline_memory_digest=baseline_memory_digest,
        candidate_memory_digest=candidate_memory_digest,
        baseline_policy_snapshot_id=baseline_policy_snapshot_id,
        candidate_policy_snapshot_id=candidate_policy_snapshot_id,
        runtime_id=runtime_id,
        baseline_behavior_digest=baseline_behavior_digest,
        candidate_behavior_digest=candidate_behavior_digest,
        target_gain=target_gain, no_regression=no_regression,
        heldout=heldout, ablation=ablation)
    if not controls_match:
        detail = dict(attribution.detail)
        detail["controls_match"] = False
        detail["control_mismatch"] = {
            "baseline": baseline_controls,
            "candidate": candidate_controls,
        }
        attribution = CapabilityAttributionReceipt(
            capability_id=attribution.capability_id,
            gates={**attribution.gates, "C4": False},
            missing_gates=tuple(sorted(set(attribution.missing_gates) | {"controls_match"})),
            promotable=False, detail=detail)
    return CapabilityCampaignReceipt(
        capability_id=capability_id,
        control_digest=control_digest,
        controls_match=controls_match,
        attribution=attribution,
        promotable=bool(controls_match and attribution.promotable))


__all__ = ["CapabilityCampaignReceipt", "evaluate_capability_campaign"]
