"""Canonical-evidence preservation checks for derived-memory maintenance.

Rule retirement, merge, and re-crystallization may change derived rules and
lifecycle rows, but they must not rewrite the raw execution evidence that
supports those interpretations.  The fingerprint here intentionally excludes
``tehm_rules``/``tehm_rule_status`` and other derived tables.
"""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass

from tehm.ids import stable_dumps


RAW_EVIDENCE_TABLES = (
    "tehm_states", "tehm_transitions", "tehm_episodes",
    "tehm_episode_steps", "tehm_dataset_membership", "tehm_edges",
    "tehm_physical_effects",
)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _table_payload(conn: sqlite3.Connection, table: str) -> list[dict]:
    if not _table_exists(conn, table):
        return []
    rows = [dict(row) for row in conn.execute(f"SELECT * FROM {table}").fetchall()]
    return sorted(rows, key=stable_dumps)


def raw_evidence_digest(conn: sqlite3.Connection) -> str:
    """Return a deterministic digest over immutable canonical evidence rows."""
    payload = {
        table: _table_payload(conn, table)
        for table in RAW_EVIDENCE_TABLES
    }
    return "sha256:" + hashlib.sha256(stable_dumps(payload).encode()).hexdigest()


@dataclass(frozen=True)
class RawEvidenceReceipt:
    before_digest: str
    after_digest: str
    preserved: bool
    tables: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict:
        return {
            "version": "raw-evidence-preservation-v1",
            "before_digest": self.before_digest,
            "after_digest": self.after_digest,
            "preserved": self.preserved,
            "tables": list(self.tables),
            "reason": self.reason,
        }


def verify_raw_evidence_unchanged(
    conn: sqlite3.Connection,
    before_digest: str,
) -> RawEvidenceReceipt:
    """Compare a pre-maintenance digest with the current canonical evidence."""
    if not isinstance(before_digest, str) or not before_digest:
        raise ValueError("before_digest is required")
    after_digest = raw_evidence_digest(conn)
    preserved = before_digest == after_digest
    return RawEvidenceReceipt(
        before_digest=before_digest,
        after_digest=after_digest,
        preserved=preserved,
        tables=RAW_EVIDENCE_TABLES,
        reason=("canonical_evidence_preserved" if preserved
                else "canonical_evidence_changed"),
    )


__all__ = [
    "RAW_EVIDENCE_TABLES", "RawEvidenceReceipt", "raw_evidence_digest",
    "verify_raw_evidence_unchanged",
]
