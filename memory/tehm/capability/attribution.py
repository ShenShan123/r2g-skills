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
    memory_snapshot_binding: Mapping | None = None,
    behavior_binding: Mapping | None = None,
    ablation_binding: Mapping | None = None,
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
    # A strict campaign must bind the two caller labels to the immutable
    # policy snapshots that supplied them.  Without this check, a caller can
    # provide an unrelated pair of digests and a valid-looking object delta;
    # C1 would then be true even though the evaluated Policy_t/Policy_t+1 do
    # not refer to those memory states.  Strict callers must provide the
    # database-bound witness; only non-strict historical fixtures may omit it.
    snapshot_binding = None
    if memory_snapshot_binding is not None:
        if isinstance(memory_snapshot_binding, Mapping):
            snapshot_binding = dict(memory_snapshot_binding)
            if strict_memory_delta:
                required_binding_fields = (
                    "version", "strict", "baseline_policy_snapshot_id",
                    "candidate_policy_snapshot_id", "baseline_memory_digest",
                    "candidate_memory_digest", "baseline_memory_snapshot_id",
                    "candidate_memory_snapshot_id", "eligible", "reasons",
                )
                incomplete = (
                    snapshot_binding.get("version") !=
                    "policy-memory-binding-v1" or
                    snapshot_binding.get("strict") is not True or
                    any(field not in snapshot_binding
                        for field in required_binding_fields) or
                    snapshot_binding.get("eligible") is not True or
                    not isinstance(snapshot_binding.get("reasons"), list)
                )
                if incomplete:
                    memory_delta_verified = False
                    reasons = list(snapshot_binding.get("reasons") or [])
                    reasons.append("memory_snapshot_binding_incomplete")
                    snapshot_binding["eligible"] = False
                    snapshot_binding["reasons"] = sorted(set(reasons))
        elif strict_memory_delta:
            snapshot_binding = {
                "eligible": False,
                "reasons": ["memory_snapshot_binding_malformed"],
            }
            memory_delta_verified = False
    elif strict_memory_delta:
        snapshot_binding = {
            "eligible": False,
            "reasons": ["memory_snapshot_binding_required"],
        }
        memory_delta_verified = False
    policy_delta = bool(baseline.get("policy_digest") and
                        candidate.get("policy_digest") and
                        baseline.get("policy_digest") != candidate.get("policy_digest"))
    runtime_loaded = bool(runtime_receipt.get("loaded") is True and
                          runtime_receipt.get("policy_digest") == candidate.get("policy_digest"))
    behavior_changed = bool(candidate.get("behavior_digest") and
                            baseline.get("behavior_digest") and
                            candidate.get("behavior_digest") != baseline.get("behavior_digest"))
    ablation_removes_gain = bool(ablation.get("gain_without_memory") is False and
                                 ablation.get("gain_with_memory") is True)
    behavior_witness = None
    if behavior_binding is not None:
        if isinstance(behavior_binding, Mapping):
            behavior_witness = dict(behavior_binding)
            if strict_memory_delta:
                required_behavior_fields = (
                    "version", "strict", "candidate_policy_snapshot_id",
                    "runtime_id", "candidate_execution_receipt_id",
                    "candidate_behavior_digest", "loaded_behavior_digest",
                    "eligible", "reasons",
                )
                incomplete = (
                    behavior_witness.get("version") !=
                    "policy-runtime-behavior-v1" or
                    behavior_witness.get("strict") is not True or
                    any(field not in behavior_witness
                        for field in required_behavior_fields) or
                    behavior_witness.get("eligible") is not True or
                    not isinstance(behavior_witness.get("reasons"), list)
                )
                if incomplete:
                    behavior_changed = False
                    reasons = list(behavior_witness.get("reasons") or [])
                    reasons.append("runtime_behavior_binding_incomplete")
                    behavior_witness["eligible"] = False
                    behavior_witness["reasons"] = sorted(set(reasons))
        elif strict_memory_delta:
            behavior_witness = {
                "eligible": False,
                "reasons": ["runtime_behavior_binding_malformed"],
            }
            behavior_changed = False
    ablation_witness = None
    if ablation_binding is not None:
        if isinstance(ablation_binding, Mapping):
            ablation_witness = dict(ablation_binding)
            if strict_memory_delta:
                required_ablation_fields = (
                    "version", "strict", "baseline_policy_snapshot_id",
                    "runtime_id", "policy_load_receipt_id",
                    "baseline_execution_receipt_id", "baseline_behavior_digest",
                    "loaded_behavior_digest", "eligible", "reasons",
                )
                incomplete = (
                    ablation_witness.get("version") !=
                    "policy-ablation-v1" or
                    ablation_witness.get("strict") is not True or
                    any(field not in ablation_witness
                        for field in required_ablation_fields) or
                    ablation_witness.get("eligible") is not True or
                    not isinstance(ablation_witness.get("reasons"), list)
                )
                if incomplete:
                    ablation_removes_gain = False
                    reasons = list(ablation_witness.get("reasons") or [])
                    reasons.append("policy_ablation_binding_incomplete")
                    ablation_witness["eligible"] = False
                    ablation_witness["reasons"] = sorted(set(reasons))
        elif strict_memory_delta:
            ablation_witness = {
                "eligible": False,
                "reasons": ["policy_ablation_binding_malformed"],
            }
            ablation_removes_gain = False
    target_gain = bool(candidate.get("target_gain") is True)
    heldout_transfer = bool(heldout.get("verdict") == "PASS" and
                            heldout.get("disjoint_lineage") is True and
                            heldout.get("evidence_id"))
    no_regression = bool(candidate.get("no_regression") is True)
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
                     if strict_memory_delta else None)),
                **({"memory_snapshot_binding": snapshot_binding}
                   if snapshot_binding is not None else {}),
                **({"runtime_behavior_binding": behavior_witness}
                   if behavior_witness is not None else {}),
                **({"policy_ablation_binding": ablation_witness}
                   if ablation_witness is not None else {})})


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
    memory_snapshot_ids: dict[str, str | None] = {}
    corrupt_snapshots: set[str] = set()
    for row in snapshots:
        snapshot_id = str(row["policy_snapshot_id"])
        try:
            checked = validate_policy_snapshot_row(row)
        except ValueError:
            corrupt_snapshots.add(snapshot_id)
            policies[snapshot_id] = None
            memory_snapshot_ids[snapshot_id] = None
        else:
            policies[snapshot_id] = checked["policy_digest"]
            memory_snapshot_ids[snapshot_id] = checked["memory_snapshot_id"]

    # Policy snapshots are the database-bound witnesses for M_t and M_t+1.
    # Keep the claimed labels in the receipt so authority can replay the
    # binding later; strict campaigns fail C1 when either label is unrelated
    # to its corresponding immutable snapshot.
    binding_reasons: list[str] = []
    baseline_snapshot_memory = memory_snapshot_ids.get(baseline_policy_snapshot_id)
    candidate_snapshot_memory = memory_snapshot_ids.get(candidate_policy_snapshot_id)
    if strict_memory_delta:
        if baseline_snapshot_memory is None:
            binding_reasons.append("baseline_policy_snapshot_memory_missing")
        elif baseline_memory_digest != baseline_snapshot_memory:
            binding_reasons.append("baseline_memory_snapshot_mismatch")
        if candidate_snapshot_memory is None:
            binding_reasons.append("candidate_policy_snapshot_memory_missing")
        elif candidate_memory_digest != candidate_snapshot_memory:
            binding_reasons.append("candidate_memory_snapshot_mismatch")
    memory_snapshot_binding = {
        "version": "policy-memory-binding-v1",
        "strict": bool(strict_memory_delta),
        "baseline_policy_snapshot_id": baseline_policy_snapshot_id,
        "candidate_policy_snapshot_id": candidate_policy_snapshot_id,
        "baseline_memory_digest": baseline_memory_digest,
        "candidate_memory_digest": candidate_memory_digest,
        "baseline_memory_snapshot_id": baseline_snapshot_memory,
        "candidate_memory_snapshot_id": candidate_snapshot_memory,
        "eligible": not binding_reasons,
        "reasons": sorted(set(binding_reasons)),
    }
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
    execution_receipt_id = None
    loaded_behavior_digest = None
    if load is not None:
        valid_load = False
        payload = None
        checked_load = None
        try:
            checked_load = validate_policy_load_row(load)
            if checked_load["loaded"] == 1:
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
                    execution_receipt_id = nested.get("execution_receipt_id")
                    loaded_behavior_digest = nested.get("behavior_digest")
                    runtime_receipt["execution_receipt_id"] = execution_receipt_id
                    runtime_receipt["behavior_digest"] = loaded_behavior_digest
    behavior_binding_reasons: list[str] = []
    if strict_memory_delta:
        if not execution_receipt_id:
            behavior_binding_reasons.append("candidate_execution_receipt_required")
        if not isinstance(loaded_behavior_digest, str) or not loaded_behavior_digest:
            behavior_binding_reasons.append("candidate_behavior_digest_receipt_required")
        elif loaded_behavior_digest != candidate_behavior_digest:
            behavior_binding_reasons.append("candidate_behavior_digest_receipt_mismatch")
    behavior_binding = {
        "version": "policy-runtime-behavior-v1",
        "strict": bool(strict_memory_delta),
        "candidate_policy_snapshot_id": candidate_policy_snapshot_id,
        "runtime_id": runtime_id,
        "candidate_execution_receipt_id": execution_receipt_id,
        "candidate_behavior_digest": candidate_behavior_digest,
        "loaded_behavior_digest": loaded_behavior_digest,
        "eligible": not behavior_binding_reasons,
        "reasons": sorted(set(behavior_binding_reasons)),
    }
    ablation_binding_reasons: list[str] = []
    ablation_policy_snapshot_id = None
    ablation_load_receipt_id = None
    ablation_execution_receipt_id = None
    ablation_loaded_behavior_digest = None
    if isinstance(ablation, Mapping):
        ablation_policy_snapshot_id = ablation.get("policy_snapshot_id")
        ablation_load_receipt_id = ablation.get("policy_load_receipt_id")
        ablation_execution_receipt_id = ablation.get("runtime_receipt_id")
    if strict_memory_delta:
        if ablation_policy_snapshot_id != baseline_policy_snapshot_id:
            ablation_binding_reasons.append("ablation_policy_snapshot_mismatch")
        if not isinstance(ablation_load_receipt_id, str) or not ablation_load_receipt_id:
            ablation_binding_reasons.append("ablation_policy_load_receipt_required")
        if not isinstance(ablation_execution_receipt_id, str) or not ablation_execution_receipt_id:
            ablation_binding_reasons.append("ablation_execution_receipt_required")
        ablation_load = None
        if isinstance(ablation_load_receipt_id, str) and ablation_load_receipt_id:
            ablation_load = conn.execute(
                "SELECT * FROM tehm_policy_load_receipts WHERE receipt_id=?",
                (ablation_load_receipt_id,)).fetchone()
        if ablation_load is None:
            ablation_binding_reasons.append("ablation_policy_load_receipt_missing")
        else:
            ablation_payload = None
            try:
                checked_ablation_load = validate_policy_load_row(ablation_load)
                ablation_payload = json.loads(
                    checked_ablation_load["receipt_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                ablation_payload = None
            nested = (ablation_payload.get("receipt")
                      if isinstance(ablation_payload, Mapping) else None)
            if not isinstance(ablation_payload, Mapping):
                ablation_binding_reasons.append("ablation_policy_load_receipt_malformed")
            elif (ablation_payload.get("policy_snapshot_id") !=
                  baseline_policy_snapshot_id or
                  ablation_payload.get("runtime_id") != runtime_id or
                  ablation_payload.get("loaded") is not True):
                ablation_binding_reasons.append("ablation_policy_load_binding_mismatch")
            if not isinstance(nested, Mapping):
                ablation_binding_reasons.append("ablation_execution_receipt_malformed")
            else:
                actual_load_id = str(ablation_load["receipt_id"])
                if actual_load_id != ablation_load_receipt_id:
                    ablation_binding_reasons.append("ablation_policy_load_receipt_id_mismatch")
                if nested.get("execution_receipt_id") != ablation_execution_receipt_id:
                    ablation_binding_reasons.append("ablation_execution_receipt_mismatch")
                ablation_loaded_behavior_digest = nested.get("behavior_digest")
                if ablation_loaded_behavior_digest != ablation.get("behavior_digest"):
                    ablation_binding_reasons.append("ablation_behavior_digest_mismatch")
    ablation_binding = {
        "version": "policy-ablation-v1",
        "strict": bool(strict_memory_delta),
        "baseline_policy_snapshot_id": baseline_policy_snapshot_id,
        "runtime_id": runtime_id,
        "policy_load_receipt_id": ablation_load_receipt_id,
        "baseline_execution_receipt_id": ablation_execution_receipt_id,
        "baseline_behavior_digest": baseline_behavior_digest,
        "loaded_behavior_digest": ablation_loaded_behavior_digest,
        "eligible": not ablation_binding_reasons,
        "reasons": sorted(set(ablation_binding_reasons)),
    }
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
        memory_delta=memory_delta, strict_memory_delta=strict_memory_delta,
        memory_snapshot_binding=memory_snapshot_binding,
        behavior_binding=behavior_binding,
        ablation_binding=ablation_binding)


__all__ = ["CapabilityAttributionReceipt", "evaluate_capability_attribution",
           "evaluate_capability_attribution_from_db"]
