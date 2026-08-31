"""Immutable Mechanism Knowledge registry and evidence ledger."""
from __future__ import annotations

import json
import hashlib
import sqlite3
from collections.abc import Mapping, Sequence

from tehm import db as tehm_db
from tehm.causal.evidence_level import validate_evidence_level
from tehm.causal.path_builder import validate_persisted_path_row
from tehm.ids import stable_dumps

from .claims import KNOWLEDGE_STATUSES, MechanismKnowledge
from .receipts import MechanismKnowledgeReceipt
from .schema import ensure_knowledge_schema


def _json_value(raw: object, field: str, expected: type) -> object:
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"mechanism knowledge {field} is malformed JSON") from exc
    if not isinstance(value, expected):
        raise ValueError(f"mechanism knowledge {field} must be {expected.__name__}")
    return value


def _claim_from_row(conn: sqlite3.Connection, row: sqlite3.Row,
                    *, target_scope: str) -> MechanismKnowledge:
    status_row = conn.execute(
        """SELECT * FROM tehm_mechanism_knowledge_status
             WHERE knowledge_id=? AND version=? AND target_scope=?""",
        (row["knowledge_id"], row["version"], target_scope)).fetchone()
    if status_row is None:
        raise ValueError("mechanism knowledge status row is missing")
    status = status_row["status"]
    if status not in KNOWLEDGE_STATUSES:
        raise ValueError("mechanism knowledge status row is invalid")
    status_version = status_row["status_version"]
    if type(status_version) is not int or status_version < 1:
        raise ValueError("mechanism knowledge status version is invalid")
    try:
        provenance = json.loads(status_row["provenance_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("mechanism knowledge status provenance is malformed") from exc
    if not isinstance(provenance, dict):
        raise ValueError("mechanism knowledge status provenance is malformed")
    if type(status_row["updated_at"]) is not str or not status_row["updated_at"]:
        raise ValueError("mechanism knowledge status updated_at is invalid")
    claim = MechanismKnowledge(
        knowledge_id=row["knowledge_id"], version=row["version"],
        mechanism_family=row["mechanism_family"],
        compatibility_profile=row["compatibility_profile"],
        antecedent=_json_value(row["antecedent_json"], "antecedent", dict),
        intervention=_json_value(row["intervention_json"], "intervention", dict),
        mediated_effects=tuple(_json_value(
            row["mediated_effects_json"], "mediated_effects", list)),
        expected_outcome=_json_value(
            row["expected_outcome_json"], "expected_outcome", dict),
        positive_applicability=tuple(_json_value(
            row["positive_applicability_json"], "positive_applicability", list)),
        negative_applicability=tuple(_json_value(
            row["negative_applicability_json"], "negative_applicability", list)),
        preserved_obligations=tuple(_json_value(
            row["obligations_json"], "obligations", list)),
        known_failure_modes=tuple(_json_value(
            row["known_failure_modes_json"], "known_failure_modes", list)),
        causal_path_ids=tuple(_json_value(
            row["causal_path_ids_json"], "causal_path_ids", list)),
        evidence_level=validate_evidence_level(row["evidence_level"]),
        support_lineages=tuple(_json_value(
            row["support_lineages_json"], "support_lineages", list)),
        status=status,
    )
    if row["content_digest"] != claim.content_digest:
        raise ValueError("mechanism knowledge content digest mismatch")
    return claim


def get_knowledge(conn: sqlite3.Connection, knowledge_id: str, version: int,
                  *, target_scope: str = "global") -> MechanismKnowledge:
    ensure_knowledge_schema(conn, commit=False)
    row = conn.execute(
        "SELECT * FROM tehm_mechanism_knowledge WHERE knowledge_id=? AND version=?",
        (knowledge_id, version)).fetchone()
    if row is None:
        raise ValueError("mechanism knowledge claim not found")
    return _claim_from_row(conn, row, target_scope=target_scope)


def get_knowledge_by_object_id(conn: sqlite3.Connection, object_id: str,
                               *, target_scope: str = "global") -> MechanismKnowledge:
    if type(object_id) is not str or "@" not in object_id:
        raise ValueError("mechanism knowledge object ID is malformed")
    knowledge_id, raw_version = object_id.rsplit("@", 1)
    try:
        version = int(raw_version)
    except (TypeError, ValueError) as exc:
        raise ValueError("mechanism knowledge object ID version is malformed") from exc
    return get_knowledge(conn, knowledge_id, version, target_scope=target_scope)


def _claim_fields(claim: MechanismKnowledge) -> dict:
    return {
        "knowledge_id": claim.knowledge_id, "version": claim.version,
        "mechanism_family": claim.mechanism_family,
        "compatibility_profile": claim.compatibility_profile,
        "antecedent_json": stable_dumps(claim.antecedent),
        "intervention_json": stable_dumps(claim.intervention),
        "mediated_effects_json": stable_dumps(list(claim.mediated_effects)),
        "expected_outcome_json": stable_dumps(claim.expected_outcome),
        "positive_applicability_json": stable_dumps(list(claim.positive_applicability)),
        "negative_applicability_json": stable_dumps(list(claim.negative_applicability)),
        "obligations_json": stable_dumps(list(claim.preserved_obligations)),
        "known_failure_modes_json": stable_dumps(list(claim.known_failure_modes)),
        "causal_path_ids_json": stable_dumps(list(claim.causal_path_ids)),
        "evidence_level": claim.evidence_level,
        "support_lineages_json": stable_dumps(list(claim.support_lineages)),
        "content_digest": claim.content_digest,
    }


def record_knowledge_evidence(
    conn: sqlite3.Connection, *, knowledge: MechanismKnowledge,
    evidence_type: str, evidence_id: str, split: str = "training",
    lineage_id: str | None = None, evidence_level: str | None = None,
    commit: bool = True,
) -> str:
    if (type(evidence_type) is not str or not evidence_type.strip() or
            type(evidence_id) is not str or not evidence_id.strip()):
        raise ValueError("mechanism knowledge evidence type and ID are required")
    evidence_type = evidence_type.strip()
    evidence_id = evidence_id.strip()
    if type(split) is not str or split not in {"training", "calibration", "heldout", "ab"}:
        raise ValueError("mechanism knowledge evidence split is invalid")
    if lineage_id is not None and (type(lineage_id) is not str or not lineage_id.strip()):
        raise ValueError("mechanism knowledge evidence lineage is invalid")
    if isinstance(lineage_id, str):
        lineage_id = lineage_id.strip()
    level = validate_evidence_level(evidence_level or knowledge.evidence_level)
    if evidence_type == "causal_path":
        if split != "training":
            raise ValueError("causal path knowledge evidence must be training")
        if evidence_id not in knowledge.causal_path_ids:
            raise ValueError("causal path evidence is not referenced by the claim")
        row = conn.execute(
            "SELECT * FROM tehm_causal_paths WHERE path_id=?", (evidence_id,)
        ).fetchone()
        if row is None:
            raise ValueError("mechanism knowledge causal path evidence is missing")
        validate_persisted_path_row(row, conn)
        if row["evidence_level"] != level:
            raise ValueError("mechanism knowledge evidence level conflicts with path")
    digest = "sha256:" + hashlib.sha256(stable_dumps({
        "knowledge_id": knowledge.knowledge_id, "version": knowledge.version,
        "evidence_type": evidence_type, "evidence_id": evidence_id,
        "split": split, "lineage_id": lineage_id, "evidence_level": level,
    }).encode()).hexdigest()
    ensure_knowledge_schema(conn, commit=False)
    had_outer_transaction = conn.in_transaction
    existing = conn.execute(
        """SELECT split, lineage_id, evidence_level, evidence_digest
             FROM tehm_mechanism_knowledge_evidence
            WHERE knowledge_id=? AND version=? AND evidence_type=? AND evidence_id=?""",
        (knowledge.knowledge_id, knowledge.version, evidence_type, evidence_id)
    ).fetchone()
    if existing is not None:
        if tuple(existing) != (split, lineage_id, level, digest):
            raise ValueError("mechanism knowledge evidence is immutable and conflicts")
        return digest
    conn.execute(
        """INSERT INTO tehm_mechanism_knowledge_evidence
           (knowledge_id, version, evidence_type, evidence_id, split,
            lineage_id, evidence_level, evidence_digest)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (knowledge.knowledge_id, knowledge.version, evidence_type, evidence_id,
         split, lineage_id, level, digest))
    if commit and not had_outer_transaction:
        conn.commit()
    return digest


def register_knowledge(
    conn: sqlite3.Connection, knowledge: MechanismKnowledge, *,
    target_scope: str = "global", provenance: Mapping | None = None,
    evidence_refs: Sequence[Mapping] | None = None,
    created_at: str | None = None, commit: bool = True,
) -> MechanismKnowledgeReceipt:
    """Register immutable claim content and a non-authoritative status row."""
    if not isinstance(knowledge, MechanismKnowledge):
        raise TypeError("register_knowledge requires MechanismKnowledge")
    if type(target_scope) is not str or not target_scope.strip():
        raise ValueError("mechanism knowledge target_scope is required")
    target_scope = target_scope.strip()
    if knowledge.status not in {"shadow", "candidate"}:
        raise ValueError("knowledge registration cannot grant validated/production status")
    if knowledge.status == "candidate" and knowledge.evidence_level not in {
            "L2_CONTROLLED_INTERVENTION", "L3_REPLICATED_EFFECT",
            "L4_TRANSFER_SUPPORTED_MECHANISM"}:
        raise ValueError("L0/L1 knowledge is shadow-only")
    ensure_knowledge_schema(conn, commit=False)
    had_outer_transaction = conn.in_transaction
    expected = _claim_fields(knowledge)
    existing = conn.execute(
        "SELECT * FROM tehm_mechanism_knowledge WHERE knowledge_id=? AND version=?",
        (knowledge.knowledge_id, knowledge.version)).fetchone()
    if existing is not None:
        mismatches = [field for field, value in expected.items()
                      if existing[field] != value]
        if mismatches:
            raise ValueError(
                "mechanism knowledge is immutable and conflicts: "
                + ", ".join(mismatches))
    else:
        now = created_at or tehm_db.now_local()
        conn.execute(
            """INSERT INTO tehm_mechanism_knowledge
               (knowledge_id, version, mechanism_family, compatibility_profile,
                antecedent_json, intervention_json, mediated_effects_json,
                expected_outcome_json, positive_applicability_json,
                negative_applicability_json, obligations_json,
                known_failure_modes_json, causal_path_ids_json, evidence_level,
                support_lineages_json, content_digest, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (knowledge.knowledge_id, knowledge.version,
             knowledge.mechanism_family, knowledge.compatibility_profile,
             expected["antecedent_json"], expected["intervention_json"],
             expected["mediated_effects_json"], expected["expected_outcome_json"],
             expected["positive_applicability_json"],
             expected["negative_applicability_json"], expected["obligations_json"],
             expected["known_failure_modes_json"], expected["causal_path_ids_json"],
             expected["evidence_level"], expected["support_lineages_json"],
             expected["content_digest"], now))
    now = created_at or tehm_db.now_local()
    provenance_json = stable_dumps(dict(provenance or {
        "authority": "knowledge-registry-shadow",
        "registry_version": "knowledge-registry-v0.1",
    }))
    current = conn.execute(
        """SELECT status, status_version, provenance_json
             FROM tehm_mechanism_knowledge_status
            WHERE knowledge_id=? AND version=? AND target_scope=?""",
        (knowledge.knowledge_id, knowledge.version, target_scope)).fetchone()
    if current is None:
        conn.execute(
            """INSERT INTO tehm_mechanism_knowledge_status
               (knowledge_id, version, target_scope, status, status_version,
                provenance_json, updated_at)
               VALUES (?, ?, ?, ?, 1, ?, ?)""",
            (knowledge.knowledge_id, knowledge.version, target_scope,
             knowledge.status, provenance_json, now))
    elif current["provenance_json"] != provenance_json:
        raise ValueError("mechanism knowledge status provenance is immutable")
    refs = evidence_refs
    if refs is None:
        refs = [{"evidence_type": "causal_path", "evidence_id": path_id,
                 "split": "training", "lineage_id": lineage,
                 "evidence_level": knowledge.evidence_level}
                for path_id in knowledge.causal_path_ids
                for lineage in (knowledge.support_lineages[:1] or (None,))]
    for ref in refs:
        if not isinstance(ref, Mapping):
            raise ValueError("mechanism knowledge evidence reference is malformed")
        record_knowledge_evidence(
            conn, knowledge=knowledge,
            evidence_type=ref.get("evidence_type"), evidence_id=ref.get("evidence_id"),
            split=ref.get("split", "training"), lineage_id=ref.get("lineage_id"),
            evidence_level=ref.get("evidence_level"), commit=False)
    stored = get_knowledge(conn, knowledge.knowledge_id, knowledge.version,
                           target_scope=target_scope)
    if commit and not had_outer_transaction:
        conn.commit()
    return MechanismKnowledgeReceipt(
        knowledge_id=stored.knowledge_id, version=stored.version,
        object_id=stored.object_id, content_digest=stored.content_digest,
        evidence_level=stored.evidence_level, status=stored.status,
        target_scope=target_scope)


__all__ = [
    "get_knowledge", "get_knowledge_by_object_id", "record_knowledge_evidence",
    "register_knowledge",
]
