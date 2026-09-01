"""Explicit Mechanism Knowledge lifecycle, independent of runtime rules."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping

from tehm import db as tehm_db
from tehm.causal.evidence_level import evidence_rank
from tehm.ids import stable_dumps

from .claims import KNOWLEDGE_STATUSES
from .registry import get_knowledge
from .schema import ensure_knowledge_schema


_TRANSITIONS = {
    "shadow": frozenset({"candidate", "invalidated", "retired", "superseded"}),
    "candidate": frozenset({"validated", "invalidated", "retired", "superseded"}),
    "validated": frozenset({"invalidated", "retired", "superseded"}),
    "superseded": frozenset(), "invalidated": frozenset(), "retired": frozenset(),
}


def get_knowledge_status(conn: sqlite3.Connection, *, knowledge_id: str,
                         version: int, target_scope: str = "global") -> dict:
    ensure_knowledge_schema(conn, commit=False)
    row = conn.execute(
        """SELECT * FROM tehm_mechanism_knowledge_status
            WHERE knowledge_id=? AND version=? AND target_scope=?""",
        (knowledge_id, version, target_scope)).fetchone()
    if row is None:
        raise ValueError("mechanism knowledge status row is missing")
    if row["status"] not in KNOWLEDGE_STATUSES:
        raise ValueError("mechanism knowledge status is invalid")
    try:
        provenance = json.loads(row["provenance_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("mechanism knowledge status provenance is malformed") from exc
    if not isinstance(provenance, dict):
        raise ValueError("mechanism knowledge status provenance is malformed")
    if type(row["status_version"]) is not int or row["status_version"] < 1:
        raise ValueError("mechanism knowledge status version is invalid")
    return {**dict(row), "provenance": provenance}


def set_knowledge_status(
    conn: sqlite3.Connection, *, knowledge_id: str, version: int,
    target_scope: str = "global", status: str,
    provenance: Mapping | None = None, authority_receipt=None,
    commit: bool = True,
) -> dict:
    """Move one claim explicitly; no transition grants ``promoted`` status."""
    if status not in KNOWLEDGE_STATUSES:
        raise ValueError(f"invalid mechanism knowledge status: {status!r}")
    if status == "promoted":  # defensive even though it is not in the enum
        raise ValueError("mechanism knowledge has no promoted runtime status")
    claim = get_knowledge(conn, knowledge_id, version, target_scope=target_scope)
    current = get_knowledge_status(conn, knowledge_id=knowledge_id,
                                   version=version, target_scope=target_scope)
    old_status = current["status"]
    if status == old_status:
        if provenance is not None and stable_dumps(dict(provenance)) != current["provenance_json"]:
            raise ValueError("mechanism knowledge status provenance is immutable")
        return current
    if status not in _TRANSITIONS[old_status]:
        raise ValueError(f"invalid mechanism knowledge status transition {old_status}->{status}")
    if status == "candidate" and evidence_rank(claim.evidence_level) < evidence_rank(
            "L2_CONTROLLED_INTERVENTION"):
        raise ValueError("L0/L1 knowledge is shadow-only")
    if status == "validated":
        if authority_receipt is None:
            raise ValueError("validated knowledge requires eligible authority receipt")
        # A pure evaluator result is diagnostic only.  Validation must consume
        # a content/status/evidence-bound receipt that is present in the
        # authority ledger and still matches the current status version.
        from .authority import verify_knowledge_authority
        verified = verify_knowledge_authority(conn, authority_receipt)
        if (not verified["eligible"] or
                verified.get("knowledge_object_id") != claim.object_id or
                verified.get("target_scope") != target_scope or
                verified.get("status_version") != current["status_version"]):
            raise ValueError(
                "validated knowledge requires eligible authority receipt: "
                f"{verified.get('reasons', ())}")
    provenance_json = stable_dumps(dict(provenance or current["provenance"]))
    now = tehm_db.now_local()
    had_outer_transaction = conn.in_transaction
    conn.execute(
        """UPDATE tehm_mechanism_knowledge_status
              SET status=?, status_version=?, provenance_json=?, updated_at=?
            WHERE knowledge_id=? AND version=? AND target_scope=?""",
        (status, int(current["status_version"]) + 1, provenance_json, now,
         knowledge_id, version, target_scope))
    result = get_knowledge_status(conn, knowledge_id=knowledge_id,
                                  version=version, target_scope=target_scope)
    if commit and not had_outer_transaction:
        conn.commit()
    return result


__all__ = ["get_knowledge_status", "set_knowledge_status"]
