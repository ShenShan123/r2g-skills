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
