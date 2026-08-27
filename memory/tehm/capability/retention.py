"""Capability retention replay receipts and their database evidence ledger.

Retention is a separate audit from acquisition.  The pure evaluator remains
useful for external/evaluation-only reports; the database-bound API below is
the stronger seam used when a replay must be consumed as evidence.  It binds
the replay to an immutable capability, candidate policy snapshot, successful
runtime load, and an independent held-out/ab lineage.  Neither API changes
capability lifecycle state or production policy.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field

from tehm import db as tehm_db
from tehm.ids import stable_dumps

from .policy_snapshot import validate_policy_load_row, validate_policy_snapshot_row
from .registry import validate_capability_row


RETENTION_VERSION = "capability-retention-v1"
RETENTION_EVIDENCE_TYPE = "capability_retention"
RETENTION_SPLITS = frozenset({"heldout", "ab"})


@dataclass(frozen=True)
class CapabilityRetentionReceipt:
    capability_id: str
    replay_id: str
    retained: bool
    replay_verdict: str
    disjoint_lineage: bool
    non_target_regression_zero: bool
    evidence_id: str | None
    reason: str
    # Populated only by the DB-bound recorder.  Keeping these optional
    # preserves the compact shape of the pure external report receipt.
    retention_receipt_id: str = ""
    candidate_policy_snapshot_id: str | None = None
    candidate_policy_digest: str | None = None
    runtime_id: str | None = None
    policy_load_receipt_id: str | None = None
    split: str | None = None
    lineage_id: str | None = None
    receipt_digest: str = ""
    payload: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = {
            "capability_id": self.capability_id,
            "replay_id": self.replay_id,
            "retained": self.retained,
            "replay_verdict": self.replay_verdict,
            "disjoint_lineage": self.disjoint_lineage,
            "non_target_regression_zero": self.non_target_regression_zero,
            "evidence_id": self.evidence_id,
            "reason": self.reason,
        }
        if self.retention_receipt_id:
            data.update({
                "retention_receipt_id": self.retention_receipt_id,
                "candidate_policy_snapshot_id": self.candidate_policy_snapshot_id,
                "candidate_policy_digest": self.candidate_policy_digest,
                "runtime_id": self.runtime_id,
                "policy_load_receipt_id": self.policy_load_receipt_id,
                "split": self.split,
                "lineage_id": self.lineage_id,
                "receipt_digest": self.receipt_digest,
                "payload": self.payload,
            })
        return data


def _ensure_retention_schema(conn: sqlite3.Connection) -> None:
    """Create the additive retention ledger without bumping schema v4."""
    # The v4 migration chain is immutable.  This extension is idempotently
    # created on first use, just like the asset/rule authority ledgers, while
    # fresh stores also receive it from schema.sql.  Individual DDL statements
    # avoid executescript committing an unrelated outer transaction.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS tehm_capability_retention_receipts (
            retention_receipt_id          TEXT PRIMARY KEY,
            capability_id                 TEXT NOT NULL,
            replay_id                     TEXT NOT NULL,
            candidate_policy_snapshot_id TEXT NOT NULL,
            candidate_policy_digest       TEXT NOT NULL,
            runtime_id                    TEXT NOT NULL,
            policy_load_receipt_id        TEXT NOT NULL,
            split                         TEXT NOT NULL CHECK (split IN
                                          ('heldout', 'ab')),
            lineage_id                    TEXT,
            evidence_id                   TEXT,
            retained                      INTEGER NOT NULL CHECK (retained IN (0, 1)),
            replay_verdict                TEXT NOT NULL,
            disjoint_lineage              INTEGER NOT NULL CHECK (disjoint_lineage IN (0, 1)),
            non_target_regression_zero    INTEGER NOT NULL CHECK (non_target_regression_zero IN (0, 1)),
            receipt_json                  TEXT NOT NULL,
            receipt_digest                TEXT NOT NULL UNIQUE,
            created_at                    TEXT NOT NULL
        )""")
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_capability_retention_scope
            ON tehm_capability_retention_receipts(capability_id, split, retained)""")


def _as_dict(value) -> dict:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if not isinstance(value, Mapping):
        raise TypeError("retention receipt must be a mapping")
    return dict(value)


def _receipt_digest(payload: Mapping) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(dict(payload)).encode()).hexdigest()


def _receipt_id(receipt_digest: str) -> str:
    return "capability_retention_" + receipt_digest.split(":", 1)[1][:20]


def _evidence_digest(*, capability_id: str, evidence_type: str,
                     evidence_id: str, split: str, verdict: str,
                     lineage_id: str | None) -> str:
    return "sha1:" + hashlib.sha1(stable_dumps({
        "capability_id": capability_id,
        "evidence_type": evidence_type,
        "evidence_id": evidence_id,
        "split": split,
        "verdict": verdict,
        "lineage_id": lineage_id,
    }).encode()).hexdigest()


def _text(value) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def evaluate_capability_retention(
    *,
    capability_id: str,
    replay_id: str,
    replay: Mapping,
) -> CapabilityRetentionReceipt:
    """Evaluate one frozen-policy retention replay, fail-closed.

    This pure function intentionally does not require a split or database
    binding so external ORFS reports can remain read-only.  Callers that need
    authority-grade evidence must use :func:`record_capability_retention`.
    """
    if not _text(capability_id) or not _text(replay_id):
        raise ValueError("capability_id and replay_id are required")
    if not isinstance(replay, Mapping):
        raise TypeError("replay must be a mapping")
    evidence_id = _text(replay.get("evidence_id"))
    verdict = _text(replay.get("verdict")) or "UNKNOWN"
    disjoint = replay.get("disjoint_lineage") is True
    no_regression = replay.get("non_target_regression_zero") is True
    retained = bool(verdict == "PASS" and disjoint and no_regression and evidence_id)
    reason = "retention_verified" if retained else (
        "requires_pass_disjoint_lineage_no_regression_and_evidence")
    return CapabilityRetentionReceipt(
        capability_id=capability_id, replay_id=replay_id,
        retained=retained, replay_verdict=verdict,
        disjoint_lineage=disjoint,
        non_target_regression_zero=no_regression,
        evidence_id=evidence_id, reason=reason)


def _policy_load_binding(
    conn: sqlite3.Connection,
    *,
    snapshot_id: str,
    policy_digest: str,
    runtime_id: str,
    policy_load_receipt_id: str | None,
) -> tuple[sqlite3.Row | None, list[str]]:
    """Resolve and validate the exact runtime load used by a replay."""
    reasons: list[str] = []
    if policy_load_receipt_id:
        row = conn.execute(
            "SELECT * FROM tehm_policy_load_receipts WHERE receipt_id=?",
            (policy_load_receipt_id,)).fetchone()
    else:
        row = conn.execute(
            """SELECT * FROM tehm_policy_load_receipts
                WHERE policy_snapshot_id=? AND runtime_id=?
                ORDER BY created_at DESC, receipt_id DESC LIMIT 1""",
            (snapshot_id, runtime_id)).fetchone()
    if row is None:
        reasons.append("candidate_policy_runtime_load_missing")
        return None, reasons
    try:
        checked = validate_policy_load_row(row)
        payload = json.loads(checked["receipt_json"] or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        reasons.append("runtime_load_receipt_digest_mismatch")
        return row, reasons
    if not isinstance(payload, Mapping):
        reasons.append("runtime_load_receipt_payload_malformed")
        return row, reasons
    if not bool(row["loaded"]):
        reasons.append("candidate_policy_runtime_load_missing")
    if row["policy_snapshot_id"] != snapshot_id or payload.get("policy_snapshot_id") != snapshot_id:
        reasons.append("runtime_load_snapshot_id_mismatch")
    if row["runtime_id"] != runtime_id or payload.get("runtime_id") != runtime_id:
        reasons.append("runtime_load_runtime_id_mismatch")
    if payload.get("policy_digest") != policy_digest:
        reasons.append("runtime_load_policy_digest_mismatch")
    if policy_load_receipt_id and row["receipt_id"] != policy_load_receipt_id:
        reasons.append("runtime_load_receipt_id_mismatch")
    return row, reasons


def _binding_reasons(
    conn: sqlite3.Connection,
    *,
    replay: Mapping,
    snapshot: Mapping,
    runtime_id: str,
    policy_load_receipt_id: str | None,
) -> tuple[list[str], sqlite3.Row | None, str | None, str | None, str | None]:
    reasons: list[str] = []
    split = _text(replay.get("split"))
    lineage_id = _text(replay.get("lineage_id"))
    replay_policy_digest = _text(replay.get("candidate_policy_digest"))
    if split not in RETENTION_SPLITS:
        reasons.append("retention_split_must_be_heldout_or_ab")
    if not lineage_id:
        reasons.append("retention_lineage_id_required")
    if replay_policy_digest and replay_policy_digest != snapshot["policy_digest"]:
        reasons.append("retention_policy_digest_mismatch")
    load, load_reasons = _policy_load_binding(
        conn, snapshot_id=snapshot["policy_snapshot_id"],
        policy_digest=snapshot["policy_digest"], runtime_id=runtime_id,
        policy_load_receipt_id=policy_load_receipt_id)
    reasons.extend(load_reasons)
    return reasons, load, split, lineage_id, replay_policy_digest


def _build_payload(
    *, capability_id: str, replay_id: str, replay: Mapping,
    base: CapabilityRetentionReceipt, candidate_policy_snapshot_id: str,
    candidate_policy_digest: str, runtime_id: str,
    policy_load_receipt_id: str, split: str, lineage_id: str | None,
    retained: bool, reasons: list[str],
) -> dict:
    return {
        "retention_version": RETENTION_VERSION,
        "capability_id": capability_id,
        "replay_id": replay_id,
        "candidate_policy_snapshot_id": candidate_policy_snapshot_id,
        "candidate_policy_digest": candidate_policy_digest,
        "runtime_id": runtime_id,
        "policy_load_receipt_id": policy_load_receipt_id,
        "split": split,
        "lineage_id": lineage_id,
        "replay": dict(replay),
        "replay_verdict": base.replay_verdict,
        "disjoint_lineage": base.disjoint_lineage,
        "non_target_regression_zero": base.non_target_regression_zero,
        "evidence_id": base.evidence_id,
        "retained": bool(retained),
        "reasons": sorted(set(reasons)),
    }


def record_capability_retention(
    conn: sqlite3.Connection,
    *,
    capability_id: str,
    replay_id: str,
    replay: Mapping,
    candidate_policy_snapshot_id: str,
    runtime_id: str,
    policy_load_receipt_id: str | None = None,
    commit: bool = True,
) -> CapabilityRetentionReceipt:
    """Persist one immutable, policy/runtime-bound retention replay.

    Failed replay attempts are recorded as ``retained=0`` evidence, while
    malformed identities or unknown capability/snapshot inputs raise before
    any ledger row is created.  No capability lifecycle update is performed.
    """
    if not _text(capability_id) or not _text(replay_id):
        raise ValueError("capability_id and replay_id are required")
    if not _text(candidate_policy_snapshot_id) or not _text(runtime_id):
        raise ValueError("candidate_policy_snapshot_id and runtime_id are required")
    if not isinstance(replay, Mapping):
        raise TypeError("replay must be a mapping")
    capability_row = conn.execute(
        "SELECT * FROM tehm_capabilities WHERE capability_id=?", (capability_id,)
    ).fetchone()
    if capability_row is None:
        raise ValueError("unknown capability_id")
    validate_capability_row(capability_row)
    snapshot_row = conn.execute(
        "SELECT * FROM tehm_policy_snapshots WHERE policy_snapshot_id=?",
        (candidate_policy_snapshot_id,)).fetchone()
    if snapshot_row is None:
        raise ValueError("unknown candidate policy snapshot")
    snapshot = validate_policy_snapshot_row(snapshot_row)
    base = evaluate_capability_retention(
        capability_id=capability_id, replay_id=replay_id, replay=replay)
    load_id = _text(policy_load_receipt_id)
    reasons, load, split, lineage_id, _ = _binding_reasons(
        conn, replay=replay, snapshot=snapshot, runtime_id=runtime_id,
        policy_load_receipt_id=load_id)
    if load is not None:
        load_id = str(load["receipt_id"])
    if not load_id:
        # The table requires a load reference even for a failed attempt.  The
        # payload records the missing binding, so this fallback cannot be
        # mistaken for a successful runtime load during verification.
        load_id = "missing"
    stored_split = split if split in RETENTION_SPLITS else "ab"
    if not base.retained:
        reasons.append(base.reason)
    retained = bool(base.retained and not reasons)
    payload = _build_payload(
        capability_id=capability_id, replay_id=replay_id, replay=replay,
        base=base, candidate_policy_snapshot_id=candidate_policy_snapshot_id,
        candidate_policy_digest=snapshot["policy_digest"], runtime_id=runtime_id,
        policy_load_receipt_id=load_id, split=stored_split,
        lineage_id=lineage_id, retained=retained, reasons=reasons)
    digest = _receipt_digest(payload)
    receipt_id = _receipt_id(digest)
    receipt = CapabilityRetentionReceipt(
        capability_id=capability_id, replay_id=replay_id,
        retained=retained, replay_verdict=base.replay_verdict,
        disjoint_lineage=base.disjoint_lineage,
        non_target_regression_zero=base.non_target_regression_zero,
        evidence_id=base.evidence_id,
        reason="retention_verified" if retained else ";".join(sorted(set(reasons))),
        retention_receipt_id=receipt_id,
        candidate_policy_snapshot_id=candidate_policy_snapshot_id,
        candidate_policy_digest=snapshot["policy_digest"], runtime_id=runtime_id,
        policy_load_receipt_id=load_id, split=stored_split,
        lineage_id=lineage_id, receipt_digest=digest, payload=payload)

    _ensure_retention_schema(conn)
    had_outer_transaction = conn.in_transaction
    savepoint = "tehm_capability_retention_v1"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        row = conn.execute(
            "SELECT * FROM tehm_capability_retention_receipts "
            "WHERE retention_receipt_id=?", (receipt_id,)).fetchone()
        row_values = (
            capability_id, replay_id, candidate_policy_snapshot_id,
            snapshot["policy_digest"], runtime_id, load_id, stored_split,
            lineage_id, base.evidence_id, int(retained), base.replay_verdict,
            int(base.disjoint_lineage), int(base.non_target_regression_zero),
            stable_dumps(payload), digest)
        fields = (
            "capability_id", "replay_id", "candidate_policy_snapshot_id",
            "candidate_policy_digest", "runtime_id", "policy_load_receipt_id",
            "split", "lineage_id", "evidence_id", "retained",
            "replay_verdict", "disjoint_lineage", "non_target_regression_zero",
            "receipt_json", "receipt_digest")
        if row is not None:
            if tuple(row[key] for key in fields) != row_values:
                raise ValueError("capability retention receipt is immutable and conflicts")
        else:
            conn.execute(
                """INSERT INTO tehm_capability_retention_receipts
                   (retention_receipt_id, capability_id, replay_id,
                    candidate_policy_snapshot_id, candidate_policy_digest,
                    runtime_id, policy_load_receipt_id, split, lineage_id,
                    evidence_id, retained, replay_verdict, disjoint_lineage,
                    non_target_regression_zero, receipt_json, receipt_digest,
                    created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (receipt_id, *row_values, tehm_db.now_local()))
        # The registry evidence row is the immutable cross-module reference;
        # it is written in the same savepoint as the retention payload.
        from .registry import record_capability_evidence
        record_capability_evidence(
            conn, capability_id=capability_id,
            evidence_type=RETENTION_EVIDENCE_TYPE, evidence_id=receipt_id,
            split=stored_split, verdict="PASS" if retained else "FAIL",
            lineage_id=lineage_id, commit=False)
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        if commit and not had_outer_transaction:
            conn.commit()
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    return receipt


def verify_capability_retention(
    conn: sqlite3.Connection,
    capability_id: str,
    retention_receipt,
) -> dict:
    """Verify receipt bytes, policy/runtime binding, and registry evidence."""
    try:
        data = _as_dict(retention_receipt)
    except TypeError as exc:
        return {"eligible": False, "retained": False, "reasons": [str(exc)]}
    reasons: list[str] = []
    payload = data.get("payload")
    if data.get("capability_id") != capability_id:
        reasons.append("capability_id_mismatch")
    if not isinstance(payload, Mapping):
        return {"eligible": False, "retained": False,
                "reasons": [*reasons, "retention_payload_missing"]}
    payload = dict(payload)
    expected_digest = _receipt_digest(payload)
    expected_id = _receipt_id(expected_digest)
    if data.get("receipt_digest") != expected_digest:
        reasons.append("retention_receipt_digest_mismatch")
    if data.get("retention_receipt_id") != expected_id:
        reasons.append("retention_receipt_id_mismatch")
    if payload.get("retention_version") != RETENTION_VERSION:
        reasons.append("retention_version_mismatch")
    for key in (
        "capability_id", "replay_id", "candidate_policy_snapshot_id",
        "candidate_policy_digest", "runtime_id", "policy_load_receipt_id",
        "split", "lineage_id", "evidence_id", "retained",
        "replay_verdict", "disjoint_lineage", "non_target_regression_zero",
    ):
        if data.get(key) != payload.get(key):
            reasons.append(f"retention_{key}_payload_mismatch")
    if payload.get("capability_id") != capability_id:
        reasons.append("retention_payload_capability_mismatch")
    row = None
    if _table_exists(conn, "tehm_capability_retention_receipts"):
        row = conn.execute(
            "SELECT * FROM tehm_capability_retention_receipts "
            "WHERE retention_receipt_id=?", (data.get("retention_receipt_id"),)
        ).fetchone()
    if row is None:
        reasons.append("retention_ledger_row_missing")
    else:
        if row["receipt_digest"] != expected_digest or row["receipt_json"] != stable_dumps(payload):
            reasons.append("retention_ledger_payload_mismatch")
        if row["capability_id"] != capability_id:
            reasons.append("retention_ledger_capability_mismatch")
        if row["retained"] != int(bool(payload.get("retained"))):
            reasons.append("retention_ledger_status_mismatch")
    try:
        capability_row = conn.execute(
            "SELECT * FROM tehm_capabilities WHERE capability_id=?", (capability_id,)
        ).fetchone()
        if capability_row is None:
            reasons.append("unknown_capability_id")
        else:
            validate_capability_row(capability_row)
    except ValueError:
        reasons.append("capability_registry_digest_mismatch")
    snapshot = None
    try:
        snapshot_row = conn.execute(
            "SELECT * FROM tehm_policy_snapshots WHERE policy_snapshot_id=?",
            (payload.get("candidate_policy_snapshot_id"),)).fetchone()
        if snapshot_row is None:
            reasons.append("candidate_policy_snapshot_missing")
        else:
            snapshot = validate_policy_snapshot_row(snapshot_row)
            if snapshot["policy_digest"] != payload.get("candidate_policy_digest"):
                reasons.append("candidate_policy_digest_mismatch")
    except ValueError:
        reasons.append("candidate_policy_snapshot_digest_mismatch")
    replay = payload.get("replay")
    if not isinstance(replay, Mapping):
        reasons.append("retention_replay_payload_malformed")
    else:
        try:
            base = evaluate_capability_retention(
                capability_id=capability_id,
                replay_id=str(payload.get("replay_id") or ""), replay=replay)
            if base.retained != bool(payload.get("retained")):
                reasons.append("retention_replay_status_mismatch")
            if base.replay_verdict != payload.get("replay_verdict"):
                reasons.append("retention_replay_verdict_mismatch")
            if base.evidence_id != payload.get("evidence_id"):
                reasons.append("retention_replay_evidence_mismatch")
        except (TypeError, ValueError):
            reasons.append("retention_replay_payload_malformed")
    if snapshot is not None:
        binding_reasons, load, split, lineage_id, _ = _binding_reasons(
            conn, replay=replay if isinstance(replay, Mapping) else {},
            snapshot=snapshot, runtime_id=str(payload.get("runtime_id") or ""),
            policy_load_receipt_id=_text(payload.get("policy_load_receipt_id")))
        reasons.extend(binding_reasons)
        if split != payload.get("split"):
            reasons.append("retention_split_payload_mismatch")
        if lineage_id != payload.get("lineage_id"):
            reasons.append("retention_lineage_payload_mismatch")
        if load is None:
            reasons.append("retention_policy_load_row_missing")
    evidence = conn.execute(
        """SELECT split, lineage_id, verdict, evidence_digest
             FROM tehm_capability_evidence
            WHERE capability_id=? AND evidence_type=? AND evidence_id=?""",
        (capability_id, RETENTION_EVIDENCE_TYPE,
         data.get("retention_receipt_id"))).fetchone()
    if evidence is None:
        reasons.append("retention_evidence_row_missing")
    else:
        expected_split = payload.get("split") if payload.get("split") in RETENTION_SPLITS else "ab"
        expected_verdict = "PASS" if payload.get("retained") is True else "FAIL"
        if (evidence["split"], evidence["lineage_id"], evidence["verdict"]) != (
                expected_split, payload.get("lineage_id"), expected_verdict):
            reasons.append("retention_evidence_row_mismatch")
        expected_evidence_digest = _evidence_digest(
            capability_id=capability_id, evidence_type=RETENTION_EVIDENCE_TYPE,
            evidence_id=str(data.get("retention_receipt_id") or ""),
            split=str(evidence["split"] or ""), verdict=str(evidence["verdict"] or ""),
            lineage_id=evidence["lineage_id"])
        if evidence["evidence_digest"] != expected_evidence_digest:
            reasons.append("retention_evidence_digest_mismatch")
    if data.get("retained") is not True:
        reasons.append("retention_not_retained")
    return {
        "eligible": not reasons,
        "retained": data.get("retained") is True,
        "reasons": sorted(set(reasons)),
        "retention_receipt_id": data.get("retention_receipt_id"),
        "candidate_policy_snapshot_id": payload.get("candidate_policy_snapshot_id"),
        "runtime_id": payload.get("runtime_id"),
    }


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


__all__ = [
    "RETENTION_VERSION", "RETENTION_EVIDENCE_TYPE", "RETENTION_SPLITS",
    "CapabilityRetentionReceipt", "evaluate_capability_retention",
    "record_capability_retention", "verify_capability_retention",
]
