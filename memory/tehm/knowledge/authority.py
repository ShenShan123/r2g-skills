"""Database-bound authority receipts for Mechanism Knowledge.

``evaluate_knowledge_authority`` remains a pure evidence projection used by
the router.  The lifecycle boundary consumes only receipts produced by
``record_knowledge_authority`` and replays their immutable claim, status and
evidence rows before allowing a claim to become ``validated``.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import replace

from tehm import db as tehm_db
from tehm.causal.evidence_level import at_least, validate_evidence_level
from tehm.causal.path_builder import validate_persisted_path_row
from tehm.ids import stable_dumps

from .claims import MechanismKnowledge
from .lifecycle import get_knowledge_status
from .receipts import KnowledgeAuthorityReceipt
from .registry import get_knowledge, get_knowledge_by_object_id
from .schema import ensure_knowledge_schema


AUTHORITY_VERSION = "knowledge-authority-v1"
_EVIDENCE_SPLITS = frozenset({"training", "calibration", "heldout", "ab"})


def _pure_authority(
        conn: sqlite3.Connection, knowledge: MechanismKnowledge, *,
        required_evidence_level: str,
        min_support_lineages: int) -> KnowledgeAuthorityReceipt:
    """Evaluate the current claim without writing an authority decision."""
    required = validate_evidence_level(required_evidence_level)
    if min_support_lineages < 1:
        raise ValueError("min_support_lineages must be positive")
    gates = {
        "claim_content_valid": True,
        "causal_paths_replay": True,
        "evidence_level_sufficient": at_least(knowledge.evidence_level, required),
        "lineage_diversity": False,
        "no_automatic_promotion": True,
    }
    lineages = set(knowledge.support_lineages)
    ensure_knowledge_schema(conn, commit=False)
    if not knowledge.causal_path_ids:
        gates["causal_paths_replay"] = False
    for path_id in knowledge.causal_path_ids:
        row = conn.execute(
            "SELECT * FROM tehm_causal_paths WHERE path_id=?", (path_id,)
        ).fetchone()
        if row is None:
            gates["causal_paths_replay"] = False
            continue
        try:
            validate_persisted_path_row(row, conn)
            if not at_least(row["evidence_level"], knowledge.evidence_level):
                gates["causal_paths_replay"] = False
            support = json.loads(row["support_json"])
            if isinstance(support, dict):
                values = support.get("unique_lineages", [])
                if isinstance(values, list):
                    lineages.update(str(value) for value in values if value)
        except (TypeError, ValueError, json.JSONDecodeError):
            gates["causal_paths_replay"] = False
    gates["lineage_diversity"] = len(lineages) >= min_support_lineages
    eligible = all(gates.values()) and knowledge.status in {"candidate", "validated"}
    reason = "eligible_for_authority_review" if eligible else \
        ";".join(name for name, passed in gates.items() if not passed)
    return KnowledgeAuthorityReceipt(
        object_id=knowledge.object_id, eligible=eligible,
        evidence_level=knowledge.evidence_level,
        required_evidence_level=required,
        support_lineages=tuple(sorted(lineages)), gates=gates, reason=reason)


def evaluate_knowledge_authority(
    conn: sqlite3.Connection, knowledge: MechanismKnowledge, *,
    required_evidence_level: str = "L3_REPLICATED_EFFECT",
    min_support_lineages: int = 2,
) -> KnowledgeAuthorityReceipt:
    """Evaluate evidence gates without writing lifecycle or authority rows."""
    if not isinstance(knowledge, MechanismKnowledge):
        raise TypeError("knowledge authority requires MechanismKnowledge")
    return _pure_authority(
        conn, knowledge, required_evidence_level=required_evidence_level,
        min_support_lineages=min_support_lineages)


def _payload(receipt: KnowledgeAuthorityReceipt | Mapping) -> dict:
    data = receipt.to_dict() if hasattr(receipt, "to_dict") else dict(receipt)
    fields = (
        "object_id", "eligible", "evidence_level", "required_evidence_level",
        "support_lineages", "gates", "reason", "authority_version",
        "knowledge_content_digest", "target_scope", "status_version",
        "min_support_lineages", "evidence_refs",
    )
    return {field: data.get(field) for field in fields}


def _receipt_digest(payload: Mapping) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(dict(payload)).encode()).hexdigest()


def _receipt_id(receipt_digest: str) -> str:
    return "knowledge_authority_" + receipt_digest.split(":", 1)[1][:20]


def _normalise_ref(value: object) -> dict:
    if not isinstance(value, Mapping):
        raise ValueError("knowledge authority evidence ref is malformed")
    required = {"evidence_type", "evidence_id", "split", "lineage_id",
                "evidence_level", "evidence_digest"}
    if not required <= set(value):
        raise ValueError("knowledge authority evidence ref is incomplete")
    result = {key: value.get(key) for key in required}
    if (type(result["evidence_type"]) is not str or
            not result["evidence_type"].strip() or
            type(result["evidence_id"]) is not str or
            not result["evidence_id"].strip() or
            result["split"] not in _EVIDENCE_SPLITS or
            result["lineage_id"] is not None and
            (type(result["lineage_id"]) is not str or
             not result["lineage_id"].strip()) or
            type(result["evidence_level"]) is not str or
            not result["evidence_level"].strip() or
            type(result["evidence_digest"]) is not str or
            not result["evidence_digest"].strip()):
        raise ValueError("knowledge authority evidence ref is invalid")
    result["evidence_type"] = result["evidence_type"].strip()
    result["evidence_id"] = result["evidence_id"].strip()
    result["evidence_level"] = validate_evidence_level(result["evidence_level"])
    if isinstance(result["lineage_id"], str):
        result["lineage_id"] = result["lineage_id"].strip()
    return result


def _claim_evidence_refs(conn: sqlite3.Connection, knowledge: MechanismKnowledge,
                         supplied: Sequence[Mapping] | None) -> tuple[dict, ...]:
    rows = conn.execute(
        """SELECT evidence_type, evidence_id, split, lineage_id,
                  evidence_level, evidence_digest
             FROM tehm_mechanism_knowledge_evidence
            WHERE knowledge_id=? AND version=?
            ORDER BY evidence_type, evidence_id""",
        (knowledge.knowledge_id, knowledge.version),
    ).fetchall()
    stored = {
        (str(row["evidence_type"]), str(row["evidence_id"])):
        {"evidence_type": row["evidence_type"], "evidence_id": row["evidence_id"],
         "split": row["split"], "lineage_id": row["lineage_id"],
         "evidence_level": row["evidence_level"],
         "evidence_digest": row["evidence_digest"]}
        for row in rows
    }
    if supplied is None:
        return tuple(stored[key] for key in sorted(stored))
    if isinstance(supplied, (str, bytes)):
        raise ValueError("knowledge authority evidence_refs must be a sequence")
    refs = tuple(_normalise_ref(item) for item in supplied)
    keys = [(item["evidence_type"], item["evidence_id"]) for item in refs]
    if len(set(keys)) != len(keys):
        raise ValueError("knowledge authority evidence_refs contain duplicates")
    for ref in refs:
        expected = stored.get((ref["evidence_type"], ref["evidence_id"]))
        if expected is None or expected != ref:
            raise ValueError("knowledge authority evidence ref is not bound to claim")
    return tuple(sorted(refs, key=lambda item: (
        item["evidence_type"], item["evidence_id"])))


def _strict_receipt(
        conn: sqlite3.Connection, knowledge: MechanismKnowledge, *,
        target_scope: str, required_evidence_level: str,
        min_support_lineages: int, evidence_refs: Sequence[Mapping] | None,
        evaluated: KnowledgeAuthorityReceipt | None = None) -> KnowledgeAuthorityReceipt:
    stored = get_knowledge(conn, knowledge.knowledge_id, knowledge.version,
                            target_scope=target_scope)
    if stored.to_dict() != knowledge.to_dict():
        raise ValueError("knowledge authority claim content does not match registry")
    refs = _claim_evidence_refs(conn, stored, evidence_refs)
    base = evaluated or _pure_authority(
        conn, stored, required_evidence_level=required_evidence_level,
        min_support_lineages=min_support_lineages)
    gates = dict(base.gates)
    gates["authority_evidence_ledger"] = bool(refs)
    eligible = bool(base.eligible and gates["authority_evidence_ledger"])
    reason = "eligible_for_authority_review" if eligible else \
        ";".join(name for name, passed in gates.items() if not passed)
    status = get_knowledge_status(
        conn, knowledge_id=stored.knowledge_id, version=stored.version,
        target_scope=target_scope)
    status_version = status["status_version"]
    if type(status_version) is not int or status_version < 1:
        raise ValueError("knowledge authority status_version is malformed")
    return replace(
        base, eligible=eligible, gates=gates, reason=reason,
        authority_version=AUTHORITY_VERSION,
        knowledge_content_digest=stored.content_digest,
        target_scope=target_scope, status_version=status_version,
        min_support_lineages=int(min_support_lineages), evidence_refs=refs)


def _insert_evidence_rows(conn: sqlite3.Connection,
                          receipt: KnowledgeAuthorityReceipt) -> None:
    knowledge_id, raw_version = receipt.object_id.rsplit("@", 1)
    for ref in receipt.evidence_refs:
        values = (
            receipt.authority_receipt_id, knowledge_id, int(raw_version),
            receipt.target_scope, ref["evidence_type"], ref["evidence_id"],
            ref["split"], ref["lineage_id"], ref["evidence_level"],
            ref["evidence_digest"],
        )
        existing = conn.execute(
            """SELECT knowledge_id, version, target_scope, split, lineage_id,
                      evidence_level, evidence_digest
                 FROM tehm_knowledge_authority_evidence
                WHERE authority_receipt_id=? AND evidence_type=? AND evidence_id=?""",
            (receipt.authority_receipt_id, ref["evidence_type"], ref["evidence_id"]),
        ).fetchone()
        if existing is not None:
            expected = (values[1], values[2], values[3], values[6],
                        values[7], values[8], values[9])
            if tuple(existing) != expected:
                raise ValueError("knowledge authority evidence is immutable and conflicts")
            continue
        conn.execute(
            """INSERT INTO tehm_knowledge_authority_evidence
               (authority_receipt_id, knowledge_id, version, target_scope,
                evidence_type, evidence_id, split, lineage_id, evidence_level,
                evidence_digest)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", values)


def record_knowledge_authority(
        conn: sqlite3.Connection, knowledge: MechanismKnowledge, *,
        target_scope: str = "global",
        required_evidence_level: str = "L3_REPLICATED_EFFECT",
        min_support_lineages: int = 2,
        evidence_refs: Sequence[Mapping] | None = None) -> KnowledgeAuthorityReceipt:
    """Persist a content/status/evidence-bound Knowledge authority receipt."""
    if not isinstance(knowledge, MechanismKnowledge):
        raise TypeError("knowledge authority requires MechanismKnowledge")
    if type(target_scope) is not str or not target_scope.strip():
        raise ValueError("knowledge authority target_scope is required")
    target_scope = target_scope.strip()
    ensure_knowledge_schema(conn, commit=False)
    evaluated = evaluate_knowledge_authority(
        conn, knowledge, required_evidence_level=required_evidence_level,
        min_support_lineages=min_support_lineages)
    receipt = _strict_receipt(
        conn, knowledge, target_scope=target_scope,
        required_evidence_level=required_evidence_level,
        min_support_lineages=min_support_lineages, evidence_refs=evidence_refs,
        evaluated=evaluated)
    payload = _payload(receipt)
    digest = _receipt_digest(payload)
    receipt = replace(receipt, authority_receipt_id=_receipt_id(digest),
                      receipt_digest=digest)
    receipt_json = stable_dumps(_payload(receipt))
    knowledge_id, raw_version = receipt.object_id.rsplit("@", 1)
    had_outer_transaction = conn.in_transaction
    savepoint = "tehm_knowledge_authority_v1"
    conn.execute(f"SAVEPOINT {savepoint}")
    active = True
    try:
        _insert_evidence_rows(conn, receipt)
        values = (
            receipt.authority_receipt_id, knowledge_id, int(raw_version),
            receipt.target_scope, int(receipt.eligible),
            receipt.knowledge_content_digest, int(receipt.status_version),
            receipt_json, receipt.receipt_digest, tehm_db.now_local())
        existing = conn.execute(
            """SELECT knowledge_id, version, target_scope, eligible,
                      knowledge_content_digest, status_version, receipt_json,
                      receipt_digest
                 FROM tehm_knowledge_authority_receipts
                WHERE authority_receipt_id=?""", (receipt.authority_receipt_id,),
        ).fetchone()
        if existing is not None:
            if tuple(existing) != values[1:-1]:
                raise ValueError("knowledge authority receipt is immutable and conflicts")
        else:
            conn.execute(
                """INSERT INTO tehm_knowledge_authority_receipts
                   (authority_receipt_id, knowledge_id, version, target_scope,
                    eligible, knowledge_content_digest, status_version,
                    receipt_json, receipt_digest, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", values)
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        active = False
        if not had_outer_transaction:
            conn.commit()
        return receipt
    except Exception:
        if active:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise


def _mapping(value: object) -> dict:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if not isinstance(value, Mapping):
        raise TypeError("knowledge authority receipt must be a mapping")
    return dict(value)


def verify_knowledge_authority(conn: sqlite3.Connection,
                               authority_receipt) -> dict:
    """Replay a stored Knowledge authority receipt and its evidence ledger."""
    try:
        data = _mapping(authority_receipt)
        payload = _payload(data)
    except (TypeError, ValueError) as exc:
        return {"eligible": False, "reasons": [str(exc)]}
    reasons: list[str] = []
    expected_digest = _receipt_digest(payload)
    if type(payload.get("eligible")) is not bool:
        reasons.append("authority_eligible_malformed")
    if type(payload.get("status_version")) is not int or isinstance(
            payload.get("status_version"), bool) or payload.get("status_version") < 1:
        reasons.append("authority_status_version_malformed")
    if type(payload.get("min_support_lineages")) is not int or isinstance(
            payload.get("min_support_lineages"), bool) or payload.get("min_support_lineages") < 1:
        reasons.append("authority_min_support_lineages_malformed")
    if payload.get("authority_version") != AUTHORITY_VERSION:
        reasons.append("authority_version_mismatch")
    if data.get("receipt_digest") != expected_digest:
        reasons.append("authority_receipt_digest_mismatch")
    if data.get("authority_receipt_id") != _receipt_id(expected_digest):
        reasons.append("authority_receipt_id_mismatch")
    object_id = payload.get("object_id")
    claim = None
    target_scope = payload.get("target_scope")
    if (type(object_id) is not str or "@" not in object_id or
            type(target_scope) is not str or not target_scope):
        reasons.append("authority_object_or_scope_malformed")
    else:
        try:
            claim = get_knowledge_by_object_id(conn, object_id,
                                               target_scope=target_scope)
        except (TypeError, ValueError):
            reasons.append("authority_claim_missing_or_malformed")
    if claim is not None:
        if payload.get("knowledge_content_digest") != claim.content_digest:
            reasons.append("authority_claim_digest_mismatch")
        try:
            status = get_knowledge_status(
                conn, knowledge_id=claim.knowledge_id, version=claim.version,
                target_scope=target_scope)
            if status["status_version"] != payload.get("status_version"):
                reasons.append("authority_status_version_mismatch")
        except (TypeError, ValueError):
            reasons.append("authority_status_missing_or_malformed")
    refs_raw = payload.get("evidence_refs")
    refs: tuple[dict, ...] = ()
    if isinstance(refs_raw, list):
        try:
            refs = tuple(_normalise_ref(item) for item in refs_raw)
        except (TypeError, ValueError) as exc:
            reasons.append(f"authority_evidence_refs_malformed:{exc}")
    else:
        reasons.append("authority_evidence_refs_missing")
    authority_id = data.get("authority_receipt_id")
    if not isinstance(authority_id, str) or not authority_id:
        reasons.append("authority_receipt_id_missing")
    elif not _table_exists(conn, "tehm_knowledge_authority_evidence"):
        reasons.append("authority_evidence_ledger_missing")
    else:
        loaded = conn.execute(
            """SELECT knowledge_id, version, target_scope, evidence_type,
                      evidence_id, split, lineage_id, evidence_level,
                      evidence_digest
                 FROM tehm_knowledge_authority_evidence
                WHERE authority_receipt_id=?
                ORDER BY evidence_type, evidence_id""", (authority_id,),
        ).fetchall()
        loaded_refs = tuple({
            "evidence_type": row["evidence_type"],
            "evidence_id": row["evidence_id"], "split": row["split"],
            "lineage_id": row["lineage_id"],
            "evidence_level": row["evidence_level"],
            "evidence_digest": row["evidence_digest"],
        } for row in loaded)
        expected_refs = tuple(sorted(refs, key=lambda item: (
            item["evidence_type"], item["evidence_id"])))
        if loaded_refs != expected_refs:
            reasons.append("authority_evidence_rows_mismatch")
        if claim is not None:
            for ref in refs:
                row = conn.execute(
                    """SELECT split, lineage_id, evidence_level, evidence_digest
                         FROM tehm_mechanism_knowledge_evidence
                        WHERE knowledge_id=? AND version=?
                          AND evidence_type=? AND evidence_id=?""",
                    (claim.knowledge_id, claim.version,
                     ref["evidence_type"], ref["evidence_id"]),
                ).fetchone()
                if row is None or tuple(row) != (
                        ref["split"], ref["lineage_id"], ref["evidence_level"],
                        ref["evidence_digest"]):
                    reasons.append("authority_claim_evidence_mismatch")
    if (claim is not None and type(payload.get("min_support_lineages")) is int and
            not isinstance(payload.get("min_support_lineages"), bool) and
            payload.get("min_support_lineages") >= 1):
        try:
            evaluated = evaluate_knowledge_authority(
                conn, claim,
                required_evidence_level=payload.get("required_evidence_level"),
                min_support_lineages=payload["min_support_lineages"])
            gates = dict(evaluated.gates)
            gates["authority_evidence_ledger"] = bool(refs)
            if gates != payload.get("gates"):
                reasons.append("authority_gates_mismatch")
            if tuple(evaluated.support_lineages) != tuple(
                    payload.get("support_lineages") or ()):
                reasons.append("authority_lineages_mismatch")
            expected_eligible = bool(evaluated.eligible and refs)
            if payload.get("eligible") is not expected_eligible:
                reasons.append("authority_eligible_mismatch")
        except (TypeError, ValueError, KeyError):
            reasons.append("authority_replay_failed")
    else:
        reasons.append("authority_min_support_lineages_malformed")
    if not _table_exists(conn, "tehm_knowledge_authority_receipts"):
        reasons.append("authority_receipt_ledger_missing")
    else:
        row = conn.execute(
            """SELECT knowledge_id, version, target_scope, eligible,
                      knowledge_content_digest, status_version, receipt_json,
                      receipt_digest
                 FROM tehm_knowledge_authority_receipts
                WHERE authority_receipt_id=?""", (authority_id,),
        ).fetchone()
        if row is None:
            reasons.append("authority_receipt_row_missing")
        else:
            try:
                stored_json = json.loads(row["receipt_json"])
            except (TypeError, json.JSONDecodeError):
                stored_json = None
            knowledge_id = (str(object_id).rsplit("@", 1)[0]
                            if isinstance(object_id, str) and "@" in object_id else "")
            raw_version = (str(object_id).rsplit("@", 1)[1]
                           if isinstance(object_id, str) and "@" in object_id else "-1")
            try:
                version = int(raw_version)
            except ValueError:
                version = -1
            expected_row = (
                knowledge_id, version, target_scope,
                int(bool(payload.get("eligible"))),
                payload.get("knowledge_content_digest"), payload.get("status_version"),
                stable_dumps(payload), expected_digest)
            actual_row = (
                row["knowledge_id"], row["version"], row["target_scope"],
                row["eligible"], row["knowledge_content_digest"],
                row["status_version"],
                stable_dumps(stored_json) if isinstance(stored_json, Mapping) else None,
                row["receipt_digest"])
            if actual_row != expected_row:
                reasons.append("authority_receipt_row_mismatch")
    if payload.get("eligible") is not True:
        reasons.append("authority_receipt_not_eligible")
    return {
        "eligible": not reasons,
        "reasons": sorted(set(reasons)),
        "knowledge_object_id": object_id,
        "target_scope": target_scope,
        "status_version": payload.get("status_version"),
        "gates": payload.get("gates") if isinstance(payload.get("gates"), Mapping) else {},
        "evidence_verified": not any("evidence" in reason for reason in reasons),
    }


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


__all__ = [
    "AUTHORITY_VERSION", "KnowledgeAuthorityReceipt",
    "evaluate_knowledge_authority", "record_knowledge_authority",
    "verify_knowledge_authority",
]
