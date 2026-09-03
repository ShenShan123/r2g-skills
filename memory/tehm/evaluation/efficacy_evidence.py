"""Typed, replayable efficacy evidence for the Revision3 production gate.

The accepted first branch is a policy-revision comparison: the same paired
cases are executed before and after a typed memory-policy revision, and the
harmful activation rate is recomputed from the executable oracle receipts.
This is deliberately separate from candidate-pool composition and never
accepts caller-supplied ``gain`` or rate booleans as authority.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

from tehm.canonical.transition import HARMFUL_OUTCOMES, POSITIVE_OUTCOMES
from tehm.ids import stable_dumps

from .policy_mir import _validate_policy_route
from .rtl_cohort import RtlPairedCohortReceipt


EFFICACY_EVIDENCE_VERSION = "r3-efficacy-evidence-v1"


class EfficacyEvidenceError(ValueError):
    """Malformed or non-comparable efficacy evidence."""


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _file_digest(path: Path) -> str:
    try:
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise EfficacyEvidenceError(f"efficacy cohort is unreadable: {path}") from exc


def _load(path: Path, label: str) -> dict:
    try:
        payload = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EfficacyEvidenceError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(payload, Mapping):
        raise EfficacyEvidenceError(f"{label} must be an object: {path}")
    return dict(payload)


def _path(raw: object, *, base: Path, label: str) -> Path:
    if type(raw) is not str or not raw.strip():
        raise EfficacyEvidenceError(f"{label} path is required")
    value = Path(raw).expanduser()
    return (value if value.is_absolute() else base / value).resolve()


def _load_cohort(path: Path) -> tuple[dict, RtlPairedCohortReceipt]:
    payload = _load(path, "efficacy cohort")
    try:
        cohort = RtlPairedCohortReceipt.from_dict(payload)
    except (TypeError, ValueError) as exc:
        raise EfficacyEvidenceError(f"efficacy cohort cannot replay: {path}") from exc
    if payload.get("receipt_digest") != cohort.receipt_digest:
        raise EfficacyEvidenceError(f"efficacy cohort receipt digest mismatch: {path}")
    if cohort.evaluation_only is not True or cohort.source_disjoint is not True or \
            cohort.source_restore_verified is not True:
        raise EfficacyEvidenceError(
            f"efficacy cohort is not evaluation-only/source-disjoint: {path}")
    return payload, cohort


def _execution_complete(receipt) -> bool:
    metadata = receipt.metadata
    oracle_metadata = metadata.get("oracle_metadata") if isinstance(metadata, Mapping) else None
    return (
        receipt.evaluation_only is True and
        isinstance(metadata, Mapping) and metadata.get("oracle_available") is True and
        isinstance(oracle_metadata, Mapping) and
        oracle_metadata.get("oracle_complete") is True and
        receipt.compile_result != "UNKNOWN" and
        receipt.functional_result != "UNKNOWN" and
        receipt.signoff_result not in {None, "UNKNOWN"} and
        receipt.outcome != "UNKNOWN")


def _harmful_activation(bundle, *, policy_arm: str, case_id: str) -> bool:
    baseline = bundle.arm_receipts["NO_MEMORY"]
    policy = bundle.arm_receipts[policy_arm]
    if baseline.source != "no_memory":
        raise EfficacyEvidenceError(f"efficacy baseline is not no-memory: {case_id}")
    if not _execution_complete(baseline) or not _execution_complete(policy):
        raise EfficacyEvidenceError(f"efficacy execution is incomplete: {case_id}")
    return (baseline.outcome in POSITIVE_OUTCOMES and
            (policy.outcome in HARMFUL_OUTCOMES or bool(policy.created_regressions)))


def _baseline_execution_fingerprint(receipt) -> str:
    """Hash baseline execution semantics without revision-specific route metadata."""
    metadata = receipt.metadata if isinstance(receipt.metadata, Mapping) else {}
    payload = {
        "case_id": receipt.case_id, "candidate_id": receipt.candidate_id,
        "source": receipt.source, "action_digest": receipt.action_digest,
        "candidate_digest": receipt.candidate_digest,
        "compile_result": receipt.compile_result,
        "functional_result": receipt.functional_result,
        "signoff_result": receipt.signoff_result, "outcome": receipt.outcome,
        "created_regressions": list(receipt.created_regressions),
        "obligations": receipt.obligations,
        "toolchain_digest": receipt.toolchain_digest,
        "oracle_digest": receipt.oracle_digest,
        "produced_transition_id": receipt.produced_transition_id,
        "budget": receipt.budget,
        "oracle_metadata": metadata.get("oracle_metadata"),
    }
    return _digest(payload)


def _validate_cohort_environment(before, after, *, case_id: str) -> None:
    before_bundle = before.case_receipts[case_id]
    after_bundle = after.case_receipts[case_id]
    environment_fields = (
        ("toolchain_digest", before.toolchain_digest, after.toolchain_digest),
        ("oracle_digest", before.oracle_digest, after.oracle_digest),
        ("platform_digest", before.platform_digest, after.platform_digest),
        ("pdk_digest", before.pdk_digest, after.pdk_digest),
        ("candidate_budget", before.candidate_budget, after.candidate_budget),
    )
    for name, left, right in environment_fields:
        if left != right:
            raise EfficacyEvidenceError(f"efficacy {name} drifted: {case_id}")
    if before.source_digests.get(case_id) != after.source_digests.get(case_id):
        raise EfficacyEvidenceError(f"efficacy source digest drifted: {case_id}")
    if before_bundle.lineage_id != after_bundle.lineage_id:
        raise EfficacyEvidenceError(f"efficacy lineage drifted: {case_id}")
    # ``case_digest`` covers the complete paired bundle and is expected to
    # change when the policy revision changes.  The immutable case identity is
    # instead bound by case_id, source digest and lineage above.
    if before_bundle.candidate_budget != after_bundle.candidate_budget:
        raise EfficacyEvidenceError(f"efficacy case budget drifted: {case_id}")
    before_baseline = before_bundle.arm_receipts["NO_MEMORY"]
    after_baseline = after_bundle.arm_receipts["NO_MEMORY"]
    if (_baseline_execution_fingerprint(before_baseline) !=
            _baseline_execution_fingerprint(after_baseline) or
            before_baseline.outcome != after_baseline.outcome):
        raise EfficacyEvidenceError(
            f"efficacy no-memory baseline drifted: {case_id}")


def _replay_components(raw: Mapping, *, base: Path, require_digest: bool = True):
    if not isinstance(raw, Mapping):
        raise EfficacyEvidenceError("efficacy evidence must be an object")
    if raw.get("version") != EFFICACY_EVIDENCE_VERSION or raw.get("metric") != "efficacy":
        raise EfficacyEvidenceError("efficacy evidence version/metric mismatch")
    if raw.get("evaluation_only") is not True or \
            raw.get("canonical_memory_mutation") != "none" or \
            raw.get("production_integration") != "not_attempted":
        raise EfficacyEvidenceError("efficacy evidence crosses an authority boundary")
    policy_arm = raw.get("policy_arm")
    if policy_arm not in {"APPLICABILITY_GATED", "CAUSAL_NO_SKILL"}:
        raise EfficacyEvidenceError("efficacy policy arm is invalid")
    before_path = _path(raw.get("before_cohort"), base=Path(base).resolve(),
                        label="efficacy before cohort")
    after_path = _path(raw.get("after_cohort"), base=Path(base).resolve(),
                       label="efficacy after cohort")
    if before_path == after_path:
        raise EfficacyEvidenceError("efficacy before/after cohorts must differ")
    if raw.get("before_cohort_sha256") != _file_digest(before_path) or \
            raw.get("after_cohort_sha256") != _file_digest(after_path):
        raise EfficacyEvidenceError("efficacy cohort file digest mismatch")
    before_payload, before = _load_cohort(before_path)
    after_payload, after = _load_cohort(after_path)
    if before_payload.get("receipt_digest") != raw.get("before_cohort_digest") or \
            after_payload.get("receipt_digest") != raw.get("after_cohort_digest"):
        raise EfficacyEvidenceError("efficacy cohort receipt binding drifted")
    if before.campaign_manifest_digest == after.campaign_manifest_digest:
        raise EfficacyEvidenceError("efficacy before/after revision boundary is missing")
    if set(before.case_receipts) != set(after.case_receipts) or not before.case_receipts:
        raise EfficacyEvidenceError("efficacy before/after case coverage differs")

    rows = []
    before_harmful = after_harmful = 0
    for case_id in sorted(before.case_receipts):
        _validate_cohort_environment(before, after, case_id=case_id)
        before_bundle = before.case_receipts[case_id]
        after_bundle = after.case_receipts[case_id]
        try:
            _validate_policy_route(before_bundle, policy_arm=policy_arm, case_id=case_id)
            _validate_policy_route(after_bundle, policy_arm=policy_arm, case_id=case_id)
        except (TypeError, ValueError) as exc:
            raise EfficacyEvidenceError(
                f"efficacy policy route cannot replay: {case_id}") from exc
        before_harm = _harmful_activation(
            before_bundle, policy_arm=policy_arm, case_id=case_id)
        after_harm = _harmful_activation(
            after_bundle, policy_arm=policy_arm, case_id=case_id)
        before_harmful += int(before_harm)
        after_harmful += int(after_harm)
        rows.append({
            "case_id": case_id,
            "lineage_id": before_bundle.lineage_id,
            "before_case_digest": before_bundle.case_digest,
            "after_case_digest": after_bundle.case_digest,
            "before_policy_outcome": before_bundle.arm_receipts[policy_arm].outcome,
            "after_policy_outcome": after_bundle.arm_receipts[policy_arm].outcome,
            "before_policy_source": before_bundle.arm_receipts[policy_arm].source,
            "after_policy_source": after_bundle.arm_receipts[policy_arm].source,
            "before_policy_execution_digest": before_bundle.arm_receipts[policy_arm].execution_digest,
            "after_policy_execution_digest": after_bundle.arm_receipts[policy_arm].execution_digest,
            "before_harmful_activation": before_harm,
            "after_harmful_activation": after_harm,
        })
    total = len(rows)
    metrics = {
        "branch": "harmful_activation_decrease",
        "policy_arm": policy_arm,
        "paired_cases": total,
        "baseline_harmful_activation_cases": before_harmful,
        "memory_harmful_activation_cases": after_harmful,
        "baseline_harmful_activation_rate": round(before_harmful / total, 6),
        "memory_harmful_activation_rate": round(after_harmful / total, 6),
        "harm_reduction_observed": after_harmful < before_harmful,
        "before_cohort_digest": before.receipt_digest,
        "after_cohort_digest": after.receipt_digest,
        "case_rows": rows,
    }
    if raw.get("metrics") != metrics and not (
            not require_digest and raw.get("metrics") is None):
        raise EfficacyEvidenceError("efficacy metrics drifted")
    if require_digest:
        unsigned = dict(raw)
        unsigned.pop("receipt_digest", None)
        if raw.get("receipt_digest") != _digest(unsigned):
            raise EfficacyEvidenceError("efficacy evidence receipt digest mismatch")
    return metrics


def replay_efficacy_evidence(raw: Mapping, *, base: Path) -> dict:
    """Replay a typed efficacy envelope and return oracle-derived metrics."""
    metrics = _replay_components(raw, base=base)
    return {**metrics, "source": "typed_efficacy", "receipt_digest": raw["receipt_digest"]}


def build_efficacy_evidence(
        *, before_cohort: Path, after_cohort: Path, policy_arm: str,
        output: Path | None = None) -> dict:
    """Build an evaluation-only efficacy receipt from two frozen cohorts."""
    before_cohort = Path(before_cohort).expanduser().resolve()
    after_cohort = Path(after_cohort).expanduser().resolve()
    before_payload, before = _load_cohort(before_cohort)
    after_payload, after = _load_cohort(after_cohort)
    report = {
        "version": EFFICACY_EVIDENCE_VERSION, "metric": "efficacy",
        "policy_arm": policy_arm,
        "before_cohort": str(before_cohort),
        "before_cohort_sha256": _file_digest(before_cohort),
        "before_cohort_digest": before.receipt_digest,
        "after_cohort": str(after_cohort),
        "after_cohort_sha256": _file_digest(after_cohort),
        "after_cohort_digest": after.receipt_digest,
        "evaluation_only": True, "canonical_memory_mutation": "none",
        "production_integration": "not_attempted",
    }
    # The builder uses the same strict replay path as production readiness.
    report["metrics"] = _replay_components(
        report, base=Path.cwd(), require_digest=False)
    report["receipt_digest"] = _digest(report)
    if output is not None:
        output = Path(output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


__all__ = [
    "EFFICACY_EVIDENCE_VERSION", "EfficacyEvidenceError",
    "build_efficacy_evidence", "replay_efficacy_evidence",
]
