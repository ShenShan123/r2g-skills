"""Hash-chained online memory events.

Events are an audit/evolution log, not a second canonical evidence store.  A
learner event must name an explicit dataset campaign and inherits its
``learner_eligible`` bit; no event can turn held-out evidence into training.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3

from tehm import db as tehm_db
from tehm.ids import stable_dumps

from .receipts import MemoryEventReceipt

EVENT_TYPES = frozenset({
    "TRANSITION_CAPTURED", "CAUSAL_FRAGMENT_CREATED", "NOVEL_MECHANISM",
    "SUPPORT_INCREASED", "RULE_CONFLICT", "RULE_HARMFUL", "UTILITY_DRIFT",
    "CONSOLIDATION_TRIGGERED", "RULE_REVISION_PROPOSED",
    "ASSET_GAP_DETECTED", "CAPABILITY_EVIDENCE_ADDED",
})


def _event_digest(payload: dict) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(payload).encode()).hexdigest()


def _event_id(event_digest: str) -> str:
    return "event_" + event_digest.split(":", 1)[1][:24]


def _validate_event_row(row: sqlite3.Row) -> None:
    """Validate the content-addressed identity of a stored event row.

    Event rows are append-only derived evidence.  A direct SQL edit must not
    be silently accepted by a later idempotent append, even when the edited
    row still matches the natural event lookup (same source/payload).
    """
    try:
        payload = json.loads(row["payload_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("memory event payload is not valid JSON") from exc
    identity = {
        "event_type": row["event_type"],
        "source_type": row["source_type"],
        "source_id": row["source_id"],
        "campaign_id": row["campaign_id"],
        "learner_eligible": bool(row["learner_eligible"]),
        "payload": payload,
        "previous_event_digest": row["previous_event_digest"],
    }
    digest = _event_digest(identity)
    if row["event_digest"] != digest:
        raise ValueError("memory event replay conflicts with event_digest")
    if row["event_id"] != _event_id(digest):
        raise ValueError("memory event replay conflicts with event_id")


def _previous(conn: sqlite3.Connection, campaign_id: str | None) -> str | None:
    rows = conn.execute(
        """SELECT event_digest, previous_event_digest
             FROM tehm_memory_events WHERE campaign_id IS ?""",
        (campaign_id,)).fetchall()
    if not rows:
        return None
    # The predecessor pointers, rather than wall-clock ordering, define the
    # chain.  This remains correct when an isolated replay pins several events
    # to the same timestamp (and fail-closes on a branched/corrupt chain).
    digests = {str(row["event_digest"]) for row in rows}
    referenced = {str(row["previous_event_digest"]) for row in rows
                  if row["previous_event_digest"]}
    heads = sorted(digests - referenced)
    if len(heads) != 1:
        raise ValueError("memory event chain has no unique head")
    return heads[0]


def _learner_source_transition_ids(
    conn: sqlite3.Connection, *, source_type: str, source_id: str,
) -> tuple[str, ...]:
    """Resolve the canonical transition(s) behind a learner event source.

    Online events are derived state, but a learner-eligible bit must still be
    justified by a canonical transition.  Keeping this mapping explicit avoids
    a generic event writer becoming a way to mark arbitrary model output as
    learner evidence.
    """
    if source_type == "transition":
        return (source_id,)
    if source_type == "causal_fragment":
        row = conn.execute(
            """SELECT owner_id FROM tehm_causal_nodes
                WHERE causal_node_id=? AND owner_type='transition'""",
            (source_id,),
        ).fetchone()
        return (str(row["owner_id"]),) if row and row["owner_id"] else ()
    if source_type == "activation":
        row = conn.execute(
            """SELECT produced_transition_id FROM tehm_activations
                WHERE activation_id=?""", (source_id,)
        ).fetchone()
        return (str(row["produced_transition_id"]),) if (
            row and row["produced_transition_id"]
        ) else ()
    return ()


def _verify_learner_source(
    conn: sqlite3.Connection, *, source_type: str, source_id: str,
    campaign_id: str,
) -> None:
    transition_ids = _learner_source_transition_ids(
        conn, source_type=source_type, source_id=source_id)
    if not transition_ids:
        raise ValueError(
            "learner-eligible event source has no canonical transition witness")
    placeholders = ",".join("?" for _ in transition_ids)
    rows = conn.execute(
        f"""SELECT transition_id, split, learner_eligible
              FROM tehm_dataset_membership
             WHERE campaign_id=? AND transition_id IN ({placeholders})""",
        (campaign_id, *transition_ids),
    ).fetchall()
    by_id = {str(row["transition_id"]): row for row in rows}
    if len(by_id) != len(set(transition_ids)) or any(
            str(row_id) not in by_id or
            str(by_id[str(row_id)]["split"]) != "training" or
            not bool(by_id[str(row_id)]["learner_eligible"])
            for row_id in transition_ids):
        raise ValueError(
            "learner-eligible event source is not training learner evidence")


def append_memory_event(
    conn: sqlite3.Connection,
    *,
    event_type: str,
    source_type: str,
    source_id: str,
    payload: dict | None = None,
    campaign_id: str | None = None,
    learner_eligible: bool = False,
    created_at: str | None = None,
    commit: bool = True,
) -> MemoryEventReceipt:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown online memory event type: {event_type!r}")
    if not source_type or not source_id:
        raise ValueError("event source_type and source_id are required")
    if learner_eligible and not campaign_id:
        raise ValueError("learner-eligible event requires campaign_id")
    if learner_eligible:
        _verify_learner_source(
            conn, source_type=source_type, source_id=source_id,
            campaign_id=str(campaign_id))
    chain = verify_event_chain(conn, campaign_id=campaign_id)
    if not chain.get("ok"):
        raise ValueError(
            "memory event replay conflicts: existing event chain is invalid")
    payload = dict(payload or {})
    payload_json = stable_dumps(payload)
    existing = conn.execute(
        """SELECT * FROM tehm_memory_events
             WHERE event_type=? AND source_type=? AND source_id=?
               AND campaign_id IS ? AND learner_eligible=? AND payload_json=?
             LIMIT 1""",
        (event_type, source_type, source_id, campaign_id,
         int(bool(learner_eligible)), payload_json)).fetchone()
    if existing is not None:
        _validate_event_row(existing)
        return MemoryEventReceipt(
            event_id=existing["event_id"], event_type=existing["event_type"],
            source_type=existing["source_type"], source_id=existing["source_id"],
            campaign_id=existing["campaign_id"],
            learner_eligible=bool(existing["learner_eligible"]),
            previous_event_digest=existing["previous_event_digest"],
            event_digest=existing["event_digest"])
    previous = _previous(conn, campaign_id)
    identity = {
        "event_type": event_type,
        "source_type": source_type,
        "source_id": source_id,
        "campaign_id": campaign_id,
        "learner_eligible": bool(learner_eligible),
        "payload": payload,
        "previous_event_digest": previous,
    }
    event_digest = _event_digest(identity)
    event_id = _event_id(event_digest)
    # A prefix collision is extraordinarily unlikely, but an existing row
    # with this ID must still be treated as immutable rather than ignored.
    colliding = conn.execute(
        "SELECT * FROM tehm_memory_events WHERE event_id=?", (event_id,)
    ).fetchone()
    if colliding is not None:
        try:
            _validate_event_row(colliding)
        except ValueError as exc:
            raise ValueError(
                "memory event replay conflicts with existing event ID"
            ) from exc
        raise ValueError("memory event ID collision")
    # Use TEHM's single timestamp source so isolated replays can pin event
    # materialization (and therefore the derived DB digest) without patching
    # the standard library.  Event identity remains content-addressed and does
    # not include this cosmetic timestamp.
    stamp = created_at or tehm_db.now_local()
    had_outer_transaction = conn.in_transaction
    conn.execute(
        """INSERT OR IGNORE INTO tehm_memory_events
           (event_id, event_type, source_type, source_id, campaign_id,
            learner_eligible, payload_json, previous_event_digest,
            event_digest, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (event_id, event_type, source_type, source_id, campaign_id,
         int(bool(learner_eligible)), payload_json, previous,
         event_digest, stamp))
    if commit and not had_outer_transaction:
        conn.commit()
    return MemoryEventReceipt(
        event_id=event_id, event_type=event_type, source_type=source_type,
        source_id=source_id, campaign_id=campaign_id,
        learner_eligible=bool(learner_eligible),
        previous_event_digest=previous, event_digest=event_digest)


def verify_event_chain(conn: sqlite3.Connection,
                       *, campaign_id: str | None = None) -> dict:
    """Verify digests and predecessor pointers for one campaign chain.

    Verification follows the explicit predecessor links instead of sorting by
    ``created_at``.  Timestamps are audit metadata and may legitimately tie in
    deterministic staging replays; they must not change chain topology.
    """
    rows = conn.execute(
        """SELECT * FROM tehm_memory_events WHERE campaign_id IS ?""",
        (campaign_id,)).fetchall()
    if not rows:
        return {"ok": True, "events": 0, "head_digest": None}
    by_digest = {str(row["event_digest"]): row for row in rows}
    if len(by_digest) != len(rows):
        return {"ok": False, "events": len(rows),
                "reason": "duplicate event digest"}
    # A root is the event with no predecessor.  (The unreferenced digest is
    # the *tail*, which is what ``_previous`` returns when appending.)
    roots = sorted(str(row["event_digest"]) for row in rows
                   if row["previous_event_digest"] is None)
    if len(roots) != 1:
        return {"ok": False, "events": len(rows),
                "reason": "event chain does not have one root"}
    children: dict[str, list[str]] = {}
    for row in rows:
        previous = row["previous_event_digest"]
        if previous is not None and str(previous) not in by_digest:
            return {"ok": False, "events": len(rows),
                    "bad_event_id": row["event_id"],
                    "reason": "event predecessor is missing"}
        if previous is not None:
            children.setdefault(str(previous), []).append(str(row["event_digest"]))

    previous = None
    visited: set[str] = set()
    digest = roots[0]
    while digest:
        if digest in visited:
            return {"ok": False, "events": len(rows),
                    "reason": "event chain contains a cycle"}
        visited.add(digest)
        row = by_digest[digest]
        if bool(row["learner_eligible"]):
            try:
                _verify_learner_source(
                    conn, source_type=str(row["source_type"]),
                    source_id=str(row["source_id"]),
                    campaign_id=str(row["campaign_id"]),
                )
            except ValueError as exc:
                return {"ok": False, "events": len(rows),
                        "bad_event_id": row["event_id"],
                        "reason": str(exc)}
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError):
            return {"ok": False, "events": len(rows),
                    "bad_event_id": row["event_id"],
                    "reason": "event payload is not valid JSON"}
        identity = {
            "event_type": row["event_type"], "source_type": row["source_type"],
            "source_id": row["source_id"], "campaign_id": row["campaign_id"],
            "learner_eligible": bool(row["learner_eligible"]),
            "payload": payload, "previous_event_digest": previous,
        }
        digest = _event_digest(identity)
        if row["event_id"] != _event_id(digest):
            return {"ok": False, "events": len(rows),
                    "bad_event_id": row["event_id"],
                    "reason": "event ID is not content-addressed"}
        if row["previous_event_digest"] != previous or row["event_digest"] != digest:
            return {"ok": False, "events": len(rows), "bad_event_id": row["event_id"]}
        next_ids = children.get(digest, [])
        if len(next_ids) > 1:
            return {"ok": False, "events": len(rows),
                    "bad_event_id": row["event_id"],
                    "reason": "event chain branches"}
        previous = digest
        digest = next_ids[0] if next_ids else ""
    if len(visited) != len(rows):
        return {"ok": False, "events": len(rows),
                "reason": "event chain has disconnected events"}
    return {"ok": True, "events": len(rows), "head_digest": previous}


__all__ = ["EVENT_TYPES", "append_memory_event", "verify_event_chain"]
