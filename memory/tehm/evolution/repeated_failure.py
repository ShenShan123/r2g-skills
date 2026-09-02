"""Typed repeated-failure evidence for the Revision3 evolution plane."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tehm.causal.mechanism import TransitionFacts, load_transition_facts
from tehm.ids import stable_dumps
from tehm.verified_execution import require_verified_transition


REPEATED_FAILURE_RECEIPT_VERSION = "repeated-failure-receipt-v0.1"


def _text(value: object, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"repeated failure receipt {field} is required")
    return value.strip()


def _strings(value: object, field: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or isinstance(value, (str, bytes)):
        raise ValueError(f"repeated failure receipt {field} must be a sequence")
    result = tuple(_text(item, field) for item in value)
    if not allow_empty and not result:
        raise ValueError(f"repeated failure receipt {field} must not be empty")
    if len(set(result)) != len(result):
        raise ValueError(f"repeated failure receipt {field} contains duplicates")
    return result


def _ordered_strings(value: object, field: str) -> tuple[str, ...]:
    """Validate an aligned vector without requiring distinct digest values."""
    if not isinstance(value, (list, tuple)) or isinstance(value, (str, bytes)):
        raise ValueError(f"repeated failure receipt {field} must be a sequence")
    return tuple(_text(item, field) for item in value)


@dataclass(frozen=True)
class RepeatedFailureReceipt:
    """Content-addressed aggregation of independent executable failures."""

    campaign_id: str
    mechanism_family: str
    compatibility_profile: str | None
    failure_family: str
    failure_transition_ids: tuple[str, ...]
    evidence_lineages: tuple[str, ...]
    resolution_ids: tuple[str, ...]
    oracle_digests: tuple[str, ...]
    learner_eligible: bool = True
    oracle_complete: tuple[bool, ...] = ()
    version: str = REPEATED_FAILURE_RECEIPT_VERSION

    def __post_init__(self) -> None:
        for field_name in ("campaign_id", "mechanism_family", "failure_family"):
            object.__setattr__(self, field_name,
                               _text(getattr(self, field_name), field_name))
        if self.compatibility_profile is not None:
            object.__setattr__(self, "compatibility_profile",
                               _text(self.compatibility_profile, "compatibility_profile"))
        transitions = _strings(self.failure_transition_ids, "failure_transition_ids")
        lineages = _strings(self.evidence_lineages, "evidence_lineages")
        resolutions = _strings(self.resolution_ids, "resolution_ids")
        digests = _ordered_strings(self.oracle_digests, "oracle_digests")
        if not (len(transitions) == len(digests)):
            raise ValueError(
                "repeated failure transition IDs and oracle digests must align")
        if (not isinstance(self.oracle_complete, (list, tuple)) or
                len(self.oracle_complete) != len(transitions) or
                any(type(item) is not bool for item in self.oracle_complete)):
            raise ValueError(
                "repeated failure transition IDs and oracle completeness must align")
        if not all(self.oracle_complete):
            raise ValueError(
                "repeated failure receipt requires complete executable oracles")
        if len(transitions) < 2:
            raise ValueError("repeated failure receipt requires at least two failures")
        if len(lineages) < 2 and len(resolutions) < 2:
            raise ValueError(
                "repeated failure receipt requires two independent lineages or resolutions")
        if type(self.learner_eligible) is not bool:
            raise ValueError("repeated failure receipt learner_eligible must be boolean")
        if self.version != REPEATED_FAILURE_RECEIPT_VERSION:
            raise ValueError("repeated failure receipt version is invalid")
        object.__setattr__(self, "failure_transition_ids", transitions)
        object.__setattr__(self, "evidence_lineages", lineages)
        object.__setattr__(self, "resolution_ids", resolutions)
        object.__setattr__(self, "oracle_digests", digests)
        object.__setattr__(self, "oracle_complete", tuple(self.oracle_complete))

    @property
    def independent_observation_count(self) -> int:
        return max(len(self.evidence_lineages), len(self.resolution_ids))

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "campaign_id": self.campaign_id,
            "mechanism_family": self.mechanism_family,
            "compatibility_profile": self.compatibility_profile,
            "failure_family": self.failure_family,
            "failure_transition_ids": list(self.failure_transition_ids),
            "evidence_lineages": list(self.evidence_lineages),
            "resolution_ids": list(self.resolution_ids),
            "oracle_digests": list(self.oracle_digests),
            "learner_eligible": self.learner_eligible,
            "oracle_complete": list(self.oracle_complete),
        }

    @property
    def receipt_digest(self) -> str:
        return "sha256:" + hashlib.sha256(
            stable_dumps(self.to_dict()).encode()).hexdigest()

    @property
    def receipt_id(self) -> str:
        return "repeated_failure_" + self.receipt_digest.split(":", 1)[1][:24]

    @classmethod
    def from_dict(cls, payload: object) -> "RepeatedFailureReceipt":
        if not isinstance(payload, Mapping):
            raise ValueError("repeated failure receipt must be an object")
        required = set(cls.__dataclass_fields__) - {"version"}
        if not required <= set(payload):
            raise ValueError("repeated failure receipt is missing fields")
        receipt = cls(
            version=payload.get("version", REPEATED_FAILURE_RECEIPT_VERSION),
            campaign_id=payload["campaign_id"],
            mechanism_family=payload["mechanism_family"],
            compatibility_profile=payload["compatibility_profile"],
            failure_family=payload["failure_family"],
            failure_transition_ids=tuple(payload["failure_transition_ids"]),
            evidence_lineages=tuple(payload["evidence_lineages"]),
            resolution_ids=tuple(payload["resolution_ids"]),
            oracle_digests=tuple(payload["oracle_digests"]),
            learner_eligible=payload["learner_eligible"],
            oracle_complete=tuple(payload["oracle_complete"]),
        )
        if payload.get("receipt_digest") not in (None, receipt.receipt_digest):
            raise ValueError("repeated failure receipt digest mismatch")
        if payload.get("receipt_id") not in (None, receipt.receipt_id):
            raise ValueError("repeated failure receipt ID mismatch")
        return receipt


def _failure_family(facts: TransitionFacts) -> str:
    payload = facts.action.get("payload") or {}
    explicit = payload.get("failure_family") or payload.get("failure_type")
    if explicit:
        return str(explicit)
    # The first implementation has a conservative coarse family.  It never
    # invents a model label: absent an explicit typed failure class, the
    # canonical mechanism family is the only stable executable grouping key.
    return facts.mechanism_family


def detect_repeated_failures(
        conn: sqlite3.Connection, *, campaign_id: str = "live",
        mechanism_family: str | None = None,
        min_independent_observations: int = 2,
) -> list[RepeatedFailureReceipt]:
    """Aggregate complete learner failures across distinct lineages/resolutions."""
    if not campaign_id:
        raise ValueError("campaign_id is required")
    if min_independent_observations < 2:
        raise ValueError("repeated failure threshold must be at least two")
    rows = conn.execute(
        """SELECT t.transition_id
             FROM tehm_transitions t
            WHERE t.outcome IN ('FAIL', 'REGRESSION')
              AND EXISTS (SELECT 1 FROM tehm_dataset_membership dm
                            WHERE dm.transition_id=t.transition_id
                              AND dm.campaign_id=? AND dm.split='training'
                              AND dm.learner_eligible=1)
            ORDER BY t.transition_id""", (campaign_id,)).fetchall()
    groups: dict[tuple[str, str | None, str], list[TransitionFacts]] = defaultdict(list)
    for row in rows:
        facts = load_transition_facts(conn, row["transition_id"])
        if mechanism_family is not None and facts.mechanism_family != mechanism_family:
            continue
        try:
            require_verified_transition(conn, facts.transition_id)
        except ValueError:
            # An incomplete/unknown oracle is not a repeated-failure witness.
            continue
        groups[(facts.mechanism_family, facts.compatibility_profile,
                _failure_family(facts))].append(facts)
    receipts: list[RepeatedFailureReceipt] = []
    for (family, profile, failure_family), facts in sorted(
            groups.items(), key=lambda item: str(item[0])):
        transitions = tuple(sorted({item.transition_id for item in facts}))
        lineages = tuple(sorted({item.lineage_id for item in facts if item.lineage_id}))
        resolutions = tuple(sorted({item.target_state["state_id"] for item in facts
                                    if item.target_state.get("state_id")}))
        independent = max(len(lineages), len(resolutions))
        if len(transitions) < min_independent_observations or independent < min_independent_observations:
            continue
        oracle_digests = tuple(
            "sha256:" + hashlib.sha256(stable_dumps(item.verifier).encode()).hexdigest()
            for item in sorted(facts, key=lambda value: value.transition_id))
        receipts.append(RepeatedFailureReceipt(
            campaign_id=campaign_id, mechanism_family=family,
            compatibility_profile=profile, failure_family=failure_family,
            failure_transition_ids=transitions, evidence_lineages=lineages,
            resolution_ids=resolutions, oracle_digests=oracle_digests,
            learner_eligible=True,
            oracle_complete=tuple(True for _ in oracle_digests)))
    return receipts


__all__ = [
    "REPEATED_FAILURE_RECEIPT_VERSION", "RepeatedFailureReceipt",
    "detect_repeated_failures",
]
