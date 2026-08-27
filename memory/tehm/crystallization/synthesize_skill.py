"""Skill synthesis (design doc 22.4, 4.3, 26 Phase 5).

Turns one ``AntiUnifyResult`` (a crystallized effect group) into a candidate
procedural rule ``r = <L, R, P_h, P_c, Q, Pi, Gamma, U, Risk, nu_P>`` shaped for
the flow/signoff domain:

    skill_type: ANTENNA_DIODE_REPAIR
    match:
      target_check: drc
      knob: $H0
    rewrite:
      value: $H1
    execution:
      rerun_from: place
      recheck: drc
    verification:
      - TARGET_FAILURE_REMOVED
      - PRESERVE_FROZEN_REGRESSION

The rule is content-addressed (rule_id), carries its source episodes and their
substitution witnesses, and is stamped ``status: CANDIDATE`` pending the V2
non-triviality audit (Phase 6).
"""
from __future__ import annotations

from tehm import ROLE_SCHEMA_VERSION, PREDICATE_SCHEMA_VERSION
from tehm.ids import rule_id, stable_dumps
from tehm.crystallization.anti_unify import ALGORITHM_VERSION, result_digest

SYNTHESIZER_VERSION = "skill-synthesizer-v0.1"
RULE_STATUS_CANDIDATE = "CANDIDATE"


def synthesize_skill(result, *, domain: str, transformation_family: str,
                     obligations: tuple, source_episodes: list,
                     action_domain: str = "unknown",
                     compatibility_profile: str | None = None,
                     created_at: str = "",
                     source_episode_transitions: dict | None = None,
                     source_episode_lineages: dict | None = None) -> dict:
    """Build the candidate rule dict from an anti-unification result.

    ``action_domain`` (e.g. ``signoff.REPAIR_ACTION`` / ``rtl.GUARD_STRENGTHEN``)
    is preserved so the activation pipeline can reproduce the executable action.
    """
    before_pattern = {
        "type": transformation_family,
        "domain": domain,
        "action_domain": action_domain,
        **result.before_pattern,
    }
    after_pattern = {
        "type": transformation_family,
        "domain": domain,
        "action_domain": action_domain,
        **result.after_pattern,
    }
    rule = {
        "rule_id": rule_id(
            domain=domain,
            before_pattern=before_pattern,
            after_pattern=after_pattern,
            hard_preconditions=[],
            obligations=list(obligations),
        ),
        "domain": domain,
        "action_domain": action_domain,
        "transformation_family": transformation_family,
        "before_pattern": before_pattern,
        "after_pattern": after_pattern,
        "hard_preconditions": [],
        "context_predicates": ({"compatibility_profile": compatibility_profile}
                                if compatibility_profile else {}),
        "obligations": sorted(set(obligations)),
        "validity_status": RULE_STATUS_CANDIDATE,          # pending V2 (Phase 6)
        "validity_profile": {"v2": "PENDING", "v1": "PENDING"},
        "confidence": {"rule": None, "activation": None},
        "utility": {"activations": 0, "positive": 0, "neutral": 0, "harmful": 0},
        "risk_profile": [],
        "predicate_schema_version": PREDICATE_SCHEMA_VERSION,
        "role_schema_version": ROLE_SCHEMA_VERSION,
        "crystallizer_version": SYNTHESIZER_VERSION,
        "abstraction_metrics": result.abstraction_metrics,
        "merge_trace_digest": result_digest(result),
        "provenance": {
            "source_episodes": sorted(set(source_episodes)),
            "source_substitutions": result.source_substitutions,
            "source_episode_transitions": {
                str(k): sorted(set(v)) for k, v in
                (source_episode_transitions or {}).items()},
            "source_episode_lineages": {
                str(k): v for k, v in (source_episode_lineages or {}).items()},
            "hole_constraints": result.hole_constraints,
            "merge_trace": [s.to_dict() for s in result.merge_trace],
            "algorithm_version": ALGORITHM_VERSION,
        },
        "created_at": created_at,
        "updated_at": created_at,
    }
    return rule


def rule_sources(rule: dict) -> list[dict]:
    """Rows for ``tehm_rule_sources``: one per source episode."""
    rows = []
    provenance = rule["provenance"]
    subs = provenance["source_substitutions"]
    by_episode = provenance.get("source_episode_transitions") or {}
    by_lineage = provenance.get("source_episode_lineages") or {}
    for episode_id in provenance["source_episodes"]:
        owned_ids = set(by_episode.get(episode_id) or [])
        # Keep a transition even when the rule has no holes: the empty
        # substitution map is still a replay witness keyed by its owner.
        transition_subs = {tid: subs[tid] for tid in sorted(owned_ids)
                           if tid in subs}
        if not transition_subs:
            raise ValueError(
                f"rule source {rule['rule_id']}:{episode_id} has no episode-owned witness")
        rows.append({
            "rule_id": rule["rule_id"],
            "episode_id": episode_id,
            "source_substitution_json": stable_dumps(transition_subs),
            "evidence_profile_json": stable_dumps(rule["abstraction_metrics"]),
            "lineage_id": by_lineage.get(episode_id),
        })
    return rows
