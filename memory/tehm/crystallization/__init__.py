"""Crystallization layer (design doc 6, 26 Phase 4-6).

Phase 4:
    effects.py        K_primary effect canonicalization (design doc 6.2)
    preflight.py      crystallizability preflight (design doc 6.3)

Phase 5 (this milestone):
    role_normalize.py   role-normalized rewrite projection (6.4)
    anti_unify.py       joint rewrite anti-unification + merge trace (6.6, 23.2)
    synthesize_skill.py candidate rule synthesis (22.4, 4.3)
    build_rules.py      full pipeline: preflight -> anti-unify -> persist (20.5)

Phase 6 (this milestone):
    validity.py       ordered rule validity audit V2->V1->V3->V4 (7, 24.1)
    risk.py           risk stratification: created regression / newly observed (8)
"""
from tehm.crystallization.effects import (
    CANON_VERSION,
    PrimaryEffect,
    canonicalize_effect_fields,
    effect_key,
    effect_key_from_transition_dict,
)
from tehm.crystallization.preflight import PreflightReport, run_preflight
from tehm.crystallization.role_normalize import (
    ROLE_NORMALIZE_VERSION,
    RoleNormalizedRewrite,
    normalize_rewrite,
)
from tehm.crystallization.anti_unify import (
    ALGORITHM_VERSION,
    AntiUnifyConfig,
    AntiUnifyResult,
    MergeStep,
    anti_unify_rewrites,
    result_digest,
)
from tehm.crystallization.synthesize_skill import (
    RULE_STATUS_CANDIDATE,
    SYNTHESIZER_VERSION,
    rule_sources,
    synthesize_skill,
)
from tehm.crystallization.build_rules import crystallize_all
from tehm.crystallization.validity import (
    ADMISSIBLE_FOR_LIFECYCLE,
    VALIDITY_AUDIT_VERSION,
    VALIDITY_STATUSES,
    GateResult,
    ValidityConfig,
    ValidityResult,
    audit_rule,
)
from tehm.crystallization.risk import RISK_KINDS, RISK_VERSION, stratify_rule_risk

__all__ = [
    # effects (Phase 4)
    "CANON_VERSION", "PrimaryEffect", "canonicalize_effect_fields",
    "effect_key", "effect_key_from_transition_dict",
    # preflight (Phase 4)
    "PreflightReport", "run_preflight",
    # role normalization + anti-unification (Phase 5)
    "ROLE_NORMALIZE_VERSION", "RoleNormalizedRewrite", "normalize_rewrite",
    "ALGORITHM_VERSION", "AntiUnifyConfig", "AntiUnifyResult", "MergeStep",
    "anti_unify_rewrites", "result_digest",
    # skill synthesis (Phase 5)
    "RULE_STATUS_CANDIDATE", "SYNTHESIZER_VERSION", "rule_sources",
    "synthesize_skill",
    # pipeline (Phase 5)
    "crystallize_all",
    # validity (Phase 6)
    "ADMISSIBLE_FOR_LIFECYCLE", "VALIDITY_AUDIT_VERSION", "VALIDITY_STATUSES",
    "GateResult", "ValidityConfig", "ValidityResult", "audit_rule",
    # risk (Phase 6)
    "RISK_KINDS", "RISK_VERSION", "stratify_rule_risk",
]
