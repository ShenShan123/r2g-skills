"""Content-addressed policy snapshots used for capability attribution."""
from __future__ import annotations

import hashlib
import sqlite3
import datetime
import json
from collections.abc import Mapping
from dataclasses import dataclass

from tehm import db as tehm_db
from tehm.ids import stable_dumps


@dataclass(frozen=True)
class PolicySnapshotReceipt:
    policy_snapshot_id: str
    memory_snapshot_id: str
    policy_digest: str

    def to_dict(self) -> dict:
        return {
            "policy_snapshot_id": self.policy_snapshot_id,
            "memory_snapshot_id": self.memory_snapshot_id,
            "policy_digest": self.policy_digest,
        }


@dataclass(frozen=True)
class PolicyLoadReceipt:
    receipt_id: str
    policy_snapshot_id: str
    runtime_id: str
    loaded: bool
    receipt_digest: str

    def to_dict(self) -> dict:
        return {
            "receipt_id": self.receipt_id,
            "policy_snapshot_id": self.policy_snapshot_id,
            "runtime_id": self.runtime_id,
            "loaded": self.loaded,
            "receipt_digest": self.receipt_digest,
        }


def _policy_content_from_row(row: Mapping) -> dict:
    """Decode one policy row into the canonical content-addressed payload."""
    try:
        rules = json.loads(row["promoted_rules_json"] or "[]")
        assets = json.loads(row["promoted_assets_json"] or "[]")
        retrieval = json.loads(row["retrieval_config_json"] or "{}")
        routing = json.loads(row["routing_config_json"] or "{}")
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("policy snapshot row contains malformed JSON") from exc
    try:
        memory_snapshot_id = row["memory_snapshot_id"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("policy snapshot memory_snapshot_id is malformed") from exc
    if (not isinstance(memory_snapshot_id, str) or
            not memory_snapshot_id.strip()):
        raise ValueError("policy snapshot memory_snapshot_id is malformed")
    if (not isinstance(rules, list) or
            any(not isinstance(item, str) for item in rules)):
        raise ValueError("policy snapshot promoted_rules is malformed")
    if (not isinstance(assets, list) or
            any(not isinstance(item, str) for item in assets)):
        raise ValueError("policy snapshot promoted_assets is malformed")
    if not isinstance(retrieval, dict) or not isinstance(routing, dict):
        raise ValueError("policy snapshot config is malformed")
    return {
        "memory_snapshot_id": memory_snapshot_id,
        "promoted_rules": rules,
        "promoted_assets": assets,
        "retrieval_config": retrieval,
        "routing_config": routing,
    }


def _policy_digest(content: Mapping) -> str:
    return "sha256:" + hashlib.sha256(
        stable_dumps(dict(content)).encode()).hexdigest()


def validate_policy_snapshot_row(row: Mapping) -> dict:
    """Fail closed when a content-addressed policy row was tampered with."""
    data = dict(row)
    content = _policy_content_from_row(data)
    digest = _policy_digest(content)
    expected_id = "policy_" + digest.split(":", 1)[1][:20]
    if (data.get("policy_digest") != digest or
            data.get("policy_snapshot_id") != expected_id):
        raise ValueError("policy snapshot content digest mismatch")
    if (not isinstance(data.get("created_at"), str) or
            not data["created_at"]):
        raise ValueError("policy snapshot created_at is malformed")
    return data


def validate_policy_load_row(row: Mapping) -> dict:
    """Verify a content-addressed runtime load receipt row before reuse."""
    data = dict(row)
    try:
        payload = json.loads(data.get("receipt_json") or "{}")
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("policy load receipt contains malformed JSON") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("policy load receipt payload is malformed")
    stored_loaded = data.get("loaded")
    if type(stored_loaded) is not int or stored_loaded not in (0, 1):
        raise ValueError("policy load receipt loaded field is malformed")
    if not isinstance(payload.get("loaded"), bool):
        raise ValueError("policy load receipt payload loaded field is malformed")
    if (not isinstance(data.get("policy_snapshot_id"), str) or
            not data["policy_snapshot_id"] or
            not isinstance(data.get("runtime_id"), str) or
            not data["runtime_id"]):
        raise ValueError("policy load receipt identity is malformed")
    digest = "sha256:" + hashlib.sha256(
        stable_dumps(dict(payload)).encode()).hexdigest()
    expected_id = "policy_load_" + digest.split(":", 1)[1][:20]
    if (data.get("receipt_digest") != digest or
            data.get("receipt_id") != expected_id or
            data.get("policy_snapshot_id") != payload.get("policy_snapshot_id") or
            data.get("runtime_id") != payload.get("runtime_id") or
            stored_loaded != int(payload.get("loaded"))):
        raise ValueError("policy load receipt digest mismatch")
    return data


def create_policy_snapshot(
    conn: sqlite3.Connection,
    *,
    memory_snapshot_id: str,
    promoted_rules: list[str] | tuple[str, ...],
    promoted_assets: list[str] | tuple[str, ...] = (),
    retrieval_config: dict | None = None,
    routing_config: dict | None = None,
    commit: bool = True,
) -> PolicySnapshotReceipt:
    if not memory_snapshot_id:
        raise ValueError("memory_snapshot_id is required")
    content = {
        "memory_snapshot_id": memory_snapshot_id,
        "promoted_rules": sorted(str(item) for item in promoted_rules),
        "promoted_assets": sorted(str(item) for item in promoted_assets),
        "retrieval_config": retrieval_config or {},
        "routing_config": routing_config or {},
    }
    policy_digest = _policy_digest(content)
    snapshot_id = "policy_" + policy_digest.split(":", 1)[1][:20]
    had_outer_transaction = conn.in_transaction
    conn.execute(
        """INSERT OR IGNORE INTO tehm_policy_snapshots
           (policy_snapshot_id, memory_snapshot_id, promoted_rules_json,
            promoted_assets_json, retrieval_config_json, routing_config_json,
            policy_digest, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (snapshot_id, memory_snapshot_id,
         stable_dumps(content["promoted_rules"]),
         stable_dumps(content["promoted_assets"]),
         stable_dumps(content["retrieval_config"]),
         stable_dumps(content["routing_config"]), policy_digest,
         tehm_db.now_local()))
    stored = conn.execute(
        "SELECT * FROM tehm_policy_snapshots WHERE policy_snapshot_id=?",
        (snapshot_id,)).fetchone()
    if stored is None:
        raise ValueError("policy snapshot was not persisted")
    validate_policy_snapshot_row(stored)
    expected_fields = {
        "memory_snapshot_id": memory_snapshot_id,
        "promoted_rules_json": stable_dumps(content["promoted_rules"]),
        "promoted_assets_json": stable_dumps(content["promoted_assets"]),
        "retrieval_config_json": stable_dumps(content["retrieval_config"]),
        "routing_config_json": stable_dumps(content["routing_config"]),
        "policy_digest": policy_digest,
    }
    if any(stored[field] != value for field, value in expected_fields.items()):
        raise ValueError("policy snapshot is immutable and conflicts")
    if commit and not had_outer_transaction:
        conn.commit()
    return PolicySnapshotReceipt(snapshot_id, memory_snapshot_id, policy_digest)


def load_policy_snapshot(conn: sqlite3.Connection,
                         policy_snapshot_id: str) -> dict:
    row = conn.execute(
        "SELECT * FROM tehm_policy_snapshots WHERE policy_snapshot_id=?",
        (policy_snapshot_id,)).fetchone()
    if row is None:
        raise KeyError(f"unknown policy snapshot: {policy_snapshot_id}")
    return validate_policy_snapshot_row(row)


def record_policy_load(
    conn: sqlite3.Connection,
    *,
    policy_snapshot_id: str,
    runtime_id: str,
    loaded: bool = True,
    receipt: dict | None = None,
    commit: bool = True,
) -> PolicyLoadReceipt:
    """Record the runtime's actual policy-load result.

    A DB snapshot alone is insufficient for C3; callers must explicitly record
    the runtime identity and whether it accepted that exact policy digest.
    """
    snapshot = load_policy_snapshot(conn, policy_snapshot_id)
    if not runtime_id:
        raise ValueError("runtime_id is required")
    payload = {
        "policy_snapshot_id": policy_snapshot_id,
        "policy_digest": snapshot["policy_digest"],
        "runtime_id": runtime_id,
        "loaded": bool(loaded),
        "receipt": dict(receipt or {}),
    }
    receipt_digest = "sha256:" + hashlib.sha256(
        stable_dumps(payload).encode()).hexdigest()
    receipt_id = "policy_load_" + receipt_digest.split(":", 1)[1][:20]
    # Load rows are replayed in creation order.  The shared TEHM timestamp is
    # second-resolution, which can make two successive loads reorder by their
    # content-addressed IDs; use microseconds here so the latest execution
    # binding is deterministic even when a runtime reloads immediately.
    had_outer_transaction = conn.in_transaction
    created_at = datetime.datetime.now().astimezone().isoformat(
        timespec="microseconds")
    conn.execute(
        """INSERT OR IGNORE INTO tehm_policy_load_receipts
           (receipt_id, policy_snapshot_id, runtime_id, loaded,
            receipt_json, receipt_digest, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (receipt_id, policy_snapshot_id, runtime_id, int(bool(loaded)),
         stable_dumps(payload), receipt_digest, created_at))
    stored = conn.execute(
        "SELECT * FROM tehm_policy_load_receipts WHERE receipt_id=?",
        (receipt_id,)).fetchone()
    if stored is None:
        raise ValueError("policy load receipt was not persisted")
    validate_policy_load_row(stored)
    if commit and not had_outer_transaction:
        conn.commit()
    return PolicyLoadReceipt(receipt_id, policy_snapshot_id, runtime_id,
                             bool(loaded), receipt_digest)


__all__ = ["PolicyLoadReceipt", "PolicySnapshotReceipt", "create_policy_snapshot",
           "load_policy_snapshot", "record_policy_load",
           "validate_policy_load_row", "validate_policy_snapshot_row"]
