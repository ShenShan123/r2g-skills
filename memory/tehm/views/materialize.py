"""phi dispatch: canonical experience -> typed memory views (design doc 5, 19.3).

``materialize_all`` runs the materializers for one captured transition:
    state.before -> semantic + diagnostic
    state.after  -> semantic
    transition   -> diagnostic (delta) + procedural
    episode      -> episodic
Parametric stays NOT_IMPLEMENTED (no row is written).
"""
from __future__ import annotations

import sqlite3

from tehm.views.base import ViewRecord
from tehm.views.semantic import materialize_semantic
from tehm.views.diagnostic import materialize_diagnostic
from tehm.views.procedural import materialize_procedural
from tehm.views.episodic import materialize_episodic


def materialize_all(conn: sqlite3.Connection, *, state_before, state_after,
                    transition, episode, before_graph, after_graph,
                    before_signature, transition_delta_signature,
                    role_map: dict | None = None,
                    materialized_at: str = "", commit: bool = True) -> list[ViewRecord]:
    """Materialize every implemented view reachable from one captured transition."""
    records: list[ViewRecord] = []

    records.append(materialize_semantic(
        conn, "state", state_before.state_id, before_graph,
        source_refs=[f"state:{state_before.state_id}"],
        materialized_at=materialized_at, commit=commit))
    records.append(materialize_semantic(
        conn, "state", state_after.state_id, after_graph,
        source_refs=[f"state:{state_after.state_id}"],
        materialized_at=materialized_at, commit=commit))

    records.append(materialize_diagnostic(
        conn, "state", state_before.state_id, before_signature,
        source_refs=[f"state:{state_before.state_id}"],
        materialized_at=materialized_at, commit=commit))
    records.append(materialize_diagnostic(
        conn, "transition", transition.transition_id, transition_delta_signature,
        source_refs=[f"transition:{transition.transition_id}"],
        materialized_at=materialized_at, commit=commit))

    records.append(materialize_procedural(
        conn, transition, role_map,
        source_refs=[f"transition:{transition.transition_id}",
                     f"episode:{episode.episode_id}"],
        materialized_at=materialized_at, commit=commit))

    records.append(materialize_episodic(
        conn, episode, [transition],
        source_refs=[f"episode:{episode.episode_id}"],
        materialized_at=materialized_at, commit=commit))

    return records


def transition_delta_signature(transition) -> dict:
    """Diagnostic view of the change a transition produced (observable delta)."""
    return {
        "source_state_id": transition.source_state_id,
        "target_state_id": transition.target_state_id,
        "observation_delta": transition.observation_delta.to_dict(),
        "outcome": transition.outcome,
        "verdict": transition.verifier.verdict,
    }
