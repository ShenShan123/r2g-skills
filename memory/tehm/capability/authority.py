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
        assets = []
    return tuple(str(item) for item in assets)


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
    if candidate.get("memory_digest") and candidate["memory_digest"] != snapshot["memory_snapshot_id"]:
        reasons.append("candidate_memory_snapshot_mismatch")
    load = conn.execute(
        """SELECT *
             FROM tehm_policy_load_receipts
             WHERE policy_snapshot_id=? AND runtime_id=?
             ORDER BY created_at DESC, receipt_id DESC LIMIT 1""",
        (candidate_policy_snapshot_id, runtime_id),
    ).fetchone()
    if load is None or not bool(load["loaded"]):
        reasons.append("candidate_policy_runtime_load_missing")
    else:
        try:
            checked_load = validate_policy_load_row(load)
            receipt = json.loads(checked_load["receipt_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            receipt = {}
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
        evidence_id = str(raw.get("evidence_id") or "")
        split = str(raw.get("split") or "")
        verdict = str(raw.get("verdict") or "")
        lineage_id = raw.get("lineage_id")
        if not evidence_id or not split or not verdict:
            raise ValueError(f"incomplete evidence reference for {gate}")
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
            execution_receipt_id = str(raw.get("execution_receipt_id") or "")
            if not execution_receipt_id:
                reasons.append("C4:execution_receipt_id_missing")
            else:
                normalised[gate]["execution_receipt_id"] = execution_receipt_id
        if gate == "C7" and "retention_receipt_id" in raw:
            retention_receipt_id = str(raw.get("retention_receipt_id") or "")
            if not retention_receipt_id:
                reasons.append("C7:retention_receipt_id_missing")
            else:
                normalised[gate]["retention_receipt_id"] = retention_receipt_id
        if split not in GATE_ALLOWED_SPLITS[gate]:
            reasons.append(f"{gate}:invalid_evidence_split")
        if verdict != "PASS":
            reasons.append(f"{gate}:evidence_verdict_not_pass")
    return normalised, reasons


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
    retention_receipt_id = str(evidence_ref.get("retention_receipt_id") or "")
    if not retention_receipt_id:
        return []
    from .retention import (
        load_capability_retention_receipt, verify_capability_retention,
    )
    retention = load_capability_retention_receipt(conn, retention_receipt_id)
    if retention is None:
        return ["C7:retention_receipt_missing"]
    checked = verify_capability_retention(conn, capability_id, retention)
    if checked.get("eligible") is True:
        return []
    return [f"C7:retention:{reason}"
            for reason in checked.get("reasons") or ("not_eligible",)]


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
    reasons = list(evidence_reasons) + policy_reasons + retention_reasons
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
    data_refs = data.get("evidence_refs") or {}
    canonical_data_refs = {
        gate: {key: value for key, value in ref.items()
               if key != "evidence_digest"}
        for gate, ref in data_refs.items()
        if isinstance(ref, Mapping)
    }
    if stable_dumps(payload.get("evidence_refs") or {}) != stable_dumps(canonical_data_refs):
        reasons.append("authority_evidence_payload_mismatch")
    for key in ("candidate_policy_snapshot_id", "runtime_id", "attribution_digest"):
        if payload.get(key) != data.get(key):
            reasons.append(f"authority_{key}_payload_mismatch")
    required_assets = _capability_row(conn, capability_id)
    gate_report = evaluate_capability_promotion_gates(
        data.get("gates") or {}, required_assets=required_assets, strict=True)
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
        evidence_id = str(ref.get("evidence_id") or "")
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
                ref.get("split"), ref.get("lineage_id"), ref.get("verdict")):
            reasons.append(f"evidence:{gate}:row_mismatch")
        recomputed = _evidence_digest(
            capability_id=capability_id, evidence_type=evidence_type,
            evidence_id=evidence_id, split=str(ref.get("split") or ""),
            verdict=str(ref.get("verdict") or ""),
            lineage_id=ref.get("lineage_id"))
        if row["evidence_digest"] != recomputed or ref.get("evidence_digest") != recomputed:
            reasons.append(f"evidence:{gate}:digest_mismatch")
    c7_ref = refs.get("C7") if isinstance(refs, Mapping) else None
    if isinstance(c7_ref, Mapping):
        reasons.extend(_retention_binding_reasons(
            conn, capability_id=capability_id, evidence_ref=c7_ref))
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
        authority_digest = _evidence_digest(
            capability_id=capability_id,
            evidence_type="capability_authority",
            evidence_id=str(data.get("authority_receipt_id") or ""),
            split=str(authority_row["split"] or ""),
            verdict=str(authority_row["verdict"] or ""),
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
