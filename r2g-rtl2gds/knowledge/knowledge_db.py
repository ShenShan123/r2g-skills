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
DEFAULT_DB_PATH = DEFAULT_KNOWLEDGE_DIR / "runs.sqlite"
DEFAULT_SCHEMA_PATH = DEFAULT_KNOWLEDGE_DIR / "schema.sql"
DEFAULT_FAMILIES_PATH = DEFAULT_KNOWLEDGE_DIR / "families.json"


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_schema(conn: sqlite3.Connection,
                  schema_path: Path | str = DEFAULT_SCHEMA_PATH) -> None:
    ddl = Path(schema_path).read_text(encoding="utf-8")
    conn.executescript(ddl)
    _migrate_add_columns(conn)
    conn.commit()


# Lightweight forward migrations. schema.sql uses CREATE TABLE IF NOT EXISTS, so a
# column added there never reaches an already-created runs.sqlite. Add such columns
# here idempotently (ALTER TABLE ADD COLUMN is a no-op error if it already exists).
_RUNS_ADDED_COLUMNS = {
    "lvs_mismatch_class": "TEXT",
}


def _migrate_add_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
    for col, decl in _RUNS_ADDED_COLUMNS.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE runs ADD COLUMN {col} {decl}")


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
    lvs_not_failed = (lvs in LVS_NOT_FAILED) or (mclass == "symmetric_matcher")
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
