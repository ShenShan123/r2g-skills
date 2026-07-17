#!/usr/bin/env python3
"""Shared SQLite + family-inference helpers for the knowledge store.

Imported by ingest_run.py, learn_heuristics.py, query_knowledge.py,
and mine_rules.py. No CLI.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

DEFAULT_KNOWLEDGE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = DEFAULT_KNOWLEDGE_DIR / "knowledge.sqlite"
DEFAULT_SCHEMA_PATH = DEFAULT_KNOWLEDGE_DIR / "schema.sql"
DEFAULT_FAMILIES_PATH = DEFAULT_KNOWLEDGE_DIR / "families.json"


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    # A no-arg connect() honors R2G_KNOWLEDGE_DB before the shipped default — symmetric
    # with journal_db's R2G_JOURNAL_DB. Lets a unit test / sandbox point every default
    # store access at an isolated DB so it never reads or writes the committed
    # knowledge.sqlite (a functional test must not depend on the shipped lifecycle
    # state). An explicit db_path always wins.
    if db_path is None:
        db_path = os.environ.get("R2G_KNOWLEDGE_DB") or DEFAULT_DB_PATH
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # timeout + busy_timeout make a concurrent writer wait-and-retry rather than
    # fail instantly with "database is locked". The campaign runs a pool of
    # ingest_run subprocesses against this one DB and the driver swallows ingest
    # errors, so an unguarded lock would silently drop a run from the store.
    # Parity with journal_db.connect (which also writes concurrently at ingest).
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL (parity with journal_db): under R2G_AB_WORKERS=8 a burst of concurrent
    # ingests could exceed the 30s busy_timeout in rollback-journal mode (readers
    # block writers), and a locked-out ingest is SWALLOWED by the driver — a run
    # silently missing from the store (2026-07-04 audit M5). WAL lets readers and
    # the writer proceed concurrently. Best-effort: flipping the mode needs a
    # moment with no competing lock, so retry on the next connect if it fails.
    # The -wal/-shm sidecars are gitignored; SQLite auto-checkpoints on the last
    # clean close, so the TRACKED knowledge.sqlite binary stays commit-complete.
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        pass
    return conn


# Indexes on columns added by _migrate_add_columns. They MUST be created after the
# migration (a legacy DB's raw tables lack symptom_id until then; creating them inside
# schema.sql's executescript — which runs before the migration — fails with "no such
# column"). Idempotent.
_POST_MIGRATION_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_fix_events_symptom     ON fix_events(symptom_id)",
    "CREATE INDEX IF NOT EXISTS idx_run_violations_symptom ON run_violations(symptom_id)",
    "CREATE INDEX IF NOT EXISTS idx_fix_traj_symptom       ON fix_trajectories(symptom_id)",
    # Idempotency guard for A/B trial retries (P0-16): unique per deterministic
    # trial_uuid; NULLs (legacy/ad-hoc rows) are exempt via the partial predicate.
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_ab_trials_uuid  ON ab_trials(trial_uuid) WHERE trial_uuid IS NOT NULL",
)


def _migrate_drop_stale_fix_trajectories(conn: sqlite3.Connection) -> None:
    """Drop a legacy-PK fix_trajectories so schema.sql recreates it with the new
    (fix_session_id, check_type, symptom_id) PK (failure-patterns #44).

    fix_trajectories is a PURE re-derivable Tier-2 projection (learn_heuristics
    DELETEs + rebuilds it every run), so dropping a stale-PK copy loses nothing —
    the next learn() repopulates it, now split per symptom instead of collapsing a
    multi-symptom session onto its first symptom. Runs BEFORE executescript so the
    CREATE TABLE IF NOT EXISTS actually takes effect. Idempotent: once the table
    carries symptom_id in its PK this is a no-op (never re-drops).
    """
    info = conn.execute("PRAGMA table_info(fix_trajectories)").fetchall()
    if not info:
        return  # absent → schema.sql creates it fresh with the new PK
    pk_cols = {row[1] for row in info if row[5]}   # row[5] = pk position (>0 in PK)
    if "symptom_id" not in pk_cols:
        conn.execute("DROP TABLE fix_trajectories")


def ensure_schema(conn: sqlite3.Connection,
                  schema_path: Path | str = DEFAULT_SCHEMA_PATH) -> None:
    _migrate_drop_stale_fix_trajectories(conn)
    ddl = Path(schema_path).read_text(encoding="utf-8")
    conn.executescript(ddl)
    _migrate_add_columns(conn)
    remapped = _migrate_legacy_symptom_ids(conn)
    for stmt in _POST_MIGRATION_INDEXES:
        conn.execute(stmt)
    conn.commit()
    if remapped:
        _repoint_journal_symptom_ids(conn, remapped)


# Idempotent ALTER TABLE ADD COLUMN migrations, keyed by table name. schema.sql
# uses CREATE TABLE IF NOT EXISTS so it never re-creates existing tables; these
# entries patch already-existing tables on legacy DBs. New tables (e.g. symptoms)
# go straight into schema.sql.
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "runs": {
        "lvs_mismatch_class": "TEXT",
        # Nullable provenance tag for the payoff A/B harness: which arm produced
        # this run ('naive' | 'learned' | NULL). Populated from config.mk EVAL_ARM
        # by ingest_run.py; absent for every non-eval run. Does not affect learning.
        "eval_arm": "TEXT",
        # Engineer-loop (spec 2026-06-09 decisions 6+8): structural class stamp
        # ("<design_type>/<size_class>", never the name) + strength metrics +
        # the heuristics generation in force when this run executed.
        "design_class": "TEXT",
        "heuristics_generation": "INTEGER",
        "first_attempt_clean": "INTEGER",
        "fix_iters_to_clean": "INTEGER",
        "wall_s_to_clean": "REAL",
        # Dense signoff reward (Win 1, paper-absorption 2026-06-16): continuous
        # [0,1] score = w_stage·stage_progress + w_vrr·VRR, or NULL when the
        # furthest stage is unknown. ADDITIVE and ADVISORY — is_success stays the
        # SOLE authority for clean/fail and for recipe promotion. A PURE function of
        # the run's OWN artifacts (its stage_log + its own fix_log), so re-ingest is
        # idempotent; never cross-row derived (that was the 2026-06-13 clobber bug).
        "outcome_score": "REAL",
        # Held-out benchmark flag (Win 3, r2g-bench): 1 when this run's design is in
        # knowledge/eval/bench_set.json. Set at ingest. Filtered ONLY at the
        # LEARNING/suggest read (learn_heuristics WHERE is_bench=0) — NEVER the
        # failure_events write path (a bench fail still gets its orfs-fail-% event
        # and stays in the honesty count). NULL/0 == not a bench run.
        "is_bench": "INTEGER",
        # Pre-route feature vector (Win 5, presynth.py) as JSON — instance count,
        # primary I/O, est logic depth, target util, clock period, routing layers.
        # PREDICTIVE inputs available at SUGGESTION time (vs the post-route
        # metadata.csv outcomes), so suggest_config can do feature-vector KNN
        # retrieval. Populated at ingest when reports/presynth_features.json exists;
        # NULL otherwise (retrieval falls back to family medians).
        "presynth_features_json": "TEXT",
        # Per-stage setup worst-slack (ns) for the Fmax slack-deterioration model
        # (feat/fmax-search 2026-06-04). Fresh DBs get these from schema.sql's runs
        # CREATE TABLE; these entries forward-migrate a legacy runs.sqlite.
        # floorplan_setup_ws = 2_1_floorplan.json floorplan__timing__setup__ws
        # place_setup_ws     = 3_5_place_dp.json   detailedplace__timing__setup__ws
        # finish_setup_ws    = 6_report.json       finish__timing__setup__ws (== wns_ns)
        "floorplan_setup_ws": "REAL",
        "place_setup_ws": "REAL",
        "finish_setup_ws": "REAL",
        # Declared flow scope (rtl-acquire ingestion 2026-07-09): 'full' (default,
        # NULL == full) or 'synth_only' for corpus-expansion runs that INTEND to
        # stop after synthesis (config.mk `export R2G_FLOW_SCOPE = synth_only`).
        # orfs_status is derived against the DECLARED scope, so a synth-only pass
        # ingests as 'pass' (not a misleading 'partial' that would flood the A/B
        # planner with runs that were never signoff subjects). Mirrors the is_bench
        # pattern: readers may filter on it; the failure_events write path NEVER does.
        "flow_scope": "TEXT",
    },
    # Symptom-indexed memory (spec 2026-06-09): raw symptom tagging on the raw tiers.
    "fix_events": {
        "symptom_id": "TEXT",
        "signature_json": "TEXT",
    },
    "fix_trajectories": {
        "symptom_id": "TEXT",
        "signature_json": "TEXT",
        # Evidence provenance carried up from the winning fix_event (P1-17,
        # 2026-07-15): 'live' | 'backfill:<source>'. Lets recipe aggregation keep
        # live and reconstructed/synthetic evidence distinguishable (the learner
        # tags each recipe with its evidence sources) instead of silently merging
        # lower-trust backfill into live-equivalent confidence.
        "provenance": "TEXT",
    },
    # Engineer-loop inline A/B trials (spec §5.4). trial_uuid makes record_trial
    # idempotent across a crash/retry (P0-16, 2026-07-15) — same planned trial ->
    # same uuid -> no duplicate row inflating promotion evidence.
    "ab_trials": {
        "trial_uuid": "TEXT",
    },
    "run_violations": {
        "symptom_id": "TEXT",
        "signature_json": "TEXT",
    },
    # Monotonic lifecycle version, +1 on every recipe_lifecycle._set transition
    # (2026-07-16 issue 6): promote/demote never bumped `generation`, so the A/B
    # judge's generation-only staleness guard was blind to a demotion landing
    # between plan and judge — a stale decisive trial then re-promoted the recipe.
    "recipe_status": {
        "status_version": "INTEGER",
    },
    # Cold archive mirrors fix_events for the SELECT * copy in archive_old_raw;
    # patch legacy sidecar archive DBs so the column counts still match.
    "fix_events_archive": {
        "symptom_id": "TEXT",
        "signature_json": "TEXT",
    },
}


def _migrate_add_columns(conn: sqlite3.Connection) -> None:
    for table, cols in _ADDED_COLUMNS.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for col, decl in cols.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    # runs.flow_scope contract is 'full' | 'synth_only' (knowledge README
    # invariant 33). The 2026-07-09 rtl-acquire migration added the column
    # schema-only, leaving legacy rows NULL — benign while no reader filters
    # ='full', but a latent silent-drop for any future one. Idempotent
    # backfill: every pre-flow_scope row was a full-flow run.
    conn.execute(
        "UPDATE runs SET flow_scope='full' "
        "WHERE flow_scope IS NULL OR flow_scope=''")


# Every knowledge-side table carrying a symptom_id column (symptoms itself is
# handled separately — it is the PK being re-keyed). recipe_status is also
# separate: its (symptom_id, design_class, platform, strategy) uniqueness needs
# collision resolution, not a blind UPDATE.
_SYMPTOM_ID_TABLES = ("fix_events", "fix_events_archive", "fix_trajectories",
                      "run_violations", "escalations", "ab_trials")

# Lifecycle precedence on a recipe_status collision after re-keying: judged /
# terminal states (promoted, demoted) outrank heal ('parked') and queue states.
_RECIPE_STATUS_RANK = {"promoted": 5, "demoted": 4, "parked": 3,
                       "candidate": 2, "shadow": 1}


def _migrate_legacy_symptom_ids(conn: sqlite3.Connection) -> dict[str, str]:
    """Re-key symptoms whose class predates symptom.normalize_class (2026-07-04).

    KLayout classes stored verbatim ("'m3.2'", quoted rule prose) minted ids that
    fragment the index against post-normalization rows: the promoted recipe sat
    stranded under the legacy quoted id while every new occurrence looked up the
    canonical id (failure-patterns.md "Dataset-Extraction" #28, 2026-07-09).
    Idempotent: rows whose class is already canonical are untouched. Returns the
    {legacy_id: canonical_id} map so the caller can re-point the journal.
    """
    import json as _json
    import symptom as _symptom   # pure module (no DB/IO) — no import cycle

    remapped: dict[str, str] = {}
    rows = conn.execute("SELECT symptom_id, check_type, class, predicates_json "
                        "FROM symptoms").fetchall()
    for sid, check, cls, preds_json in rows:
        ncls = _symptom.normalize_class(cls)
        if ncls == cls:
            continue
        try:
            preds = _json.loads(preds_json or "{}")
        except (ValueError, TypeError):
            preds = {}
        nid = _symptom.symptom_id(
            {"check": check, "class": ncls, "predicates": preds})
        if nid == sid:
            continue
        for table in _SYMPTOM_ID_TABLES:
            conn.execute(f"UPDATE {table} SET symptom_id=? WHERE symptom_id=?",
                         (nid, sid))
        for rowid, dc, plat, strat, status in conn.execute(
                "SELECT rowid, design_class, platform, strategy, status "
                "FROM recipe_status WHERE symptom_id=?", (sid,)).fetchall():
            twin = conn.execute(
                "SELECT rowid, status FROM recipe_status WHERE symptom_id=? AND "
                "design_class=? AND platform=? AND strategy=?",
                (nid, dc, plat, strat)).fetchone()
            if twin is None:
                conn.execute("UPDATE recipe_status SET symptom_id=? WHERE rowid=?",
                             (nid, rowid))
            elif (_RECIPE_STATUS_RANK.get(status, 0)
                    > _RECIPE_STATUS_RANK.get(twin[1], 0)):
                conn.execute("DELETE FROM recipe_status WHERE rowid=?", (twin[0],))
                conn.execute("UPDATE recipe_status SET symptom_id=? WHERE rowid=?",
                             (nid, rowid))
            else:
                conn.execute("DELETE FROM recipe_status WHERE rowid=?", (rowid,))
        twin = conn.execute("SELECT first_seen FROM symptoms WHERE symptom_id=?",
                            (nid,)).fetchone()
        if twin is None:
            conn.execute("UPDATE symptoms SET symptom_id=?, class=? "
                         "WHERE symptom_id=?", (nid, ncls, sid))
        else:
            conn.execute(
                "UPDATE symptoms SET first_seen=MIN(first_seen, "
                "(SELECT first_seen FROM symptoms WHERE symptom_id=?)) "
                "WHERE symptom_id=?", (sid, nid))
            conn.execute("DELETE FROM symptoms WHERE symptom_id=?", (sid,))
        remapped[sid] = nid
    return remapped


def _repoint_journal_symptom_ids(conn: sqlite3.Connection,
                                 remapped: dict[str, str]) -> None:
    """Keep the journal's symptom linkage in step with a knowledge-side re-key.

    check_db_integrity's L1/L2 correspondence joins the two books on symptom_id;
    a knowledge-only re-key would orphan the journal's ab_launch/promote actions.
    Best-effort by contract: only patches a journal.sqlite sibling of THIS
    knowledge db (the standard layout), silently skips when absent.
    """
    row = conn.execute("PRAGMA database_list").fetchone()
    db_file = row[2] if row else ""
    if not db_file:
        return
    journal = Path(db_file).parent / "journal.sqlite"
    if not journal.exists():
        return
    jconn = sqlite3.connect(str(journal))
    try:
        for sid, nid in remapped.items():
            jconn.execute("UPDATE actions SET symptom_id=? WHERE symptom_id=?",
                          (nid, sid))
        jconn.commit()
    finally:
        jconn.close()


# --- Learnable-success predicate (shared) ---------------------------------
# The ONE definition of "a learnable success", imported by both learners
# (learn_heuristics.py and monitor_health.py) so they never disagree.
#
# Signoff status values that do NOT indicate a failed/blocked signoff stage.
# 'None' means the stage was not run for this row (absence is not failure).
DRC_NOT_FAILED = {None, "clean", "clean_beol", "skipped"}
LVS_NOT_FAILED = {None, "clean", "skipped"}
RCX_NOT_FAILED = {None, "complete", "skipped"}


def is_success(row: dict) -> bool:
    """A run counts as a learnable success if EITHER the flow reported a full
    6-stage ORFS pass (strict, legacy), OR it reached a final signed-off layout
    with positive clean signoff and no failed signoff (relaxed).

    The relaxed path exists because most historical runs have an incomplete
    backend/stage_log.jsonl, so ingest leaves orfs_status='partial'/'unknown'
    even though they produced a clean GDS — clean DRC/LVS/RCX cannot exist
    without a completed finish stage. Absence of signoff data alone is NOT a
    success: at least one POSITIVE clean signal is required.
    """
    drc = row.get("drc_status")
    lvs = row.get("lvs_status")
    rcx = row.get("rcx_status")
    mclass = row.get("lvs_mismatch_class")

    # symmetric_matcher is a KLayout tool limitation on a clean layout, not a
    # real defect (see references LVS notes), so it counts as a not-failed LVS.
    # It is only meaningful on a 'fail' verdict (a complete, electrically-correct
    # lvsdb the symmetric matcher couldn't balance); requiring lvs == "fail" stops
    # a future path that set the class on an incomplete/crash LVS from leaking a
    # real failure through as a success.
    lvs_not_failed = (lvs in LVS_NOT_FAILED) or (
        mclass == "symmetric_matcher" and lvs == "fail"
    )
    drc_not_failed = drc in DRC_NOT_FAILED
    rcx_not_failed = rcx in RCX_NOT_FAILED

    strict = (
        row.get("orfs_status") == "pass"
        and drc_not_failed and lvs_not_failed and rcx_not_failed
    )

    has_positive_signoff = (
        lvs == "clean"
        or mclass == "symmetric_matcher"
        or drc in ("clean", "clean_beol")
        or rcx == "complete"
    )
    relaxed = has_positive_signoff and drc_not_failed and lvs_not_failed and rcx_not_failed
    return strict or relaxed


def load_families(families_path: Path | str = DEFAULT_FAMILIES_PATH) -> dict[str, Any]:
    data = json.loads(Path(families_path).read_text(encoding="utf-8"))
    if "mappings" not in data:
        data["mappings"] = {}
    if "patterns" not in data:
        data["patterns"] = []
    return data


def infer_family(design_name: str, families: dict[str, Any]) -> str:
    if not design_name:
        return "unknown"
    mappings: dict[str, str] = families.get("mappings", {})
    if design_name in mappings:
        return mappings[design_name]
    for entry in families.get("patterns", []):
        if re.search(entry["regex"], design_name, re.IGNORECASE):
            return entry["family"]
    return design_name.split("_", 1)[0].lower()


def diff_config_rows(old: dict[str, str], new: dict[str, str]) -> dict[str, Any]:
    """Compute the config diff between two config.mk field dicts.

    Returns {"changed": {key: {"old": v1, "new": v2}},
             "added": {key: value}, "removed": {key: value}}.
    """
    old_keys = set(old)
    new_keys = set(new)
    changed = {}
    for k in old_keys & new_keys:
        if old[k] != new[k]:
            changed[k] = {"old": old[k], "new": new[k]}
    added = {k: new[k] for k in new_keys - old_keys}
    removed = {k: old[k] for k in old_keys - new_keys}
    return {"changed": changed, "added": added, "removed": removed}
