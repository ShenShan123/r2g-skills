"""Shadow conflict detector for online memory evolution."""
from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field

from tehm.causal.mechanism import action_digest, load_transition_facts
from tehm.ids import stable_dumps


CONFLICT_TYPES = frozenset({
    "DEFINITION_CONFLICT", "OUTCOME_CONFLICT", "EFFECT_CONFLICT",
    "OBLIGATION_CONFLICT",
})
CONFLICT_RECEIPT_VERSION = "conflict-receipt-v0.1"


def _text(value: object, field_name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"conflict receipt {field_name} is required")
    return value.strip()


@dataclass(frozen=True)
class ConflictReceipt:
    transition_id: str
    campaign_id: str
    mechanism_family: str
    compatibility_profile: str | None
    conflict_types: tuple[str, ...] = ()
    evidence_transition_ids: tuple[str, ...] = ()
    details: dict = field(default_factory=dict)
    lineage_id: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("transition_id", "campaign_id", "mechanism_family"):
            object.__setattr__(self, field_name,
                               _text(getattr(self, field_name), field_name))
        if self.compatibility_profile is not None:
            object.__setattr__(self, "compatibility_profile",
                               _text(self.compatibility_profile, "compatibility_profile"))
        if self.lineage_id is not None:
            object.__setattr__(self, "lineage_id", _text(self.lineage_id, "lineage_id"))
        if not isinstance(self.conflict_types, (list, tuple)):
            raise ValueError("conflict receipt conflict_types must be a sequence")
        conflict_types = tuple(sorted(set(_text(item, "conflict_types")
                                          for item in self.conflict_types)))
        if any(item not in CONFLICT_TYPES for item in conflict_types):
            raise ValueError("conflict receipt contains an invalid conflict type")
        if not isinstance(self.evidence_transition_ids, (list, tuple)):
            raise ValueError(
                "conflict receipt evidence_transition_ids must be a sequence")
        evidence = tuple(sorted(set(_text(item, "evidence_transition_ids")
                                    for item in self.evidence_transition_ids)))
        if not isinstance(self.details, Mapping):
            raise ValueError("conflict receipt details must be an object")
        object.__setattr__(self, "conflict_types", conflict_types)
        object.__setattr__(self, "evidence_transition_ids", evidence)
        object.__setattr__(self, "details", dict(self.details))

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
            "lineage_id": self.lineage_id,
        }

    @property
    def receipt_digest(self) -> str:
        return "sha256:" + hashlib.sha256(
            stable_dumps(self.to_dict()).encode()).hexdigest()

    @property
    def receipt_id(self) -> str:
        return "conflict_" + self.receipt_digest.split(":", 1)[1][:24]

    @classmethod
    def from_dict(cls, payload: object) -> "ConflictReceipt":
        if not isinstance(payload, Mapping):
            raise ValueError("conflict receipt must be an object")
        required = {
            "transition_id", "campaign_id", "mechanism_family",
            "compatibility_profile", "conflict_types", "evidence_transition_ids",
            "details", "lineage_id",
        }
        if not required <= set(payload):
            raise ValueError("conflict receipt is missing fields")
        receipt = cls(
            transition_id=payload["transition_id"], campaign_id=payload["campaign_id"],
            mechanism_family=payload["mechanism_family"],
            compatibility_profile=payload["compatibility_profile"],
            conflict_types=tuple(payload["conflict_types"]),
            evidence_transition_ids=tuple(payload["evidence_transition_ids"]),
            details=dict(payload["details"]), lineage_id=payload["lineage_id"])
        if payload.get("receipt_digest") not in (None, receipt.receipt_digest):
            raise ValueError("conflict receipt digest mismatch")
        if payload.get("receipt_id") not in (None, receipt.receipt_id):
            raise ValueError("conflict receipt ID mismatch")
        return receipt


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
        details={key: sorted(set(value)) for key, value in details.items() if value},
        lineage_id=facts.lineage_id)


__all__ = ["CONFLICT_RECEIPT_VERSION", "CONFLICT_TYPES", "ConflictReceipt",
           "detect_conflicts"]
