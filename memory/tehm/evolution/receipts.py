"""Receipts for online memory evolution operations."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MemoryEventReceipt:
    event_id: str
    event_type: str
    source_type: str
    source_id: str
    campaign_id: str | None
    learner_eligible: bool
    previous_event_digest: str | None
    event_digest: str

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "campaign_id": self.campaign_id,
            "learner_eligible": self.learner_eligible,
            "previous_event_digest": self.previous_event_digest,
            "event_digest": self.event_digest,
        }


@dataclass(frozen=True)
class OnlineMemoryReceipt:
    transition_id: str
    campaign_id: str
    learner_eligible: bool
    fragment: object | None
    # Typed fast-memory witnesses.  These are derived references for a later
    # shadow consolidation pass; they never grant lifecycle authority.
    mechanism_signature: dict = field(default_factory=dict)
    affected_rule_ids: tuple[str, ...] = field(default_factory=tuple)
    affected_path_ids: tuple[str, ...] = field(default_factory=tuple)
    events: tuple[MemoryEventReceipt, ...] = field(default_factory=tuple)
    novelty: str = "UNKNOWN"
    consolidation_triggered: bool = False
    path_id: str | None = None
    trigger_reasons: tuple[str, ...] = field(default_factory=tuple)
    affected_effect_keys: tuple[str, ...] = field(default_factory=tuple)
    trigger_event_id: str | None = None
    consolidation_preview: object | None = None
    consolidation_operation: str = "RETAIN"
    consolidation_decision: object | None = None

    def to_dict(self) -> dict:
        return {
            "transition_id": self.transition_id,
            "campaign_id": self.campaign_id,
            "learner_eligible": self.learner_eligible,
            "fragment": self.fragment.to_dict() if self.fragment else None,
            "mechanism_signature": dict(self.mechanism_signature),
            "affected_rule_ids": list(self.affected_rule_ids),
            "affected_path_ids": list(self.affected_path_ids),
            "events": [event.to_dict() for event in self.events],
            "novelty": self.novelty,
            "consolidation_triggered": self.consolidation_triggered,
            "path_id": self.path_id,
            "trigger_reasons": list(self.trigger_reasons),
            "affected_effect_keys": list(self.affected_effect_keys),
            "trigger_event_id": self.trigger_event_id,
            "consolidation_preview": (
                self.consolidation_preview.to_dict()
                if self.consolidation_preview is not None else None),
            "consolidation_operation": self.consolidation_operation,
            "consolidation_decision": (
                self.consolidation_decision.to_dict()
                if self.consolidation_decision is not None else None),
        }


@dataclass(frozen=True)
class IncrementalCrystallizationReceipt:
    campaign_id: str
    transition_ids: tuple[str, ...]
    affected_effect_keys: tuple[str, ...]
    rules: tuple[dict, ...]
    full_rebuild_equivalent: bool | None = None
    full_rebuild_rule_ids: tuple[str, ...] = field(default_factory=tuple)
    mode: str = "persist"
    affected_group_keys: tuple[tuple[str, str | None], ...] = field(
        default_factory=tuple)
    raw_evidence_before_digest: str | None = None
    raw_evidence_after_digest: str | None = None
    raw_evidence_preserved: bool | None = None

    def to_dict(self) -> dict:
        return {
            "campaign_id": self.campaign_id,
            "transition_ids": list(self.transition_ids),
            "affected_effect_keys": list(self.affected_effect_keys),
            "rules": list(self.rules),
            "full_rebuild_equivalent": self.full_rebuild_equivalent,
            "full_rebuild_rule_ids": list(self.full_rebuild_rule_ids),
            "mode": self.mode,
            "affected_group_keys": [list(group) for group in self.affected_group_keys],
            "raw_evidence_before_digest": self.raw_evidence_before_digest,
            "raw_evidence_after_digest": self.raw_evidence_after_digest,
            "raw_evidence_preserved": self.raw_evidence_preserved,
        }


@dataclass(frozen=True)
class RuleRevisionReceipt:
    revision_id: str
    parent_rule_id: str | None
    child_rule_id: str
    operation: str
    trigger_event_id: str
    evidence_refs: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "revision_id": self.revision_id,
            "parent_rule_id": self.parent_rule_id,
            "child_rule_id": self.child_rule_id,
            "operation": self.operation,
            "trigger_event_id": self.trigger_event_id,
            "evidence_refs": list(self.evidence_refs),
        }


__all__ = ["MemoryEventReceipt", "OnlineMemoryReceipt",
           "IncrementalCrystallizationReceipt", "RuleRevisionReceipt"]
