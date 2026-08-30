"""Shared verified-execution admission for learner-derived memory state.

Dataset membership answers *which partition* a transition belongs to.  It is
not evidence that the transition was actually executed under an executable
oracle.  Every learner consumer reuses this predicate so crystallization and
online event writers cannot provide weaker authority paths around one another.
"""
from __future__ import annotations

import sqlite3

from tehm.causal.mechanism import load_transition_facts


def require_verified_execution(facts) -> None:
    """Require a complete executable oracle before learner-derived updates."""
    verifier = facts.verifier
    reasons: list[str] = []
    if verifier.get("verdict") not in {"PASS", "FAIL"}:
        reasons.append("verifier_verdict_not_definitive")
    if verifier.get("oracle_complete") is not True:
        reasons.append("oracle_incomplete")
    if verifier.get("oracle_type") in {"UNKNOWN", "COMPILE", "LINT"}:
        reasons.append("oracle_type_not_executable")
    full_oracle = verifier.get("full_oracle")
    if full_oracle is not None:
        if not isinstance(full_oracle, dict):
            reasons.append("full_oracle_malformed")
        else:
            for arm in ("before", "after"):
                payload = full_oracle.get(arm)
                if not isinstance(payload, dict) or payload.get("complete") is not True:
                    reasons.append(f"full_oracle_{arm}_incomplete")
    if reasons:
        raise ValueError(
            "online learner observation requires complete verified execution: "
            + ",".join(sorted(set(reasons))))


def require_verified_transition(conn: sqlite3.Connection,
                                transition_id: str) -> None:
    """Load and validate one canonical transition's execution witness."""
    try:
        facts = load_transition_facts(conn, transition_id)
    except (KeyError, ValueError) as exc:
        raise ValueError(
            "learner-eligible event source transition is not a valid "
            "canonical execution witness") from exc
    try:
        require_verified_execution(facts)
    except ValueError as exc:
        raise ValueError(
            "learner-eligible event source lacks complete verified execution: "
            + str(exc)) from exc


__all__ = ["require_verified_execution", "require_verified_transition"]
