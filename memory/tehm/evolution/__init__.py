"""Online, shadow-only memory evolution APIs."""
from .events import EVENT_TYPES, append_memory_event, verify_event_chain
from .conflict import CONFLICT_TYPES, ConflictReceipt, detect_conflicts
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
from .novelty import detect_novelty
from .attribution import (
    FAILURE_TYPES, UPDATE_TARGETS, MemoryFailureAttributionReceipt,
    attribute_failure, failure_attribution_digest,
)
from .local_revision import (
    UPDATE_OPERATIONS, LocalizedUpdatePlan, LocalizedUpdatePlanReceipt,
    plan_localized_update,
)
from .receipts import (
    ExperienceValueReceipt, IncrementalCrystallizationReceipt,
    MemoryEventReceipt, OnlineMemoryReceipt, RuleRevisionReceipt,
)
from .revision import REVISION_OPERATIONS, record_rule_revision
from .rollback import IsolatedRollbackReceipt, build_isolated_rollback_receipt
from .anti_forgetting import (
    RAW_EVIDENCE_TABLES, RawEvidenceReceipt, raw_evidence_digest,
    verify_raw_evidence_unchanged,
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
    "append_memory_event", "crystallize_affected_groups",
    "preview_affected_groups", "observe_transition",
    "CandidateTrialError", "CandidateTrialReceipt",
    "run_shadow_candidate_trial",
    "CONSOLIDATION_OPERATIONS", "ConsolidationDecisionReceipt",
    "decide_consolidation",
    "record_rule_revision", "verify_event_chain", "CONFLICT_TYPES",
    "IsolatedRollbackReceipt", "build_isolated_rollback_receipt",
    "RAW_EVIDENCE_TABLES", "RawEvidenceReceipt", "raw_evidence_digest",
    "verify_raw_evidence_unchanged",
    "ConflictReceipt", "detect_conflicts", "detect_novelty",
    "attribute_failure", "failure_attribution_digest", "plan_localized_update",
    "TRIGGER_REASONS", "ConsolidationTriggerReceipt",
    "evaluate_consolidation_trigger",
    "require_verified_execution", "require_verified_transition",
    "EXPERIENCE_VALUE_VERSION", "VALUE_SCHEMA_SQL", "VALUE_WEIGHTS",
    "ensure_experience_value_schema", "evaluate_experience_value",
    "evaluate_and_record_experience_value", "experience_value_digest",
    "load_experience_value", "record_experience_value",
]
