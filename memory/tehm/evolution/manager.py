"""Online observation boundary (shadow-only)."""
from __future__ import annotations

import json
import sqlite3

from tehm.causal import build_transition_causal_fragment
from tehm.causal.mechanism import load_transition_facts, mechanism_signature
from tehm.causal.path_builder import validate_persisted_path_row

from .conflict import detect_conflicts
from .consolidation import decide_consolidation
from .events import append_memory_event
from .incremental_crystallize import preview_affected_groups
from .novelty import detect_novelty
from .receipts import OnlineMemoryReceipt
from .triggers import evaluate_consolidation_trigger


def _membership(conn: sqlite3.Connection, transition_id: str,
                campaign_id: str) -> tuple[bool, str]:
    row = conn.execute(
        """SELECT learner_eligible, split FROM tehm_dataset_membership
             WHERE transition_id=? AND campaign_id=?""",
        (transition_id, campaign_id)).fetchone()
    if row is None:
        raise ValueError("online observation requires explicit dataset membership")
    split = str(row["split"])
    # The split is part of the learner authority predicate.  This protects
    # the online lane from contradictory rows created by old/direct-SQL
    # writers, even when learner_eligible is set to 1.
    return bool(row["learner_eligible"] and split == "training"), split


def _affected_rule_ids(conn: sqlite3.Connection,
                       transition_id: str) -> tuple[str, ...]:
    """Resolve rules whose episode-owned witnesses include this transition.

    A rule is affected only when its immutable source witness names the
    transition.  Matching by family/effect alone would make an online event
    claim to revise unrelated rules and would blur revision lineage.
    """
    rows = conn.execute(
        """SELECT DISTINCT rs.rule_id
             FROM tehm_rule_sources rs
             JOIN tehm_episode_steps es ON es.episode_id=rs.episode_id
            WHERE es.transition_id=?
            ORDER BY rs.rule_id""", (transition_id,)).fetchall()
    return tuple(str(row["rule_id"]) for row in rows if row["rule_id"])


def _affected_path_ids(
        conn: sqlite3.Connection, transition_id: str, *,
        mechanism_family: str, compatibility_profile: str | None,
        campaign_id: str) -> tuple[str, ...]:
    """Resolve and replay-check shadow paths containing this transition.

    Path rows are derived and therefore cannot be trusted merely because the
    source list parses.  A matching row is fully replay-validated before its
    ID is exposed in the online receipt; corruption fails closed instead of
    producing a misleading affected-path witness.
    """
    rows = conn.execute(
        """SELECT * FROM tehm_causal_paths
            WHERE mechanism_family=? AND compatibility_profile IS ?
              AND status != 'retired'
            ORDER BY path_id""",
        (mechanism_family, compatibility_profile)).fetchall()
    affected: list[str] = []
    for row in rows:
        try:
            source_ids = json.loads(row["source_transitions_json"] or "[]")
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError(
                "online affected causal path source witness is malformed") from exc
        if not isinstance(source_ids, list):
            raise ValueError(
                "online affected causal path source witness is malformed")
        if transition_id not in {str(value) for value in source_ids}:
            continue
        validate_persisted_path_row(row, conn)
        # The path validator checks the campaign carried by its own witness;
        # this explicit comparison keeps the online event bound to the
        # campaign requested by its caller as well.
        try:
            support = json.loads(row["support_json"] or "{}")
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError(
                "online affected causal path support is malformed") from exc
        campaigns = support.get("source_campaigns") if isinstance(support, dict) else None
        if not isinstance(campaigns, list) or campaigns != [campaign_id]:
            raise ValueError("online affected causal path campaign mismatch")
        affected.append(str(row["path_id"]))
    return tuple(affected)


def observe_transition(conn: sqlite3.Connection, transition_id: str,
                       campaign_id: str = "live",
                       *, created_at: str | None = None) -> OnlineMemoryReceipt:
    """Record an eligible transition and its causal fragment in shadow only.

    This operation deliberately does not crystallize rules or alter lifecycle
    status.  It emits a deterministic event chain that a later gated manager
    may use to trigger affected-group consolidation.
    """
    learner_eligible, split = _membership(conn, transition_id, campaign_id)
    # Observation creates a causal fragment and a linked event chain as one
    # derived shadow update.  A later novelty/conflict/preview failure must
    # not leave an orphaned prefix that falsely appears to be a complete
    # online observation.  Preserve an outer transaction owned by the caller.
    had_outer_transaction = conn.in_transaction
    savepoint = "tehm_online_observe_v1"
    conn.execute(f"SAVEPOINT {savepoint}")
    savepoint_active = True

    def emit(**kwargs):
        return append_memory_event(conn, commit=False, **kwargs)

    try:
        facts = load_transition_facts(conn, transition_id)
        signature = mechanism_signature(facts)
        capture_event = emit(
            event_type="TRANSITION_CAPTURED", source_type="transition",
            source_id=transition_id, campaign_id=campaign_id,
            learner_eligible=learner_eligible,
            payload={"split": split, "mechanism_signature": signature},
            created_at=created_at)
        fragment = build_transition_causal_fragment(
            conn, transition_id, campaign_id=campaign_id, commit=False)
        affected_rule_ids = _affected_rule_ids(conn, transition_id)
        affected_path_ids = _affected_path_ids(
            conn, transition_id, mechanism_family=fragment.mechanism_family,
            compatibility_profile=fragment.compatibility_profile,
            campaign_id=campaign_id)
        fragment_event = emit(
            event_type="CAUSAL_FRAGMENT_CREATED", source_type="transition",
            source_id=transition_id, campaign_id=campaign_id,
            learner_eligible=learner_eligible,
            payload={"fragment_node_ids": list(fragment.node_ids),
                     "fragment_edge_ids": list(fragment.edge_ids),
                     "evidence_level": fragment.evidence_level,
                     "mechanism_signature": signature,
                     "affected_rule_ids": list(affected_rule_ids),
                     "affected_path_ids": list(affected_path_ids)},
            created_at=created_at)
        novelty_result = detect_novelty(conn, transition_id, campaign_id=campaign_id)
        novelty = novelty_result["status"]
        novelty_event = emit(
                event_type=novelty, source_type="causal_fragment",
                source_id=fragment.node_ids[0], campaign_id=campaign_id,
                learner_eligible=learner_eligible,
                payload={"mechanism_family": fragment.mechanism_family,
                         "mechanism_signature": signature,
                         "affected_rule_ids": list(affected_rule_ids),
                         "affected_path_ids": list(affected_path_ids)},
                created_at=created_at) \
            if novelty in {"NOVEL_MECHANISM"} else None
        conflict = detect_conflicts(conn, transition_id, campaign_id=campaign_id)
        conflict_event = None
        harmful_event = None
        if conflict.has_conflict:
            conflict_event = emit(
                event_type="RULE_CONFLICT", source_type="transition",
                source_id=transition_id, campaign_id=campaign_id,
                learner_eligible=learner_eligible,
                payload={**conflict.to_dict(),
                         "affected_rule_ids": list(affected_rule_ids),
                         "affected_path_ids": list(affected_path_ids)},
                created_at=created_at)
        if any(edge.relation_type == "CREATES" for edge in fragment.edges):
            harmful_event = emit(
                event_type="RULE_HARMFUL", source_type="transition",
                source_id=transition_id, campaign_id=campaign_id,
                learner_eligible=learner_eligible,
                payload={"outcome": "harmful_or_nonpositive",
                         "transition_id": transition_id,
                         "affected_rule_ids": list(affected_rule_ids),
                         "affected_path_ids": list(affected_path_ids)},
                created_at=created_at)
        trigger = evaluate_consolidation_trigger(
            conn, transition_id, campaign_id=campaign_id,
            learner_eligible=learner_eligible, novelty=novelty,
            conflict=conflict)
        trigger_event = None
        if trigger.triggered:
            trigger_event = emit(
                event_type="CONSOLIDATION_TRIGGERED", source_type="transition",
                source_id=transition_id, campaign_id=campaign_id,
                learner_eligible=learner_eligible,
                payload={**trigger.to_dict(),
                         "mechanism_signature": signature,
                         "affected_rule_ids": list(affected_rule_ids),
                         "affected_path_ids": list(affected_path_ids)},
                created_at=created_at)
        preview = None
        decision = decide_consolidation(trigger)
        proposal_event = None
        if trigger.triggered:
            preview = preview_affected_groups(
                conn, [transition_id], campaign_id=campaign_id)
            decision = decide_consolidation(trigger, preview)
            proposal_event = emit(
                event_type="RULE_REVISION_PROPOSED", source_type="transition",
                source_id=transition_id, campaign_id=campaign_id,
                learner_eligible=learner_eligible,
                payload={"trigger_event_id": trigger_event.event_id
                         if trigger_event else None,
                         "preview": preview.to_dict(),
                         "decision": decision.to_dict(),
                         "mechanism_signature": signature,
                         "affected_rule_ids": list(affected_rule_ids),
                         "affected_path_ids": list(affected_path_ids),
                         "authority": "shadow_only"}, created_at=created_at)
        events = tuple(event for event in (
            capture_event, fragment_event, novelty_event, conflict_event,
            harmful_event, trigger_event, proposal_event) if event is not None)
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        savepoint_active = False
        if not had_outer_transaction:
            conn.commit()
        return OnlineMemoryReceipt(
            transition_id=transition_id, campaign_id=campaign_id,
            learner_eligible=learner_eligible, fragment=fragment,
            mechanism_signature=signature,
            affected_rule_ids=affected_rule_ids,
            affected_path_ids=affected_path_ids,
            events=events, novelty=novelty,
            consolidation_triggered=trigger.triggered, path_id=None,
            trigger_reasons=trigger.reasons,
            affected_effect_keys=trigger.affected_effect_keys,
            trigger_event_id=trigger_event.event_id if trigger_event else None,
            consolidation_preview=preview,
            consolidation_operation=decision.operation,
            consolidation_decision=decision)
    except Exception:
        if savepoint_active:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise


__all__ = ["observe_transition"]
