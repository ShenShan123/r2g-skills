"""TEHM schema migrations (design doc 19.2 note: independent migration).

``ensure_schema`` applies pending entries in order. Each migration is a tiny
idempotent unit; once shipped it is never edited. The schema.sql content always
describes the newest schema; this registry upgrades existing stores.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class Migration:
    """One forward-only schema migration.

    ``from_version``  : recorded schema version this applies on top of.
    ``to_version``    : version recorded after this migration succeeds.
    ``name``          : short human slug for the ledger.
    ``statements``    : raw SQL run in a single transaction.
    """

    from_version: str
    to_version: str
    name: str
    statements: str

    def apply(self, conn: sqlite3.Connection) -> None:
        if self.name == "physical_graph_context":
            # Some early v1 snapshots were created from a schema.sql that
            # already contained these columns despite recording tehm-v1.
            # Make the shipped migration genuinely idempotent instead of
            # failing on "duplicate column" during a portable freeze import.
            columns = {row[1] for row in conn.execute(
                "PRAGMA table_info(tehm_physical_effects)")}
            for name in ("graph_context_json", "graph_context_digest",
                         "graph_extractor_version"):
                if name not in columns:
                    conn.execute(
                        f"ALTER TABLE tehm_physical_effects ADD COLUMN {name} TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_physical_graph_context "
                "ON tehm_physical_effects(graph_context_digest)")
        elif self.name == "dataset_membership_firewall":
            # A few early migration tests/snapshots contain only ``tehm_meta``
            # plus the table being upgraded.  Create the firewall table there,
            # but backfill legacy transitions only when that table exists.
            conn.executescript(self.statements)
            has_transitions = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='tehm_transitions'").fetchone()
            if has_transitions:
                conn.execute(
                    """INSERT OR IGNORE INTO tehm_dataset_membership
                       (transition_id, campaign_id, split, learner_eligible,
                        frozen_snapshot_digest, assigned_at)
                       SELECT t.transition_id, 'live', 'training', 1, NULL,
                              'migration-v3'
                         FROM tehm_transitions t""")
        else:
            conn.executescript(self.statements)
        conn.commit()


# Registry, oldest first. Append new migrations; never re-order or edit shipped
# entries.
MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        from_version="tehm-v1",
        to_version="tehm-v2",
        name="physical_graph_context",
        statements="""
            ALTER TABLE tehm_physical_effects ADD COLUMN graph_context_json TEXT;
            ALTER TABLE tehm_physical_effects ADD COLUMN graph_context_digest TEXT;
            ALTER TABLE tehm_physical_effects ADD COLUMN graph_extractor_version TEXT;
            CREATE INDEX IF NOT EXISTS idx_physical_graph_context
                ON tehm_physical_effects(graph_context_digest);
        """,
    ),
    Migration(
        from_version="tehm-v2",
        to_version="tehm-v3",
        name="dataset_membership_firewall",
        statements="""
            CREATE TABLE IF NOT EXISTS tehm_dataset_membership (
                transition_id          TEXT NOT NULL,
                campaign_id            TEXT NOT NULL,
                split                  TEXT NOT NULL CHECK (split IN
                                      ('training', 'calibration', 'heldout', 'ab')),
                learner_eligible       INTEGER NOT NULL CHECK (learner_eligible IN (0, 1)),
                frozen_snapshot_digest TEXT,
                assigned_at            TEXT NOT NULL,
                PRIMARY KEY (transition_id, campaign_id)
            );
            CREATE INDEX IF NOT EXISTS idx_dataset_membership_campaign
                ON tehm_dataset_membership(campaign_id, learner_eligible, split);
        """,
    ),
    Migration(
        from_version="tehm-v3",
        to_version="tehm-v4",
        name="causal_online_assets_capability",
        statements="""
            CREATE TABLE IF NOT EXISTS tehm_causal_nodes (
                causal_node_id TEXT PRIMARY KEY, node_type TEXT NOT NULL,
                owner_type TEXT, owner_id TEXT, payload_json TEXT NOT NULL,
                payload_digest TEXT NOT NULL, extractor_version TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_causal_nodes_owner
                ON tehm_causal_nodes(owner_type, owner_id);
            CREATE INDEX IF NOT EXISTS idx_causal_nodes_type
                ON tehm_causal_nodes(node_type);
            CREATE TABLE IF NOT EXISTS tehm_causal_edges (
                causal_edge_id TEXT PRIMARY KEY, source_node_id TEXT NOT NULL,
                relation_type TEXT NOT NULL, target_node_id TEXT NOT NULL,
                evidence_level TEXT NOT NULL CHECK (evidence_level IN
                    ('L0_ASSOCIATION', 'L1_EXECUTED_INTERVENTION',
                     'L2_CONTROLLED_INTERVENTION', 'L3_REPLICATED_EFFECT',
                     'L4_TRANSFER_SUPPORTED_MECHANISM')),
                support_json TEXT NOT NULL, confidence_json TEXT NOT NULL,
                evidence_refs_json TEXT NOT NULL, campaign_id TEXT,
                learner_eligible INTEGER NOT NULL CHECK (learner_eligible IN (0, 1)),
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_causal_edges_source
                ON tehm_causal_edges(source_node_id, relation_type);
            CREATE INDEX IF NOT EXISTS idx_causal_edges_target
                ON tehm_causal_edges(target_node_id, relation_type);
            CREATE INDEX IF NOT EXISTS idx_causal_edges_level
                ON tehm_causal_edges(evidence_level, learner_eligible);
            CREATE TABLE IF NOT EXISTS tehm_causal_paths (
                path_id TEXT PRIMARY KEY, mechanism_family TEXT NOT NULL,
                compatibility_profile TEXT, ordered_nodes_json TEXT NOT NULL,
                ordered_edges_json TEXT NOT NULL, evidence_level TEXT NOT NULL
                    CHECK (evidence_level IN ('L0_ASSOCIATION',
                     'L1_EXECUTED_INTERVENTION', 'L2_CONTROLLED_INTERVENTION',
                     'L3_REPLICATED_EFFECT', 'L4_TRANSFER_SUPPORTED_MECHANISM')),
                support_json TEXT NOT NULL, source_transitions_json TEXT NOT NULL,
                path_digest TEXT NOT NULL, status TEXT NOT NULL CHECK (status IN
                    ('shadow', 'candidate', 'validated', 'retired')),
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_causal_paths_family
                ON tehm_causal_paths(mechanism_family, compatibility_profile, status);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_causal_paths_digest
                ON tehm_causal_paths(path_digest);
            CREATE TABLE IF NOT EXISTS tehm_intervention_pairs (
                pair_id TEXT PRIMARY KEY, control_transition_id TEXT NOT NULL,
                treatment_transition_id TEXT NOT NULL, target_scope TEXT NOT NULL,
                matched_context_digest TEXT, changed_action_digest TEXT NOT NULL,
                outcome_delta_json TEXT NOT NULL, oracle_equivalence_json TEXT NOT NULL,
                lineage_id TEXT, validity_status TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_intervention_pairs_lineage
                ON tehm_intervention_pairs(lineage_id, validity_status);
            CREATE TABLE IF NOT EXISTS tehm_memory_events (
                event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL,
                source_type TEXT NOT NULL, source_id TEXT NOT NULL, campaign_id TEXT,
                learner_eligible INTEGER NOT NULL CHECK (learner_eligible IN (0, 1)),
                payload_json TEXT NOT NULL, previous_event_digest TEXT,
                event_digest TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_memory_events_source
                ON tehm_memory_events(source_type, source_id);
            CREATE INDEX IF NOT EXISTS idx_memory_events_campaign
                ON tehm_memory_events(campaign_id, learner_eligible, event_type);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_events_digest
                ON tehm_memory_events(event_digest);
            CREATE TABLE IF NOT EXISTS tehm_rule_revisions (
                revision_id TEXT PRIMARY KEY, parent_rule_id TEXT,
                child_rule_id TEXT NOT NULL, operation TEXT NOT NULL CHECK (operation IN
                    ('MERGE', 'SPLIT', 'SPECIALIZE', 'GENERALIZE', 'REVISE')),
                trigger_event_id TEXT NOT NULL, evidence_refs_json TEXT NOT NULL,
                validation_json TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_rule_revisions_parent
                ON tehm_rule_revisions(parent_rule_id, operation);
            CREATE INDEX IF NOT EXISTS idx_rule_revisions_child
                ON tehm_rule_revisions(child_rule_id);
            CREATE TABLE IF NOT EXISTS tehm_assets (
                asset_id TEXT PRIMARY KEY, asset_type TEXT NOT NULL, name TEXT NOT NULL,
                version TEXT NOT NULL, definition_json TEXT NOT NULL,
                input_contract_json TEXT NOT NULL, output_contract_json TEXT NOT NULL,
                verifier_contract_json TEXT NOT NULL, compatibility_json TEXT NOT NULL,
                provenance_json TEXT NOT NULL, content_digest TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_assets_digest
                ON tehm_assets(content_digest);
            CREATE TABLE IF NOT EXISTS tehm_asset_status (
                asset_id TEXT NOT NULL, target_scope TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('draft', 'shadow', 'candidate',
                    'promoted', 'demoted', 'quarantined', 'retired')),
                status_version INTEGER NOT NULL, provenance_json TEXT NOT NULL,
                updated_at TEXT NOT NULL, PRIMARY KEY (asset_id, target_scope)
            );
            CREATE INDEX IF NOT EXISTS idx_asset_status_runtime
                ON tehm_asset_status(target_scope, status);
            CREATE TABLE IF NOT EXISTS tehm_capabilities (
                capability_id TEXT PRIMARY KEY, mechanism_family TEXT NOT NULL,
                applicability_json TEXT NOT NULL, required_rules_json TEXT NOT NULL,
                required_assets_json TEXT NOT NULL, obligations_json TEXT NOT NULL,
                budget_json TEXT NOT NULL, status TEXT NOT NULL CHECK (status IN
                    ('observed_gap', 'candidate', 'verified', 'promoted', 'regressed', 'retired')),
                version INTEGER NOT NULL, provenance_json TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_capabilities_family
                ON tehm_capabilities(mechanism_family, status);
            CREATE TABLE IF NOT EXISTS tehm_capability_evidence (
                capability_id TEXT NOT NULL, evidence_type TEXT NOT NULL,
                evidence_id TEXT NOT NULL, split TEXT NOT NULL CHECK (split IN
                    ('training', 'calibration', 'heldout', 'ab')),
                lineage_id TEXT, verdict TEXT NOT NULL, evidence_digest TEXT NOT NULL,
                PRIMARY KEY (capability_id, evidence_type, evidence_id)
            );
            CREATE INDEX IF NOT EXISTS idx_capability_evidence_split
                ON tehm_capability_evidence(capability_id, split, verdict);
            CREATE TABLE IF NOT EXISTS tehm_policy_snapshots (
                policy_snapshot_id TEXT PRIMARY KEY, memory_snapshot_id TEXT NOT NULL,
                promoted_rules_json TEXT NOT NULL, promoted_assets_json TEXT NOT NULL,
                retrieval_config_json TEXT NOT NULL, routing_config_json TEXT NOT NULL,
                policy_digest TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_policy_snapshots_digest
                ON tehm_policy_snapshots(policy_digest);
            CREATE TABLE IF NOT EXISTS tehm_policy_load_receipts (
                receipt_id TEXT PRIMARY KEY,
                policy_snapshot_id TEXT NOT NULL, runtime_id TEXT NOT NULL,
                loaded INTEGER NOT NULL CHECK (loaded IN (0, 1)),
                receipt_json TEXT NOT NULL, receipt_digest TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_policy_load_receipts_policy
                ON tehm_policy_load_receipts(policy_snapshot_id, runtime_id, loaded);
        """,
    ),
)


def pending_migrations(recorded_version: str) -> tuple[Migration, ...]:
    """Return the complete forward chain from ``recorded_version``.

    ``_none`` means a fresh DB (schema.sql just ran at the current version), so
    no migration is pending.
    """
    if recorded_version == "_none":
        return ()
    pending: list[Migration] = []
    current = recorded_version
    seen: set[str] = set()
    by_from = {migration.from_version: migration for migration in MIGRATIONS}
    while current in by_from:
        if current in seen:
            raise RuntimeError(f"cyclic TEHM migration chain at {current}")
        seen.add(current)
        migration = by_from[current]
        pending.append(migration)
        current = migration.to_version
    return tuple(pending)
