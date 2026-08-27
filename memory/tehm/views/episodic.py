"""Episodic view: the repair trajectory (design doc 22.3).

Built independently from TEHM transitions (never legacy ``fix_trajectories``):
ordered steps, branches, partial/harmful/recovery segments, terminal oracle
set, and per-step state digests.
"""
from __future__ import annotations

import sqlite3

from tehm import SCHEMA_VERSION
from tehm.views.base import ViewRecord, upsert_view

EPISODIC_EXTRACTOR_VERSION = "episodic-v0.1"


def build_episodic_view(episode, transitions: list, *,
                        source_refs: list[str] | None = None,
                        materialized_at: str = "") -> ViewRecord:
    """Build the episodic view for a CanonicalEpisode + its ordered transitions.

    ``transitions``: canonical transition objects whose ids must equal
    ``episode.ordered_transition_ids`` (in order).
    """
    steps = []
    for t in transitions:
        steps.append({
            "transition_id": t.transition_id,
            "source_state_id": t.source_state_id,
            "target_state_id": t.target_state_id,
            "outcome": t.outcome,
            "verdict": t.verifier.verdict,
            "confidence_tier": t.verifier.confidence_tier,
        })
    payload = {
        "episode_id": episode.episode_id,
        "initial_state_id": episode.initial_state_id,
        "terminal_state_id": episode.terminal_state_id,
        "terminal_status": episode.terminal_status,
        "steps": steps,
        "branches": {"main": [t.transition_id for t in transitions]},
        "trajectory_summary": episode.trajectory_summary_json,
    }
    return ViewRecord(
        owner_type="episode",
        owner_id=episode.episode_id,
        view_type="episodic",
        schema_version=SCHEMA_VERSION,
        extractor_version=EPISODIC_EXTRACTOR_VERSION,
        payload=payload,
        source_refs=list(source_refs or []),
        materialized_at=materialized_at,
    )


def materialize_episodic(conn: sqlite3.Connection, episode, transitions: list, *,
                         source_refs: list[str] | None = None,
                         materialized_at: str = "", commit: bool = True) -> ViewRecord:
    record = build_episodic_view(episode, transitions,
                                 source_refs=source_refs,
                                 materialized_at=materialized_at)
    upsert_view(conn, record, commit=commit)
    return record
