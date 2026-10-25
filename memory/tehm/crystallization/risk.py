"""Risk stratification (design doc 8, 26 Phase 6).

Must distinguish:
    CREATED_REGRESSION     PASS -> FAIL   (the patch introduced a new error)
    NEWLY_OBSERVED_FAILURE N/A  -> FAIL   (new oracle / widened scope / exposed
                                          deeper bug after the original cleared)

These are NOT primary effect keys (design doc 6.2) — they ride the rule as a
risk profile. v1 records the activation contexts but NEVER auto-promotes a
context predicate (P_c) to a hard precondition (P_h).
"""
from __future__ import annotations

RISK_VERSION = "risk-v0.1"
RISK_KINDS = ("CREATED_REGRESSION", "NEWLY_OBSERVED_FAILURE")


def stratify_rule_risk(rule: dict, source_transitions: list[dict]) -> list[dict]:
    """Aggregate created regressions / newly observed failures across the rule's
    source episodes into a risk profile (design doc 8.3)."""
    occurrences: dict[str, list[dict]] = {k: [] for k in RISK_KINDS}
    for t in source_transitions:
        delta = t.get("observation_delta") or {}
        created = delta.get("created_regressions") or []
        newly = delta.get("newly_observed_failures") or []
        tid = t.get("transition_id")
        if created:
            occurrences["CREATED_REGRESSION"].append(
                {"transition_id": tid, "details": list(created)})
        if newly:
            occurrences["NEWLY_OBSERVED_FAILURE"].append(
                {"transition_id": tid, "details": list(newly)})

    profile: list[dict] = []
    for kind in RISK_KINDS:
        contexts = occurrences[kind]
        if not contexts:
            continue
        profile.append({
            "risk": kind,
            "support": len(contexts),
            "status": "CONTEXT_DEPENDENT",  # v1: never auto-promoted to P_h (8)
            "contexts": contexts,
        })
    return profile
