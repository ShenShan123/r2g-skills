"""Online, shadow-only memory evolution APIs."""
from .events import (
    EVENT_TYPES, append_memory_event, append_state_shift_observation,
    append_routed_state_shift_observation, load_state_shift_observations,
    verify_event_chain,
)
from .conflict import (
    CONFLICT_RECEIPT_VERSION, CONFLICT_TYPES, ConflictReceipt, detect_conflicts,
)
from .candidate_trial import (
    CandidateTrialError, CandidateTrialReceipt, run_shadow_candidate_trial,
)
from .consolidation import (
    CONSOLIDATION_OPERATIONS, ConsolidationDecisionReceipt,
    decide_consolidation,
)
from .incremental_crystallize import (
    crystallize_affected_groups, preview_affected_groups,
)
from .manager import observe_transition
from .novelty import NOVELTY_RECEIPT_VERSION, NoveltyReceipt, detect_novelty
from .repeated_failure import (
    REPEATED_FAILURE_RECEIPT_VERSION, RepeatedFailureReceipt,
    detect_repeated_failures,
)
from .attribution import (
    FAILURE_TYPES, UPDATE_TARGETS, MemoryFailureAttributionReceipt,
    attribute_failure, failure_attribution_digest,
)
from .retrieval_attribution import (
    RETRIEVAL_ATTRIBUTION_VERSION, RetrievalAttributionError,
    RetrievalAttributionReceipt, attribute_retrieval_failure,
)
from .p12_shadow_trigger import (
    P12_SHADOW_TRIGGER_VERSION, P13_EVOLUTION_REASONS,
    P13_EVOLUTION_REASON_RECEIPT_VERSION, P12ShadowTriggerError,
    P13EvolutionReasonReceipt,
    P12ShadowUpdateTriggerReceipt, build_p12_shadow_update_triggers,
    build_p12_shadow_update_triggers_from_reason_receipt,
)
from .reason_derivation import (
    DERIVATION_MODES, EVOLUTION_REASON_DERIVATION_VERSION,
    EVOLUTION_REASONS, EvolutionReasonDerivationError,
    EvolutionReasonDerivationReceipt, derive_capability_gap_reason,
    derive_conflict_reason, derive_memory_interference_reason,
    derive_novelty_reason, derive_repeated_failure_reason,
    derive_state_shift_reason,
    p13_reason_receipt_from_derivations,
)
from .admission import (
    EVOLUTION_ADMISSION_VERSION, EvolutionAdmissionError,
    EvolutionAdmissionReceipt, admit_evolution_reason,
)
from .capability_gap import (
    CAPABILITY_GAP_PROPOSAL_VERSION, PROPOSAL_KINDS,
    CapabilityGapEvolutionProposal, CapabilityGapProposalError,
    propose_capability_gap_asset, propose_capability_gap_expansion,
    propose_capability_gap_knowledge,
)
from .interference_revision import (
    INTERFERENCE_REVISION_VERSION, MemoryInterferenceRevisionError,
    MemoryInterferenceEvolutionProposal,
    propose_memory_interference_specialization,
    interference_proposal_to_localized_plan,
)
from .local_revision import (
    UPDATE_OPERATIONS, LocalizedUpdatePlan, LocalizedUpdatePlanReceipt,
    plan_localized_update,
)
from .state_shift_revision import (
    STATE_SHIFT_EVOLUTION_VERSION, STATE_SHIFT_EVOLUTION_OPERATIONS,
    STATE_SHIFT_EVOLUTION_REASONS, StateShiftEvolutionError,
    StateShiftEvolutionProposal, plan_repeated_state_shift,
    propose_repeated_state_shift, propose_repeated_state_shift_from_events,
    propose_repeated_state_shift_from_paired_receipts,
    state_shift_proposal_to_localized_plan,
)
from .receipts import (
    ExperienceValueReceipt, IncrementalCrystallizationReceipt,
    MemoryEventReceipt, OnlineMemoryReceipt, RuleRevisionReceipt,
)
from .revision import REVISION_OPERATIONS, record_rule_revision
from .rollback import IsolatedRollbackReceipt, build_isolated_rollback_receipt
from .anti_forgetting import (
    ANTI_FORGETTING_VERSION, RAW_EVIDENCE_TABLES, AntiForgettingWitness,
    RawEvidenceReceipt, raw_evidence_digest, verify_raw_evidence_unchanged,
)
from .triggers import (
    TRIGGER_REASONS, ConsolidationTriggerReceipt,
    evaluate_consolidation_trigger,
)
from .verification import require_verified_execution, require_verified_transition
from .value import (
    EXPERIENCE_VALUE_VERSION, VALUE_SCHEMA_SQL, VALUE_WEIGHTS,
    ensure_experience_value_schema, evaluate_and_record_experience_value,
    evaluate_experience_value, experience_value_digest,
    load_experience_value, record_experience_value,
)

__all__ = [
    "EVENT_TYPES", "REVISION_OPERATIONS", "ExperienceValueReceipt",
    "FAILURE_TYPES", "UPDATE_TARGETS", "UPDATE_OPERATIONS",
    "MemoryFailureAttributionReceipt", "LocalizedUpdatePlan",
    "LocalizedUpdatePlanReceipt",
    "IncrementalCrystallizationReceipt",
    "MemoryEventReceipt", "OnlineMemoryReceipt", "RuleRevisionReceipt",
    "append_memory_event", "append_state_shift_observation",
    "append_routed_state_shift_observation",
    "load_state_shift_observations", "crystallize_affected_groups",
    "preview_affected_groups", "observe_transition",
    "CandidateTrialError", "CandidateTrialReceipt",
    "run_shadow_candidate_trial",
    "CONSOLIDATION_OPERATIONS", "ConsolidationDecisionReceipt",
    "decide_consolidation",
    "record_rule_revision", "verify_event_chain", "CONFLICT_RECEIPT_VERSION",
    "CONFLICT_TYPES",
    "IsolatedRollbackReceipt", "build_isolated_rollback_receipt",
    "RAW_EVIDENCE_TABLES", "RawEvidenceReceipt", "raw_evidence_digest",
    "verify_raw_evidence_unchanged", "ANTI_FORGETTING_VERSION",
    "AntiForgettingWitness",
    "ConflictReceipt", "detect_conflicts", "NOVELTY_RECEIPT_VERSION",
    "NoveltyReceipt", "detect_novelty", "REPEATED_FAILURE_RECEIPT_VERSION",
    "RepeatedFailureReceipt", "detect_repeated_failures",
    "attribute_failure", "failure_attribution_digest", "plan_localized_update",
    "RETRIEVAL_ATTRIBUTION_VERSION", "RetrievalAttributionError",
    "RetrievalAttributionReceipt", "attribute_retrieval_failure",
    "P12_SHADOW_TRIGGER_VERSION", "P13_EVOLUTION_REASONS",
    "P13_EVOLUTION_REASON_RECEIPT_VERSION", "P12ShadowTriggerError",
    "P13EvolutionReasonReceipt",
    "P12ShadowUpdateTriggerReceipt", "build_p12_shadow_update_triggers",
    "build_p12_shadow_update_triggers_from_reason_receipt",
    "EVOLUTION_REASON_DERIVATION_VERSION", "DERIVATION_MODES",
    "EVOLUTION_REASONS", "EvolutionReasonDerivationError",
    "EvolutionReasonDerivationReceipt", "derive_state_shift_reason",
    "derive_capability_gap_reason", "derive_conflict_reason",
    "derive_memory_interference_reason", "derive_novelty_reason",
    "derive_repeated_failure_reason",
    "p13_reason_receipt_from_derivations",
    "EVOLUTION_ADMISSION_VERSION", "EvolutionAdmissionError",
    "EvolutionAdmissionReceipt", "admit_evolution_reason",
    "CAPABILITY_GAP_PROPOSAL_VERSION", "PROPOSAL_KINDS",
    "CapabilityGapEvolutionProposal", "CapabilityGapProposalError",
    "propose_capability_gap_asset", "propose_capability_gap_expansion",
    "propose_capability_gap_knowledge",
    "INTERFERENCE_REVISION_VERSION", "MemoryInterferenceRevisionError",
    "MemoryInterferenceEvolutionProposal",
    "propose_memory_interference_specialization",
    "interference_proposal_to_localized_plan",
    "STATE_SHIFT_EVOLUTION_VERSION", "STATE_SHIFT_EVOLUTION_OPERATIONS",
    "STATE_SHIFT_EVOLUTION_REASONS", "StateShiftEvolutionError",
    "StateShiftEvolutionProposal", "plan_repeated_state_shift",
    "propose_repeated_state_shift", "propose_repeated_state_shift_from_events",
    "propose_repeated_state_shift_from_paired_receipts",
    "state_shift_proposal_to_localized_plan",
    "TRIGGER_REASONS", "ConsolidationTriggerReceipt",
    "evaluate_consolidation_trigger",
    "require_verified_execution", "require_verified_transition",
    "EXPERIENCE_VALUE_VERSION", "VALUE_SCHEMA_SQL", "VALUE_WEIGHTS",
    "ensure_experience_value_schema", "evaluate_experience_value",
    "evaluate_and_record_experience_value", "experience_value_digest",
    "load_experience_value", "record_experience_value",
]

# P13: apply a localized plan only to an isolated SQLite shadow and discard it.
from .apply_update import (
    SHADOW_UPDATE_VERSION, ShadowUpdateError, AppliedShadowUpdateReceipt,
    apply_localized_update_shadow,
)

__all__ += [
    "SHADOW_UPDATE_VERSION", "ShadowUpdateError",
    "AppliedShadowUpdateReceipt", "apply_localized_update_shadow",
]
