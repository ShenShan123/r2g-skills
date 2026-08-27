"""Shadow conflict detector for online memory evolution."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from tehm.causal.mechanism import action_digest, load_transition_facts


CONFLICT_TYPES = frozenset({
    "DEFINITION_CONFLICT", "OUTCOME_CONFLICT", "EFFECT_CONFLICT",
    "OBLIGATION_CONFLICT",
})


@dataclass(frozen=True)
class ConflictReceipt:
    transition_id: str
    campaign_id: str
    mechanism_family: str
    compatibility_profile: str | None
    conflict_types: tuple[str, ...] = ()
    evidence_transition_ids: tuple[str, ...] = ()
    details: dict = field(default_factory=dict)

    @property
    def has_conflict(self) -> bool:
        return bool(self.conflict_types)

    def to_dict(self) -> dict:
        return {
            "transition_id": self.transition_id, "campaign_id": self.campaign_id,
            "mechanism_family": self.mechanism_family,
            "compatibility_profile": self.compatibility_profile,
            "conflict_types": list(self.conflict_types),
            "evidence_transition_ids": list(self.evidence_transition_ids),
            "details": self.details,
        }


def detect_conflicts(conn: sqlite3.Connection, transition_id: str,
                     *, campaign_id: str = "live") -> ConflictReceipt:
    facts = load_transition_facts(conn, transition_id)
    rows = conn.execute(
        """SELECT t.transition_id
             FROM tehm_transitions t
            WHERE t.transition_id != ?
              AND EXISTS (SELECT 1 FROM tehm_dataset_membership dm
                            WHERE dm.transition_id=t.transition_id
                              AND dm.campaign_id=? AND dm.split='training'
                              AND dm.learner_eligible=1)
            ORDER BY t.transition_id""", (transition_id, campaign_id)).fetchall()
    conflicts: set[str] = set()
    evidence: set[str] = set()
    details: dict[str, list] = {name: [] for name in sorted(CONFLICT_TYPES)}
    for row in rows:
        other = load_transition_facts(conn, row["transition_id"])
        if (other.mechanism_family != facts.mechanism_family or
                other.compatibility_profile != facts.compatibility_profile):
            continue
        same_action = action_digest(other.action) == facts.action_digest
        if not same_action:
            conflicts.add("DEFINITION_CONFLICT")
            details["DEFINITION_CONFLICT"].append(other.transition_id)
        if same_action and other.outcome != facts.outcome:
            conflicts.add("OUTCOME_CONFLICT")
            details["OUTCOME_CONFLICT"].append(other.transition_id)
        if same_action and other.primary_effect_key != facts.primary_effect_key:
            conflicts.add("EFFECT_CONFLICT")
            details["EFFECT_CONFLICT"].append(other.transition_id)
        if other.delta.get("created_regressions") or other.delta.get("newly_observed_failures"):
            conflicts.add("OBLIGATION_CONFLICT")
            details["OBLIGATION_CONFLICT"].append(other.transition_id)
        if any(other.transition_id in values for values in details.values()):
            evidence.add(other.transition_id)
    return ConflictReceipt(
        transition_id=transition_id, campaign_id=campaign_id,
        mechanism_family=facts.mechanism_family,
        compatibility_profile=facts.compatibility_profile,
        conflict_types=tuple(sorted(conflicts)),
        evidence_transition_ids=tuple(sorted(evidence)),
        details={key: sorted(set(value)) for key, value in details.items() if value})


__all__ = ["CONFLICT_TYPES", "ConflictReceipt", "detect_conflicts"]
