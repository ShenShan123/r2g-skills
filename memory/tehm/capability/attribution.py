"""Capability attribution gates C1-C8.

The harness consumes explicit receipts and metrics; it never infers a
capability claim from rule count or a single PASS.  The result is an audit
object only—promotion still requires the registry's explicit gate check.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from collections.abc import Mapping

from tehm.ids import stable_dumps

from .delta import MemoryDeltaReceipt, evaluate_memory_delta
from .policy_snapshot import validate_policy_load_row, validate_policy_snapshot_row


@dataclass(frozen=True)
class CapabilityAttributionReceipt:
    capability_id: str
    gates: dict[str, bool]
    missing_gates: tuple[str, ...]
    promotable: bool
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "capability_id": self.capability_id,
            "gates": dict(self.gates),
            "missing_gates": list(self.missing_gates),
            "promotable": self.promotable,
            "detail": self.detail,
        }


def evaluate_capability_attribution(
    *,
    capability_id: str,
    baseline: dict,
    candidate: dict,
    runtime_receipt: dict,
    heldout: dict,
    ablation: dict,
    memory_delta: Mapping | None = None,
    strict_memory_delta: bool = False,
) -> CapabilityAttributionReceipt:
    """Evaluate the eight explicit attribution gates.

    Expected fields are intentionally small and serializable.  Missing fields
    fail closed instead of being treated as a successful measurement.
    """
    if not capability_id:
        raise ValueError("capability_id is required")
    baseline_memory_digest = baseline.get("memory_digest")
    candidate_memory_digest = candidate.get("memory_digest")
    delta_receipt: MemoryDeltaReceipt | None = None
    if memory_delta is not None:
        delta_receipt = evaluate_memory_delta(
            baseline_memory_digest, candidate_memory_digest, memory_delta)
        memory_delta_verified = delta_receipt.eligible
    elif strict_memory_delta:
        memory_delta_verified = False
    else:
        # Compatibility mode for historical fixtures.  New capability
        # campaigns should pass ``strict_memory_delta=True`` so C1 is bound to
        # concrete changed objects rather than merely unequal labels.
        memory_delta_verified = bool(
            baseline_memory_digest and candidate_memory_digest and
            baseline_memory_digest != candidate_memory_digest)
    policy_delta = bool(baseline.get("policy_digest") and
                        candidate.get("policy_digest") and
                        baseline.get("policy_digest") != candidate.get("policy_digest"))
    runtime_loaded = bool(runtime_receipt.get("loaded") is True and
                          runtime_receipt.get("policy_digest") == candidate.get("policy_digest"))
    behavior_changed = bool(candidate.get("behavior_digest") and
                            baseline.get("behavior_digest") and
                            candidate.get("behavior_digest") != baseline.get("behavior_digest"))
    target_gain = bool(candidate.get("target_gain") is True)
    heldout_transfer = bool(heldout.get("verdict") == "PASS" and
                            heldout.get("disjoint_lineage") is True and
                            heldout.get("evidence_id"))
    no_regression = bool(candidate.get("no_regression") is True)
    ablation_removes_gain = bool(ablation.get("gain_without_memory") is False and
                                 ablation.get("gain_with_memory") is True)
    gates = {
        "C1": memory_delta_verified,
        "C2": policy_delta,
        "C3": runtime_loaded,
        "C4": behavior_changed,
        "C5": target_gain,
        "C6": heldout_transfer,
        "C7": no_regression,
        "C8": ablation_removes_gain,
    }
    missing = tuple(gate for gate, passed in gates.items() if not passed)
    return CapabilityAttributionReceipt(
        capability_id=capability_id, gates=gates, missing_gates=missing,
        promotable=not missing,
        detail={"baseline": dict(baseline), "candidate": dict(candidate),
                "heldout": dict(heldout), "ablation": dict(ablation),
                "memory_delta": (
                    delta_receipt.to_dict() if delta_receipt is not None else
                    ({"eligible": False, "reasons": ["memory_delta_required"]}
                     if strict_memory_delta else None))})


def evaluate_capability_attribution_from_db(
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
    memory_delta: Mapping | None = None,
    strict_memory_delta: bool = False,
) -> CapabilityAttributionReceipt:
    """Build C2/C3 inputs from the policy snapshot/load receipt tables."""
    snapshots = conn.execute(
        """SELECT * FROM tehm_policy_snapshots
             WHERE policy_snapshot_id IN (?, ?)""",
        (baseline_policy_snapshot_id, candidate_policy_snapshot_id)).fetchall()
    snapshot_ids = {str(row["policy_snapshot_id"]) for row in snapshots}
    required_ids = {baseline_policy_snapshot_id, candidate_policy_snapshot_id}
    if snapshot_ids != required_ids:
        raise ValueError("both baseline and candidate policy snapshots are required")
    policies: dict[str, str | None] = {}
    corrupt_snapshots: set[str] = set()
    for row in snapshots:
        snapshot_id = str(row["policy_snapshot_id"])
        try:
            checked = validate_policy_snapshot_row(row)
        except ValueError:
            corrupt_snapshots.add(snapshot_id)
            policies[snapshot_id] = None
        else:
            policies[snapshot_id] = checked["policy_digest"]
    load = None if candidate_policy_snapshot_id in corrupt_snapshots else conn.execute(
        """SELECT *
             FROM tehm_policy_load_receipts
             WHERE policy_snapshot_id=? AND runtime_id=?
             ORDER BY created_at DESC, receipt_id DESC LIMIT 1""",
        (candidate_policy_snapshot_id, runtime_id)).fetchone()
    # C3 is an execution-facing gate.  A row with ``loaded=1`` is only
    # admissible when its immutable JSON receipt still matches the stored
    # digest, snapshot digest, and runtime identity.  Malformed/tampered rows
    # therefore become an explicit failed runtime receipt rather than an
    # optimistic policy load.
    runtime_receipt = {
        "loaded": False,
        "policy_digest": None,
        "runtime_id": runtime_id,
    }
    if load is not None and bool(load["loaded"]):
        valid_load = False
        payload = None
        try:
            checked_load = validate_policy_load_row(load)
            payload = json.loads(checked_load["receipt_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, Mapping):
            expected_digest = "sha256:" + hashlib.sha256(
                stable_dumps(dict(payload)).encode()).hexdigest()
            valid_load = (
                load["receipt_digest"] == expected_digest
                and payload.get("policy_snapshot_id") == candidate_policy_snapshot_id
                and payload.get("policy_digest") == policies[candidate_policy_snapshot_id]
                and payload.get("runtime_id") == runtime_id
                and payload.get("loaded") is True
            )
            if valid_load:
                runtime_receipt["loaded"] = True
                runtime_receipt["policy_digest"] = payload["policy_digest"]
                nested = payload.get("receipt")
                if isinstance(nested, Mapping):
                    runtime_receipt["execution_receipt_id"] = nested.get(
                        "execution_receipt_id")
    return evaluate_capability_attribution(
        capability_id=capability_id,
        baseline={"memory_digest": baseline_memory_digest,
                  "policy_digest": policies.get(baseline_policy_snapshot_id),
                  "behavior_digest": baseline_behavior_digest},
        candidate={"memory_digest": candidate_memory_digest,
                   "policy_digest": policies.get(candidate_policy_snapshot_id),
                   "behavior_digest": candidate_behavior_digest,
                   "target_gain": target_gain,
                   "no_regression": no_regression},
        runtime_receipt=runtime_receipt, heldout=heldout, ablation=ablation,
        memory_delta=memory_delta, strict_memory_delta=strict_memory_delta)


__all__ = ["CapabilityAttributionReceipt", "evaluate_capability_attribution",
           "evaluate_capability_attribution_from_db"]
