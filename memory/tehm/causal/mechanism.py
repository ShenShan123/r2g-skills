"""Deterministic facts extracted from canonical transitions.

This module intentionally consumes typed JSON already produced by canonical
capture.  It does not ask a model to invent a ``CAUSES`` relation.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from tehm import db as tehm_db
from tehm.ids import stable_dumps
from tehm.rtl.compatibility import profile_from_graph


def _json(value, default):
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


def action_digest(action: dict) -> str:
    import hashlib
    return "sha1:" + hashlib.sha1(stable_dumps(action).encode()).hexdigest()


@dataclass(frozen=True)
class TransitionFacts:
    transition_id: str
    action: dict
    delta: dict
    verifier: dict
    outcome: str
    primary_effect_key: str | None
    source_state: dict
    target_state: dict
    mechanism_family: str
    compatibility_profile: str | None
    lineage_id: str | None

    @property
    def failure_graph_digest(self) -> str | None:
        return self.source_state.get("context_graph_digest")

    @property
    def action_digest(self) -> str:
        return action_digest(self.action)


def load_transition_facts(conn: sqlite3.Connection, transition_id: str) -> TransitionFacts:
    row = conn.execute(
        """SELECT t.*, s.domain AS source_domain, s.lineage_id,
                  s.context_graph_digest AS source_graph_digest,
                  s.verifier_snapshot_json AS source_verifier_json,
                  s.artifact_manifest_json AS source_artifacts_json,
                  d.context_graph_digest AS target_graph_digest,
                  d.verifier_snapshot_json AS target_verifier_json,
                  d.artifact_manifest_json AS target_artifacts_json
             FROM tehm_transitions t
             JOIN tehm_states s ON s.state_id=t.source_state_id
             JOIN tehm_states d ON d.state_id=t.target_state_id
            WHERE t.transition_id=?""", (transition_id,)).fetchone()
    if row is None:
        raise KeyError(f"unknown TEHM transition: {transition_id}")
    action = _json(row["action_json"], {})
    delta = _json(row["observation_delta_json"], {})
    verifier = _json(row["verifier_json"], {})
    source_state = {
        "state_id": row["source_state_id"],
        "domain": row["source_domain"],
        "lineage_id": row["lineage_id"],
        "context_graph_digest": row["source_graph_digest"],
        "verifier": _json(row["source_verifier_json"], {}),
        "artifacts": _json(row["source_artifacts_json"], {}),
    }
    target_state = {
        "state_id": row["target_state_id"],
        "context_graph_digest": row["target_graph_digest"],
        "verifier": _json(row["target_verifier_json"], {}),
        "artifacts": _json(row["target_artifacts_json"], {}),
    }
    payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
    mechanism_family = str(action.get("transformation_family") or
                           action.get("domain") or row["source_domain"] or "UNKNOWN")
    episode = conn.execute(
        """SELECT e.mechanism_family FROM tehm_episode_steps es
             JOIN tehm_episodes e ON e.episode_id=es.episode_id
            WHERE es.transition_id=? ORDER BY es.episode_id LIMIT 1""",
        (transition_id,)).fetchone()
    if episode and episode["mechanism_family"]:
        mechanism_family = str(episode["mechanism_family"])
    profile = payload.get("compatibility_profile")
    if not profile:
        profile = profile_from_graph(_graph_from_manifest(source_state["artifacts"]))
    return TransitionFacts(
        transition_id=transition_id,
        action=action,
        delta=delta,
        verifier=verifier,
        outcome=str(row["outcome"]),
        primary_effect_key=row["primary_effect_key"],
        source_state=source_state,
        target_state=target_state,
        mechanism_family=mechanism_family,
        compatibility_profile=str(profile) if profile else None,
        lineage_id=row["lineage_id"],
    )


def _graph_from_manifest(manifest: dict) -> dict:
    ref = (manifest or {}).get("before_graph")
    # The artifact store is intentionally not opened here.  A digest reference
    # is still useful as a stable structural witness; callers with a resolved
    # graph may pass a profile through the action payload.
    return ref if isinstance(ref, dict) and "nodes" in ref else {}


def mechanism_signature(facts: TransitionFacts) -> dict:
    payload = facts.action.get("payload") or {}
    return {
        "mechanism_family": facts.mechanism_family,
        "action_domain": facts.action.get("domain"),
        "transformation_family": facts.action.get("transformation_family"),
        "compatibility_profile": facts.compatibility_profile,
        "module": payload.get("module"),
        "source_state": payload.get("source_state"),
        "target_state": payload.get("target_state"),
        "guard": payload.get("add_condition"),
        "failure_graph_digest": facts.failure_graph_digest,
    }


__all__ = ["TransitionFacts", "load_transition_facts", "action_digest",
           "mechanism_signature"]
