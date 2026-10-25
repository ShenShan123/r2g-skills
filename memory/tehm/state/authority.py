"""Persistent authority ledger for state-affecting memory relations.

Relations are immutable evidence edges.  This module stores a separate,
content-addressed authority decision so a relation can be approved without
rewriting its row or creating a circular relation/authority digest.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence

from tehm import db as tehm_db
from tehm.ids import stable_dumps

from .receipts import RelationAuthorityReceipt
from .relations import get_relation
from .schema import ensure_state_schema


_RELATION_EFFECTS = {
    "SUPERSEDES": "suppress_target",
    "INVALIDATES": "suppress_target",
    "RETIRES": "suppress_target",
    "REPLACED_BY": "replace_source",
}


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"relation authority {name} is required")
    return value.strip()


def _refs(value: Sequence[str] | None, *, required: bool) -> tuple[str, ...]:
    if value is None:
        values: tuple[str, ...] = ()
    elif isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError("relation authority evidence_refs must be a sequence")
    else:
        values = tuple(value)
    if required and not values:
        raise ValueError("eligible relation authority requires evidence_refs")
    if any(type(item) is not str or not item.strip() for item in values):
        raise ValueError("relation authority evidence_refs are invalid")
    if len(set(values)) != len(values):
        raise ValueError("relation authority evidence_refs contain duplicates")
    return tuple(sorted(item.strip() for item in values))


def _scope(value: Mapping | None) -> dict:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("relation authority scope must be an object")
    try:
        decoded = json.loads(stable_dumps(dict(value)))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("relation authority scope must be JSON-serializable") from exc
    if not isinstance(decoded, dict):  # pragma: no cover - serializer guarantee
        raise ValueError("relation authority scope must be an object")
    return decoded


def _build_receipt(
        relation_id: str, *, authority_type: str, eligible: bool,
        evidence_refs: Sequence[str] | None, scope: Mapping | None,
        approved_effect: str | None) -> RelationAuthorityReceipt:
    relation_id = _text(relation_id, "relation_id")
    authority_type = _text(authority_type, "authority_type")
    if type(eligible) is not bool:
        raise ValueError("relation authority eligible must be boolean")
    refs = _refs(evidence_refs, required=eligible)
    scope_value = _scope(scope)
    payload = {
        "version": "relation-authority-v1",
        "relation_id": relation_id,
        "authority_type": authority_type,
        "eligible": eligible,
        "evidence_refs": list(refs),
        "scope": scope_value,
        "approved_effect": approved_effect,
    }
    digest = "sha256:" + hashlib.sha256(
        stable_dumps(payload).encode()).hexdigest()
    return RelationAuthorityReceipt(
        relation_id=relation_id, authority_type=authority_type,
        eligible=eligible, evidence_refs=refs, replay_digest=digest,
        scope=scope_value, approved_effect=approved_effect)


def _result(receipt: RelationAuthorityReceipt, *, eligible: bool,
            reason: str) -> dict:
    return {
        "authority_receipt_id": receipt.receipt_id,
        "relation_id": receipt.relation_id,
        "eligible": eligible,
        "reason": reason,
        "receipt_digest": receipt.receipt_digest,
    }


def record_relation_authority(
        conn: sqlite3.Connection, relation_id: str, *,
        authority_type: str = "relation-authority",
        eligible: bool = True, evidence_refs: Sequence[str] | None = None,
        scope: Mapping | None = None,
        approved_effect: str | None = None,
        commit: bool = True) -> RelationAuthorityReceipt:
    """Record an immutable authority decision for an existing relation.

    The relation row is never rewritten.  The receipt's scope must equal the
    relation scope, and an eligible decision must use the effect prescribed by
    the relation type; this prevents a broad or semantically unrelated
    authority token from being reused at resolution time.
    """
    ensure_state_schema(conn, commit=False)
    relation = get_relation(conn, relation_id)
    if relation is None:
        raise ValueError("relation authority relation does not exist")
    if relation.relation_type not in _RELATION_EFFECTS:
        raise ValueError("relation authority requires a state-affecting relation")
    scope_value = relation.scope if scope is None else _scope(scope)
    if scope_value != relation.scope:
        raise ValueError("relation authority scope does not match relation")
    expected_effect = _RELATION_EFFECTS[relation.relation_type]
    if eligible and approved_effect != expected_effect:
        raise ValueError(
            f"relation authority approved_effect must be {expected_effect}")
    receipt = _build_receipt(
        relation.relation_id, authority_type=authority_type,
        eligible=eligible, evidence_refs=evidence_refs,
        scope=scope_value, approved_effect=approved_effect)
    receipt_json = stable_dumps(receipt.to_dict())
    values = (
        receipt.receipt_id, receipt.relation_id, receipt.authority_type,
        int(receipt.eligible), stable_dumps(receipt.scope),
        stable_dumps(list(receipt.evidence_refs)), receipt.approved_effect,
        receipt_json, receipt.receipt_digest, tehm_db.now_local(),
    )
    had_outer_transaction = conn.in_transaction
    savepoint = "tehm_relation_authority_v1"
    conn.execute(f"SAVEPOINT {savepoint}")
    active = True
    try:
        existing = conn.execute(
            """SELECT relation_id, authority_type, eligible, scope_json,
                      evidence_refs_json, approved_effect, receipt_json,
                      receipt_digest
                 FROM tehm_relation_authority_receipts
                WHERE authority_receipt_id=?""", (receipt.receipt_id,)
        ).fetchone()
        if existing is not None:
            if tuple(existing) != values[1:-1]:
                raise ValueError(
                    "relation authority receipt is immutable and conflicts")
        else:
            conn.execute(
                """INSERT INTO tehm_relation_authority_receipts
                   (authority_receipt_id, relation_id, authority_type, eligible,
                    scope_json, evidence_refs_json, approved_effect, receipt_json,
                    receipt_digest, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", values)
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        active = False
        if commit and not had_outer_transaction:
            conn.commit()
        return receipt
    except Exception:
        if active:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise


def verify_relation_authority(
        conn: sqlite3.Connection,
        receipt: RelationAuthorityReceipt | Mapping) -> dict:
    """Replay a stored relation authority receipt without mutating state."""
    try:
        checked = (receipt if isinstance(receipt, RelationAuthorityReceipt)
                   else RelationAuthorityReceipt.from_dict(receipt))
        row = conn.execute(
            """SELECT relation_id, authority_type, eligible, scope_json,
                      evidence_refs_json, approved_effect, receipt_json,
                      receipt_digest
                 FROM tehm_relation_authority_receipts
                WHERE authority_receipt_id=?""", (checked.receipt_id,)
        ).fetchone()
        if row is None:
            return _result(checked, eligible=False, reason="missing_receipt")
        stored_payload = json.loads(row["receipt_json"])
        stored = RelationAuthorityReceipt.from_dict(stored_payload)
        relation = get_relation(conn, checked.relation_id)
        if relation is None or relation.relation_type not in _RELATION_EFFECTS:
            return _result(checked, eligible=False, reason="missing_relation")
        expected = (
            relation.relation_id, checked.authority_type, int(checked.eligible),
            stable_dumps(checked.scope), stable_dumps(list(checked.evidence_refs)),
            checked.approved_effect, stable_dumps(checked.to_dict()),
            checked.receipt_digest,
        )
        if tuple(row) != expected or stored != checked:
            return _result(checked, eligible=False, reason="receipt_conflict")
        if checked.scope != relation.scope:
            return _result(checked, eligible=False, reason="scope_mismatch")
        if (checked.eligible and
                checked.approved_effect != _RELATION_EFFECTS[relation.relation_type]):
            return _result(checked, eligible=False, reason="approved_effect_mismatch")
        return _result(checked, eligible=checked.eligible, reason="replayed")
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        if isinstance(receipt, RelationAuthorityReceipt):
            return _result(receipt, eligible=False, reason="malformed_receipt")
        return {"authority_receipt_id": None, "eligible": False,
                "reason": "malformed_receipt"}


__all__ = ["record_relation_authority", "verify_relation_authority"]
