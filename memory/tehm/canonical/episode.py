"""Canonical repair episode graph (design doc 4.2).

A repair episode is an ordered (optionally branched) sequence of verified
transitions from ``initial_state`` to ``terminal_state``. Globally episodes
form a graph (a state can branch into several candidate actions); the local
linear chain is stored here with ``branch_id``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from tehm import SCHEMA_VERSION
from tehm.ids import episode_id, stable_dumps
from tehm.canonical.transition import (
    HARMFUL_OUTCOMES,
    NEUTRAL_OUTCOMES,
    POSITIVE_OUTCOMES,
    OUTCOMES,
)

TERMINAL_STATUSES = (
    "VERIFIED_REPAIR", "VERIFIED_OBSERVATION", "PARTIAL", "ABANDONED",
    "FAILED", "OPEN",
)


def trajectory_summary(transition_outcomes: list[str]) -> dict:
    """Aggregate an episode's transitions into positive/neutral/harmful counts."""
    summary = {
        "steps": len(transition_outcomes),
        "positive_transitions": 0,
        "neutral_transitions": 0,
        "harmful_transitions": 0,
        "oracle_calls": None,  # caller stamps if known
    }
    for outcome in transition_outcomes:
        if outcome in POSITIVE_OUTCOMES:
            summary["positive_transitions"] += 1
        elif outcome in NEUTRAL_OUTCOMES:
            summary["neutral_transitions"] += 1
        elif outcome in HARMFUL_OUTCOMES:
            summary["harmful_transitions"] += 1
        else:
            raise ValueError(f"unexpected outcome {outcome!r} (not in {OUTCOMES})")
    return summary


@dataclass
class CanonicalEpisode:
    """A repair episode graph (linear branch stored as ordered steps)."""

    domain: str
    initial_state_id: str
    mechanism_family: str | None = None
    lineage_id: str | None = None
    terminal_state_id: str | None = None
    terminal_status: str = "OPEN"
    trajectory_summary_json: dict = field(default_factory=dict)
    provenance: dict = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION
    ordered_transition_ids: list = field(default_factory=list)

    @property
    def episode_id(self) -> str:
        return episode_id(
            domain=self.domain,
            initial_state_id=self.initial_state_id,
            mechanism_family=self.mechanism_family,
            lineage_id=self.lineage_id,
            ordered_transition_ids=self.ordered_transition_ids,
        )

    def validate(self) -> None:
        if not self.domain or not self.initial_state_id:
            raise ValueError("episode needs domain and initial_state_id")
        if self.terminal_status not in TERMINAL_STATUSES:
            raise ValueError(
                f"terminal_status must be one of {TERMINAL_STATUSES}, "
                f"got {self.terminal_status!r}")
        if not self.ordered_transition_ids:
            raise ValueError("episode must contain at least one step")

    def to_dict(self) -> dict:
        return {
            "episode_id": self.episode_id,
            "domain": self.domain,
            "initial_state_id": self.initial_state_id,
            "terminal_state_id": self.terminal_state_id,
            "terminal_status": self.terminal_status,
            "mechanism_family": self.mechanism_family,
            "lineage_id": self.lineage_id,
            "trajectory_summary": self.trajectory_summary_json,
            "provenance": self.provenance,
            "schema_version": self.schema_version,
            "ordered_transition_ids": list(self.ordered_transition_ids),
        }

    def to_row(self) -> dict:
        return {
            "episode_id": self.episode_id,
            "domain": self.domain,
            "initial_state_id": self.initial_state_id,
            "terminal_state_id": self.terminal_state_id,
            "terminal_status": self.terminal_status,
            "mechanism_family": self.mechanism_family,
            "lineage_id": self.lineage_id,
            "trajectory_summary_json": stable_dumps(self.trajectory_summary_json),
            "provenance_json": stable_dumps(self.provenance),
            "schema_version": self.schema_version,
        }
