"""Immutable, content-addressed state relations."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from collections.abc import Mapping, Sequence

from tehm import db as tehm_db
from tehm.ids import stable_dumps

from .receipts import MemoryRelationReceipt
from .schema import ensure_state_schema
from .validation import (
    normalize_evidence_refs, normalize_scope, relation_content, relation_digest,
)


@dataclass(frozen=True)
class MemoryRelation:
    relation_id: str
    source_type: str
    source_id: str
    relation_type: str
    target_type: str
    target_id: str
    scope: dict
    evidence_refs: tuple[str, ...]
    authority_ref: str | None
    relation_digest: str
    created_at: str

    def content(self) -> dict:
        return relation_content(
            source_type=self.source_type, source_id=self.source_id,
            relation_type=self.relation_type, target_type=self.target_type,
            target_id=self.target_id, scope=self.scope,
            evidence_refs=self.evidence_refs, authority_ref=self.authority_ref)

    def to_dict(self) -> dict:
        return {**self.content(), "relation_id": self.relation_id,
                "relation_digest": self.relation_digest,
                "created_at": self.created_at}


def _relation_from_row(row: sqlite3.Row | Mapping) -> MemoryRelation:
    try:
        scope = json.loads(row["scope_json"])
        refs = json.loads(row["evidence_refs_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("state relation row contains malformed JSON") from exc
    content = relation_content(
        source_type=row["source_type"], source_id=row["source_id"],
        relation_type=row["relation_type"], target_type=row["target_type"],
        target_id=row["target_id"], scope=scope, evidence_refs=refs,
        authority_ref=row["authority_ref"])
    digest = relation_digest(content)
    if row["relation_digest"] != digest:
        raise ValueError("state relation digest mismatch")
    relation_id = row["relation_id"]
    expected_id = "relation_" + digest.split(":", 1)[1][:24]
    if relation_id != expected_id:
        raise ValueError("state relation id is not content-addressed")
    created_at = row["created_at"]
    if type(created_at) is not str or not created_at:
        raise ValueError("state relation created_at is invalid")
    return MemoryRelation(
        relation_id=relation_id, source_type=content["source_type"],
        source_id=content["source_id"], relation_type=content["relation_type"],
        target_type=content["target_type"], target_id=content["target_id"],
        scope=content["scope"], evidence_refs=tuple(content["evidence_refs"]),
        authority_ref=content["authority_ref"], relation_digest=digest,
        created_at=created_at)


def record_relation(
    conn: sqlite3.Connection, *, source_type: str, source_id: str,
    relation_type: str, target_type: str, target_id: str,
    scope: Mapping | None = None, evidence_refs: Sequence[str],
    authority_ref: str | None = None, created_at: str | None = None,
    commit: bool = True,
) -> MemoryRelationReceipt:
    """Persist one immutable relation; no lifecycle or canonical row changes."""
    ensure_state_schema(conn, commit=False)
    content = relation_content(
        source_type=source_type, source_id=source_id,
        relation_type=relation_type, target_type=target_type, target_id=target_id,
        scope=scope or {}, evidence_refs=evidence_refs, authority_ref=authority_ref)
    digest = relation_digest(content)
    relation_id = "relation_" + digest.split(":", 1)[1][:24]
    now = created_at or tehm_db.now_local()
    values = (
        relation_id, content["source_type"], content["source_id"],
        content["relation_type"], content["target_type"], content["target_id"],
        stable_dumps(content["scope"]), stable_dumps(content["evidence_refs"]),
        content["authority_ref"], digest, now,
    )
    had_outer_transaction = conn.in_transaction
    conn.execute(
        """INSERT OR IGNORE INTO tehm_memory_relations
           (relation_id, source_type, source_id, relation_type, target_type,
            target_id, scope_json, evidence_refs_json, authority_ref,
            relation_digest, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", values)
    row = conn.execute(
        "SELECT * FROM tehm_memory_relations WHERE relation_id=?",
        (relation_id,)).fetchone()
    if row is None:
        raise ValueError("state relation was not persisted")
    persisted = _relation_from_row(row)
    if persisted.content() != content or persisted.relation_digest != digest:
        raise ValueError("state relation replay conflicts with immutable content")
    if commit and not had_outer_transaction:
        conn.commit()
    return MemoryRelationReceipt(
        relation_id=relation_id, relation_type=persisted.relation_type,
        source_type=persisted.source_type, source_id=persisted.source_id,
        target_type=persisted.target_type, target_id=persisted.target_id,
        relation_digest=digest, shadow_only=authority_ref is None)


def get_relation(conn: sqlite3.Connection, relation_id: str) -> MemoryRelation | None:
    ensure_state_schema(conn, commit=False)
    row = conn.execute(
        "SELECT * FROM tehm_memory_relations WHERE relation_id=?", (relation_id,)
    ).fetchone()
    return _relation_from_row(row) if row is not None else None


def load_relations(conn: sqlite3.Connection) -> tuple[MemoryRelation, ...]:
    ensure_state_schema(conn, commit=False)
    rows = conn.execute(
        "SELECT * FROM tehm_memory_relations ORDER BY relation_id").fetchall()
    return tuple(_relation_from_row(row) for row in rows)


__all__ = ["MemoryRelation", "get_relation", "load_relations", "record_relation"]
