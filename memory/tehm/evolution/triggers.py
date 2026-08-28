"""Deterministic consolidation-trigger evaluation for online memory."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from tehm.canonical.transition import HARMFUL_OUTCOMES
from tehm.causal.mechanism import load_transition_facts, mechanism_signature
from tehm.dataset import require_learner_bool, validate_membership_row

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
    # Typed witnesses are propagated from the transition rather than inferred
    # by a later consumer from effect-group names.
    mechanism_signature: dict = field(default_factory=dict)
    affected_rule_ids: tuple[str, ...] = ()
    affected_path_ids: tuple[str, ...] = ()

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
            "mechanism_signature": dict(self.mechanism_signature or {}),
            "affected_rule_ids": list(self.affected_rule_ids),
            "affected_path_ids": list(self.affected_path_ids),
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
    affected_rule_ids: tuple[str, ...] = (),
    affected_path_ids: tuple[str, ...] = (),
) -> ConsolidationTriggerReceipt:
    """Evaluate online trigger reasons without mutating rules or lifecycle.

    The caller supplies novelty/conflict receipts already created for this
    transition.  Support is counted only in the explicit learner-eligible
    campaign; held-out and calibration rows can never trigger consolidation.
    """
    if not transition_id or not campaign_id:
        raise ValueError("transition_id and campaign_id are required")
    learner_eligible = require_learner_bool(learner_eligible)
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
    try:
        stored_eligible, split = validate_membership_row(membership)
    except ValueError as exc:
        # Keep a contradictory non-training row audit-only for compatibility
        # with legacy databases; weakly typed values still fail closed.
        if str(exc) != "non-training dataset membership cannot be learner-eligible":
            raise
        stored_eligible, split = False, membership["split"]
    authority_eligible = stored_eligible and split == "training"
    if learner_eligible != authority_eligible:
        raise ValueError(
            "consolidation trigger learner_eligible conflicts with dataset membership")
    # The database membership is the authority; the argument is retained only
    # as a consistency check for callers and compatibility with the existing
    # manager seam.
    learner_eligible = authority_eligible
    facts = load_transition_facts(conn, transition_id)
    signature = mechanism_signature(facts)
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
            conflict_types=tuple(conflict.conflict_types),
            mechanism_signature=signature,
            affected_rule_ids=tuple(sorted(str(value) for value in affected_rule_ids
                                           if str(value))),
            affected_path_ids=tuple(sorted(str(value) for value in affected_path_ids
                                           if str(value))))

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
        conflict_types=tuple(conflict.conflict_types),
        mechanism_signature=signature,
        affected_rule_ids=tuple(sorted(str(value) for value in affected_rule_ids
                                       if str(value))),
        affected_path_ids=tuple(sorted(str(value) for value in affected_path_ids
                                       if str(value))))


__all__ = ["TRIGGER_REASONS", "ConsolidationTriggerReceipt",
           "evaluate_consolidation_trigger"]
