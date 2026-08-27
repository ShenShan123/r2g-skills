"""Deterministic shadow operation decisions for online consolidation.

Triggers answer *whether* a consolidation condition exists.  This module
answers the separate question of *what operation should be proposed*.  It is
intentionally pure: the returned operation is an auditable hypothesis and
never changes rule lifecycle or production authority.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .receipts import IncrementalCrystallizationReceipt
from .triggers import ConsolidationTriggerReceipt

CONSOLIDATION_OPERATIONS = frozenset({
    "RETAIN", "ADD", "MERGE", "SPECIALIZE", "GENERALIZE", "REVISE",
    "SPLIT", "DEMOTE", "QUARANTINE", "RETIRE", "ROLLBACK",
})


@dataclass(frozen=True)
class ConsolidationDecisionReceipt:
    transition_id: str
    campaign_id: str
    learner_eligible: bool
    triggered: bool
    operation: str
    reasons: tuple[str, ...] = field(default_factory=tuple)
    affected_effect_keys: tuple[str, ...] = field(default_factory=tuple)
    candidate_rule_ids: tuple[str, ...] = field(default_factory=tuple)
    full_rebuild_equivalent: bool | None = None
    rationale: str = ""
    authority: str = "shadow_only"

    def to_dict(self) -> dict:
        return {
            "transition_id": self.transition_id,
            "campaign_id": self.campaign_id,
            "learner_eligible": self.learner_eligible,
            "triggered": self.triggered,
            "operation": self.operation,
            "reasons": list(self.reasons),
            "affected_effect_keys": list(self.affected_effect_keys),
            "candidate_rule_ids": list(self.candidate_rule_ids),
            "full_rebuild_equivalent": self.full_rebuild_equivalent,
            "rationale": self.rationale,
            "authority": self.authority,
        }


def decide_consolidation(
    trigger: ConsolidationTriggerReceipt,
    preview: IncrementalCrystallizationReceipt | None = None,
) -> ConsolidationDecisionReceipt:
    """Choose a shadow operation from trigger facts and an optional preview.

    Priority is deliberately conservative: harmful evidence quarantines before
    any merge/add proposal, explicit conflicts split, and a non-crystallizable
    trigger retains fast memory.  ``ADD``/``MERGE``/``REVISE`` are proposals
    only; an independent candidate trial and authority call remain required.
    """
    if preview is not None:
        if preview.mode != "preview":
            raise ValueError("consolidation preview must use mode=preview")
        if preview.campaign_id != trigger.campaign_id:
            raise ValueError("trigger and preview campaigns must match")
    candidate_rule_ids = tuple(sorted(
        rule["rule_id"] for rule in (preview.rules if preview else ())))
    equivalent = preview.full_rebuild_equivalent if preview else None
    reasons = tuple(trigger.reasons)
    operation = "RETAIN"
    if trigger.learner_eligible and trigger.triggered:
        if "HARMFUL_ACTIVATION" in reasons:
            operation = "QUARANTINE"
            rationale = "harmful activation takes precedence over consolidation"
        elif trigger.conflict_types:
            operation = "SPLIT"
            rationale = "conflicting evidence requires separate rule branches"
        elif not candidate_rule_ids:
            rationale = "triggered evidence has no crystallizable repeat group"
        elif "NOVEL_MECHANISM" in reasons:
            operation = "ADD"
            rationale = "novel learner mechanism has a crystallizable shadow rule"
        elif "SUFFICIENT_SUPPORT" in reasons:
            operation = "MERGE"
            rationale = "support threshold reached for an existing effect group"
        else:
            operation = "REVISE"
            rationale = "triggered evidence proposes a shadow rule revision"
    elif not trigger.learner_eligible:
        rationale = "learner-ineligible evidence is retained for audit only"
    else:
        rationale = "no consolidation trigger; retain fast memory"
    return ConsolidationDecisionReceipt(
        transition_id=trigger.transition_id,
        campaign_id=trigger.campaign_id,
        learner_eligible=trigger.learner_eligible,
        triggered=trigger.triggered,
        operation=operation,
        reasons=reasons,
        affected_effect_keys=trigger.affected_effect_keys,
        candidate_rule_ids=candidate_rule_ids,
        full_rebuild_equivalent=equivalent,
        rationale=rationale,
    )


__all__ = ["CONSOLIDATION_OPERATIONS", "ConsolidationDecisionReceipt",
           "decide_consolidation"]
