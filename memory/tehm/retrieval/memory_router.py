"""Shadow-only state/knowledge/asset memory routing.

The ordinary retrieval pipeline remains the production promoted-rule lane.  This
module is its deliberately separate P5 sibling: it resolves the current state,
checks validated mechanism knowledge and asset gates, and emits a typed
``MemoryRoutingDecision`` describing what a candidate generator *may* use.  It
never inserts a candidate, executes an asset, changes a lifecycle row, or
promotes memory.  The no-memory arm is always retained.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping

from contracts import (
    MEMORY_ROUTING_DECISIONS,
    MemoryCandidate,
    MemoryQuery,
    MemoryRoutingDecision,
    RepairContext,
)
from tehm.assets.registry import get_asset, get_asset_status
from tehm.causal.evidence_level import at_least, evidence_rank
from tehm.causal.path_builder import validate_persisted_path_row
from tehm.ids import stable_dumps
from tehm.knowledge.applicability import evaluate_applicability
from tehm.knowledge.authority import evaluate_knowledge_authority
from tehm.knowledge.registry import get_knowledge_by_object_id
from tehm.knowledge.lifecycle import get_knowledge_status
from tehm.state import StateResolutionError, resolve_current_state


ROUTER_VERSION = "memory-router-v0.1"
MIN_CAUSAL_EVIDENCE = "L2_CONTROLLED_INTERVENTION"
_VALIDATED_KNOWLEDGE_STATUS = "validated"
_ADVISORY_ASSET_STATUSES = frozenset({"candidate", "promoted"})


class MemoryRouterError(ValueError):
    """A malformed routing request cannot be evaluated safely."""


def _query_plan(query: MemoryQuery | RepairContext) -> dict:
    if isinstance(query, RepairContext):
        from tehm.retrieval.query_planner import plan_query

        query = plan_query(query)
    if not isinstance(query, MemoryQuery):
        raise TypeError("memory routing requires MemoryQuery or RepairContext")
    if not isinstance(query.query_plan, Mapping):
        raise MemoryRouterError("memory routing query_plan must be an object")
    try:
        # JSON round-trip rejects caller-side Mapping implementations and
        # values that would make a receipt non-deterministic.
        plan = json.loads(stable_dumps(dict(query.query_plan)))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MemoryRouterError("memory routing query_plan is not JSON-serializable") from exc
    if not isinstance(plan, dict):  # pragma: no cover - serializer guarantee
        raise MemoryRouterError("memory routing query_plan must be an object")
    return plan


def _mechanism_family(plan: Mapping) -> str | None:
    direct = plan.get("mechanism_family")
    signature = plan.get("mechanism_signature")
    if direct is None and isinstance(signature, Mapping):
        direct = (signature.get("mechanism_family") or signature.get("family")
                  or signature.get("type"))
    if direct is None:
        return None
    if type(direct) is not str or not direct.strip():
        raise MemoryRouterError("memory routing mechanism_family is invalid")
    return direct.strip()


def _compatibility_profile(plan: Mapping) -> str | None:
    direct = plan.get("compatibility_profile")
    signature = plan.get("mechanism_signature")
    if direct is None and isinstance(signature, Mapping):
        direct = signature.get("compatibility_profile")
    if direct is None:
        return None
    if type(direct) is not str or not direct.strip():
        raise MemoryRouterError("memory routing compatibility_profile is invalid")
    return direct.strip()


def scope_for_query(query: MemoryQuery | RepairContext) -> dict:
    """Derive resolver scope from a typed query; caller fields are not trusted."""
    plan = _query_plan(query)
    scope: dict[str, str] = {}
    family = _mechanism_family(plan)
    profile = _compatibility_profile(plan)
    target_scope = plan.get("target_scope")
    if target_scope is None and isinstance(plan.get("scope"), Mapping):
        target_scope = plan["scope"].get("target_scope")
    if target_scope is None:
        # Rule lifecycle rows commonly use the concrete failing check (for
        # example ``route``) as their target scope.  A planned query therefore
        # inherits its check unless a caller supplies a narrower scope.
        target_scope = plan.get("check") or "global"
    if family is not None:
        scope["mechanism_family"] = family
    if profile is not None:
        scope["compatibility_profile"] = profile
    if target_scope is not None:
        if type(target_scope) is not str or not target_scope.strip():
            raise MemoryRouterError("memory routing target_scope is invalid")
        scope["target_scope"] = target_scope.strip()
    else:
        # A query without an explicit runtime scope is the global shadow
        # lane.  Supplying the default prevents the resolver from mixing
        # every scope-local lifecycle row into one ambiguous state.
        scope["target_scope"] = "global"
    return scope


def _applicability_context(plan: Mapping, scope: Mapping) -> dict:
    context: dict = dict(plan)
    # The resolver-derived values override any nested signature aliases.
    for key in ("mechanism_family", "compatibility_profile", "target_scope"):
        if key in scope:
            context[key] = scope[key]
    signature = plan.get("mechanism_signature")
    if isinstance(signature, Mapping):
        # Positive/negative applicability predicates are flat typed facts.  A
        # signature may provide aliases, but never overwrites explicit values.
        for key, value in signature.items():
            context.setdefault(key, value)
    return context


def _budget(no_memory_budget: object, memory_budget: object) -> tuple[int, int, int]:
    for value, name in ((no_memory_budget, "no_memory_budget"),
                        (memory_budget, "memory_budget")):
        if type(value) is not int or value < 0:
            raise MemoryRouterError(
                f"memory routing {name} must be a non-negative integer")
    no = int(no_memory_budget)
    memory = int(memory_budget)
    if no < 1:
        raise MemoryRouterError(
            "memory routing requires at least one no-memory candidate")
    if memory > 2:
        raise MemoryRouterError(
            "memory routing shadow budget allows at most two memory/causal candidates")
    return no, memory, no + memory


def _empty_decision(*, decision: str, state_id: str, reasons: tuple[str, ...],
                    total_budget: int, applicability: dict | None = None,
                    causal_support: dict | None = None,
                    risk: dict | None = None) -> MemoryRoutingDecision:
    if decision not in MEMORY_ROUTING_DECISIONS:
        raise MemoryRouterError(f"unknown memory routing decision: {decision}")
    return MemoryRoutingDecision(
        decision=decision, resolved_state_id=state_id,
        selected_rule_ids=(), selected_path_ids=(), selected_asset_ids=(),
        applicability=applicability or {},
        causal_support=causal_support or {}, risk=risk or {},
        abstain_reasons=tuple(reasons), no_memory_budget=max(1, total_budget),
        memory_budget=0)


def _status_for_scope(conn: sqlite3.Connection, *, table: str,
                      key_column: str, key_value: str,
                      target_scope: str, version: int | None = None,
                      fallback_scopes: tuple[str, ...] = ()) -> dict | None:
    """Read an exact scope, then the explicit global fallback."""
    if table == "tehm_asset_status":
        row = get_asset_status(conn, asset_id=key_value, target_scope=target_scope)
        for candidate_scope in (*fallback_scopes, "global"):
            if row is not None or candidate_scope == target_scope:
                continue
            row = get_asset_status(conn, asset_id=key_value,
                                   target_scope=candidate_scope)
        return row
    predicates = [f"{key_column}=?", "target_scope=?"]
    values: list[object] = [key_value, target_scope]
    if version is not None:
        predicates.append("version=?")
        values.append(version)
    where = " AND ".join(predicates)
    row = conn.execute(f"SELECT * FROM {table} WHERE {where}", values).fetchone()
    if row is None and target_scope != "global":
        values[-2 if version is not None else -1] = "global"
        row = conn.execute(f"SELECT * FROM {table} WHERE {where}", values).fetchone()
    return dict(row) if row is not None else None


def _knowledge_for_state(conn: sqlite3.Connection, *, state, scope: Mapping,
                         context: Mapping) -> tuple[list[dict], list[str], list[str], list[str]]:
    """Return validated/evidenced claims plus veto and integrity reasons."""
    target_scope = str(scope.get("target_scope") or "global")
    eligible: list[dict] = []
    vetoes: list[str] = []
    insufficient: list[str] = []
    integrity: list[str] = []
    for object_id in state.active_knowledge_claims:
        try:
            # The resolver may activate a global claim for a scoped query.  A
            # scoped status row is preferred, but the registry lookup itself
            # is exact-scope and therefore needs the same explicit global
            # fallback as the status lookup.
            try:
                claim = get_knowledge_by_object_id(
                    conn, object_id, target_scope=target_scope)
            except ValueError as exc:
                if (target_scope == "global" or
                        "status row is missing" not in str(exc)):
                    raise
                claim = get_knowledge_by_object_id(
                    conn, object_id, target_scope="global")
            status = _status_for_scope(
                conn, table="tehm_mechanism_knowledge_status",
                key_column="knowledge_id", key_value=claim.knowledge_id,
                target_scope=target_scope, version=claim.version)
            if status is None or int(status.get("version", claim.version)) != claim.version:
                raise ValueError("knowledge status row is missing")
        except (TypeError, ValueError, KeyError) as exc:
            integrity.append(f"KNOWLEDGE_INVALID:{object_id}:{exc}")
            continue
        if status.get("status") != _VALIDATED_KNOWLEDGE_STATUS:
            # P5 deliberately excludes shadow/candidate claims from a memory
            # candidate.  They remain visible to offline evaluation only.
            continue
        applicability = evaluate_applicability(claim, context)
        if applicability.reason == "negative_applicability":
            vetoes.append(f"{object_id}:{applicability.reason}")
            continue
        if applicability.reason in {
                "mechanism_family_mismatch", "compatibility_profile_mismatch"}:
            # A claim for a different mechanism/profile is simply not a
            # candidate for this query.  It must not become a global hard veto
            # when another validated claim is applicable.
            insufficient.append(f"{object_id}:{applicability.reason}")
            continue
        if not applicability.eligible:
            insufficient.append(f"{object_id}:{applicability.reason}")
            continue
        try:
            authority = evaluate_knowledge_authority(conn, claim)
        except (TypeError, ValueError, sqlite3.Error) as exc:
            integrity.append(f"KNOWLEDGE_AUTHORITY_INVALID:{object_id}:{exc}")
            continue
        if not authority.eligible:
            insufficient.append(f"{object_id}:authority:{authority.reason}")
            continue
        if not at_least(claim.evidence_level, MIN_CAUSAL_EVIDENCE):
            insufficient.append(f"{object_id}:causal_evidence:{claim.evidence_level}")
            continue
        path_ids: list[str] = []
        for path_id in claim.causal_path_ids:
            if path_id not in state.active_causal_paths:
                continue
            row = conn.execute(
                "SELECT * FROM tehm_causal_paths WHERE path_id=?", (path_id,)
            ).fetchone()
            if row is None:
                integrity.append(f"CAUSAL_PATH_MISSING:{path_id}")
                continue
            try:
                validate_persisted_path_row(row, conn)
            except (TypeError, ValueError, KeyError) as exc:
                integrity.append(f"CAUSAL_PATH_INVALID:{path_id}:{exc}")
                continue
            if not at_least(row["evidence_level"], MIN_CAUSAL_EVIDENCE):
                continue
            path_ids.append(path_id)
        if not path_ids:
            insufficient.append(f"{object_id}:causal_path_missing_or_weak")
            continue
        eligible.append({
            "claim": claim, "authority": authority,
            "applicability": applicability, "path_ids": tuple(sorted(path_ids)),
        })
    return eligible, vetoes, insufficient, integrity


def _rule_ids(conn: sqlite3.Connection, query: MemoryQuery, state) -> tuple[str, ...]:
    """Report applicable promoted rules without invoking the candidate pipeline."""
    try:
        from tehm.retrieval.index import build_index
        from tehm.retrieval.symbolic_filter import apply_symbolic_filter

        index = build_index(conn, lifecycle_statuses=frozenset({"promoted"}))
        selected = []
        for rule_id in sorted(state.active_rules):
            rule = index.get(rule_id)
            if rule is None:
                continue
            if apply_symbolic_filter(rule, query) == "APPLICABLE":
                selected.append(rule_id)
        return tuple(selected)
    except (TypeError, ValueError, KeyError, sqlite3.Error):
        # A malformed rule is not allowed to make a validated knowledge claim
        # executable.  The router can still return a conservative advisory.
        return ()


def _asset_candidates(conn: sqlite3.Connection, *, state, scope: Mapping,
                      claims: list[dict]) -> tuple[list[str], list[str], dict]:
    """Find compatible candidate/promoted assets and verify promoted authority."""
    target_scope = str(scope.get("target_scope") or "global")
    profiles = {item["claim"].compatibility_profile for item in claims
                if item["claim"].compatibility_profile is not None}
    families = {item["claim"].mechanism_family for item in claims}
    selected: list[str] = []
    promoted_authorized: list[str] = []
    binding_ok: list[str] = []
    authority_ok: list[str] = []
    status_counts: dict[str, int] = {}
    for asset_id in sorted(state.active_assets):
        asset = get_asset(conn, asset_id)
        if asset is None:
            continue
        compatibility = asset.get("compatibility") or {}
        if profiles and compatibility.get("compatibility_profile") not in profiles:
            continue
        provenance = asset.get("provenance") or {}
        bound_family = provenance.get("bound_mechanism_family")
        if bound_family is not None and bound_family not in families:
            continue
        status = _status_for_scope(
            conn, table="tehm_asset_status", key_column="asset_id",
            key_value=asset_id, target_scope=target_scope,
            fallback_scopes=tuple(profile for profile in profiles if profile))
        if status is None or status.get("status") not in _ADVISORY_ASSET_STATUSES:
            continue
        status_name = str(status["status"])
        status_counts[status_name] = status_counts.get(status_name, 0) + 1
        # A parser-backed action with a declared input/output contract is the
        # minimum resolvable binding for a shadow decision.  Actual project
        # binding is still performed by the execution adapter later.
        definition = asset.get("definition") or {}
        action = definition.get("action") if isinstance(definition, Mapping) else None
        binding = isinstance(action, Mapping) and isinstance(action.get("payload"), Mapping)
        if binding:
            binding_ok.append(asset_id)
        if status_name == "promoted":
            # Promotion status alone is not authority.  Recheck a stored,
            # content-bound authority receipt if one exists.
            verified = False
            if (conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    ("tehm_asset_authority_receipts",)).fetchone() is not None):
                rows = conn.execute(
                    "SELECT receipt_json FROM tehm_asset_authority_receipts "
                    "WHERE asset_id=? AND target_scope=? AND eligible=1 "
                    "ORDER BY authority_receipt_id",
                    (asset_id, status.get("target_scope") or target_scope)).fetchall()
                from tehm.assets.authority import verify_asset_authority

                for row in rows:
                    try:
                        payload = json.loads(row["receipt_json"])
                        if verify_asset_authority(conn, payload).get("eligible") is True:
                            verified = True
                            break
                    except (TypeError, ValueError, json.JSONDecodeError, sqlite3.Error):
                        continue
            if verified:
                authority_ok.append(asset_id)
                if binding:
                    promoted_authorized.append(asset_id)
        if status_name == "candidate" or (status_name == "promoted" and verified):
            selected.append(asset_id)
    return (tuple(sorted(set(selected))), tuple(sorted(set(promoted_authorized))), {
        "status_counts": status_counts,
        "binding_resolvable_assets": sorted(set(binding_ok)),
        "authority_verified_assets": sorted(set(authority_ok)),
    })


def _risk(*, claims: list[dict], assets: Mapping, state) -> dict:
    failure_modes = sorted({mode for item in claims
                            for mode in item["claim"].known_failure_modes})
    risk_penalty = 0.0 if not failure_modes else min(1.0, 0.25 * len(failure_modes))
    return {
        "risk_penalty": round(risk_penalty, 6),
        "known_failure_mode_count": len(failure_modes),
        "known_failure_modes": failure_modes,
        "memory_interference": False,
        "authority_verified_assets": list(assets.get("authority_verified_assets", [])),
        "unresolved_state": bool(state.unresolved_conflicts),
    }


def route_memory(
    conn: sqlite3.Connection,
    query: MemoryQuery | RepairContext,
    *,
    no_memory_budget: int = 3,
    memory_budget: int = 0,
    mode: str = "shadow",
    persist_state: bool = False,
    commit: bool = False,
) -> MemoryRoutingDecision:
    """Resolve and route memory in P5 shadow mode.

    ``mode='production'`` is intentionally unavailable until the empirical
    R3 gates exist.  ``persist_state`` may be enabled by the backend seam to
    retain the derived state snapshot; it never mutates canonical evidence.
    """
    if mode != "shadow":
        raise StateResolutionError(
            "P5 memory router is shadow-only; production routing is not established")
    no_budget, memory_capacity, total_budget = _budget(
        no_memory_budget, memory_budget)
    del no_budget  # allocation is recomputed for the selected decision below
    plan = _query_plan(query)
    typed_query = (query if isinstance(query, MemoryQuery) else
                   __import__("tehm.retrieval.query_planner", fromlist=["plan_query"])
                   .plan_query(query))
    scope = scope_for_query(typed_query)
    context = _applicability_context(plan, scope)
    had_outer_transaction = conn.in_transaction
    try:
        state = resolve_current_state(
            conn, scope, mode="shadow", persist=persist_state, commit=commit)
    except (StateResolutionError, ValueError, TypeError, sqlite3.Error) as exc:
        # A failed derived snapshot write must not leave an implicit backend
        # transaction open.  Preserve a caller-owned outer transaction.
        if commit and not had_outer_transaction and conn.in_transaction:
            conn.rollback()
        return _empty_decision(
            decision="ABSTAIN", state_id="UNRESOLVED",
            reasons=(f"state_unresolved:{exc}",), total_budget=total_budget,
            applicability={"status": "UNRESOLVED", "hard_gate": "state"},
            causal_support={"status": "UNRESOLVED"},
            risk={"risk_penalty": 1.0, "unresolved_state": True})

    if state.unresolved_conflicts:
        return _empty_decision(
            decision="ABSTAIN", state_id=state.resolution_id,
            reasons=tuple(sorted(f"state_conflict:{item}"
                                 for item in state.unresolved_conflicts)),
            total_budget=total_budget,
            applicability={"status": "UNRESOLVED", "hard_gate": "state"},
            causal_support={"status": "UNRESOLVED"},
            risk={"risk_penalty": 1.0, "unresolved_state": True})
    if plan.get("out_of_distribution") is True or plan.get("ood") is True:
        return _empty_decision(
            decision="ABSTAIN", state_id=state.resolution_id,
            reasons=("out_of_distribution",), total_budget=total_budget,
            applicability={"status": "OOD", "hard_gate": "distribution"},
            causal_support={"status": "NOT_EVALUATED"},
            risk={"risk_penalty": 1.0, "unresolved_state": False})
    if plan.get("memory_interference") is True:
        return _empty_decision(
            decision="ABSTAIN", state_id=state.resolution_id,
            reasons=("memory_interference",), total_budget=total_budget,
            applicability={"status": "ABSTAIN", "hard_gate": "memory_interference"},
            causal_support={"status": "NOT_EVALUATED"},
            risk={"risk_penalty": 1.0, "memory_interference": True})

    claims, vetoes, insufficient, integrity = _knowledge_for_state(
        conn, state=state, scope=scope, context=context)
    app_summary = {
        "status": "APPLICABLE" if claims else "NOT_APPLICABLE",
        "validated_knowledge_count": len(claims),
        "negative_vetoes": sorted(vetoes),
        "insufficient": sorted(insufficient),
        "integrity_errors": sorted(integrity),
    }
    if vetoes:
        return _empty_decision(
            decision="INAPPLICABLE", state_id=state.resolution_id,
            reasons=tuple(sorted(vetoes)), total_budget=total_budget,
            applicability=app_summary,
            causal_support={"status": "VETOED"},
            risk={"risk_penalty": 1.0, "unresolved_state": False})
    if integrity:
        return _empty_decision(
            decision="ABSTAIN", state_id=state.resolution_id,
            reasons=tuple(sorted(integrity)), total_budget=total_budget,
            applicability=app_summary,
            causal_support={"status": "INVALID"},
            risk={"risk_penalty": 1.0, "unresolved_state": False})
    if not claims:
        # A validated claim that is in-scope but lacks positive applicability,
        # authority, or minimum causal support is an evidence shortfall, not a
        # deliberate clean-slate choice.  Keep the distinction observable so
        # calibration can separate ``ABSTAIN`` from ``NO_SKILL``.  Pure
        # family/profile mismatches mean no skill exists for this query.
        scope_only = bool(insufficient) and all(
            item.endswith(":mechanism_family_mismatch") or
            item.endswith(":compatibility_profile_mismatch")
            for item in insufficient)
        if insufficient and not scope_only:
            return _empty_decision(
                decision="ABSTAIN", state_id=state.resolution_id,
                reasons=tuple(sorted(insufficient)), total_budget=total_budget,
                applicability=app_summary,
                causal_support={
                    "status": "INSUFFICIENT",
                    "minimum_evidence_level": MIN_CAUSAL_EVIDENCE,
                },
                risk={"risk_penalty": 1.0, "unresolved_state": False})
        reason = ("no_validated_mechanism_knowledge" if not insufficient else
                  "validated_knowledge_not_eligible")
        return _empty_decision(
            decision="NO_SKILL", state_id=state.resolution_id,
            reasons=(reason,), total_budget=total_budget,
            applicability=app_summary,
            causal_support={"status": "INSUFFICIENT", "minimum_evidence_level": MIN_CAUSAL_EVIDENCE},
            risk={"risk_penalty": 0.0, "unresolved_state": False})

    path_ids = tuple(sorted({path_id for item in claims for path_id in item["path_ids"]}))
    strongest = max((item["claim"].evidence_level for item in claims),
                    key=evidence_rank)
    causal_summary = {
        "status": "SUPPORTED",
        "minimum_evidence_level": MIN_CAUSAL_EVIDENCE,
        "evidence_levels": sorted({item["claim"].evidence_level for item in claims},
                                    key=evidence_rank),
        "strongest_evidence_level": strongest,
        "validated_knowledge_count": len(claims),
        "causal_path_count": len(path_ids),
        "causal_path_ids": list(path_ids),
    }
    assets, promoted_authorized, asset_summary = _asset_candidates(
        conn, state=state, scope=scope, claims=claims)
    risk = _risk(claims=claims, assets=asset_summary, state=state)
    selected_rules = _rule_ids(conn, typed_query, state)
    binding_assets = set(asset_summary["binding_resolvable_assets"])
    if (memory_capacity >= 2 and promoted_authorized and
            set(promoted_authorized) <= binding_assets and path_ids):
        decision = "APPLY"
        allocated_memory = 2
        allocated_no_memory = max(1, total_budget - allocated_memory)
        selected_assets = tuple(promoted_authorized)
        reasons: tuple[str, ...] = ()
    elif memory_capacity >= 1:
        decision = "CONSIDER"
        allocated_memory = 1
        allocated_no_memory = max(1, total_budget - allocated_memory)
        selected_assets = assets[:1]
        reasons = ()
    else:
        decision = "NO_SKILL"
        allocated_memory = 0
        allocated_no_memory = total_budget
        selected_rules = ()
        path_ids = ()
        selected_assets = ()
        reasons = ("memory_budget_zero",)
    return MemoryRoutingDecision(
        decision=decision, resolved_state_id=state.resolution_id,
        selected_rule_ids=tuple(selected_rules), selected_path_ids=path_ids,
        selected_asset_ids=tuple(selected_assets), applicability=app_summary,
        causal_support=causal_summary, risk=risk,
        abstain_reasons=reasons, no_memory_budget=allocated_no_memory,
        memory_budget=allocated_memory)


def retrieve_assets(
    conn: sqlite3.Connection, decision: MemoryRoutingDecision,
) -> list[MemoryCandidate]:
    """Return advisory candidates only; no ``tehm_asset`` source is invented."""
    if not isinstance(decision, MemoryRoutingDecision):
        raise TypeError("retrieve_assets requires MemoryRoutingDecision")
    # Asset candidates are not yet a backend candidate source (Appendix 19.2).
    # Return no executable candidate until the asset authority and candidate
    # source contract are promoted in a later phase.
    return []


__all__ = [
    "MIN_CAUSAL_EVIDENCE", "ROUTER_VERSION", "MemoryRouterError",
    "retrieve_assets", "route_memory", "scope_for_query",
]
