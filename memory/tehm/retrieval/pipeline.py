"""Retrieval pipeline orchestrator (design doc 9, 21.3 step 1).

    RepairContext -> plan_query -> high_recall -> symbolic filter -> rerank
                                              -> RetrievalReceipt
"""
from __future__ import annotations

import sqlite3
import time

from contracts import MemoryQuery, RepairContext
from tehm.retrieval.index import build_index
from tehm.retrieval.query_planner import plan_query
from tehm.retrieval.recall import high_recall
from tehm.retrieval.rerank import rerank
from tehm.retrieval.result import APPLICABLE, INAPPLICABLE, RetrievalReceipt
from tehm.retrieval.symbolic_filter import apply_symbolic_filter

PIPELINE_VERSION = "retrieval-pipeline-v0.1"
_RECALL_MULTIPLIER = 3


def retrieve(conn: sqlite3.Connection, context: RepairContext, *,
             limit: int = 10,
             lifecycle_statuses: frozenset[str] = frozenset({"promoted"})) -> RetrievalReceipt:
    """Retrieve admissible rules for the current repair state (design doc 9)."""
    return retrieve_query(
        conn, plan_query(context), limit=limit,
        lifecycle_statuses=lifecycle_statuses)


def retrieve_query(conn: sqlite3.Connection, query: MemoryQuery, *,
                   limit: int = 10,
                   lifecycle_statuses: frozenset[str] = frozenset({"promoted"})) -> RetrievalReceipt:
    """Retrieve directly from a frozen :class:`MemoryQuery`.

    Backend seams must not reconstruct a ``RepairContext`` and call
    ``plan_query`` a second time: doing so silently drops fields such as
    ``structural_graph`` and ``compatibility_profile``.  This entry point is
    also the stable hook for future causal retrieval stages.
    """
    allowed = frozenset(lifecycle_statuses)
    if not allowed or not allowed <= {"candidate", "promoted"}:
        raise ValueError("retrieval accepts only candidate/promoted lifecycle statuses")
    started = time.perf_counter()
    # Runtime authority is narrower than validity authority: shadow/candidate
    # rules may be audited or trialed, but only promoted rules may be recalled
    # into a production repair decision (design doc 20.8 / 24.3).
    # Production callers keep the default promoted-only authority.  Evaluation
    # harnesses may explicitly inspect candidate rules in an isolated copy;
    # this opt-in does not change the lifecycle row or canonical memory.
    index = build_index(conn, lifecycle_statuses=allowed)
    candidates = high_recall(index, query, limit=limit * _RECALL_MULTIPLIER)

    applicable = inapplicable = unresolved = 0
    filtered = []
    for candidate in candidates:
        status = apply_symbolic_filter(candidate.rule, query)
        if status == APPLICABLE:
            applicable += 1
        elif status == INAPPLICABLE:
            inapplicable += 1
        else:
            unresolved += 1
        filtered.append((candidate.rule, candidate.similarity, status))

    results = rerank(filtered, limit=limit)
    latency_ms = round((time.perf_counter() - started) * 1000, 3)
    return RetrievalReceipt(
        query_plan=query.query_plan,
        candidates_retrieved=len(candidates),
        applicable=applicable,
        inapplicable=inapplicable,
        unresolved=unresolved,
        results=results,
        latency_ms=latency_ms,
    )
