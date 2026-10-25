"""View base contract (design doc 19.3).

A ``ViewRecord`` is the materialized unit in ``tehm_views``:
    owner_type  state | transition | episode | rule | activation
    view_type   semantic | diagnostic | episodic | procedural | parametric
    schema_version / extractor_version  both stamped and version-locked
    payload     the typed view payload (deterministic)
    source_refs canonical owner references for provenance (H2)

The payload digest is content-addressed: identical input yields the identical
digest, so re-materialization is an idempotent exact replay; conflicting content
for the same materialization key is rejected.
"""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, field

from tehm.ids import stable_dumps

OWNER_TYPES = ("state", "transition", "episode", "rule", "activation")
VIEW_TYPES = ("semantic", "diagnostic", "episodic", "procedural", "parametric")


@dataclass(frozen=True)
class ViewRecord:
    owner_type: str
    owner_id: str
    view_type: str
    schema_version: str
    extractor_version: str
    payload: dict
    source_refs: list = field(default_factory=list)
    materialized_at: str = ""

    def validate(self) -> None:
        if self.owner_type not in OWNER_TYPES:
            raise ValueError(f"owner_type must be one of {OWNER_TYPES}, got {self.owner_type!r}")
        if not self.owner_id:
            raise ValueError("owner_id is required")
        if self.view_type not in VIEW_TYPES:
            raise ValueError(f"view_type must be one of {VIEW_TYPES}, got {self.view_type!r}")
        if not self.schema_version:
            raise ValueError("schema_version is required")
        if not self.extractor_version:
            raise ValueError("extractor_version is required (H2 provenance)")
        if not isinstance(self.payload, dict):
            raise ValueError("view payload must be a dict")
        if not isinstance(self.source_refs, list):
            raise ValueError("view source_refs must be a list")

    def payload_digest(self) -> str:
        return payload_digest(self.schema_version, self.extractor_version, self.payload)

    def to_row(self) -> dict:
        return {
            "owner_type": self.owner_type,
            "owner_id": self.owner_id,
            "view_type": self.view_type,
            "schema_version": self.schema_version,
            "extractor_version": self.extractor_version,
            "payload_json": stable_dumps(self.payload),
            "payload_digest": self.payload_digest(),
            "source_refs_json": stable_dumps(self.source_refs),
            "materialized_at": self.materialized_at,
        }


def payload_digest(schema_version: str, extractor_version: str, payload: dict) -> str:
    content = stable_dumps({
        "schema_version": schema_version,
        "extractor_version": extractor_version,
        "payload": payload,
    })
    return f"view_{hashlib.sha1(content.encode()).hexdigest()[:16]}"


def upsert_view(conn: sqlite3.Connection, record: ViewRecord, *, commit: bool = True) -> None:
    """Insert one materialized view or accept an exact replay.

    ``commit=False`` lets a caller include several view writes in one enclosing
    savepoint (for example canonical capture).  Existing direct callers keep
    the historical commit-on-success behavior.  The materialization timestamp
    is cosmetic; all content/provenance fields are immutable for a fixed
    owner/schema/extractor key.  A conflict is rejected instead of silently
    replacing a potentially corrupted view.
    """
    record.validate()
    row = record.to_row()
    identity = (row["owner_type"], row["owner_id"], row["view_type"],
                row["schema_version"], row["extractor_version"])
    existing = conn.execute(
        "SELECT payload_json, payload_digest, source_refs_json "
        "FROM tehm_views WHERE owner_type=? AND owner_id=? AND view_type=? "
        "AND schema_version=? AND extractor_version=?", identity).fetchone()
    if existing is not None:
        fields = ("payload_json", "payload_digest", "source_refs_json")
        if any(existing[field] != row[field] for field in fields):
            raise ValueError(
                "view replay conflicts with immutable materialization "
                f"{row['owner_type']}:{row['owner_id']}:{row['view_type']}"
            )
    else:
        conn.execute(
            """
            INSERT INTO tehm_views (
                owner_type, owner_id, view_type, schema_version, extractor_version,
                payload_json, payload_digest, source_refs_json, materialized_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (row["owner_type"], row["owner_id"], row["view_type"], row["schema_version"],
             row["extractor_version"], row["payload_json"], row["payload_digest"],
             row["source_refs_json"], row["materialized_at"]),
        )
    if commit:
        conn.commit()
