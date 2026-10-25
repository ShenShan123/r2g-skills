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
from tehm.dataset import (normalize_stored_learner_bool,
                          require_learner_bool, validate_membership_row)
from tehm.ids import stable_dumps

from .receipts import MemoryEventReceipt
from tehm.state.shift_receipts import StateShiftReceipt
from .verification import require_verified_transition

EVENT_TYPES = frozenset({
    "TRANSITION_CAPTURED", "CAUSAL_FRAGMENT_CREATED", "NOVEL_MECHANISM",
    "SUPPORT_INCREASED", "RULE_CONFLICT", "RULE_HARMFUL", "UTILITY_DRIFT",
    "CONSOLIDATION_TRIGGERED", "RULE_REVISION_PROPOSED",
    "ASSET_GAP_DETECTED", "CAPABILITY_EVIDENCE_ADDED",
    # P4 localized evolution vocabulary.  Events are append-only audit
    # primitives; observe_transition currently stores these receipts in its
    # existing immutable snapshot to preserve the legacy event cardinality.
    "EXPERIENCE_VALUED", "STATE_RESOLVED", "KNOWLEDGE_CONFLICT",
    "KNOWLEDGE_REVISION_PROPOSED", "KNOWLEDGE_SUPERSEDED",
    "KNOWLEDGE_INVALIDATED", "ASSET_INTERFERENCE", "ASSET_REVISION_PROPOSED",
    "CAPABILITY_GAP_UPDATED", "CAPABILITY_REGRESSION_OBSERVED",
    "MEMORY_ABSTAINED", "NO_SKILL_SELECTED", "STATE_SHIFT_OBSERVED",
})


def _event_digest(payload: dict) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(payload).encode()).hexdigest()


def _event_id(event_digest: str) -> str:
    return "event_" + event_digest.split(":", 1)[1][:24]


def _event_payload(raw: object) -> dict:
    """Decode the event payload as an object, never as an arbitrary JSON value."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("memory event payload JSON is empty")
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("memory event payload is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("memory event payload must decode to object")
    return payload


def _validate_event_row(row: sqlite3.Row) -> None:
    """Validate the content-addressed identity of a stored event row.

    Event rows are append-only derived evidence.  A direct SQL edit must not
    be silently accepted by a later idempotent append, even when the edited
    row still matches the natural event lookup (same source/payload).
    """
    payload = _event_payload(row["payload_json"])
    event_eligible = normalize_stored_learner_bool(row["learner_eligible"])
    identity = {
        "event_type": row["event_type"],
        "source_type": row["source_type"],
        "source_id": row["source_id"],
        "campaign_id": row["campaign_id"],
        "learner_eligible": event_eligible,
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
    if len(by_id) != len(set(transition_ids)):
        raise ValueError(
            "learner-eligible event source is not training learner evidence")
    for row_id in transition_ids:
        try:
            eligible, split = validate_membership_row(by_id[str(row_id)])
        except ValueError as exc:
            raise ValueError(
                "learner-eligible event source has malformed membership") from exc
        if split != "training" or not eligible:
            raise ValueError(
                "learner-eligible event source is not training learner evidence")
        # Membership is necessary but not sufficient.  Re-load the immutable
        # canonical transition and require a complete executable oracle here
        # as well as in observe_transition(); otherwise a caller could bypass
        # the online manager by writing a learner event directly.
        require_verified_transition(conn, str(row_id))


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
    learner_eligible = require_learner_bool(learner_eligible)
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
    if payload is None:
        payload = {}
    elif not isinstance(payload, dict):
        raise ValueError("memory event payload must be a mapping")
    else:
        payload = dict(payload)
    payload_json = stable_dumps(payload)
    existing = conn.execute(
        """SELECT * FROM tehm_memory_events
             WHERE event_type=? AND source_type=? AND source_id=?
               AND campaign_id IS ? AND learner_eligible=? AND payload_json=?
             LIMIT 1""",
        (event_type, source_type, source_id, campaign_id,
         int(learner_eligible), payload_json)).fetchone()
    if existing is not None:
        _validate_event_row(existing)
        return MemoryEventReceipt(
            event_id=existing["event_id"], event_type=existing["event_type"],
            source_type=existing["source_type"], source_id=existing["source_id"],
            campaign_id=existing["campaign_id"],
            learner_eligible=normalize_stored_learner_bool(
                existing["learner_eligible"]),
            previous_event_digest=existing["previous_event_digest"],
            event_digest=existing["event_digest"])
    previous = _previous(conn, campaign_id)
    identity = {
        "event_type": event_type,
        "source_type": source_type,
        "source_id": source_id,
        "campaign_id": campaign_id,
        "learner_eligible": learner_eligible,
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
         int(learner_eligible), payload_json, previous,
         event_digest, stamp))
    if commit and not had_outer_transaction:
        conn.commit()
    return MemoryEventReceipt(
        event_id=event_id, event_type=event_type, source_type=source_type,
        source_id=source_id, campaign_id=campaign_id,
        learner_eligible=learner_eligible,
        previous_event_digest=previous, event_digest=event_digest)


def append_state_shift_observation(
    conn: sqlite3.Connection,
    receipt: StateShiftReceipt,
    *,
    transition_id: str,
    campaign_id: str,
    learner_eligible: bool,
    routing_decision=None,
    created_at: str | None = None,
    commit: bool = True,
) -> MemoryEventReceipt:
    """Append one explicit ``STATE_SHIFT_OBSERVED`` shadow event.

    A state-shift receipt is produced by the deterministic router, but it is
    not itself a learner event.  The event writer therefore requires an
    explicit canonical transition anchor and delegates learner-partition and
    complete-oracle checks to :func:`append_memory_event`.  This function is
    an audit bridge only: it never changes a support envelope, Knowledge
    claim, lifecycle status, or production authority.
    """
    if not isinstance(receipt, StateShiftReceipt):
        raise TypeError("state shift observation requires StateShiftReceipt")
    if receipt.reason != "STATE_SHIFT" or receipt.transferable is not False:
        raise ValueError(
            "state shift observation requires a non-transferable STATE_SHIFT receipt")
    if type(transition_id) is not str or not transition_id.strip():
        raise ValueError("state shift observation transition_id is required")
    if type(campaign_id) is not str or not campaign_id.strip():
        raise ValueError("state shift observation campaign_id is required")
    if type(learner_eligible) is not bool:
        raise ValueError("state shift observation learner_eligible must be boolean")
    route_payload = None
    if routing_decision is not None:
        from contracts import MemoryRoutingDecision

        if not isinstance(routing_decision, MemoryRoutingDecision):
            raise TypeError(
                "state shift observation routing_decision must be MemoryRoutingDecision")
        if (routing_decision.decision != "NO_SKILL" or
                routing_decision.no_skill_reason != "STATE_SHIFT"):
            raise ValueError(
                "state shift observation route must be NO_SKILL/STATE_SHIFT")
        if routing_decision.state_shift_receipt_id != receipt.receipt_id:
            raise ValueError(
                "state shift observation route receipt ID does not match")
        if routing_decision.resolved_state_id != receipt.current_resolution_id:
            raise ValueError(
                "state shift observation route resolution does not match")
        if routing_decision.state_shift_receipt is None:
            raise ValueError(
                "state shift observation route requires full replayable receipt")
        try:
            routed_receipt = StateShiftReceipt.from_dict(
                routing_decision.state_shift_receipt)
        except (TypeError, ValueError, KeyError) as exc:
            raise ValueError(
                "state shift observation route receipt payload is malformed") from exc
        if routed_receipt.to_dict() != receipt.to_dict():
            raise ValueError(
                "state shift observation route receipt payload does not match")
        route_payload = {
            **routing_decision.to_dict(),
            "decision_digest": routing_decision.decision_digest,
            "routing_receipt_id": routing_decision.routing_receipt_id,
        }
    payload = {
        "state_shift_receipt": receipt.to_dict(),
        "state_shift_receipt_id": receipt.receipt_id,
        "no_skill_reason": "STATE_SHIFT",
        "evolution_reason": "STATE_SHIFT_OBSERVED",
    }
    if route_payload is not None:
        payload["routing_decision"] = route_payload
    return append_memory_event(
        conn,
        event_type="STATE_SHIFT_OBSERVED",
        source_type="transition",
        source_id=transition_id.strip(),
        campaign_id=campaign_id.strip(),
        learner_eligible=learner_eligible,
        payload=payload,
        created_at=created_at,
        commit=commit,
    )


def append_routed_state_shift_observation(
    conn: sqlite3.Connection,
    receipt: StateShiftReceipt,
    routing_decision,
    *,
    transition_id: str,
    campaign_id: str,
    learner_eligible: bool,
    created_at: str | None = None,
    commit: bool = True,
) -> MemoryEventReceipt:
    """Bridge an actual ``NO_SKILL/STATE_SHIFT`` route into the event log.

    This is the preferred 8A.9 path.  The generic append function remains
    available for replay/migration, while this wrapper prevents a caller from
    manufacturing a state-shift teaching signal without a matching router
    receipt.  The route is stored as typed payload evidence and is revalidated
    by :func:`load_state_shift_observations`.
    """
    return append_state_shift_observation(
        conn, receipt, transition_id=transition_id, campaign_id=campaign_id,
        learner_eligible=learner_eligible, routing_decision=routing_decision,
        created_at=created_at, commit=commit,
    )


def load_state_shift_observations(
    conn: sqlite3.Connection,
    *,
    campaign_id: str,
    knowledge_object_id: str | None = None,
) -> tuple[tuple[MemoryEventReceipt, StateShiftReceipt], ...]:
    """Replay validated state-shift events without inferring new evidence."""
    if type(campaign_id) is not str or not campaign_id.strip():
        raise ValueError("state shift observation campaign_id is required")
    chain = verify_event_chain(conn, campaign_id=campaign_id.strip())
    if not chain.get("ok"):
        raise ValueError("state shift observation event chain is invalid")
    rows = conn.execute(
        """SELECT * FROM tehm_memory_events
             WHERE event_type='STATE_SHIFT_OBSERVED' AND campaign_id=?
             ORDER BY event_digest""", (campaign_id.strip(),)
    ).fetchall()
    observations: list[tuple[MemoryEventReceipt, StateShiftReceipt]] = []
    for row in rows:
        _validate_event_row(row)
        try:
            payload = _event_payload(row["payload_json"])
            receipt = StateShiftReceipt.from_dict(payload["state_shift_receipt"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("state shift observation payload is malformed") from exc
        if (payload.get("state_shift_receipt_id") != receipt.receipt_id or
                payload.get("no_skill_reason") != "STATE_SHIFT" or
                payload.get("evolution_reason") != "STATE_SHIFT_OBSERVED" or
                receipt.reason != "STATE_SHIFT"):
            raise ValueError("state shift observation payload conflicts with receipt")
        route_payload = payload.get("routing_decision")
        if route_payload is not None:
            try:
                from contracts import MemoryRoutingDecision

                route = MemoryRoutingDecision.from_dict(route_payload)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "state shift observation routing payload is malformed") from exc
            if (route.decision != "NO_SKILL" or
                    route.no_skill_reason != "STATE_SHIFT" or
                    route.state_shift_receipt_id != receipt.receipt_id or
                    route.resolved_state_id != receipt.current_resolution_id or
                    route_payload.get("routing_receipt_id") != route.routing_receipt_id):
                raise ValueError(
                    "state shift observation routing payload conflicts with receipt")
        if knowledge_object_id is not None and receipt.knowledge_object_id != knowledge_object_id:
            continue
        observations.append((
            MemoryEventReceipt(
                event_id=row["event_id"], event_type=row["event_type"],
                source_type=row["source_type"], source_id=row["source_id"],
                campaign_id=row["campaign_id"],
                learner_eligible=normalize_stored_learner_bool(row["learner_eligible"]),
                previous_event_digest=row["previous_event_digest"],
                event_digest=row["event_digest"]),
            receipt,
        ))
    return tuple(observations)


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
        try:
            event_eligible = normalize_stored_learner_bool(
                row["learner_eligible"])
        except ValueError:
            return {"ok": False, "events": len(rows),
                    "bad_event_id": row["event_id"],
                    "reason": "event learner_eligible type is invalid"}
        if event_eligible:
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
            payload = _event_payload(row["payload_json"])
        except ValueError as exc:
            return {"ok": False, "events": len(rows),
                    "bad_event_id": row["event_id"],
                    "reason": str(exc)}
        identity = {
            "event_type": row["event_type"], "source_type": row["source_type"],
            "source_id": row["source_id"], "campaign_id": row["campaign_id"],
            "learner_eligible": event_eligible,
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


__all__ = [
    "EVENT_TYPES", "append_memory_event", "append_state_shift_observation",
    "append_routed_state_shift_observation",
    "load_state_shift_observations", "verify_event_chain",
]
