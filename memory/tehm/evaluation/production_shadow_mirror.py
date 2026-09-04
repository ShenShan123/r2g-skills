"""Fail-closed P17 production shadow-mirror preparation.

Revision3 keeps a production mirror strictly after the P15 readiness
conjunction.  This module is intentionally only a *comparison receipt*: it
does not import a runtime, promote a rule, write SQLite, or execute a
candidate.  An ineligible readiness receipt produces a blocked receipt and
cannot be accompanied by shadow observations.  A future runtime adapter may
feed immutable base/shadow decisions into :func:`prepare_shadow_mirror`, but
the authority and docs boundaries remain part of the receipt contract.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tehm.ids import stable_dumps

from .production_readiness import ProductionReadinessError, ProductionReadinessReceipt


PRODUCTION_SHADOW_MIRROR_VERSION = "r3-production-shadow-mirror-v1"
MIRROR_STATUSES = frozenset({"BLOCKED_READINESS", "READY_FOR_SHADOW_COMPARISON"})


class ProductionShadowMirrorError(ValueError):
    """A shadow-mirror request or receipt is malformed."""


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ProductionShadowMirrorError(f"shadow mirror {name} is required")
    return value.strip()


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _digest_text(value: object, name: str) -> str:
    text = _text(value, name)
    if not text.startswith("sha256:") or len(text) != len("sha256:") + 64:
        raise ProductionShadowMirrorError(
            f"shadow mirror {name} must be a sha256 digest")
    try:
        int(text[7:], 16)
    except ValueError as exc:
        raise ProductionShadowMirrorError(
            f"shadow mirror {name} must be a sha256 digest") from exc
    return text


def _strings(value: object, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ProductionShadowMirrorError(f"shadow mirror {name} must be a sequence")
    result = tuple(_text(item, name) for item in value)
    if len(set(result)) != len(result):
        raise ProductionShadowMirrorError(f"shadow mirror {name} contains duplicates")
    return result


def _decision(value: object, name: str) -> dict:
    if not isinstance(value, Mapping):
        raise ProductionShadowMirrorError(f"shadow mirror {name} must be an object")
    try:
        normalized = json.loads(stable_dumps(dict(value)))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ProductionShadowMirrorError(
            f"shadow mirror {name} is not JSON-serializable") from exc
    if not isinstance(normalized, dict):  # pragma: no cover - serializer guarantee
        raise ProductionShadowMirrorError(f"shadow mirror {name} must be an object")
    return normalized


def _readiness(value: ProductionReadinessReceipt | Mapping) -> ProductionReadinessReceipt:
    if isinstance(value, ProductionReadinessReceipt):
        payload = value.to_dict()
    elif isinstance(value, Mapping):
        payload = value.get("readiness", value.get("receipt", value))
        # Accept the JSON wrapper emitted by the readiness CLI, but bind the
        # wrapper's outer digest and firewall fields when they are present.
        outer_digest = value.get("receipt_digest")
        if outer_digest is not None:
            _digest_text(outer_digest, "readiness receipt_digest")
        for key, expected in (("production_integration", "not_attempted"),
                              ("canonical_memory_mutation", "none"),
                              ("memory_docs_submitted", False)):
            if key in value and value[key] != expected:
                raise ProductionShadowMirrorError(
                    f"readiness wrapper crosses {key} boundary")
    else:
        raise ProductionShadowMirrorError(
            "shadow mirror readiness must be a production-readiness receipt")
    try:
        checked = ProductionReadinessReceipt.from_dict(payload)
    except (ProductionReadinessError, TypeError, ValueError, KeyError) as exc:
        raise ProductionShadowMirrorError(
            f"shadow mirror readiness cannot replay: {exc}") from exc
    if isinstance(value, Mapping) and value.get("receipt_digest") is not None and \
            value["receipt_digest"] != checked.receipt_digest:
        raise ProductionShadowMirrorError("readiness wrapper receipt digest mismatch")
    return checked


def _comparisons(value: object, allowlist: tuple[str, ...]) -> tuple[dict, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ProductionShadowMirrorError("shadow mirror comparisons must be a sequence")
    rows: list[dict] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, Mapping):
            raise ProductionShadowMirrorError("shadow mirror comparison must be an object")
        case_id = _text(raw.get("case_id"), "comparison.case_id")
        if case_id in seen:
            raise ProductionShadowMirrorError(
                "shadow mirror comparisons contain duplicate case IDs")
        if allowlist and case_id not in allowlist:
            raise ProductionShadowMirrorError(
                f"shadow mirror comparison is outside the allowlist: {case_id}")
        seen.add(case_id)
        base = _decision(raw.get("base_decision"), "comparison.base_decision")
        shadow = _decision(raw.get("shadow_decision"), "comparison.shadow_decision")
        rows.append({
            "case_id": case_id,
            "base_decision": base,
            "shadow_decision": shadow,
            "base_decision_digest": _digest(base),
            "shadow_decision_digest": _digest(shadow),
            "changed": base != shadow,
        })
    return tuple(rows)


@dataclass(frozen=True)
class ProductionShadowMirrorReceipt:
    """Content-addressed, evaluation-only P17 mirror preparation result."""

    readiness_campaign_id: str
    readiness_receipt_id: str
    readiness_receipt_digest: str
    readiness_eligible: bool
    mirror_status: str
    allowlist: tuple[str, ...]
    comparisons: tuple[dict, ...]
    reasons: tuple[str, ...]
    evaluation_only: bool = True
    canonical_memory_mutation: str = "none"
    production_runtime_imported: bool = False
    promotion_attempted: bool = False
    production_integration: str = "not_attempted"
    memory_docs_submitted: bool = False
    version: str = PRODUCTION_SHADOW_MIRROR_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "readiness_campaign_id",
                           _text(self.readiness_campaign_id, "readiness_campaign_id"))
        object.__setattr__(self, "readiness_receipt_id",
                           _text(self.readiness_receipt_id, "readiness_receipt_id"))
        object.__setattr__(self, "readiness_receipt_digest",
                           _digest_text(self.readiness_receipt_digest,
                                        "readiness_receipt_digest"))
        if type(self.readiness_eligible) is not bool:
            raise ProductionShadowMirrorError("shadow mirror readiness_eligible must be boolean")
        if self.version != PRODUCTION_SHADOW_MIRROR_VERSION:
            raise ProductionShadowMirrorError("shadow mirror receipt version mismatch")
        if self.mirror_status not in MIRROR_STATUSES:
            raise ProductionShadowMirrorError("shadow mirror status is invalid")
        allowlist = _strings(self.allowlist, "allowlist")
        if isinstance(self.comparisons, (str, bytes)) or not isinstance(
                self.comparisons, (list, tuple)):
            raise ProductionShadowMirrorError("shadow mirror comparisons must be a sequence")
        comparisons: list[dict] = []
        for raw in self.comparisons:
            if not isinstance(raw, Mapping):
                raise ProductionShadowMirrorError("shadow mirror comparison must be an object")
            required = {
                "case_id", "base_decision", "shadow_decision",
                "base_decision_digest", "shadow_decision_digest", "changed",
            }
            if not required <= set(raw):
                raise ProductionShadowMirrorError(
                    "shadow mirror comparison is missing fields")
            case_id = _text(raw.get("case_id"), "comparison.case_id")
            base = _decision(raw.get("base_decision"), "comparison.base_decision")
            shadow = _decision(raw.get("shadow_decision"), "comparison.shadow_decision")
            if _digest_text(raw.get("base_decision_digest"),
                            "comparison.base_decision_digest") != _digest(base):
                raise ProductionShadowMirrorError(
                    "shadow mirror base decision digest mismatch")
            if _digest_text(raw.get("shadow_decision_digest"),
                            "comparison.shadow_decision_digest") != _digest(shadow):
                raise ProductionShadowMirrorError(
                    "shadow mirror shadow decision digest mismatch")
            if type(raw.get("changed")) is not bool or \
                    raw["changed"] is not (base != shadow):
                raise ProductionShadowMirrorError(
                    "shadow mirror comparison changed flag mismatch")
            comparisons.append({
                "case_id": case_id, "base_decision": base,
                "shadow_decision": shadow,
                "base_decision_digest": raw["base_decision_digest"],
                "shadow_decision_digest": raw["shadow_decision_digest"],
                "changed": raw["changed"],
            })
        comparisons = tuple(comparisons)
        case_ids = tuple(item["case_id"] for item in comparisons)
        if len(set(case_ids)) != len(case_ids):
            raise ProductionShadowMirrorError("shadow mirror comparisons contain duplicates")
        if allowlist and any(case_id not in allowlist for case_id in case_ids):
            raise ProductionShadowMirrorError("shadow mirror comparisons exceed allowlist")
        if self.mirror_status == "BLOCKED_READINESS":
            if self.readiness_eligible or comparisons:
                raise ProductionShadowMirrorError(
                    "blocked shadow mirror cannot carry eligible readiness or comparisons")
        elif not self.readiness_eligible:
            raise ProductionShadowMirrorError(
                "ready shadow mirror requires eligible readiness")
        if type(self.evaluation_only) is not bool or not self.evaluation_only:
            raise ProductionShadowMirrorError("shadow mirror must be evaluation-only")
        if self.canonical_memory_mutation != "none" or \
                self.production_runtime_imported is not False or \
                self.promotion_attempted is not False or \
                self.production_integration != "not_attempted" or \
                self.memory_docs_submitted is not False:
            raise ProductionShadowMirrorError(
                "shadow mirror crosses canonical/production/docs boundary")
        if not isinstance(self.reasons, (list, tuple)) or any(
                type(item) is not str or not item.strip() for item in self.reasons):
            raise ProductionShadowMirrorError("shadow mirror reasons are invalid")
        object.__setattr__(self, "allowlist", allowlist)
        object.__setattr__(self, "comparisons", comparisons)
        object.__setattr__(self, "reasons", tuple(self.reasons))

    @property
    def comparison_count(self) -> int:
        return len(self.comparisons)

    @property
    def changed_count(self) -> int:
        return sum(item.get("changed") is True for item in self.comparisons)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "readiness_campaign_id": self.readiness_campaign_id,
            "readiness_receipt_id": self.readiness_receipt_id,
            "readiness_receipt_digest": self.readiness_receipt_digest,
            "readiness_eligible": self.readiness_eligible,
            "mirror_status": self.mirror_status,
            "allowlist": list(self.allowlist),
            "comparisons": [dict(item) for item in self.comparisons],
            "comparison_count": self.comparison_count,
            "changed_count": self.changed_count,
            "reasons": list(self.reasons),
            "evaluation_only": self.evaluation_only,
            "canonical_memory_mutation": self.canonical_memory_mutation,
            "production_runtime_imported": self.production_runtime_imported,
            "promotion_attempted": self.promotion_attempted,
            "production_integration": self.production_integration,
            "memory_docs_submitted": self.memory_docs_submitted,
        }

    @property
    def receipt_digest(self) -> str:
        return _digest(self.to_dict())

    @property
    def receipt_id(self) -> str:
        return "production_shadow_mirror_" + self.receipt_digest.split(":", 1)[1][:24]

    @classmethod
    def from_dict(cls, payload: object) -> "ProductionShadowMirrorReceipt":
        if not isinstance(payload, Mapping):
            raise ProductionShadowMirrorError("shadow mirror receipt must be an object")
        required = {
            "version", "readiness_campaign_id", "readiness_receipt_id",
            "readiness_receipt_digest", "readiness_eligible", "mirror_status",
            "allowlist", "comparisons", "reasons", "evaluation_only",
            "canonical_memory_mutation", "production_runtime_imported",
            "promotion_attempted", "production_integration", "memory_docs_submitted",
        }
        if not required <= set(payload):
            raise ProductionShadowMirrorError("shadow mirror receipt is missing fields")
        if payload.get("version") != PRODUCTION_SHADOW_MIRROR_VERSION:
            raise ProductionShadowMirrorError("shadow mirror receipt version mismatch")
        receipt = cls(
            readiness_campaign_id=payload["readiness_campaign_id"],
            readiness_receipt_id=payload["readiness_receipt_id"],
            readiness_receipt_digest=payload["readiness_receipt_digest"],
            readiness_eligible=payload["readiness_eligible"],
            mirror_status=payload["mirror_status"], allowlist=tuple(payload["allowlist"]),
            comparisons=payload["comparisons"],
            reasons=payload["reasons"],
            evaluation_only=payload["evaluation_only"],
            canonical_memory_mutation=payload["canonical_memory_mutation"],
            production_runtime_imported=payload["production_runtime_imported"],
            promotion_attempted=payload["promotion_attempted"],
            production_integration=payload["production_integration"],
            memory_docs_submitted=payload["memory_docs_submitted"],
            version=payload["version"],
        )
        if payload.get("comparison_count") is not None and \
                payload["comparison_count"] != receipt.comparison_count:
            raise ProductionShadowMirrorError("shadow mirror comparison_count mismatch")
        if payload.get("changed_count") is not None and \
                payload["changed_count"] != receipt.changed_count:
            raise ProductionShadowMirrorError("shadow mirror changed_count mismatch")
        if payload.get("receipt_digest") is not None and \
                payload["receipt_digest"] != receipt.receipt_digest:
            raise ProductionShadowMirrorError("shadow mirror receipt digest mismatch")
        if payload.get("receipt_id") is not None and payload["receipt_id"] != receipt.receipt_id:
            raise ProductionShadowMirrorError("shadow mirror receipt ID mismatch")
        return receipt


def prepare_shadow_mirror(
        readiness: ProductionReadinessReceipt | Mapping, *,
        comparisons: Sequence[Mapping] | None = None,
        allowlist: Sequence[str] | None = None) -> ProductionShadowMirrorReceipt:
    """Prepare a P17 comparison receipt without touching any runtime.

    An ineligible readiness result is a normal, replayable block.  Passing
    observations alongside that result is rejected so a caller cannot make a
    blocked campaign look like a partially executed production mirror.
    """
    checked = _readiness(readiness)
    allow = _strings(allowlist, "allowlist")
    if not checked.eligible:
        if comparisons:
            raise ProductionShadowMirrorError(
                "cannot record shadow comparisons before readiness is eligible")
        return ProductionShadowMirrorReceipt(
            readiness_campaign_id=checked.campaign_id,
            readiness_receipt_id=checked.receipt_id,
            readiness_receipt_digest=checked.receipt_digest,
            readiness_eligible=False, mirror_status="BLOCKED_READINESS",
            allowlist=allow, comparisons=(),
            reasons=("readiness_ineligible", *checked.reasons),
        )
    rows = _comparisons(comparisons, allow)
    return ProductionShadowMirrorReceipt(
        readiness_campaign_id=checked.campaign_id,
        readiness_receipt_id=checked.receipt_id,
        readiness_receipt_digest=checked.receipt_digest,
        readiness_eligible=True, mirror_status="READY_FOR_SHADOW_COMPARISON",
        allowlist=allow, comparisons=rows, reasons=(),
    )


def replay_shadow_mirror(payload: object) -> ProductionShadowMirrorReceipt:
    """Replay a serialized P17 receipt and recheck its content digest."""
    return ProductionShadowMirrorReceipt.from_dict(payload)


__all__ = [
    "PRODUCTION_SHADOW_MIRROR_VERSION", "MIRROR_STATUSES",
    "ProductionShadowMirrorError", "ProductionShadowMirrorReceipt",
    "prepare_shadow_mirror", "replay_shadow_mirror",
]
