"""Current knowledge-state resolver built on the P1 state resolver."""
from __future__ import annotations

import sqlite3
from collections.abc import Mapping

from tehm.state import StateResolutionError, resolve_current_state

from .applicability import evaluate_applicability
from .registry import get_knowledge_by_object_id
from .receipts import KnowledgeResolutionReceipt
from .schema import ensure_knowledge_schema


def resolve_knowledge(
    conn: sqlite3.Connection, scope: Mapping | None = None, *,
    mode: str = "shadow", persist: bool = True,
) -> KnowledgeResolutionReceipt:
    """Resolve applicable shadow/candidate knowledge; production always abstains."""
    if mode not in {"shadow", "production"}:
        raise ValueError("knowledge resolver mode must be 'shadow' or 'production'")
    if mode == "production":
        raise StateResolutionError(
            "UNRESOLVED_AUTHORITY: mechanism knowledge has no promoted runtime status")
    ensure_knowledge_schema(conn, commit=False)
    requested = dict(scope or {})
    state = resolve_current_state(conn, requested, mode="shadow", persist=persist)
    active: list[str] = []
    suppressed: list[str] = []
    unresolved = list(state.unresolved_conflicts)
    for object_id in state.active_knowledge_claims:
        target_scope = str(requested.get("target_scope") or "global")
        try:
            claim = get_knowledge_by_object_id(
                conn, object_id, target_scope=target_scope)
        except (TypeError, ValueError) as exc:
            # P1 state resolution intentionally exposes both global and
            # scope-local status rows.  A global claim remains applicable in
            # a narrower requested scope; retry that immutable status lane
            # before declaring the claim corrupt.
            if target_scope == "global":
                unresolved.append(f"KNOWLEDGE_INVALID:{object_id}:{exc}")
                continue
            try:
                claim = get_knowledge_by_object_id(
                    conn, object_id, target_scope="global")
            except (TypeError, ValueError) as fallback_exc:
                unresolved.append(f"KNOWLEDGE_INVALID:{object_id}:{fallback_exc}")
                continue
        applicability = evaluate_applicability(claim, requested)
        if applicability.eligible:
            active.append(object_id)
        else:
            suppressed.append(f"{object_id}:{applicability.reason}")
    return KnowledgeResolutionReceipt(
        resolution_id="knowledge_" + state.resolution_id,
        scope=requested, active_knowledge=tuple(sorted(active)),
        suppressed=tuple(sorted(suppressed)),
        unresolved_conflicts=tuple(sorted(set(unresolved))), mode=mode)


__all__ = ["resolve_knowledge"]
