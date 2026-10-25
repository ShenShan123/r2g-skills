"""Rule lifecycle status (design doc 20.10, 24.3, 19.6).

Statuses: shadow -> candidate -> promoted / demoted / quarantined, each with a
monotonic ``status_version`` bumped on EVERY transition (resume/trial staleness
is judged against it). The entry gate is validity: only PROVISIONAL_VALID /
VALIDATED rules may enter shadow (honesty H6, design doc 24.3).
"""
from __future__ import annotations

import sqlite3
import json
from collections.abc import Mapping

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
    """Write one immutable lifecycle status transition.

    A same-status call is a deterministic replay: it returns the existing
    version only when provenance is identical and otherwise fails closed.
    Status changes update the existing primary-key row in place and re-read the
    complete row before returning, avoiding ``INSERT OR REPLACE``'s delete /
    reinsert semantics and silent provenance loss.
    """
    if status not in LIFECYCLE_STATUSES:
        raise RuleLifecycleError(f"invalid lifecycle status {status!r}")
    if provenance is not None and not isinstance(provenance, Mapping):
        raise RuleLifecycleError("rule lifecycle provenance must be a mapping")
    requested_provenance = dict(provenance or {})
    current = get_status(conn, rule_id=rule_id, target_scope=target_scope)
    if current is not None and status == current["status"]:
        if stable_dumps(current.get("provenance") or {}) != stable_dumps(
                requested_provenance):
            raise RuleLifecycleError(
                "rule lifecycle replay conflicts with immutable provenance")
        return int(current["status_version"])
    version = (int(current["status_version"]) if current else 0) + 1
    had_outer_transaction = conn.in_transaction
    updated_at = tehm_db.now_local()
    if current is None:
        conn.execute(
            """INSERT INTO tehm_rule_status (
                   rule_id, target_scope, status, status_version,
                   provenance_json, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (rule_id, target_scope, status, version,
             stable_dumps(requested_provenance), updated_at))
    else:
        conn.execute(
            """UPDATE tehm_rule_status
                  SET status=?, status_version=?, provenance_json=?, updated_at=?
                WHERE rule_id=? AND target_scope=?""",
            (status, version, stable_dumps(requested_provenance), updated_at,
             rule_id, target_scope))
    persisted = get_status(conn, rule_id=rule_id, target_scope=target_scope)
    if persisted is None:
        raise RuleLifecycleError("rule lifecycle status was not persisted")
    expected = {
        "status": status,
        "status_version": version,
        "provenance": requested_provenance,
        "updated_at": updated_at,
    }
    if any(persisted.get(key) != value for key, value in expected.items()):
        raise RuleLifecycleError(
            "rule lifecycle status write is immutable and conflicts")
    if commit and not had_outer_transaction:
        conn.commit()
    return version


def get_status(conn: sqlite3.Connection, *, rule_id: str,
               target_scope: str) -> dict | None:
    row = conn.execute(
        "SELECT rule_id, target_scope, status, status_version, provenance_json, updated_at "
        "FROM tehm_rule_status WHERE rule_id=? AND target_scope=?",
        (rule_id, target_scope)).fetchone()
    if row is None:
        return None
    status = row["status"]
    if status not in LIFECYCLE_STATUSES:
        raise RuleLifecycleError("rule lifecycle status row contains invalid status")
    version = row["status_version"]
    if type(version) is not int:
        raise RuleLifecycleError(
            "rule lifecycle status row contains invalid status_version")
    if version < 1:
        raise RuleLifecycleError(
            "rule lifecycle status row contains invalid status_version")
    try:
        provenance = json.loads(row["provenance_json"] or "{}")
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuleLifecycleError(
            "rule lifecycle status row contains malformed provenance") from exc
    if not isinstance(provenance, dict):
        raise RuleLifecycleError(
            "rule lifecycle status row contains malformed provenance")
    if not isinstance(row["updated_at"], str) or not row["updated_at"]:
        raise RuleLifecycleError(
            "rule lifecycle status row contains invalid updated_at")
    return {"rule_id": row["rule_id"], "target_scope": row["target_scope"],
            "status": status, "status_version": version,
            "provenance": provenance, "updated_at": row["updated_at"]}


def _rule_validity(conn: sqlite3.Connection, rule_id: str) -> str | None:
    row = conn.execute(
        "SELECT validity_status FROM tehm_rules WHERE rule_id=?",
        (rule_id,)).fetchone()
    return row["validity_status"] if row else None
