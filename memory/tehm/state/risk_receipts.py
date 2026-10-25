"""Replayable evidence for typed ``NO_SKILL/RISK`` routing decisions.

The router may refuse an otherwise applicable memory candidate when an
explicit expected-utility witness says the predicted harm outweighs the
expected gain.  This receipt keeps that refusal auditable without treating the
caller-provided utility as authority or as an execution outcome.
"""
from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from dataclasses import dataclass

from tehm.ids import stable_dumps


RISK_RECEIPT_VERSION = "risk-receipt-v0.1"


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


@dataclass(frozen=True)
class RiskReceipt:
    """Content-addressed expected-utility evidence for a risk refusal."""

    current_resolution_id: str
    expected_utility: float
    evidence_refs: tuple[str, ...]
    risk_model: str
    replay_digest: str
    reason: str = "RISK"
    version: str = RISK_RECEIPT_VERSION

    def _payload(self) -> dict:
        return {
            "version": self.version,
            "current_resolution_id": self.current_resolution_id,
            "expected_utility": self.expected_utility,
            "evidence_refs": list(self.evidence_refs),
            "risk_model": self.risk_model,
            "reason": self.reason,
        }

    def __post_init__(self) -> None:
        if (type(self.current_resolution_id) is not str or
                not self.current_resolution_id.strip()):
            raise ValueError("risk receipt current_resolution_id is required")
        if (isinstance(self.expected_utility, bool) or
                not isinstance(self.expected_utility, (int, float)) or
                not math.isfinite(float(self.expected_utility))):
            raise ValueError("risk receipt expected_utility must be finite")
        if not isinstance(self.evidence_refs, tuple) or not self.evidence_refs:
            raise ValueError("risk receipt evidence_refs are required")
        if any(type(item) is not str or not item.strip()
               for item in self.evidence_refs):
            raise ValueError("risk receipt evidence_refs are invalid")
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("risk receipt evidence_refs contain duplicates")
        object.__setattr__(self, "evidence_refs",
                           tuple(sorted(item.strip() for item in self.evidence_refs)))
        if type(self.risk_model) is not str or not self.risk_model.strip():
            raise ValueError("risk receipt risk_model is required")
        if self.reason != "RISK":
            raise ValueError("risk receipt reason is invalid")
        if type(self.version) is not str or not self.version.strip():
            raise ValueError("risk receipt version is required")
        if (type(self.replay_digest) is not str or
                self.replay_digest != _digest(self._payload())):
            raise ValueError("risk receipt replay digest mismatch")

    @property
    def receipt_id(self) -> str:
        return "risk_" + self.replay_digest.split(":", 1)[1][:24]

    def to_dict(self) -> dict:
        return {**self._payload(), "replay_digest": self.replay_digest,
                "receipt_id": self.receipt_id}

    @classmethod
    def from_dict(cls, payload: object) -> "RiskReceipt":
        if not isinstance(payload, Mapping):
            raise ValueError("risk receipt must be an object")
        required = {
            "current_resolution_id", "expected_utility", "evidence_refs",
            "risk_model", "replay_digest", "reason",
        }
        if not required <= set(payload):
            raise ValueError("risk receipt is missing fields")
        refs = payload["evidence_refs"]
        if (not isinstance(refs, (list, tuple)) or
                isinstance(refs, (str, bytes))):
            raise ValueError("risk receipt evidence_refs must be a sequence")
        receipt = cls(
            current_resolution_id=payload["current_resolution_id"],
            expected_utility=payload["expected_utility"],
            evidence_refs=tuple(refs), risk_model=payload["risk_model"],
            replay_digest=payload["replay_digest"], reason=payload["reason"],
            version=payload.get("version", RISK_RECEIPT_VERSION),
        )
        supplied_id = payload.get("receipt_id")
        if supplied_id is not None and supplied_id != receipt.receipt_id:
            raise ValueError("risk receipt ID mismatch")
        return receipt


__all__ = ["RISK_RECEIPT_VERSION", "RiskReceipt"]
