"""Procedural view: instance-level executable repair (design doc 22.4).

At the transition level this is the *instance* procedural view: a role-normalized
before/after pattern pair, the obligations the verification covered, and the
source episode + substitution (witness). Crystallization (Phase 5) generalizes
several instance views into a rule with shared holes.
"""
from __future__ import annotations

import sqlite3

from tehm import SCHEMA_VERSION
from tehm.views.base import ViewRecord, upsert_view

PROCEDURAL_EXTRACTOR_VERSION = "procedural-instance-v0.1"


def _derive_obligations(transition) -> list[str]:
    obligations: list[str] = []
    delta = transition.observation_delta
    if delta.original_failure == "REMOVED":
        obligations.append("TARGET_FAILURE_REMOVED")
    if transition.verifier.oracle_type == "REGRESSION":
        obligations.append("PRESERVE_FROZEN_REGRESSION")
    if transition.verifier.oracle_type in ("FORMAL", "DIFFERENTIAL_SIM"):
        obligations.append(f"{transition.verifier.oracle_type}_OBLIGATION_PRESERVED")
    if not obligations:
        obligations.append(f"VERIFIER_{transition.verifier.oracle_type}")
    return sorted(obligations)


def build_procedural_view(transition, role_map: dict | None = None, *,
                          source_refs: list[str] | None = None,
                          materialized_at: str = "") -> ViewRecord:
    """Instance-level procedural view for one verified transition."""
    action = transition.action
    before_snapshot = {
        "source_state_id": transition.source_state_id,
        "structural_roles": _roles_for_action(role_map, action.payload),
    }
    after_snapshot = {
        "target_state_id": transition.target_state_id,
        "structural_roles": _roles_for_action(role_map, action.payload),
    }
    payload = {
        "skill_type": action.transformation_family,
        "action_domain": action.domain,
        "before_pattern": {
            "type": action.transformation_family,
            **before_snapshot,
        },
        "after_pattern": {
            "type": action.transformation_family,
            **after_snapshot,
        },
        "hard_preconditions": [],
        "context_predicates": {},
        "obligations": _derive_obligations(transition),
        "source_substitution": transition.provenance.get("substitution", {}),
        "provenance": {
            "transition_id": transition.transition_id,
            "primary_effect_key": transition.primary_effect_key,
            "episode": transition.provenance.get("episode_id"),
        },
    }
    return ViewRecord(
        owner_type="transition",
        owner_id=transition.transition_id,
        view_type="procedural",
        schema_version=SCHEMA_VERSION,
        extractor_version=PROCEDURAL_EXTRACTOR_VERSION,
        payload=payload,
        source_refs=list(source_refs or []),
        materialized_at=materialized_at,
    )


def _roles_for_action(role_map: dict | None, action_payload: dict) -> dict:
    """Subset of the role map relevant to the action's payload references."""
    if not role_map:
        return {}
    tokens: set[str] = set()
    for value in action_payload.values():
        if isinstance(value, str):
            tokens.add(value)
        elif isinstance(value, dict):
            for v in value.values():
                if isinstance(v, str):
                    tokens.add(v)
    return {
        entity: role for entity, role in role_map.items()
        if role in ("TARGET_CHECK", "PRESERVE_CHECK", "RERUN_STAGE", "CONFIG_KNOB")
        or any(tok in entity for tok in tokens)
    }


def materialize_procedural(conn: sqlite3.Connection, transition, role_map: dict | None = None, *,
                           source_refs: list[str] | None = None,
                           materialized_at: str = "", commit: bool = True) -> ViewRecord:
    record = build_procedural_view(transition, role_map,
                                   source_refs=source_refs,
                                   materialized_at=materialized_at)
    upsert_view(conn, record, commit=commit)
    return record
