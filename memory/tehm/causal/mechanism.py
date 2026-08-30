"""Deterministic facts extracted from canonical transitions.

This module intentionally consumes typed JSON already produced by canonical
capture.  It does not ask a model to invent a ``CAUSES`` relation.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from tehm import db as tehm_db
from tehm.canonical.transition import (
    Action, ObservationDelta, classify_outcome,
    primary_effect_key as canonical_primary_effect_key,
)
from tehm.canonical.verifier import VerifierSnapshot
from tehm.ids import stable_dumps, transition_id as canonical_transition_id
from tehm.rtl.compatibility import profile_from_graph


_MISSING = object()


def _json_object(value, field: str, *, optional: bool = False) -> dict:
    """Decode one persisted transition fact without inventing defaults.

    Canonical capture writes JSON objects for all transition payloads.  The
    previous helper converted malformed JSON and valid non-object JSON into
    ``{}``, which could make a damaged action/delta look like a legitimate
    low-information causal fragment.  Optional state snapshots may be NULL in
    legacy rows, but a present value must still be valid object JSON.
    """
    if value is None:
        if optional:
            return {}
        raise ValueError(f"transition facts {field} is missing")
    if isinstance(value, str):
        if not value.strip():
            raise ValueError(f"transition facts {field} JSON is empty")
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"transition facts {field} JSON is malformed") from exc
    else:
        parsed = value
    if not isinstance(parsed, dict):
        raise ValueError(
            f"transition facts {field} must decode to object")
    return parsed


def _non_empty_string(value: object, field: str) -> str:
    """Require a persisted identity/enum field to remain a real string."""
    if type(value) is not str or not value.strip():
        raise ValueError(f"transition facts {field} must be a non-empty string")
    return value.strip()


def _validate_action_payload(action: dict) -> Action:
    """Validate action semantics without the coercions in ``from_dict``."""
    domain = _non_empty_string(action.get("domain", _MISSING), "action.domain")
    family = _non_empty_string(
        action.get("transformation_family", _MISSING),
        "action.transformation_family")
    payload = action.get("payload", _MISSING)
    if type(payload) is not dict or not payload:
        raise ValueError(
            "transition facts action.payload must be a non-empty object")
    typed = Action(domain=domain, transformation_family=family, payload=payload)
    typed.validate()
    return typed


def _validate_delta_payload(delta: dict) -> ObservationDelta:
    """Validate the typed observation-delta contract without string/list coercion."""
    original = _non_empty_string(
        delta.get("original_failure", _MISSING),
        "observation_delta.original_failure")
    first_divergence = delta.get("first_divergence")
    failing_tests = delta.get("failing_tests")
    for name, value in (("first_divergence", first_divergence),
                        ("failing_tests", failing_tests)):
        if value is not None and type(value) is not dict:
            raise ValueError(
                f"transition facts observation_delta.{name} must be an object or null")
    created = delta.get("created_regressions", [])
    newly_observed = delta.get("newly_observed_failures", [])
    for name, value in (("created_regressions", created),
                        ("newly_observed_failures", newly_observed)):
        if type(value) is not list:
            raise ValueError(
                f"transition facts observation_delta.{name} must be a list")
    experiment_kind = delta.get("experiment_kind", "UNKNOWN")
    utility_verdict = delta.get("utility_verdict", "UNKNOWN")
    _non_empty_string(experiment_kind,
                      "observation_delta.experiment_kind")
    _non_empty_string(utility_verdict,
                      "observation_delta.utility_verdict")
    typed = ObservationDelta(
        original_failure=original,
        first_divergence=first_divergence,
        failing_tests=failing_tests,
        created_regressions=created,
        newly_observed_failures=newly_observed,
        experiment_kind=experiment_kind,
        utility_verdict=utility_verdict,
    )
    typed.validate()
    return typed


def _validate_verifier_payload(verifier: dict) -> VerifierSnapshot:
    """Validate verifier enums and container types without inventing defaults."""
    for name in ("verdict", "oracle_type", "confidence_tier"):
        _non_empty_string(verifier.get(name, _MISSING), f"verifier.{name}")
    if "scope" in verifier:
        _non_empty_string(verifier["scope"], "verifier.scope")
    if "extractor_version" in verifier:
        _non_empty_string(verifier["extractor_version"],
                          "verifier.extractor_version")
    evidence_refs = verifier.get("evidence_refs", [])
    if type(evidence_refs) is not list:
        raise ValueError("transition facts verifier.evidence_refs must be a list")
    if "tool_versions" in verifier and verifier["tool_versions"] is not None \
            and type(verifier["tool_versions"]) is not dict:
        raise ValueError("transition facts verifier.tool_versions must be an object or null")
    typed = VerifierSnapshot(
        verdict=verifier["verdict"],
        oracle_type=verifier["oracle_type"],
        scope=verifier.get("scope", "unknown_scope"),
        confidence_tier=verifier["confidence_tier"],
        obligation_coverage=verifier.get("obligation_coverage"),
        oracle_complete=verifier.get("oracle_complete"),
        evidence_refs=evidence_refs,
        extractor_version=verifier.get("extractor_version", "verifier-v0.1"),
        tool_versions=verifier.get("tool_versions"),
        input_binding=verifier.get("input_binding"),
        timing_contract=verifier.get("timing_contract"),
        full_oracle=verifier.get("full_oracle"),
        semantic_oracle=verifier.get("semantic_oracle"),
        execution_preflight=verifier.get("execution_preflight"),
        toolchain_binding=verifier.get("toolchain_binding"),
    )
    typed.validate()
    return typed


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
    action = _json_object(row["action_json"], "action")
    delta = _json_object(row["observation_delta_json"], "observation_delta")
    verifier = _json_object(row["verifier_json"], "verifier")
    typed_action = _validate_action_payload(action)
    typed_delta = _validate_delta_payload(delta)
    typed_verifier = _validate_verifier_payload(verifier)
    stored_domain = _non_empty_string(
        row["action_domain"], "transition.action_domain")
    if stored_domain != action["domain"]:
        raise ValueError(
            "transition facts action_domain conflicts with action.domain")
    expected_outcome = classify_outcome(typed_delta, typed_verifier)
    stored_outcome = _non_empty_string(
        row["outcome"], "transition.outcome")
    if stored_outcome != expected_outcome:
        raise ValueError(
            "transition facts outcome conflicts with typed observation/verifier")
    expected_transition_id = canonical_transition_id(
        source_state_id=row["source_state_id"],
        target_state_id=row["target_state_id"],
        action=typed_action.to_dict(),
        observation_delta=typed_delta.to_dict(),
        verifier=typed_verifier.content(),
    )
    if row["transition_id"] != expected_transition_id:
        raise ValueError(
            "transition facts content-addressed transition_id mismatch")
    stored_effect = row["primary_effect_key"]
    if stored_effect not in (None, ""):
        expected_effect = canonical_primary_effect_key(
            typed_action, typed_delta, typed_verifier,
        )
        if stored_effect != expected_effect:
            raise ValueError(
                "transition facts primary_effect_key conflicts with typed payload")
    source_state = {
        "state_id": row["source_state_id"],
        "domain": row["source_domain"],
        "lineage_id": row["lineage_id"],
        "context_graph_digest": row["source_graph_digest"],
        "verifier": _json_object(
            row["source_verifier_json"], "source_verifier_snapshot",
            optional=True),
        "artifacts": _json_object(
            row["source_artifacts_json"], "source_artifact_manifest",
            optional=True),
    }
    target_state = {
        "state_id": row["target_state_id"],
        "context_graph_digest": row["target_graph_digest"],
        "verifier": _json_object(
            row["target_verifier_json"], "target_verifier_snapshot",
            optional=True),
        "artifacts": _json_object(
            row["target_artifacts_json"], "target_artifact_manifest",
            optional=True),
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
