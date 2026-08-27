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
from tehm.retrieval.causal_recall import CausalPathMatch, retrieve_causal_paths

__all__ = [
    "RetrievalReceipt", "RetrievedRule",
    "plan_query", "retrieve_query", "RuleIndex", "build_index", "high_recall",
    "apply_symbolic_filter", "rerank", "retrieve",
    "CausalPathMatch", "retrieve_causal_paths",
]
