"""Content-addressed candidate lineage for P14 attribution.

The C5 witness must identify the exact structured candidate that was selected
and executed, together with the routing, asset-selection, runtime-binding and
execution receipts that produced it.  This module is evaluation-only: it does
not execute a candidate, mutate canonical memory, or grant authority.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

from tehm.ids import stable_dumps


CANDIDATE_LINEAGE_VERSION = "candidate-lineage-v1"


class CandidateLineageError(ValueError):
    """A candidate lineage witness is malformed or internally inconsistent."""


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _text(value: object, field_name: str) -> str:
    if type(value) is not str or not value.strip():
        raise CandidateLineageError(
            f"candidate lineage {field_name} is required")
    return value.strip()


def _digest_text(value: object, field_name: str) -> str:
    value = _text(value, field_name)
    if not value.startswith("sha256:"):
        raise CandidateLineageError(
            f"candidate lineage {field_name} must be a sha256 digest")
    return value


def _mapping(value: object, field_name: str) -> dict:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if not isinstance(value, Mapping):
        raise CandidateLineageError(
            f"candidate lineage {field_name} must be an object")
    try:
        decoded = json.loads(stable_dumps(dict(value)))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CandidateLineageError(
            f"candidate lineage {field_name} is not JSON-serializable") from exc
    if not isinstance(decoded, dict):  # pragma: no cover
        raise CandidateLineageError(
            f"candidate lineage {field_name} must be an object")
    return decoded


def _with_property(payload: dict, source: object, field_name: str) -> dict:
    """Copy a derived receipt property that is intentionally absent from to_dict()."""
    if field_name not in payload and hasattr(source, field_name):
        payload[field_name] = getattr(source, field_name)
    return payload


def _strings(value: object, field_name: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise CandidateLineageError(
            f"candidate lineage {field_name} must be a sequence")
    values = tuple(value)
    if not allow_empty and not values:
        raise CandidateLineageError(
            f"candidate lineage {field_name} must not be empty")
    if any(type(item) is not str or not item.strip() for item in values):
        raise CandidateLineageError(
            f"candidate lineage {field_name} contains invalid IDs")
    values = tuple(item.strip() for item in values)
    if len(set(values)) != len(values):
        raise CandidateLineageError(
            f"candidate lineage {field_name} contains duplicates")
    return values


def _candidate_receipt_digest(candidate: Mapping) -> str:
    """Rebuild the compact structured-candidate receipt from candidate data."""
    required = (
        "candidate_id", "candidate_digest", "resolved_state_id",
        "knowledge_object_id", "asset_id", "applicability_receipt_id",
        "binding_receipt_id",
    )
    if any(field not in candidate for field in required):
        raise CandidateLineageError(
            "candidate lineage structured candidate fields are incomplete")
    payload = {
        "candidate_id": candidate["candidate_id"],
        "candidate_digest": candidate["candidate_digest"],
        "resolved_state_id": candidate["resolved_state_id"],
        "knowledge_object_id": candidate["knowledge_object_id"],
        "asset_id": candidate["asset_id"],
        "applicability_receipt_id": candidate["applicability_receipt_id"],
        "binding_receipt_id": candidate["binding_receipt_id"],
        "evaluation_only": candidate.get("evaluation_only"),
        "reasons": list(candidate.get("reasons", ())),
    }
    return _digest(payload)


@dataclass(frozen=True)
class CandidateLineageReceipt:
    """Immutable witness connecting one candidate to its upstream receipts."""

    candidate_id: str
    candidate_digest: str
    structured_candidate_receipt_digest: str
    routing_receipt_id: str
    routing_decision_digest: str
    asset_selection_receipt_id: str
    asset_selection_receipt_digest: str
    binding_receipt_id: str
    binding_digest: str
    execution_receipt_digest: str
    knowledge_object_id: str
    asset_id: str
    causal_path_ids: tuple[str, ...]
    eligible: bool
    reasons: tuple[str, ...] = ()
    version: str = CANDIDATE_LINEAGE_VERSION

    def __post_init__(self) -> None:
        for value, name in (
                (self.candidate_id, "candidate_id"),
                (self.knowledge_object_id, "knowledge_object_id"),
                (self.asset_id, "asset_id"),
                (self.routing_receipt_id, "routing_receipt_id"),
                (self.asset_selection_receipt_id, "asset_selection_receipt_id"),
                (self.binding_receipt_id, "binding_receipt_id")):
            _text(value, name)
        for value, name in (
                (self.candidate_digest, "candidate_digest"),
                (self.structured_candidate_receipt_digest,
                 "structured_candidate_receipt_digest"),
                (self.routing_decision_digest, "routing_decision_digest"),
                (self.asset_selection_receipt_digest,
                 "asset_selection_receipt_digest"),
                (self.binding_digest, "binding_digest"),
                (self.execution_receipt_digest, "execution_receipt_digest")):
            _digest_text(value, name)
        _strings(self.causal_path_ids, "causal_path_ids")
        if type(self.eligible) is not bool:
            raise CandidateLineageError("candidate lineage eligible must be boolean")
        if not isinstance(self.reasons, tuple) or any(
                type(item) is not str or not item.strip() for item in self.reasons):
            raise CandidateLineageError("candidate lineage reasons are invalid")
        if self.version != CANDIDATE_LINEAGE_VERSION:
            raise CandidateLineageError("candidate lineage version is unsupported")
        if self.eligible and self.reasons:
            raise CandidateLineageError(
                "eligible candidate lineage cannot contain failure reasons")

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "candidate_id": self.candidate_id,
            "candidate_digest": self.candidate_digest,
            "structured_candidate_receipt_digest": self.structured_candidate_receipt_digest,
            "routing_receipt_id": self.routing_receipt_id,
            "routing_decision_digest": self.routing_decision_digest,
            "asset_selection_receipt_id": self.asset_selection_receipt_id,
            "asset_selection_receipt_digest": self.asset_selection_receipt_digest,
            "binding_receipt_id": self.binding_receipt_id,
            "binding_digest": self.binding_digest,
            "execution_receipt_digest": self.execution_receipt_digest,
            "knowledge_object_id": self.knowledge_object_id,
            "asset_id": self.asset_id,
            "causal_path_ids": list(self.causal_path_ids),
            "eligible": self.eligible,
            "reasons": list(self.reasons),
        }

    @property
    def receipt_digest(self) -> str:
        return _digest(self.to_dict())

    @classmethod
    def from_dict(cls, payload: object) -> "CandidateLineageReceipt":
        if not isinstance(payload, Mapping):
            raise CandidateLineageError("candidate lineage receipt must be an object")
        required = {
            "version", "candidate_id", "candidate_digest",
            "structured_candidate_receipt_digest", "routing_receipt_id",
            "routing_decision_digest", "asset_selection_receipt_id",
            "asset_selection_receipt_digest", "binding_receipt_id",
            "binding_digest", "execution_receipt_digest", "knowledge_object_id",
            "asset_id", "causal_path_ids", "eligible", "reasons",
        }
        if not required <= set(payload):
            raise CandidateLineageError(
                "candidate lineage receipt is missing fields")
        receipt = cls(
            version=payload["version"], candidate_id=payload["candidate_id"],
            candidate_digest=payload["candidate_digest"],
            structured_candidate_receipt_digest=payload[
                "structured_candidate_receipt_digest"],
            routing_receipt_id=payload["routing_receipt_id"],
            routing_decision_digest=payload["routing_decision_digest"],
            asset_selection_receipt_id=payload["asset_selection_receipt_id"],
            asset_selection_receipt_digest=payload[
                "asset_selection_receipt_digest"],
            binding_receipt_id=payload["binding_receipt_id"],
            binding_digest=payload["binding_digest"],
            execution_receipt_digest=payload["execution_receipt_digest"],
            knowledge_object_id=payload["knowledge_object_id"],
            asset_id=payload["asset_id"],
            causal_path_ids=tuple(payload["causal_path_ids"]),
            eligible=payload["eligible"], reasons=tuple(payload["reasons"]),
        )
        supplied = payload.get("receipt_digest")
        if supplied is not None and supplied != receipt.receipt_digest:
            raise CandidateLineageError("candidate lineage receipt digest mismatch")
        return receipt


def build_candidate_lineage(
        *, candidate: object, routing: object, asset_selection: object,
        runtime_binding: object, execution: object,
        ) -> CandidateLineageReceipt:
    """Build and cross-check a candidate lineage from typed upstream objects."""
    candidate_payload = _mapping(candidate, "candidate")
    routing_payload = _mapping(routing, "routing")
    _with_property(routing_payload, routing, "routing_receipt_id")
    _with_property(routing_payload, routing, "decision_digest")
    selection_payload = _mapping(asset_selection, "asset_selection")
    if "receipt" in selection_payload:
        selection_source = asset_selection.receipt if hasattr(
            asset_selection, "receipt") else selection_payload["receipt"]
        selection_receipt = _mapping(selection_source, "asset_selection.receipt")
    else:
        selection_source = asset_selection
        selection_receipt = selection_payload
    _with_property(selection_receipt, selection_source, "selection_receipt_id")
    _with_property(selection_receipt, selection_source, "asset_selection_id")
    _with_property(selection_receipt, selection_source, "receipt_digest")
    binding_payload = _mapping(runtime_binding, "runtime_binding")
    _with_property(binding_payload, runtime_binding, "binding_receipt_id")
    execution_payload = _mapping(execution, "execution")
    _with_property(execution_payload, execution, "execution_digest")

    candidate_id = _text(candidate_payload.get("candidate_id"), "candidate_id")
    candidate_digest = _digest_text(
        candidate_payload.get("candidate_digest"), "candidate_digest")
    state_id = _text(candidate_payload.get("resolved_state_id"), "resolved_state_id")
    knowledge_id = _text(candidate_payload.get("knowledge_object_id"),
                         "knowledge_object_id")
    asset_id = _text(candidate_payload.get("asset_id"), "asset_id")
    paths = _strings(candidate_payload.get("causal_path_ids"), "causal_path_ids")
    if candidate_payload.get("evaluation_only") is not True:
        raise CandidateLineageError("candidate lineage candidate is not evaluation-only")

    route_base = {key: routing_payload.get(key) for key in (
        "decision", "resolved_state_id", "selected_rule_ids", "selected_path_ids",
        "selected_asset_ids", "applicability", "causal_support", "risk",
        "abstain_reasons", "no_memory_budget", "memory_budget",
        "no_skill_reason", "state_shift_receipt_id", "risk_receipt_id")}
    route_digest = _digest_text(
        routing_payload.get("decision_digest") or _digest(route_base),
        "routing_decision_digest")
    route_id_value = routing_payload.get("routing_receipt_id")
    route_id = _text(route_id_value or
                     "routing_" + route_digest.split(":", 1)[1][:24],
                     "routing_receipt_id")
    if routing_payload.get("resolved_state_id") != state_id:
        raise CandidateLineageError("candidate lineage routing state mismatch")
    if (routing_payload.get("decision") not in {"APPLY", "CONSIDER"} or
            type(routing_payload.get("memory_budget")) is not int or
            routing_payload["memory_budget"] < 1):
        raise CandidateLineageError("candidate lineage routing does not authorize memory")
    if asset_id not in tuple(routing_payload.get("selected_asset_ids") or ()):
        raise CandidateLineageError("candidate lineage asset is absent from routing")
    route_paths = set(routing_payload.get("selected_path_ids") or ())
    if not set(paths) <= route_paths:
        raise CandidateLineageError("candidate lineage causal paths differ from routing")

    selection_digest = _digest_text(
        selection_receipt.get("receipt_digest") or _digest({
            key: selection_receipt.get(key) for key in (
                "decision", "resolved_state_id", "routing_receipt_id",
                "knowledge_object_ids", "selected_asset_ids", "applicability",
                "causal_support", "binding", "abstain_reasons",
                "candidate_budget", "selector_version", "shadow_only")}),
        "asset_selection_receipt_digest")
    selection_id = _text(
        selection_receipt.get("selection_receipt_id") or
        selection_receipt.get("asset_selection_id") or
        "asset_selection_" + selection_digest.split(":", 1)[1][:24],
        "asset_selection_receipt_id")
    if selection_receipt.get("decision") != "SELECT":
        raise CandidateLineageError("candidate lineage requires SELECT asset selection")
    if (type(selection_receipt.get("candidate_budget")) is not int or
            selection_receipt["candidate_budget"] < 1):
        raise CandidateLineageError("candidate lineage asset-selection budget is exhausted")
    selection_support = selection_receipt.get("causal_support") or {}
    if (not isinstance(selection_support, Mapping) or not set(paths) <=
            set(selection_support.get("causal_path_ids") or ())):
        raise CandidateLineageError("candidate lineage causal paths differ from selection")
    if selection_receipt.get("resolved_state_id") != state_id:
        raise CandidateLineageError("candidate lineage asset-selection state mismatch")
    if selection_receipt.get("routing_receipt_id") != route_id:
        raise CandidateLineageError("candidate lineage asset-selection routing mismatch")
    if tuple(selection_receipt.get("selected_asset_ids") or ()) != (asset_id,):
        raise CandidateLineageError("candidate lineage asset-selection asset mismatch")
    if knowledge_id not in tuple(selection_receipt.get("knowledge_object_ids") or ()):
        raise CandidateLineageError("candidate lineage knowledge is absent from selection")

    binding_digest = _digest_text(binding_payload.get("binding_digest"),
                                  "binding_digest")
    binding_id = _text(binding_payload.get("binding_receipt_id") or
                       "binding_" + binding_digest.split(":", 1)[1][:24],
                       "binding_receipt_id")
    if binding_payload.get("eligible") is not True:
        raise CandidateLineageError("candidate lineage runtime binding is not eligible")
    if binding_payload.get("asset_id") != asset_id:
        raise CandidateLineageError("candidate lineage binding asset mismatch")
    if binding_payload.get("knowledge_id") != knowledge_id:
        raise CandidateLineageError("candidate lineage binding knowledge mismatch")

    provenance = candidate_payload.get("provenance")
    provenance = provenance if isinstance(provenance, Mapping) else {}
    if provenance.get("routing_receipt_id") != route_id:
        raise CandidateLineageError("candidate lineage candidate-routing mismatch")
    if provenance.get("asset_selection_receipt_id") != selection_id:
        raise CandidateLineageError("candidate lineage candidate-selection mismatch")
    if provenance.get("binding_digest") != binding_digest:
        raise CandidateLineageError("candidate lineage candidate-binding mismatch")
    if candidate_payload.get("binding_receipt_id") != binding_id:
        raise CandidateLineageError("candidate lineage candidate binding ID mismatch")
    structured_digest = _candidate_receipt_digest(candidate_payload)

    if execution_payload.get("evaluation_only") is not True:
        raise CandidateLineageError("candidate lineage execution is not evaluation-only")
    if execution_payload.get("source") != "structured_memory":
        raise CandidateLineageError("candidate lineage execution source is not structured memory")
    if execution_payload.get("candidate_id") != candidate_id:
        raise CandidateLineageError("candidate lineage execution candidate ID mismatch")
    if execution_payload.get("candidate_digest") != candidate_digest:
        raise CandidateLineageError("candidate lineage execution candidate digest mismatch")
    execution_digest = _digest({
        key: execution_payload.get(key) for key in (
            "version", "case_id", "candidate_id", "source", "action_digest",
            "candidate_digest", "compile_result", "functional_result",
            "signoff_result", "outcome", "created_regressions", "obligations",
            "toolchain_digest", "oracle_digest", "produced_transition_id",
            "budget", "evaluation_only", "metadata")})
    supplied_execution_digest = execution_payload.get("execution_digest")
    if supplied_execution_digest is not None and supplied_execution_digest != execution_digest:
        raise CandidateLineageError("candidate lineage execution digest mismatch")

    return CandidateLineageReceipt(
        candidate_id=candidate_id, candidate_digest=candidate_digest,
        structured_candidate_receipt_digest=structured_digest,
        routing_receipt_id=route_id, routing_decision_digest=route_digest,
        asset_selection_receipt_id=selection_id,
        asset_selection_receipt_digest=selection_digest,
        binding_receipt_id=binding_id, binding_digest=binding_digest,
        execution_receipt_digest=execution_digest,
        knowledge_object_id=knowledge_id, asset_id=asset_id,
        causal_path_ids=paths, eligible=True)


__all__ = [
    "CANDIDATE_LINEAGE_VERSION", "CandidateLineageError",
    "CandidateLineageReceipt", "build_candidate_lineage",
]
