"""Online observation boundary (shadow-only)."""
from __future__ import annotations

import json
import sqlite3

from tehm.causal import build_transition_causal_fragment
from tehm.causal.mechanism import load_transition_facts, mechanism_signature
from tehm.causal.path_builder import validate_persisted_path_row
from tehm.causal.witness import parse_source_transition_ids
from tehm.dataset import (normalize_stored_learner_bool,
                          validate_membership_row)

from .conflict import detect_conflicts
from .consolidation import (CONSOLIDATION_OPERATIONS,
                             ConsolidationDecisionReceipt,
                             decide_consolidation)
from .events import append_memory_event, verify_event_chain
from .incremental_crystallize import preview_affected_groups
from .novelty import detect_novelty
from .receipts import (IncrementalCrystallizationReceipt,
                       MemoryEventReceipt, OnlineMemoryReceipt,
                       ExperienceValueReceipt)
from .triggers import evaluate_consolidation_trigger
from .verification import require_verified_execution
from .value import evaluate_experience_value, load_experience_value, record_experience_value


def _membership(conn: sqlite3.Connection, transition_id: str,
                campaign_id: str) -> tuple[bool, str]:
    row = conn.execute(
        """SELECT learner_eligible, split FROM tehm_dataset_membership
             WHERE transition_id=? AND campaign_id=?""",
        (transition_id, campaign_id)).fetchone()
    if row is None:
        raise ValueError("online observation requires explicit dataset membership")
    # The split is part of the learner authority predicate.  This protects
    # the online lane from contradictory rows created by old/direct-SQL
    # writers, even when learner_eligible is set to 1.  The shared reader
    # additionally rejects weakly typed copied rows.
    try:
        eligible, split = validate_membership_row(row)
    except ValueError as exc:
        # A legacy/direct-SQL writer may have marked an audit split with a
        # learner bit.  Keep the online lane fail-closed (audit-only) for
        # this known contradiction while still surfacing weakly typed rows.
        if str(exc) != "non-training dataset membership cannot be learner-eligible":
            raise
        split = row["split"]
        return False, split
    return eligible and split == "training", split


def _affected_rule_ids(conn: sqlite3.Connection,
                       transition_id: str, *,
                       campaign_id: str) -> tuple[str, ...]:
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
    affected: list[str] = []
    for raw in rows:
        rule_id = str(raw["rule_id"] or "")
        if not rule_id:
            raise ValueError("online affected rule witness has no rule ID")
        if conn.execute(
                "SELECT 1 FROM tehm_rules WHERE rule_id=?", (rule_id,)
        ).fetchone() is None:
            raise ValueError("online affected rule witness references missing rule")
        source_rows = conn.execute(
            """SELECT episode_id, source_substitution_json
                 FROM tehm_rule_sources
                WHERE rule_id=? ORDER BY episode_id""", (rule_id,)
        ).fetchall()
        if not source_rows:
            raise ValueError("online affected rule witness has no source rows")
        source_ids: set[str] = set()
        owns_transition = False
        for source_row in source_rows:
            episode_id = str(source_row["episode_id"] or "")
            try:
                substitutions = json.loads(
                    source_row["source_substitution_json"] or "{}")
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError(
                    "online affected rule source witness is malformed") from exc
            if (not isinstance(substitutions, dict) or not substitutions or
                    any(not isinstance(key, str) or not key
                        for key in substitutions)):
                raise ValueError("online affected rule source witness is malformed")
            episode_steps = conn.execute(
                """SELECT transition_id FROM tehm_episode_steps
                    WHERE episode_id=?""", (episode_id,)).fetchall()
            step_ids = {str(step["transition_id"]) for step in episode_steps}
            row_ids = {str(key) for key in substitutions}
            if not row_ids <= step_ids:
                raise ValueError(
                    "online affected rule source is outside episode witness")
            owns_transition = owns_transition or transition_id in row_ids
            source_ids.update(row_ids)
        if not owns_transition:
            # The initial join selected the rule through an episode step, so a
            # source map omitting that transition is a direct provenance
            # contradiction rather than an unrelated rule.
            raise ValueError(
                "online affected rule source omits observed transition")
        placeholders = ",".join("?" for _ in source_ids)
        memberships = conn.execute(
            f"""SELECT transition_id, split, learner_eligible
                   FROM tehm_dataset_membership
                  WHERE campaign_id=? AND transition_id IN ({placeholders})""",
            (campaign_id, *sorted(source_ids))).fetchall()
        by_transition = {str(item["transition_id"]): item
                         for item in memberships}
        if len(by_transition) != len(source_ids):
            raise ValueError(
                "online affected rule source is not target-campaign training evidence")
        for source_id in source_ids:
            try:
                eligible, split = validate_membership_row(by_transition[source_id])
            except ValueError as exc:
                raise ValueError(
                    "online affected rule source has malformed membership") from exc
            if split != "training" or not eligible:
                raise ValueError(
                    "online affected rule source is not target-campaign training evidence")
        affected.append(rule_id)
    return tuple(sorted(set(affected)))


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
        source_ids, source_error = parse_source_transition_ids(
            row["source_transitions_json"])
        if source_ids is None:
            raise ValueError(
                "online affected causal path source witness is malformed"
                f" ({source_error or 'unknown'})")
        if transition_id not in source_ids:
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


def _event_receipt(row: sqlite3.Row) -> MemoryEventReceipt:
    """Convert a validated event row into the public receipt shape."""
    return MemoryEventReceipt(
        event_id=str(row["event_id"]), event_type=str(row["event_type"]),
        source_type=str(row["source_type"]), source_id=str(row["source_id"]),
        campaign_id=row["campaign_id"],
        learner_eligible=normalize_stored_learner_bool(
            row["learner_eligible"]),
        previous_event_digest=row["previous_event_digest"],
        event_digest=str(row["event_digest"]))


def _strict_optional_bool(value, *, label: str) -> bool | None:
    """Decode an optional snapshot boolean without truthy coercion."""
    if value is None:
        return None
    if type(value) is not bool:
        raise ValueError(f"online observation {label} is not boolean")
    return value


def _strict_string_tuple(payload: dict, key: str, *, label: str) -> tuple[str, ...]:
    """Decode a snapshot list of non-empty string IDs/reasons."""
    values = payload.get(key)
    if values is None:
        return ()
    if not isinstance(values, list) or any(
            type(value) is not str or not value for value in values):
        raise ValueError(f"online observation {label} is malformed")
    return tuple(values)


def _strict_digest(payload: dict, key: str, *, label: str) -> str | None:
    """Decode an optional content digest field from a replay snapshot."""
    value = payload.get(key)
    if value is None:
        return None
    if type(value) is not str or not value:
        raise ValueError(f"online observation {label} is malformed")
    return value


def _preview_from_dict(payload: dict | None) -> IncrementalCrystallizationReceipt | None:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise ValueError("online observation preview snapshot is malformed")
    groups = payload.get("affected_group_keys")
    if groups is None:
        groups = []
    if not isinstance(groups, list) or any(
            not isinstance(group, (list, tuple)) or len(group) != 2 or
            type(group[0]) is not str or not group[0] or
            (group[1] is not None and
             (type(group[1]) is not str or not group[1]))
            for group in groups):
        raise ValueError("online observation preview groups are malformed")
    transition_ids = _strict_string_tuple(
        payload, "transition_ids", label="preview transition IDs")
    effect_keys = _strict_string_tuple(
        payload, "affected_effect_keys", label="preview effect keys")
    full_rule_ids = _strict_string_tuple(
        payload, "full_rebuild_rule_ids", label="preview full-rebuild rule IDs")
    raw_before = _strict_digest(
        payload, "raw_evidence_before_digest", label="preview raw-before digest")
    raw_after = _strict_digest(
        payload, "raw_evidence_after_digest", label="preview raw-after digest")
    rules = payload.get("rules")
    if not isinstance(rules, list) or any(not isinstance(rule, dict) for rule in rules):
        raise ValueError("online observation preview rules are malformed")
    campaign_id = payload.get("campaign_id")
    mode = payload.get("mode")
    if type(campaign_id) is not str or not campaign_id:
        raise ValueError("online observation preview campaign is malformed")
    if type(mode) is not str or mode != "preview":
        raise ValueError("online observation preview mode is malformed")
    return IncrementalCrystallizationReceipt(
        campaign_id=campaign_id,
        transition_ids=transition_ids,
        affected_effect_keys=effect_keys,
        rules=tuple(rules),
        full_rebuild_equivalent=_strict_optional_bool(
            payload.get("full_rebuild_equivalent"),
            label="preview full_rebuild_equivalent"),
        full_rebuild_rule_ids=full_rule_ids,
        mode=mode,
        affected_group_keys=tuple(
            (str(group[0]), group[1]) for group in groups),
        raw_evidence_before_digest=raw_before,
        raw_evidence_after_digest=raw_after,
        raw_evidence_preserved=_strict_optional_bool(
            payload.get("raw_evidence_preserved"),
            label="preview raw_evidence_preserved"),
    )


def _decision_from_dict(payload: dict) -> ConsolidationDecisionReceipt:
    if not isinstance(payload, dict):
        raise ValueError("online observation decision snapshot is malformed")
    learner_eligible = payload.get("learner_eligible")
    if type(learner_eligible) is not bool:
        raise ValueError(
            "online observation decision learner_eligible is not boolean")
    triggered = payload.get("triggered")
    if type(triggered) is not bool:
        raise ValueError(
            "online observation decision triggered is not boolean")
    transition_id = payload.get("transition_id")
    campaign_id = payload.get("campaign_id")
    operation = payload.get("operation")
    rationale = payload.get("rationale")
    authority = payload.get("authority")
    if (type(transition_id) is not str or not transition_id or
            type(campaign_id) is not str or not campaign_id or
            type(operation) is not str or operation not in CONSOLIDATION_OPERATIONS or
            type(rationale) is not str or
            type(authority) is not str or authority != "shadow_only"):
        raise ValueError("online observation decision identity is malformed")
    reasons = _strict_string_tuple(
        payload, "reasons", label="decision reasons")
    affected_effect_keys = _strict_string_tuple(
        payload, "affected_effect_keys", label="decision effect keys")
    candidate_rule_ids = _strict_string_tuple(
        payload, "candidate_rule_ids", label="decision candidate rule IDs")
    affected_rule_ids = _strict_string_tuple(
        payload, "affected_rule_ids", label="decision affected rule IDs")
    affected_path_ids = _strict_string_tuple(
        payload, "affected_path_ids", label="decision affected path IDs")
    signature = payload.get("mechanism_signature")
    if not isinstance(signature, dict):
        raise ValueError("online observation decision mechanism signature is malformed")
    return ConsolidationDecisionReceipt(
        transition_id=transition_id,
        campaign_id=campaign_id,
        learner_eligible=learner_eligible,
        triggered=triggered,
        operation=operation,
        reasons=reasons,
        affected_effect_keys=affected_effect_keys,
        candidate_rule_ids=candidate_rule_ids,
        mechanism_signature=signature,
        affected_rule_ids=affected_rule_ids,
        affected_path_ids=affected_path_ids,
        full_rebuild_equivalent=_strict_optional_bool(
            payload.get("full_rebuild_equivalent"),
            label="decision full_rebuild_equivalent"),
        rationale=rationale,
        authority=authority,
    )


def _observation_sequence(
        *, transition_id: str, fragment, novelty: str, conflict,
        harmful: bool, trigger_event: bool) -> list[dict]:
    """Describe the expected contiguous event chain without event IDs."""
    sequence = [
        {"event_type": "TRANSITION_CAPTURED", "source_type": "transition",
         "source_id": transition_id},
        {"event_type": "CAUSAL_FRAGMENT_CREATED", "source_type": "transition",
         "source_id": transition_id},
    ]
    if novelty == "NOVEL_MECHANISM":
        sequence.append({"event_type": "NOVEL_MECHANISM",
                         "source_type": "causal_fragment",
                         "source_id": fragment.node_ids[0]})
    if conflict.has_conflict:
        sequence.append({"event_type": "RULE_CONFLICT",
                         "source_type": "transition", "source_id": transition_id})
    if harmful:
        sequence.append({"event_type": "RULE_HARMFUL",
                         "source_type": "transition", "source_id": transition_id})
    if trigger_event:
        sequence.extend([
            {"event_type": "CONSOLIDATION_TRIGGERED",
             "source_type": "transition", "source_id": transition_id},
            {"event_type": "RULE_REVISION_PROPOSED",
             "source_type": "transition", "source_id": transition_id},
        ])
    return sequence


def _replay_existing_observation(
        conn: sqlite3.Connection, transition_id: str, campaign_id: str,
        learner_eligible: bool) -> OnlineMemoryReceipt | None:
    """Return an immutable prior receipt, if this observation was committed.

    The online lane is a shadow log, but a retry must not reinterpret a
    transition using paths/rules created after the original observation.  The
    first capture event therefore carries a compact receipt snapshot and the
    expected event sequence.  We validate that sequence against the current
    hash chain before reconstructing the public receipt.
    """
    rows = conn.execute(
        """SELECT * FROM tehm_memory_events
            WHERE event_type='TRANSITION_CAPTURED' AND source_type='transition'
              AND source_id=? AND campaign_id=?
            ORDER BY event_digest""", (transition_id, campaign_id)).fetchall()
    candidates = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("online capture event payload is malformed") from exc
        snapshot = payload.get("online_observation")
        if snapshot is not None:
            candidates.append((row, payload, snapshot))
    if not candidates:
        if rows:
            # A pre-snapshot chain cannot prove which derived decision was
            # returned.  Refuse to append a second interpretation; migration
            # or an explicit operator reset is required instead.
            raise ValueError(
                "online observation lacks immutable replay snapshot")
        return None
    if len(candidates) != 1:
        raise ValueError("online observation has multiple immutable capture snapshots")
    capture_row, capture_payload, snapshot = candidates[0]
    if not isinstance(snapshot, dict) or snapshot.get("version") != "online-receipt-v1":
        raise ValueError("online observation snapshot is malformed")
    capture_eligible = normalize_stored_learner_bool(
        capture_row["learner_eligible"])
    if capture_eligible != learner_eligible:
        raise ValueError("online observation replay conflicts with learner eligibility")
    sequence = snapshot.get("event_sequence")
    if not isinstance(sequence, list) or len(sequence) < 2:
        raise ValueError("online observation event sequence is malformed")
    expected_first = {
        "event_type": capture_row["event_type"],
        "source_type": capture_row["source_type"],
        "source_id": capture_row["source_id"],
    }
    if sequence[0] != expected_first:
        raise ValueError("online observation event sequence starts incorrectly")
    verified = verify_event_chain(conn, campaign_id=campaign_id)
    if not verified.get("ok"):
        raise ValueError("online observation replay conflicts with event chain")
    by_previous = {}
    for row in conn.execute(
            "SELECT * FROM tehm_memory_events WHERE campaign_id=?",
            (campaign_id,)):
        previous = row["previous_event_digest"]
        if previous is not None:
            by_previous.setdefault(str(previous), []).append(row)
    event_rows = [capture_row]
    current = capture_row
    for expected in sequence[1:]:
        children = by_previous.get(str(current["event_digest"]), [])
        if len(children) != 1:
            raise ValueError("online observation event sequence is not contiguous")
        child = children[0]
        if any(child[key] != expected[key]
               for key in ("event_type", "source_type", "source_id")):
            raise ValueError("online observation event sequence conflicts")
        event_rows.append(child)
        current = child

    fragment = build_transition_causal_fragment(
        conn, transition_id, campaign_id=campaign_id, commit=False)
    fragment_node_ids = _strict_string_tuple(
        snapshot, "fragment_node_ids", label="fragment node IDs")
    fragment_edge_ids = _strict_string_tuple(
        snapshot, "fragment_edge_ids", label="fragment edge IDs")
    if (fragment_node_ids != tuple(fragment.node_ids) or
            fragment_edge_ids != tuple(fragment.edge_ids)):
        raise ValueError("online observation causal fragment witness conflicts")
    signature = snapshot.get("mechanism_signature")
    if not isinstance(signature, dict) or signature != capture_payload.get(
            "mechanism_signature"):
        raise ValueError("online observation mechanism snapshot is malformed")
    decision = _decision_from_dict(snapshot.get("consolidation_decision") or {})
    snapshot_triggered = snapshot.get("consolidation_triggered")
    if type(snapshot_triggered) is not bool:
        raise ValueError(
            "online observation consolidation_triggered is not boolean")
    snapshot_operation = snapshot.get("consolidation_operation")
    if (type(snapshot_operation) is not str or
            snapshot_operation not in CONSOLIDATION_OPERATIONS):
        raise ValueError(
            "online observation consolidation operation is malformed")
    if (decision.transition_id != transition_id or
            decision.campaign_id != campaign_id or
            decision.learner_eligible != learner_eligible or
            decision.triggered != snapshot_triggered or
            decision.operation != snapshot_operation):
        raise ValueError("online observation decision snapshot conflicts")
    preview = _preview_from_dict(snapshot.get("consolidation_preview"))
    if preview is not None and preview.campaign_id != campaign_id:
        raise ValueError("online observation preview snapshot conflicts")
    snapshot_rule_ids = _strict_string_tuple(
        snapshot, "affected_rule_ids", label="affected rule IDs")
    snapshot_path_ids = _strict_string_tuple(
        snapshot, "affected_path_ids", label="affected path IDs")
    snapshot_effect_keys = _strict_string_tuple(
        snapshot, "affected_effect_keys", label="affected effect keys")
    snapshot_reasons = _strict_string_tuple(
        snapshot, "trigger_reasons", label="trigger reasons")
    if (snapshot_rule_ids != decision.affected_rule_ids or
            snapshot_path_ids != decision.affected_path_ids or
            snapshot_effect_keys != decision.affected_effect_keys or
            snapshot_reasons != decision.reasons):
        raise ValueError("online observation decision witness conflicts")
    if (preview is not None and
            preview.full_rebuild_equivalent != decision.full_rebuild_equivalent):
        raise ValueError("online observation preview decision conflicts")
    novelty = snapshot.get("novelty")
    if type(novelty) is not str or not novelty:
        raise ValueError("online observation novelty is malformed")
    experience_value = None
    raw_value = snapshot.get("experience_value")
    if raw_value is not None:
        experience_value = ExperienceValueReceipt.from_dict(raw_value)
        if (experience_value.transition_id != transition_id or
                experience_value.campaign_id != campaign_id):
            raise ValueError("online observation experience value identity conflicts")
        stored_value = load_experience_value(
            conn, transition_id, campaign_id=campaign_id)
        if stored_value.to_dict() != experience_value.to_dict():
            raise ValueError("online observation experience value replay conflicts")
    trigger_ids = [row["event_id"] for row in event_rows
                   if row["event_type"] == "CONSOLIDATION_TRIGGERED"]
    return OnlineMemoryReceipt(
        transition_id=transition_id, campaign_id=campaign_id,
        learner_eligible=learner_eligible, fragment=fragment,
        mechanism_signature=dict(signature),
        affected_rule_ids=snapshot_rule_ids,
        affected_path_ids=snapshot_path_ids,
        events=tuple(_event_receipt(row) for row in event_rows),
        novelty=novelty,
        consolidation_triggered=snapshot_triggered,
        path_id=None,
        trigger_reasons=snapshot_reasons,
        affected_effect_keys=snapshot_effect_keys,
        trigger_event_id=(str(trigger_ids[0]) if trigger_ids else None),
        consolidation_preview=preview,
        consolidation_operation=str(
            snapshot.get("consolidation_operation") or decision.operation),
        consolidation_decision=decision,
        experience_value=experience_value,
    )


def observe_transition(conn: sqlite3.Connection, transition_id: str,
                       campaign_id: str = "live",
                       *, created_at: str | None = None) -> OnlineMemoryReceipt:
    """Record an eligible transition and its causal fragment in shadow only.

    This operation deliberately does not crystallize rules or alter lifecycle
    status.  It emits a deterministic event chain that a later gated manager
    may use to trigger affected-group consolidation.
    """
    learner_eligible, split = _membership(conn, transition_id, campaign_id)
    # Load and validate the content-addressed transition before any replay or
    # derived write.  Membership alone is not an execution oracle.  Ineligible
    # evaluation partitions remain audit-only and deliberately do not need a
    # learner-grade oracle here.
    facts = load_transition_facts(conn, transition_id)
    if learner_eligible:
        require_verified_execution(facts)
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
        replay = _replay_existing_observation(
            conn, transition_id, campaign_id, learner_eligible)
        if replay is not None:
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            savepoint_active = False
            if not had_outer_transaction:
                conn.commit()
            return replay

        signature = mechanism_signature(facts)
        fragment = build_transition_causal_fragment(
            conn, transition_id, campaign_id=campaign_id, commit=False)
        affected_rule_ids = _affected_rule_ids(
            conn, transition_id, campaign_id=campaign_id)
        affected_path_ids = _affected_path_ids(
            conn, transition_id, mechanism_family=fragment.mechanism_family,
            compatibility_profile=fragment.compatibility_profile,
            campaign_id=campaign_id)
        novelty_result = detect_novelty(conn, transition_id, campaign_id=campaign_id)
        novelty = novelty_result["status"]
        conflict = detect_conflicts(conn, transition_id, campaign_id=campaign_id)
        harmful = any(edge.relation_type == "CREATES" for edge in fragment.edges)
        trigger = evaluate_consolidation_trigger(
            conn, transition_id, campaign_id=campaign_id,
            learner_eligible=learner_eligible, novelty=novelty,
            conflict=conflict, affected_rule_ids=affected_rule_ids,
            affected_path_ids=affected_path_ids)
        preview = None
        decision = decide_consolidation(trigger)
        if trigger.triggered:
            preview = preview_affected_groups(
                conn, [transition_id], campaign_id=campaign_id)
            decision = decide_consolidation(trigger, preview)
        # P2 is deliberately parallel to the legacy trigger: value selection
        # records an auditable update priority but cannot alter trigger,
        # operation, canonical evidence, lifecycle, or runtime authority.
        experience_value = evaluate_experience_value(
            conn, transition_id, campaign_id=campaign_id,
            novelty_result=novelty_result, conflict=conflict, trigger=trigger)
        record_experience_value(
            conn, experience_value, created_at=created_at, commit=False)
        snapshot = {
            "version": "online-receipt-v1",
            "fragment_node_ids": list(fragment.node_ids),
            "fragment_edge_ids": list(fragment.edge_ids),
            "mechanism_signature": signature,
            "affected_rule_ids": list(affected_rule_ids),
            "affected_path_ids": list(affected_path_ids),
            "novelty": novelty,
            "consolidation_triggered": trigger.triggered,
            "trigger_reasons": list(trigger.reasons),
            "affected_effect_keys": list(trigger.affected_effect_keys),
            "consolidation_operation": decision.operation,
            "consolidation_preview": (
                preview.to_dict() if preview is not None else None),
            "consolidation_decision": decision.to_dict(),
            "experience_value": experience_value.to_dict(),
            "event_sequence": _observation_sequence(
                transition_id=transition_id, fragment=fragment,
                novelty=novelty, conflict=conflict, harmful=harmful,
                trigger_event=trigger.triggered),
        }
        capture_event = emit(
            event_type="TRANSITION_CAPTURED", source_type="transition",
            source_id=transition_id, campaign_id=campaign_id,
            learner_eligible=learner_eligible,
            payload={"split": split, "mechanism_signature": signature,
                     "online_observation": snapshot},
            created_at=created_at)
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
        conflict_event = None
        if conflict.has_conflict:
            conflict_event = emit(
                event_type="RULE_CONFLICT", source_type="transition",
                source_id=transition_id, campaign_id=campaign_id,
                learner_eligible=learner_eligible,
                payload={**conflict.to_dict(),
                         "affected_rule_ids": list(affected_rule_ids),
                         "affected_path_ids": list(affected_path_ids)},
                created_at=created_at)
        harmful_event = None
        if harmful:
            harmful_event = emit(
                event_type="RULE_HARMFUL", source_type="transition",
                source_id=transition_id, campaign_id=campaign_id,
                learner_eligible=learner_eligible,
                payload={"outcome": "harmful_or_nonpositive",
                         "transition_id": transition_id,
                         "affected_rule_ids": list(affected_rule_ids),
                         "affected_path_ids": list(affected_path_ids)},
                created_at=created_at)
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
        proposal_event = None
        if trigger.triggered:
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
            consolidation_decision=decision,
            experience_value=experience_value)
    except Exception:
        if savepoint_active:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise


__all__ = ["observe_transition"]
