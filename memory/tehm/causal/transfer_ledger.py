"""Database-bound ledger for held-out causal-transfer evaluations.

The transfer evaluator is intentionally pure and read-only.  This module is
the adjacent evidence seam: it records the evaluator receipt in an additive
shadow ledger, binds it to the exact persisted path and transition witnesses,
and replays those bindings before a caller may consume the result.  It never
changes a causal-path status, rule lifecycle, canonical evidence, or runtime
policy.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field

from tehm import db as tehm_db
from tehm.ids import stable_dumps

from .path_builder import validate_persisted_path_row
from .transfer import evaluate_transfer_supported_mechanism


TRANSFER_LEDGER_VERSION = "causal-transfer-ledger-v1"


@dataclass(frozen=True)
class CausalTransferLedgerReceipt:
    """Content-addressed wrapper around a pure :class:`TransferReceipt`."""

    transfer_receipt_id: str
    receipt_digest: str
    path_id: str
    path_digest: str
    training_campaign_id: str
    transfer_campaign_id: str
    require_full_oracle: bool
    eligible: bool
    evidence_level: str
    reason: str
    transfer_receipt: dict
    payload: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "version": TRANSFER_LEDGER_VERSION,
            "transfer_receipt_id": self.transfer_receipt_id,
            "receipt_digest": self.receipt_digest,
            "path_id": self.path_id,
            "path_digest": self.path_digest,
            "training_campaign_id": self.training_campaign_id,
            "transfer_campaign_id": self.transfer_campaign_id,
            "require_full_oracle": self.require_full_oracle,
            "eligible": self.eligible,
            "evidence_level": self.evidence_level,
            "reason": self.reason,
            "transfer_receipt": dict(self.transfer_receipt),
            "payload": dict(self.payload),
        }


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def ensure_transfer_ledger_schema(conn: sqlite3.Connection) -> None:
    """Create the additive transfer ledger without implicit commits."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS tehm_causal_transfer_receipts (
            transfer_receipt_id       TEXT PRIMARY KEY,
            path_id                   TEXT NOT NULL,
            path_digest               TEXT NOT NULL,
            training_campaign_id      TEXT NOT NULL,
            transfer_campaign_id      TEXT NOT NULL,
            training_transition_ids_json TEXT NOT NULL,
            transfer_transition_ids_json TEXT NOT NULL,
            require_full_oracle      INTEGER NOT NULL CHECK
                                      (require_full_oracle IN (0, 1)),
            eligible                  INTEGER NOT NULL CHECK (eligible IN (0, 1)),
            evidence_level            TEXT NOT NULL,
            reason                    TEXT NOT NULL,
            receipt_json              TEXT NOT NULL,
            receipt_digest            TEXT NOT NULL UNIQUE,
            created_at                TEXT NOT NULL
        )""")
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_causal_transfer_receipts_path
            ON tehm_causal_transfer_receipts(path_id, eligible)""")


def _receipt_digest(payload: Mapping) -> str:
    return "sha256:" + hashlib.sha256(
        stable_dumps(dict(payload)).encode()).hexdigest()


def _receipt_id(receipt_digest: str) -> str:
    return "causal_transfer_" + receipt_digest.split(":", 1)[1][:20]


def _ids(values, *, name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or values is None:
        raise ValueError(f"{name} must be a non-empty sequence")
    try:
        result = tuple(str(value).strip() for value in values)
    except TypeError as exc:
        raise ValueError(f"{name} must be a non-empty sequence") from exc
    if not result or any(not value for value in result):
        raise ValueError(f"{name} must be a non-empty sequence")
    if len(set(result)) != len(result):
        raise ValueError(f"{name} contains duplicate transition IDs")
    return tuple(sorted(result))


def _path_binding(conn: sqlite3.Connection, path_id: str) -> str:
    row = conn.execute(
        "SELECT * FROM tehm_causal_paths WHERE path_id=?", (path_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"unknown causal path: {path_id}")
    validate_persisted_path_row(row, conn)
    return str(row["path_digest"])


def _payload(
    *, path_id: str, path_digest: str, training_campaign_id: str,
    transfer_campaign_id: str, require_full_oracle: bool, receipt: Mapping,
) -> dict:
    return {
        "ledger_version": TRANSFER_LEDGER_VERSION,
        "path_id": path_id,
        "path_digest": path_digest,
        "training_campaign_id": training_campaign_id,
        "transfer_campaign_id": transfer_campaign_id,
        "require_full_oracle": bool(require_full_oracle),
        "training_transition_ids": list(receipt.get("training_transition_ids") or []),
        "transfer_transition_ids": list(receipt.get("transfer_transition_ids") or []),
        "eligible": receipt.get("eligible") is True,
        "evidence_level": str(receipt.get("evidence_level") or ""),
        "reason": str(receipt.get("reason") or ""),
        "transfer_receipt": dict(receipt),
    }


def _from_row(row: sqlite3.Row) -> CausalTransferLedgerReceipt:
    try:
        payload = json.loads(row["receipt_json"] or "{}")
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("causal transfer receipt payload is malformed") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("causal transfer receipt payload must be an object")
    transfer = payload.get("transfer_receipt")
    if not isinstance(transfer, Mapping):
        raise ValueError("causal transfer receipt witness is missing")
    return CausalTransferLedgerReceipt(
        transfer_receipt_id=str(row["transfer_receipt_id"]),
        receipt_digest=str(row["receipt_digest"]),
        path_id=str(row["path_id"]),
        path_digest=str(row["path_digest"]),
        training_campaign_id=str(row["training_campaign_id"]),
        transfer_campaign_id=str(row["transfer_campaign_id"]),
        require_full_oracle=bool(row["require_full_oracle"]),
        eligible=bool(row["eligible"]),
        evidence_level=str(row["evidence_level"]),
        reason=str(row["reason"]),
        transfer_receipt=dict(transfer),
        payload=dict(payload),
    )


def record_causal_transfer(
    conn: sqlite3.Connection,
    *,
    path_id: str,
    transfer_transition_ids,
    training_campaign_id: str,
    transfer_campaign_id: str | None = None,
    require_full_oracle: bool = False,
    commit: bool = True,
) -> CausalTransferLedgerReceipt:
    """Evaluate and persist one immutable shadow transfer receipt.

    Ineligible evaluations are retained as negative evidence.  The only
    writes are to the additive ledger table; the evaluator itself is called
    read-only and no causal path or lifecycle row is updated.
    """
    if not isinstance(path_id, str) or not path_id.strip():
        raise ValueError("path_id is required")
    if not isinstance(training_campaign_id, str) or not training_campaign_id.strip():
        raise ValueError("training_campaign_id is required")
    transfer_campaign_id = str(transfer_campaign_id or training_campaign_id)
    if not transfer_campaign_id.strip():
        raise ValueError("transfer_campaign_id is required")
    ids = _ids(transfer_transition_ids, name="transfer_transition_ids")
    path_digest = _path_binding(conn, path_id)
    pure = evaluate_transfer_supported_mechanism(
        conn, path_id, ids, training_campaign_id=training_campaign_id,
        transfer_campaign_id=transfer_campaign_id,
        require_full_oracle=require_full_oracle)
    transfer = pure.to_dict()
    payload = _payload(
        path_id=path_id, path_digest=path_digest,
        training_campaign_id=training_campaign_id,
        transfer_campaign_id=transfer_campaign_id,
        require_full_oracle=require_full_oracle, receipt=transfer)
    digest = _receipt_digest(payload)
    receipt_id = _receipt_id(digest)
    had_outer_transaction = conn.in_transaction
    ensure_transfer_ledger_schema(conn)
    savepoint = "tehm_causal_transfer_v1"
    conn.execute(f"SAVEPOINT {savepoint}")
    active = True
    try:
        row = conn.execute(
            """SELECT path_id, path_digest, training_campaign_id,
                      transfer_campaign_id, training_transition_ids_json,
                      transfer_transition_ids_json, require_full_oracle,
                      eligible, evidence_level, reason, receipt_json,
                      receipt_digest
                 FROM tehm_causal_transfer_receipts
                WHERE transfer_receipt_id=?""", (receipt_id,)).fetchone()
        values = (
            path_id, path_digest, training_campaign_id, transfer_campaign_id,
            stable_dumps(payload["training_transition_ids"]),
            stable_dumps(payload["transfer_transition_ids"]),
            int(require_full_oracle), int(pure.eligible), pure.evidence_level,
            pure.reason, stable_dumps(payload), digest,
        )
        if row is not None:
            if tuple(row) != values:
                raise ValueError("causal transfer receipt is immutable and conflicts")
        else:
            conn.execute(
                """INSERT INTO tehm_causal_transfer_receipts
                   (transfer_receipt_id, path_id, path_digest,
                    training_campaign_id, transfer_campaign_id,
                    training_transition_ids_json, transfer_transition_ids_json,
                    require_full_oracle, eligible, evidence_level, reason,
                    receipt_json, receipt_digest, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (receipt_id, *values, tehm_db.now_local()))
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        active = False
        if commit and not had_outer_transaction:
            conn.commit()
    except Exception:
        if active:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    row = conn.execute(
        "SELECT * FROM tehm_causal_transfer_receipts WHERE transfer_receipt_id=?",
        (receipt_id,)).fetchone()
    if row is None:  # pragma: no cover - guarded by insert/replay above
        raise RuntimeError("causal transfer receipt disappeared after write")
    return _from_row(row)


def load_causal_transfer_receipt(
    conn: sqlite3.Connection, transfer_receipt_id: str,
) -> CausalTransferLedgerReceipt | None:
    """Load one ledger receipt without trusting its serialized contents."""
    if not _table_exists(conn, "tehm_causal_transfer_receipts"):
        return None
    row = conn.execute(
        "SELECT * FROM tehm_causal_transfer_receipts "
        "WHERE transfer_receipt_id=?", (transfer_receipt_id,)).fetchone()
    return None if row is None else _from_row(row)


def _as_dict(value) -> dict:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if not isinstance(value, Mapping):
        raise TypeError("causal transfer receipt must be a mapping")
    return dict(value)


def verify_causal_transfer(conn: sqlite3.Connection, receipt) -> dict:
    """Replay and verify a ledger receipt against the current shadow DB.

    ``verified`` means the immutable receipt and all current witnesses agree;
    ``eligible`` is the underlying L4 claim.  A valid negative receipt thus
    returns ``verified=True, eligible=False`` and remains useful audit data.
    """
    reasons: list[str] = []
    try:
        data = _as_dict(receipt)
    except TypeError as exc:
        return {"verified": False, "eligible": False, "reasons": [str(exc)]}
    payload = data.get("payload")
    if not isinstance(payload, Mapping):
        return {"verified": False, "eligible": False,
                "reasons": ["causal_transfer_payload_missing"]}
    payload = dict(payload)
    expected_digest = _receipt_digest(payload)
    expected_id = _receipt_id(expected_digest)
    if data.get("version") != TRANSFER_LEDGER_VERSION:
        reasons.append("transfer_ledger_version_mismatch")
    if data.get("receipt_digest") != expected_digest:
        reasons.append("transfer_receipt_digest_mismatch")
    if data.get("transfer_receipt_id") != expected_id:
        reasons.append("transfer_receipt_id_mismatch")
    fields = ("path_id", "path_digest", "training_campaign_id",
              "transfer_campaign_id", "require_full_oracle")
    for field_name in fields:
        if data.get(field_name) != payload.get(field_name):
            reasons.append(f"transfer_{field_name}_mismatch")
    # ``to_dict()`` exposes a small convenience projection of the nested pure
    # receipt at the ledger top level.  It is not an independent authority
    # source: every exposed value must agree with the content-addressed
    # payload.  Without these checks a caller could alter the top-level
    # eligibility/evidence/reason fields (or the nested receipt) while keeping
    # an otherwise valid payload digest and receive ``verified=True``.
    transfer_payload = payload.get("transfer_receipt")
    if not isinstance(transfer_payload, Mapping):
        transfer_payload = {}
        reasons.append("transfer_receipt_witness_missing")
    for field_name in ("eligible", "evidence_level", "reason"):
        if data.get(field_name) != transfer_payload.get(field_name):
            reasons.append(f"transfer_{field_name}_mismatch")
    exposed_transfer = data.get("transfer_receipt")
    if not isinstance(exposed_transfer, Mapping):
        reasons.append("transfer_receipt_projection_missing")
    elif stable_dumps(dict(exposed_transfer)) != stable_dumps(dict(transfer_payload)):
        reasons.append("transfer_receipt_projection_mismatch")
    path_id = str(payload.get("path_id") or "")
    training_campaign_id = str(payload.get("training_campaign_id") or "")
    transfer_campaign_id = str(payload.get("transfer_campaign_id") or "")
    transfer_ids = payload.get("transfer_transition_ids")
    try:
        ids = _ids(transfer_ids, name="transfer_transition_ids")
    except ValueError as exc:
        reasons.append(str(exc))
        ids = ()
    try:
        current_path_digest = _path_binding(conn, path_id)
    except (KeyError, ValueError) as exc:
        reasons.append(f"path_binding_failed:{exc}")
        current_path_digest = None
    if current_path_digest is not None and payload.get("path_digest") != current_path_digest:
        reasons.append("path_digest_mismatch")
    transfer = transfer_payload
    if ids and path_id and training_campaign_id and transfer_campaign_id:
        replay = evaluate_transfer_supported_mechanism(
            conn, path_id, ids, training_campaign_id=training_campaign_id,
            transfer_campaign_id=transfer_campaign_id,
            require_full_oracle=bool(payload.get("require_full_oracle")))
        if stable_dumps(replay.to_dict()) != stable_dumps(dict(transfer)):
            reasons.append("transfer_replay_mismatch")
    stored = load_causal_transfer_receipt(
        conn, str(data.get("transfer_receipt_id") or ""))
    if stored is None:
        reasons.append("transfer_receipt_row_missing")
    else:
        if stable_dumps(stored.payload) != stable_dumps(payload):
            reasons.append("transfer_receipt_row_payload_mismatch")
        expected_row = (
            payload.get("path_id"), payload.get("path_digest"),
            payload.get("training_campaign_id"), payload.get("transfer_campaign_id"),
            stable_dumps(payload.get("training_transition_ids") or []),
            stable_dumps(payload.get("transfer_transition_ids") or []),
            int(bool(payload.get("require_full_oracle"))),
            int(payload.get("eligible") is True), payload.get("evidence_level"),
            payload.get("reason"), stable_dumps(payload), expected_digest,
        )
        actual_row = conn.execute(
            """SELECT path_id, path_digest, training_campaign_id,
                      transfer_campaign_id, training_transition_ids_json,
                      transfer_transition_ids_json, require_full_oracle,
                      eligible, evidence_level, reason, receipt_json,
                      receipt_digest
                 FROM tehm_causal_transfer_receipts
                WHERE transfer_receipt_id=?""",
            (str(data.get("transfer_receipt_id") or ""),)).fetchone()
        if actual_row is None or tuple(actual_row) != expected_row:
            reasons.append("transfer_receipt_row_binding_mismatch")
    valid = not reasons
    return {
        "verified": valid,
        "eligible": bool(valid and payload.get("eligible") is True),
        "reasons": sorted(set(reasons)),
        "transfer_receipt_id": data.get("transfer_receipt_id"),
        "path_id": path_id,
        "evidence_level": payload.get("evidence_level"),
    }


__all__ = [
    "TRANSFER_LEDGER_VERSION", "CausalTransferLedgerReceipt",
    "ensure_transfer_ledger_schema", "record_causal_transfer",
    "load_causal_transfer_receipt", "verify_causal_transfer",
]
