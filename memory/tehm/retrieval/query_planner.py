"""Stage 0: query planning (design doc 9.2).

``RepairContext -> MemoryQuery``. The query plan sets per-view priority; the
repair evidence (check, failure signature, design id) is embedded in the plan so
downstream stages (high-recall + symbolic filter) have the context they need.
"""
from __future__ import annotations

from contracts import MemoryQuery, RepairContext


def plan_query(context: RepairContext) -> MemoryQuery:
    """Plan a typed query from the current repair state (design doc 9.2)."""
    has_diagnostic = bool(context.check) and bool(context.reports)
    query_plan = {
        "diagnostic_view": "high" if has_diagnostic else "medium",
        "semantic_view": "medium",
        "episodic_view": "medium",
        "procedural_view": "high",
        # repair evidence embedded for the recall / filter / rerank stages.
        "check": context.check,
        "design_id": context.design_id,
        "platform": context.platform,
        "symptom_signature": context.symptom_signature,
        "structural_graph": context.structural_graph,
        "compatibility_profile": context.compatibility_profile,
        "mechanism_signature": getattr(context, "mechanism_signature", None),
        "failure_graph_digest": getattr(context, "failure_graph_digest", None),
        "causal_context_digest": getattr(context, "causal_context_digest", None),
        "prior_action_digests": list(getattr(context, "prior_action_digests", []) or []),
    }
    dominant_dimensions = {"temporal": "high", "structural": "high",
                           "width_type": "low"}
    return MemoryQuery(
        query_plan=query_plan,
        dominant_dimensions=dominant_dimensions,
        context_ref=context.design_id,
    )
