"""Replayable aggregation for typed P6 candidate-pool evidence.

An individual candidate-pool receipt is bound to one frozen RTL cohort.  P15-B
needs the same denominator as a multi-cohort MIR aggregate, so this module
joins independently frozen per-cohort pool receipts and recomputes the
metrics from their typed rows.  It never trusts a caller-provided aggregate
scalar and never crosses the canonical-memory or production-runtime boundary.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from tehm.ids import stable_dumps
from tehm.retrieval.candidate_pool import (
    CandidatePoolError, summarize_candidate_pool,
)

from .candidate_pool_evidence import (
    CandidatePoolEvidenceError, _replay_candidate_pool_components,
)


CANDIDATE_POOL_AGGREGATE_VERSION = "r3-candidate-pool-aggregate-v1"


class CandidatePoolAggregateError(CandidatePoolEvidenceError):
    """A multi-cohort candidate-pool aggregate cannot be replayed safely."""


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _file_digest(path: Path) -> str:
    try:
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise CandidatePoolAggregateError(
            f"candidate-pool evidence is unreadable: {path}") from exc


def _load(path: Path, label: str) -> dict:
    try:
        payload = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CandidatePoolAggregateError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(payload, Mapping):
        raise CandidatePoolAggregateError(f"{label} must be an object: {path}")
    return dict(payload)


def _path(raw: object, *, base: Path) -> Path:
    if type(raw) is not str or not raw.strip():
        raise CandidatePoolAggregateError("candidate-pool evidence path is required")
    value = Path(raw).expanduser()
    return (value if value.is_absolute() else base / value).resolve()


def _digest_text(value: object, name: str) -> str:
    if type(value) is not str or not value.startswith("sha256:") or len(value) <= 7:
        raise CandidatePoolAggregateError(
            f"candidate-pool {name} must be a sha256 digest")
    return value


def _normalise_rows(refs: Sequence[Mapping], *, base: Path):
    if isinstance(refs, (str, bytes)) or not isinstance(refs, Sequence) or not refs:
        raise CandidatePoolAggregateError(
            "candidate_pool_receipts must be a non-empty sequence")
    rows = []
    seen_paths: set[Path] = set()
    seen_campaigns: set[str] = set()
    seen_cases: set[str] = set()
    seen_sources: set[str] = set()
    seen_lineages: set[str] = set()
    fixed_environment = None
    typed_receipts = []
    typed_outcomes = []

    for raw_ref in refs:
        if not isinstance(raw_ref, Mapping):
            raise CandidatePoolAggregateError(
                "candidate-pool evidence reference is malformed")
        evidence_path = _path(raw_ref.get("path"), base=base)
        if evidence_path in seen_paths:
            raise CandidatePoolAggregateError(
                "candidate-pool evidence references contain duplicates")
        seen_paths.add(evidence_path)
        file_sha256 = _file_digest(evidence_path)
        if raw_ref.get("sha256") != file_sha256:
            raise CandidatePoolAggregateError(
                f"candidate-pool evidence file digest mismatch: {evidence_path}")
        report = _load(evidence_path, "candidate-pool evidence")
        if report.get("version") != "r3-candidate-pool-evidence-v1":
            raise CandidatePoolAggregateError(
                f"candidate-pool evidence is not a per-cohort receipt: {evidence_path}")
        try:
            cohort, receipts, outcomes, metrics = _replay_candidate_pool_components(
                report, base=evidence_path.parent)
        except CandidatePoolEvidenceError as exc:
            raise CandidatePoolAggregateError(
                f"candidate-pool evidence cannot replay: {evidence_path}") from exc
        report_digest = report.get("receipt_digest")
        if raw_ref.get("receipt_digest") != report_digest:
            raise CandidatePoolAggregateError(
                f"candidate-pool evidence receipt binding drifted: {evidence_path}")
        if raw_ref.get("cohort_receipt_digest") != cohort.receipt_digest:
            raise CandidatePoolAggregateError(
                f"candidate-pool cohort binding drifted: {evidence_path}")
        if raw_ref.get("campaign_id") != cohort.campaign_id:
            raise CandidatePoolAggregateError(
                f"candidate-pool campaign binding drifted: {evidence_path}")
        expected_cases = len(cohort.case_receipts)
        if type(raw_ref.get("case_count")) is not int or raw_ref["case_count"] != expected_cases:
            raise CandidatePoolAggregateError(
                f"candidate-pool case count binding drifted: {evidence_path}")
        if cohort.evaluation_only is not True or cohort.source_disjoint is not True or \
                cohort.source_restore_verified is not True:
            raise CandidatePoolAggregateError(
                f"candidate-pool cohort is not evaluation-only/source-disjoint: {evidence_path}")
        if cohort.campaign_id in seen_campaigns:
            raise CandidatePoolAggregateError(
                "candidate-pool cohorts contain duplicate campaigns")
        seen_campaigns.add(cohort.campaign_id)
        environment = (cohort.toolchain_digest, cohort.oracle_digest,
                       cohort.platform_digest, cohort.pdk_digest,
                       cohort.candidate_budget)
        if fixed_environment is None:
            fixed_environment = environment
        elif environment != fixed_environment:
            raise CandidatePoolAggregateError(
                "candidate-pool cohort fixed environment drifted")
        for case_id, bundle in sorted(cohort.case_receipts.items()):
            if case_id in seen_cases:
                raise CandidatePoolAggregateError(
                    f"candidate-pool cohorts contain duplicate case IDs: {case_id}")
            seen_cases.add(case_id)
            source_digest = _digest_text(
                cohort.source_digests.get(case_id), "source_digest")
            if source_digest in seen_sources:
                raise CandidatePoolAggregateError(
                    f"candidate-pool cohorts contain overlapping source digests: {case_id}")
            seen_sources.add(source_digest)
            lineage_id = bundle.lineage_id
            if type(lineage_id) is not str or not lineage_id.strip():
                raise CandidatePoolAggregateError(
                    f"candidate-pool lineage is missing: {case_id}")
            if lineage_id in seen_lineages:
                raise CandidatePoolAggregateError(
                    f"candidate-pool cohorts contain overlapping lineages: {case_id}")
            seen_lineages.add(lineage_id)
        rows.append({
            "path": str(evidence_path), "sha256": file_sha256,
            "receipt_digest": report_digest,
            "cohort_receipt_digest": cohort.receipt_digest,
            "campaign_id": cohort.campaign_id, "case_count": expected_cases,
        })
        typed_receipts.extend(receipts)
        typed_outcomes.extend(outcomes)

    policy_arms = {receipt.arm for receipt in typed_receipts}
    if len(policy_arms) != 1:
        raise CandidatePoolAggregateError(
            "candidate-pool aggregate requires one policy arm")
    try:
        metrics = summarize_candidate_pool(
            tuple(typed_receipts), tuple(typed_outcomes)).to_dict()
    except (CandidatePoolError, TypeError, ValueError) as exc:
        raise CandidatePoolAggregateError(
            "candidate-pool aggregate metrics cannot be derived") from exc
    return tuple(sorted(rows, key=lambda row: row["campaign_id"])), metrics


def _payload_from_refs(refs: Sequence[Mapping], *, base: Path) -> dict:
    rows, metrics = _normalise_rows(refs, base=base)
    payload = {
        "version": CANDIDATE_POOL_AGGREGATE_VERSION,
        "metric": "candidate_pool", "policy_arm": metrics["arm"],
        "candidate_pool_receipts": list(rows),
        "cohort_count": len(rows), "case_count": metrics["cases"],
        "metrics": metrics,
        "evaluation_only": True, "canonical_memory_mutation": "none",
        "production_integration": "not_attempted",
    }
    payload["receipt_digest"] = _digest(payload)
    return payload


def build_candidate_pool_aggregate(
        evidence_paths: Sequence[Path], *, output: Path | None = None) -> dict:
    """Aggregate independently frozen per-cohort candidate-pool receipts."""
    if isinstance(evidence_paths, (str, bytes)) or not isinstance(evidence_paths, Sequence):
        raise CandidatePoolAggregateError("evidence_paths must be a sequence")
    paths = tuple(Path(item).expanduser().resolve() for item in evidence_paths)
    if not paths:
        raise CandidatePoolAggregateError("evidence_paths must not be empty")
    refs = []
    for path in paths:
        report = _load(path, "candidate-pool evidence")
        if report.get("version") != "r3-candidate-pool-evidence-v1":
            raise CandidatePoolAggregateError(
                f"candidate-pool evidence is not a per-cohort receipt: {path}")
        cohort_path = _path(report.get("cohort_receipt"), base=path.parent)
        cohort_payload = _load(cohort_path, "candidate-pool cohort")
        refs.append({
            "path": str(path), "sha256": _file_digest(path),
            "receipt_digest": report.get("receipt_digest"),
            "cohort_receipt_digest": report.get("cohort_receipt_digest"),
            "campaign_id": cohort_payload.get("campaign_id"),
            "case_count": len(report.get("pools", ())),
        })
    payload = _payload_from_refs(refs, base=Path.cwd())
    if output is not None:
        output = Path(output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


def replay_candidate_pool_aggregate(raw: Mapping, *, base: Path) -> dict:
    """Replay an aggregate and return metrics for the P9 projection."""
    if not isinstance(raw, Mapping):
        raise CandidatePoolAggregateError("candidate-pool aggregate must be an object")
    if raw.get("version") != CANDIDATE_POOL_AGGREGATE_VERSION or \
            raw.get("metric") != "candidate_pool":
        raise CandidatePoolAggregateError(
            "candidate-pool aggregate version/metric mismatch")
    if raw.get("evaluation_only") is not True or \
            raw.get("canonical_memory_mutation") != "none" or \
            raw.get("production_integration") != "not_attempted":
        raise CandidatePoolAggregateError(
            "candidate-pool aggregate crosses an authority boundary")
    rows, metrics = _normalise_rows(
        raw.get("candidate_pool_receipts"), base=Path(base).expanduser().resolve())
    expected = {
        "version": CANDIDATE_POOL_AGGREGATE_VERSION,
        "metric": "candidate_pool", "policy_arm": metrics["arm"],
        "candidate_pool_receipts": list(rows),
        "cohort_count": len(rows), "case_count": metrics["cases"],
        "metrics": metrics,
        "evaluation_only": True, "canonical_memory_mutation": "none",
        "production_integration": "not_attempted",
    }
    expected["receipt_digest"] = _digest(expected)
    if dict(raw) != expected:
        raise CandidatePoolAggregateError(
            "candidate-pool aggregate replay mismatch")
    return {**metrics, "source": "typed_candidate_pool_aggregate",
            "cohort_count": len(rows), "receipt_digest": raw["receipt_digest"]}


__all__ = [
    "CANDIDATE_POOL_AGGREGATE_VERSION", "CandidatePoolAggregateError",
    "build_candidate_pool_aggregate", "replay_candidate_pool_aggregate",
]
