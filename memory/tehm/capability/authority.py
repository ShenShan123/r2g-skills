"""Database-bound capability promotion authority.

The C1-C8 attribution harness deliberately returns an audit object, not
authority.  This module is the bridge between that audit and the registry: it
records one immutable evidence row for every gate, binds the candidate policy
to a real runtime-load receipt, and emits a content-addressed authority
receipt.  Promotion can consume only that receipt.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field

from tehm import db as tehm_db
from tehm.ids import stable_dumps
from tehm.lifecycle.promotion_gates import (
    CAPABILITY_GATES, evaluate_capability_promotion_gates,
)

from .delta import evaluate_memory_delta
from .policy_snapshot import validate_policy_load_row, validate_policy_snapshot_row
from .registry import validate_capability_row


AUTHORITY_VERSION = "capability-promotion-authority-v1"
GATE_EVIDENCE_TYPES = {gate: f"capability_gate:{gate}"
                       for gate in CAPABILITY_GATES}
GATE_EVIDENCE_TYPES["asset_authority_verified"] = (
    "capability_gate:asset_authority_verified")

# A gate's evidence split is part of the claim contract.  This prevents a
# caller from satisfying held-out transfer or ablation with training rows.
GATE_ALLOWED_SPLITS = {
    "C1": frozenset({"training", "ab"}),
    "C2": frozenset({"ab"}),
    "C3": frozenset({"ab"}),
    "C4": frozenset({"training", "ab"}),
    "C5": frozenset({"training"}),
    "C6": frozenset({"heldout"}),
    "C7": frozenset({"heldout", "ab"}),
    "C8": frozenset({"ab"}),
    "asset_authority_verified": frozenset({"training", "ab"}),
}


@dataclass(frozen=True)
class CapabilityAuthorityReceipt:
    capability_id: str
    authority_receipt_id: str
    authority_version: str
    attribution_digest: str
    candidate_policy_snapshot_id: str
    runtime_id: str
    gates: dict[str, bool]
    evidence_refs: dict[str, dict]
    eligible: bool
    reasons: tuple[str, ...] = ()
    receipt_digest: str = ""
    payload: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "capability_id": self.capability_id,
            "authority_receipt_id": self.authority_receipt_id,
            "authority_version": self.authority_version,
            "attribution_digest": self.attribution_digest,
            "candidate_policy_snapshot_id": self.candidate_policy_snapshot_id,
            "runtime_id": self.runtime_id,
            "gates": dict(self.gates),
            "evidence_refs": self.evidence_refs,
            "eligible": self.eligible,
            "reasons": list(self.reasons),
            "receipt_digest": self.receipt_digest,
            "payload": self.payload,
        }


def _as_dict(value) -> dict:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if not isinstance(value, Mapping):
        raise TypeError("receipt must be a mapping or expose to_dict()")
    return dict(value)


def _evidence_digest(*, capability_id: str, evidence_type: str,
                     evidence_id: str, split: str, verdict: str,
                     lineage_id: str | None) -> str:
    payload = {
        "capability_id": capability_id,
        "evidence_type": evidence_type,
        "evidence_id": evidence_id,
        "split": split,
        "verdict": verdict,
        "lineage_id": lineage_id,
    }
    return "sha1:" + hashlib.sha1(stable_dumps(payload).encode()).hexdigest()


def _strict_text(value, *, label: str) -> str:
    """Return one non-empty text identity without implicit string coercion."""
    if type(value) is not str or not value.strip():
        raise ValueError(f"{label}_malformed")
    return value.strip()


def _strict_optional_text(value, *, label: str) -> str | None:
    if value is None:
        return None
    return _strict_text(value, label=label)


def _strict_id_vector(value, *, label: str) -> list[str] | None:
    """Normalize optional receipt-ID vectors while rejecting weak elements."""
    if not isinstance(value, (list, tuple)) or not value:
        return None
    if any(type(item) is not str or not item.strip() for item in value):
        return None
    result = [item.strip() for item in value]
    if len(set(result)) != len(result):
        return None
    return sorted(result)


def _capability_row(conn: sqlite3.Connection, capability_id: str):
    row = conn.execute(
        "SELECT * FROM tehm_capabilities "
        "WHERE capability_id=?", (capability_id,)).fetchone()
    if row is None:
        raise ValueError("unknown capability_id")
    row = validate_capability_row(row)
    try:
        assets = json.loads(row["required_assets_json"] or "[]")
    except (TypeError, json.JSONDecodeError):
        raise ValueError("capability required assets are malformed")
    if (not isinstance(assets, list) or
            any(type(item) is not str for item in assets)):
        raise ValueError("capability required assets are malformed")
    return tuple(assets)


def _policy_binding_reasons(
    conn: sqlite3.Connection,
    attribution: Mapping,
    *,
    candidate_policy_snapshot_id: str,
    runtime_id: str,
    execution_receipt_id: str | None = None,
) -> list[str]:
    reasons: list[str] = []
    if not candidate_policy_snapshot_id:
        reasons.append("candidate_policy_snapshot_required")
        return reasons
    if not runtime_id:
        reasons.append("runtime_id_required")
        return reasons
    snapshot = conn.execute(
        "SELECT * FROM tehm_policy_snapshots "
        "WHERE policy_snapshot_id=?", (candidate_policy_snapshot_id,)
    ).fetchone()
    if snapshot is None:
        reasons.append("candidate_policy_snapshot_missing")
        return reasons
    try:
        snapshot = validate_policy_snapshot_row(snapshot)
    except ValueError:
        reasons.append("candidate_policy_snapshot_digest_mismatch")
        return reasons
    detail = attribution.get("detail") or {}
    candidate = detail.get("candidate") or {}
    if not candidate.get("policy_digest"):
        reasons.append("attribution_candidate_policy_digest_missing")
    elif candidate["policy_digest"] != snapshot["policy_digest"]:
        reasons.append("candidate_policy_digest_mismatch")
    binding = detail.get("memory_snapshot_binding")
    if isinstance(binding, Mapping) and binding.get("strict") is True:
        reasons.extend(_memory_snapshot_binding_reasons(
            conn, binding,
            candidate_policy_snapshot_id=candidate_policy_snapshot_id))
    elif (candidate.get("memory_digest") and
          candidate["memory_digest"] != snapshot["memory_snapshot_id"]):
        # Preserve the historical authority check for compatibility receipts
        # that predate the explicit strict binding witness.
        reasons.append("candidate_memory_snapshot_mismatch")
    behavior_binding = detail.get("runtime_behavior_binding")
    if isinstance(behavior_binding, Mapping) and behavior_binding.get("strict") is True:
        reasons.extend(_runtime_behavior_binding_reasons(
            conn, behavior_binding,
            candidate_policy_snapshot_id=candidate_policy_snapshot_id,
            runtime_id=runtime_id,
            expected_behavior_digest=candidate.get("behavior_digest")))
    ablation_binding = detail.get("policy_ablation_binding")
    if isinstance(ablation_binding, Mapping) and ablation_binding.get("strict") is True:
        memory_binding = detail.get("memory_snapshot_binding")
        expected_baseline_policy = (
            memory_binding.get("baseline_policy_snapshot_id")
            if isinstance(memory_binding, Mapping) else None)
        reasons.extend(_policy_ablation_binding_reasons(
            conn, ablation_binding,
            baseline_policy_snapshot_id=expected_baseline_policy,
            runtime_id=runtime_id))
    load = conn.execute(
        """SELECT *
             FROM tehm_policy_load_receipts
             WHERE policy_snapshot_id=? AND runtime_id=?
             ORDER BY created_at DESC, receipt_id DESC LIMIT 1""",
        (candidate_policy_snapshot_id, runtime_id),
    ).fetchone()
    if load is None:
        reasons.append("candidate_policy_runtime_load_missing")
    else:
        checked_load = None
        try:
            checked_load = validate_policy_load_row(load)
            receipt = json.loads(checked_load["receipt_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            # Do not let SQLite's weak typing (for example, the string
            # ``"false"`` in an INTEGER column) become a truthy runtime load.
            # A malformed row is an explicit failed witness; it must never
            # enter the receipt-binding checks below as if it were loaded.
            reasons.append("candidate_policy_runtime_load_malformed")
            receipt = {}
        if load is not None and (
                not isinstance(checked_load, Mapping) or
                checked_load.get("loaded") != 1):
            reasons.append("candidate_policy_runtime_load_missing")
        if not isinstance(receipt, Mapping):
            receipt = {}
        expected_load_digest = "sha256:" + hashlib.sha256(
            stable_dumps(dict(receipt)).encode()).hexdigest()
        if load["receipt_digest"] != expected_load_digest:
            reasons.append("runtime_load_receipt_digest_mismatch")
        if receipt.get("policy_snapshot_id") != candidate_policy_snapshot_id:
            reasons.append("runtime_load_snapshot_id_mismatch")
        if receipt.get("policy_digest") != snapshot["policy_digest"]:
            reasons.append("runtime_load_policy_digest_mismatch")
        if receipt.get("runtime_id") != runtime_id:
            reasons.append("runtime_load_runtime_id_mismatch")
        # C3/C4 must be tied to an execution produced after this exact policy
        # was loaded.  A plain ``loaded=true`` row is insufficient authority.
        if not execution_receipt_id:
            reasons.append("candidate_runtime_execution_receipt_missing")
        nested = receipt.get("receipt")
        actual_execution_id = (
            nested.get("execution_receipt_id")
            if isinstance(nested, Mapping) else None)
        if execution_receipt_id and actual_execution_id != execution_receipt_id:
            reasons.append("candidate_runtime_execution_receipt_mismatch")
    return reasons


def _memory_snapshot_binding_reasons(
    conn: sqlite3.Connection,
    binding: Mapping,
    *,
    candidate_policy_snapshot_id: str,
) -> list[str]:
    """Replay strict C1's M_t/M_t+1-to-policy-snapshot binding.

    ``memory_delta`` proves that some concrete object changed; this witness
    proves that the two labels used for that delta are the memory states named
    by the exact baseline/candidate policy snapshots.  It is intentionally a
    derived authority check and never creates or mutates a memory snapshot.
    """
    if not isinstance(binding, Mapping):
        return ["C1:memory_snapshot_binding_malformed"]
    reasons: list[str] = []
    if binding.get("version") != "policy-memory-binding-v1":
        reasons.append("C1:memory_snapshot_binding_version_mismatch")
    if binding.get("strict") is not True:
        reasons.append("C1:memory_snapshot_binding_not_strict")
    if type(candidate_policy_snapshot_id) is not str or not candidate_policy_snapshot_id.strip():
        reasons.append("C1:candidate_policy_snapshot_required")
        candidate_policy_snapshot_id = ""
    else:
        candidate_policy_snapshot_id = candidate_policy_snapshot_id.strip()
    baseline_policy_id = binding.get("baseline_policy_snapshot_id")
    candidate_policy_id = binding.get("candidate_policy_snapshot_id")
    if (baseline_policy_id is not None and
            (type(baseline_policy_id) is not str or not baseline_policy_id.strip())):
        reasons.append("C1:baseline_policy_snapshot_malformed")
        baseline_policy_id = ""
    elif isinstance(baseline_policy_id, str):
        baseline_policy_id = baseline_policy_id.strip()
    if (candidate_policy_id is not None and
            (type(candidate_policy_id) is not str or not candidate_policy_id.strip())):
        reasons.append("C1:candidate_policy_snapshot_malformed")
        candidate_policy_id = ""
    elif isinstance(candidate_policy_id, str):
        candidate_policy_id = candidate_policy_id.strip()
    baseline_policy_id = baseline_policy_id or ""
    candidate_policy_id = candidate_policy_id or ""
    if not baseline_policy_id:
        reasons.append("C1:baseline_policy_snapshot_required")
    if not candidate_policy_id:
        reasons.append("C1:candidate_policy_snapshot_required")
    if candidate_policy_id and candidate_policy_id != candidate_policy_snapshot_id:
        reasons.append("C1:candidate_policy_snapshot_binding_mismatch")

    snapshots: dict[str, dict | None] = {}
    for side, policy_id in (("baseline", baseline_policy_id),
                            ("candidate", candidate_policy_id)):
        if not policy_id:
            snapshots[side] = None
            continue
        row = conn.execute(
            "SELECT * FROM tehm_policy_snapshots WHERE policy_snapshot_id=?",
            (policy_id,)).fetchone()
        if row is None:
            reasons.append(f"C1:{side}_policy_snapshot_missing")
            snapshots[side] = None
            continue
        try:
            snapshots[side] = validate_policy_snapshot_row(row)
        except ValueError:
            reasons.append(f"C1:{side}_policy_snapshot_digest_mismatch")
            snapshots[side] = None

    for side in ("baseline", "candidate"):
        snapshot = snapshots[side]
        if snapshot is None:
            continue
        snapshot_memory_id = snapshot["memory_snapshot_id"]
        claimed_snapshot_id = binding.get(f"{side}_memory_snapshot_id")
        if claimed_snapshot_id != snapshot_memory_id:
            reasons.append(f"C1:{side}_memory_snapshot_witness_mismatch")
        claimed_digest = binding.get(f"{side}_memory_digest")
        if claimed_digest != snapshot_memory_id:
            reasons.append(f"C1:{side}_memory_snapshot_mismatch")

    expected_eligible = not reasons
    if binding.get("eligible") is not expected_eligible:
        reasons.append("C1:memory_snapshot_binding_eligibility_mismatch")
    return reasons


def _runtime_behavior_binding_reasons(
    conn: sqlite3.Connection,
    binding: Mapping,
    *,
    candidate_policy_snapshot_id: str,
    runtime_id: str,
    expected_behavior_digest: str | None = None,
) -> list[str]:
    """Replay strict C4's binding to the exact candidate runtime load.

    C4 must describe behavior emitted after the candidate policy was loaded,
    not an unrelated caller-provided digest.  The nested execution receipt and
    behavior digest are therefore checked against the latest immutable load
    row for this policy/runtime pair.
    """
    if not isinstance(binding, Mapping):
        return ["C4:runtime_behavior_binding_malformed"]
    reasons: list[str] = []
    if binding.get("version") != "policy-runtime-behavior-v1":
        reasons.append("C4:runtime_behavior_binding_version_mismatch")
    if binding.get("strict") is not True:
        reasons.append("C4:runtime_behavior_binding_not_strict")
    if binding.get("candidate_policy_snapshot_id") != candidate_policy_snapshot_id:
        reasons.append("C4:candidate_policy_snapshot_binding_mismatch")
    if binding.get("runtime_id") != runtime_id:
        reasons.append("C4:runtime_binding_runtime_id_mismatch")
    if expected_behavior_digest is not None and (
            binding.get("candidate_behavior_digest") != expected_behavior_digest):
        reasons.append("C4:behavior_digest_claim_mismatch")
    execution_id = binding.get("candidate_execution_receipt_id")
    if not isinstance(execution_id, str) or not execution_id:
        reasons.append("C4:candidate_execution_receipt_required")
    loaded_behavior = binding.get("loaded_behavior_digest")
    if not isinstance(loaded_behavior, str) or not loaded_behavior:
        reasons.append("C4:candidate_behavior_digest_receipt_required")
    if (isinstance(expected_behavior_digest, str) and
            isinstance(loaded_behavior, str) and
            loaded_behavior != expected_behavior_digest):
        reasons.append("C4:candidate_behavior_digest_receipt_mismatch")

    load = conn.execute(
        """SELECT * FROM tehm_policy_load_receipts
            WHERE policy_snapshot_id=? AND runtime_id=? AND loaded=1
            ORDER BY created_at DESC, receipt_id DESC LIMIT 1""",
        (candidate_policy_snapshot_id, runtime_id)).fetchone()
    receipt: Mapping | None = None
    if load is None:
        reasons.append("C4:candidate_policy_runtime_load_missing")
    else:
        try:
            checked = validate_policy_load_row(load)
            raw = json.loads(checked["receipt_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            raw = None
        if isinstance(raw, Mapping):
            nested = raw.get("receipt")
            receipt = nested if isinstance(nested, Mapping) else None
        if receipt is None:
            reasons.append("C4:runtime_behavior_receipt_malformed")
        else:
            actual_execution_id = receipt.get("execution_receipt_id")
            actual_behavior = receipt.get("behavior_digest")
            if actual_execution_id != execution_id:
                reasons.append("C4:execution_receipt_binding_mismatch")
            if actual_behavior != loaded_behavior:
                reasons.append("C4:behavior_digest_binding_mismatch")

    expected_eligible = not reasons
    if binding.get("eligible") is not expected_eligible:
        reasons.append("C4:runtime_behavior_binding_eligibility_mismatch")
    return reasons


def _policy_ablation_binding_reasons(
    conn: sqlite3.Connection,
    binding: Mapping,
    *,
    baseline_policy_snapshot_id: str | None = None,
    runtime_id: str | None = None,
) -> list[str]:
    """Replay strict C8's baseline-policy ablation witness.

    The ablation must load the exact baseline policy, execute it, and bind the
    resulting behavior digest.  A pair of booleans without this witness is
    only a claim and cannot establish that memory removal caused the loss.
    """
    if not isinstance(binding, Mapping):
        return ["C8:policy_ablation_binding_malformed"]
    reasons: list[str] = []
    if binding.get("version") != "policy-ablation-v1":
        reasons.append("C8:policy_ablation_binding_version_mismatch")
    if binding.get("strict") is not True:
        reasons.append("C8:policy_ablation_binding_not_strict")
    baseline_policy_id = binding.get("baseline_policy_snapshot_id")
    if not isinstance(baseline_policy_id, str) or not baseline_policy_id:
        reasons.append("C8:baseline_policy_snapshot_required")
    if (baseline_policy_snapshot_id is not None and
            baseline_policy_id != baseline_policy_snapshot_id):
        reasons.append("C8:baseline_policy_snapshot_binding_mismatch")
    bound_runtime_id = binding.get("runtime_id")
    if not isinstance(bound_runtime_id, str) or not bound_runtime_id:
        reasons.append("C8:runtime_id_required")
    if runtime_id is not None and bound_runtime_id != runtime_id:
        reasons.append("C8:runtime_binding_runtime_id_mismatch")
    load_id = binding.get("policy_load_receipt_id")
    if not isinstance(load_id, str) or not load_id:
        reasons.append("C8:policy_load_receipt_required")
    execution_id = binding.get("baseline_execution_receipt_id")
    if not isinstance(execution_id, str) or not execution_id:
        reasons.append("C8:execution_receipt_required")
    expected_behavior = binding.get("baseline_behavior_digest")
    loaded_behavior = binding.get("loaded_behavior_digest")
    if not isinstance(expected_behavior, str) or not expected_behavior:
        reasons.append("C8:baseline_behavior_digest_required")
    if not isinstance(loaded_behavior, str) or not loaded_behavior:
        reasons.append("C8:ablation_behavior_digest_required")
    if (isinstance(expected_behavior, str) and isinstance(loaded_behavior, str) and
            expected_behavior != loaded_behavior):
        reasons.append("C8:ablation_behavior_digest_mismatch")

    load = None
    if isinstance(load_id, str) and load_id:
        load = conn.execute(
            "SELECT * FROM tehm_policy_load_receipts WHERE receipt_id=?",
            (load_id,)).fetchone()
    receipt: Mapping | None = None
    if load is None:
        reasons.append("C8:policy_load_receipt_missing")
    else:
        raw = None
        try:
            checked = validate_policy_load_row(load)
            raw = json.loads(checked["receipt_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            raw = None
        if isinstance(raw, Mapping):
            nested = raw.get("receipt")
            receipt = nested if isinstance(nested, Mapping) else None
            if (raw.get("policy_snapshot_id") != baseline_policy_id or
                    raw.get("runtime_id") != bound_runtime_id or
                    raw.get("loaded") is not True):
                reasons.append("C8:policy_load_binding_mismatch")
        else:
            reasons.append("C8:policy_load_receipt_malformed")
        if receipt is None:
            reasons.append("C8:ablation_execution_receipt_malformed")
        else:
            if receipt.get("execution_receipt_id") != execution_id:
                reasons.append("C8:ablation_execution_receipt_mismatch")
            if receipt.get("behavior_digest") != loaded_behavior:
                reasons.append("C8:ablation_behavior_receipt_mismatch")

    expected_eligible = not reasons
    if binding.get("eligible") is not expected_eligible:
        reasons.append("C8:policy_ablation_binding_eligibility_mismatch")
    return reasons


def _normalise_evidence_refs(
    evidence_refs: Mapping[str, Mapping], required: list[str],
) -> tuple[dict[str, dict], list[str]]:
    if not isinstance(evidence_refs, Mapping):
        raise ValueError("evidence_refs must be a mapping keyed by gate")
    normalised: dict[str, dict] = {}
    reasons: list[str] = []
    seen: set[tuple[str, str]] = set()
    for gate in required:
        raw = evidence_refs.get(gate)
        if not isinstance(raw, Mapping):
            raise ValueError(f"missing evidence reference for {gate}")
        try:
            evidence_id = _strict_text(
                raw.get("evidence_id"), label=f"{gate}:evidence_id")
            split = _strict_text(raw.get("split"), label=f"{gate}:split")
            verdict = _strict_text(raw.get("verdict"), label=f"{gate}:verdict")
            lineage_id = _strict_optional_text(
                raw.get("lineage_id"), label=f"{gate}:lineage_id")
        except ValueError as exc:
            raise ValueError(f"incomplete evidence reference for {gate}") from exc
        if split not in {"training", "calibration", "heldout", "ab"}:
            raise ValueError(f"invalid evidence split for {gate}: {split!r}")
        key = (GATE_EVIDENCE_TYPES[gate], evidence_id)
        if key in seen:
            raise ValueError("one evidence row cannot satisfy multiple capability gates")
        seen.add(key)
        normalised[gate] = {
            "evidence_id": evidence_id,
            "split": split,
            "verdict": verdict,
            "lineage_id": lineage_id,
        }
        if gate == "C4":
            try:
                execution_receipt_id = _strict_text(
                    raw.get("execution_receipt_id"),
                    label="C4:execution_receipt_id")
            except ValueError:
                reasons.append("C4:execution_receipt_id_missing")
            else:
                normalised[gate]["execution_receipt_id"] = execution_receipt_id
        if gate == "C6" and "causal_transfer_receipt_id" in raw:
            try:
                transfer_receipt_id = _strict_text(
                    raw.get("causal_transfer_receipt_id"),
                    label="C6:causal_transfer_receipt_id")
            except ValueError:
                reasons.append("C6:causal_transfer_receipt_id_missing")
            else:
                normalised[gate]["causal_transfer_receipt_id"] = transfer_receipt_id
        if gate == "C6" and "causal_transfer_receipt_ids" in raw:
            raw_ids = raw.get("causal_transfer_receipt_ids")
            if "causal_transfer_receipt_id" in raw:
                reasons.append("C6:causal_transfer_receipt_id_and_ids_ambiguous")
            transfer_ids = _strict_id_vector(
                raw_ids, label="C6:causal_transfer_receipt_ids")
            if transfer_ids is None:
                reasons.append("C6:causal_transfer_receipt_ids_malformed")
            else:
                normalised[gate]["causal_transfer_receipt_ids"] = transfer_ids
        elif gate != "C6" and "causal_transfer_receipt_ids" in raw:
            reasons.append(f"{gate}:causal_transfer_receipt_ids_not_allowed")
        elif gate != "C6" and "causal_transfer_receipt_id" in raw:
            reasons.append(f"{gate}:causal_transfer_receipt_id_not_allowed")
        if gate == "C7" and "retention_receipt_id" in raw:
            try:
                retention_receipt_id = _strict_text(
                    raw.get("retention_receipt_id"),
                    label="C7:retention_receipt_id")
            except ValueError:
                reasons.append("C7:retention_receipt_id_missing")
            else:
                normalised[gate]["retention_receipt_id"] = retention_receipt_id
        if gate == "C7" and "retention_receipt_ids" in raw:
            raw_ids = raw.get("retention_receipt_ids")
            if "retention_receipt_id" in raw:
                reasons.append("C7:retention_receipt_id_and_ids_ambiguous")
            retention_ids = _strict_id_vector(
                raw_ids, label="C7:retention_receipt_ids")
            if retention_ids is None:
                reasons.append("C7:retention_receipt_ids_malformed")
            else:
                normalised[gate]["retention_receipt_ids"] = retention_ids
        elif gate != "C7" and "retention_receipt_ids" in raw:
            reasons.append(f"{gate}:retention_receipt_ids_not_allowed")
        if split not in GATE_ALLOWED_SPLITS[gate]:
            reasons.append(f"{gate}:invalid_evidence_split")
        if verdict != "PASS":
            reasons.append(f"{gate}:evidence_verdict_not_pass")
    return normalised, reasons


def _validate_replay_evidence_ref(
        ref: Mapping, *, gate: str) -> tuple[dict | None, list[str]]:
    """Validate one persisted capability ref before using it as a lookup key."""
    reasons: list[str] = []
    try:
        evidence_id = _strict_text(
            ref.get("evidence_id"), label=f"{gate}:evidence_id")
        split = _strict_text(ref.get("split"), label=f"{gate}:split")
        verdict = _strict_text(ref.get("verdict"), label=f"{gate}:verdict")
        lineage_id = _strict_optional_text(
            ref.get("lineage_id"), label=f"{gate}:lineage_id")
        evidence_digest = _strict_text(
            ref.get("evidence_digest"), label=f"{gate}:evidence_digest")
    except ValueError:
        return None, [f"evidence:{gate}:ref_identity_malformed"]
    if split not in {"training", "calibration", "heldout", "ab"}:
        reasons.append(f"evidence:{gate}:ref_invalid_split")
    elif split not in GATE_ALLOWED_SPLITS[gate]:
        reasons.append(f"evidence:{gate}:ref_invalid_evidence_split")
    if "execution_receipt_id" in ref:
        if gate != "C4":
            reasons.append(f"evidence:{gate}:execution_receipt_id_not_allowed")
        try:
            execution_id = _strict_text(
                ref.get("execution_receipt_id"),
                label=f"{gate}:execution_receipt_id")
        except ValueError:
            reasons.append(f"evidence:{gate}:execution_receipt_id_malformed")
            execution_id = None
    else:
        execution_id = None
    optional_vectors: dict[str, list[str]] = {}
    for key in ("causal_transfer_receipt_ids", "retention_receipt_ids"):
        if key not in ref:
            continue
        if ((key.startswith("causal_") and gate != "C6") or
                (key.startswith("retention_") and gate != "C7")):
            reasons.append(f"evidence:{gate}:{key}_not_allowed")
        vector = _strict_id_vector(ref.get(key), label=f"{gate}:{key}")
        if vector is None:
            reasons.append(f"evidence:{gate}:{key}_malformed")
        else:
            optional_vectors[key] = vector
    for key in ("causal_transfer_receipt_id", "retention_receipt_id"):
        if key not in ref:
            continue
        expected_gate = "C6" if key.startswith("causal_") else "C7"
        if gate != expected_gate:
            reasons.append(f"evidence:{gate}:{key}_not_allowed")
        try:
            optional_vectors[key] = [_strict_text(
                ref.get(key), label=f"{gate}:{key}")]
        except ValueError:
            reasons.append(f"evidence:{gate}:{key}_malformed")
    if reasons:
        return None, reasons
    result = {
        "evidence_id": evidence_id, "split": split, "verdict": verdict,
        "lineage_id": lineage_id, "evidence_digest": evidence_digest,
    }
    if execution_id is not None:
        result["execution_receipt_id"] = execution_id
    result.update(optional_vectors)
    return result, []


def _retention_binding_reasons(
    conn: sqlite3.Connection, *, capability_id: str,
    evidence_ref: Mapping,
) -> list[str]:
    """Optionally bind C7 to a replayable retention ledger receipt.

    The field is optional for compatibility with older authority fixtures.  If
    supplied, it is a hard dependency: a missing, tampered, failed, or
    capability-mismatched retention receipt makes the authority attempt
    ineligible.  The retention receipt itself remains evaluation evidence and
    does not mutate capability lifecycle.
    """
    retention_receipt_id = evidence_ref.get("retention_receipt_id")
    if retention_receipt_id is not None and (
            type(retention_receipt_id) is not str or
            not retention_receipt_id.strip()):
        return ["C7:retention_receipt_id_malformed"]
    if isinstance(retention_receipt_id, str):
        retention_receipt_id = retention_receipt_id.strip()
    raw_ids = evidence_ref.get("retention_receipt_ids")
    if raw_ids is not None:
        receipt_ids = _strict_id_vector(
            raw_ids, label="C7:retention_receipt_ids")
        if receipt_ids is None:
            return ["C7:retention_receipt_ids_malformed"]
        if retention_receipt_id:
            return ["C7:retention_receipt_id_and_ids_ambiguous"]
    elif retention_receipt_id:
        receipt_ids = [retention_receipt_id]
    else:
        return []
    from .retention import (
        load_capability_retention_receipt, verify_capability_retention,
    )
    reasons: list[str] = []
    for ordinal, receipt_id in enumerate(receipt_ids):
        retention = load_capability_retention_receipt(conn, receipt_id)
        if retention is None:
            reasons.append(f"C7:retention:{ordinal}:receipt_missing")
            continue
        checked = verify_capability_retention(conn, capability_id, retention)
        if checked.get("eligible") is not True:
            reasons.extend(
                f"C7:retention:{ordinal}:{reason}"
                for reason in checked.get("reasons") or ("not_eligible",))
    return reasons


def _causal_transfer_binding_reasons(
    conn: sqlite3.Connection, *, evidence_ref: Mapping,
) -> list[str]:
    """Optionally bind C6 to replayable causal-transfer shadow receipts.

    C6 remains compatible with older generic capability fixtures that only
    have a held-out evidence row.  When a transfer receipt is supplied, it is
    a hard dependency: the receipt must exist, replay against the current DB,
    and establish an eligible L4 transfer.  This makes a causal C6 claim
    stronger without turning the causal ledger itself into lifecycle authority.
    """
    transfer_receipt_id = evidence_ref.get("causal_transfer_receipt_id")
    if transfer_receipt_id is not None and (
            type(transfer_receipt_id) is not str or
            not transfer_receipt_id.strip()):
        return ["C6:causal_transfer_receipt_id_malformed"]
    if isinstance(transfer_receipt_id, str):
        transfer_receipt_id = transfer_receipt_id.strip()
    raw_ids = evidence_ref.get("causal_transfer_receipt_ids")
    if raw_ids is not None:
        receipt_ids = _strict_id_vector(
            raw_ids, label="C6:causal_transfer_receipt_ids")
        if receipt_ids is None:
            return ["C6:causal_transfer_receipt_ids_malformed"]
        if transfer_receipt_id:
            return ["C6:causal_transfer_receipt_id_and_ids_ambiguous"]
    elif transfer_receipt_id:
        receipt_ids = [transfer_receipt_id]
    else:
        return []

    from tehm.causal import (  # local import avoids package import cycles
        load_causal_transfer_receipt, verify_causal_transfer,
    )

    expected_lineage = evidence_ref.get("lineage_id")
    if expected_lineage is not None and (
            type(expected_lineage) is not str or
            not expected_lineage.strip()):
        return ["C6:lineage_id_malformed"]
    if isinstance(expected_lineage, str):
        expected_lineage = expected_lineage.strip()
    reasons: list[str] = []
    for ordinal, receipt_id in enumerate(receipt_ids):
        prefix = f"C6:causal_transfer[{ordinal}]"
        try:
            receipt = load_causal_transfer_receipt(conn, receipt_id)
        except (TypeError, ValueError, sqlite3.Error):
            receipt = None
        if receipt is None:
            reasons.append(f"{prefix}:receipt_missing")
            continue
        try:
            checked = verify_causal_transfer(conn, receipt)
        except (TypeError, ValueError, sqlite3.Error) as exc:
            reasons.append(f"{prefix}:verification_error:{exc}")
            continue
        if checked.get("verified") is not True:
            reasons.extend(
                f"{prefix}:{reason}"
                for reason in checked.get("reasons") or ("not_verified",))
        elif checked.get("eligible") is not True:
            reasons.append(f"{prefix}:not_eligible")
        if receipt.evidence_level != "L4_TRANSFER_SUPPORTED_MECHANISM":
            reasons.append(f"{prefix}:evidence_level_not_l4")
        if expected_lineage not in (None, ""):
            transfer_lineages = receipt.transfer_receipt.get("transfer_lineages")
            if (not isinstance(transfer_lineages, (list, tuple)) or
                    any(type(value) is not str or not value.strip()
                        for value in transfer_lineages)):
                reasons.append(f"{prefix}:lineages_malformed")
            elif expected_lineage not in {
                    value.strip() for value in transfer_lineages}:
                reasons.append(f"{prefix}:lineage_mismatch")
    return reasons


def _memory_delta_binding(
    attribution: Mapping,
) -> tuple[dict | None, list[str]]:
    """Validate and return a self-contained C1 memory-delta receipt.

    Historical attribution fixtures omit ``detail.memory_delta`` and remain
    compatible.  New strict campaigns include the receipt; authority records
    it in the signed payload so a later replay cannot silently reduce C1 to a
    comparison of two opaque digest labels.
    """
    detail = attribution.get("detail")
    if not isinstance(detail, Mapping):
        return None, []
    raw = detail.get("memory_delta")
    if raw is None:
        return None, []
    if not isinstance(raw, Mapping):
        return None, ["C1:memory_delta_malformed"]
    baseline = (detail.get("baseline") or {})
    candidate = (detail.get("candidate") or {})
    if not isinstance(baseline, Mapping) or not isinstance(candidate, Mapping):
        return None, ["C1:memory_delta_baseline_candidate_malformed"]
    baseline_digest = baseline.get("memory_digest")
    candidate_digest = candidate.get("memory_digest")
    delta = raw.get("delta")
    if not isinstance(delta, Mapping):
        return None, ["C1:memory_delta_delta_malformed"]
    manifest = {
        "version": raw.get("version"),
        "baseline_memory_digest": raw.get("baseline_memory_digest"),
        "candidate_memory_digest": raw.get("candidate_memory_digest"),
        **dict(delta),
    }
    checked = evaluate_memory_delta(baseline_digest, candidate_digest, manifest)
    reasons: list[str] = []
    if checked.eligible is not True:
        reasons.extend(f"C1:{reason}" for reason in checked.reasons)
    if raw.get("eligible") is not checked.eligible:
        reasons.append("C1:memory_delta_eligibility_mismatch")
    if raw.get("changed_ids") != list(checked.changed_ids):
        reasons.append("C1:memory_delta_changed_ids_mismatch")
    if raw.get("delta") != checked.delta:
        reasons.append("C1:memory_delta_normalisation_mismatch")
    return dict(raw), reasons


def record_capability_authority(
    conn: sqlite3.Connection,
    *,
    capability_id: str,
    attribution_receipt,
    evidence_refs: Mapping[str, Mapping],
    candidate_policy_snapshot_id: str,
    runtime_id: str,
    gates: Mapping | None = None,
) -> CapabilityAuthorityReceipt:
    """Record an immutable, DB-bound C1-C8 authority receipt.

    The function is audit-safe when gates fail: it records the supplied gate
    evidence as a failed authority attempt and returns ``eligible=False``.
    It never changes capability lifecycle status.
    """
    try:
        capability_id = _strict_text(capability_id, label="capability_id")
        candidate_policy_snapshot_id = _strict_text(
            candidate_policy_snapshot_id, label="candidate_policy_snapshot_id")
        runtime_id = _strict_text(runtime_id, label="runtime_id")
    except ValueError as exc:
        raise ValueError("capability authority identity is malformed") from exc
    required_assets = _capability_row(conn, capability_id)
    attribution = _as_dict(attribution_receipt)
    attribution_gates = attribution.get("gates") or {}
    merged_gates = dict(attribution_gates)
    if gates is not None:
        merged_gates.update(dict(gates))
    gate_report = evaluate_capability_promotion_gates(
        merged_gates, required_assets=required_assets, strict=True)
    required = list(gate_report["required"])
    refs, evidence_reasons = _normalise_evidence_refs(evidence_refs, required)
    attribution_digest = "sha256:" + hashlib.sha256(
        stable_dumps(attribution).encode()).hexdigest()
    policy_reasons = _policy_binding_reasons(
        conn, attribution, candidate_policy_snapshot_id=candidate_policy_snapshot_id,
        runtime_id=runtime_id,
        execution_receipt_id=(refs.get("C4") or {}).get("execution_receipt_id"))
    retention_reasons = _retention_binding_reasons(
        conn, capability_id=capability_id, evidence_ref=refs.get("C7") or {})
    transfer_reasons = _causal_transfer_binding_reasons(
        conn, evidence_ref=refs.get("C6") or {})
    memory_delta, memory_delta_reasons = _memory_delta_binding(attribution)
    reasons = (list(evidence_reasons) + policy_reasons + retention_reasons +
               transfer_reasons + memory_delta_reasons)
    if attribution.get("promotable") is not True:
        reasons.append("attribution_receipt_not_promotable")
    if any(attribution_gates.get(gate) is not True for gate in CAPABILITY_GATES):
        reasons.append("attribution_c1_c8_gate_failed")
    if not gate_report["eligible"]:
        reasons.extend(f"gate:{name}" for name in gate_report["missing"])
    eligible = not reasons

    # Evidence digests are returned to callers and checked from the table, but
    # are not part of receipt identity (the receipt id is needed as the final
    # authority evidence row key).
    payload_refs = {
        gate: {key: value for key, value in ref.items()
               if key != "evidence_digest"}
        for gate, ref in refs.items()
    }
    payload = {
        "authority_version": AUTHORITY_VERSION,
        "capability_id": capability_id,
        "attribution_digest": attribution_digest,
        "candidate_policy_snapshot_id": candidate_policy_snapshot_id,
        "runtime_id": runtime_id,
        "gates": merged_gates,
        "evidence_refs": payload_refs,
        "required_assets": list(required_assets),
        "eligible": eligible,
        "reasons": sorted(set(reasons)),
    }
    if memory_delta is not None:
        payload["memory_delta"] = memory_delta
    memory_snapshot_binding = (attribution.get("detail") or {}).get(
        "memory_snapshot_binding")
    if (isinstance(memory_snapshot_binding, Mapping) and
            memory_snapshot_binding.get("strict") is True):
        # Persist the strict M_t/M_t+1 witness alongside the delta so later
        # authority replay can validate both policy snapshots independently of
        # the opaque attribution digest.
        payload["memory_snapshot_binding"] = dict(memory_snapshot_binding)
    runtime_behavior_binding = (attribution.get("detail") or {}).get(
        "runtime_behavior_binding")
    if (isinstance(runtime_behavior_binding, Mapping) and
            runtime_behavior_binding.get("strict") is True):
        # Persist the execution-to-behavior witness so C4 can be independently
        # replayed after the in-memory attribution object is gone.
        payload["runtime_behavior_binding"] = dict(runtime_behavior_binding)
    policy_ablation_binding = (attribution.get("detail") or {}).get(
        "policy_ablation_binding")
    if (isinstance(policy_ablation_binding, Mapping) and
            policy_ablation_binding.get("strict") is True):
        # Persist the baseline-policy ablation witness for independent C8
        # replay; the booleans alone are never treated as authority.
        payload["policy_ablation_binding"] = dict(policy_ablation_binding)
    expanded_attribution = (attribution.get("detail") or {}).get(
        "expanded_attribution")
    if isinstance(expanded_attribution, Mapping):
        # Keep P8 object-level witnesses with the authority payload so a
        # later replay can validate KDelta/ADelta and routing/state/attribution
        # receipts after the original in-memory audit object is gone.
        payload["expanded_attribution"] = dict(expanded_attribution)
    receipt_digest = "sha256:" + hashlib.sha256(
        stable_dumps(payload).encode()).hexdigest()
    receipt_id = "capability_authority_" + receipt_digest.split(":", 1)[1][:20]

    # Import lazily to avoid a registry <-> authority import cycle.
    from .registry import record_capability_evidence

    # Authority evidence is one derived transaction.  A conflicting immutable
    # row or any later write failure must not leave the first few gate rows in
    # the registry without their authority receipt.  Respect an outer caller
    # transaction by using a savepoint and committing only when this function
    # owns the transaction.
    had_outer_transaction = conn.in_transaction
    savepoint = "tehm_capability_authority_v1"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        for gate, ref in refs.items():
            evidence_type = GATE_EVIDENCE_TYPES[gate]
            digest = record_capability_evidence(
                conn, capability_id=capability_id, evidence_type=evidence_type,
                evidence_id=ref["evidence_id"], split=ref["split"],
                verdict=ref["verdict"], lineage_id=ref.get("lineage_id"),
                commit=False)
            ref["evidence_digest"] = digest
        record_capability_evidence(
            conn, capability_id=capability_id, evidence_type="capability_authority",
            evidence_id=receipt_id, split="ab",
            verdict="PASS" if eligible else "FAIL", commit=False)
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        if not had_outer_transaction:
            conn.commit()
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    return CapabilityAuthorityReceipt(
        capability_id=capability_id, authority_receipt_id=receipt_id,
        authority_version=AUTHORITY_VERSION, attribution_digest=attribution_digest,
        candidate_policy_snapshot_id=candidate_policy_snapshot_id,
        runtime_id=runtime_id, gates={key: value is True for key, value in merged_gates.items()},
        evidence_refs=refs, eligible=eligible,
        reasons=tuple(sorted(set(reasons))), receipt_digest=receipt_digest,
        payload=payload)


def verify_capability_authority(
    conn: sqlite3.Connection,
    capability_id: str,
    authority_receipt,
) -> dict:
    """Verify receipt bytes, evidence rows, policy binding, and all gates."""
    data = _as_dict(authority_receipt)
    if data.get("capability_id") != capability_id:
        return {"eligible": False, "reasons": ["capability_id_mismatch"]}
    payload = data.get("payload")
    if not isinstance(payload, Mapping):
        return {"eligible": False, "reasons": ["authority_payload_missing"]}
    expected_digest = "sha256:" + hashlib.sha256(
        stable_dumps(dict(payload)).encode()).hexdigest()
    reasons: list[str] = []
    for field in (
            "authority_receipt_id", "authority_version", "attribution_digest",
            "candidate_policy_snapshot_id", "runtime_id", "receipt_digest"):
        value = data.get(field)
        if type(value) is not str or not value.strip():
            reasons.append(f"authority_{field}_malformed")
    payload_candidate_policy_id = payload.get("candidate_policy_snapshot_id")
    payload_runtime_id = payload.get("runtime_id")
    if (type(payload_candidate_policy_id) is not str or
            not payload_candidate_policy_id.strip()):
        reasons.append("authority_candidate_policy_snapshot_id_payload_malformed")
        payload_candidate_policy_id = ""
    else:
        payload_candidate_policy_id = payload_candidate_policy_id.strip()
    if type(payload_runtime_id) is not str or not payload_runtime_id.strip():
        reasons.append("authority_runtime_id_payload_malformed")
        payload_runtime_id = ""
    else:
        payload_runtime_id = payload_runtime_id.strip()
    if data.get("authority_version") != AUTHORITY_VERSION:
        reasons.append("authority_version_mismatch")
    if data.get("receipt_digest") != expected_digest:
        reasons.append("authority_receipt_digest_mismatch")
    if data.get("authority_receipt_id") != "capability_authority_" + expected_digest.split(":", 1)[1][:20]:
        reasons.append("authority_receipt_id_mismatch")
    if data.get("eligible") is not True or payload.get("eligible") is not True:
        reasons.append("authority_receipt_not_eligible")
    if stable_dumps(payload.get("gates") or {}) != stable_dumps(data.get("gates") or {}):
        reasons.append("authority_gate_payload_mismatch")
    data_refs = data.get("evidence_refs")
    if data_refs is None:
        data_refs = {}
    elif not isinstance(data_refs, Mapping):
        reasons.append("authority_evidence_refs_malformed")
        data_refs = {}
    canonical_data_refs = {
        gate: {key: value for key, value in ref.items()
               if key != "evidence_digest"}
        for gate, ref in data_refs.items()
        if isinstance(ref, Mapping)
    }
    if stable_dumps(payload.get("evidence_refs") or {}) != stable_dumps(canonical_data_refs):
        reasons.append("authority_evidence_payload_mismatch")
    payload_memory_delta = payload.get("memory_delta")
    if "memory_delta" in payload:
        if not isinstance(payload_memory_delta, Mapping):
            reasons.append("C1:memory_delta_malformed")
        else:
            _, memory_delta_reasons = _memory_delta_binding({
                "detail": {
                    "baseline": {
                        "memory_digest": payload_memory_delta.get(
                            "baseline_memory_digest")},
                    "candidate": {
                        "memory_digest": payload_memory_delta.get(
                            "candidate_memory_digest")},
                    "memory_delta": payload_memory_delta,
                }})
            reasons.extend(memory_delta_reasons)
    payload_memory_snapshot_binding = payload.get("memory_snapshot_binding")
    if payload_memory_snapshot_binding is not None:
        if (not isinstance(payload_memory_snapshot_binding, Mapping) or
                payload_memory_snapshot_binding.get("strict") is not True):
            reasons.append("C1:memory_snapshot_binding_malformed")
        else:
            reasons.extend(_memory_snapshot_binding_reasons(
                conn, payload_memory_snapshot_binding,
                candidate_policy_snapshot_id=payload_candidate_policy_id))
    payload_runtime_behavior_binding = payload.get("runtime_behavior_binding")
    if payload_runtime_behavior_binding is not None:
        if (not isinstance(payload_runtime_behavior_binding, Mapping) or
                payload_runtime_behavior_binding.get("strict") is not True):
            reasons.append("C4:runtime_behavior_binding_malformed")
        else:
            reasons.extend(_runtime_behavior_binding_reasons(
                conn, payload_runtime_behavior_binding,
                candidate_policy_snapshot_id=payload_candidate_policy_id,
                runtime_id=payload_runtime_id))
    payload_policy_ablation_binding = payload.get("policy_ablation_binding")
    if payload_policy_ablation_binding is not None:
        if (not isinstance(payload_policy_ablation_binding, Mapping) or
                payload_policy_ablation_binding.get("strict") is not True):
            reasons.append("C8:policy_ablation_binding_malformed")
        else:
            payload_memory_binding = payload.get("memory_snapshot_binding")
            expected_baseline_policy = (
                payload_memory_binding.get("baseline_policy_snapshot_id")
                if isinstance(payload_memory_binding, Mapping) else None)
            reasons.extend(_policy_ablation_binding_reasons(
                conn, payload_policy_ablation_binding,
                baseline_policy_snapshot_id=expected_baseline_policy,
                runtime_id=payload_runtime_id))
    payload_expanded = payload.get("expanded_attribution")
    if payload_expanded is not None:
        if not isinstance(payload_expanded, Mapping):
            reasons.append("P8:expanded_attribution_malformed")
        else:
            memory_manifest = payload.get("memory_delta")
            if not isinstance(memory_manifest, Mapping):
                memory_manifest = {}
                if payload_expanded.get("strict") is True:
                    reasons.append("P8:expanded_memory_delta_missing")
                else:
                    # Non-strict compatibility receipts may carry the
                    # baseline/candidate labels only inside a typed delta.
                    # Recover those labels for structural replay without
                    # treating them as strict C1 authority.
                    for key in ("knowledge_delta", "asset_delta"):
                        candidate_delta = payload_expanded.get(key)
                        if isinstance(candidate_delta, Mapping):
                            memory_manifest = candidate_delta
                            break
            from .attribution import validate_expanded_attribution

            expanded, expanded_reasons = validate_expanded_attribution(
                baseline_memory_digest=memory_manifest.get(
                    "baseline_memory_digest"),
                candidate_memory_digest=memory_manifest.get(
                    "candidate_memory_digest"),
                knowledge_delta=payload_expanded.get("knowledge_delta"),
                asset_delta=payload_expanded.get("asset_delta"),
                routing_receipts=payload_expanded.get("routing_receipts"),
                state_resolution_receipt=payload_expanded.get(
                    "state_resolution_receipt"),
                failure_attribution_receipts=payload_expanded.get(
                    "failure_attribution_receipts"),
                candidate_lineage=payload_expanded.get("candidate_lineage"),
                strict=payload_expanded.get("strict") is True,
                memory_changed_ids=tuple(memory_manifest.get("changed_ids") or ()))
            reasons.extend(f"P8:{reason}" for reason in expanded_reasons)
            if stable_dumps(dict(payload_expanded)) != stable_dumps(expanded):
                reasons.append("P8:expanded_attribution_replay_mismatch")
            state_payload = payload_expanded.get("state_resolution_receipt")
            if isinstance(state_payload, Mapping):
                try:
                    from tehm.state import verify_resolution_snapshot

                    checked_state = verify_resolution_snapshot(
                        conn, state_payload.get("resolution_id"))
                    if stable_dumps(checked_state.to_dict()) != stable_dumps(
                            dict(state_payload)):
                        reasons.append("P8:state_resolution_receipt_replay_mismatch")
                except (TypeError, ValueError, KeyError, sqlite3.Error):
                    reasons.append("P8:state_resolution_receipt_unverifiable")
    for key in ("candidate_policy_snapshot_id", "runtime_id", "attribution_digest"):
        if payload.get(key) != data.get(key):
            reasons.append(f"authority_{key}_payload_mismatch")
    required_assets = _capability_row(conn, capability_id)
    raw_gates = data.get("gates")
    if raw_gates is None:
        raw_gates = {}
    elif not isinstance(raw_gates, Mapping):
        reasons.append("authority_gates_malformed")
        raw_gates = {}
    gate_report = evaluate_capability_promotion_gates(
        raw_gates, required_assets=required_assets, strict=True)
    if not gate_report["eligible"]:
        reasons.extend(f"gate:{name}" for name in gate_report["missing"])
    refs = data.get("evidence_refs")
    if not isinstance(refs, Mapping):
        reasons.append("authority_evidence_refs_missing")
        refs = {}
    for gate in gate_report["required"]:
        ref = refs.get(gate)
        if not isinstance(ref, Mapping):
            reasons.append(f"evidence:{gate}:missing")
            continue
        checked_ref, ref_reasons = _validate_replay_evidence_ref(ref, gate=gate)
        reasons.extend(ref_reasons)
        if checked_ref is None:
            continue
        evidence_id = checked_ref["evidence_id"]
        evidence_type = GATE_EVIDENCE_TYPES[gate]
        row = conn.execute(
            """SELECT split, lineage_id, verdict, evidence_digest
                 FROM tehm_capability_evidence
                WHERE capability_id=? AND evidence_type=? AND evidence_id=?""",
            (capability_id, evidence_type, evidence_id),
        ).fetchone()
        if row is None:
            reasons.append(f"evidence:{gate}:row_missing")
            continue
        if (row["split"], row["lineage_id"], row["verdict"]) != (
                checked_ref["split"], checked_ref["lineage_id"],
                checked_ref["verdict"]):
            reasons.append(f"evidence:{gate}:row_mismatch")
        recomputed = _evidence_digest(
            capability_id=capability_id, evidence_type=evidence_type,
            evidence_id=evidence_id, split=checked_ref["split"],
            verdict=checked_ref["verdict"],
            lineage_id=checked_ref["lineage_id"])
        if (row["evidence_digest"] != recomputed or
                checked_ref["evidence_digest"] != recomputed):
            reasons.append(f"evidence:{gate}:digest_mismatch")
    c7_ref = refs.get("C7") if isinstance(refs, Mapping) else None
    if isinstance(c7_ref, Mapping):
        reasons.extend(_retention_binding_reasons(
            conn, capability_id=capability_id, evidence_ref=c7_ref))
    c6_ref = refs.get("C6") if isinstance(refs, Mapping) else None
    if isinstance(c6_ref, Mapping):
        reasons.extend(_causal_transfer_binding_reasons(
            conn, evidence_ref=c6_ref))
    # Re-check the candidate snapshot and actual runtime load after the receipt
    # was written; this prevents a stale or copied policy receipt being reused.
    attribution_digest = data.get("attribution_digest")
    # The full attribution is intentionally not persisted in the registry; the
    # digest still binds the authority receipt to the caller's immutable audit.
    # Policy rows/load receipts provide the independently verifiable C2/C3 part.
    snapshot = conn.execute(
        "SELECT * FROM tehm_policy_snapshots WHERE policy_snapshot_id=?",
        (data.get("candidate_policy_snapshot_id"),)).fetchone()
    if snapshot is None:
        reasons.append("candidate_policy_snapshot_missing")
    else:
        try:
            snapshot = validate_policy_snapshot_row(snapshot)
        except ValueError:
            reasons.append("candidate_policy_snapshot_digest_mismatch")
            snapshot = None
    if isinstance(payload_memory_delta, Mapping) and snapshot is not None:
        if (payload_memory_delta.get("candidate_memory_digest") !=
                snapshot["memory_snapshot_id"]):
            reasons.append("C1:candidate_memory_snapshot_mismatch")
    load = conn.execute(
        """SELECT *
             FROM tehm_policy_load_receipts
             WHERE policy_snapshot_id=? AND runtime_id=? AND loaded=1
             ORDER BY created_at DESC, receipt_id DESC LIMIT 1""",
        (data.get("candidate_policy_snapshot_id"), data.get("runtime_id")),
    ).fetchone()
    if load is None:
        reasons.append("candidate_policy_runtime_load_missing")
    else:
        try:
            checked_load = validate_policy_load_row(load)
            load_payload = json.loads(checked_load["receipt_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            load_payload = {}
        if not isinstance(load_payload, Mapping):
            load_payload = {}
        expected_load_digest = "sha256:" + hashlib.sha256(
            stable_dumps(dict(load_payload)).encode()).hexdigest()
        if load["receipt_digest"] != expected_load_digest:
            reasons.append("runtime_load_receipt_digest_mismatch")
        if load_payload.get("policy_snapshot_id") != data.get(
                "candidate_policy_snapshot_id"):
            reasons.append("runtime_load_snapshot_id_mismatch")
        if load_payload.get("runtime_id") != data.get("runtime_id"):
            reasons.append("runtime_load_runtime_id_mismatch")
        if snapshot is not None and load_payload.get("policy_digest") != snapshot["policy_digest"]:
            reasons.append("runtime_load_policy_digest_mismatch")
        expected_execution = ((refs.get("C4") or {}).get(
            "execution_receipt_id"))
        nested = load_payload.get("receipt")
        actual_execution = (
            nested.get("execution_receipt_id")
            if isinstance(nested, Mapping) else None)
        if not expected_execution:
            reasons.append("candidate_runtime_execution_receipt_missing")
        elif actual_execution != expected_execution:
            reasons.append("candidate_runtime_execution_receipt_mismatch")
    authority_row = conn.execute(
        """SELECT evidence_digest, split, verdict
             FROM tehm_capability_evidence
            WHERE capability_id=? AND evidence_type='capability_authority'
              AND evidence_id=?""",
        (capability_id, data.get("authority_receipt_id")),
    ).fetchone()
    if authority_row is None:
        reasons.append("authority_evidence_row_missing")
    else:
        if authority_row["split"] != "ab":
            reasons.append("authority_evidence_row_split_mismatch")
        if authority_row["verdict"] != "PASS":
            reasons.append("authority_evidence_row_not_pass")
        authority_id = data.get("authority_receipt_id")
        row_split = authority_row["split"]
        row_verdict = authority_row["verdict"]
        if (type(authority_id) is not str or not authority_id.strip() or
                type(row_split) is not str or not row_split.strip() or
                type(row_verdict) is not str or not row_verdict.strip()):
            reasons.append("authority_evidence_row_identity_malformed")
        else:
            authority_digest = _evidence_digest(
                capability_id=capability_id,
                evidence_type="capability_authority",
                evidence_id=authority_id.strip(),
                split=row_split.strip(),
                verdict=row_verdict.strip(),
                lineage_id=None,
            )
            if authority_row["evidence_digest"] != authority_digest:
                reasons.append("authority_evidence_row_digest_mismatch")
    return {
        "eligible": not reasons,
        "reasons": sorted(set(reasons)),
        "gate_report": gate_report,
        "attribution_digest": attribution_digest,
    }


__all__ = [
    "AUTHORITY_VERSION", "CapabilityAuthorityReceipt",
    "GATE_ALLOWED_SPLITS", "GATE_EVIDENCE_TYPES",
    "record_capability_authority", "verify_capability_authority",
]
