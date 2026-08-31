"""Additive schema for intervention-grounded Mechanism Knowledge (P3)."""
from __future__ import annotations

import sqlite3


KNOWLEDGE_SCHEMA_VERSION = "mechanism-knowledge-v0.1"

KNOWLEDGE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tehm_mechanism_knowledge (
    knowledge_id                TEXT NOT NULL,
    version                     INTEGER NOT NULL,
    mechanism_family            TEXT NOT NULL,
    compatibility_profile       TEXT,
    antecedent_json             TEXT NOT NULL,
    intervention_json           TEXT NOT NULL,
    mediated_effects_json       TEXT NOT NULL,
    expected_outcome_json       TEXT NOT NULL,
    positive_applicability_json TEXT NOT NULL,
    negative_applicability_json TEXT NOT NULL,
    obligations_json            TEXT NOT NULL,
    known_failure_modes_json    TEXT NOT NULL,
    causal_path_ids_json        TEXT NOT NULL,
    evidence_level              TEXT NOT NULL,
    support_lineages_json       TEXT NOT NULL,
    content_digest              TEXT NOT NULL UNIQUE,
    created_at                  TEXT NOT NULL,
    PRIMARY KEY (knowledge_id, version)
);
CREATE INDEX IF NOT EXISTS idx_mechanism_knowledge_family
    ON tehm_mechanism_knowledge(mechanism_family, compatibility_profile);

CREATE TABLE IF NOT EXISTS tehm_mechanism_knowledge_status (
    knowledge_id     TEXT NOT NULL,
    version          INTEGER NOT NULL,
    target_scope     TEXT NOT NULL,
    status           TEXT NOT NULL,
    status_version   INTEGER NOT NULL,
    provenance_json  TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    PRIMARY KEY (knowledge_id, version, target_scope)
);
CREATE INDEX IF NOT EXISTS idx_mechanism_knowledge_status_scope
    ON tehm_mechanism_knowledge_status(target_scope, status);

-- Explicit evidence references keep the claim/evidence boundary auditable;
-- the claim itself remains content-addressed and immutable.
CREATE TABLE IF NOT EXISTS tehm_mechanism_knowledge_evidence (
    knowledge_id    TEXT NOT NULL,
    version         INTEGER NOT NULL,
    evidence_type   TEXT NOT NULL,
    evidence_id     TEXT NOT NULL,
    split           TEXT NOT NULL CHECK (split IN
                    ('training', 'calibration', 'heldout', 'ab')),
    lineage_id      TEXT,
    evidence_level  TEXT NOT NULL,
    evidence_digest TEXT NOT NULL,
    PRIMARY KEY (knowledge_id, version, evidence_type, evidence_id)
);
CREATE INDEX IF NOT EXISTS idx_mechanism_knowledge_evidence
    ON tehm_mechanism_knowledge_evidence(knowledge_id, version, split);
"""


def ensure_knowledge_schema(conn: sqlite3.Connection, *, commit: bool = True) -> None:
    """Create P3 tables lazily without changing the frozen v4 meta version."""
    had_outer_transaction = conn.in_transaction
    for statement in (item.strip() for item in KNOWLEDGE_SCHEMA_SQL.split(";")
                      if item.strip()):
        conn.execute(statement)
    if commit and not had_outer_transaction:
        conn.commit()


__all__ = ["KNOWLEDGE_SCHEMA_SQL", "KNOWLEDGE_SCHEMA_VERSION",
           "ensure_knowledge_schema"]
