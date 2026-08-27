-- TEHM canonical store schema (design doc sections 19.2 - 19.7, 20.11).
-- One SQLite file holds ONLY TEHM authority. Legacy knowledge lives in the
-- isolated signoff-loop/knowledge/ tree and is never opened here (honesty H5).
--
-- Version: tehm-v4 (db.DB_SCHEMA_VERSION = 4). The rule/asset authority
-- ledgers are additive v4 extensions and are also created lazily for already
-- frozen v4 stores. Bump migrations.py + version only for a forward schema
-- migration; never edit shipped rows in place.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS tehm_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- =====================================================================
-- Canonical substrate: verified states, transitions, episodes (19.2)
-- =====================================================================
CREATE TABLE IF NOT EXISTS tehm_states (
    state_id                TEXT PRIMARY KEY,
    domain                  TEXT NOT NULL,
    project_id              TEXT,
    design_id               TEXT,
    lineage_id              TEXT,
    repository_ref          TEXT,
    source_digest           TEXT,
    context_graph_digest    TEXT,
    verifier_snapshot_json  TEXT,
    artifact_manifest_json  TEXT,
    created_at              TEXT NOT NULL,
    schema_version          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_states_lineage ON tehm_states(domain, lineage_id);

CREATE TABLE IF NOT EXISTS tehm_transitions (
    transition_id           TEXT PRIMARY KEY,
    source_state_id         TEXT NOT NULL,
    target_state_id         TEXT NOT NULL,
    action_domain           TEXT NOT NULL,
    action_json             TEXT NOT NULL,
    observation_delta_json  TEXT NOT NULL,
    verifier_json           TEXT NOT NULL,
    primary_effect_key      TEXT,
    outcome                 TEXT NOT NULL,
    created_regressions_json TEXT,
    newly_observed_json     TEXT,
    provenance_json         TEXT NOT NULL,
    schema_version          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_transitions_src ON tehm_transitions(source_state_id);
CREATE INDEX IF NOT EXISTS idx_transitions_dst ON tehm_transitions(target_state_id);
CREATE INDEX IF NOT EXISTS idx_transitions_outcome ON tehm_transitions(outcome);

CREATE TABLE IF NOT EXISTS tehm_episodes (
    episode_id              TEXT PRIMARY KEY,
    domain                  TEXT NOT NULL,
    initial_state_id        TEXT NOT NULL,
    terminal_state_id       TEXT,
    terminal_status         TEXT,
    mechanism_family        TEXT,
    lineage_id              TEXT,
    trajectory_summary_json TEXT,
    provenance_json         TEXT NOT NULL,
    schema_version          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_episodes_lineage ON tehm_episodes(lineage_id, mechanism_family);

CREATE TABLE IF NOT EXISTS tehm_episode_steps (
    episode_id     TEXT NOT NULL,
    step_index     INTEGER NOT NULL,
    transition_id  TEXT NOT NULL,
    branch_id      TEXT DEFAULT 'main',
    PRIMARY KEY (episode_id, branch_id, step_index)
);
CREATE INDEX IF NOT EXISTS idx_steps_transition ON tehm_episode_steps(transition_id);

-- Dataset membership is an explicit data-plane firewall.  Canonical evidence
-- may be retained for audit, but only rows marked learner_eligible for the
-- requested campaign can contribute to crystallization.
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

-- =====================================================================
-- Typed views: five views are first-class materialized objects (19.3)
-- =====================================================================
CREATE TABLE IF NOT EXISTS tehm_views (
    owner_type         TEXT NOT NULL,   -- state | transition | episode | rule | activation
    owner_id           TEXT NOT NULL,
    view_type          TEXT NOT NULL,   -- semantic | diagnostic | episodic | procedural | parametric
    schema_version     TEXT NOT NULL,
    extractor_version  TEXT NOT NULL,
    payload_json       TEXT NOT NULL,
    payload_digest     TEXT NOT NULL,
    source_refs_json   TEXT,
    materialized_at    TEXT NOT NULL,
    PRIMARY KEY (owner_type, owner_id, view_type, schema_version, extractor_version)
);
CREATE INDEX IF NOT EXISTS idx_views_owner ON tehm_views(owner_type, owner_id);

-- =====================================================================
-- Crystallized procedural rules + provenance (19.4)
-- =====================================================================
CREATE TABLE IF NOT EXISTS tehm_rules (
    rule_id                   TEXT PRIMARY KEY,
    domain                    TEXT NOT NULL,
    before_pattern_json       TEXT NOT NULL,
    after_pattern_json        TEXT NOT NULL,
    hard_preconditions_json   TEXT NOT NULL,
    context_profile_json      TEXT NOT NULL,
    obligations_json          TEXT NOT NULL,
    validity_status           TEXT NOT NULL,
    validity_profile_json     TEXT NOT NULL,
    confidence_json           TEXT NOT NULL,
    utility_json              TEXT NOT NULL,
    risk_profile_json         TEXT NOT NULL,
    predicate_schema_version  TEXT NOT NULL,
    role_schema_version       TEXT NOT NULL,
    crystallizer_version      TEXT NOT NULL,
    merge_trace_digest        TEXT NOT NULL,
    created_at                TEXT NOT NULL,
    updated_at                TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tehm_rule_sources (
    rule_id                   TEXT NOT NULL,
    episode_id                TEXT NOT NULL,
    source_substitution_json  TEXT NOT NULL,
    evidence_profile_json     TEXT NOT NULL,
    lineage_id                TEXT,
    PRIMARY KEY (rule_id, episode_id)
);

-- =====================================================================
-- Runtime authority: activations + rule lifecycle (19.5, 19.6)
-- =====================================================================
CREATE TABLE IF NOT EXISTS tehm_activations (
    activation_id              TEXT PRIMARY KEY,
    rule_id                    TEXT NOT NULL,
    target_state_id            TEXT NOT NULL,
    query_plan_json            TEXT,
    retrieval_receipt_json     TEXT NOT NULL,
    applicability_status       TEXT NOT NULL,
    predicate_snapshot_id      TEXT,
    binding_status             TEXT,
    binding_json               TEXT,
    executability_status       TEXT,
    obligation_transfer_json   TEXT,
    obligation_coverage        REAL,
    verification_status        TEXT,
    verifier_json              TEXT,
    outcome                    TEXT,
    created_regressions_json   TEXT,
    produced_transition_id     TEXT,
    rollback_receipt_json      TEXT,
    trial_uuid                 TEXT,
    created_at                 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activations_rule ON tehm_activations(rule_id, target_state_id);

-- Rule lifecycle is independent of the legacy recipe lifecycle (19.6).
CREATE TABLE IF NOT EXISTS tehm_rule_status (
    rule_id          TEXT NOT NULL,
    target_scope     TEXT NOT NULL,
    status           TEXT NOT NULL,   -- shadow | candidate | promoted | demoted | quarantined | retired
    status_version   INTEGER NOT NULL,
    provenance_json  TEXT,
    updated_at       TEXT NOT NULL,
    PRIMARY KEY (rule_id, target_scope)
);

-- Independent rule-promotion authority ledger.  This is an additive v4
-- extension; older v4 stores create it lazily when the authority seam is
-- first used.  Evidence and receipt rows are immutable and content-bound.
CREATE TABLE IF NOT EXISTS tehm_rule_authority_evidence (
    rule_id         TEXT NOT NULL,
    target_scope    TEXT NOT NULL,
    gate_name       TEXT NOT NULL,
    evidence_id     TEXT NOT NULL,
    split           TEXT NOT NULL CHECK (split IN
                    ('training', 'calibration', 'heldout', 'ab')),
    lineage_id      TEXT,
    verdict         TEXT NOT NULL,
    payload_json    TEXT NOT NULL,
    evidence_digest TEXT NOT NULL,
    PRIMARY KEY (rule_id, target_scope, gate_name, evidence_id)
);
CREATE INDEX IF NOT EXISTS idx_rule_authority_evidence_scope
    ON tehm_rule_authority_evidence
       (rule_id, target_scope, gate_name, split, verdict);

CREATE TABLE IF NOT EXISTS tehm_rule_authority_receipts (
    authority_receipt_id TEXT PRIMARY KEY,
    rule_id             TEXT NOT NULL,
    target_scope        TEXT NOT NULL,
    status_version      INTEGER,
    eligible            INTEGER NOT NULL CHECK (eligible IN (0, 1)),
    receipt_json        TEXT NOT NULL,
    receipt_digest      TEXT NOT NULL UNIQUE,
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rule_authority_receipts_scope
    ON tehm_rule_authority_receipts(rule_id, target_scope, eligible);

-- =====================================================================
-- Experience graph edges (19.7)
-- =====================================================================
CREATE TABLE IF NOT EXISTS tehm_edges (
    source_id      TEXT NOT NULL,
    relation_type  TEXT NOT NULL,   -- EXECUTED_FROM | PRODUCED_STATE | ... (13 relations)
    target_id      TEXT NOT NULL,
    metadata_json  TEXT,
    PRIMARY KEY (source_id, relation_type, target_id)
);
CREATE INDEX IF NOT EXISTS idx_edges_target ON tehm_edges(target_id, relation_type);

-- =====================================================================
-- TEHM A/B trials (20.11) — independent of legacy ab_trials
-- =====================================================================
CREATE TABLE IF NOT EXISTS tehm_trials (
    trial_id        TEXT PRIMARY KEY,
    rule_id         TEXT NOT NULL,
    target_scope    TEXT NOT NULL,
    arm_a_run_id    TEXT,
    arm_b_run_id    TEXT,
    verdict         TEXT,
    metrics_json    TEXT,
    match_level     TEXT,
    trial_uuid      TEXT UNIQUE,
    status_version  INTEGER,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trials_rule ON tehm_trials(rule_id, target_scope);

-- =====================================================================
-- Cross-stage Physical Effect Memory (design doc 26 Phase 11).
-- Records the PHYSICAL effect (delta PPA) of a flow/RTL action so the
-- flow stage can predict what a repair action does downstream. First phase
-- records empirical deltas + aggregates per action; it does NOT claim a
-- differentiable gradient (design doc Phase 11).
-- =====================================================================
CREATE TABLE IF NOT EXISTS tehm_physical_effects (
    transition_id          TEXT PRIMARY KEY,
    action_domain          TEXT,
    transformation_family  TEXT,
    effect_key             TEXT,
    domain                 TEXT,
    before_ppa_json        TEXT,
    after_ppa_json         TEXT,
    deltas_json            TEXT NOT NULL,
    evidence_refs_json     TEXT,
    graph_context_json     TEXT,
    graph_context_digest   TEXT,
    graph_extractor_version TEXT,
    created_at             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_physical_family
    ON tehm_physical_effects(transformation_family, effect_key);
CREATE INDEX IF NOT EXISTS idx_physical_graph_context
    ON tehm_physical_effects(graph_context_digest);

-- =====================================================================
-- Causal shadow graph (upgrade plan sections 3 and 7)
-- =====================================================================
CREATE TABLE IF NOT EXISTS tehm_causal_nodes (
    causal_node_id       TEXT PRIMARY KEY,
    node_type            TEXT NOT NULL,
    owner_type           TEXT,
    owner_id             TEXT,
    payload_json         TEXT NOT NULL,
    payload_digest       TEXT NOT NULL,
    extractor_version    TEXT NOT NULL,
    created_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_causal_nodes_owner
    ON tehm_causal_nodes(owner_type, owner_id);
CREATE INDEX IF NOT EXISTS idx_causal_nodes_type
    ON tehm_causal_nodes(node_type);

CREATE TABLE IF NOT EXISTS tehm_causal_edges (
    causal_edge_id       TEXT PRIMARY KEY,
    source_node_id       TEXT NOT NULL,
    relation_type        TEXT NOT NULL,
    target_node_id       TEXT NOT NULL,
    evidence_level       TEXT NOT NULL CHECK (evidence_level IN
                             ('L0_ASSOCIATION', 'L1_EXECUTED_INTERVENTION',
                              'L2_CONTROLLED_INTERVENTION',
                              'L3_REPLICATED_EFFECT',
                              'L4_TRANSFER_SUPPORTED_MECHANISM')),
    support_json         TEXT NOT NULL,
    confidence_json      TEXT NOT NULL,
    evidence_refs_json   TEXT NOT NULL,
    campaign_id          TEXT,
    learner_eligible     INTEGER NOT NULL CHECK (learner_eligible IN (0, 1)),
    created_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_causal_edges_source
    ON tehm_causal_edges(source_node_id, relation_type);
CREATE INDEX IF NOT EXISTS idx_causal_edges_target
    ON tehm_causal_edges(target_node_id, relation_type);
CREATE INDEX IF NOT EXISTS idx_causal_edges_level
    ON tehm_causal_edges(evidence_level, learner_eligible);

CREATE TABLE IF NOT EXISTS tehm_causal_paths (
    path_id                TEXT PRIMARY KEY,
    mechanism_family       TEXT NOT NULL,
    compatibility_profile  TEXT,
    ordered_nodes_json     TEXT NOT NULL,
    ordered_edges_json     TEXT NOT NULL,
    evidence_level         TEXT NOT NULL CHECK (evidence_level IN
                              ('L0_ASSOCIATION', 'L1_EXECUTED_INTERVENTION',
                               'L2_CONTROLLED_INTERVENTION',
                               'L3_REPLICATED_EFFECT',
                               'L4_TRANSFER_SUPPORTED_MECHANISM')),
    support_json           TEXT NOT NULL,
    source_transitions_json TEXT NOT NULL,
    path_digest            TEXT NOT NULL,
    status                 TEXT NOT NULL CHECK (status IN
                              ('shadow', 'candidate', 'validated', 'retired')),
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_causal_paths_family
    ON tehm_causal_paths(mechanism_family, compatibility_profile, status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_causal_paths_digest
    ON tehm_causal_paths(path_digest);

CREATE TABLE IF NOT EXISTS tehm_intervention_pairs (
    pair_id                 TEXT PRIMARY KEY,
    control_transition_id   TEXT NOT NULL,
    treatment_transition_id TEXT NOT NULL,
    target_scope            TEXT NOT NULL,
    matched_context_digest  TEXT,
    changed_action_digest   TEXT NOT NULL,
    outcome_delta_json      TEXT NOT NULL,
    oracle_equivalence_json TEXT NOT NULL,
    lineage_id              TEXT,
    validity_status         TEXT NOT NULL,
    created_at              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_intervention_pairs_lineage
    ON tehm_intervention_pairs(lineage_id, validity_status);

-- =====================================================================
-- Online gated evolution (upgrade plan section 4)
-- =====================================================================
CREATE TABLE IF NOT EXISTS tehm_memory_events (
    event_id              TEXT PRIMARY KEY,
    event_type            TEXT NOT NULL,
    source_type           TEXT NOT NULL,
    source_id             TEXT NOT NULL,
    campaign_id           TEXT,
    learner_eligible      INTEGER NOT NULL CHECK (learner_eligible IN (0, 1)),
    payload_json          TEXT NOT NULL,
    previous_event_digest TEXT,
    event_digest          TEXT NOT NULL,
    created_at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_events_source
    ON tehm_memory_events(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_memory_events_campaign
    ON tehm_memory_events(campaign_id, learner_eligible, event_type);
CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_events_digest
    ON tehm_memory_events(event_digest);

CREATE TABLE IF NOT EXISTS tehm_rule_revisions (
    revision_id       TEXT PRIMARY KEY,
    parent_rule_id    TEXT,
    child_rule_id     TEXT NOT NULL,
    operation         TEXT NOT NULL CHECK (operation IN
                      ('MERGE', 'SPLIT', 'SPECIALIZE', 'GENERALIZE', 'REVISE')),
    trigger_event_id  TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    validation_json   TEXT NOT NULL,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rule_revisions_parent
    ON tehm_rule_revisions(parent_rule_id, operation);
CREATE INDEX IF NOT EXISTS idx_rule_revisions_child
    ON tehm_rule_revisions(child_rule_id);

-- =====================================================================
-- Asset memory (upgrade plan section 5)
-- =====================================================================
CREATE TABLE IF NOT EXISTS tehm_assets (
    asset_id               TEXT PRIMARY KEY,
    asset_type             TEXT NOT NULL,
    name                   TEXT NOT NULL,
    version                TEXT NOT NULL,
    definition_json        TEXT NOT NULL,
    input_contract_json    TEXT NOT NULL,
    output_contract_json   TEXT NOT NULL,
    verifier_contract_json TEXT NOT NULL,
    compatibility_json     TEXT NOT NULL,
    provenance_json        TEXT NOT NULL,
    content_digest         TEXT NOT NULL,
    created_at             TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_assets_digest
    ON tehm_assets(content_digest);

CREATE TABLE IF NOT EXISTS tehm_asset_status (
    asset_id        TEXT NOT NULL,
    target_scope    TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN
                    ('draft', 'shadow', 'candidate', 'promoted', 'demoted',
                     'quarantined', 'retired')),
    status_version  INTEGER NOT NULL,
    provenance_json TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (asset_id, target_scope)
);
CREATE INDEX IF NOT EXISTS idx_asset_status_runtime
    ON tehm_asset_status(target_scope, status);

-- Asset authority evidence ledger.  This is an additive v4 extension: the
-- authority seam lazily creates the same tables for already-frozen v4 stores,
-- so the shipped v4 migration chain remains byte-compatible.
CREATE TABLE IF NOT EXISTS tehm_asset_authority_evidence (
    asset_id        TEXT NOT NULL,
    target_scope    TEXT NOT NULL,
    evidence_type   TEXT NOT NULL,
    evidence_id     TEXT NOT NULL,
    split           TEXT NOT NULL CHECK (split IN
                    ('training', 'calibration', 'heldout', 'ab')),
    lineage_id      TEXT,
    verdict         TEXT NOT NULL,
    payload_json    TEXT NOT NULL,
    evidence_digest TEXT NOT NULL,
    PRIMARY KEY (asset_id, target_scope, evidence_type, evidence_id)
);
CREATE INDEX IF NOT EXISTS idx_asset_authority_evidence_scope
    ON tehm_asset_authority_evidence(asset_id, target_scope, split, verdict);

CREATE TABLE IF NOT EXISTS tehm_asset_authority_receipts (
    authority_receipt_id TEXT PRIMARY KEY,
    asset_id             TEXT NOT NULL,
    target_scope         TEXT NOT NULL,
    eligible             INTEGER NOT NULL CHECK (eligible IN (0, 1)),
    receipt_json         TEXT NOT NULL,
    receipt_digest       TEXT NOT NULL UNIQUE,
    created_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_asset_authority_receipts_scope
    ON tehm_asset_authority_receipts(asset_id, target_scope, eligible);

-- =====================================================================
-- Capability registry and attributable policy snapshots (section 6)
-- =====================================================================
CREATE TABLE IF NOT EXISTS tehm_capabilities (
    capability_id        TEXT PRIMARY KEY,
    mechanism_family     TEXT NOT NULL,
    applicability_json   TEXT NOT NULL,
    required_rules_json  TEXT NOT NULL,
    required_assets_json TEXT NOT NULL,
    obligations_json     TEXT NOT NULL,
    budget_json          TEXT NOT NULL,
    status               TEXT NOT NULL CHECK (status IN
                          ('observed_gap', 'candidate', 'verified', 'promoted',
                           'regressed', 'retired')),
    version              INTEGER NOT NULL,
    provenance_json      TEXT NOT NULL,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_capabilities_family
    ON tehm_capabilities(mechanism_family, status);

CREATE TABLE IF NOT EXISTS tehm_capability_evidence (
    capability_id   TEXT NOT NULL,
    evidence_type   TEXT NOT NULL,
    evidence_id     TEXT NOT NULL,
    split           TEXT NOT NULL CHECK (split IN
                    ('training', 'calibration', 'heldout', 'ab')),
    lineage_id      TEXT,
    verdict         TEXT NOT NULL,
    evidence_digest TEXT NOT NULL,
    PRIMARY KEY (capability_id, evidence_type, evidence_id)
);
CREATE INDEX IF NOT EXISTS idx_capability_evidence_split
    ON tehm_capability_evidence(capability_id, split, verdict);

CREATE TABLE IF NOT EXISTS tehm_policy_snapshots (
    policy_snapshot_id    TEXT PRIMARY KEY,
    memory_snapshot_id    TEXT NOT NULL,
    promoted_rules_json   TEXT NOT NULL,
    promoted_assets_json  TEXT NOT NULL,
    retrieval_config_json TEXT NOT NULL,
    routing_config_json   TEXT NOT NULL,
    policy_digest         TEXT NOT NULL,
    created_at            TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_policy_snapshots_digest
    ON tehm_policy_snapshots(policy_digest);

CREATE TABLE IF NOT EXISTS tehm_policy_load_receipts (
    receipt_id          TEXT PRIMARY KEY,
    policy_snapshot_id  TEXT NOT NULL,
    runtime_id          TEXT NOT NULL,
    loaded              INTEGER NOT NULL CHECK (loaded IN (0, 1)),
    receipt_json        TEXT NOT NULL,
    receipt_digest      TEXT NOT NULL UNIQUE,
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_policy_load_receipts_policy
    ON tehm_policy_load_receipts(policy_snapshot_id, runtime_id, loaded);
