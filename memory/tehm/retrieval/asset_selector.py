"""Knowledge-grounded Asset Memory selection in the P7 shadow lane.

An asset is only useful to the memory experiment when it is tied to an
applicable, authority-checked :class:`MechanismKnowledge` claim.  This module
therefore sits *after* state/knowledge routing and before any candidate or
activation pipeline.  It returns a content-addressed receipt and the registry
definitions that would be advisory; it never emits a ``MemoryCandidate``,
executes an RTL action, changes lifecycle state, or grants production
authority.

``compatibility_mode=True`` is intentionally narrow.  It keeps the older
asset fixtures inspectable while strict campaigns migrate their proposals to
``provenance.mechanism_knowledge_ids`` and a concrete manifest binding.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import json
import sqlite3

from contracts import MemoryQuery, MemoryRoutingDecision, RepairContext
from tehm.assets.registry import get_asset, get_asset_status
from tehm.assets.validation import validate_asset_schema
from tehm.ids import stable_dumps
from tehm.knowledge.applicability import evaluate_applicability
from tehm.knowledge.registry import get_knowledge_by_object_id
from tehm.state.resolver import StateResolutionError, resolve_current_state


ASSET_SELECTOR_VERSION = "asset-selector-v0.1"
ASSET_SELECTION_DECISIONS = ("SELECT", "ABSTAIN", "INAPPLICABLE", "NO_SKILL")
MAX_KNOWLEDGE_GROUNDED_ASSETS = 1
_ADVISORY_ASSET_STATUSES = frozenset({"candidate", "promoted"})


class AssetSelectorError(ValueError):
    """A malformed selection request cannot be evaluated safely."""


def _mapping(value: object, field_name: str) -> dict:
    if not isinstance(value, Mapping):
        raise AssetSelectorError(f"asset selection {field_name} must be an object")
    try:
        decoded = json.loads(stable_dumps(dict(value)))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AssetSelectorError(
            f"asset selection {field_name} must be JSON-serializable") from exc
    if not isinstance(decoded, dict):  # pragma: no cover - serializer guarantee
        raise AssetSelectorError(f"asset selection {field_name} must be an object")
    return decoded


def _ids(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)) or isinstance(value, (str, bytes)):
        raise AssetSelectorError(f"asset selection {field_name} must be a sequence")
    result = tuple(value)
    if any(type(item) is not str or not item for item in result):
        raise AssetSelectorError(
            f"asset selection {field_name} must contain non-empty strings")
    if len(set(result)) != len(result):
        raise AssetSelectorError(f"asset selection {field_name} must not contain duplicates")
    return result


@dataclass(frozen=True)
class AssetSelectionReceipt:
    """Replayable proof of one shadow asset-selection attempt."""

    decision: str
    resolved_state_id: str
    routing_receipt_id: str
    knowledge_object_ids: tuple[str, ...]
    selected_asset_ids: tuple[str, ...]
    applicability: dict
    causal_support: dict
    binding: dict
    abstain_reasons: tuple[str, ...] = ()
    candidate_budget: int = MAX_KNOWLEDGE_GROUNDED_ASSETS
    selector_version: str = ASSET_SELECTOR_VERSION
    shadow_only: bool = True

    def __post_init__(self) -> None:
        if self.decision not in ASSET_SELECTION_DECISIONS:
            raise AssetSelectorError(
                f"asset selection decision must be one of {ASSET_SELECTION_DECISIONS}")
        for field_name in ("resolved_state_id", "routing_receipt_id", "selector_version"):
            value = getattr(self, field_name)
            if type(value) is not str or not value:
                raise AssetSelectorError(f"asset selection {field_name} is required")
        _ids(self.knowledge_object_ids, "knowledge_object_ids")
        _ids(self.selected_asset_ids, "selected_asset_ids")
        if len(self.selected_asset_ids) > MAX_KNOWLEDGE_GROUNDED_ASSETS:
            raise AssetSelectorError("P7 asset selection allows at most one asset")
        _ids(self.abstain_reasons, "abstain_reasons")
        _mapping(self.applicability, "applicability")
        _mapping(self.causal_support, "causal_support")
        _mapping(self.binding, "binding")
        if type(self.candidate_budget) is not int or self.candidate_budget < 0:
            raise AssetSelectorError("asset selection candidate_budget must be non-negative")
        if self.candidate_budget > MAX_KNOWLEDGE_GROUNDED_ASSETS:
            raise AssetSelectorError("P7 asset selection budget allows at most one asset")
        if self.shadow_only is not True:
            raise AssetSelectorError("asset selection receipts are shadow-only")
        if self.decision in {"ABSTAIN", "INAPPLICABLE", "NO_SKILL"} and self.selected_asset_ids:
            raise AssetSelectorError(
                f"{self.decision} asset selection cannot select an asset")
        if self.decision == "SELECT" and not self.selected_asset_ids:
            raise AssetSelectorError("SELECT asset selection requires an asset")

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "resolved_state_id": self.resolved_state_id,
            "routing_receipt_id": self.routing_receipt_id,
            "knowledge_object_ids": list(self.knowledge_object_ids),
            "selected_asset_ids": list(self.selected_asset_ids),
            "applicability": _mapping(self.applicability, "applicability"),
            "causal_support": _mapping(self.causal_support, "causal_support"),
            "binding": _mapping(self.binding, "binding"),
            "abstain_reasons": list(self.abstain_reasons),
            "candidate_budget": self.candidate_budget,
            "selector_version": self.selector_version,
            "shadow_only": self.shadow_only,
        }

    @property
    def receipt_digest(self) -> str:
        import hashlib

        return "sha256:" + hashlib.sha256(
            stable_dumps(self.to_dict()).encode()).hexdigest()

    @property
    def selection_receipt_id(self) -> str:
        return "asset_selection_" + self.receipt_digest.split(":", 1)[1][:24]

    # ``asset_selection_id`` is a convenient compatibility spelling for
    # callers that treat every shadow receipt as an ID-bearing decision.
    @property
    def asset_selection_id(self) -> str:
        return self.selection_receipt_id

    @classmethod
    def from_dict(cls, payload: object) -> "AssetSelectionReceipt":
        if not isinstance(payload, Mapping):
            raise AssetSelectorError("asset selection receipt must be an object")
        required = {
            "decision", "resolved_state_id", "routing_receipt_id",
            "knowledge_object_ids", "selected_asset_ids", "applicability",
            "causal_support", "binding", "abstain_reasons", "candidate_budget",
            "selector_version", "shadow_only",
        }
        if any(key not in payload for key in required):
            raise AssetSelectorError("asset selection receipt is missing required fields")
        receipt = cls(
            decision=payload["decision"],
            resolved_state_id=payload["resolved_state_id"],
            routing_receipt_id=payload["routing_receipt_id"],
            knowledge_object_ids=tuple(payload["knowledge_object_ids"]),
            selected_asset_ids=tuple(payload["selected_asset_ids"]),
            applicability=dict(payload["applicability"]),
            causal_support=dict(payload["causal_support"]),
            binding=dict(payload["binding"]),
            abstain_reasons=tuple(payload["abstain_reasons"]),
            candidate_budget=payload["candidate_budget"],
            selector_version=payload["selector_version"],
            shadow_only=payload["shadow_only"],
        )
        supplied = payload.get("receipt_digest")
        if supplied is not None and supplied != receipt.receipt_digest:
            raise AssetSelectorError("asset selection receipt digest mismatch")
        return receipt


@dataclass(frozen=True)
class AssetSelection:
    """Advisory registry objects paired with their selection receipt."""

    assets: tuple[dict, ...]
    receipt: AssetSelectionReceipt
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.assets, tuple):
            raise AssetSelectorError("asset selection assets must be a tuple")
        if any(not isinstance(asset, Mapping) for asset in self.assets):
            raise AssetSelectorError("asset selection assets must be objects")
        if len(self.assets) != len(self.receipt.selected_asset_ids):
            raise AssetSelectorError("asset selection assets do not match receipt IDs")
        _mapping(self.metadata, "metadata")

    def to_dict(self) -> dict:
        return {
            "assets": [dict(asset) for asset in self.assets],
            "receipt": self.receipt.to_dict(),
            "metadata": _mapping(self.metadata, "metadata"),
        }


def _query(value: MemoryQuery | RepairContext) -> MemoryQuery:
    if isinstance(value, RepairContext):
        from tehm.retrieval.query_planner import plan_query

        value = plan_query(value)
    if not isinstance(value, MemoryQuery):
        raise TypeError("asset selection requires MemoryQuery or RepairContext")
    if not isinstance(value.query_plan, Mapping):
        raise AssetSelectorError("asset selection query_plan must be an object")
    return value


def _scope(query: MemoryQuery) -> dict:
    from tehm.retrieval.memory_router import scope_for_query

    return scope_for_query(query)


def _context(query: MemoryQuery, scope: Mapping) -> dict:
    plan = dict(query.query_plan)
    for key in ("mechanism_family", "compatibility_profile", "target_scope"):
        if key in scope:
            plan[key] = scope[key]
    signature = plan.get("mechanism_signature")
    if isinstance(signature, Mapping):
        for key, value in signature.items():
            plan.setdefault(key, value)
    return plan


def _status_for_scope(conn: sqlite3.Connection, *, asset_id: str,
                      target_scope: str, profile: str | None) -> dict | None:
    scopes = [target_scope]
    if profile and profile not in scopes:
        scopes.append(profile)
    if "global" not in scopes:
        scopes.append("global")
    for candidate in scopes:
        row = get_asset_status(conn, asset_id=asset_id, target_scope=candidate)
        if row is not None:
            return row
    return None


def _knowledge_refs(asset: Mapping) -> tuple[str, ...]:
    provenance = asset.get("provenance") or {}
    if not isinstance(provenance, Mapping):
        return ()
    raw = (provenance.get("mechanism_knowledge_ids") or
           provenance.get("knowledge_object_ids") or
           provenance.get("knowledge_ids") or provenance.get("knowledge_refs"))
    if raw is None:
        raw = provenance.get("knowledge_id")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return ()
    refs: list[str] = []
    for value in raw:
        if isinstance(value, Mapping):
            value = value.get("object_id") or value.get("knowledge_object_id")
        if type(value) is str and value.strip() and "@" in value:
            refs.append(value.strip())
    return tuple(sorted(set(refs)))


def _binding(asset: Mapping, *, profiles: set[str], families: set[str],
             knowledge_ids: set[str], strict: bool) -> tuple[bool, str, dict]:
    schema_valid, schema_errors = validate_asset_schema(asset)
    if not schema_valid:
        return False, "asset_schema_invalid", {"schema_errors": list(schema_errors)}
    if asset.get("asset_type") != "RTL_REWRITE_TEMPLATE":
        return False, "asset_type_not_supported", {"asset_type": asset.get("asset_type")}
    compatibility = asset.get("compatibility") or {}
    profile = compatibility.get("compatibility_profile") if isinstance(
        compatibility, Mapping) else None
    if profiles and profile not in profiles:
        return False, "asset_compatibility_profile_mismatch", {"profile": profile}
    provenance = asset.get("provenance") or {}
    if not isinstance(provenance, Mapping):
        return False, "asset_provenance_invalid", {}
    family = provenance.get("bound_mechanism_family")
    if families and family is not None and family not in families:
        return False, "asset_mechanism_family_mismatch", {"bound_family": family}
    definition = asset.get("definition") or {}
    action = definition.get("action") if isinstance(definition, Mapping) else None
    payload = action.get("payload") if isinstance(action, Mapping) else None
    if not isinstance(action, Mapping) or not isinstance(payload, Mapping):
        return False, "asset_binding_payload_missing", {}
    if strict:
        if provenance.get("binding_contract") != "manifest_fix_v1":
            return False, "asset_binding_proof_missing", {"binding_contract": provenance.get("binding_contract")}
        if not str(provenance.get("binding_digest") or "").startswith("sha256:"):
            return False, "asset_binding_digest_missing", {}
        refs = _knowledge_refs(asset)
        if not refs:
            return False, "asset_knowledge_binding_missing", {}
        matched = sorted(set(refs) & knowledge_ids)
        if not matched:
            return False, "asset_knowledge_binding_mismatch", {
                "asset_knowledge_ids": list(refs),
            }
    else:
        refs = _knowledge_refs(asset)
        matched = sorted(set(refs) & knowledge_ids)
    return True, "asset_binding_verified", {
        "binding_contract": provenance.get("binding_contract"),
        "binding_digest": provenance.get("binding_digest"),
        "bound_mechanism_family": family,
        "compatibility_profile": profile,
        "knowledge_object_ids": matched,
        "payload_keys": sorted(str(key) for key in payload),
    }


def _promoted_authority_verified(conn: sqlite3.Connection, *, asset_id: str,
                                 target_scope: str) -> bool:
    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        ("tehm_asset_authority_receipts",)).fetchone()
    if table is None:
        return False
    from tehm.assets.authority import verify_asset_authority

    rows = conn.execute(
        "SELECT receipt_json FROM tehm_asset_authority_receipts "
        "WHERE asset_id=? AND target_scope=? AND eligible=1 "
        "ORDER BY authority_receipt_id", (asset_id, target_scope)).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["receipt_json"])
            if verify_asset_authority(conn, payload).get("eligible") is True:
                return True
        except (TypeError, ValueError, KeyError, json.JSONDecodeError, sqlite3.Error):
            continue
    return False


def _receipt(*, decision: str, state_id: str, routing_id: str,
             knowledge_ids: tuple[str, ...], applicability: dict,
             causal_support: dict, binding: dict, reasons: tuple[str, ...],
             candidate_budget: int) -> AssetSelection:
    receipt = AssetSelectionReceipt(
        decision=decision, resolved_state_id=state_id,
        routing_receipt_id=routing_id,
        knowledge_object_ids=tuple(sorted(set(knowledge_ids))),
        selected_asset_ids=(), applicability=applicability,
        causal_support=causal_support, binding=binding,
        abstain_reasons=tuple(sorted(set(reasons))),
        candidate_budget=candidate_budget)
    return AssetSelection(assets=(), receipt=receipt)


def select_knowledge_grounded_assets(
    conn: sqlite3.Connection,
    query: MemoryQuery | RepairContext,
    *,
    routing: MemoryRoutingDecision | None = None,
    candidate_budget: int = MAX_KNOWLEDGE_GROUNDED_ASSETS,
    mode: str = "shadow",
    compatibility_mode: bool = False,
) -> AssetSelection:
    """Select at most one knowledge-grounded asset for shadow evaluation.

    A supplied routing receipt is treated as a reference, never as authority:
    the current state is resolved again and every selected asset is checked
    against registry content, lifecycle status, authority, binding proof, and
    the applicable knowledge claim.  ``compatibility_mode`` only relaxes the
    knowledge-object binding for legacy fixtures; it remains shadow-only.
    """
    if mode != "shadow":
        raise StateResolutionError(
            "P7 knowledge-grounded asset selection is shadow-only; production selection is not established")
    if type(candidate_budget) is not int or candidate_budget < 0:
        raise AssetSelectorError("candidate_budget must be a non-negative integer")
    if candidate_budget > MAX_KNOWLEDGE_GROUNDED_ASSETS:
        raise AssetSelectorError("P7 asset selection allows at most one asset")
    typed_query = _query(query)
    scope = _scope(typed_query)
    context = _context(typed_query, scope)
    if routing is None:
        from tehm.retrieval.memory_router import route_memory

        routing = route_memory(
            conn, typed_query, no_memory_budget=1,
            memory_budget=min(1, candidate_budget), mode="shadow",
            persist_state=False, commit=False)
    if not isinstance(routing, MemoryRoutingDecision):
        raise TypeError("asset selection routing must be MemoryRoutingDecision")
    routing_id = routing.routing_receipt_id
    if routing.decision == "ABSTAIN":
        return _receipt(
            decision="ABSTAIN", state_id=routing.resolved_state_id,
            routing_id=routing_id, knowledge_ids=(), applicability=routing.applicability,
            causal_support=routing.causal_support, binding={},
            reasons=routing.abstain_reasons or ("routing_abstain",),
            candidate_budget=candidate_budget)
    if routing.decision == "INAPPLICABLE":
        return _receipt(
            decision="INAPPLICABLE", state_id=routing.resolved_state_id,
            routing_id=routing_id, knowledge_ids=(), applicability=routing.applicability,
            causal_support=routing.causal_support, binding={},
            reasons=routing.abstain_reasons or ("routing_inapplicable",),
            candidate_budget=candidate_budget)
    legacy_no_skill = compatibility_mode and routing.decision == "NO_SKILL"
    if (routing.decision == "NO_SKILL" and not legacy_no_skill) or candidate_budget == 0:
        return _receipt(
            decision="NO_SKILL", state_id=routing.resolved_state_id,
            routing_id=routing_id, knowledge_ids=(), applicability=routing.applicability,
            causal_support=routing.causal_support, binding={},
            reasons=routing.abstain_reasons or ("no_skill",),
            candidate_budget=candidate_budget)
    try:
        state = resolve_current_state(
            conn, scope, mode="shadow", persist=False, commit=False)
    except (StateResolutionError, ValueError, TypeError, sqlite3.Error) as exc:
        return _receipt(
            decision="ABSTAIN", state_id="UNRESOLVED", routing_id=routing_id,
            knowledge_ids=(), applicability={"status": "UNRESOLVED"},
            causal_support={"status": "UNRESOLVED"}, binding={},
            reasons=(f"state_unresolved:{exc}",), candidate_budget=candidate_budget)
    if state.resolution_id != routing.resolved_state_id:
        return _receipt(
            decision="ABSTAIN", state_id=state.resolution_id,
            routing_id=routing_id, knowledge_ids=(),
            applicability={"status": "ROUTING_STATE_MISMATCH"},
            causal_support={"status": "NOT_EVALUATED"}, binding={},
            reasons=("routing_state_mismatch",), candidate_budget=candidate_budget)
    if routing.memory_budget < 1 and not legacy_no_skill:
        return _receipt(
            decision="NO_SKILL", state_id=state.resolution_id, routing_id=routing_id,
            knowledge_ids=(), applicability=routing.applicability,
            causal_support=routing.causal_support, binding={},
            reasons=("routing_memory_budget_zero",), candidate_budget=candidate_budget)
    if (not legacy_no_skill and
            any(asset_id not in state.active_assets for asset_id in routing.selected_asset_ids)):
        return _receipt(
            decision="ABSTAIN", state_id=state.resolution_id, routing_id=routing_id,
            knowledge_ids=(), applicability={"status": "ROUTING_ASSET_NOT_ACTIVE"},
            causal_support={"status": "NOT_EVALUATED"}, binding={},
            reasons=("routing_asset_not_active",), candidate_budget=candidate_budget)
    if any(path_id not in state.active_causal_paths for path_id in routing.selected_path_ids):
        return _receipt(
            decision="ABSTAIN", state_id=state.resolution_id, routing_id=routing_id,
            knowledge_ids=(), applicability={"status": "ROUTING_PATH_NOT_ACTIVE"},
            causal_support={"status": "NOT_EVALUATED"}, binding={},
            reasons=("routing_path_not_active",), candidate_budget=candidate_budget)
    if state.unresolved_conflicts:
        return _receipt(
            decision="ABSTAIN", state_id=state.resolution_id, routing_id=routing_id,
            knowledge_ids=(), applicability={"status": "UNRESOLVED"},
            causal_support={"status": "UNRESOLVED"}, binding={},
            reasons=tuple(sorted(state.unresolved_conflicts)), candidate_budget=candidate_budget)

    claims: list[dict] = []
    integrity: list[str] = []
    applicability = dict(routing.applicability)
    if not compatibility_mode:
        # Reuse the router's fail-closed knowledge/causal checks.  This keeps
        # routing and asset selection on the same authority interpretation.
        from tehm.retrieval.memory_router import _knowledge_for_state

        try:
            claims, vetoes, insufficient, integrity = _knowledge_for_state(
                conn, state=state, scope=scope, context=context)
        except (TypeError, ValueError, KeyError, sqlite3.Error) as exc:
            return _receipt(
                decision="ABSTAIN", state_id=state.resolution_id,
                routing_id=routing_id, knowledge_ids=(), applicability=applicability,
                causal_support={"status": "INVALID"}, binding={},
                reasons=(f"knowledge_resolution_failed:{exc}",),
                candidate_budget=candidate_budget)
        if vetoes:
            return _receipt(
                decision="INAPPLICABLE", state_id=state.resolution_id,
                routing_id=routing_id, knowledge_ids=(), applicability=applicability,
                causal_support={"status": "VETOED"}, binding={}, reasons=tuple(vetoes),
                candidate_budget=candidate_budget)
        if integrity:
            return _receipt(
                decision="ABSTAIN", state_id=state.resolution_id,
                routing_id=routing_id, knowledge_ids=(), applicability=applicability,
                causal_support={"status": "INVALID"}, binding={}, reasons=tuple(integrity),
                candidate_budget=candidate_budget)
        if not claims:
            reasons = tuple(insufficient) or ("mechanism_knowledge_required",)
            return _receipt(
                decision="ABSTAIN", state_id=state.resolution_id,
                routing_id=routing_id, knowledge_ids=(), applicability=applicability,
                causal_support={"status": "INSUFFICIENT"}, binding={}, reasons=reasons,
                candidate_budget=candidate_budget)
    knowledge_ids = tuple(sorted(item["claim"].object_id for item in claims))
    families = {item["claim"].mechanism_family for item in claims}
    profiles = {item["claim"].compatibility_profile for item in claims
                if item["claim"].compatibility_profile is not None}
    if compatibility_mode:
        family = context.get("mechanism_family")
        profile = context.get("compatibility_profile")
        families = {family} if isinstance(family, str) else set()
        profiles = {profile} if isinstance(profile, str) else set()
    path_ids = tuple(sorted({path_id for item in claims for path_id in item.get("path_ids", ())}))
    selected_ids = (tuple(sorted(state.active_assets))[:candidate_budget]
                    if legacy_no_skill else
                    tuple(routing.selected_asset_ids[:candidate_budget]))
    if not selected_ids:
        return _receipt(
            decision="NO_SKILL", state_id=state.resolution_id, routing_id=routing_id,
            knowledge_ids=knowledge_ids, applicability=applicability,
            causal_support={**routing.causal_support, "causal_path_ids": list(path_ids)},
            binding={}, reasons=("routing_selected_asset_missing",),
            candidate_budget=candidate_budget)
    binding_failures: list[str] = []
    for asset_id in selected_ids:
        asset = get_asset(conn, asset_id)
        if asset is None:
            binding_failures.append(f"{asset_id}:asset_registry_invalid")
            continue
        compatibility = asset.get("compatibility") or {}
        profile = compatibility.get("compatibility_profile") if isinstance(
            compatibility, Mapping) else None
        try:
            status = _status_for_scope(
                conn, asset_id=asset_id,
                target_scope=str(scope.get("target_scope") or "global"), profile=profile)
        except (TypeError, ValueError, sqlite3.Error) as exc:
            binding_failures.append(f"{asset_id}:asset_status_invalid:{exc}")
            continue
        if status is None or status.get("status") not in _ADVISORY_ASSET_STATUSES:
            binding_failures.append(f"{asset_id}:asset_status_not_advisory")
            continue
        if status.get("status") == "promoted" and not _promoted_authority_verified(
                conn, asset_id=asset_id, target_scope=str(status.get("target_scope") or "")):
            binding_failures.append(f"{asset_id}:promoted_authority_unverified")
            continue
        ok, reason, proof = _binding(
            asset, profiles=profiles, families=families,
            knowledge_ids=set(knowledge_ids), strict=not compatibility_mode)
        if not ok:
            binding_failures.append(f"{asset_id}:{reason}")
            continue
        receipt = AssetSelectionReceipt(
            decision="SELECT", resolved_state_id=state.resolution_id,
            routing_receipt_id=routing_id, knowledge_object_ids=knowledge_ids,
            selected_asset_ids=(asset_id,), applicability=applicability,
            causal_support={**routing.causal_support,
                            "status": "SUPPORTED" if path_ids else routing.causal_support.get("status"),
                            "causal_path_ids": list(path_ids)},
            binding={"assets": {asset_id: proof}, "compatibility_mode": compatibility_mode},
            abstain_reasons=(), candidate_budget=candidate_budget)
        return AssetSelection(assets=(asset,), receipt=receipt,
                              metadata={"asset_status": status.get("status"),
                                        "shadow_only": True})
    return _receipt(
        decision="ABSTAIN", state_id=state.resolution_id, routing_id=routing_id,
        knowledge_ids=knowledge_ids, applicability=applicability,
        causal_support={**routing.causal_support, "causal_path_ids": list(path_ids)},
        binding={"compatibility_mode": compatibility_mode},
        reasons=tuple(binding_failures) or ("asset_binding_failed",),
        candidate_budget=candidate_budget)


select_assets = select_knowledge_grounded_assets
select_asset = select_knowledge_grounded_assets


__all__ = [
    "ASSET_SELECTION_DECISIONS", "ASSET_SELECTOR_VERSION",
    "MAX_KNOWLEDGE_GROUNDED_ASSETS", "AssetSelection", "AssetSelectionReceipt",
    "AssetSelectorError", "select_asset", "select_assets",
    "select_knowledge_grounded_assets",
]
