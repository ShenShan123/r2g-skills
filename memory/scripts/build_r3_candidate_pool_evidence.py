#!/usr/bin/env python3
"""Build typed P6 candidate-pool evidence for a frozen RTL cohort."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts import MemoryCandidate  # noqa: E402
from tehm.evaluation.candidate_pool_evidence import (  # noqa: E402
    CandidatePoolEvidenceError, build_candidate_pool_evidence,
)
from tehm.evaluation.rtl_cohort import RtlPairedCohortReceipt  # noqa: E402
from tehm.ids import stable_dumps  # noqa: E402
from tehm.retrieval.candidate_pool import (  # noqa: E402
    CandidatePoolError, CandidatePoolReceipt, _candidate_action_family,
    _candidate_mechanism_hypothesis, _normalised_entropy,
)


DESCRIPTOR_VERSION = "r3-candidate-pool-descriptor-v1"


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _load(path: Path, label: str) -> dict:
    try:
        payload = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CandidatePoolEvidenceError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(payload, Mapping):
        raise CandidatePoolEvidenceError(f"{label} must be an object: {path}")
    return dict(payload)


def _candidate(raw: object, *, case_id: str) -> MemoryCandidate:
    if not isinstance(raw, Mapping):
        raise CandidatePoolEvidenceError(f"candidate descriptor is malformed: {case_id}")
    try:
        candidate = MemoryCandidate(
            candidate_id=raw["candidate_id"], source=raw["source"],
            payload=dict(raw["payload"]), score=raw.get("score"),
            provenance=dict(raw.get("provenance", {})))
        candidate.validate()
    except (KeyError, TypeError, ValueError) as exc:
        raise CandidatePoolEvidenceError(
            f"candidate descriptor cannot replay: {case_id}") from exc
    return candidate


def _entry(case_id: str, descriptor: Mapping, bundle, budget: int,
           policy_arm: str) -> dict:
    if not isinstance(descriptor, Mapping):
        raise CandidatePoolEvidenceError(f"candidate descriptor is malformed: {case_id}")
    query = descriptor.get("query")
    if not isinstance(query, Mapping):
        raise CandidatePoolEvidenceError(f"candidate query is missing: {case_id}")
    query = dict(query)
    query.setdefault("context_ref", case_id)
    query["query_digest"] = _digest({
        "query_plan": query.get("query_plan", {}),
        "dominant_dimensions": query.get("dominant_dimensions", {}),
        "context_ref": query.get("context_ref"),
    })
    raw_candidates = descriptor.get("candidates")
    if isinstance(raw_candidates, (str, bytes)) or \
            not isinstance(raw_candidates, Sequence) or not raw_candidates:
        raise CandidatePoolEvidenceError(f"candidate list is missing: {case_id}")
    candidates = tuple(_candidate(item, case_id=case_id)
                       for item in raw_candidates)
    ids = tuple(item.candidate_id for item in candidates)
    sources = tuple(item.source for item in candidates)
    no_ids = tuple(item.candidate_id for item in candidates
                   if item.source == "cold_start")
    memory_ids = tuple(item.candidate_id for item in candidates
                       if item.source != "cold_start")
    families = tuple(sorted({_candidate_action_family(item) for item in candidates}))
    mechanisms = tuple(sorted({_candidate_mechanism_hypothesis(item) for item in candidates}))
    diversity = round(len(families) / len(candidates), 6)
    entropy = _normalised_entropy(tuple(_candidate_action_family(item)
                                        for item in candidates))
    try:
        receipt = CandidatePoolReceipt(
            case_id=case_id, arm=policy_arm,
            query_digest=query["query_digest"],
            routing_receipt_id=bundle.routing_receipt_id,
            routing_decision=bundle.routing_decision,
            candidate_budget=budget, candidate_ids=ids,
            candidate_sources=sources, no_memory_candidate_ids=no_ids,
            memory_candidate_ids=memory_ids,
            unique_action_families=families,
            unique_mechanism_hypotheses=mechanisms,
            candidate_diversity=diversity, search_entropy=entropy,
            memory_admitted=bool(memory_ids),
            reasons=tuple(descriptor.get("reasons", ())),
            no_skill_reason=descriptor.get("no_skill_reason"),
            state_shift_receipt_id=descriptor.get("state_shift_receipt_id"),
            risk_receipt_id=descriptor.get("risk_receipt_id"),
            risk_receipt=descriptor.get("risk_receipt"),
        )
    except (CandidatePoolError, KeyError, TypeError, ValueError) as exc:
        raise CandidatePoolEvidenceError(
            f"candidate-pool receipt cannot be built: {case_id}") from exc
    return {
        "query": query,
        "receipt": {**receipt.to_dict(), "receipt_digest": receipt.receipt_digest},
        "candidates": [
            {"candidate_id": item.candidate_id, "source": item.source,
             "payload": item.payload, "score": item.score,
             "provenance": item.provenance}
            for item in candidates
        ],
    }


def build_from_descriptor(cohort_path: Path, descriptor_path: Path,
                          policy_arm: str, output: Path | None = None) -> dict:
    cohort_path = cohort_path.expanduser().resolve()
    descriptor_path = descriptor_path.expanduser().resolve()
    cohort_payload = _load(cohort_path, "RTL cohort")
    descriptors = _load(descriptor_path, "candidate descriptor manifest")
    if descriptors.get("version") != DESCRIPTOR_VERSION or \
            descriptors.get("policy_arm") != policy_arm:
        raise CandidatePoolEvidenceError(
            "candidate descriptor manifest version/arm mismatch")
    try:
        cohort = RtlPairedCohortReceipt.from_dict(cohort_payload)
    except (TypeError, ValueError) as exc:
        raise CandidatePoolEvidenceError("RTL cohort cannot replay") from exc
    if cohort_payload.get("receipt_digest") != cohort.receipt_digest:
        raise CandidatePoolEvidenceError("RTL cohort receipt digest mismatch")
    raw_cases = descriptors.get("cases")
    if not isinstance(raw_cases, Mapping) or set(raw_cases) != set(cohort.case_receipts):
        raise CandidatePoolEvidenceError("candidate descriptor case coverage mismatch")
    pools = [_entry(case_id, raw_cases[case_id], cohort.case_receipts[case_id],
                    cohort.candidate_budget, policy_arm)
             for case_id in sorted(cohort.case_receipts)]
    return build_candidate_pool_evidence(
        cohort_receipt=cohort_path, policy_arm=policy_arm, pools=pools,
        output=output)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--descriptor-manifest", type=Path, required=True)
    parser.add_argument("--policy-arm", default="CAUSAL_NO_SKILL",
                        choices=("ALWAYS_MEMORY", "APPLICABILITY_GATED",
                                 "CAUSAL_NO_SKILL"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = build_from_descriptor(args.cohort, args.descriptor_manifest,
                                       args.policy_arm, args.output)
    except (OSError, CandidatePoolEvidenceError) as exc:
        parser.error(str(exc))
    print(json.dumps({"receipt_digest": report["receipt_digest"],
                      "metrics": report["metrics"],
                      "output": str(args.output.expanduser().resolve())},
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
