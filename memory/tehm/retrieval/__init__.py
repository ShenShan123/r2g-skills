"""Typed retrieval (design doc 9, 26 Phase 7).

Retrieval object is the CURRENT REPAIR STATE -> similar rules / sub-trajectories,
never a text query -> text chunk (design doc 9.1). Four stages:

    Stage 0  query planning      (query_planner.py)   RepairContext -> MemoryQuery
    Stage 1  high-recall         (index.py, recall.py) fingerprint / rule index
    Stage 2  hard symbolic filter (symbolic_filter.py) P_h(S_q) -> APPLICABLE | INAPPLICABLE | UNRESOLVED
    Stage 3  transparent rerank  (rerank.py)          similarity * utility * confidence * risk

Symbolic veto is NEVER overridden by the ranker (design doc 9.5). Only rules
meeting minimum validity are retrievable (honesty H6).
"""
from tehm.retrieval.result import RetrievalReceipt, RetrievedRule
from tehm.retrieval.query_planner import plan_query
from tehm.retrieval.pipeline import retrieve_query
from tehm.retrieval.index import RuleIndex, build_index
from tehm.retrieval.recall import high_recall
from tehm.retrieval.symbolic_filter import apply_symbolic_filter
from tehm.retrieval.rerank import rerank
from tehm.retrieval.pipeline import retrieve
from tehm.retrieval.causal_recall import (
    CausalPathMatch, CausalPathQuality, retrieve_causal_paths,
    score_causal_path,
)

# ``memory_router`` depends on the state resolver.  The state resolver is also
# imported by capability/lifecycle modules that import this package, so eager
# importing the shadow router here creates a package-initialisation cycle.
# Keep the public names available through a small lazy export instead.
_MEMORY_ROUTER_EXPORTS = frozenset({
    "MIN_CAUSAL_EVIDENCE", "ROUTER_VERSION", "MemoryRouterError",
    "retrieve_assets", "route_memory", "scope_for_query",
})
_CANDIDATE_POOL_EXPORTS = frozenset({
    "CANDIDATE_POOL_ARMS", "CANDIDATE_POOL_VERSION",
    "MAX_MEMORY_ADVISOR_CANDIDATES", "POOL_OUTCOMES", "CandidatePoolError",
    "CandidatePoolReceipt", "CandidatePool", "build_candidate_pool",
    "CandidatePoolOutcome", "CandidatePoolMetrics", "summarize_candidate_pool",
})
_ASSET_SELECTOR_EXPORTS = frozenset({
    "ASSET_SELECTION_DECISIONS", "ASSET_SELECTOR_VERSION",
    "MAX_KNOWLEDGE_GROUNDED_ASSETS", "AssetSelection", "AssetSelectionReceipt",
    "AssetSelectorError", "select_asset", "select_assets",
    "select_knowledge_grounded_assets",
})


def __getattr__(name):
    if name in _MEMORY_ROUTER_EXPORTS:
        from tehm.retrieval import memory_router
        return getattr(memory_router, name)
    if name in _CANDIDATE_POOL_EXPORTS:
        from tehm.retrieval import candidate_pool
        return getattr(candidate_pool, name)
    if name in _ASSET_SELECTOR_EXPORTS:
        from tehm.retrieval import asset_selector
        return getattr(asset_selector, name)
    raise AttributeError(name)

__all__ = [
    "RetrievalReceipt", "RetrievedRule",
    "plan_query", "retrieve_query", "RuleIndex", "build_index", "high_recall",
    "apply_symbolic_filter", "rerank", "retrieve",
    "CausalPathMatch", "CausalPathQuality", "score_causal_path",
    "retrieve_causal_paths", "MIN_CAUSAL_EVIDENCE", "ROUTER_VERSION",
    "MemoryRouterError", "retrieve_assets", "route_memory", "scope_for_query",
    "CANDIDATE_POOL_ARMS", "CANDIDATE_POOL_VERSION",
    "MAX_MEMORY_ADVISOR_CANDIDATES", "POOL_OUTCOMES", "CandidatePoolError",
    "CandidatePoolReceipt", "CandidatePool", "build_candidate_pool",
    "CandidatePoolOutcome", "CandidatePoolMetrics", "summarize_candidate_pool",
    "ASSET_SELECTION_DECISIONS", "ASSET_SELECTOR_VERSION",
    "MAX_KNOWLEDGE_GROUNDED_ASSETS", "AssetSelection", "AssetSelectionReceipt",
    "AssetSelectorError", "select_asset", "select_assets",
    "select_knowledge_grounded_assets",
]
