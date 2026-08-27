"""Deterministic consolidation-trigger evaluation for online memory."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from tehm.canonical.transition import HARMFUL_OUTCOMES
from tehm.causal.mechanism import load_transition_facts

from .conflict import ConflictReceipt


TRIGGER_REASONS = frozenset({
    "NOVEL_MECHANISM", "SUFFICIENT_SUPPORT", "RULE_CONFLICT",
    "HARMFUL_ACTIVATION",
})


@dataclass(frozen=True)
class ConsolidationTriggerReceipt:
    transition_id: str
    campaign_id: str
    learner_eligible: bool
    triggered: bool
    reasons: tuple[str, ...]
    affected_effect_keys: tuple[str, ...]
    support_count: int
    conflict_types: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "transition_id": self.transition_id,
            "campaign_id": self.campaign_id,
            "learner_eligible": self.learner_eligible,
            "triggered": self.triggered,
            "reasons": list(self.reasons),
            "affected_effect_keys": list(self.affected_effect_keys),
            "support_count": self.support_count,
            "conflict_types": list(self.conflict_types),
        }


def evaluate_consolidation_trigger(
    conn: sqlite3.Connection,
    transition_id: str,
    *,
    campaign_id: str,
    learner_eligible: bool,
    novelty: str,
    conflict: ConflictReceipt,
    min_support: int = 2,
) -> ConsolidationTriggerReceipt:
    """Evaluate online trigger reasons without mutating rules or lifecycle.

    The caller supplies novelty/conflict receipts already created for this
    transition.  Support is counted only in the explicit learner-eligible
    campaign; held-out and calibration rows can never trigger consolidation.
    """
    if not transition_id or not campaign_id:
        raise ValueError("transition_id and campaign_id are required")
    if min_support < 1:
        raise ValueError("min_support must be positive")
    if novelty not in {"NOVEL_MECHANISM", "KNOWN_MECHANISM"}:
        raise ValueError(f"invalid novelty status: {novelty!r}")
    if (conflict.transition_id != transition_id or
            conflict.campaign_id != campaign_id):
        raise ValueError(
            "consolidation trigger conflict receipt does not match transition/campaign")
    membership = conn.execute(
        """SELECT split, learner_eligible FROM tehm_dataset_membership
             WHERE transition_id=? AND campaign_id=?""",
        (transition_id, campaign_id)).fetchone()
    if membership is None:
        raise ValueError(
            "consolidation trigger requires explicit dataset membership")
    authority_eligible = bool(
        membership["learner_eligible"] and
        str(membership["split"]) == "training")
    if bool(learner_eligible) != authority_eligible:
        raise ValueError(
            "consolidation trigger learner_eligible conflicts with dataset membership")
    # The database membership is the authority; the argument is retained only
    # as a consistency check for callers and compatibility with the existing
    # manager seam.
    learner_eligible = authority_eligible
    facts = load_transition_facts(conn, transition_id)
    support_count = 0
    if learner_eligible:
        row = conn.execute(
            """SELECT COUNT(*) AS n
                 FROM tehm_transitions t
                 JOIN tehm_dataset_membership dm
                   ON dm.transition_id=t.transition_id
                  AND dm.campaign_id=? AND dm.split='training'
                  AND dm.learner_eligible=1
                WHERE t.primary_effect_key IS ?""",
            (campaign_id, facts.primary_effect_key)).fetchone()
        support_count = int(row["n"] if row else 0)
    if not learner_eligible:
        return ConsolidationTriggerReceipt(
            transition_id=transition_id, campaign_id=campaign_id,
            learner_eligible=False, triggered=False,
            reasons=("NOT_LEARNER_ELIGIBLE",), affected_effect_keys=(),
            support_count=support_count,
            conflict_types=tuple(conflict.conflict_types))

    reasons: set[str] = set()
    if novelty == "NOVEL_MECHANISM":
        reasons.add("NOVEL_MECHANISM")
    if support_count >= min_support:
        reasons.add("SUFFICIENT_SUPPORT")
    if conflict.has_conflict:
        reasons.add("RULE_CONFLICT")
    if (facts.outcome in HARMFUL_OUTCOMES or
            facts.delta.get("created_regressions") or
            facts.delta.get("newly_observed_failures")):
        reasons.add("HARMFUL_ACTIVATION")
    ordered = tuple(sorted(reasons))
    return ConsolidationTriggerReceipt(
        transition_id=transition_id, campaign_id=campaign_id,
        learner_eligible=True, triggered=bool(ordered), reasons=ordered,
        affected_effect_keys=((facts.primary_effect_key,)
                              if facts.primary_effect_key else ()),
        support_count=support_count,
        conflict_types=tuple(conflict.conflict_types))


__all__ = ["TRIGGER_REASONS", "ConsolidationTriggerReceipt",
           "evaluate_consolidation_trigger"]
