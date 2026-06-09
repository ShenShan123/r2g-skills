-- r2g-rtl2gds knowledge store schema. DO NOT edit at runtime —
-- all writes go through knowledge/knowledge_db.py::ensure_schema.

CREATE TABLE IF NOT EXISTS runs (
    run_id                  TEXT PRIMARY KEY,
    project_path            TEXT NOT NULL,
    design_name             TEXT,
    design_family           TEXT,
    platform                TEXT,
    ingested_at             TEXT NOT NULL,

    -- config inputs (parsed from constraints/config.mk)
    core_utilization        REAL,
    place_density_lb_addon  REAL,
    synth_hierarchical      INTEGER,
    abc_area                INTEGER,
    die_area                TEXT,
    clock_period_ns         REAL,
    extra_config_json       TEXT,

    -- outcomes (parsed from reports/*.json)
    orfs_status             TEXT,
    orfs_fail_stage         TEXT,
    wns_ns                  REAL,
    tns_ns                  REAL,
    timing_tier             TEXT,
    cell_count              INTEGER,
    area_um2                REAL,
    power_mw                REAL,
    drc_status              TEXT,
    drc_violations          INTEGER,
    lvs_status              TEXT,
    -- LVS fail sub-class from extract_lvs.py::classify_lvs_mismatch:
    -- symmetric_matcher (tool limit, layout clean) | real_connectivity (defect) | generic
    lvs_mismatch_class      TEXT,
    rcx_status              TEXT,

    -- timings
    total_elapsed_s         REAL,
    stage_times_json        TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_family_platform ON runs(design_family, platform);
CREATE INDEX IF NOT EXISTS idx_runs_design_platform ON runs(design_name, platform);

CREATE TABLE IF NOT EXISTS failure_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    stage       TEXT,
    signature   TEXT NOT NULL,
    detail      TEXT
);

CREATE INDEX IF NOT EXISTS idx_failure_signature ON failure_events(signature);
CREATE INDEX IF NOT EXISTS idx_failure_run ON failure_events(run_id);

CREATE TABLE IF NOT EXISTS config_lineage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    design_name     TEXT NOT NULL,
    platform        TEXT NOT NULL,
    current_run_id  TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    previous_run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    diff_json       TEXT NOT NULL,
    current_outcome TEXT,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_lineage_design_platform
    ON config_lineage(design_name, platform);
CREATE INDEX IF NOT EXISTS idx_lineage_current_run
    ON config_lineage(current_run_id);

-- ── Fix-Learning Loop (spec 2026-06-05) ──────────────────────────────────
-- Tier-1: append-only raw, one row per fix iteration (lossless system of record).
CREATE TABLE IF NOT EXISTS fix_events (
    fix_event_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    fix_session_id          TEXT NOT NULL,           -- episode key (stable across re-ingest)
    project_path            TEXT,
    design_name             TEXT,
    design_family           TEXT,
    platform                TEXT,
    check_type              TEXT,                    -- timing | drc | lvs
    violation_class         TEXT,                    -- dominant DRC cat | lvs mismatch_class | timing tier
    iter                    INTEGER,
    strategy                TEXT,
    from_stage              TEXT,                    -- ORFS rerun-from stage
    before_count            REAL,
    after_count             REAL,
    before_categories_json  TEXT,                    -- full category vector (D9)
    after_categories_json   TEXT,
    rule_details_json       TEXT,                    -- rule/net/path specifics where emitted
    before_status           TEXT,
    after_status            TEXT,
    verdict                 TEXT,                    -- cleared|win|no_change|regression|inconclusive
    config_delta_json       TEXT,                    -- this iteration's config.mk edit
    cumulative_config_json  TEXT,                    -- full applied-fix block snapshot
    env_flags_json          TEXT,                    -- PLACE_FAST/ROUTE_FAST/SKIP_ANTENNA_REPAIR/...
    tool_versions_json      TEXT,                    -- openroad/klayout/yosys/orfs
    stage_metrics_json      TEXT,                    -- per-stage slacks/area/power/IR
    stacked                 INTEGER,                 -- 1 if prior edits still in effect
    elapsed_s               REAL,
    ts                      TEXT,
    provenance              TEXT,                    -- live | backfill:<source>
    symptom_id              TEXT,
    signature_json          TEXT,
    UNIQUE(fix_session_id, iter, strategy)
);
CREATE INDEX IF NOT EXISTS idx_fix_events_session ON fix_events(fix_session_id);
CREATE INDEX IF NOT EXISTS idx_fix_events_fam
    ON fix_events(design_family, platform, check_type, violation_class);

-- Tier-2: per-episode trajectory (re-derivable from fix_events; materialized).
-- PK is composite (fix_session_id, check_type): a '--check both' fix run shares
-- ONE fix_session_id across DRC and LVS events, and each check must yield its own
-- trajectory so the LVS recipe is not mis-filed under the DRC violation_class
-- (bug #2/#8). See references/signoff-fixing.md ("Correctness invariants").
CREATE TABLE IF NOT EXISTS fix_trajectories (
    fix_session_id          TEXT NOT NULL,
    project_path            TEXT,
    design_name             TEXT,
    design_family           TEXT,
    platform                TEXT,
    check_type              TEXT NOT NULL,
    violation_class         TEXT,
    path_json               TEXT,                    -- ordered [{iter,strategy,before,after,verdict}]
    n_iters                 INTEGER,
    outcome                 TEXT,                    -- resolved | abandoned
    winning_strategy        TEXT,
    winning_config_json     TEXT,
    failed_strategies_json  TEXT,
    initial_count           REAL,
    final_count             REAL,
    total_elapsed_s         REAL,
    symptom_id              TEXT,
    signature_json          TEXT,
    PRIMARY KEY (fix_session_id, check_type)
);
CREATE INDEX IF NOT EXISTS idx_fix_traj_fam
    ON fix_trajectories(design_family, platform, check_type, violation_class);

-- Per-run violation snapshot (EVERY run, incl. clean) — the complete landscape (D9).
CREATE TABLE IF NOT EXISTS run_violations (
    run_id                  TEXT PRIMARY KEY REFERENCES runs(run_id) ON DELETE CASCADE,
    design_family           TEXT,
    platform                TEXT,
    drc_status              TEXT,
    drc_categories_json     TEXT,
    lvs_status              TEXT,
    lvs_mismatch_class      TEXT,
    timing_tier             TEXT,
    wns_ns                  REAL,
    symptom_id              TEXT,
    signature_json          TEXT,
    snapshot_ts             TEXT
);
CREATE INDEX IF NOT EXISTS idx_run_violations_fam ON run_violations(design_family, platform);

-- Cold archive for raw fix_events evicted past the size threshold (D13). Same columns
-- as fix_events (no autoincrement PK / UNIQUE — it's a sink). ensure_schema creates it
-- in both the main DB and the separate knowledge/fix_events_archive.sqlite file.
CREATE TABLE IF NOT EXISTS fix_events_archive (
    fix_event_id INTEGER, fix_session_id TEXT, project_path TEXT, design_name TEXT,
    design_family TEXT, platform TEXT, check_type TEXT, violation_class TEXT, iter INTEGER,
    strategy TEXT, from_stage TEXT, before_count REAL, after_count REAL,
    before_categories_json TEXT, after_categories_json TEXT, rule_details_json TEXT,
    before_status TEXT, after_status TEXT, verdict TEXT, config_delta_json TEXT,
    cumulative_config_json TEXT, env_flags_json TEXT, tool_versions_json TEXT,
    stage_metrics_json TEXT, stacked INTEGER, elapsed_s REAL, ts TEXT, provenance TEXT,
    symptom_id TEXT, signature_json TEXT          -- mirror fix_events (SELECT * archive copy)
);

-- ── Symptom-indexed memory (spec 2026-06-09) ─────────────────────────────
-- Raw symptom catalog: one row per distinct symptom_id. The symptom is the
-- universal index for learned repair experience; design-family/name is NEVER
-- a key (only evidence_designs provenance in the derived heuristics.json).
CREATE TABLE IF NOT EXISTS symptoms (
    symptom_id              TEXT PRIMARY KEY,        -- sha1(check, class, sorted true predicates)[:16]
    check_type              TEXT,                    -- drc | lvs | timing | synth | orfs_stage
    class                   TEXT,                    -- dominant DRC cat | lvs mismatch_class | timing tier | ...
    predicates_json         TEXT,                    -- {"nets_balanced": true, ...} (sparse, true-only)
    symptom_schema_version  INTEGER,                 -- bump when the predicate set / hashing changes
    first_seen              TEXT
);
CREATE INDEX IF NOT EXISTS idx_symptoms_check_class ON symptoms(check_type, class);

-- Fast symptom lookups on the raw tiers (symptom_id/signature_json columns are
-- added by knowledge_db._migrate_add_columns for legacy DBs; created here for new).
CREATE INDEX IF NOT EXISTS idx_fix_events_symptom    ON fix_events(symptom_id);
CREATE INDEX IF NOT EXISTS idx_run_violations_symptom ON run_violations(symptom_id);
CREATE INDEX IF NOT EXISTS idx_fix_traj_symptom      ON fix_trajectories(symptom_id);
