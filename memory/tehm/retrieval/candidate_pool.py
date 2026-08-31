"""Evaluation-only candidate-pool A/B composition (P6).

The existing promoted-rule retrieval path and the P5 router remain untouched.
This module is a separate experiment seam: it composes typed no-memory and
memory-advisor candidates, records diversity/budget diagnostics, and computes
outcome metrics.  It never executes a candidate, changes lifecycle state, or
turns an asset into a ``MemoryCandidate`` source.
"""
from __future__ import annotations

import hashlib
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from contracts import (
    CANDIDATE_SOURCES,
    MEMORY_ROUTING_DECISIONS,
    MemoryCandidate,
    MemoryQuery,
    MemoryRoutingDecision,
)
from tehm.canonical.transition import OUTCOMES, POSITIVE_OUTCOMES, HARMFUL_OUTCOMES
from tehm.ids import stable_dumps


CANDIDATE_POOL_VERSION = "candidate-pool-v0.1"
CANDIDATE_POOL_ARMS = (
    "NO_MEMORY", "ALWAYS_MEMORY", "APPLICABILITY_GATED", "CAUSAL_NO_SKILL",
)
MAX_MEMORY_ADVISOR_CANDIDATES = 1
POOL_OUTCOMES = (*OUTCOMES, "ABSTAIN")
_ARM_ALIASES = {
    "GATED_MEMORY": "APPLICABILITY_GATED",
    "CAUSAL_MEMORY": "CAUSAL_NO_SKILL",
}


class CandidatePoolError(ValueError):
    """A malformed evaluation pool cannot be safely composed."""


def _text(value: object, field_name: str) -> str:
    if type(value) is not str or not value.strip():
        raise CandidatePoolError(f"candidate pool {field_name} is required")
    return value.strip()


def _finite_unit(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CandidatePoolError(f"candidate pool {field_name} must be numeric")
    value = float(value)
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise CandidatePoolError(
            f"candidate pool {field_name} must be finite in [0, 1]")
    return round(value, 6)


def _strings(value: object, field_name: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise CandidatePoolError(f"candidate pool {field_name} must be a sequence")
    values = tuple(value)
    if not allow_empty and not values:
        raise CandidatePoolError(f"candidate pool {field_name} must not be empty")
    if any(type(item) is not str or not item.strip() for item in values):
        raise CandidatePoolError(
            f"candidate pool {field_name} must contain non-empty strings")
    values = tuple(item.strip() for item in values)
    if len(set(values)) != len(values):
        raise CandidatePoolError(
            f"candidate pool {field_name} must not contain duplicates")
    return values


def _source_strings(value: object) -> tuple[str, ...]:
    """Validate the parallel source vector; repeated source labels are valid."""
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise CandidatePoolError("candidate pool candidate_sources must be a sequence")
    values = tuple(value)
    if any(type(item) is not str or not item.strip() for item in values):
        raise CandidatePoolError(
            "candidate pool candidate_sources must contain non-empty strings")
    return tuple(item.strip() for item in values)


def _canonical_arm(value: object) -> str:
    arm = _text(value, "arm").upper()
    arm = _ARM_ALIASES.get(arm, arm)
    if arm not in CANDIDATE_POOL_ARMS:
        raise CandidatePoolError(
            f"candidate pool arm must be one of {CANDIDATE_POOL_ARMS}")
    return arm


def _query_digest(query: MemoryQuery) -> str:
    if not isinstance(query, MemoryQuery):
        raise TypeError("candidate pool requires MemoryQuery")
    try:
        payload = query.to_dict()
        encoded = stable_dumps(payload)
    except (TypeError, ValueError) as exc:
        raise CandidatePoolError("candidate pool query is not JSON-serializable") from exc
    return "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()


def _candidate_payload(candidate: MemoryCandidate) -> dict:
    if not isinstance(candidate, MemoryCandidate):
        raise TypeError("candidate pool entries must be MemoryCandidate")
    candidate.validate()
    if type(candidate.candidate_id) is not str or not candidate.candidate_id.strip():
        raise CandidatePoolError("candidate pool candidate_id is required")
    payload = candidate.payload
    if not isinstance(payload, Mapping):
        raise CandidatePoolError("candidate pool candidate payload must be an object")
    try:
        import json

        decoded = json.loads(stable_dumps(dict(payload)))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CandidatePoolError(
            f"candidate pool payload for {candidate.candidate_id} is not JSON-serializable") from exc
    if not isinstance(decoded, dict):  # pragma: no cover - serializer guarantee
        raise CandidatePoolError("candidate pool candidate payload must be an object")
    return decoded


def _normalise_candidates(candidates: Sequence[MemoryCandidate], field_name: str) -> tuple[MemoryCandidate, ...]:
    if isinstance(candidates, (str, bytes)) or not isinstance(candidates, Sequence):
        raise CandidatePoolError(f"candidate pool {field_name} must be a sequence")
    values = tuple(candidates)
    ids: set[str] = set()
    for candidate in values:
        _candidate_payload(candidate)
        if candidate.candidate_id in ids:
            raise CandidatePoolError(
                f"candidate pool {field_name} contains duplicate candidate_id")
        ids.add(candidate.candidate_id)
    # Scores are transparent inputs, not an opaque learned ranker.  Missing or
    # malformed scores sort after finite scores and tie-break by ID.
    def rank_key(candidate: MemoryCandidate) -> tuple[float, str]:
        score = candidate.score
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            score = float("-inf")
        elif not math.isfinite(float(score)):
            score = float("-inf")
        return (-float(score), candidate.candidate_id)

    return tuple(sorted(values, key=rank_key))


def _nested_mappings(payload: Mapping) -> tuple[Mapping, ...]:
    values: list[Mapping] = [payload]
    for key in ("rule", "skill", "action", "asset", "knowledge", "mechanism"):
        value = payload.get(key)
        if isinstance(value, Mapping):
            values.append(value)
    return tuple(values)


def _candidate_action_family(candidate: MemoryCandidate) -> str:
    payload = _candidate_payload(candidate)
    for mapping in _nested_mappings(payload):
        for key in ("transformation_family", "action_family", "family"):
            value = mapping.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        action = mapping.get("action")
        if isinstance(action, Mapping):
            value = action.get("transformation_family") or action.get("family")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return "UNKNOWN"


def _candidate_mechanism_hypothesis(candidate: MemoryCandidate) -> str:
    payload = _candidate_payload(candidate)
    for mapping in _nested_mappings(payload):
        for key in ("mechanism_family", "mechanism_hypothesis", "mechanism_signature"):
            value = mapping.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, Mapping):
                family = value.get("mechanism_family") or value.get("family") or value.get("type")
                if isinstance(family, str) and family.strip():
                    return family.strip()
        for key in ("knowledge_id", "path_id"):
            value = mapping.get(key)
            if isinstance(value, str) and value.strip():
                return f"{key}:{value.strip()}"
    return "UNKNOWN"


def _candidate_applicable(candidate: MemoryCandidate) -> bool:
    payload = _candidate_payload(candidate)
    for mapping in _nested_mappings(payload):
        if mapping.get("applicability_status") == "APPLICABLE":
            return True
        status = mapping.get("applicability")
        if isinstance(status, Mapping) and status.get("eligible") is True:
            return True
        if mapping.get("applicable") is True:
            return True
    return False


def _candidate_reference_ids(candidate: MemoryCandidate) -> set[str]:
    payload = _candidate_payload(candidate)
    refs = {candidate.candidate_id}
    for mapping in _nested_mappings(payload):
        for key in ("rule_id", "path_id", "asset_id", "knowledge_id", "candidate_id"):
            value = mapping.get(key)
            if isinstance(value, str) and value.strip():
                refs.add(value.strip())
    return refs


def _normalised_entropy(values: Sequence[str]) -> float:
    if not values:
        return 0.0
    counts = Counter(values)
    total = float(len(values))
    entropy = -sum((count / total) * math.log(count / total, 2)
                   for count in counts.values())
    maximum = math.log(len(counts), 2) if len(counts) > 1 else 0.0
    # A one-class pool is intentionally zero diversity; a pool with N distinct
    # hypotheses has normalized entropy one.
    return round(entropy / maximum, 6) if maximum else 0.0


@dataclass(frozen=True)
class CandidatePoolReceipt:
    """Content-addressed composition/audit receipt for one A/B case."""

    case_id: str
    arm: str
    query_digest: str
    routing_receipt_id: str | None
    routing_decision: str | None
    candidate_budget: int
    candidate_ids: tuple[str, ...]
    candidate_sources: tuple[str, ...]
    no_memory_candidate_ids: tuple[str, ...]
    memory_candidate_ids: tuple[str, ...]
    unique_action_families: tuple[str, ...]
    unique_mechanism_hypotheses: tuple[str, ...]
    candidate_diversity: float
    search_entropy: float
    memory_admitted: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _text(self.case_id, "case_id")
        object.__setattr__(self, "arm", _canonical_arm(self.arm))
        _text(self.query_digest, "query_digest")
        if self.routing_receipt_id is not None:
            _text(self.routing_receipt_id, "routing_receipt_id")
        if self.routing_decision is not None and self.routing_decision not in MEMORY_ROUTING_DECISIONS:
            raise CandidatePoolError("candidate pool routing_decision is invalid")
        if type(self.candidate_budget) is not int or self.candidate_budget < 1:
            raise CandidatePoolError("candidate pool candidate_budget must be positive")
        ids = _strings(self.candidate_ids, "candidate_ids")
        sources = _source_strings(self.candidate_sources)
        no_ids = _strings(self.no_memory_candidate_ids, "no_memory_candidate_ids", allow_empty=False)
        memory_ids = _strings(self.memory_candidate_ids, "memory_candidate_ids")
        if len(ids) != len(sources):
            raise CandidatePoolError("candidate pool IDs and sources must align")
        if len(ids) > self.candidate_budget:
            raise CandidatePoolError("candidate pool exceeds candidate_budget")
        if len(ids) != len(set(no_ids) | set(memory_ids)):
            raise CandidatePoolError("candidate pool arm IDs do not match candidate_ids")
        if set(no_ids) & set(memory_ids):
            raise CandidatePoolError("candidate pool memory/no-memory IDs overlap")
        if set(ids) != set(no_ids) | set(memory_ids):
            raise CandidatePoolError("candidate pool arm IDs do not cover candidate_ids")
        if len(memory_ids) > MAX_MEMORY_ADVISOR_CANDIDATES:
            raise CandidatePoolError("candidate pool admits at most one memory candidate")
        if any(source not in CANDIDATE_SOURCES for source in sources):
            raise CandidatePoolError("candidate pool contains an unknown candidate source")
        families = _strings(self.unique_action_families, "unique_action_families")
        mechanisms = _strings(self.unique_mechanism_hypotheses, "unique_mechanism_hypotheses")
        if len(families) > len(ids) or len(mechanisms) > len(ids):
            raise CandidatePoolError("candidate pool diversity counts are invalid")
        _finite_unit(self.candidate_diversity, "candidate_diversity")
        _finite_unit(self.search_entropy, "search_entropy")
        if type(self.memory_admitted) is not bool:
            raise CandidatePoolError("candidate pool memory_admitted must be boolean")
        if self.memory_admitted != bool(memory_ids):
            raise CandidatePoolError("candidate pool memory_admitted disagrees with IDs")
        reasons = _strings(self.reasons, "reasons")
        object.__setattr__(self, "candidate_ids", ids)
        object.__setattr__(self, "candidate_sources", sources)
        object.__setattr__(self, "no_memory_candidate_ids", no_ids)
        object.__setattr__(self, "memory_candidate_ids", memory_ids)
        object.__setattr__(self, "unique_action_families", families)
        object.__setattr__(self, "unique_mechanism_hypotheses", mechanisms)
        object.__setattr__(self, "candidate_diversity",
                           _finite_unit(self.candidate_diversity, "candidate_diversity"))
        object.__setattr__(self, "search_entropy",
                           _finite_unit(self.search_entropy, "search_entropy"))
        object.__setattr__(self, "reasons", reasons)

    def to_dict(self) -> dict:
        return {
            "version": CANDIDATE_POOL_VERSION,
            "case_id": self.case_id,
            "arm": self.arm,
            "query_digest": self.query_digest,
            "routing_receipt_id": self.routing_receipt_id,
            "routing_decision": self.routing_decision,
            "candidate_budget": self.candidate_budget,
            "candidate_ids": list(self.candidate_ids),
            "candidate_sources": list(self.candidate_sources),
            "no_memory_candidate_ids": list(self.no_memory_candidate_ids),
            "memory_candidate_ids": list(self.memory_candidate_ids),
            "unique_action_families": list(self.unique_action_families),
            "unique_mechanism_hypotheses": list(self.unique_mechanism_hypotheses),
            "candidate_diversity": self.candidate_diversity,
            "search_entropy": self.search_entropy,
            "memory_admitted": self.memory_admitted,
            "reasons": list(self.reasons),
        }

    @property
    def receipt_digest(self) -> str:
        return "sha256:" + hashlib.sha256(
            stable_dumps(self.to_dict()).encode()).hexdigest()

    @classmethod
    def from_dict(cls, payload: object) -> "CandidatePoolReceipt":
        if not isinstance(payload, Mapping):
            raise CandidatePoolError("candidate pool receipt must be an object")
        if payload.get("version", CANDIDATE_POOL_VERSION) != CANDIDATE_POOL_VERSION:
            raise CandidatePoolError("candidate pool receipt version is unsupported")
        required = {
            "case_id", "arm", "query_digest", "routing_receipt_id",
            "routing_decision", "candidate_budget", "candidate_ids",
            "candidate_sources", "no_memory_candidate_ids",
            "memory_candidate_ids", "unique_action_families",
            "unique_mechanism_hypotheses", "candidate_diversity",
            "search_entropy", "memory_admitted", "reasons",
        }
        if any(key not in payload for key in required):
            raise CandidatePoolError("candidate pool receipt is missing fields")
        receipt = cls(
            case_id=payload["case_id"], arm=payload["arm"],
            query_digest=payload["query_digest"],
            routing_receipt_id=payload["routing_receipt_id"],
            routing_decision=payload["routing_decision"],
            candidate_budget=payload["candidate_budget"],
            candidate_ids=tuple(payload["candidate_ids"]),
            candidate_sources=tuple(payload["candidate_sources"]),
            no_memory_candidate_ids=tuple(payload["no_memory_candidate_ids"]),
            memory_candidate_ids=tuple(payload["memory_candidate_ids"]),
            unique_action_families=tuple(payload["unique_action_families"]),
            unique_mechanism_hypotheses=tuple(payload["unique_mechanism_hypotheses"]),
            candidate_diversity=payload["candidate_diversity"],
            search_entropy=payload["search_entropy"],
            memory_admitted=payload["memory_admitted"],
            reasons=tuple(payload["reasons"]),
        )
        supplied = payload.get("receipt_digest")
        if supplied is not None and supplied != receipt.receipt_digest:
            raise CandidatePoolError("candidate pool receipt digest mismatch")
        return receipt


@dataclass(frozen=True)
class CandidatePool:
    """Selected candidates plus their immutable composition receipt."""

    candidates: tuple[MemoryCandidate, ...]
    receipt: CandidatePoolReceipt

    def __post_init__(self) -> None:
        values = tuple(self.candidates)
        if len(values) != len(self.receipt.candidate_ids):
            raise CandidatePoolError("candidate pool result/receipt cardinality mismatch")
        if tuple(item.candidate_id for item in values) != self.receipt.candidate_ids:
            raise CandidatePoolError("candidate pool result/receipt IDs differ")
        if tuple(item.source for item in values) != self.receipt.candidate_sources:
            raise CandidatePoolError("candidate pool result/receipt sources differ")
        object.__setattr__(self, "candidates", values)

    def to_dict(self) -> dict:
        """Serialize the pool for an evaluation artifact without execution."""
        return {
            "receipt": {**self.receipt.to_dict(),
                        "receipt_digest": self.receipt.receipt_digest},
            "candidates": [
                {
                    "candidate_id": candidate.candidate_id,
                    "source": candidate.source,
                    "payload": candidate.payload,
                    "score": candidate.score,
                    "provenance": candidate.provenance,
                }
                for candidate in self.candidates
            ],
        }


def _routing_refs(routing: MemoryRoutingDecision | None) -> set[str]:
    if routing is None:
        return set()
    return set(routing.selected_rule_ids) | set(routing.selected_path_ids) | set(routing.selected_asset_ids)


def _admit_memory(
        arm: str, candidate: MemoryCandidate,
        routing: MemoryRoutingDecision | None) -> tuple[bool, str]:
    if arm == "NO_MEMORY":
        return False, "no_memory_arm"
    if arm == "ALWAYS_MEMORY":
        return True, "always_memory_arm"
    if arm == "APPLICABILITY_GATED":
        applicable = _candidate_applicable(candidate)
        return applicable, "applicability_pass" if applicable else "applicability_unresolved"
    if routing is None:
        return False, "routing_receipt_required"
    if routing.decision not in {"APPLY", "CONSIDER"}:
        return False, f"routing_{routing.decision.lower()}"
    if routing.memory_budget < 1:
        return False, "routing_memory_budget_zero"
    if routing.causal_support.get("status") != "SUPPORTED":
        return False, "causal_support_insufficient"
    if not routing.selected_path_ids:
        return False, "causal_path_required"
    if not _candidate_applicable(candidate):
        return False, "applicability_unresolved"
    if not (_candidate_reference_ids(candidate) & _routing_refs(routing)):
        return False, "candidate_not_selected_by_router"
    return True, "causal_and_no_skill_gate_pass"


def build_candidate_pool(
        query: MemoryQuery,
        no_memory_candidates: Sequence[MemoryCandidate],
        memory_candidates: Sequence[MemoryCandidate],
        *,
        arm: str = "CAUSAL_NO_SKILL",
        routing: MemoryRoutingDecision | None = None,
        candidate_budget: int = 3,
        case_id: str | None = None,
) -> CandidatePool:
    """Compose one fixed-budget A/B pool without executing it.

    All arms reserve at least one no-memory candidate.  Even the deliberately
    unsafe ``ALWAYS_MEMORY`` arm can contribute at most one memory candidate;
    this keeps P6 comparable and prevents a shadow experiment from silently
    becoming a 3/3 memory-only runtime policy.
    """
    canonical_arm = _canonical_arm(arm)
    if type(candidate_budget) is not int or candidate_budget < 1:
        raise CandidatePoolError("candidate pool candidate_budget must be positive")
    if routing is not None and not isinstance(routing, MemoryRoutingDecision):
        raise TypeError("candidate pool routing must be MemoryRoutingDecision")
    no_memory = _normalise_candidates(no_memory_candidates, "no_memory_candidates")
    memory = _normalise_candidates(memory_candidates, "memory_candidates")
    overlap = {candidate.candidate_id for candidate in no_memory} & {
        candidate.candidate_id for candidate in memory}
    if overlap:
        raise CandidatePoolError(
            "candidate pool no-memory and memory candidates overlap")
    if any(candidate.source == "cold_start" for candidate in memory):
        raise CandidatePoolError(
            "candidate pool memory candidates cannot use cold_start source")
    query_id = _query_digest(query)
    selected_memory: tuple[MemoryCandidate, ...] = ()
    reasons: list[str] = []
    for candidate in memory:
        admitted, reason = _admit_memory(canonical_arm, candidate, routing)
        if admitted:
            selected_memory = (candidate,)
            reasons.append(reason)
            break
        reasons.append(f"{candidate.candidate_id}:{reason}")

    # Reserve one unbiased no-memory slot before admitting the advisor.  When
    # memory is eligible, leave exactly one slot for it (up to the hard
    # advisor limit); when it is not, fill the whole budget with no-memory
    # candidates.  Thus an abundant no-memory pool cannot accidentally turn
    # ``ALWAYS_MEMORY`` or ``GATED_MEMORY`` into a no-memory-only arm.
    if selected_memory and candidate_budget >= 2:
        selected_no_memory = no_memory[:candidate_budget - 1]
        selected_memory = selected_memory[:MAX_MEMORY_ADVISOR_CANDIDATES]
    else:
        selected_no_memory = no_memory[:candidate_budget]
        if selected_memory:
            selected_memory = ()
            reasons.append("candidate_budget_reserved_for_no_memory")
    selected = (*selected_no_memory, *selected_memory)
    if not selected_no_memory:
        raise CandidatePoolError("candidate pool requires at least one no-memory candidate")

    families = tuple(sorted({_candidate_action_family(candidate) for candidate in selected}))
    mechanisms = tuple(sorted({_candidate_mechanism_hypothesis(candidate) for candidate in selected}))
    diversity = (len(families) / len(selected)) if selected else 0.0
    entropy = _normalised_entropy(tuple(_candidate_action_family(candidate) for candidate in selected))
    receipt = CandidatePoolReceipt(
        case_id=_text(case_id or query.context_ref or query_id, "case_id"),
        arm=canonical_arm, query_digest=query_id,
        routing_receipt_id=routing.routing_receipt_id if routing else None,
        routing_decision=routing.decision if routing else None,
        candidate_budget=candidate_budget,
        candidate_ids=tuple(candidate.candidate_id for candidate in selected),
        candidate_sources=tuple(candidate.source for candidate in selected),
        no_memory_candidate_ids=tuple(candidate.candidate_id for candidate in selected_no_memory),
        memory_candidate_ids=tuple(candidate.candidate_id for candidate in selected_memory),
        unique_action_families=families,
        unique_mechanism_hypotheses=mechanisms,
        candidate_diversity=diversity, search_entropy=entropy,
        memory_admitted=bool(selected_memory), reasons=tuple(reasons),
    )
    return CandidatePool(candidates=tuple(selected), receipt=receipt)


@dataclass(frozen=True)
class CandidatePoolOutcome:
    """Explicit oracle outcomes used for P6 interference metrics."""

    case_id: str
    arm: str
    no_memory_outcome: str
    memory_outcome: str
    routing_decision: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "arm", _canonical_arm(self.arm))
        _text(self.case_id, "case_id")
        if self.no_memory_outcome not in POOL_OUTCOMES:
            raise CandidatePoolError("candidate pool no_memory_outcome is invalid")
        if self.memory_outcome not in POOL_OUTCOMES:
            raise CandidatePoolError("candidate pool memory_outcome is invalid")
        if self.routing_decision is not None and self.routing_decision not in MEMORY_ROUTING_DECISIONS:
            raise CandidatePoolError("candidate pool outcome routing_decision is invalid")

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id, "arm": self.arm,
            "no_memory_outcome": self.no_memory_outcome,
            "memory_outcome": self.memory_outcome,
            "routing_decision": self.routing_decision,
        }


@dataclass(frozen=True)
class CandidatePoolMetrics:
    """Aggregate, denominator-explicit P6 metrics for one arm."""

    arm: str
    cases: int
    paired_cases: int
    no_memory_passes: int
    memory_passes: int
    memory_interference_cases: int
    memory_interference_rate: float | None
    abstain_cases: int
    abstention_utility: float | None
    memory_harm_cases: int
    candidate_budget_efficiency: float | None
    candidate_diversity: float | None
    search_entropy: float | None
    memory_admitted_cases: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "arm", _canonical_arm(self.arm))
        if type(self.cases) is not int or self.cases < 0:
            raise CandidatePoolError("candidate pool metrics cases must be non-negative")
        if type(self.paired_cases) is not int or self.paired_cases < 0 or self.paired_cases > self.cases:
            raise CandidatePoolError("candidate pool metrics paired_cases is invalid")
        for field_name in (
                "no_memory_passes", "memory_passes", "memory_interference_cases",
                "abstain_cases", "memory_harm_cases", "memory_admitted_cases"):
            value = getattr(self, field_name)
            if type(value) is not int or value < 0 or value > self.cases:
                raise CandidatePoolError(f"candidate pool metrics {field_name} is invalid")
        for field_name in (
                "memory_interference_rate", "abstention_utility",
                "candidate_budget_efficiency", "candidate_diversity", "search_entropy"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _finite_unit(value, field_name))

    def to_dict(self) -> dict:
        return {
            "version": CANDIDATE_POOL_VERSION,
            "arm": self.arm, "cases": self.cases,
            "paired_cases": self.paired_cases,
            "no_memory_passes": self.no_memory_passes,
            "memory_passes": self.memory_passes,
            "memory_interference_cases": self.memory_interference_cases,
            "memory_interference_rate": self.memory_interference_rate,
            "abstain_cases": self.abstain_cases,
            "abstention_utility": self.abstention_utility,
            "memory_harm_cases": self.memory_harm_cases,
            "candidate_budget_efficiency": self.candidate_budget_efficiency,
            "candidate_diversity": self.candidate_diversity,
            "search_entropy": self.search_entropy,
            "memory_admitted_cases": self.memory_admitted_cases,
        }


def summarize_candidate_pool(
        receipts: Sequence[CandidatePoolReceipt],
        outcomes: Sequence[CandidatePoolOutcome] = (),
) -> CandidatePoolMetrics:
    """Summarize explicit receipts without treating unknown outcomes as pass."""
    if isinstance(receipts, (str, bytes)) or not isinstance(receipts, Sequence):
        raise CandidatePoolError("candidate pool receipts must be a sequence")
    rows = tuple(receipts)
    if any(not isinstance(row, CandidatePoolReceipt) for row in rows):
        raise TypeError("candidate pool receipts must contain CandidatePoolReceipt")
    if not rows:
        raise CandidatePoolError("candidate pool metrics require at least one receipt")
    arms = {row.arm for row in rows}
    if len(arms) != 1:
        raise CandidatePoolError("candidate pool metrics require one arm")
    receipt_case_ids = [row.case_id for row in rows]
    if len(set(receipt_case_ids)) != len(receipt_case_ids):
        raise CandidatePoolError("candidate pool receipts contain duplicate case IDs")
    outcome_rows = tuple(outcomes)
    if any(not isinstance(row, CandidatePoolOutcome) for row in outcome_rows):
        raise TypeError("candidate pool outcomes must contain CandidatePoolOutcome")
    by_case = {row.case_id: row for row in outcome_rows}
    if len(by_case) != len(outcome_rows):
        raise CandidatePoolError("candidate pool outcomes contain duplicate case IDs")
    if any(case_id not in {row.case_id for row in rows} for case_id in by_case):
        raise CandidatePoolError("candidate pool outcome has no matching receipt")
    arm = next(iter(arms))
    if any(row.arm != arm for row in outcome_rows):
        raise CandidatePoolError("candidate pool outcome arm does not match receipt arm")
    matched = [by_case[row.case_id] for row in rows if row.case_id in by_case]
    no_pass = sum(row.no_memory_outcome in POSITIVE_OUTCOMES for row in matched)
    mem_pass = sum(row.memory_outcome in POSITIVE_OUTCOMES for row in matched)
    interference = sum(
        row.no_memory_outcome == "PASS" and row.memory_outcome in HARMFUL_OUTCOMES
        for row in matched)
    abstains = sum(row.memory_outcome == "ABSTAIN" for row in matched)
    abstention_pass = sum(
        row.memory_outcome == "ABSTAIN" and row.no_memory_outcome == "PASS"
        for row in matched)
    harm = sum(
        row.no_memory_outcome in POSITIVE_OUTCOMES and
        row.memory_outcome in HARMFUL_OUTCOMES for row in matched)
    paired = [row for row in matched
              if row.no_memory_outcome in OUTCOMES and
              row.memory_outcome in OUTCOMES]
    admitted = sum(bool(row.memory_candidate_ids) for row in rows)
    efficiency = sum(len(row.candidate_ids) / row.candidate_budget for row in rows) / len(rows)
    diversity = sum(row.candidate_diversity for row in rows) / len(rows)
    entropy = sum(row.search_entropy for row in rows) / len(rows)
    return CandidatePoolMetrics(
        arm=arm, cases=len(rows), paired_cases=len(paired), no_memory_passes=no_pass,
        memory_passes=mem_pass, memory_interference_cases=interference,
        memory_interference_rate=(interference / len(paired) if paired else None),
        abstain_cases=abstains,
        abstention_utility=(abstention_pass / abstains if abstains else None),
        memory_harm_cases=harm,
        candidate_budget_efficiency=efficiency,
        candidate_diversity=diversity, search_entropy=entropy,
        memory_admitted_cases=admitted)


__all__ = [
    "CANDIDATE_POOL_ARMS", "CANDIDATE_POOL_VERSION",
    "MAX_MEMORY_ADVISOR_CANDIDATES", "POOL_OUTCOMES", "CandidatePoolError",
    "CandidatePoolReceipt", "CandidatePool", "build_candidate_pool",
    "CandidatePoolOutcome", "CandidatePoolMetrics", "summarize_candidate_pool",
]
