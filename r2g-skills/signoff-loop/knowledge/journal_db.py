#!/usr/bin/env python3
"""Tier-0 journal DB helpers (engineer-loop spec §5.2, decisions 10/11).

SEPARATE high-volume gitignored SQLite file (default knowledge/journal.sqlite).
EVIDENCE only — learning conclusions stay in knowledge.sqlite/heuristics.json, so
journal loss/rotation never loses a recipe. WAL + busy_timeout: safe for
concurrent append-only flow workers. Mirrors knowledge_db.py conventions.
"""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from pathlib import Path

DEFAULT_KNOWLEDGE_DIR = Path(__file__).resolve().parent
DEFAULT_JOURNAL_PATH = DEFAULT_KNOWLEDGE_DIR / "journal.sqlite"
DEFAULT_SCHEMA_PATH = DEFAULT_KNOWLEDGE_DIR / "journal_schema.sql"

ACTION_TYPES = ("config_knob_delta", "sdc_edit", "stage_rerun", "tool_invoke",
                "escalate", "ab_launch", "promote", "demote")


def _now() -> str:
        # SYSTEM-LOCAL time with numeric offset (2026-07-04, operator request) —
    # replaces utcnow()+"Z". Readers must compare timestamps via julianday()
    # (parses both regimes), never lexicographically.
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def connect(db_path: Path | str = DEFAULT_JOURNAL_PATH) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


# Idempotent ALTERs for legacy journal DBs (same pattern as knowledge_db).
_ADDED_COLUMNS: dict[str, dict[str, str]] = {}


def ensure_schema(conn: sqlite3.Connection,
                  schema_path: Path | str = DEFAULT_SCHEMA_PATH) -> None:
    conn.executescript(Path(schema_path).read_text(encoding="utf-8"))
    for table, cols in _ADDED_COLUMNS.items():
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, decl in cols.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    conn.commit()


def append_action(conn, *, project_path: str, actor: str, action_type: str,
                  payload: dict | None = None, design: str | None = None,
                  platform: str | None = None, run_id: str | None = None,
                  fix_session_id: str | None = None,
                  parent_action_id: int | None = None,
                  symptom_id: str | None = None, ts: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO actions (ts, project_path, run_id, fix_session_id, design,"
        " platform, actor, action_type, payload_json, parent_action_id, symptom_id)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (ts or _now(), project_path, run_id, fix_session_id, design, platform,
         actor, action_type, json.dumps(payload or {}, sort_keys=True),
         parent_action_id, symptom_id))
    conn.commit()
    return cur.lastrowid


def append_log_summary(conn, *, project_path: str, stage: str | None,
                       tool: str | None, source_path: str | None,
                       status: str | None, digest: str,
                       error_count: int | None = None,
                       warning_count: int | None = None,
                       first_error: str | None = None,
                       last_lines: str | None = None,
                       metrics: dict | None = None,
                       action_id: int | None = None,
                       run_id: str | None = None, ts: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO log_summaries (ts, project_path, run_id, action_id, stage,"
        " tool, source_path, status, error_count, warning_count, first_error,"
        " last_lines, metrics_json, digest) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (ts or _now(), project_path, run_id, action_id, stage, tool, source_path,
         status, error_count, warning_count, first_error, last_lines,
         json.dumps(metrics or {}, sort_keys=True), digest))
    conn.commit()
    return cur.lastrowid


def append_tool_bug(conn, *, project_path: str, stage: str | None,
                    tool: str | None, signature: str,
                    symptom_id: str | None = None,
                    signature_json: str | None = None,
                    log_excerpt: str | None = None,
                    action_id: int | None = None,
                    run_id: str | None = None, ts: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO tool_bugs (ts, project_path, run_id, action_id, stage, tool,"
        " signature, symptom_id, signature_json, log_excerpt)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (ts or _now(), project_path, run_id, action_id, stage, tool, signature,
         symptom_id, signature_json, log_excerpt))
    conn.commit()
    return cur.lastrowid


def backfill_run_id(conn, *, project_path: str, run_id: str) -> int:
    """Link this project's journal rows to the run_id minted at ingest.
    Only fills NULLs — never clobbers an existing link (re-ingest safe)."""
    n = 0
    for t in ("actions", "log_summaries", "tool_bugs"):
        n += conn.execute(
            f"UPDATE {t} SET run_id=? WHERE project_path=? AND run_id IS NULL",
            (run_id, project_path)).rowcount
    conn.commit()
    return n
