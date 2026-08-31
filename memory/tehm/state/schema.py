"""Additive schema for current-valid-state resolution.

P1 deliberately keeps the canonical database version at ``tehm-v4``.  State
relations and resolution snapshots are derived, shadow-only tables that can be
created on an existing v4 store without rewriting the frozen v4 migration
history.  A later v5 migration may move these statements into the forward
migration chain; the table contract is kept here in the meantime so every
writer and resolver uses one definition.
"""
from __future__ import annotations

import sqlite3


STATE_SCHEMA_VERSION = "state-resolution-v0.1"

STATE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tehm_memory_relations (
    relation_id       TEXT PRIMARY KEY,
    source_type       TEXT NOT NULL,
    source_id         TEXT NOT NULL,
    relation_type     TEXT NOT NULL,
    target_type       TEXT NOT NULL,
    target_id         TEXT NOT NULL,
    scope_json        TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    authority_ref     TEXT,
    relation_digest    TEXT NOT NULL UNIQUE,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_relations_source
    ON tehm_memory_relations(source_type, source_id, relation_type);
CREATE INDEX IF NOT EXISTS idx_memory_relations_target
    ON tehm_memory_relations(target_type, target_id, relation_type);
CREATE INDEX IF NOT EXISTS idx_memory_relations_scope
    ON tehm_memory_relations(relation_type, scope_json);

CREATE TABLE IF NOT EXISTS tehm_state_resolution_snapshots (
    resolution_id            TEXT PRIMARY KEY,
    input_memory_digest      TEXT NOT NULL,
    scope_json               TEXT NOT NULL,
    active_rules_json        TEXT NOT NULL,
    active_paths_json        TEXT NOT NULL,
    active_knowledge_json    TEXT NOT NULL,
    active_assets_json       TEXT NOT NULL,
    active_capabilities_json TEXT NOT NULL,
    suppressed_json          TEXT NOT NULL,
    unresolved_conflicts_json TEXT NOT NULL DEFAULT '[]',
    relation_ids_json        TEXT NOT NULL DEFAULT '[]',
    shadow_relation_ids_json TEXT NOT NULL DEFAULT '[]',
    resolution_digest        TEXT NOT NULL UNIQUE,
    resolver_version         TEXT NOT NULL,
    created_at               TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_state_resolution_input
    ON tehm_state_resolution_snapshots(input_memory_digest);
"""


def ensure_state_schema(conn: sqlite3.Connection, *, commit: bool = True) -> None:
    """Create the additive state tables, preserving any outer transaction."""
    had_outer_transaction = conn.in_transaction
    # ``Connection.executescript`` commits an open transaction before running
    # the script.  Use individual DDL statements so a relation write nested in
    # canonical capture remains atomic with its caller's transaction.
    for statement in (item.strip() for item in STATE_SCHEMA_SQL.split(";")
                      if item.strip()):
        conn.execute(statement)

    # A short-lived development snapshot may have the original six-column
    # snapshot table.  Add only the columns introduced by this implementation;
    # never drop or rewrite existing derived rows.
    columns = {
        str(row[1]) for row in conn.execute(
            "PRAGMA table_info(tehm_state_resolution_snapshots)")
    }
    for name, definition in (
        ("unresolved_conflicts_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("relation_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("shadow_relation_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
    ):
        if name not in columns:
            conn.execute(
                f"ALTER TABLE tehm_state_resolution_snapshots "
                f"ADD COLUMN {name} {definition}")
    if commit and not had_outer_transaction:
        conn.commit()


__all__ = ["STATE_SCHEMA_VERSION", "STATE_SCHEMA_SQL", "ensure_state_schema"]
