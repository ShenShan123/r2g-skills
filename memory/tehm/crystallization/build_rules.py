"""Rule building pipeline (design doc 20.5, 26 Phase 5).

``crystallize_all``: group captured transitions by primary effect key (preflight),
role-normalize each non-singleton group, joint-anti-unify the rewrites, synthesize
candidate rules, and persist them into ``tehm_rules`` + ``tehm_rule_sources``.

Phase 6 (Rule Validity) audits these candidates next; nothing here claims
validity — rules are stored as ``CANDIDATE`` (V2 pending).
"""
from __future__ import annotations

import sqlite3
from functools import wraps

from tehm import db as tehm_db
from tehm.crystallization.anti_unify import AntiUnifyConfig, anti_unify_rewrites
from tehm.crystallization.preflight import run_preflight
from tehm.crystallization.risk import stratify_rule_risk
from tehm.crystallization.role_normalize import RoleNormalizedRewrite, normalize_rewrite
from tehm.crystallization.synthesize_skill import rule_sources, synthesize_skill
from tehm.crystallization.validity import ValidityConfig, audit_rule


def _atomic_crystallization(fn):
    """Make rule definitions and their witness rows one derived transaction.

    A full rebuild can emit several rules and may retire stale lifecycle rows.
    The savepoint prevents a later anti-unification/persistence/retirement
    failure from exposing only a prefix, while preserving a caller-owned outer
    transaction.  ``commit=False`` remains available to enclosing lanes.
    """
    @wraps(fn)
    def wrapped(conn: sqlite3.Connection, *args, **kwargs):
        requested_commit = kwargs.get("commit", True)
        had_outer_transaction = conn.in_transaction
        savepoint = "tehm_crystallize_v1"
        conn.execute(f"SAVEPOINT {savepoint}")
        savepoint_active = True
        try:
            result = fn(conn, *args, **kwargs)
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            savepoint_active = False
            if requested_commit and not had_outer_transaction:
                conn.commit()
            return result
        except Exception:
            if savepoint_active:
                conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise

    return wrapped


@_atomic_crystallization
def crystallize_all(conn: sqlite3.Connection, *, min_group_size: int = 2,
                    validity_config: ValidityConfig | None = None,
                    dry_run: bool = False, campaign_id: str = "live",
                    effect_keys: frozenset[str] | set[str] | None = None,
                    retire_stale: bool = True,
                    created_at: str | None = None,
                    commit: bool = True,
                    group_keys: (frozenset[tuple[str, str | None]] |
                                 set[tuple[str, str | None]] | None) = None
                    ) -> list[dict]:
    """Run the Phase 5-6 pipeline; returns audited candidate rules.

    Each crystallized rule is put through the ordered validity audit
    (V2 -> V1 -> V3 -> V4, design doc 7) and risk stratification (design doc 8)
    before being persisted with its final ``validity_status``.
    ``commit=False`` is reserved for callers that wrap a derived update in
    their own transaction/savepoint.  Stale-rule retirement uses the same
    commit boundary, so it remains safe when ``commit=False`` is requested.
    """
    if not campaign_id:
        raise ValueError("campaign_id is required for crystallization")
    validity_config = validity_config or ValidityConfig(min_group_size=min_group_size)
    transitions, lineage_of, episode_of = _load_transitions(
        conn, campaign_id=campaign_id)
    by_id = {t["transition_id"]: t for t in transitions}
    report = run_preflight(conn, min_group_size=min_group_size,
                            campaign_id=campaign_id)

    rules: list[dict] = []
    for key, group in report.groups.items():
        if effect_keys is not None and key not in effect_keys:
            continue
        if group["size"] < min_group_size:
            continue  # singletons never crystallize (design doc 7.2/6.3)
        # A primary effect can contain structurally different executors.  Do
        # not anti-unify those into a wildcard profile: split first on the
        # explicit compatibility contract carried by each RTL action.
        compatibility_groups: dict[str | None, list[str]] = {}
        for tid in group["transition_ids"]:
            action = by_id[tid].get("action") or {}
            payload = action.get("payload") or {}
            profile = payload.get("compatibility_profile")
            compatibility_groups.setdefault(profile, []).append(tid)
        for compatibility_profile, transition_ids in sorted(
                compatibility_groups.items(), key=lambda item: str(item[0])):
            if (group_keys is not None and
                    (key, compatibility_profile) not in group_keys):
                continue
            if len(transition_ids) < min_group_size:
                continue
            members = [by_id[tid] for tid in transition_ids]
            for t in members:
                t["lineage_id"] = lineage_of.get(t["source_state_id"])
            internal_key = (key if compatibility_profile is None else
                            f"{key}|compatibility:{compatibility_profile}")
            rewrites: list[RoleNormalizedRewrite] = [
                normalize_rewrite(
                    t, effect_key=internal_key,
                    episode_id=episode_of.get(t["transition_id"]),
                    lineage_id=lineage_of.get(t["source_state_id"]))
                for t in members
            ]
            result = anti_unify_rewrites(
                rewrites, AntiUnifyConfig(min_group_size=min_group_size))
            obligations = tuple(sorted({o for r in rewrites for o in r.obligations}))
            source_episodes = sorted({r.episode_id for r in rewrites})
            source_episode_transitions = {
                episode_id: sorted({r.transition_id for r in rewrites
                                    if r.episode_id == episode_id})
                for episode_id in source_episodes}
            source_episode_lineages = {
                episode_id: next((r.lineage_id for r in rewrites
                                  if r.episode_id == episode_id and r.lineage_id), None)
                for episode_id in source_episodes}
            family = rewrites[0].transformation_family
            rule = synthesize_skill(
                result, domain=rewrites[0].domain,
                transformation_family=family,
                action_domain=rewrites[0].action_domain,
                obligations=obligations, source_episodes=source_episodes,
                compatibility_profile=compatibility_profile,
                created_at=(created_at if created_at is not None
                            else tehm_db.now_local()),
                source_episode_transitions=source_episode_transitions,
                source_episode_lineages=source_episode_lineages)
            # Phase 6: ordered validity audit + risk stratification.
            audit = audit_rule(rule, source_transitions=members,
                               config=validity_config)
            rule["validity_status"] = audit.status
            rule["validity_profile"] = audit.to_dict()
            rule["risk_profile"] = stratify_rule_risk(rule, members)
            if not dry_run:
                # The enclosing savepoint owns the commit boundary.  Keeping
                # this write uncommitted is required for all-rules atomicity.
                _persist_rule(conn, rule, commit=False)
            rules.append(rule)
    if not dry_run and retire_stale and effect_keys is None:
        _retire_stale_rules(conn, {r["rule_id"] for r in rules}, campaign_id,
                            commit=False)
    return rules


def _load_transitions(conn: sqlite3.Connection, *, campaign_id: str = "live"):
    rows = conn.execute(
        "SELECT transition_id, source_state_id, target_state_id, action_json, "
        "observation_delta_json, verifier_json, provenance_json, outcome "
        "FROM tehm_transitions t "
        "WHERE EXISTS (SELECT 1 FROM tehm_dataset_membership dm "
        "WHERE dm.transition_id=t.transition_id AND dm.campaign_id=? "
        "AND dm.split='training' AND dm.learner_eligible=1)", (campaign_id,)).fetchall()
    transitions = [
        {
            "transition_id": r["transition_id"],
            "source_state_id": r["source_state_id"],
            "target_state_id": r["target_state_id"],
            "action": tehm_db.read_json(r["action_json"]),
            "observation_delta": tehm_db.read_json(r["observation_delta_json"]),
            "verifier": tehm_db.read_json(r["verifier_json"]),
            "provenance": tehm_db.read_json(r["provenance_json"]),
            "outcome": r["outcome"],
        }
        for r in rows
    ]
    lineage_of: dict[str, str] = {}
    for r in conn.execute("SELECT state_id, lineage_id FROM tehm_states"):
        lineage_of[r["state_id"]] = r["lineage_id"] or "?"
    episode_of: dict[str, str] = {}
    for r in conn.execute(
            "SELECT transition_id, episode_id FROM tehm_episode_steps"):
        episode_of[r["transition_id"]] = r["episode_id"]
    return transitions, lineage_of, episode_of


def _persist_rule(conn: sqlite3.Connection, rule: dict, *, commit: bool = True) -> None:
    from tehm.ids import stable_dumps

    conn.execute(
        """INSERT INTO tehm_rules (
               rule_id, domain, before_pattern_json, after_pattern_json,
               hard_preconditions_json, context_profile_json, obligations_json,
               validity_status, validity_profile_json, confidence_json,
               utility_json, risk_profile_json, predicate_schema_version,
               role_schema_version, crystallizer_version, merge_trace_digest,
               created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(rule_id) DO UPDATE SET
             domain=excluded.domain,
             before_pattern_json=excluded.before_pattern_json,
             after_pattern_json=excluded.after_pattern_json,
             hard_preconditions_json=excluded.hard_preconditions_json,
             context_profile_json=excluded.context_profile_json,
             obligations_json=excluded.obligations_json,
             validity_status=excluded.validity_status,
             validity_profile_json=excluded.validity_profile_json,
             risk_profile_json=excluded.risk_profile_json,
             predicate_schema_version=excluded.predicate_schema_version,
             role_schema_version=excluded.role_schema_version,
             crystallizer_version=excluded.crystallizer_version,
             merge_trace_digest=excluded.merge_trace_digest,
             updated_at=excluded.updated_at""",
        (
            rule["rule_id"],
            rule["domain"],
            stable_dumps(rule["before_pattern"]),
            stable_dumps(rule["after_pattern"]),
            stable_dumps(rule["hard_preconditions"]),
            stable_dumps(rule["context_predicates"]),
            stable_dumps(rule["obligations"]),
            rule["validity_status"],
            stable_dumps(rule["validity_profile"]),
            stable_dumps(rule["confidence"]),
            stable_dumps(rule["utility"]),
            stable_dumps(rule["risk_profile"]),
            rule["predicate_schema_version"],
            rule["role_schema_version"],
            rule["crystallizer_version"],
            rule["merge_trace_digest"],
            rule["created_at"],
            rule["updated_at"],
        ))
    # Source rows are a materialized view of this crystallization witness set.
    # Utility and lifecycle authority are intentionally preserved above.
    conn.execute("DELETE FROM tehm_rule_sources WHERE rule_id=?",
                 (rule["rule_id"],))
    for source in rule_sources(rule):
        conn.execute(
            """INSERT OR REPLACE INTO tehm_rule_sources (
                   rule_id, episode_id, source_substitution_json,
                   evidence_profile_json, lineage_id)
               VALUES (?, ?, ?, ?, ?)""",
            (source["rule_id"], source["episode_id"],
             source["source_substitution_json"],
             source["evidence_profile_json"], source["lineage_id"]))
    if commit:
        conn.commit()


def _retire_stale_rules(conn: sqlite3.Connection, active_rule_ids: set[str],
                        campaign_id: str, *, commit: bool = True) -> None:
    """Retire lifecycle rows no longer produced by a full rebuild.

    Definitions and source evidence remain available for audit; ``retired``
    only removes stale runtime authority.
    """
    from tehm.evolution.anti_forgetting import (
        raw_evidence_digest, verify_raw_evidence_unchanged,
    )
    from tehm.lifecycle.rule_status import set_status

    evidence_before = raw_evidence_digest(conn)
    rows = conn.execute(
        """SELECT rs.rule_id, rs.target_scope, rs.status,
                         r.validity_status
             FROM tehm_rule_status rs
             LEFT JOIN tehm_rules r ON r.rule_id=rs.rule_id
            WHERE rs.status IN ('shadow', 'candidate', 'promoted')"""
    ).fetchall()
    for row in rows:
        if (row["rule_id"] in active_rule_ids and
                row["validity_status"] in {"PROVISIONAL_VALID", "VALIDATED"}):
            continue
        invalid = row["rule_id"] in active_rule_ids
        set_status(
            conn, rule_id=row["rule_id"], target_scope=row["target_scope"],
            status="quarantined" if invalid else "retired",
            provenance={"authority": "crystallize_all",
                        "reason": "revalidated_below_lifecycle_threshold"
                        if invalid else "not_rebuilt_in_campaign",
                        "campaign_id": campaign_id}, commit=commit)
    evidence_receipt = verify_raw_evidence_unchanged(conn, evidence_before)
    if not evidence_receipt.preserved:
        raise RuntimeError(
            "full rebuild retirement changed canonical evidence: "
            f"{evidence_receipt.before_digest} -> "
            f"{evidence_receipt.after_digest}")
