"""Capability retention replay receipts.

Retention is a separate audit from acquisition.  A capability that worked on
its acquisition cohort must be replayed with a frozen policy on a later,
non-target cohort; missing evidence or any non-target regression fails closed.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CapabilityRetentionReceipt:
    capability_id: str
    replay_id: str
    retained: bool
    replay_verdict: str
    disjoint_lineage: bool
    non_target_regression_zero: bool
    evidence_id: str | None
    reason: str

    def to_dict(self) -> dict:
        return {
            "capability_id": self.capability_id,
            "replay_id": self.replay_id,
            "retained": self.retained,
            "replay_verdict": self.replay_verdict,
            "disjoint_lineage": self.disjoint_lineage,
            "non_target_regression_zero": self.non_target_regression_zero,
            "evidence_id": self.evidence_id,
            "reason": self.reason,
        }


def evaluate_capability_retention(
    *,
    capability_id: str,
    replay_id: str,
    replay: dict,
) -> CapabilityRetentionReceipt:
    """Evaluate one frozen-policy retention replay, fail-closed."""
    if not capability_id or not replay_id:
        raise ValueError("capability_id and replay_id are required")
    evidence_id = replay.get("evidence_id")
    disjoint = replay.get("disjoint_lineage") is True
    no_regression = replay.get("non_target_regression_zero") is True
    verdict = str(replay.get("verdict") or "UNKNOWN")
    retained = bool(verdict == "PASS" and disjoint and no_regression and evidence_id)
    reason = "retention_verified" if retained else (
        "requires_pass_disjoint_lineage_no_regression_and_evidence")
    return CapabilityRetentionReceipt(
        capability_id=capability_id, replay_id=replay_id,
        retained=retained, replay_verdict=verdict,
        disjoint_lineage=disjoint,
        non_target_regression_zero=no_regression,
        evidence_id=str(evidence_id) if evidence_id else None,
        reason=reason)


__all__ = ["CapabilityRetentionReceipt", "evaluate_capability_retention"]
