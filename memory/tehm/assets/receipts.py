"""Serializable asset, gap, and validation receipts."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from collections.abc import Mapping

from tehm.ids import stable_dumps


@dataclass(frozen=True)
class AssetReceipt:
    asset_id: str
    asset_type: str
    name: str
    version: str
    content_digest: str
    target_scope: str
    status: str
    status_version: int

    def to_dict(self) -> dict:
        return {
            "asset_id": self.asset_id, "asset_type": self.asset_type,
            "name": self.name, "version": self.version,
            "content_digest": self.content_digest,
            "target_scope": self.target_scope, "status": self.status,
            "status_version": self.status_version,
        }


@dataclass(frozen=True)
class CapabilityGapReceipt:
    gap_id: str
    mechanism_family: str
    compatibility_profile: str | None
    evidence_transitions: tuple[str, ...]
    evidence_lineages: tuple[str, ...]
    missing_asset_types: tuple[str, ...]
    reason: str
    current_action_coverage: dict = field(default_factory=dict)
    confidence: float = 0.0

    def __post_init__(self) -> None:
        for field_name in ("gap_id", "mechanism_family", "reason"):
            value = getattr(self, field_name)
            if type(value) is not str or not value.strip():
                raise ValueError(f"capability gap {field_name} is required")
            object.__setattr__(self, field_name, value.strip())
        if self.compatibility_profile is not None:
            if (type(self.compatibility_profile) is not str or
                    not self.compatibility_profile.strip()):
                raise ValueError("capability gap compatibility_profile is invalid")
            object.__setattr__(self, "compatibility_profile",
                               self.compatibility_profile.strip())
        for field_name in ("evidence_transitions", "evidence_lineages",
                           "missing_asset_types"):
            values = getattr(self, field_name)
            if not isinstance(values, (list, tuple)) or isinstance(values, (str, bytes)):
                raise ValueError(f"capability gap {field_name} must be a sequence")
            normalized = tuple(item.strip() if isinstance(item, str) else item
                               for item in values)
            if not normalized or any(type(item) is not str or not item for item in normalized):
                raise ValueError(f"capability gap {field_name} must be non-empty strings")
            if len(set(normalized)) != len(normalized):
                raise ValueError(f"capability gap {field_name} contains duplicates")
            object.__setattr__(self, field_name, normalized)
        if not isinstance(self.current_action_coverage, Mapping):
            raise ValueError("capability gap current_action_coverage must be an object")
        object.__setattr__(self, "current_action_coverage",
                           dict(self.current_action_coverage))
        if type(self.confidence) not in (int, float) or not 0.0 <= self.confidence <= 1.0:
            raise ValueError("capability gap confidence must be between 0 and 1")

    @property
    def receipt_digest(self) -> str:
        """Content digest used when the gap becomes evolution evidence.

        The digest is deliberately derived only from the typed diagnostic;
        it does not include a proposal, mutation plan, or canonical state.
        """
        return "sha256:" + hashlib.sha256(
            stable_dumps(self.to_dict()).encode()).hexdigest()

    @property
    def receipt_id(self) -> str:
        return "capability_gap_" + self.receipt_digest.split(":", 1)[1][:24]

    @classmethod
    def from_dict(cls, payload: object) -> "CapabilityGapReceipt":
        if not isinstance(payload, Mapping):
            raise ValueError("capability gap receipt must be an object")
        required = {
            "gap_id", "mechanism_family", "compatibility_profile",
            "evidence_transitions", "evidence_lineages", "missing_asset_types",
            "reason", "current_action_coverage", "confidence",
        }
        if not required <= set(payload):
            raise ValueError("capability gap receipt is missing fields")
        receipt = cls(
            gap_id=payload["gap_id"],
            mechanism_family=payload["mechanism_family"],
            compatibility_profile=payload["compatibility_profile"],
            evidence_transitions=tuple(payload["evidence_transitions"]),
            evidence_lineages=tuple(payload["evidence_lineages"]),
            missing_asset_types=tuple(payload["missing_asset_types"]),
            reason=payload["reason"],
            current_action_coverage=dict(payload["current_action_coverage"]),
            confidence=payload["confidence"],
        )
        supplied_digest = payload.get("receipt_digest")
        if supplied_digest is not None and supplied_digest != receipt.receipt_digest:
            raise ValueError("capability gap receipt digest mismatch")
        supplied_id = payload.get("receipt_id")
        if supplied_id is not None and supplied_id != receipt.receipt_id:
            raise ValueError("capability gap receipt ID mismatch")
        return receipt

    def to_dict(self) -> dict:
        return {
            "gap_id": self.gap_id,
            "mechanism_family": self.mechanism_family,
            "compatibility_profile": self.compatibility_profile,
            "evidence_transitions": list(self.evidence_transitions),
            "evidence_lineages": list(self.evidence_lineages),
            "missing_asset_types": list(self.missing_asset_types),
            "reason": self.reason,
            "current_action_coverage": self.current_action_coverage,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class AssetValidationReceipt:
    asset_id: str | None
    status: str
    schema_valid: bool
    static_valid: bool
    independent_verifier: bool
    oracle_verdict: str | None
    regression_verdict: str | None
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "asset_id": self.asset_id, "status": self.status,
            "schema_valid": self.schema_valid, "static_valid": self.static_valid,
            "independent_verifier": self.independent_verifier,
            "oracle_verdict": self.oracle_verdict,
            "regression_verdict": self.regression_verdict,
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class AssetPromotionReceipt:
    asset_id: str
    target_scope: str
    eligible: bool
    checks: dict
    missing: tuple[str, ...]
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "asset_id": self.asset_id, "target_scope": self.target_scope,
            "eligible": self.eligible, "checks": self.checks,
            "missing": list(self.missing), "evidence": self.evidence,
        }


@dataclass(frozen=True)
class AssetAuthorityReceipt:
    """Content-bound asset authority receipt for strict lifecycle promotion."""

    asset_id: str
    target_scope: str
    authority_version: str
    asset_content_digest: str
    eligible: bool
    checks: dict
    missing: tuple[str, ...]
    evidence: dict = field(default_factory=dict)
    authority_receipt_id: str = ""
    receipt_digest: str = ""

    def to_dict(self) -> dict:
        return {
            "asset_id": self.asset_id,
            "target_scope": self.target_scope,
            "authority_version": self.authority_version,
            "asset_content_digest": self.asset_content_digest,
            "eligible": self.eligible,
            "checks": self.checks,
            "missing": list(self.missing),
            "evidence": self.evidence,
            "authority_receipt_id": self.authority_receipt_id,
            "receipt_digest": self.receipt_digest,
        }


@dataclass(frozen=True)
class RuntimeBindingReceipt:
    """Gold-leakage-safe binding receipt for a live repair context.

    This receipt records a selection from RTL structure and observed failure
    evidence. It intentionally carries no repaired RTL, manifest fix, or
    held-out answer and never mutates the asset registry.
    """

    asset_id: str | None
    knowledge_id: str
    target_design: str
    candidate_entities: tuple[str, ...]
    selected_binding: dict
    structural_evidence: tuple[str, ...]
    failure_evidence: tuple[str, ...]
    ambiguity_count: int
    eligible: bool
    reason: str
    binding_digest: str

    def to_dict(self) -> dict:
        return {
            "asset_id": self.asset_id,
            "knowledge_id": self.knowledge_id,
            "target_design": self.target_design,
            "candidate_entities": list(self.candidate_entities),
            "selected_binding": dict(self.selected_binding),
            "structural_evidence": list(self.structural_evidence),
            "failure_evidence": list(self.failure_evidence),
            "ambiguity_count": self.ambiguity_count,
            "eligible": self.eligible,
            "reason": self.reason,
            "binding_digest": self.binding_digest,
        }

    @property
    def binding_receipt_id(self) -> str:
        return "binding_" + self.binding_digest.split(":", 1)[1][:24]


__all__ = ["AssetAuthorityReceipt", "AssetReceipt", "CapabilityGapReceipt",
           "AssetValidationReceipt", "AssetPromotionReceipt", "RuntimeBindingReceipt"]
