"""Step 2: applicability check (design doc 10, 9.4).

``P_h^r(S_q)`` — evaluates the rule's hard preconditions against the current
repair state. Same semantics as the retrieval symbolic filter (UNKNOWN never
defaults to pass); this is the activation-time authority on applicability.
"""
from __future__ import annotations

from contracts import RepairContext
from tehm.retrieval.query_planner import plan_query
from tehm.retrieval.symbolic_filter import apply_symbolic_filter

APPLICABILITY_VERSION = "applicability-v0.1"


def check_applicability(rule: dict, context: RepairContext) -> str:
    """Step 2 result: APPLICABLE | INAPPLICABLE | UNRESOLVED."""
    return apply_symbolic_filter(rule, plan_query(context))
