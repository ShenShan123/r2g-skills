#!/usr/bin/env python3
"""Shared SQLite + family-inference helpers for the knowledge store.

Imported by ingest_run.py, learn_heuristics.py, query_knowledge.py,
and mine_rules.py. No CLI.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

DEFAULT_KNOWLEDGE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = DEFAULT_KNOWLEDGE_DIR / "knowledge.sqlite"
DEFAULT_SCHEMA_PATH = DEFAULT_KNOWLEDGE_DIR / "schema.sql"
DEFAULT_FAMILIES_PATH = DEFAULT_KNOWLEDGE_DIR / "families.json"


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
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
    return conn


# Indexes on columns added by _migrate_add_columns. They MUST be created after the
# migration (a legacy DB's raw tables lack symptom_id until then; creating them inside
# schema.sql's executescript — which runs before the migration — fails with "no such
# column"). Idempotent.
_POST_MIGRATION_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_fix_events_symptom     ON fix_events(symptom_id)",
    "CREATE INDEX IF NOT EXISTS idx_run_violations_symptom ON run_violations(symptom_id)",
    "CREATE INDEX IF NOT EXISTS idx_fix_traj_symptom       ON fix_trajectories(symptom_id)",
)


def ensure_schema(conn: sqlite3.Connection,
                  schema_path: Path | str = DEFAULT_SCHEMA_PATH) -> None:
    ddl = Path(schema_path).read_text(encoding="utf-8")
    conn.executescript(ddl)
    _migrate_add_columns(conn)
    for stmt in _POST_MIGRATION_INDEXES:
        conn.execute(stmt)
    conn.commit()


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
    },
    # Symptom-indexed memory (spec 2026-06-09): raw symptom tagging on the raw tiers.
    "fix_events": {
        "symptom_id": "TEXT",
        "signature_json": "TEXT",
    },
    "fix_trajectories": {
        "symptom_id": "TEXT",
        "signature_json": "TEXT",
    },
    "run_violations": {
        "symptom_id": "TEXT",
        "signature_json": "TEXT",
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
