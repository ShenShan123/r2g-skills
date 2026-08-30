"""Causal shadow memory (A1/A2).

The package is intentionally opt-in.  Nothing here is called by production
retrieval or lifecycle promotion; callers explicitly create an auditable
fragment/path in the evaluation lane.
"""
from .edges import CausalEdge, persist_edge
from .evidence_level import (
    CausalEvidenceLevel, EVIDENCE_LEVELS, at_least, evidence_rank,
    transition_evidence_level, validate_evidence_level,
)
from .intervention import build_intervention_pair
from .mechanism import TransitionFacts, action_digest, load_transition_facts, mechanism_signature
from .matcher import MechanismMatch, match_causal_path
from .nodes import CausalNode, persist_node
from .path_builder import build_transition_causal_fragment, consolidate_causal_path
from .receipts import CausalFragment, CausalPathCandidate, InterventionReceipt
from .rtl import RtlCausalReceipt, capture_rtl_causal_fragment
from .replication import ReplicationReceipt, evaluate_replicated_effect
from .transfer import (
    TransferReceipt, evaluate_transfer_supported_mechanism,
    full_oracle_complete,
)
from .transfer_ledger import (
    CausalTransferLedgerReceipt, ensure_transfer_ledger_schema,
    load_causal_transfer_receipt, record_causal_transfer,
    verify_causal_transfer,
)
from .orfs import (
    ORFS_CAUSAL_SHADOW_VERSION, build_orfs_causal_shadow,
    build_orfs_controlled_replication,
)
from .authority import CausalRuleEvidenceReceipt, evaluate_causal_rule_evidence
from .witness import (
    learner_edge_transition_coverage, parse_evidence_refs,
    parse_source_transition_ids,
)

__all__ = [
    "CausalEdge", "CausalEvidenceLevel", "CausalFragment", "CausalNode",
    "CausalPathCandidate", "EVIDENCE_LEVELS", "InterventionReceipt",
    "RtlCausalReceipt", "ReplicationReceipt",
    "TransitionFacts", "action_digest", "at_least",
    "build_intervention_pair", "build_transition_causal_fragment",
    "capture_rtl_causal_fragment",
    "consolidate_causal_path", "evidence_rank", "load_transition_facts",
    "mechanism_signature", "persist_edge", "persist_node",
    "MechanismMatch", "match_causal_path",
    "evaluate_replicated_effect",
    "TransferReceipt", "evaluate_transfer_supported_mechanism",
    "full_oracle_complete",
    "CausalTransferLedgerReceipt", "ensure_transfer_ledger_schema",
    "load_causal_transfer_receipt", "record_causal_transfer",
    "verify_causal_transfer",
    "ORFS_CAUSAL_SHADOW_VERSION", "build_orfs_causal_shadow",
    "build_orfs_controlled_replication",
    "CausalRuleEvidenceReceipt", "evaluate_causal_rule_evidence",
    "learner_edge_transition_coverage", "parse_evidence_refs",
    "parse_source_transition_ids",
    "transition_evidence_level", "validate_evidence_level",
]
