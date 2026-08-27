"""Rule lifecycle status (design doc 20.10, 24.3, 19.6).

Statuses: shadow -> candidate -> promoted / demoted / quarantined, each with a
monotonic ``status_version`` bumped on EVERY transition (resume/trial staleness
is judged against it). The entry gate is validity: only PROVISIONAL_VALID /
VALIDATED rules may enter shadow (honesty H6, design doc 24.3).
"""
from __future__ import annotations

import sqlite3

from tehm import db as tehm_db
from tehm.crystallization.validity import ADMISSIBLE_FOR_LIFECYCLE
from tehm.ids import stable_dumps

LIFECYCLE_VERSION = "rule-lifecycle-v0.1"
LIFECYCLE_STATUSES = (
    "shadow", "candidate", "promoted", "demoted", "quarantined", "retired")


class RuleLifecycleError(RuntimeError):
    pass


def enter_shadow(conn: sqlite3.Connection, *, rule_id: str, target_scope: str,
                 provenance: dict | None = None, commit: bool = True) -> int:
    """Enter shadow — refused unless the rule meets minimum validity (H6)."""
    validity = _rule_validity(conn, rule_id)
    if validity not in ADMISSIBLE_FOR_LIFECYCLE:
        raise RuleLifecycleError(
            f"rule {rule_id} validity={validity!r} is below the lifecycle "
            f"minimum {ADMISSIBLE_FOR_LIFECYCLE}; it may not enter shadow (H6)")
    return set_status(conn, rule_id=rule_id, target_scope=target_scope,
                      status="shadow", provenance=provenance, commit=commit)


def set_status(conn: sqlite3.Connection, *, rule_id: str, target_scope: str,
               status: str, provenance: dict | None = None,
               commit: bool = True) -> int:
    """UPSERT a lifecycle status; bumps ``status_version`` on every transition."""
    if status not in LIFECYCLE_STATUSES:
        raise RuleLifecycleError(f"invalid lifecycle status {status!r}")
    current = get_status(conn, rule_id=rule_id, target_scope=target_scope)
    version = (current["status_version"] if current else 0) + 1
    conn.execute(
        """INSERT OR REPLACE INTO tehm_rule_status (
               rule_id, target_scope, status, status_version,
               provenance_json, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (rule_id, target_scope, status, version,
         stable_dumps(provenance or {}), tehm_db.now_local()))
    if commit:
        conn.commit()
    return version


def get_status(conn: sqlite3.Connection, *, rule_id: str,
               target_scope: str) -> dict | None:
    row = conn.execute(
        "SELECT rule_id, target_scope, status, status_version, updated_at "
        "FROM tehm_rule_status WHERE rule_id=? AND target_scope=?",
        (rule_id, target_scope)).fetchone()
    if row is None:
        return None
    return {"rule_id": row["rule_id"], "target_scope": row["target_scope"],
            "status": row["status"], "status_version": row["status_version"],
            "updated_at": row["updated_at"]}


def _rule_validity(conn: sqlite3.Connection, rule_id: str) -> str | None:
    row = conn.execute(
        "SELECT validity_status FROM tehm_rules WHERE rule_id=?",
        (rule_id,)).fetchone()
    return row["validity_status"] if row else None
