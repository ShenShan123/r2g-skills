"""Typed receipts for deterministic Experience Value selection.

The receipt is a derived decision about *which update layer may be worth
considering*.  It is never an authority grant and it never replaces the
immutable canonical transition.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


VALUE_PRIORITIES = ("P0_CRITICAL", "P1_HIGH", "P2_MEDIUM", "P3_LOW")
UPDATE_LAYERS = ("STATE", "CAUSAL", "RULE", "ASSET", "CAPABILITY", "NONE")


def _unit(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"experience value {field_name} must be a number")
    value = float(value)
    if not math.isfinite(value) or value < 0.0 or value > 1.0:
        raise ValueError(
            f"experience value {field_name} must be finite in [0, 1]")
    return value


def _strings(values: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        raise ValueError(f"experience value {field_name} must be a sequence")
    result = tuple(values)
    if any(type(value) is not str or not value for value in result):
        raise ValueError(
            f"experience value {field_name} must contain non-empty strings")
    if len(set(result)) != len(result):
        raise ValueError(f"experience value {field_name} must not contain duplicates")
    return result


@dataclass(frozen=True)
class ExperienceValueReceipt:
    """Content-addressed, auditable value decision for one transition."""

    transition_id: str
    campaign_id: str
    novelty: float
    severity: float
    capability_gap: float
    causal_discrimination: float
    surprise: float
    counterexample: float
    memory_interference: float
    redundancy: float
    value_score: float
    priority: str
    update_layers: tuple[str, ...] = field(default_factory=tuple)
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        for field_name in (
                "novelty", "severity", "capability_gap",
                "causal_discrimination", "surprise", "counterexample",
                "memory_interference", "redundancy", "value_score"):
            _unit(getattr(self, field_name), field_name)
        for field_name in ("transition_id", "campaign_id"):
            value = getattr(self, field_name)
            if type(value) is not str or not value:
                raise ValueError(f"experience value {field_name} is required")
        if self.priority not in VALUE_PRIORITIES:
            raise ValueError(f"invalid experience value priority: {self.priority!r}")
        layers = _strings(self.update_layers, "update_layers")
        if any(layer not in UPDATE_LAYERS for layer in layers):
            raise ValueError("experience value update_layers contains an unknown layer")
        if "NONE" in layers and len(layers) != 1:
            raise ValueError("experience value NONE cannot accompany another layer")
        _strings(self.reasons, "reasons")

    def to_dict(self) -> dict:
        return {
            "transition_id": self.transition_id,
            "campaign_id": self.campaign_id,
            "novelty": self.novelty,
            "severity": self.severity,
            "capability_gap": self.capability_gap,
            "causal_discrimination": self.causal_discrimination,
            "surprise": self.surprise,
            "counterexample": self.counterexample,
            "memory_interference": self.memory_interference,
            "redundancy": self.redundancy,
            "value_score": self.value_score,
            "priority": self.priority,
            "update_layers": list(self.update_layers),
            "reasons": list(self.reasons),
        }

    @classmethod
    def from_dict(cls, payload: object) -> "ExperienceValueReceipt":
        if not isinstance(payload, dict):
            raise ValueError("experience value receipt must be an object")
        required = (
            "transition_id", "campaign_id", "novelty", "severity",
            "capability_gap", "causal_discrimination", "surprise",
            "counterexample", "memory_interference", "redundancy",
            "value_score", "priority", "update_layers", "reasons",
        )
        if any(key not in payload for key in required):
            raise ValueError("experience value receipt is missing required fields")
        return cls(
            transition_id=payload["transition_id"], campaign_id=payload["campaign_id"],
            novelty=payload["novelty"], severity=payload["severity"],
            capability_gap=payload["capability_gap"],
            causal_discrimination=payload["causal_discrimination"],
            surprise=payload["surprise"], counterexample=payload["counterexample"],
            memory_interference=payload["memory_interference"],
            redundancy=payload["redundancy"], value_score=payload["value_score"],
            priority=payload["priority"],
            update_layers=tuple(payload["update_layers"]),
            reasons=tuple(payload["reasons"]),
        )


__all__ = ["ExperienceValueReceipt", "UPDATE_LAYERS", "VALUE_PRIORITIES"]
