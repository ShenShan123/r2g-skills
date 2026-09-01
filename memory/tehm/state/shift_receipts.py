"""Replayable receipts for typed state-shift evaluation."""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from collections.abc import Mapping

from tehm.ids import stable_dumps


STATE_SHIFT_VERSION = "state-shift-v0.1"
SHIFT_DIMENSIONS = (
    "structural_shift", "mechanism_shift", "flow_shift",
    "constraint_shift", "oracle_shift", "history_shift",
)


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


@dataclass(frozen=True)
class StateShiftReceipt:
    current_resolution_id: str
    knowledge_object_id: str
    support_envelope_digest: str
    structural_shift: float
    mechanism_shift: float
    flow_shift: float
    constraint_shift: float
    oracle_shift: float
    history_shift: float
    aggregate_shift: float
    shifted_dimensions: tuple[str, ...]
    transferable: bool
    reason: str
    evidence_refs: tuple[str, ...]
    replay_digest: str
    version: str = STATE_SHIFT_VERSION

    def _payload(self) -> dict:
        return {
            "version": self.version,
            "current_resolution_id": self.current_resolution_id,
            "knowledge_object_id": self.knowledge_object_id,
            "support_envelope_digest": self.support_envelope_digest,
            "structural_shift": self.structural_shift,
            "mechanism_shift": self.mechanism_shift,
            "flow_shift": self.flow_shift,
            "constraint_shift": self.constraint_shift,
            "oracle_shift": self.oracle_shift,
            "history_shift": self.history_shift,
            "aggregate_shift": self.aggregate_shift,
            "shifted_dimensions": list(self.shifted_dimensions),
            "transferable": self.transferable, "reason": self.reason,
            "evidence_refs": list(self.evidence_refs),
        }

    def __post_init__(self) -> None:
        for name in ("current_resolution_id", "knowledge_object_id",
                     "support_envelope_digest", "reason"):
            if type(getattr(self, name)) is not str or not getattr(self, name):
                raise ValueError(f"state shift {name} is required")
        if type(self.transferable) is not bool:
            raise ValueError("state shift transferable must be boolean")
        if self.reason not in {"NO_SHIFT", "STATE_SHIFT"}:
            raise ValueError("state shift reason is invalid")
        if not isinstance(self.shifted_dimensions, tuple) or any(
                item not in SHIFT_DIMENSIONS for item in self.shifted_dimensions):
            raise ValueError("state shift dimensions are invalid")
        if len(set(self.shifted_dimensions)) != len(self.shifted_dimensions):
            raise ValueError("state shift dimensions contain duplicates")
        if not isinstance(self.evidence_refs, tuple) or any(
                type(item) is not str or not item for item in self.evidence_refs):
            raise ValueError("state shift evidence_refs are invalid")
        for name in (*SHIFT_DIMENSIONS, "aggregate_shift"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"state shift {name} is not numeric")
            if not math.isfinite(float(value)) or not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"state shift {name} must be in [0,1]")
        expected = _digest(self._payload())
        if self.replay_digest != expected:
            raise ValueError("state shift replay digest mismatch")

    @property
    def receipt_id(self) -> str:
        return "state_shift_" + self.replay_digest.split(":", 1)[1][:24]

    def to_dict(self) -> dict:
        return {**self._payload(), "replay_digest": self.replay_digest,
                "receipt_id": self.receipt_id}

    @classmethod
    def from_dict(cls, payload: object) -> "StateShiftReceipt":
        if not isinstance(payload, Mapping):
            raise ValueError("state shift receipt must be an object")
        fields = set(cls.__dataclass_fields__) - {"version"}
        if not fields <= set(payload):
            raise ValueError("state shift receipt is missing fields")
        receipt = cls(
            current_resolution_id=payload["current_resolution_id"],
            knowledge_object_id=payload["knowledge_object_id"],
            support_envelope_digest=payload["support_envelope_digest"],
            structural_shift=payload["structural_shift"],
            mechanism_shift=payload["mechanism_shift"],
            flow_shift=payload["flow_shift"],
            constraint_shift=payload["constraint_shift"],
            oracle_shift=payload["oracle_shift"],
            history_shift=payload["history_shift"],
            aggregate_shift=payload["aggregate_shift"],
            shifted_dimensions=tuple(payload["shifted_dimensions"]),
            transferable=payload["transferable"], reason=payload["reason"],
            evidence_refs=tuple(payload["evidence_refs"]),
            replay_digest=payload["replay_digest"],
            version=payload.get("version", STATE_SHIFT_VERSION),
        )
        supplied = payload.get("receipt_id")
        if supplied is not None and supplied != receipt.receipt_id:
            raise ValueError("state shift receipt ID mismatch")
        return receipt


__all__ = ["STATE_SHIFT_VERSION", "SHIFT_DIMENSIONS", "StateShiftReceipt"]
