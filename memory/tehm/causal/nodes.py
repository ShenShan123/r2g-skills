"""Content-addressed causal shadow nodes."""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, field

from tehm import db as tehm_db
from tehm.ids import stable_dumps

from .schema import validate_node_type

EXTRACTOR_VERSION = "causal-node-extractor-v0.1"


def _digest(payload: object) -> str:
    return hashlib.sha1(stable_dumps(payload).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class CausalNode:
    node_type: str
    payload: dict = field(default_factory=dict)
    owner_type: str | None = None
    owner_id: str | None = None
    extractor_version: str = EXTRACTOR_VERSION

    def __post_init__(self) -> None:
        validate_node_type(self.node_type)
        if not isinstance(self.payload, dict):
            raise ValueError("causal node payload must be a dict")

    @property
    def payload_digest(self) -> str:
        return f"sha1:{_digest(self.payload)}"

    @property
    def causal_node_id(self) -> str:
        return "causal_node_" + _digest({
            "node_type": self.node_type,
            "owner_type": self.owner_type,
            "owner_id": self.owner_id,
            "payload": self.payload,
            "extractor_version": self.extractor_version,
        })

    def to_row(self, *, created_at: str | None = None) -> dict:
        return {
            "causal_node_id": self.causal_node_id,
            "node_type": self.node_type,
            "owner_type": self.owner_type,
            "owner_id": self.owner_id,
            "payload_json": stable_dumps(self.payload),
            "payload_digest": self.payload_digest,
            "extractor_version": self.extractor_version,
            "created_at": created_at or tehm_db.now_local(),
        }


def persist_node(conn: sqlite3.Connection, node: CausalNode,
                 *, created_at: str | None = None) -> str:
    """Insert an immutable node or accept an exact replay.

    ``INSERT OR IGNORE`` is unsafe for a content-addressed shadow object: a
    direct SQL edit could leave the old ID pointing at different payload and
    the next extraction would silently continue.  Created-at is cosmetic and
    is deliberately excluded from the replay comparison.
    """
    expected = node.to_row(created_at=created_at)
    existing = conn.execute(
        "SELECT node_type, owner_type, owner_id, payload_json, payload_digest, "
        "extractor_version FROM tehm_causal_nodes WHERE causal_node_id=?",
        (expected["causal_node_id"],)).fetchone()
    if existing is not None:
        fields = ("node_type", "owner_type", "owner_id", "payload_json",
                  "payload_digest", "extractor_version")
        if any(existing[field] != expected[field] for field in fields):
            raise ValueError(
                f"causal node replay conflicts with immutable node "
                f"{expected['causal_node_id']}")
        return expected["causal_node_id"]
    conn.execute(
        """INSERT INTO tehm_causal_nodes
           (causal_node_id, node_type, owner_type, owner_id, payload_json,
            payload_digest, extractor_version, created_at)
           VALUES (:causal_node_id, :node_type, :owner_type, :owner_id,
                   :payload_json, :payload_digest, :extractor_version, :created_at)""",
        expected)
    return expected["causal_node_id"]


__all__ = ["CausalNode", "EXTRACTOR_VERSION", "persist_node"]
