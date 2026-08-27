"""Independent rule lifecycle + A/B (design doc 20.10, 24.3, 26 Phase 9).

Rule validity (Phase 6, crystallization-time) is STRICTLY SEPARATED from the
runtime lifecycle (this package). Only rules meeting minimum validity
(PROVISIONAL_VALID / VALIDATED) may enter shadow (honesty H6).

    PROVISIONAL_VALID / VALIDATED
        -> shadow -> candidate -> A/B trial -> promoted / demoted / quarantined

Lifecycle rows live in ``tehm_rule_status``; trial verdicts in ``tehm_trials`` —
NEVER the legacy ``recipe_status`` / ``ab_trials`` (design doc 20.10).
"""
from tehm.lifecycle.rule_status import (
    LIFECYCLE_STATUSES,
    RuleLifecycleError,
    enter_shadow,
    get_status,
    set_status,
)
from tehm.lifecycle.trial_adapter import (
    TrialSubject,
    TEHMRuleTrialSubject,
    judge_trial,
    lcb,
    run_trial,
)
from tehm.lifecycle.authority import (
    apply_production_trial_verdict, apply_trial_verdict)
from tehm.lifecycle.promotion_gates import (
    evaluate_capability_promotion_gates, evaluate_promotion_gates)
from tehm.lifecycle.rule_authority import (
    RuleAuthorityReceipt, promote_rule, record_rule_authority,
    rule_content_digest, verify_rule_authority)
from tehm.lifecycle.rtl_trial import run_rtl_external_trial

__all__ = [
    "LIFECYCLE_STATUSES", "RuleLifecycleError", "enter_shadow", "get_status",
    "set_status",
    "TrialSubject", "TEHMRuleTrialSubject", "judge_trial", "lcb", "run_trial",
    "apply_trial_verdict", "apply_production_trial_verdict",
    "evaluate_promotion_gates", "evaluate_capability_promotion_gates",
    "RuleAuthorityReceipt", "record_rule_authority", "verify_rule_authority",
    "rule_content_digest", "promote_rule",
    "run_rtl_external_trial",
]
