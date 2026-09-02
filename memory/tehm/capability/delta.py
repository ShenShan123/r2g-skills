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

from tehm.ids import stable_dumps


MEMORY_DELTA_VERSION = "memory-delta-v1"
MEMORY_DELTA_ID_FIELDS = (
    "added_transition_ids", "removed_transition_ids", "revised_transition_ids",
    "added_rule_ids", "removed_rule_ids", "revised_rule_ids",
    "added_asset_ids", "removed_asset_ids", "revised_asset_ids",
    "added_causal_path_ids", "removed_causal_path_ids",
    "revised_causal_path_ids",
    "added_capability_ids", "removed_capability_ids",
    "revised_capability_ids",
    "added_knowledge_ids", "removed_knowledge_ids",
    "revised_knowledge_ids",
    # Relations are immutable evidence edges.  They can be added or retired
    # in a memory-state delta, but are never revised in place.
    "added_relation_ids", "removed_relation_ids",
)
RELATION_DELTA_ID_FIELDS = ("added_relation_ids", "removed_relation_ids")
_ENTITY_FAMILIES = (
    "transition", "rule", "asset", "causal_path", "capability", "knowledge",
)

DERIVED_DELTA_VERSIONS = {
    "knowledge": "knowledge-delta-v1",
    "asset": "asset-delta-v1",
}


@dataclass(frozen=True)
class MemoryDeltaReceipt:
    """Replayable result of validating one explicit memory delta manifest."""

    baseline_memory_digest: str | None
    candidate_memory_digest: str | None
    delta: dict
    changed_ids: tuple[str, ...]
    eligible: bool
    reasons: tuple[str, ...] = ()

    @property
    def added_relation_ids(self) -> tuple[str, ...]:
        return tuple(self.delta.get("added_relation_ids", ()))

    @property
    def removed_relation_ids(self) -> tuple[str, ...]:
        return tuple(self.delta.get("removed_relation_ids", ()))

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

    @property
    def receipt_digest(self) -> str:
        """Content digest for the normalized C1 receipt payload."""
        import hashlib

        return "sha256:" + hashlib.sha256(
            stable_dumps(self.to_dict()).encode()).hexdigest()

    @classmethod
    def from_dict(cls, payload: object) -> "MemoryDeltaReceipt":
        """Replay and validate a serialized C1 memory-delta receipt."""
        if not isinstance(payload, Mapping):
            raise ValueError("memory delta receipt must be an object")
        required = {
            "version", "baseline_memory_digest", "candidate_memory_digest",
            "delta", "changed_ids", "eligible", "reasons",
        }
        if not required <= set(payload):
            raise ValueError("memory delta receipt is missing required fields")
        delta = payload["delta"]
        if not isinstance(delta, Mapping):
            raise ValueError("memory delta receipt delta must be an object")
        try:
            manifest = {
                "version": payload["version"],
                "baseline_memory_digest": payload["baseline_memory_digest"],
                "candidate_memory_digest": payload["candidate_memory_digest"],
                **dict(delta),
            }
        except (TypeError, ValueError) as exc:
            raise ValueError("memory delta receipt delta is malformed") from exc
        checked = evaluate_memory_delta(
            payload["baseline_memory_digest"],
            payload["candidate_memory_digest"], manifest)
        if (payload["changed_ids"] != list(checked.changed_ids) or
                payload["eligible"] is not checked.eligible or
                payload["reasons"] != list(checked.reasons)):
            raise ValueError("memory delta receipt replay mismatch")
        supplied = payload.get("receipt_digest")
        if supplied is not None and supplied != checked.receipt_digest:
            raise ValueError("memory delta receipt digest mismatch")
        return checked


_SHADOW_OBJECT_FIELDS = {
    "transition": "added_transition_ids",
    "rule": "added_rule_ids",
    "asset": "added_asset_ids",
    "causal_path": "added_causal_path_ids",
    "capability": "added_capability_ids",
    "knowledge": "added_knowledge_ids",
}
_SHADOW_BOOKKEEPING_PREFIXES = frozenset({"episode", "rule_revision"})


def memory_delta_from_shadow_update(receipt) -> MemoryDeltaReceipt:
    """Derive a C1 memory delta from one discarded P13 shadow receipt.

    The P13 executor records the logical source and post-update staging
    digests, plus the exact objects/relations created in staging.  This
    adapter is the typed P13→P14 seam: it never accepts caller-provided
    memory digests or outcome booleans, and it refuses a receipt that crosses
    the canonical/production boundary.  ``episode`` and ``rule_revision``
    rows are bookkeeping generated alongside their owning memory object and
    are intentionally not treated as independent C1 objects.
    """
    try:
        from tehm.evolution import AppliedShadowUpdateReceipt
    except ImportError as exc:  # pragma: no cover - package wiring failure
        raise TypeError("P13 shadow receipt support is unavailable") from exc
    if not isinstance(receipt, AppliedShadowUpdateReceipt):
        raise TypeError("memory delta requires AppliedShadowUpdateReceipt")
    if (receipt.canonical_memory_mutation != "none" or
            receipt.production_runtime_imported is not False or
            receipt.production_authority_changed or
            receipt.canonical_rows_changed or
            receipt.staging_discarded is not True or
            receipt.lifecycle_mutation != "isolated_staging_only"):
        raise ValueError("P13 shadow receipt crosses an authority boundary")
    if not receipt.raw_evidence_preserved:
        raise ValueError("P13 shadow receipt did not preserve raw evidence")
    if receipt.source_digest_before != receipt.source_digest_after:
        raise ValueError("P13 shadow source digest changed")
    if receipt.raw_evidence_before_digest != receipt.raw_evidence_after_digest:
        raise ValueError("P13 shadow raw evidence digest changed")

    def _digest(value: object, field: str) -> str:
        if (type(value) is not str or
                len(value) != len("sha256:") + 64 or
                not value.startswith("sha256:") or
                any(char not in "0123456789abcdef" for char in value[7:])):
            raise ValueError(f"P13 shadow {field} is not a sha256 digest")
        return value

    baseline = _digest(receipt.source_digest_before, "source digest")
    candidate = _digest(receipt.staging_digest_after, "staging digest")
    delta = {field: [] for field in MEMORY_DELTA_ID_FIELDS}
    seen: set[str] = set()
    for raw_id in receipt.created_object_ids:
        if type(raw_id) is not str or not raw_id.strip():
            raise ValueError("P13 shadow created object ID is malformed")
        object_id = raw_id.strip()
        if object_id in seen:
            raise ValueError("P13 shadow created object IDs contain duplicates")
        seen.add(object_id)
        prefix, separator, _suffix = object_id.partition(":")
        if not separator or not _suffix:
            raise ValueError("P13 shadow created object ID lacks a type prefix")
        field = _SHADOW_OBJECT_FIELDS.get(prefix)
        if field is not None:
            delta[field].append(object_id)
        elif prefix not in _SHADOW_BOOKKEEPING_PREFIXES:
            raise ValueError(
                f"P13 shadow created object type is unsupported: {prefix}")
    for raw_id in receipt.created_relation_ids:
        if type(raw_id) is not str or not raw_id.strip():
            raise ValueError("P13 shadow created relation ID is malformed")
        relation_id = raw_id.strip()
        if relation_id in seen:
            raise ValueError("P13 shadow created IDs contain duplicates")
        seen.add(relation_id)
        delta["added_relation_ids"].append(relation_id)
    return evaluate_memory_delta(baseline, candidate, {
        "version": MEMORY_DELTA_VERSION,
        "baseline_memory_digest": baseline,
        "candidate_memory_digest": candidate,
        **delta,
    })


@dataclass(frozen=True)
class KnowledgeDeltaReceipt:
    """Content-bound change set for Mechanism Knowledge (P8 witness)."""

    baseline_memory_digest: str | None
    candidate_memory_digest: str | None
    added_knowledge_ids: tuple[str, ...]
    removed_knowledge_ids: tuple[str, ...]
    revised_knowledge_ids: tuple[str, ...]
    eligible: bool
    reasons: tuple[str, ...] = ()

    @property
    def changed_ids(self) -> tuple[str, ...]:
        return tuple(sorted({
            *self.added_knowledge_ids, *self.removed_knowledge_ids,
            *self.revised_knowledge_ids,
        }))

    def to_dict(self) -> dict:
        return {
            "version": DERIVED_DELTA_VERSIONS["knowledge"],
            "baseline_memory_digest": self.baseline_memory_digest,
            "candidate_memory_digest": self.candidate_memory_digest,
            "added_knowledge_ids": list(self.added_knowledge_ids),
            "removed_knowledge_ids": list(self.removed_knowledge_ids),
            "revised_knowledge_ids": list(self.revised_knowledge_ids),
            "changed_ids": list(self.changed_ids), "eligible": self.eligible,
            "reasons": list(self.reasons),
        }

    @property
    def receipt_digest(self) -> str:
        import hashlib

        return "sha256:" + hashlib.sha256(
            stable_dumps(self.to_dict()).encode()).hexdigest()

    @classmethod
    def from_dict(cls, payload: object) -> "KnowledgeDeltaReceipt":
        if not isinstance(payload, Mapping):
            raise ValueError("knowledge delta receipt must be an object")
        required = {
            "version", "baseline_memory_digest", "candidate_memory_digest",
            "added_knowledge_ids", "removed_knowledge_ids", "revised_knowledge_ids",
            "changed_ids", "eligible", "reasons",
        }
        if any(key not in payload for key in required):
            raise ValueError("knowledge delta receipt is missing required fields")
        manifest = {key: payload[key] for key in (
            "version", "baseline_memory_digest", "candidate_memory_digest",
            "added_knowledge_ids", "removed_knowledge_ids", "revised_knowledge_ids")}
        checked = evaluate_knowledge_delta(
            payload.get("baseline_memory_digest"),
            payload.get("candidate_memory_digest"), manifest)
        if (payload.get("changed_ids") != list(checked.changed_ids) or
                payload.get("eligible") is not checked.eligible or
                payload.get("reasons") != list(checked.reasons)):
            raise ValueError("knowledge delta receipt replay mismatch")
        supplied = payload.get("receipt_digest")
        if supplied is not None and supplied != checked.receipt_digest:
            raise ValueError("knowledge delta receipt digest mismatch")
        return checked


@dataclass(frozen=True)
class AssetDeltaReceipt:
    """Content-bound change set for executable Assets (P8 witness)."""

    baseline_memory_digest: str | None
    candidate_memory_digest: str | None
    added_asset_ids: tuple[str, ...]
    removed_asset_ids: tuple[str, ...]
    revised_asset_ids: tuple[str, ...]
    eligible: bool
    reasons: tuple[str, ...] = ()

    @property
    def changed_ids(self) -> tuple[str, ...]:
        return tuple(sorted({
            *self.added_asset_ids, *self.removed_asset_ids,
            *self.revised_asset_ids,
        }))

    def to_dict(self) -> dict:
        return {
            "version": DERIVED_DELTA_VERSIONS["asset"],
            "baseline_memory_digest": self.baseline_memory_digest,
            "candidate_memory_digest": self.candidate_memory_digest,
            "added_asset_ids": list(self.added_asset_ids),
            "removed_asset_ids": list(self.removed_asset_ids),
            "revised_asset_ids": list(self.revised_asset_ids),
            "changed_ids": list(self.changed_ids), "eligible": self.eligible,
            "reasons": list(self.reasons),
        }

    @property
    def receipt_digest(self) -> str:
        import hashlib

        return "sha256:" + hashlib.sha256(
            stable_dumps(self.to_dict()).encode()).hexdigest()

    @classmethod
    def from_dict(cls, payload: object) -> "AssetDeltaReceipt":
        if not isinstance(payload, Mapping):
            raise ValueError("asset delta receipt must be an object")
        required = {
            "version", "baseline_memory_digest", "candidate_memory_digest",
            "added_asset_ids", "removed_asset_ids", "revised_asset_ids",
            "changed_ids", "eligible", "reasons",
        }
        if any(key not in payload for key in required):
            raise ValueError("asset delta receipt is missing required fields")
        manifest = {key: payload[key] for key in (
            "version", "baseline_memory_digest", "candidate_memory_digest",
            "added_asset_ids", "removed_asset_ids", "revised_asset_ids")}
        checked = evaluate_asset_delta(
            payload.get("baseline_memory_digest"),
            payload.get("candidate_memory_digest"), manifest)
        if (payload.get("changed_ids") != list(checked.changed_ids) or
                payload.get("eligible") is not checked.eligible or
                payload.get("reasons") != list(checked.reasons)):
            raise ValueError("asset delta receipt replay mismatch")
        supplied = payload.get("receipt_digest")
        if supplied is not None and supplied != checked.receipt_digest:
            raise ValueError("asset delta receipt digest mismatch")
        return checked


def _evaluate_derived_delta(
    kind: str, baseline_memory_digest: str | None,
    candidate_memory_digest: str | None, delta: Mapping | None,
):
    """Validate one knowledge/asset-only delta manifest fail-closed."""
    if kind not in DERIVED_DELTA_VERSIONS:
        raise ValueError(f"unsupported derived delta kind: {kind!r}")
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
    fields = _id_fields_for_family(kind)
    allowed = {"version", "baseline_memory_digest", "candidate_memory_digest",
               *fields}
    normalised: dict[str, list[str]] = {field: [] for field in fields}
    if not isinstance(delta, Mapping):
        reasons.append("derived_delta_required")
    else:
        reasons.extend(
            f"unknown_field:{field}" for field in sorted(
                set(delta) - allowed, key=str))
        if delta.get("version") != DERIVED_DELTA_VERSIONS[kind]:
            reasons.append("version_mismatch")
        if ("baseline_memory_digest" in delta and
                delta.get("baseline_memory_digest") != baseline):
            reasons.append("baseline_memory_digest_mismatch")
        if ("candidate_memory_digest" in delta and
                delta.get("candidate_memory_digest") != candidate):
            reasons.append("candidate_memory_digest_mismatch")
        for field in fields:
            values, field_reasons = _normalise_ids(
                delta.get(field, []), field=field)
            normalised[field] = list(values)
            reasons.extend(field_reasons)
    sets = {field: set(values) for field, values in normalised.items()}
    if sets[fields[0]] & sets[fields[1]] or sets[fields[0]] & sets[fields[2]] or sets[fields[1]] & sets[fields[2]]:
        reasons.append(f"{kind}:delta_sets_overlap")
    changed = tuple(sorted({value for values in normalised.values() for value in values}))
    if not changed:
        reasons.append("changed_derived_object_required")
    common = {
        "baseline_memory_digest": baseline,
        "candidate_memory_digest": candidate,
        "eligible": not reasons,
        "reasons": tuple(sorted(set(reasons))),
    }
    if kind == "knowledge":
        return KnowledgeDeltaReceipt(
            **common, added_knowledge_ids=tuple(normalised[fields[0]]),
            removed_knowledge_ids=tuple(normalised[fields[1]]),
            revised_knowledge_ids=tuple(normalised[fields[2]]))
    return AssetDeltaReceipt(
        **common, added_asset_ids=tuple(normalised[fields[0]]),
        removed_asset_ids=tuple(normalised[fields[1]]),
        revised_asset_ids=tuple(normalised[fields[2]]))


def evaluate_knowledge_delta(
    baseline_memory_digest: str | None,
    candidate_memory_digest: str | None,
    delta: Mapping | None,
) -> KnowledgeDeltaReceipt:
    return _evaluate_derived_delta(
        "knowledge", baseline_memory_digest, candidate_memory_digest, delta)


def evaluate_asset_delta(
    baseline_memory_digest: str | None,
    candidate_memory_digest: str | None,
    delta: Mapping | None,
) -> AssetDeltaReceipt:
    return _evaluate_derived_delta(
        "asset", baseline_memory_digest, candidate_memory_digest, delta)


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

    added_relations = set(normalised["added_relation_ids"])
    removed_relations = set(normalised["removed_relation_ids"])
    if added_relations & removed_relations:
        reasons.append("relation:delta_sets_overlap")

    changed_ids = tuple(sorted({value for values in normalised.values()
                                for value in values}))
    if not changed_ids:
        reasons.append("changed_memory_object_required")
    return MemoryDeltaReceipt(
        baseline_memory_digest=baseline, candidate_memory_digest=candidate,
        delta=normalised, changed_ids=changed_ids, eligible=not reasons,
        reasons=tuple(sorted(set(reasons))))


__all__ = [
    "MEMORY_DELTA_ID_FIELDS", "RELATION_DELTA_ID_FIELDS",
    "MEMORY_DELTA_VERSION", "MemoryDeltaReceipt",
    "DERIVED_DELTA_VERSIONS", "KnowledgeDeltaReceipt", "AssetDeltaReceipt",
    "evaluate_asset_delta", "evaluate_knowledge_delta", "evaluate_memory_delta",
    "memory_delta_from_shadow_update",
]
