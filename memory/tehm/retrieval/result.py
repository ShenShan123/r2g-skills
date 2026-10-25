"""Retrieval results + receipts (design doc 9.x, 19.5).

``RetrievalReceipt`` is the audit record of one retrieval: how many candidates
were recalled, how the symbolic filter decided each, and the final reranked
ordering. Activation (Phase 8) persists this as ``retrieval_receipt_json``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

APPLICABLE = "APPLICABLE"
INAPPLICABLE = "INAPPLICABLE"
UNRESOLVED = "UNRESOLVED"


@dataclass
class RetrievedRule:
    """One reranked retrieval hit (a tehm rule)."""

    rule_id: str
    candidate_id: str
    transformation_family: str
    similarity: float
    applicability_status: str
    utility: dict
    confidence: dict
    risk_penalty: float
    score: float
    source_episodes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "candidate_id": self.candidate_id,
            "transformation_family": self.transformation_family,
            "similarity": self.similarity,
            "applicability_status": self.applicability_status,
            "utility": self.utility,
            "confidence": self.confidence,
            "risk_penalty": self.risk_penalty,
            "score": self.score,
            "source_episodes": list(self.source_episodes),
        }


@dataclass
class RetrievalReceipt:
    """The audit record of one retrieval call."""

    query_plan: dict = field(default_factory=dict)
    candidates_retrieved: int = 0
    applicable: int = 0
    inapplicable: int = 0
    unresolved: int = 0
    results: list = field(default_factory=list)   # RetrievedRule, ranked
    latency_ms: float | None = None

    def to_dict(self) -> dict:
        return {
            "query_plan": self.query_plan,
            "candidates_retrieved": self.candidates_retrieved,
            "applicable": self.applicable,
            "inapplicable": self.inapplicable,
            "unresolved": self.unresolved,
            "results": [r.to_dict() for r in self.results],
            "latency_ms": self.latency_ms,
        }
