"""Localized, shadow-only update planning for online evolution.

The planner consumes typed value, attribution, and state-resolution receipts
and emits a deterministic proposal.  It deliberately does not execute a
rule/knowledge/asset mutation; each target remains behind its own authority
boundary.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from collections.abc import Mapping

from tehm.ids import stable_dumps

from .attribution import (
    UPDATE_TARGETS, MemoryFailureAttributionReceipt,
)
from .value_receipts import ExperienceValueReceipt, VALUE_PRIORITIES


UPDATE_OPERATIONS = frozenset({
    "RETAIN", "ADD", "REVISE", "SPECIALIZE", "GENERALIZE", "SPLIT",
    "MERGE", "SUPERSEDE", "INVALIDATE", "REACTIVATE",
})

_LAYER_TO_TARGET = {
    "STATE": "UPDATE_STATE_RELATION",
    "CAUSAL": "UPDATE_CAUSAL_KNOWLEDGE",
    "RULE": "UPDATE_RULE",
    "ASSET": "UPDATE_ASSET",
    "CAPABILITY": "UPDATE_CAPABILITY",
    "NONE": "UPDATE_NONE",
}


def _strings(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"localized update plan {field_name} must be a sequence")
    values = tuple(value)
    if any(type(item) is not str or not item.strip() for item in values):
        raise ValueError(
            f"localized update plan {field_name} must contain non-empty strings")
    values = tuple(sorted(item.strip() for item in values))
    if len(set(values)) != len(values):
        raise ValueError(f"localized update plan {field_name} must not contain duplicates")
    return values


def _ordered_strings(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"localized update plan {field_name} must be a sequence")
    values = tuple(item.strip() if isinstance(item, str) else item for item in value)
    if any(type(item) is not str or not item for item in values):
        raise ValueError(
            f"localized update plan {field_name} must contain non-empty strings")
    if len(set(values)) != len(values):
        raise ValueError(f"localized update plan {field_name} must not contain duplicates")
    return values


def _unit(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("localized update plan value_score must be numeric")
    value = float(value)
    if not math.isfinite(value) or value < 0.0 or value > 1.0:
        raise ValueError("localized update plan value_score must be in [0, 1]")
    return value


@dataclass(frozen=True)
class LocalizedUpdatePlan:
    transition_id: str
    campaign_id: str
    learner_eligible: bool
    priority: str
    value_score: float
    update_target: str
    candidate_targets: tuple[str, ...]
    operation: str
    failure_type: str
    state_resolution_id: str | None = None
    knowledge_refs: tuple[str, ...] = field(default_factory=tuple)
    rule_refs: tuple[str, ...] = field(default_factory=tuple)
    asset_refs: tuple[str, ...] = field(default_factory=tuple)
    capability_refs: tuple[str, ...] = field(default_factory=tuple)
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    rationale: str = ""
    shadow_only: bool = True

    def __post_init__(self) -> None:
        for value, name in ((self.transition_id, "transition_id"),
                            (self.campaign_id, "campaign_id"),
                            (self.failure_type, "failure_type")):
            if type(value) is not str or not value.strip():
                raise ValueError(f"localized update plan {name} is invalid")
        if type(self.learner_eligible) is not bool:
            raise ValueError("localized update plan learner_eligible must be boolean")
        if self.priority not in VALUE_PRIORITIES:
            raise ValueError(f"invalid localized update plan priority: {self.priority!r}")
        score = _unit(self.value_score)
        if self.update_target not in UPDATE_TARGETS:
            raise ValueError("localized update plan target is invalid")
        targets = _ordered_strings(self.candidate_targets, "candidate_targets")
        if any(target not in UPDATE_TARGETS for target in targets):
            raise ValueError("localized update plan candidate target is invalid")
        if not targets:
            raise ValueError("localized update plan requires candidate targets")
        if self.update_target not in targets:
            raise ValueError("localized update plan target is not in candidates")
        if self.operation not in UPDATE_OPERATIONS:
            raise ValueError(f"invalid localized update plan operation: {self.operation!r}")
        if self.update_target == "UPDATE_NONE" and self.operation != "RETAIN":
            raise ValueError("UPDATE_NONE must use RETAIN")
        if self.update_target != "UPDATE_NONE" and self.operation == "RETAIN":
            raise ValueError("non-empty update target cannot use RETAIN")
        if type(self.shadow_only) is not bool or not self.shadow_only:
            raise ValueError("localized update plan must remain shadow-only")
        if self.state_resolution_id is not None and (
                type(self.state_resolution_id) is not str or not self.state_resolution_id.strip()):
            raise ValueError("localized update plan state resolution ID is invalid")
        for name in ("knowledge_refs", "rule_refs", "asset_refs", "capability_refs", "evidence_refs"):
            object.__setattr__(self, name, _strings(getattr(self, name), name))
        if type(self.rationale) is not str or not self.rationale.strip():
            raise ValueError("localized update plan rationale is required")
        if not self.learner_eligible and self.update_target != "UPDATE_NONE":
            raise ValueError("audit-only evidence cannot select a localized update")
        object.__setattr__(self, "value_score", score)
        object.__setattr__(self, "candidate_targets", targets)
        object.__setattr__(self, "rationale", self.rationale.strip())

    def to_dict(self) -> dict:
        return {
            "transition_id": self.transition_id,
            "campaign_id": self.campaign_id,
            "learner_eligible": self.learner_eligible,
            "priority": self.priority,
            "value_score": self.value_score,
            "update_target": self.update_target,
            "candidate_targets": list(self.candidate_targets),
            "operation": self.operation,
            "failure_type": self.failure_type,
            "state_resolution_id": self.state_resolution_id,
            "knowledge_refs": list(self.knowledge_refs),
            "rule_refs": list(self.rule_refs),
            "asset_refs": list(self.asset_refs),
            "capability_refs": list(self.capability_refs),
            "evidence_refs": list(self.evidence_refs),
            "rationale": self.rationale,
            "shadow_only": self.shadow_only,
        }

    @property
    def plan_digest(self) -> str:
        return "sha256:" + hashlib.sha256(
            stable_dumps(self.to_dict()).encode()).hexdigest()

    @classmethod
    def from_dict(cls, payload: object) -> "LocalizedUpdatePlan":
        if not isinstance(payload, Mapping):
            raise ValueError("localized update plan must be an object")
        required = {
            "transition_id", "campaign_id", "learner_eligible", "priority",
            "value_score", "update_target", "candidate_targets", "operation",
            "failure_type", "state_resolution_id", "knowledge_refs", "rule_refs",
            "asset_refs", "capability_refs", "evidence_refs", "rationale",
            "shadow_only",
        }
        if any(key not in payload for key in required):
            raise ValueError("localized update plan is missing required fields")
        return cls(
            transition_id=payload["transition_id"], campaign_id=payload["campaign_id"],
            learner_eligible=payload["learner_eligible"], priority=payload["priority"],
            value_score=payload["value_score"], update_target=payload["update_target"],
            candidate_targets=tuple(payload["candidate_targets"]), operation=payload["operation"],
            failure_type=payload["failure_type"], state_resolution_id=payload["state_resolution_id"],
            knowledge_refs=tuple(payload["knowledge_refs"]), rule_refs=tuple(payload["rule_refs"]),
            asset_refs=tuple(payload["asset_refs"]), capability_refs=tuple(payload["capability_refs"]),
            evidence_refs=tuple(payload["evidence_refs"]), rationale=payload["rationale"],
            shadow_only=payload["shadow_only"],
        )


LocalizedUpdatePlanReceipt = LocalizedUpdatePlan


def _ordered_targets(value: ExperienceValueReceipt,
                    attribution: MemoryFailureAttributionReceipt) -> tuple[str, ...]:
    selected: list[str] = []
    for target in attribution.recommended_update_layers:
        if target not in selected:
            selected.append(target)
    for layer in value.update_layers:
        target = _LAYER_TO_TARGET[layer]
        if target not in selected:
            selected.append(target)
    if not selected:
        selected.append("UPDATE_NONE")
    if "UPDATE_NONE" in selected and len(selected) > 1:
        selected.remove("UPDATE_NONE")
    return tuple(selected)


def plan_localized_update(
    value: ExperienceValueReceipt, attribution: MemoryFailureAttributionReceipt,
    *, state_resolution=None, knowledge_refs=(), rule_refs=(), asset_refs=(),
    capability_refs=(), evidence_refs=(),
) -> LocalizedUpdatePlan:
    """Select one update target while retaining all candidate targets."""
    if not isinstance(value, ExperienceValueReceipt):
        raise TypeError("localized update planning requires ExperienceValueReceipt")
    if not isinstance(attribution, MemoryFailureAttributionReceipt):
        raise TypeError("localized update planning requires attribution receipt")
    if attribution.transition_id != value.transition_id:
        raise ValueError("localized update value/attribution transition mismatch")
    targets = _ordered_targets(value, attribution)
    if not value.update_layers or value.update_layers == ("NONE",) or not targets:
        targets = ("UPDATE_NONE",)
    if not value.campaign_id or attribution.transition_id is None:
        targets = ("UPDATE_NONE",)
    if "UPDATE_NONE" in targets and len(targets) > 1:
        targets = tuple(target for target in targets if target != "UPDATE_NONE")
    target = targets[0]
    if attribution.failure_type in {"VERIFICATION_FAILURE", "AUTHORITY_FAILURE"}:
        target = "UPDATE_NONE"
        targets = ("UPDATE_NONE",)
    if not value.campaign_id:
        target = "UPDATE_NONE"
        targets = ("UPDATE_NONE",)
    if not value.transition_id:
        target = "UPDATE_NONE"
        targets = ("UPDATE_NONE",)
    operation = "RETAIN"
    if target != "UPDATE_NONE":
        if attribution.failure_type in {"MEMORY_INTERFERENCE", "STATE_RESOLUTION_FAILURE"}:
            operation = "INVALIDATE"
        elif target == "UPDATE_CAUSAL_KNOWLEDGE" and value.novelty >= 1.0:
            operation = "ADD"
        elif target == "UPDATE_STATE_RELATION" and value.novelty >= 1.0:
            operation = "SUPERSEDE"
        else:
            operation = "REVISE"
    rationale = (
        f"{attribution.failure_type} selects {target}; "
        f"value priority={value.priority} remains shadow-only")
    state_id = getattr(state_resolution, "resolution_id", None)
    return LocalizedUpdatePlan(
        transition_id=value.transition_id, campaign_id=value.campaign_id,
        learner_eligible=True if value.update_layers != ("NONE",) else False,
        priority=value.priority, value_score=value.value_score,
        update_target=target, candidate_targets=targets, operation=operation,
        failure_type=attribution.failure_type, state_resolution_id=state_id,
        knowledge_refs=tuple(knowledge_refs), rule_refs=tuple(rule_refs),
        asset_refs=tuple(asset_refs), capability_refs=tuple(capability_refs),
        evidence_refs=tuple(evidence_refs), rationale=rationale, shadow_only=True)


__all__ = [
    "UPDATE_OPERATIONS", "LocalizedUpdatePlan", "LocalizedUpdatePlanReceipt",
    "plan_localized_update",
]
