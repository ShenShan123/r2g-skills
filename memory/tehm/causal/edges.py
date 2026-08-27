"""Typed, evidence-bearing causal shadow edges."""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, field

from tehm import db as tehm_db
from tehm.ids import stable_dumps

from .evidence_level import validate_evidence_level
from .schema import validate_relation


def _digest(payload: object) -> str:
    return hashlib.sha1(stable_dumps(payload).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class CausalEdge:
    source_node_id: str
    relation_type: str
    target_node_id: str
    evidence_level: str
    support: dict = field(default_factory=dict)
    confidence: dict = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()
    campaign_id: str | None = None
    learner_eligible: bool = False

    def __post_init__(self) -> None:
        if not self.source_node_id or not self.target_node_id:
            raise ValueError("causal edge endpoints are required")
        validate_relation(self.relation_type)
        validate_evidence_level(self.evidence_level)
        if not self.evidence_refs:
            raise ValueError("causal edge needs canonical evidence_refs")

    @property
    def causal_edge_id(self) -> str:
        return "causal_edge_" + _digest({
            "source": self.source_node_id,
            "relation": self.relation_type,
            "target": self.target_node_id,
            "evidence_level": self.evidence_level,
            "support": self.support,
            "confidence": self.confidence,
            "evidence_refs": list(self.evidence_refs),
            "campaign_id": self.campaign_id,
            "learner_eligible": bool(self.learner_eligible),
        })

    def to_row(self, *, created_at: str | None = None) -> dict:
        return {
            "causal_edge_id": self.causal_edge_id,
            "source_node_id": self.source_node_id,
            "relation_type": self.relation_type,
            "target_node_id": self.target_node_id,
            "evidence_level": self.evidence_level,
            "support_json": stable_dumps(self.support),
            "confidence_json": stable_dumps(self.confidence),
            "evidence_refs_json": stable_dumps(list(self.evidence_refs)),
            "campaign_id": self.campaign_id,
            "learner_eligible": int(bool(self.learner_eligible)),
            "created_at": created_at or tehm_db.now_local(),
        }


def persist_edge(conn: sqlite3.Connection, edge: CausalEdge,
                 *, created_at: str | None = None) -> str:
    """Insert an immutable edge or accept an exact replay."""
    expected = edge.to_row(created_at=created_at)
    existing = conn.execute(
        "SELECT source_node_id, relation_type, target_node_id, evidence_level, "
        "support_json, confidence_json, evidence_refs_json, campaign_id, "
        "learner_eligible FROM tehm_causal_edges WHERE causal_edge_id=?",
        (expected["causal_edge_id"],)).fetchone()
    if existing is not None:
        fields = ("source_node_id", "relation_type", "target_node_id",
                  "evidence_level", "support_json", "confidence_json",
                  "evidence_refs_json", "campaign_id", "learner_eligible")
        if any(existing[field] != expected[field] for field in fields):
            raise ValueError(
                f"causal edge replay conflicts with immutable edge "
                f"{expected['causal_edge_id']}")
        return expected["causal_edge_id"]
    conn.execute(
        """INSERT INTO tehm_causal_edges
           (causal_edge_id, source_node_id, relation_type, target_node_id,
            evidence_level, support_json, confidence_json, evidence_refs_json,
            campaign_id, learner_eligible, created_at)
           VALUES (:causal_edge_id, :source_node_id, :relation_type,
                   :target_node_id, :evidence_level, :support_json,
                   :confidence_json, :evidence_refs_json, :campaign_id,
                   :learner_eligible, :created_at)""",
        expected)
    return expected["causal_edge_id"]


__all__ = ["CausalEdge", "persist_edge"]
