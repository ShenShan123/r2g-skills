"""Primary effect canonicalization ``K_primary`` (design doc 6.2).

.. math::

    K_primary = Canon(\\Delta V_{target/preserve},\\ \\Delta F,\\ \\Delta C)

- ``\\Delta V``  oracle role delta: target verdict before/after + preserve pass count
- ``\\Delta F``  failure delta: the failure-state CHANGE (normalized categories,
  not instance-specific raw values)
- ``\\Delta C``  coarse structural delta: transformation family + structural flags

``CREATED_REGRESSION`` and ``NEWLY_OBSERVED_FAILURE`` are deliberately NOT part
of the primary key (design doc 6.2) — they enter risk stratification instead, so
an identical repair move that happens to regress something does NOT fragment the
effect group.

This module is PURE: it canonicalizes plain dicts, so the capture-time stored
key, the preflight grouping key, and (later) the crystallization group key all
share ONE canon with no import cycle.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from tehm.ids import stable_dumps

CANON_VERSION = "effect-canon-v0.2"

# original_failure states that imply the target oracle was failing before.
_ORIGINAL_FAILURE_FAILING = frozenset({"REMOVED", "PRESENT"})


@dataclass(frozen=True)
class PrimaryEffect:
    """The canonical primary effect of one transition (design doc 6.2)."""

    target_oracle_delta: dict
    preserve_delta: dict
    failure_delta: dict
    coarse_structural_delta: dict
    canon_version: str = CANON_VERSION

    def to_dict(self) -> dict:
        return {
            "target_oracle_delta": self.target_oracle_delta,
            "preserve_delta": self.preserve_delta,
            "failure_delta": self.failure_delta,
            "coarse_structural_delta": self.coarse_structural_delta,
            "canon_version": self.canon_version,
        }

    def key(self) -> str:
        payload = stable_dumps(self.to_dict())
        return f"effect_{hashlib.sha1(payload.encode()).hexdigest()[:16]}"


def canonicalize_effect_fields(action: dict, observation_delta: dict,
                               verifier: dict,
                               coarse_structural_delta: dict | None = None,
                               ) -> PrimaryEffect:
    """Canonicalize the primary effect from plain action/delta/verifier dicts.

    ``verifier`` may carry an optional ``preserve_pass_count`` (design doc 6.2
    example); when absent the preserve delta is ``None`` — never fabricated.
    """
    original_failure = observation_delta.get("original_failure", "UNKNOWN")
    verdict_before = "FAIL" if original_failure in _ORIGINAL_FAILURE_FAILING else "UNKNOWN"

    target_oracle_delta = {
        "oracle_type": verifier.get("oracle_type", "UNKNOWN"),
        "verdict_before": verdict_before,
        "verdict_after": verifier.get("verdict", "UNKNOWN"),
    }
    preserve_delta = {
        "pass_to_pass_count": verifier.get("preserve_pass_count"),
    }
    failure_delta = {
        "original_failure": original_failure,
        "first_divergence_delta": normalize_divergence(
            observation_delta.get("first_divergence")),
        "failing_tests_delta": normalize_tests(
            observation_delta.get("failing_tests")),
    }
    coarse = _build_coarse(action, coarse_structural_delta)
    return PrimaryEffect(
        target_oracle_delta=target_oracle_delta,
        preserve_delta=preserve_delta,
        failure_delta=failure_delta,
        coarse_structural_delta=coarse,
    )


def effect_key(action: dict, observation_delta: dict, verifier: dict,
               coarse_structural_delta: dict | None = None) -> str:
    """Content-addressed effect key for grouping (deterministic)."""
    return canonicalize_effect_fields(
        action, observation_delta, verifier, coarse_structural_delta).key()


def effect_key_from_transition_dict(transition_dict: dict) -> str:
    """Effect key from a transition's canonical dict (``to_dict`` shape)."""
    return effect_key(
        transition_dict.get("action") or {},
        transition_dict.get("observation_delta") or {},
        transition_dict.get("verifier") or {},
    )


def normalize_divergence(fd) -> str | None:
    """Category of the first-divergence change (FIXED / SHIFTED / NEW / ...)."""
    if not isinstance(fd, dict):
        return None
    before = _as_int(fd.get("before"))
    after = _as_int(fd.get("after"))
    if before is not None and after is None:
        return "FIXED"
    if before is None and after is not None:
        return "NEW"
    if before is not None and after is not None:
        return "SHIFTED" if before != after else "UNCHANGED"
    return "UNCHANGED"


def normalize_tests(ft) -> str | None:
    """Category of the failing-test-count change (REDUCED / INCREASED / ...)."""
    if not isinstance(ft, dict):
        return None
    before = _as_int(ft.get("before"))
    after = _as_int(ft.get("after"))
    if before is None or after is None:
        return "UNKNOWN"
    if after < before:
        return "REDUCED"
    if after > before:
        return "INCREASED"
    return "UNCHANGED"


def _default_coarse(action: dict) -> dict:
    payload = action.get("payload") or {}
    return {
        "transformation_family": action.get("transformation_family"),
        "action_domain": action.get("domain"),
        "register_boundary_changed": bool(payload.get("register_boundary_changed", False)),
        "dependency_cone_changed": bool(payload.get("dependency_cone_changed", True)),
        "rerun_from": payload.get("rerun_from"),
        "recheck": payload.get("recheck"),
    }


def _build_coarse(action: dict, override: dict | None) -> dict:
    """Coarse structural delta: always from the action; ``override`` only fills
    structural flags that are not derivable from the action dict. This keeps the
    capture-time stored key and the preflight grouping key byte-identical."""
    coarse = _default_coarse(action)
    if override:
        coarse.update({k: v for k, v in override.items() if v is not None})
    return coarse


def _as_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
