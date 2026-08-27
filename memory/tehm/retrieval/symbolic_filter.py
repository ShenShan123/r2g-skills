"""Stage 2: hard symbolic filtering (design doc 9.4).

``P_h(S_q)`` evaluates the rule's hard preconditions + concrete match pattern
against the current repair state:

    APPLICABLE   all hard predicates pass (or none exist)
    INAPPLICABLE a hard predicate concretely fails
    UNRESOLVED   the state lacks the evidence to decide — NEVER defaulted to pass
                 (honesty H3 / design doc 9.4).

A symbolic veto here is final: the reranker cannot override it (design doc 9.5).
"""
from __future__ import annotations

from contracts import MemoryQuery
from tehm.ids import is_hole

from tehm.retrieval.result import APPLICABLE, INAPPLICABLE, UNRESOLVED


def apply_symbolic_filter(rule: dict, query: MemoryQuery) -> str:
    """Evaluate the rule's hard applicability against the query's state.

    v1: the concrete ``match.target_check`` is the only hard predicate (rules
    crystallize with empty ``hard_preconditions`` until Phase 10 enriches them).
    """
    check = (query.query_plan or {}).get("check")

    rule_profile = _rule_compatibility_profile(rule)
    query_profile = (query.query_plan or {}).get("compatibility_profile")
    if isinstance(rule_profile, str) and not rule_profile.startswith("$H"):
        if not query_profile:
            return UNRESOLVED
        if query_profile != rule_profile:
            return INAPPLICABLE

    hard_preconditions = rule.get("hard_preconditions") or []
    if hard_preconditions:
        # v1: no evaluable hard predicates yet; never silently pass unknowns.
        return UNRESOLVED

    target_check = (rule.get("before_pattern") or {}).get("target_check")
    if isinstance(target_check, str) and not is_hole(target_check):
        if not check:
            return UNRESOLVED            # need the failing check to decide
        if target_check != check:
            return INAPPLICABLE          # hard veto — ranker cannot override
        return APPLICABLE

    # Hole target_check = matches any check; needs at least a check to apply.
    return APPLICABLE if check else UNRESOLVED


def _rule_compatibility_profile(rule: dict):
    """Read the persisted context contract without losing its authority.

    Crystallization keeps the profile in both the match pattern (so it can be
    anti-unified) and ``context_predicates`` (so the context scope remains
    explicit).  The index validates that the two copies agree; this helper
    accepts either shape for older evaluation fixtures while never inventing a
    profile when one is absent.
    """
    before = rule.get("before_pattern") or {}
    profile = before.get("compatibility_profile")
    if profile is None:
        profile = before.get("match.compatibility_profile")
    if profile is None:
        context = rule.get("context_predicates") or {}
        if isinstance(context, dict):
            profile = context.get("compatibility_profile")
    return profile
