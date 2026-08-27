"""Explicit rule revision lineage receipts."""
from __future__ import annotations

import hashlib
import sqlite3

from tehm import db as tehm_db
from tehm.ids import stable_dumps

from .events import verify_event_chain
from .receipts import RuleRevisionReceipt

REVISION_OPERATIONS = frozenset({"MERGE", "SPLIT", "SPECIALIZE", "GENERALIZE", "REVISE"})
_REVISION_TRIGGER_TYPES = frozenset({
    "CONSOLIDATION_TRIGGERED", "RULE_REVISION_PROPOSED",
})


def _validate_provenance(
    conn: sqlite3.Connection,
    *,
    trigger_event_id: str,
    evidence_refs: tuple[str, ...],
) -> tuple[sqlite3.Row, tuple[str, ...]]:
    """Validate the evidence firewall for one rule-revision receipt.

    ``tehm_rule_revisions`` is derived state, so its free-form reference
    columns cannot be protected by SQLite foreign keys alone.  Keep the
    authority check here: a revision must be caused by an online consolidation
    event whose learner campaign is intact, and every evidence reference must
    resolve to a canonical transition in that same training campaign.
    """
    event = conn.execute(
        "SELECT * FROM tehm_memory_events WHERE event_id=?",
        (trigger_event_id,),
    ).fetchone()
    if event is None:
        raise ValueError("revision trigger_event_id is not in the event log")
    if event["event_type"] not in _REVISION_TRIGGER_TYPES:
        raise ValueError(
            "revision trigger must be a consolidation or rule-proposal event")
    campaign_id = event["campaign_id"]
    if not campaign_id or not bool(event["learner_eligible"]):
        raise ValueError(
            "revision trigger must be learner-eligible and campaign-bound")
    chain = verify_event_chain(conn, campaign_id=str(campaign_id))
    if not chain.get("ok"):
        raise ValueError("revision trigger event chain is invalid")

    # A source transition is the minimum auditable anchor for a trigger.  If a
    # future manager uses a different source type, the evidence refs still
    # provide the canonical anchors below.
    if event["source_type"] == "transition" and str(event["source_id"]) not in evidence_refs:
        raise ValueError("revision evidence must include the trigger transition")

    placeholders = ",".join("?" for _ in evidence_refs)
    transitions = conn.execute(
        f"SELECT transition_id FROM tehm_transitions "
        f"WHERE transition_id IN ({placeholders})",
        evidence_refs,
    ).fetchall()
    found = {str(row["transition_id"]) for row in transitions}
    missing = [ref for ref in evidence_refs if ref not in found]
    if missing:
        raise ValueError(
            "revision evidence refs must resolve to canonical transitions: "
            + ",".join(missing))

    memberships = conn.execute(
        f"""SELECT transition_id, split, learner_eligible
              FROM tehm_dataset_membership
             WHERE campaign_id=? AND transition_id IN ({placeholders})""",
        (campaign_id, *evidence_refs),
    ).fetchall()
    by_id = {str(row["transition_id"]): row for row in memberships}
    if len(by_id) != len(evidence_refs):
        missing_membership = [ref for ref in evidence_refs if ref not in by_id]
        raise ValueError(
            "revision evidence is missing trigger-campaign membership: "
            + ",".join(missing_membership))
    invalid = [
        ref for ref in evidence_refs
        if not bool(by_id[ref]["learner_eligible"])
        or str(by_id[ref]["split"]) != "training"
    ]
    if invalid:
        raise ValueError(
            "revision evidence must be learner-eligible training data: "
            + ",".join(invalid))
    return event, evidence_refs


def record_rule_revision(
    conn: sqlite3.Connection,
    *,
    parent_rule_id: str | None,
    child_rule_id: str,
    operation: str,
    trigger_event_id: str,
    evidence_refs: list[str] | tuple[str, ...],
    validation: dict | None = None,
    created_at: str | None = None,
    commit: bool = True,
) -> RuleRevisionReceipt:
    if operation not in REVISION_OPERATIONS:
        raise ValueError(f"invalid rule revision operation: {operation!r}")
    if not child_rule_id or not trigger_event_id or not evidence_refs:
        raise ValueError("revision requires child, trigger event, and evidence refs")
    refs = tuple(sorted({str(ref).strip() for ref in evidence_refs}))
    if not refs or any(not ref for ref in refs):
        raise ValueError("revision evidence refs must be non-empty")
    _validate_provenance(
        conn, trigger_event_id=trigger_event_id, evidence_refs=refs)
    identity = {"parent": parent_rule_id, "child": child_rule_id,
                "operation": operation, "trigger": trigger_event_id,
                "evidence_refs": refs}
    revision_id = "revision_" + hashlib.sha1(
        stable_dumps(identity).encode()).hexdigest()[:20]
    had_outer_transaction = conn.in_transaction
    conn.execute(
        """INSERT OR IGNORE INTO tehm_rule_revisions
           (revision_id, parent_rule_id, child_rule_id, operation,
            trigger_event_id, evidence_refs_json, validation_json, created_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (revision_id, parent_rule_id, child_rule_id, operation,
         trigger_event_id, stable_dumps(list(refs)),
         stable_dumps(validation or {}), created_at or tehm_db.now_local()))
    if commit and not had_outer_transaction:
        conn.commit()
    return RuleRevisionReceipt(
        revision_id=revision_id, parent_rule_id=parent_rule_id,
        child_rule_id=child_rule_id, operation=operation,
        trigger_event_id=trigger_event_id, evidence_refs=refs)


__all__ = ["REVISION_OPERATIONS", "record_rule_revision"]
