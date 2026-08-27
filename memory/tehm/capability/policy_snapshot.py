"""Content-addressed policy snapshots used for capability attribution."""
from __future__ import annotations

import hashlib
import sqlite3
import datetime
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


def create_policy_snapshot(
    conn: sqlite3.Connection,
    *,
    memory_snapshot_id: str,
    promoted_rules: list[str] | tuple[str, ...],
    promoted_assets: list[str] | tuple[str, ...] = (),
    retrieval_config: dict | None = None,
    routing_config: dict | None = None,
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
    policy_digest = "sha256:" + hashlib.sha256(
        stable_dumps(content).encode()).hexdigest()
    snapshot_id = "policy_" + policy_digest.split(":", 1)[1][:20]
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
    conn.commit()
    return PolicySnapshotReceipt(snapshot_id, memory_snapshot_id, policy_digest)


def load_policy_snapshot(conn: sqlite3.Connection,
                         policy_snapshot_id: str) -> dict:
    row = conn.execute(
        "SELECT * FROM tehm_policy_snapshots WHERE policy_snapshot_id=?",
        (policy_snapshot_id,)).fetchone()
    if row is None:
        raise KeyError(f"unknown policy snapshot: {policy_snapshot_id}")
    return dict(row)


def record_policy_load(
    conn: sqlite3.Connection,
    *,
    policy_snapshot_id: str,
    runtime_id: str,
    loaded: bool = True,
    receipt: dict | None = None,
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
    created_at = datetime.datetime.now().astimezone().isoformat(
        timespec="microseconds")
    conn.execute(
        """INSERT OR IGNORE INTO tehm_policy_load_receipts
           (receipt_id, policy_snapshot_id, runtime_id, loaded,
            receipt_json, receipt_digest, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (receipt_id, policy_snapshot_id, runtime_id, int(bool(loaded)),
         stable_dumps(payload), receipt_digest, created_at))
    conn.commit()
    return PolicyLoadReceipt(receipt_id, policy_snapshot_id, runtime_id,
                             bool(loaded), receipt_digest)


__all__ = ["PolicyLoadReceipt", "PolicySnapshotReceipt", "create_policy_snapshot",
           "load_policy_snapshot", "record_policy_load"]
