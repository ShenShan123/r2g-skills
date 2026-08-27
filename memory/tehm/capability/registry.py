"""Capability registry and evidence firewall."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass

from tehm import db as tehm_db
from tehm.ids import stable_dumps

CAPABILITY_STATUSES = frozenset({
    "observed_gap", "candidate", "verified", "promoted", "regressed", "retired",
})
EVIDENCE_SPLITS = frozenset({"training", "calibration", "heldout", "ab"})


@dataclass(frozen=True)
class CapabilityReceipt:
    capability_id: str
    mechanism_family: str
    status: str
    version: int

    def to_dict(self) -> dict:
        return {
            "capability_id": self.capability_id,
            "mechanism_family": self.mechanism_family,
            "status": self.status,
            "version": self.version,
        }


def capability_content_digest(capability: Mapping) -> str | None:
    """Recompute the content digest implied by a registry capability row."""
    if not isinstance(capability, Mapping):
        return None
    try:
        content = {
            "mechanism_family": capability["mechanism_family"],
            "applicability": json.loads(capability["applicability_json"]),
            "required_rules": json.loads(capability["required_rules_json"]),
            "required_assets": json.loads(capability["required_assets_json"]),
            "obligations": json.loads(capability["obligations_json"]),
            "budget": json.loads(capability["budget_json"]),
            "version": int(capability["version"]),
        }
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if (not isinstance(content["required_rules"], list) or
            not isinstance(content["required_assets"], list) or
            any(not isinstance(item, str)
                for item in (*content["required_rules"],
                             *content["required_assets"]))):
        return None
    return "sha1:" + hashlib.sha1(
        stable_dumps(content).encode()).hexdigest()


def validate_capability_row(row: Mapping) -> dict:
    """Fail closed when the content-addressed capability row is corrupted."""
    data = dict(row)
    digest = capability_content_digest(data)
    expected_id = ("capability_" + digest.split(":", 1)[1][:20]
                   if digest else None)
    if not digest or data.get("capability_id") != expected_id:
        raise ValueError("capability registry content digest mismatch")
    return data


def register_capability(
    conn: sqlite3.Connection,
    *,
    mechanism_family: str,
    applicability: dict | list,
    required_rules: list[str] | tuple[str, ...] = (),
    required_assets: list[str] | tuple[str, ...] = (),
    obligations: dict | list | None = None,
    budget: dict | None = None,
    status: str = "observed_gap",
    version: int = 1,
    provenance: dict | None = None,
    commit: bool = True,
) -> CapabilityReceipt:
    if not mechanism_family:
        raise ValueError("mechanism_family is required")
    if status not in CAPABILITY_STATUSES:
        raise ValueError(f"invalid capability status: {status!r}")
    if status not in {"observed_gap", "candidate"}:
        raise ValueError(
            "capability registration cannot grant verified/promoted/lifecycle status")
    if version < 1:
        raise ValueError("capability version must be positive")
    identity = {
        "mechanism_family": mechanism_family,
        "applicability": applicability,
        "required_rules": list(required_rules),
        "required_assets": list(required_assets),
        "obligations": obligations or {}, "budget": budget or {},
        "version": version,
    }
    capability_id = "capability_" + hashlib.sha1(
        stable_dumps(identity).encode()).hexdigest()[:20]
    now = tehm_db.now_local()
    had_outer_transaction = conn.in_transaction
    conn.execute(
        """INSERT OR IGNORE INTO tehm_capabilities
           (capability_id, mechanism_family, applicability_json,
            required_rules_json, required_assets_json, obligations_json,
            budget_json, status, version, provenance_json, created_at,
            updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (capability_id, mechanism_family, stable_dumps(applicability),
         stable_dumps(list(required_rules)), stable_dumps(list(required_assets)),
         stable_dumps(obligations or {}), stable_dumps(budget or {}), status,
         int(version), stable_dumps(provenance or {}), now, now))
    stored = conn.execute(
        "SELECT * FROM tehm_capabilities WHERE capability_id=?",
        (capability_id,)).fetchone()
    if stored is None:
        raise ValueError("capability was not persisted")
    validate_capability_row(stored)
    expected_fields = {
        "mechanism_family": mechanism_family,
        "applicability_json": stable_dumps(applicability),
        "required_rules_json": stable_dumps(list(required_rules)),
        "required_assets_json": stable_dumps(list(required_assets)),
        "obligations_json": stable_dumps(obligations or {}),
        "budget_json": stable_dumps(budget or {}),
        "version": int(version),
    }
    if any(stored[field] != value for field, value in expected_fields.items()):
        raise ValueError("capability is immutable and conflicts")
    if commit and not had_outer_transaction:
        conn.commit()
    return CapabilityReceipt(capability_id, mechanism_family, status, int(version))


def record_capability_evidence(
    conn: sqlite3.Connection,
    *,
    capability_id: str,
    evidence_type: str,
    evidence_id: str,
    split: str,
    verdict: str,
    lineage_id: str | None = None,
    commit: bool = True,
) -> str:
    capability_row = conn.execute(
        "SELECT * FROM tehm_capabilities WHERE capability_id=?",
        (capability_id,)).fetchone()
    if capability_row is None:
        raise ValueError("unknown capability_id")
    validate_capability_row(capability_row)
    if split not in EVIDENCE_SPLITS:
        raise ValueError(f"invalid capability evidence split: {split!r}")
    if not evidence_type or not evidence_id:
        raise ValueError("evidence_type and evidence_id are required")
    digest = "sha1:" + hashlib.sha1(stable_dumps({
        "capability_id": capability_id, "evidence_type": evidence_type,
        "evidence_id": evidence_id, "split": split, "verdict": verdict,
        "lineage_id": lineage_id,
    }).encode()).hexdigest()
    existing = conn.execute(
        """SELECT evidence_digest FROM tehm_capability_evidence
             WHERE capability_id=? AND evidence_type=? AND evidence_id=?""",
        (capability_id, evidence_type, evidence_id),
    ).fetchone()
    if existing is not None:
        if existing["evidence_digest"] != digest:
            raise ValueError("capability evidence is immutable and conflicts")
        return digest
    had_outer_transaction = conn.in_transaction
    conn.execute(
        """INSERT INTO tehm_capability_evidence
           (capability_id, evidence_type, evidence_id, split, lineage_id,
            verdict, evidence_digest)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (capability_id, evidence_type, evidence_id, split, lineage_id,
         verdict, digest))
    if commit and not had_outer_transaction:
        conn.commit()
    return digest


def promote_capability(conn: sqlite3.Connection, capability_id: str,
                       *, gates: dict | None = None,
                       attribution_receipt: dict | None = None,
                       authority_receipt: dict | None = None,
                       commit: bool = True) -> CapabilityReceipt:
    """Promote only after a database-bound C1-C8 authority receipt.

    ``gates`` and ``attribution_receipt`` are retained as compatibility
    arguments for callers that want to cross-check their inputs, but neither
    is trusted as authority.  ``authority_receipt`` must have been produced by
    :func:`tehm.capability.authority.record_capability_authority` and all of
    its referenced evidence rows are re-verified before the lifecycle update.
    """
    row = conn.execute(
        """SELECT *
             FROM tehm_capabilities WHERE capability_id=?""",
        (capability_id,)).fetchone()
    if row is None:
        raise ValueError("unknown capability_id")
    row = validate_capability_row(row)
    # Once the capability id is known, the authority receipt is the first
    # required boundary.  A lifecycle state alone must never look sufficient
    # for promotion (especially for an ``observed_gap`` capability).
    if authority_receipt is None:
        raise ValueError("capability promotion requires a recorded authority receipt")
    if row["status"] not in {"candidate", "verified"}:
        raise ValueError(
            f"capability status {row['status']!r} is not promotable")
    from .authority import verify_capability_authority

    authority_data = (authority_receipt.to_dict()
                      if hasattr(authority_receipt, "to_dict")
                      else dict(authority_receipt))
    authority_check = verify_capability_authority(
        conn, capability_id, authority_data)
    if not authority_check["eligible"]:
        raise ValueError(
            "capability authority receipt is not eligible: "
            f"{authority_check['reasons']}")
    if gates is not None and dict(gates) != dict(authority_data.get("gates") or {}):
        raise ValueError("supplied capability gates do not match authority receipt")
    if attribution_receipt is not None:
        attribution_data = (attribution_receipt.to_dict()
                            if hasattr(attribution_receipt, "to_dict")
                            else dict(attribution_receipt))
        attribution_digest = "sha256:" + hashlib.sha256(
            stable_dumps(attribution_data).encode()).hexdigest()
        if attribution_digest != authority_data.get("attribution_digest"):
            raise ValueError("attribution receipt does not match authority receipt")
    try:
        required_assets = json.loads(row["required_assets_json"] or "[]")
    except (TypeError, json.JSONDecodeError):
        required_assets = []
    gate_report = authority_check["gate_report"]
    provenance = stable_dumps({"authority": authority_data,
                               "gates": gate_report})
    had_outer_transaction = conn.in_transaction
    conn.execute(
        """UPDATE tehm_capabilities
              SET status='promoted', provenance_json=?, updated_at=?
            WHERE capability_id=?""",
        (provenance, tehm_db.now_local(), capability_id))
    if commit and not had_outer_transaction:
        conn.commit()
    return CapabilityReceipt(capability_id, row["mechanism_family"], "promoted",
                             int(row["version"]))


__all__ = ["CAPABILITY_STATUSES", "CapabilityReceipt", "EVIDENCE_SPLITS",
           "capability_content_digest", "validate_capability_row",
           "promote_capability", "record_capability_evidence",
           "register_capability"]
