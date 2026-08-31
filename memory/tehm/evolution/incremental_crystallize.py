"""Affected-group crystallization wrapper."""
from __future__ import annotations

import sqlite3

from tehm import db as tehm_db
from tehm.crystallization.build_rules import crystallize_all

from .anti_forgetting import raw_evidence_digest, verify_raw_evidence_unchanged
from .events import append_memory_event
from .receipts import IncrementalCrystallizationReceipt
from .revision import record_rule_revision


def _normalize_ids(transition_ids: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Validate the caller-owned transition witness without coercion.

    Incremental crystallization is a learner-derived write path.  Silently
    converting integers to strings or deduplicating repeated IDs would let a
    malformed/ambiguous witness select a different evidence set than the
    caller declared.  Keep the accepted shape deliberately narrow and fail
    closed before any SQL or derived-state work.
    """
    if type(transition_ids) not in (list, tuple) or not transition_ids:
        raise ValueError("transition_ids must be a non-empty list or tuple")
    if any(type(tid) is not str or not tid for tid in transition_ids):
        raise ValueError("transition_ids must contain non-empty strings")
    if len(set(transition_ids)) != len(transition_ids):
        raise ValueError("transition_ids must not contain duplicates")
    return tuple(sorted(transition_ids))


def _affected_inputs(conn: sqlite3.Connection, tids: tuple[str, ...],
                     campaign_id: str) -> tuple[
                         tuple[str, ...], tuple[tuple[str, str | None], ...],
                         dict[str, str]]:
    placeholders = ",".join("?" for _ in tids)
    rows = conn.execute(
        f"""SELECT transition_id, primary_effect_key, action_json
              FROM tehm_transitions t
             WHERE t.transition_id IN ({placeholders})
               AND EXISTS (SELECT 1 FROM tehm_dataset_membership dm
                            WHERE dm.transition_id=t.transition_id
                              AND dm.campaign_id=? AND dm.split='training'
                              AND dm.learner_eligible=1)""",
        (*tids, campaign_id)).fetchall()
    if len(rows) != len(tids):
        raise ValueError("all transitions must be learner-eligible in campaign")
    keys = tuple(sorted({row["primary_effect_key"] for row in rows
                         if row["primary_effect_key"]}))
    groups = tuple(sorted({
        (row["primary_effect_key"],
         ((tehm_db.read_json(row["action_json"]).get("payload") or {})
          .get("compatibility_profile")))
        for row in rows if row["primary_effect_key"]
    }, key=lambda group: (group[0], str(group[1]))))
    return keys, groups, {
        row["transition_id"]: row["primary_effect_key"] for row in rows}


def _rule_uses_transitions(rule: dict, tids: set[str]) -> bool:
    provenance = rule.get("provenance") or {}
    by_episode = provenance.get("source_episode_transitions") or {}
    return bool(tids & {
        str(transition_id)
        for transition_ids in by_episode.values()
        for transition_id in (transition_ids or [])
    })


def _equivalence(
    affected_rules: list[dict], full_rules: list[dict], tids: tuple[str, ...]
) -> tuple[bool, tuple[str, ...]]:
    expected = tuple(sorted(
        rule["rule_id"] for rule in full_rules
        if _rule_uses_transitions(rule, set(tids))))
    observed = tuple(sorted(rule["rule_id"] for rule in affected_rules))
    return observed == expected, expected


def preview_affected_groups(
    conn: sqlite3.Connection,
    transition_ids: list[str] | tuple[str, ...],
    campaign_id: str = "live",
    *,
    min_group_size: int = 2,
) -> IncrementalCrystallizationReceipt:
    """Build a non-mutating shadow revision for affected effect groups.

    Both projections run with ``dry_run=True``.  The second projection is a
    full campaign rebuild, filtered only when comparing its source witnesses;
    it therefore provides an actual incremental/full equivalence witness.
    """
    tids = _normalize_ids(transition_ids)
    if not campaign_id:
        raise ValueError("campaign_id is required")
    raw_before = raw_evidence_digest(conn)
    keys, groups, _ = _affected_inputs(conn, tids, campaign_id)
    affected = crystallize_all(
        conn, min_group_size=min_group_size, campaign_id=campaign_id,
        effect_keys=frozenset(keys), dry_run=True, retire_stale=False,
        created_at="1970-01-01T00:00:00+00:00",
        group_keys=frozenset(groups))
    full = crystallize_all(
        conn, min_group_size=min_group_size, campaign_id=campaign_id,
        dry_run=True, retire_stale=False,
        created_at="1970-01-01T00:00:00+00:00")
    equivalent, full_ids = _equivalence(affected, full, tids)
    raw_receipt = verify_raw_evidence_unchanged(conn, raw_before)
    if not raw_receipt.preserved:
        raise RuntimeError(
            "incremental preview changed canonical evidence: "
            f"{raw_receipt.before_digest} -> {raw_receipt.after_digest}")
    return IncrementalCrystallizationReceipt(
        campaign_id=campaign_id, transition_ids=tids,
        affected_effect_keys=keys, rules=tuple(affected),
        full_rebuild_equivalent=equivalent,
        full_rebuild_rule_ids=full_ids,
        mode="preview", affected_group_keys=groups,
        raw_evidence_before_digest=raw_receipt.before_digest,
        raw_evidence_after_digest=raw_receipt.after_digest,
        raw_evidence_preserved=raw_receipt.preserved)


def crystallize_affected_groups(
    conn: sqlite3.Connection,
    transition_ids: list[str] | tuple[str, ...],
    campaign_id: str = "live",
    *,
    min_group_size: int = 2,
    created_at: str | None = None,
) -> IncrementalCrystallizationReceipt:
    tids = _normalize_ids(transition_ids)
    if not campaign_id:
        raise ValueError("campaign_id is required")
    raw_before = raw_evidence_digest(conn)
    keys, groups, _ = _affected_inputs(conn, tids, campaign_id)
    old_rules = {
        row["rule_id"] for row in conn.execute(
            """SELECT DISTINCT rs.rule_id
                 FROM tehm_rule_sources rs
                 JOIN tehm_episode_steps es ON es.episode_id=rs.episode_id
                WHERE es.transition_id IN (%s)""" % ",".join("?" for _ in tids),
            tids)
    }
    # Keep the whole derived update atomic.  ``crystallize_all`` historically
    # commits each rule, which is safe for a full rebuild but can leave an
    # incremental revision half-materialized if a later witness check fails.
    # A savepoint lets this lane roll back rules, events, and revision rows as
    # one unit while preserving any transaction owned by the caller.
    had_outer_transaction = conn.in_transaction
    savepoint = "tehm_incremental_crystallize_v1"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        # Current crystallizer remains the audited implementation.  Filtering
        # persistence to affected effect/profile groups is implemented in
        # build_rules; stale unrelated lifecycle rows remain untouched here.
        rules = crystallize_all(
            conn, min_group_size=min_group_size, campaign_id=campaign_id,
            effect_keys=frozenset(keys), retire_stale=False, commit=False,
            group_keys=frozenset(groups))
        new_rule_ids = {rule["rule_id"] for rule in rules}
        changed_rule_ids = sorted(new_rule_ids - old_rules)
        # Compare the persisted affected projection against a full campaign
        # rebuild before emitting any revision event.  Only full-rebuild rules
        # whose episode-owned witnesses include an affected transition belong
        # in this comparison.
        full_projection = crystallize_all(
            conn, min_group_size=min_group_size, campaign_id=campaign_id,
            dry_run=True, retire_stale=False)
        equivalent, full_ids = _equivalence(rules, full_projection, tids)
        raw_receipt = verify_raw_evidence_unchanged(conn, raw_before)
        if not raw_receipt.preserved:
            raise RuntimeError(
                "incremental crystallization changed canonical evidence: "
                f"{raw_receipt.before_digest} -> {raw_receipt.after_digest}")
        if not equivalent:
            raise RuntimeError(
                "incremental crystallization diverged from full rebuild: "
                f"observed={sorted(new_rule_ids)} expected={list(full_ids)}")

        if changed_rule_ids:
            trigger = append_memory_event(
                conn, event_type="CONSOLIDATION_TRIGGERED", source_type="transition",
                source_id=tids[0], campaign_id=campaign_id, learner_eligible=True,
                payload={"transition_ids": list(tids),
                         "affected_effect_keys": list(keys),
                         "rule_ids": changed_rule_ids,
                         "raw_evidence_before_digest": raw_receipt.before_digest,
                         "raw_evidence_after_digest": raw_receipt.after_digest},
                created_at=created_at, commit=False)
            parent = sorted(old_rules)[0] if old_rules else None
            for rule_id in changed_rule_ids:
                record_rule_revision(
                    conn, parent_rule_id=parent, child_rule_id=rule_id,
                    operation="REVISE" if parent else "SPECIALIZE",
                    trigger_event_id=trigger.event_id,
                    evidence_refs=list(tids),
                    validation={"incremental": True, "campaign_id": campaign_id,
                                "raw_evidence_preserved": True},
                    created_at=created_at, commit=False)
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        if not had_outer_transaction:
            conn.commit()
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    return IncrementalCrystallizationReceipt(
        campaign_id=campaign_id, transition_ids=tids,
        affected_effect_keys=keys, rules=tuple(rules),
        full_rebuild_equivalent=equivalent,
        full_rebuild_rule_ids=full_ids,
        mode="persist", affected_group_keys=groups,
        raw_evidence_before_digest=raw_receipt.before_digest,
        raw_evidence_after_digest=raw_receipt.after_digest,
        raw_evidence_preserved=raw_receipt.preserved)


__all__ = ["crystallize_affected_groups", "preview_affected_groups"]
