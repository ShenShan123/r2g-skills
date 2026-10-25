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
from collections.abc import Mapping

from tehm.ids import stable_dumps


RAW_EVIDENCE_TABLES = (
    "tehm_states", "tehm_transitions", "tehm_episodes",
    "tehm_episode_steps", "tehm_dataset_membership", "tehm_edges",
    "tehm_physical_effects",
)

ANTI_FORGETTING_VERSION = "anti-forgetting-witness-v1"


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"anti-forgetting witness {name} is required")
    return value.strip()


def _digest_text(value: object, name: str) -> str:
    value = _text(value, name)
    if not value.startswith("sha256:") or len(value) <= len("sha256:"):
        raise ValueError(f"anti-forgetting witness {name} must be a sha256 digest")
    return value


def _strict_bool(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"anti-forgetting witness {name} must be boolean")
    return value


def _refs(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or isinstance(value, (str, bytes)):
        raise ValueError("anti-forgetting witness evidence_refs must be a sequence")
    refs = tuple(_text(item, "evidence_refs") for item in value)
    if len(set(refs)) != len(refs):
        raise ValueError("anti-forgetting witness evidence_refs must be unique")
    return tuple(sorted(refs))


@dataclass(frozen=True)
class AntiForgettingWitness:
    """Typed proof that a proposed shadow mutation was regression-audited.

    The four receipts are deliberately references, not caller booleans about
    production safety.  A mutation may be consumed only when the target
    replay, non-target regression audit, independent held-out audit, and
    rollback pointer all report a successful gate.  The witness itself never
    changes canonical memory or lifecycle authority.
    """

    target_replay_receipt_id: str
    target_replay_digest: str
    target_replay_passed: bool
    non_target_regression_receipt_id: str
    non_target_regression_digest: str
    non_target_regression_free: bool
    heldout_audit_receipt_id: str
    heldout_audit_digest: str
    heldout_audit_passed: bool
    rollback_pointer: str
    rollback_receipt_digest: str
    rollback_verified: bool
    evidence_refs: tuple[str, ...] = ()
    version: str = ANTI_FORGETTING_VERSION

    def __post_init__(self) -> None:
        for value, name in (
                (self.target_replay_receipt_id, "target_replay_receipt_id"),
                (self.non_target_regression_receipt_id,
                 "non_target_regression_receipt_id"),
                (self.heldout_audit_receipt_id, "heldout_audit_receipt_id"),
                (self.rollback_pointer, "rollback_pointer"),
                (self.version, "version")):
            _text(value, name)
        for value, name in (
                (self.target_replay_digest, "target_replay_digest"),
                (self.non_target_regression_digest, "non_target_regression_digest"),
                (self.heldout_audit_digest, "heldout_audit_digest"),
                (self.rollback_receipt_digest, "rollback_receipt_digest")):
            _digest_text(value, name)
        for value, name in (
                (self.target_replay_passed, "target_replay_passed"),
                (self.non_target_regression_free, "non_target_regression_free"),
                (self.heldout_audit_passed, "heldout_audit_passed"),
                (self.rollback_verified, "rollback_verified")):
            _strict_bool(value, name)
        object.__setattr__(self, "evidence_refs", _refs(self.evidence_refs))

    @property
    def eligible(self) -> bool:
        return bool(
            self.target_replay_passed and self.non_target_regression_free and
            self.heldout_audit_passed and self.rollback_verified)

    @property
    def receipt_digest(self) -> str:
        return "sha256:" + hashlib.sha256(
            stable_dumps(self.to_dict()).encode()).hexdigest()

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "target_replay_receipt_id": self.target_replay_receipt_id,
            "target_replay_digest": self.target_replay_digest,
            "target_replay_passed": self.target_replay_passed,
            "non_target_regression_receipt_id": self.non_target_regression_receipt_id,
            "non_target_regression_digest": self.non_target_regression_digest,
            "non_target_regression_free": self.non_target_regression_free,
            "heldout_audit_receipt_id": self.heldout_audit_receipt_id,
            "heldout_audit_digest": self.heldout_audit_digest,
            "heldout_audit_passed": self.heldout_audit_passed,
            "rollback_pointer": self.rollback_pointer,
            "rollback_receipt_digest": self.rollback_receipt_digest,
            "rollback_verified": self.rollback_verified,
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, payload: object) -> "AntiForgettingWitness":
        if not isinstance(payload, Mapping):
            raise ValueError("anti-forgetting witness must be an object")
        required = {
            "target_replay_receipt_id", "target_replay_digest", "target_replay_passed",
            "non_target_regression_receipt_id", "non_target_regression_digest",
            "non_target_regression_free", "heldout_audit_receipt_id",
            "heldout_audit_digest", "heldout_audit_passed", "rollback_pointer",
            "rollback_receipt_digest", "rollback_verified", "evidence_refs",
        }
        if not required <= set(payload):
            raise ValueError("anti-forgetting witness is missing fields")
        witness = cls(
            target_replay_receipt_id=payload["target_replay_receipt_id"],
            target_replay_digest=payload["target_replay_digest"],
            target_replay_passed=payload["target_replay_passed"],
            non_target_regression_receipt_id=payload["non_target_regression_receipt_id"],
            non_target_regression_digest=payload["non_target_regression_digest"],
            non_target_regression_free=payload["non_target_regression_free"],
            heldout_audit_receipt_id=payload["heldout_audit_receipt_id"],
            heldout_audit_digest=payload["heldout_audit_digest"],
            heldout_audit_passed=payload["heldout_audit_passed"],
            rollback_pointer=payload["rollback_pointer"],
            rollback_receipt_digest=payload["rollback_receipt_digest"],
            rollback_verified=payload["rollback_verified"],
            evidence_refs=tuple(payload["evidence_refs"]),
            version=payload.get("version", ANTI_FORGETTING_VERSION),
        )
        supplied = payload.get("receipt_digest")
        if supplied is not None and supplied != witness.receipt_digest:
            raise ValueError("anti-forgetting witness receipt digest mismatch")
        return witness


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
