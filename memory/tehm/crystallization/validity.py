"""Rule Validity audit (design doc 7, 24.1, 26 Phase 6).

Ordered state machine — V2 -> V1 -> V3 -> V4:

    Candidate Rule -> V2 Non-Triviality  -> fail: REJECT_DEGENERATE
                  -> V1 Faithful Replay  -> fail: REJECT_UNFAITHFUL
                  -> V3 Effective Support-> insufficient: INSTANCE_MEMORY
                  -> V4 Stability        -> n < 3: PROVISIONAL_VALID
                                         -> stable: VALIDATED
                                         -> unstable: UNSTABLE_CANDIDATE

Two-time-scale separation (design doc 7, C3):
    Rule Validity  (this module, crystallization-time)
        perp
    Applicability / Executability / Verifiability  (activation-time, Phase 8)

V1 uses ONLY the anti-unification witnesses (design doc 7.3): it never re-searches
for a favorable binding. V2 runs strictly before V1 is consulted (honesty H5).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from tehm.crystallization.anti_unify import AntiUnifyConfig, anti_unify_rewrites, is_hole
from tehm.crystallization.role_normalize import normalize_rewrite

VALIDITY_AUDIT_VERSION = "validity-v0.1"

VALIDITY_STATUSES = (
    "CANDIDATE", "REJECT_DEGENERATE", "REJECT_UNFAITHFUL", "INSTANCE_MEMORY",
    "PROVISIONAL_VALID", "UNSTABLE_CANDIDATE", "VALIDATED",
)
# Design doc 24.3: only rules meeting minimum validity may enter runtime lifecycle.
ADMISSIBLE_FOR_LIFECYCLE = ("PROVISIONAL_VALID", "VALIDATED")


@dataclass(frozen=True)
class ValidityConfig:
    min_group_size: int = 2
    max_hole_ratio: float = 0.8                 # above -> wildcard collapse (7.2)
    min_sources_for_concrete_repeat: int = 3    # hole_ratio==0 with <n -> memorization
    min_lineages_for_cross: int = 2
    v4_min_support: int = 3                     # n < 3 -> V4 = N/A (not FAIL)


@dataclass
class GateResult:
    name: str
    ok: bool | None          # None means N/A (design doc 7.5)
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass
class ValidityResult:
    status: str
    gates: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "version": VALIDITY_AUDIT_VERSION,
            "gates": [g.to_dict() for g in self.gates],
        }


def audit_rule(rule: dict, *, source_transitions: list[dict],
               config: ValidityConfig | None = None) -> ValidityResult:
    """Run the ordered validity audit over one crystallized rule.

    ``source_transitions``: the DB-row transition dicts the rule crystallized
    from, each carrying ``lineage_id`` (attached by the caller).
    """
    config = config or ValidityConfig()
    gates: list[GateResult] = []

    # V2 must run before V1 is ever consulted (honesty H5).
    v2 = _v2_non_triviality(rule, config)
    gates.append(v2)
    if not v2.ok:
        return ValidityResult("REJECT_DEGENERATE", gates)

    v1 = _v1_faithful_replay(rule, source_transitions)
    gates.append(v1)
    if not v1.ok:
        return ValidityResult("REJECT_UNFAITHFUL", gates)

    v3 = _v3_effective_support(rule, source_transitions, config)
    gates.append(v3)
    if not v3.ok:
        return ValidityResult("INSTANCE_MEMORY", gates)

    v4 = _v4_stability(rule, source_transitions, config)
    gates.append(v4)
    if v4.ok is None:
        return ValidityResult("PROVISIONAL_VALID", gates)   # V4 = N/A (7.5)
    if v4.ok:
        return ValidityResult("VALIDATED", gates)
    return ValidityResult("UNSTABLE_CANDIDATE", gates)


# -- V2 -----------------------------------------------------------------------

def _v2_non_triviality(rule: dict, config: ValidityConfig) -> GateResult:
    """Exclude instance memorization (under-abstraction) and wildcard collapse
    (over-abstraction); the rule must sit in the valid abstraction band (7.2)."""
    metrics = rule.get("abstraction_metrics") or {}
    hole_ratio = float(metrics.get("hole_ratio", 0.0))
    n_sources = int(metrics.get("num_sources", 0))
    flags: list[str] = []
    if hole_ratio > config.max_hole_ratio:
        flags.append("wildcard_collapse")
    if hole_ratio <= 0.0:
        if n_sources < config.min_sources_for_concrete_repeat:
            flags.append("instance_memorization")
        else:
            flags.append("concrete_repeat")  # strong repeat, not parametrized
    ok = "wildcard_collapse" not in flags and "instance_memorization" not in flags
    return GateResult("V2", ok, {
        "hole_ratio": hole_ratio,
        "num_sources": n_sources,
        "structural_retention": metrics.get("structural_retention"),
        "flags": flags,
    })


# -- V1 -----------------------------------------------------------------------

def _v1_faithful_replay(rule: dict, source_transitions: list[dict]) -> GateResult:
    """Derivation-faithful replay: instantiate the rule with EACH source's
    crystallization-time witness and require it to reproduce that source's
    rewrite EXACTLY. No re-binding search (design doc 7.3, H4)."""
    subs = rule["provenance"]["source_substitutions"]
    rule_slots = _rule_slots(rule)
    failures: list[str] = []
    checked = 0
    for t in source_transitions:
        tid = t.get("transition_id")
        witnesses = subs.get(tid, {})
        source_slots = normalize_rewrite(t).slot_dict()
        checked += 1
        for path, pattern in rule_slots.items():
            instantiated = (witnesses.get(pattern, pattern)
                            if isinstance(pattern, str) and is_hole(pattern)
                            else pattern)
            if path in source_slots:
                if source_slots[path] != instantiated:
                    failures.append(f"{tid}:{path}")
            elif instantiated is not None:
                failures.append(f"{tid}:{path}:rule-requires-missing-slot")
    return GateResult("V1", not failures, {
        "checked": checked,
        "failures": failures[:5],
        "rule": "witness-only replay (no binding re-search)",
    })


# -- V3 -----------------------------------------------------------------------

def _v3_effective_support(rule: dict, source_transitions: list[dict],
                          config: ValidityConfig) -> GateResult:
    """Effective support profile (design doc 7.4): raw support, unique attempts,
    unique bug instances (lineages), unique mechanism families. A single-bug
    multi-seed corpus never claims cross-lineage transfer."""
    metrics = rule.get("abstraction_metrics") or {}
    raw_support = len(source_transitions)
    unique_lineages = len({t.get("lineage_id") for t in source_transitions
                           if t.get("lineage_id")})
    unique_families = len({
        (t.get("action") or {}).get("transformation_family")
        for t in source_transitions
        if (t.get("action") or {}).get("transformation_family")})
    ok = raw_support >= config.min_group_size
    return GateResult("V3", ok, {
        "raw_support": raw_support,
        "unique_attempts": metrics.get("num_sources", raw_support),
        "unique_lineages": unique_lineages,
        "unique_families": unique_families,
        "cross_lineage": unique_lineages >= config.min_lineages_for_cross,
    })


# -- V4 -----------------------------------------------------------------------

def _v4_stability(rule: dict, source_transitions: list[dict],
                  config: ValidityConfig) -> GateResult:
    """Leave-one-out stability (design doc 7.5): for each source, re-crystallize
    the rest (r_{-i}) and check it explains the held-out episode. n < 3 -> N/A,
    NOT a failure."""
    n = len(source_transitions)
    if n < config.v4_min_support:
        return GateResult("V4", None, {"status": "N/A",
                                       "reason": f"n={n} < {config.v4_min_support}"})
    failures: list[str] = []
    failure_details: list[dict] = []
    au_config = AntiUnifyConfig(min_group_size=min(2, n - 1))
    for i, held in enumerate(source_transitions):
        rest = [t for j, t in enumerate(source_transitions) if j != i]
        rewrites = [normalize_rewrite(t) for t in rest]
        result = anti_unify_rewrites(rewrites, au_config)
        held_slots = normalize_rewrite(held).slot_dict()
        mismatches = _explanation_mismatches(result, held_slots)
        if mismatches:
            transition_id = held.get("transition_id")
            failures.append(transition_id)
            failure_details.append({
                "transition_id": transition_id,
                "slot_mismatches": mismatches,
            })
    return GateResult("V4", not failures, {
        "leave_one_out": n,
        "failures": failures,
        "failure_details": failure_details,
        "method": "r_{-i} = phi_P(G \\ e_i); does it explain e_i?",
    })


def _explains(result, held_slots: dict) -> bool:
    """Does the anti-unification result r_{-i} cover the held-out rewrite?"""
    return not _explanation_mismatches(result, held_slots)


def _explanation_mismatches(result, held_slots: dict) -> list[dict]:
    """Return concrete structural incompatibilities in a V4 replay.

    Keeping the slot path and both values turns a binary stability failure into
    a grouping signal (for example ``rtl.target_state: DONE vs COMMIT``).  It
    does not relax V4 or re-bind a witness.
    """
    mismatches = []
    for path, pattern in _result_slots(result).items():
        if isinstance(pattern, str) and is_hole(pattern):
            continue                       # hole accepts any value
        actual = held_slots.get(path)
        if actual != pattern:
            mismatches.append({"path": path, "expected": pattern,
                               "observed": actual})
    return mismatches


# -- shared slot reconstruction ------------------------------------------------

_PATTERN_META = ("type", "domain", "action_domain")


def _rule_slots(rule: dict) -> dict:
    """Reconstruct the full slot dict from a synthesized rule's before/after."""
    slots: dict = {}
    for key, value in rule.get("before_pattern", {}).items():
        if key in _PATTERN_META:
            continue
        slots[f"match.{key}"] = value
    for key, value in rule.get("after_pattern", {}).items():
        if key in _PATTERN_META:
            continue
        slots[key] = value
    return slots


def _result_slots(result) -> dict:
    """Reconstruct the full slot dict from an AntiUnifyResult's patterns."""
    slots: dict = {}
    for key, value in result.before_pattern.items():
        if key in _PATTERN_META:
            continue
        slots[f"match.{key}"] = value
    for key, value in result.after_pattern.items():
        if key in _PATTERN_META:
            continue
        slots[key] = value
    return slots
