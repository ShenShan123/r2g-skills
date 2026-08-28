"""Content-bound memory-change receipts for capability attribution.

The C1 gate is deliberately stronger than comparing two caller-provided
strings.  A capability claim must identify at least one concrete memory
object that was added, removed, or revised, and the optional digest fields in
the delta must agree with the baseline/candidate inputs.  This module remains
evaluation-only: it does not mutate a memory snapshot or grant lifecycle
authority.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


MEMORY_DELTA_VERSION = "memory-delta-v1"
MEMORY_DELTA_ID_FIELDS = (
    "added_transition_ids", "removed_transition_ids", "revised_transition_ids",
    "added_rule_ids", "removed_rule_ids", "revised_rule_ids",
    "added_asset_ids", "removed_asset_ids", "revised_asset_ids",
    "added_causal_path_ids", "removed_causal_path_ids",
    "revised_causal_path_ids",
    "added_capability_ids", "removed_capability_ids",
    "revised_capability_ids",
)
_ENTITY_FAMILIES = ("transition", "rule", "asset", "causal_path", "capability")


@dataclass(frozen=True)
class MemoryDeltaReceipt:
    """Replayable result of validating one explicit memory delta manifest."""

    baseline_memory_digest: str | None
    candidate_memory_digest: str | None
    delta: dict
    changed_ids: tuple[str, ...]
    eligible: bool
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "version": MEMORY_DELTA_VERSION,
            "baseline_memory_digest": self.baseline_memory_digest,
            "candidate_memory_digest": self.candidate_memory_digest,
            "delta": self.delta,
            "changed_ids": list(self.changed_ids),
            "eligible": self.eligible,
            "reasons": list(self.reasons),
        }


def _id_fields_for_family(family: str) -> tuple[str, ...]:
    return tuple(f"{prefix}_{family}_ids"
                 for prefix in ("added", "removed", "revised"))


def _normalise_ids(value, *, field: str) -> tuple[tuple[str, ...], list[str]]:
    if value is None:
        return (), [f"{field}:malformed"]
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        return (), [f"{field}:malformed"]
    reasons: list[str] = []
    if any(not isinstance(item, str) for item in value):
        reasons.append(f"{field}:non_string_id")
    values = tuple(item for item in value if isinstance(item, str))
    if any(not item or not item.strip() for item in values):
        reasons.append(f"{field}:empty_id")
    if len(set(values)) != len(values):
        reasons.append(f"{field}:duplicate_id")
    return tuple(sorted(set(values))), reasons


def evaluate_memory_delta(
    baseline_memory_digest: str | None,
    candidate_memory_digest: str | None,
    memory_delta: Mapping | None,
) -> MemoryDeltaReceipt:
    """Validate a concrete memory delta without trusting caller booleans.

    The evaluator returns an ineligible receipt for malformed input instead of
    raising, matching the fail-closed semantics of the C1-C8 attribution
    evaluator.  A non-empty changed-object set is mandatory.
    """
    reasons: list[str] = []
    baseline = (baseline_memory_digest
                if isinstance(baseline_memory_digest, str) and
                baseline_memory_digest.strip() else None)
    candidate = (candidate_memory_digest
                 if isinstance(candidate_memory_digest, str) and
                 candidate_memory_digest.strip() else None)
    if baseline is None:
        reasons.append("baseline_memory_digest_required")
    if candidate is None:
        reasons.append("candidate_memory_digest_required")
    if baseline is not None and candidate is not None and baseline == candidate:
        reasons.append("memory_digest_unchanged")
    if not isinstance(memory_delta, Mapping):
        reasons.append("memory_delta_required")
        return MemoryDeltaReceipt(
            baseline_memory_digest=baseline, candidate_memory_digest=candidate,
            delta={}, changed_ids=(), eligible=False,
            reasons=tuple(sorted(set(reasons))))

    allowed = {"version", "baseline_memory_digest", "candidate_memory_digest",
               *MEMORY_DELTA_ID_FIELDS}
    # Keep malformed non-string mapping keys fail-closed without allowing a
    # mixed-type key set to raise while sorting diagnostics.
    unknown = set(memory_delta) - allowed
    reasons.extend(
        f"unknown_field:{field}" for field in sorted(unknown, key=str))
    if memory_delta.get("version") != MEMORY_DELTA_VERSION:
        reasons.append("version_mismatch")
    if ("baseline_memory_digest" in memory_delta and
            memory_delta.get("baseline_memory_digest") != baseline):
        reasons.append("baseline_memory_digest_mismatch")
    if ("candidate_memory_digest" in memory_delta and
            memory_delta.get("candidate_memory_digest") != candidate):
        reasons.append("candidate_memory_digest_mismatch")

    normalised: dict[str, list[str]] = {}
    for field in MEMORY_DELTA_ID_FIELDS:
        values, field_reasons = _normalise_ids(
            memory_delta.get(field, []), field=field)
        normalised[field] = list(values)
        reasons.extend(field_reasons)

    for family in _ENTITY_FAMILIES:
        added, removed, revised = (
            set(normalised[field]) for field in _id_fields_for_family(family))
        if added & removed or added & revised or removed & revised:
            reasons.append(f"{family}:delta_sets_overlap")

    changed_ids = tuple(sorted({value for values in normalised.values()
                                for value in values}))
    if not changed_ids:
        reasons.append("changed_memory_object_required")
    return MemoryDeltaReceipt(
        baseline_memory_digest=baseline, candidate_memory_digest=candidate,
        delta=normalised, changed_ids=changed_ids, eligible=not reasons,
        reasons=tuple(sorted(set(reasons))))


__all__ = [
    "MEMORY_DELTA_ID_FIELDS", "MEMORY_DELTA_VERSION", "MemoryDeltaReceipt",
    "evaluate_memory_delta",
]
