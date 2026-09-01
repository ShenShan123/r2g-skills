"""Typed receipts for evaluation-only structured repair candidates."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import hashlib

from tehm.ids import stable_dumps


@dataclass(frozen=True)
class StructuredCandidateReceipt:
    """Small immutable receipt paired with a StructuredRepairCandidate."""

    candidate_id: str
    candidate_digest: str
    resolved_state_id: str
    knowledge_object_id: str
    asset_id: str
    applicability_receipt_id: str
    binding_receipt_id: str
    evaluation_only: bool = True
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        for field_name in (
                "candidate_id", "candidate_digest", "resolved_state_id",
                "knowledge_object_id", "asset_id", "applicability_receipt_id",
                "binding_receipt_id"):
            value = getattr(self, field_name)
            if type(value) is not str or not value.strip():
                raise ValueError(f"structured candidate {field_name} is required")
        if self.evaluation_only is not True:
            raise ValueError("structured candidate receipt must be evaluation-only")
        if not isinstance(self.reasons, tuple) or any(
                type(item) is not str or not item for item in self.reasons):
            raise ValueError("structured candidate receipt reasons are invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "candidate_digest": self.candidate_digest,
            "resolved_state_id": self.resolved_state_id,
            "knowledge_object_id": self.knowledge_object_id,
            "asset_id": self.asset_id,
            "applicability_receipt_id": self.applicability_receipt_id,
            "binding_receipt_id": self.binding_receipt_id,
            "evaluation_only": self.evaluation_only,
            "reasons": list(self.reasons),
        }

    @property
    def receipt_digest(self) -> str:
        return "sha256:" + hashlib.sha256(
            stable_dumps(self.to_dict()).encode()).hexdigest()

    @classmethod
    def from_dict(cls, payload: object) -> "StructuredCandidateReceipt":
        if not isinstance(payload, dict):
            raise ValueError("structured candidate receipt must be an object")
        required = {"candidate_id", "candidate_digest", "resolved_state_id",
                    "knowledge_object_id", "asset_id", "applicability_receipt_id",
                    "binding_receipt_id", "evaluation_only", "reasons"}
        if not required <= set(payload):
            raise ValueError("structured candidate receipt is missing fields")
        receipt = cls(
            candidate_id=payload["candidate_id"], candidate_digest=payload["candidate_digest"],
            resolved_state_id=payload["resolved_state_id"],
            knowledge_object_id=payload["knowledge_object_id"], asset_id=payload["asset_id"],
            applicability_receipt_id=payload["applicability_receipt_id"],
            binding_receipt_id=payload["binding_receipt_id"],
            evaluation_only=payload["evaluation_only"], reasons=tuple(payload["reasons"]))
        supplied = payload.get("receipt_digest")
        if supplied is not None and supplied != receipt.receipt_digest:
            raise ValueError("structured candidate receipt digest mismatch")
        return receipt


__all__ = ["StructuredCandidateReceipt"]
