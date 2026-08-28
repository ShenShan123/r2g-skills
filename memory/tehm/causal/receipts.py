"""Serializable receipts returned by causal shadow operations."""
from __future__ import annotations

from dataclasses import dataclass, field

from tehm.dataset import require_learner_bool


@dataclass(frozen=True)
class CausalFragment:
    transition_id: str
    mechanism_family: str
    compatibility_profile: str | None
    evidence_level: str
    learner_eligible: bool
    campaign_id: str | None
    lineage_id: str | None
    failure_graph_digest: str | None
    nodes: tuple = field(default_factory=tuple)
    edges: tuple = field(default_factory=tuple)

    def __post_init__(self) -> None:
        # A fragment is a typed shadow receipt.  Do not let a caller-provided
        # string/int flag be normalized later into learner authority.
        require_learner_bool(self.learner_eligible,
                             field="causal fragment learner_eligible")

    @property
    def node_ids(self) -> tuple[str, ...]:
        return tuple(node.causal_node_id for node in self.nodes)

    @property
    def edge_ids(self) -> tuple[str, ...]:
        return tuple(edge.causal_edge_id for edge in self.edges)

    def to_dict(self) -> dict:
        return {
            "transition_id": self.transition_id,
            "mechanism_family": self.mechanism_family,
            "compatibility_profile": self.compatibility_profile,
            "evidence_level": self.evidence_level,
            "learner_eligible": self.learner_eligible,
            "campaign_id": self.campaign_id,
            "lineage_id": self.lineage_id,
            "failure_graph_digest": self.failure_graph_digest,
            "node_ids": list(self.node_ids),
            "edge_ids": list(self.edge_ids),
        }


@dataclass(frozen=True)
class CausalPathCandidate:
    path_id: str
    path_digest: str
    mechanism_family: str
    compatibility_profile: str | None
    evidence_level: str
    source_transition_ids: tuple[str, ...]
    node_ids: tuple[str, ...]
    edge_ids: tuple[str, ...]
    support: dict = field(default_factory=dict)
    status: str = "shadow"

    def to_dict(self) -> dict:
        return {
            "path_id": self.path_id,
            "path_digest": self.path_digest,
            "mechanism_family": self.mechanism_family,
            "compatibility_profile": self.compatibility_profile,
            "evidence_level": self.evidence_level,
            "source_transition_ids": list(self.source_transition_ids),
            "node_ids": list(self.node_ids),
            "edge_ids": list(self.edge_ids),
            "support": self.support,
            "status": self.status,
        }


@dataclass(frozen=True)
class InterventionReceipt:
    pair_id: str
    control_transition_id: str
    treatment_transition_id: str
    target_scope: str
    matched_context_digest: str | None
    changed_action_digest: str
    validity_status: str
    evidence_level: str
    lineage_id: str | None
    outcome_delta: dict = field(default_factory=dict)
    oracle_equivalence: dict = field(default_factory=dict)
    causal_edge_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "pair_id": self.pair_id,
            "control_transition_id": self.control_transition_id,
            "treatment_transition_id": self.treatment_transition_id,
            "target_scope": self.target_scope,
            "matched_context_digest": self.matched_context_digest,
            "changed_action_digest": self.changed_action_digest,
            "validity_status": self.validity_status,
            "evidence_level": self.evidence_level,
            "lineage_id": self.lineage_id,
            "outcome_delta": self.outcome_delta,
            "oracle_equivalence": self.oracle_equivalence,
            "causal_edge_id": self.causal_edge_id,
        }


__all__ = ["CausalFragment", "CausalPathCandidate", "InterventionReceipt"]
