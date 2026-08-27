"""Stage 1: high-recall retrieval (design doc 9.3).

The current repair state (check + failure signature) is matched against the rule
index. High recall is deliberately lenient: everything with ANY overlap is
recalled (precision is the symbolic filter's job, Stage 2). A rule with a
concrete ``match.target_check`` that differs from the query still surfaces here
with similarity 0 — the filter vetoes it, never the ranker (9.5).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from contracts import MemoryQuery
from tehm.ids import is_hole

RECALL_VERSION = "recall-v0.1"
# Query-side obligations derived from the repair state (design doc 25 RTL/signoff).
_QUERY_OBLIGATIONS_BY_CHECK = {
    "drc": ("TARGET_FAILURE_REMOVED", "PRESERVE_FROZEN_REGRESSION"),
    "lvs": ("TARGET_FAILURE_REMOVED", "PRESERVE_FROZEN_REGRESSION"),
    "timing": ("TARGET_FAILURE_REMOVED", "PRESERVE_TIMING_TIER"),
    "timing_check": ("TARGET_FAILURE_REMOVED", "PRESERVE_TIMING_TIER"),
    "route": ("TARGET_FAILURE_REMOVED", "PRESERVE_ROUTE_COMPLETION"),
}


@dataclass
class RuleCandidate:
    rule_id: str
    rule: dict
    similarity: float
    matched_keys: list = field(default_factory=list)


def high_recall(index, query: MemoryQuery, *, limit: int) -> list[RuleCandidate]:
    """Recall admissible rules against the query's repair state (lenient)."""
    check = (query.query_plan or {}).get("check")
    query_obligations = _query_obligations(check)
    candidates: list[RuleCandidate] = []
    for rule_id, rule in index.rules.items():
        similarity, keys = _similarity(rule, check, query_obligations)
        candidates.append(RuleCandidate(
            rule_id=rule_id, rule=rule, similarity=similarity, matched_keys=keys))
    candidates.sort(key=lambda c: c.similarity, reverse=True)
    return candidates[:limit]


def _similarity(rule: dict, check: str | None, query_obligations: tuple):
    """Transparent similarity (design doc 9.5): check match + obligation overlap."""
    keys: list[str] = []
    target_check = (rule.get("before_pattern") or {}).get("target_check")
    if isinstance(target_check, str) and not is_hole(target_check):
        check_match = 1.0 if check == target_check else 0.0
        if check_match:
            keys.append("check")
    else:
        check_match = 0.5  # hole: no check constraint, neutral
    rule_obligations = set(rule.get("obligations") or [])
    overlap = rule_obligations & set(query_obligations)
    if overlap:
        keys.append("obligations")
    obligation_score = (len(overlap) / max(1, len(query_obligations))
                        if query_obligations else 0.0)
    return (check_match + obligation_score) / 2, keys


def _query_obligations(check: str | None) -> tuple:
    if not check:
        return ("TARGET_FAILURE_REMOVED",)
    return _QUERY_OBLIGATIONS_BY_CHECK.get(check, ("TARGET_FAILURE_REMOVED",))
