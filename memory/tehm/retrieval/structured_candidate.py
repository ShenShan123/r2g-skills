"""Evaluation-only StructuredRepairCandidate construction (P11).

This module is intentionally downstream of routing, asset selection and the
runtime binding receipt. It does not alter ``MemoryCandidate.source``, write a
database row, execute an action, or grant lifecycle/production authority.
"""
from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from contracts import MemoryQuery, MemoryRoutingDecision, RepairContext
from tehm.assets.receipts import RuntimeBindingReceipt
from tehm.ids import is_hole, stable_dumps

from .asset_selector import AssetSelection
from .structured_candidate_receipts import StructuredCandidateReceipt


CANDIDATE_VERSION = "structured-repair-candidate-v0.1"
_GOLD_KEYS = frozenset({"fix", "gold_patch", "repaired_rtl", "heldout_answer"})


class StructuredCandidateError(ValueError):
    """A candidate cannot cross the typed evaluation boundary."""


def _text(value: object, field_name: str) -> str:
    if type(value) is not str or not value.strip():
        raise StructuredCandidateError(
            f"structured candidate {field_name} is required")
    return value.strip()


def _mapping(value: object, field_name: str) -> dict:
    if not isinstance(value, Mapping):
        raise StructuredCandidateError(
            f"structured candidate {field_name} must be an object")
    try:
        decoded = __import__("json").loads(stable_dumps(dict(value)))
    except (TypeError, ValueError, __import__("json").JSONDecodeError) as exc:
        raise StructuredCandidateError(
            f"structured candidate {field_name} is not JSON-serializable") from exc
    if not isinstance(decoded, dict):  # pragma: no cover
        raise StructuredCandidateError(
            f"structured candidate {field_name} must be an object")
    return decoded


def _contains_gold(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(key in _GOLD_KEYS or _contains_gold(item)
                   for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_gold(item) for item in value)
    return False


def _contains_hole(value: object) -> bool:
    if isinstance(value, str):
        return is_hole(value)
    if isinstance(value, Mapping):
        return any(_contains_hole(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_hole(item) for item in value)
    return False


def _binding_value(binding: object, field: str, default=None):
    if hasattr(binding, field):
        return getattr(binding, field)
    if isinstance(binding, Mapping):
        return binding.get(field, default)
    return default


def _binding_dict(binding: object) -> dict:
    value = binding.to_dict() if hasattr(binding, "to_dict") else binding
    return _mapping(value, "runtime_binding")


def _binding_digest(binding: object) -> str:
    value = _binding_value(binding, "binding_digest")
    if type(value) is not str or not value.startswith("sha256:"):
        raise StructuredCandidateError("runtime binding digest is missing or invalid")
    return value


@dataclass(frozen=True)
class StructuredRepairCandidate:
    candidate_id: str
    resolved_state_id: str
    knowledge_object_id: str
    causal_path_ids: tuple[str, ...]
    asset_id: str
    action_family: str
    concrete_action: dict
    applicability_receipt_id: str
    binding_receipt_id: str
    obligations: tuple[str, ...]
    evidence_level: str
    authority: dict
    risk: dict
    provenance: dict
    evaluation_only: bool = True
    candidate_version: str = CANDIDATE_VERSION

    def __post_init__(self) -> None:
        for field_name in (
                "candidate_id", "resolved_state_id", "knowledge_object_id",
                "asset_id", "action_family", "applicability_receipt_id",
                "binding_receipt_id", "evidence_level", "candidate_version"):
            _text(getattr(self, field_name), field_name)
        if not isinstance(self.causal_path_ids, tuple) or not self.causal_path_ids:
            raise StructuredCandidateError("structured candidate causal paths are required")
        if any(type(item) is not str or not item for item in self.causal_path_ids):
            raise StructuredCandidateError("structured candidate causal paths are invalid")
        if len(set(self.causal_path_ids)) != len(self.causal_path_ids):
            raise StructuredCandidateError("structured candidate causal paths are duplicated")
        if not isinstance(self.obligations, tuple) or not self.obligations:
            raise StructuredCandidateError("structured candidate obligations are required")
        if any(type(item) is not str or not item for item in self.obligations):
            raise StructuredCandidateError("structured candidate obligations are invalid")
        for field_name in ("concrete_action", "authority", "risk", "provenance"):
            _mapping(getattr(self, field_name), field_name)
        if _contains_gold(self.concrete_action) or _contains_gold(self.provenance):
            raise StructuredCandidateError("structured candidate contains gold-answer fields")
        if _contains_hole(self.concrete_action):
            raise StructuredCandidateError("structured candidate action has unresolved holes")
        if self.evaluation_only is not True:
            raise StructuredCandidateError("structured candidate is evaluation-only")
        if self.provenance.get("evaluation_only") is not True:
            raise StructuredCandidateError("structured candidate provenance is not evaluation-only")

    def content(self) -> dict:
        return {
            "version": self.candidate_version,
            "resolved_state_id": self.resolved_state_id,
            "knowledge_object_id": self.knowledge_object_id,
            "causal_path_ids": list(self.causal_path_ids),
            "asset_id": self.asset_id,
            "action_family": self.action_family,
            "concrete_action": self.concrete_action,
            "applicability_receipt_id": self.applicability_receipt_id,
            "binding_receipt_id": self.binding_receipt_id,
            "obligations": list(self.obligations),
            "evidence_level": self.evidence_level,
            "authority": self.authority,
            "risk": self.risk,
            "provenance": self.provenance,
            "evaluation_only": self.evaluation_only,
        }

    @property
    def candidate_digest(self) -> str:
        return "sha256:" + hashlib.sha256(
            stable_dumps(self.content()).encode()).hexdigest()

    @property
    def receipt_id(self) -> str:
        return "structured_candidate_" + self.candidate_digest.split(":", 1)[1][:24]

    def to_dict(self) -> dict[str, Any]:
        return {**self.content(), "candidate_id": self.candidate_id,
                "candidate_digest": self.candidate_digest,
                "receipt_id": self.receipt_id}

    def receipt(self) -> StructuredCandidateReceipt:
        return StructuredCandidateReceipt(
            candidate_id=self.candidate_id, candidate_digest=self.candidate_digest,
            resolved_state_id=self.resolved_state_id,
            knowledge_object_id=self.knowledge_object_id, asset_id=self.asset_id,
            applicability_receipt_id=self.applicability_receipt_id,
            binding_receipt_id=self.binding_receipt_id,
            evaluation_only=self.evaluation_only)

    @classmethod
    def from_dict(cls, payload: object) -> "StructuredRepairCandidate":
        if not isinstance(payload, Mapping):
            raise StructuredCandidateError("structured candidate must be an object")
        required = {
            "candidate_id", "resolved_state_id", "knowledge_object_id",
            "causal_path_ids", "asset_id", "action_family", "concrete_action",
            "applicability_receipt_id", "binding_receipt_id", "obligations",
            "evidence_level", "authority", "risk", "provenance",
            "evaluation_only", "candidate_version",
        }
        if not required <= set(payload):
            raise StructuredCandidateError("structured candidate is missing fields")
        candidate = cls(
            candidate_id=payload["candidate_id"], resolved_state_id=payload["resolved_state_id"],
            knowledge_object_id=payload["knowledge_object_id"],
            causal_path_ids=tuple(payload["causal_path_ids"]), asset_id=payload["asset_id"],
            action_family=payload["action_family"], concrete_action=dict(payload["concrete_action"]),
            applicability_receipt_id=payload["applicability_receipt_id"],
            binding_receipt_id=payload["binding_receipt_id"],
            obligations=tuple(payload["obligations"]), evidence_level=payload["evidence_level"],
            authority=dict(payload["authority"]), risk=dict(payload["risk"]),
            provenance=dict(payload["provenance"]), evaluation_only=payload["evaluation_only"],
            candidate_version=payload["candidate_version"])
        supplied = payload.get("candidate_digest")
        if supplied is not None and supplied != candidate.candidate_digest:
            raise StructuredCandidateError("structured candidate digest mismatch")
        if payload.get("receipt_id") is not None and payload["receipt_id"] != candidate.receipt_id:
            raise StructuredCandidateError("structured candidate receipt ID mismatch")
        return candidate


def _asset_action(asset: Mapping, binding: object) -> tuple[str, dict]:
    if _contains_gold(asset):
        raise StructuredCandidateError("asset contains gold-answer fields")
    definition = asset.get("definition")
    action = definition.get("action") if isinstance(definition, Mapping) else None
    if not isinstance(action, Mapping) or not isinstance(action.get("payload"), Mapping):
        raise StructuredCandidateError("selected asset has no structured action")
    family = _text(action.get("transformation_family") or action.get("family"),
                   "action_family")
    concrete = copy.deepcopy(dict(action))
    payload = concrete.get("payload")
    selected = _binding_value(binding, "selected_binding", {})
    if not isinstance(selected, Mapping):
        raise StructuredCandidateError("runtime binding selected_binding is malformed")
    payload.update(dict(selected))
    concrete["payload"] = payload
    return family, concrete


def _selected_knowledge(selection: AssetSelection, binding: object) -> str:
    ids = tuple(selection.receipt.knowledge_object_ids)
    if not ids:
        raise StructuredCandidateError("asset selection has no knowledge object")
    bound = _text(_binding_value(binding, "knowledge_id"), "binding knowledge_id")
    if bound not in ids:
        raise StructuredCandidateError("runtime binding knowledge does not match selection")
    return bound


def _paths(routing: MemoryRoutingDecision, selection: AssetSelection) -> tuple[str, ...]:
    route_paths = set(routing.selected_path_ids)
    support = selection.receipt.causal_support.get("causal_path_ids", ())
    support_paths = {item for item in support if isinstance(item, str) and item}
    if route_paths and support_paths:
        paths = route_paths & support_paths
    else:
        paths = route_paths or support_paths
    if not paths:
        raise StructuredCandidateError("structured candidate requires causal path agreement")
    if route_paths and support_paths and not paths:
        raise StructuredCandidateError("routing and selection causal paths differ")
    return tuple(sorted(paths))


def _obligations(asset: Mapping) -> tuple[str, ...]:
    contract = asset.get("verifier_contract") or {}
    values = contract.get("obligations") if isinstance(contract, Mapping) else None
    if not isinstance(values, (list, tuple)):
        values = ()
    result = tuple(sorted({item.strip() for item in values
                           if isinstance(item, str) and item.strip()}))
    if not result:
        raise StructuredCandidateError("structured candidate obligations are required")
    return result


def build_structured_candidate(
    query: MemoryQuery | RepairContext | None,
    routing: MemoryRoutingDecision,
    asset_selection: AssetSelection,
    runtime_binding: RuntimeBindingReceipt | Mapping,
) -> StructuredRepairCandidate:
    """Build one candidate after rechecking all P5/P7/P10 boundaries."""
    if query is not None and not isinstance(query, (MemoryQuery, RepairContext)):
        raise TypeError("structured candidate query must be MemoryQuery or RepairContext")
    if not isinstance(routing, MemoryRoutingDecision):
        raise TypeError("structured candidate routing must be MemoryRoutingDecision")
    if not isinstance(asset_selection, AssetSelection):
        raise TypeError("structured candidate asset_selection must be AssetSelection")
    if asset_selection.receipt.decision != "SELECT" or len(asset_selection.assets) != 1:
        raise StructuredCandidateError("structured candidate requires one selected asset")
    binding = _binding_dict(runtime_binding)
    if _contains_gold(binding):
        raise StructuredCandidateError("runtime binding contains gold-answer fields")
    if _binding_value(runtime_binding, "eligible") is not True:
        raise StructuredCandidateError("runtime binding is not eligible")
    if routing.resolved_state_id != asset_selection.receipt.resolved_state_id:
        raise StructuredCandidateError("routing and selection state IDs differ")
    asset = asset_selection.assets[0]
    asset_id = _text(asset_selection.receipt.selected_asset_ids[0], "asset_id")
    bound_asset = _binding_value(runtime_binding, "asset_id")
    if bound_asset != asset_id:
        raise StructuredCandidateError("runtime binding asset does not match selection")
    knowledge_id = _selected_knowledge(asset_selection, runtime_binding)
    paths = _paths(routing, asset_selection)
    applicability = asset_selection.receipt.applicability
    for key in ("negative_matches", "negative_vetoes", "negative_applicability"):
        if applicability.get(key):
            raise StructuredCandidateError("structured candidate negative applicability")
    family, action = _asset_action(asset, runtime_binding)
    obligations = _obligations(asset)
    evidence_level = (routing.causal_support.get("strongest_evidence_level") or
                      routing.causal_support.get("strongest_evidence") or
                      (asset.get("provenance") or {}).get("evidence_level") or
                      "UNSPECIFIED")
    evidence_level = _text(evidence_level, "evidence_level")
    authority = asset_selection.receipt.binding
    risk = routing.risk
    provenance = {
        "source": "tehm_structured_candidate",
        "evaluation_only": True,
        "candidate_version": CANDIDATE_VERSION,
        "routing_receipt_id": routing.routing_receipt_id,
        "asset_selection_receipt_id": asset_selection.receipt.selection_receipt_id,
        "binding_digest": _binding_digest(runtime_binding),
    }
    content = {
        "version": CANDIDATE_VERSION, "resolved_state_id": routing.resolved_state_id,
        "knowledge_object_id": knowledge_id, "causal_path_ids": list(paths),
        "asset_id": asset_id, "action_family": family, "concrete_action": action,
        "applicability_receipt_id": asset_selection.receipt.selection_receipt_id,
        "binding_receipt_id": (getattr(runtime_binding, "binding_receipt_id", None) or
                                "binding_" + provenance["binding_digest"].split(":", 1)[1][:24]),
        "obligations": list(obligations), "evidence_level": evidence_level,
        "authority": authority, "risk": risk, "provenance": provenance,
        "evaluation_only": True,
    }
    digest = "sha256:" + hashlib.sha256(stable_dumps(content).encode()).hexdigest()
    candidate_id = "structured_candidate_" + digest.split(":", 1)[1][:24]
    return StructuredRepairCandidate(
        candidate_id=candidate_id, resolved_state_id=routing.resolved_state_id,
        knowledge_object_id=knowledge_id, causal_path_ids=paths, asset_id=asset_id,
        action_family=family, concrete_action=action,
        applicability_receipt_id=content["applicability_receipt_id"],
        binding_receipt_id=content["binding_receipt_id"], obligations=obligations,
        evidence_level=evidence_level, authority=authority, risk=risk,
        provenance=provenance)


__all__ = [
    "CANDIDATE_VERSION", "StructuredCandidateError", "StructuredRepairCandidate",
    "build_structured_candidate",
]
