"""Replayable P6 candidate-pool evidence for the Revision3 P15-B gate.

The production gate needs a paired candidate-pool denominator and a measured
diversity value.  A routed P12 cohort alone cannot provide either: its
execution receipt does not contain the complete pool composition.  This
module therefore accepts an explicit, evaluation-only pool witness for every
case and joins it to a typed RTL cohort.  Every candidate payload and query
digest is replayed; summary scalars are never treated as authority.

The resulting report is still an evaluation receipt.  It cannot write a
canonical store, change lifecycle authority, or enable a production runtime.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from contracts import MemoryCandidate, MemoryQuery
from tehm.ids import stable_dumps
from tehm.retrieval.candidate_pool import (
    CandidatePool, CandidatePoolError,
    CandidatePoolOutcome, CandidatePoolReceipt,
    _candidate_action_family, _candidate_mechanism_hypothesis,
    _normalised_entropy, summarize_candidate_pool,
)

from .rtl_cohort import RtlPairedCohortReceipt


CANDIDATE_POOL_EVIDENCE_VERSION = "r3-candidate-pool-evidence-v1"
_POLICY_ARMS = frozenset({"ALWAYS_MEMORY", "APPLICABILITY_GATED", "CAUSAL_NO_SKILL"})
_GOLD_KEYS = frozenset({"fix", "gold_patch", "repaired_rtl", "heldout_answer"})


class CandidatePoolEvidenceError(ValueError):
    """A candidate-pool witness cannot be safely replayed."""


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _file_digest(path: Path) -> str:
    try:
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise CandidatePoolEvidenceError(
            f"candidate-pool cohort is unreadable: {path}") from exc


def _load(path: Path, label: str) -> dict:
    try:
        payload = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CandidatePoolEvidenceError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(payload, Mapping):
        raise CandidatePoolEvidenceError(f"{label} must be an object: {path}")
    return dict(payload)


def _path(raw: object, *, base: Path, label: str) -> Path:
    if type(raw) is not str or not raw.strip():
        raise CandidatePoolEvidenceError(f"{label} path is required")
    value = Path(raw).expanduser()
    return (value if value.is_absolute() else base / value).resolve()


def _contains_gold(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(key in _GOLD_KEYS or _contains_gold(item)
                   for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_gold(item) for item in value)
    return False


def _candidate(raw: object, *, case_id: str) -> MemoryCandidate:
    if not isinstance(raw, Mapping):
        raise CandidatePoolEvidenceError(
            f"candidate-pool candidate is malformed: {case_id}")
    required = {"candidate_id", "source", "payload"}
    if not required <= set(raw):
        raise CandidatePoolEvidenceError(
            f"candidate-pool candidate fields are incomplete: {case_id}")
    payload = raw.get("payload")
    provenance = raw.get("provenance", {})
    if not isinstance(payload, Mapping) or not isinstance(provenance, Mapping):
        raise CandidatePoolEvidenceError(
            f"candidate-pool candidate payload is malformed: {case_id}")
    if _contains_gold(payload) or _contains_gold(provenance):
        raise CandidatePoolEvidenceError(
            f"candidate-pool candidate contains gold-answer fields: {case_id}")
    try:
        candidate = MemoryCandidate(
            candidate_id=raw["candidate_id"], source=raw["source"],
            payload=dict(payload), score=raw.get("score"),
            provenance=dict(provenance))
        candidate.validate()
    except (TypeError, ValueError) as exc:
        raise CandidatePoolEvidenceError(
            f"candidate-pool candidate cannot replay: {case_id}") from exc
    return candidate


def _query(raw: object, *, case_id: str) -> MemoryQuery:
    if not isinstance(raw, Mapping):
        raise CandidatePoolEvidenceError(
            f"candidate-pool query is malformed: {case_id}")
    query_plan = raw.get("query_plan", {})
    dimensions = raw.get("dominant_dimensions", {})
    if not isinstance(query_plan, Mapping) or not isinstance(dimensions, Mapping):
        raise CandidatePoolEvidenceError(
            f"candidate-pool query fields are malformed: {case_id}")
    try:
        query = MemoryQuery(query_plan=dict(query_plan),
                            dominant_dimensions=dict(dimensions),
                            context_ref=raw.get("context_ref"))
        digest = "sha256:" + hashlib.sha256(
            stable_dumps(query.to_dict()).encode()).hexdigest()
    except (TypeError, ValueError) as exc:
        raise CandidatePoolEvidenceError(
            f"candidate-pool query cannot replay: {case_id}") from exc
    if raw.get("query_digest") != digest:
        raise CandidatePoolEvidenceError(
            f"candidate-pool query digest mismatch: {case_id}")
    return query


def _replay_pool(raw: object, *, case_id: str, cohort_bundle,
                 expected_arm: str, expected_budget: int) -> CandidatePoolReceipt:
    if not isinstance(raw, Mapping):
        raise CandidatePoolEvidenceError(
            f"candidate-pool entry is malformed: {case_id}")
    receipt_payload = raw.get("receipt")
    if not isinstance(receipt_payload, Mapping):
        raise CandidatePoolEvidenceError(
            f"candidate-pool receipt is missing: {case_id}")
    try:
        receipt = CandidatePoolReceipt.from_dict(receipt_payload)
    except (CandidatePoolError, TypeError, ValueError) as exc:
        raise CandidatePoolEvidenceError(
            f"candidate-pool receipt cannot replay: {case_id}") from exc
    if receipt_payload.get("receipt_digest") != receipt.receipt_digest:
        raise CandidatePoolEvidenceError(
            f"candidate-pool receipt digest mismatch: {case_id}")
    if receipt.case_id != case_id or receipt.arm != expected_arm:
        raise CandidatePoolEvidenceError(
            f"candidate-pool case/arm binding mismatch: {case_id}")
    if receipt.candidate_budget != expected_budget:
        raise CandidatePoolEvidenceError(
            f"candidate-pool budget binding mismatch: {case_id}")
    if receipt.routing_receipt_id != cohort_bundle.routing_receipt_id or \
            receipt.routing_decision != cohort_bundle.routing_decision:
        raise CandidatePoolEvidenceError(
            f"candidate-pool route binding mismatch: {case_id}")
    query = _query(raw.get("query"), case_id=case_id)
    candidates_raw = raw.get("candidates")
    if isinstance(candidates_raw, (str, bytes)) or \
            not isinstance(candidates_raw, Sequence) or not candidates_raw:
        raise CandidatePoolEvidenceError(
            f"candidate-pool selected candidates are missing: {case_id}")
    try:
        candidates = tuple(_candidate(item, case_id=case_id)
                           for item in candidates_raw)
        CandidatePool(candidates=candidates, receipt=receipt)
    except CandidatePoolEvidenceError:
        raise
    except (CandidatePoolError, TypeError, ValueError) as exc:
        raise CandidatePoolEvidenceError(
            f"candidate-pool candidates do not match receipt: {case_id}") from exc
    expected_ids = tuple(candidate.candidate_id for candidate in candidates)
    expected_sources = tuple(candidate.source for candidate in candidates)
    families = tuple(sorted({_candidate_action_family(candidate)
                             for candidate in candidates}))
    mechanisms = tuple(sorted({_candidate_mechanism_hypothesis(candidate)
                               for candidate in candidates}))
    family_values = tuple(_candidate_action_family(candidate)
                          for candidate in candidates)
    expected_diversity = round(len(families) / len(candidates), 6)
    expected_entropy = _normalised_entropy(family_values)
    if (receipt.candidate_ids != expected_ids or
            receipt.candidate_sources != expected_sources or
            receipt.unique_action_families != families or
            receipt.unique_mechanism_hypotheses != mechanisms or
            receipt.candidate_diversity != expected_diversity or
            receipt.search_entropy != expected_entropy):
        raise CandidatePoolEvidenceError(
            f"candidate-pool composition metrics drifted: {case_id}")
    policy_receipt = cohort_bundle.arm_receipts[expected_arm]
    memory_ids = set(receipt.memory_candidate_ids)
    if policy_receipt.source == "structured_memory":
        if memory_ids != {policy_receipt.candidate_id}:
            raise CandidatePoolEvidenceError(
                f"candidate-pool selected candidate disagrees with execution: {case_id}")
    elif policy_receipt.source == "no_memory":
        if memory_ids:
            raise CandidatePoolEvidenceError(
                f"candidate-pool fallback admits memory: {case_id}")
    else:
        raise CandidatePoolEvidenceError(
            f"candidate-pool policy source is invalid: {case_id}")
    return receipt


def _replay_candidate_pool_components(raw: Mapping, *, base: Path):
    """Replay one per-cohort envelope and expose typed rows to an aggregator."""
    if not isinstance(raw, Mapping):
        raise CandidatePoolEvidenceError("candidate-pool evidence must be an object")
    if raw.get("version") != CANDIDATE_POOL_EVIDENCE_VERSION or \
            raw.get("metric") != "candidate_pool":
        raise CandidatePoolEvidenceError("candidate-pool evidence version/metric mismatch")
    if raw.get("evaluation_only") is not True or \
            raw.get("canonical_memory_mutation") != "none" or \
            raw.get("production_integration") != "not_attempted":
        raise CandidatePoolEvidenceError(
            "candidate-pool evidence crosses an authority boundary")
    policy_arm = raw.get("policy_arm")
    if policy_arm not in _POLICY_ARMS:
        raise CandidatePoolEvidenceError("candidate-pool policy arm is invalid")
    cohort_path = _path(raw.get("cohort_receipt"), base=Path(base).resolve(),
                        label="candidate-pool cohort")
    if raw.get("cohort_receipt_sha256") != _file_digest(cohort_path):
        raise CandidatePoolEvidenceError("candidate-pool cohort digest mismatch")
    cohort_payload = _load(cohort_path, "candidate-pool cohort")
    try:
        cohort = RtlPairedCohortReceipt.from_dict(cohort_payload)
    except (TypeError, ValueError) as exc:
        raise CandidatePoolEvidenceError(
            "candidate-pool cohort cannot replay") from exc
    if cohort_payload.get("receipt_digest") != cohort.receipt_digest or \
            raw.get("cohort_receipt_digest") != cohort.receipt_digest:
        raise CandidatePoolEvidenceError("candidate-pool cohort receipt binding drifted")
    if cohort.evaluation_only is not True or cohort.source_disjoint is not True or \
            cohort.source_restore_verified is not True:
        raise CandidatePoolEvidenceError(
            "candidate-pool cohort is not evaluation-only/source-disjoint")
    if cohort.candidate_budget < 1:
        raise CandidatePoolEvidenceError("candidate-pool cohort budget is invalid")
    entries = raw.get("pools")
    if isinstance(entries, (str, bytes)) or not isinstance(entries, Sequence) or not entries:
        raise CandidatePoolEvidenceError("candidate-pool entries are missing")
    by_case = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise CandidatePoolEvidenceError("candidate-pool entry is malformed")
        receipt = entry.get("receipt")
        case_id = receipt.get("case_id") if isinstance(receipt, Mapping) else None
        if type(case_id) is not str or case_id in by_case:
            raise CandidatePoolEvidenceError("candidate-pool case coverage is duplicated")
        by_case[case_id] = entry
    if set(by_case) != set(cohort.case_receipts):
        raise CandidatePoolEvidenceError(
            "candidate-pool case coverage does not match cohort")
    receipts = []
    outcomes = []
    for case_id in sorted(cohort.case_receipts):
        bundle = cohort.case_receipts[case_id]
        receipt = _replay_pool(
            by_case[case_id], case_id=case_id, cohort_bundle=bundle,
            expected_arm=policy_arm, expected_budget=cohort.candidate_budget)
        receipts.append(receipt)
        baseline = bundle.arm_receipts["NO_MEMORY"]
        policy = bundle.arm_receipts[policy_arm]
        outcomes.append(CandidatePoolOutcome(
            case_id=case_id, arm=policy_arm,
            no_memory_outcome=baseline.outcome,
            memory_outcome=policy.outcome,
            routing_decision=bundle.routing_decision))
    try:
        metrics = summarize_candidate_pool(receipts, outcomes).to_dict()
    except (CandidatePoolError, TypeError, ValueError) as exc:
        raise CandidatePoolEvidenceError(
            "candidate-pool metrics cannot be derived") from exc
    if raw.get("metrics") != metrics:
        raise CandidatePoolEvidenceError("candidate-pool aggregate metrics drifted")
    unsigned = dict(raw)
    unsigned.pop("receipt_digest", None)
    if raw.get("receipt_digest") != _digest(unsigned):
        raise CandidatePoolEvidenceError("candidate-pool evidence receipt digest mismatch")
    return cohort, tuple(receipts), tuple(outcomes), metrics


def replay_candidate_pool_evidence(raw: Mapping, *, base: Path) -> dict:
    """Replay a per-cohort or multi-cohort candidate-pool evidence envelope."""
    if isinstance(raw, Mapping) and raw.get("version") == "r3-candidate-pool-aggregate-v1":
        # Keep the aggregate dependency lazy: the per-cohort builder remains
        # usable without importing the aggregate module, while production
        # readiness has one stable replay entry point for both forms.
        from .candidate_pool_aggregate import replay_candidate_pool_aggregate
        return replay_candidate_pool_aggregate(raw, base=base)
    _, _, _, metrics = _replay_candidate_pool_components(raw, base=base)
    return {**metrics, "source": "typed_candidate_pool",
            "cohort_receipt_digest": raw["cohort_receipt_digest"],
            "receipt_digest": raw["receipt_digest"]}


def build_candidate_pool_evidence(
        *, cohort_receipt: Path, policy_arm: str, pools: Sequence[Mapping],
        output: Path | None = None) -> dict:
    """Build a content-addressed candidate-pool evidence envelope.

    ``pools`` must already contain explicit typed receipts and candidate
    payloads.  This builder never invents candidate composition; it calls the
    same replay path used later by production readiness.
    """
    cohort_receipt = Path(cohort_receipt).expanduser().resolve()
    payload = _load(cohort_receipt, "candidate-pool cohort")
    try:
        cohort = RtlPairedCohortReceipt.from_dict(payload)
    except (TypeError, ValueError) as exc:
        raise CandidatePoolEvidenceError(
            "candidate-pool cohort cannot replay") from exc
    if payload.get("receipt_digest") != cohort.receipt_digest:
        raise CandidatePoolEvidenceError("candidate-pool cohort receipt digest mismatch")
    report = {
        "version": CANDIDATE_POOL_EVIDENCE_VERSION,
        "metric": "candidate_pool", "policy_arm": policy_arm,
        "cohort_receipt": str(cohort_receipt),
        "cohort_receipt_sha256": _file_digest(cohort_receipt),
        "cohort_receipt_digest": cohort.receipt_digest,
        "pools": [dict(item) for item in pools],
        "evaluation_only": True, "canonical_memory_mutation": "none",
        "production_integration": "not_attempted",
    }
    # Build the typed metrics from the explicit entries before adding the
    # aggregate digest.  Replay requires an exact equality with this payload.
    entries = report["pools"]
    by_case = {}
    for entry in entries:
        receipt = entry.get("receipt") if isinstance(entry, Mapping) else None
        case_id = receipt.get("case_id") if isinstance(receipt, Mapping) else None
        by_case[case_id] = entry
    if set(by_case) != set(cohort.case_receipts):
        raise CandidatePoolEvidenceError("candidate-pool case coverage does not match cohort")
    typed_receipts = []
    typed_outcomes = []
    for case_id in sorted(cohort.case_receipts):
        bundle = cohort.case_receipts[case_id]
        typed_receipts.append(_replay_pool(
            by_case[case_id], case_id=case_id, cohort_bundle=bundle,
            expected_arm=policy_arm, expected_budget=cohort.candidate_budget))
        typed_outcomes.append(CandidatePoolOutcome(
            case_id=case_id, arm=policy_arm,
            no_memory_outcome=bundle.arm_receipts["NO_MEMORY"].outcome,
            memory_outcome=bundle.arm_receipts[policy_arm].outcome,
            routing_decision=bundle.routing_decision))
    try:
        report["metrics"] = summarize_candidate_pool(
            typed_receipts, typed_outcomes).to_dict()
    except (CandidatePoolError, TypeError, ValueError) as exc:
        raise CandidatePoolEvidenceError("candidate-pool metrics cannot be derived") from exc
    report["receipt_digest"] = _digest(report)
    if output is not None:
        output = Path(output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


__all__ = [
    "CANDIDATE_POOL_EVIDENCE_VERSION", "CandidatePoolEvidenceError",
    "build_candidate_pool_evidence", "replay_candidate_pool_evidence",
]
