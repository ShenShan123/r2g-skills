"""Replayable aggregation for routed-policy memory-interference evidence.

P15-B needs a denominator that can grow across independently frozen cohorts.
This module joins typed :class:`RtlPairedCohortReceipt` files without trusting
their compact summaries: every cohort receipt digest, campaign, source digest,
fixed toolchain/oracle/platform/PDK identity, route witness, oracle outcome,
and aggregate count is checked before a Wilson interval is computed.

The result is an evaluation receipt only.  It never writes a canonical store,
changes lifecycle authority, or enables a production runtime.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from tehm.canonical.transition import HARMFUL_OUTCOMES, POSITIVE_OUTCOMES
from tehm.ids import stable_dumps

from .no_skill_calibration import wilson_interval
from .rtl_cohort import RtlPairedCohortReceipt


POLICY_MIR_VERSION = "r3-policy-mir-v2"
POLICY_MIR_ARMS = frozenset({"APPLICABILITY_GATED", "CAUSAL_NO_SKILL"})
_ROUTING_DECISIONS = frozenset({
    "APPLY", "CONSIDER", "NO_SKILL", "ABSTAIN", "INAPPLICABLE",
})
_NO_MEMORY_DECISIONS = frozenset({"NO_SKILL", "ABSTAIN", "INAPPLICABLE"})


class PolicyMIRError(ValueError):
    """A routed-policy MIR cohort or aggregate is malformed."""


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _file_digest(path: Path) -> str:
    try:
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise PolicyMIRError(f"policy MIR cohort is unreadable: {path}") from exc


def _load_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PolicyMIRError(f"policy MIR cohort is not valid JSON: {path}") from exc
    if not isinstance(payload, Mapping):
        raise PolicyMIRError(f"policy MIR cohort must be an object: {path}")
    return dict(payload)


def _path(raw: object, *, base: Path) -> Path:
    if type(raw) is not str or not raw.strip():
        raise PolicyMIRError("policy MIR cohort path is required")
    value = Path(raw).expanduser()
    return (value if value.is_absolute() else base / value).resolve()


def _digest_text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip() or not value.startswith("sha256:"):
        raise PolicyMIRError(f"policy MIR {name} must be a sha256 digest")
    return value.strip()


def _execution_complete(receipt) -> bool:
    return (
        receipt.evaluation_only is True and
        receipt.metadata.get("oracle_available") is True and
        receipt.compile_result != "UNKNOWN" and
        receipt.functional_result != "UNKNOWN" and
        receipt.signoff_result not in {None, "UNKNOWN"} and
        receipt.outcome != "UNKNOWN")


def _validate_policy_route(bundle, *, policy_arm: str, case_id: str) -> None:
    """Recheck that the routed arm obeyed its recorded policy decision.

    A route receipt ID alone is not enough: a caller could attach an otherwise
    valid-looking ID while changing the policy arm source or fallback metadata.
    MIR replay must reject bundles that omit the decision field instead of
    treating them as routed observations.
    """
    decision = bundle.routing_decision
    if decision not in _ROUTING_DECISIONS:
        raise PolicyMIRError(
            f"policy MIR routing decision is missing or invalid: {case_id}")
    policy = bundle.arm_receipts[policy_arm]
    metadata = policy.metadata
    if not isinstance(metadata, Mapping):
        raise PolicyMIRError(
            f"policy MIR policy metadata is malformed: {case_id}")
    fallback = metadata.get("policy_fallback")
    if fallback is not None and type(fallback) is not bool:
        raise PolicyMIRError(
            f"policy MIR policy fallback witness is malformed: {case_id}")
    if (metadata.get("routing_decision") is not None and
            metadata.get("routing_decision") != decision):
        raise PolicyMIRError(
            f"policy MIR routing metadata disagrees with receipt: {case_id}")
    if policy_arm == "CAUSAL_NO_SKILL":
        expected_source = ("no_memory" if decision in _NO_MEMORY_DECISIONS
                           else "structured_memory")
        expected_fallback = decision in _NO_MEMORY_DECISIONS
        if (policy.source != expected_source or
                (expected_fallback and fallback is not True) or
                (not expected_fallback and fallback is True)):
            raise PolicyMIRError(
                f"policy MIR causal arm violates route semantics: {case_id}")
    elif policy_arm == "APPLICABILITY_GATED":
        if policy.source == "no_memory" and fallback is not True:
            raise PolicyMIRError(
                f"policy MIR applicability fallback is not witnessed: {case_id}")
        if policy.source == "structured_memory" and fallback:
            raise PolicyMIRError(
                f"policy MIR applicability memory execution is marked fallback: {case_id}")
        if policy.source not in {"no_memory", "structured_memory"}:
            raise PolicyMIRError(
                f"policy MIR applicability arm source is invalid: {case_id}")


def _load_cohorts(
        refs: Sequence[Mapping], *, base: Path, expected_policy_arm: str,
        require_aggregates: bool = True,
        ) -> tuple[list[dict], dict]:
    if expected_policy_arm not in POLICY_MIR_ARMS:
        raise PolicyMIRError("policy MIR policy_arm is invalid")
    if isinstance(refs, (str, bytes)) or not isinstance(refs, Sequence) or not refs:
        raise PolicyMIRError("policy MIR cohort_receipts must be non-empty")

    cohort_rows: list[dict] = []
    seen_paths: set[Path] = set()
    seen_campaigns: set[str] = set()
    seen_cases: set[str] = set()
    seen_sources: set[str] = set()
    seen_lineages: set[str] = set()
    fixed: tuple[str, str, str, str, int] | None = None
    harmful = known = unknown = routed = case_count = 0

    for raw_ref in refs:
        if not isinstance(raw_ref, Mapping):
            raise PolicyMIRError("policy MIR cohort reference is malformed")
        cohort_path = _path(raw_ref.get("path"), base=base)
        if cohort_path in seen_paths:
            raise PolicyMIRError("policy MIR cohort references contain duplicates")
        seen_paths.add(cohort_path)
        actual_file_digest = _file_digest(cohort_path)
        if raw_ref.get("sha256") != actual_file_digest:
            raise PolicyMIRError(f"policy MIR cohort file digest mismatch: {cohort_path}")
        payload = _load_json(cohort_path)
        try:
            cohort = RtlPairedCohortReceipt.from_dict(payload)
        except (TypeError, ValueError) as exc:
            raise PolicyMIRError(
                f"policy MIR cohort cannot replay: {cohort_path}") from exc
        if payload.get("receipt_digest") != cohort.receipt_digest:
            raise PolicyMIRError(f"policy MIR cohort receipt digest mismatch: {cohort_path}")
        if raw_ref.get("receipt_digest") != cohort.receipt_digest:
            raise PolicyMIRError(f"policy MIR cohort binding drifted: {cohort_path}")
        if raw_ref.get("campaign_id") != cohort.campaign_id:
            raise PolicyMIRError(f"policy MIR cohort campaign binding drifted: {cohort_path}")
        if cohort.campaign_id in seen_campaigns:
            raise PolicyMIRError("policy MIR cohorts contain duplicate campaigns")
        seen_campaigns.add(cohort.campaign_id)
        if (cohort.evaluation_only is not True or
                cohort.source_disjoint is not True or
                cohort.source_restore_verified is not True):
            raise PolicyMIRError("policy MIR cohort is not evaluation-only/source-disjoint")
        environment = (cohort.toolchain_digest, cohort.oracle_digest,
                       cohort.platform_digest, cohort.pdk_digest,
                       cohort.candidate_budget)
        if fixed is None:
            fixed = environment
        elif environment != fixed:
            raise PolicyMIRError("policy MIR cohort fixed environment drifted")

        local_case_count = len(cohort.case_receipts)
        expected_local_cases = raw_ref.get("case_count")
        if type(expected_local_cases) is not int or expected_local_cases != local_case_count:
            raise PolicyMIRError("policy MIR cohort case count binding drifted")
        local_harmful = local_known = local_unknown = local_routed = 0
        for case_id, bundle in sorted(cohort.case_receipts.items()):
            if case_id in seen_cases:
                raise PolicyMIRError("policy MIR cohorts contain duplicate case IDs")
            seen_cases.add(case_id)
            source_digest = _digest_text(
                cohort.source_digests.get(case_id), "source_digest")
            if source_digest in seen_sources:
                raise PolicyMIRError("policy MIR cohorts contain overlapping source digests")
            seen_sources.add(source_digest)
            lineage = bundle.lineage_id
            if type(lineage) is not str or not lineage.strip():
                raise PolicyMIRError(
                    f"policy MIR lineage witness is missing: {case_id}")
            lineage = lineage.strip()
            if lineage in seen_lineages:
                raise PolicyMIRError(
                    f"policy MIR cohorts contain overlapping lineages: {case_id}")
            seen_lineages.add(lineage)
            baseline = bundle.arm_receipts["NO_MEMORY"]
            policy = bundle.arm_receipts[expected_policy_arm]
            if baseline.source != "no_memory":
                raise PolicyMIRError(
                    f"policy MIR baseline is not no-memory: {case_id}")
            if bundle.routing_receipt_id is None:
                raise PolicyMIRError(
                    f"policy MIR route receipt is missing: {case_id}")
            _validate_policy_route(
                bundle, policy_arm=expected_policy_arm, case_id=case_id)
            local_routed += 1
            if not _execution_complete(baseline) or not _execution_complete(policy):
                local_unknown += 1
                continue
            local_known += 1
            if (baseline.outcome in POSITIVE_OUTCOMES and
                    (policy.outcome in HARMFUL_OUTCOMES or
                     bool(policy.created_regressions))):
                local_harmful += 1
        aggregate_mismatch = (
            type(raw_ref.get("known_cases")) is not int or
            type(raw_ref.get("unknown_cases")) is not int or
            type(raw_ref.get("routed_cases")) is not int or
            raw_ref.get("known_cases") != local_known or
            raw_ref.get("unknown_cases") != local_unknown or
            raw_ref.get("routed_cases") != local_routed or
            raw_ref.get("harmful_cases") is not None and
            raw_ref.get("harmful_cases") != local_harmful)
        if require_aggregates and aggregate_mismatch:
            raise PolicyMIRError(
                f"policy MIR cohort aggregate disagrees with cohort: {cohort_path}")
        # Build-time refs may omit harmful_cases; replayed v2 refs always carry
        # it after normalization below.
        cohort_rows.append({
            "path": str(cohort_path), "sha256": actual_file_digest,
            "receipt_digest": cohort.receipt_digest,
            "campaign_id": cohort.campaign_id, "case_count": local_case_count,
            "known_cases": local_known, "unknown_cases": local_unknown,
            "routed_cases": local_routed, "harmful_cases": local_harmful,
        })
        case_count += local_case_count
        known += local_known
        unknown += local_unknown
        routed += local_routed
        harmful += local_harmful

    if fixed is None or known <= 0:
        raise PolicyMIRError("policy MIR has no complete paired oracle cases")
    interval = wilson_interval(harmful, known)
    metrics = {
        "source": "routed_policy", "baseline_arm": "NO_MEMORY",
        "policy_arm": expected_policy_arm, "cohort_count": len(cohort_rows),
        "case_count": case_count, "harmful_cases": harmful,
        "known_cases": known, "total_cases": known, "unknown_cases": unknown,
        "routed_cases": routed,
        "routing_receipt_coverage": round(routed / case_count, 6),
        "point": interval["point"], "upper_ci": interval["upper"],
        "confidence": interval["confidence"],
        "toolchain_digest": fixed[0], "oracle_digest": fixed[1],
        "platform_digest": fixed[2], "pdk_digest": fixed[3],
        "candidate_budget": fixed[4],
    }
    return cohort_rows, metrics


def _validate_aggregate(raw: Mapping, *, metrics: Mapping) -> None:
    required = {
        "version", "metric", "baseline_arm", "policy_arm", "cohort_receipts",
        "cohort_count", "case_count", "known_cases", "unknown_cases",
        "routed_cases", "harmful_cases", "routing_receipt_coverage", "point",
        "upper_ci", "confidence", "evaluation_only", "canonical_memory_mutation",
        "production_integration", "receipt_digest",
    }
    if not required <= set(raw):
        raise PolicyMIRError("policy MIR aggregate is missing fields")
    if raw.get("version") != POLICY_MIR_VERSION or raw.get("metric") != "routed_policy":
        raise PolicyMIRError("policy MIR aggregate version/metric mismatch")
    if raw.get("baseline_arm") != metrics["baseline_arm"] or \
            raw.get("policy_arm") != metrics["policy_arm"]:
        raise PolicyMIRError("policy MIR aggregate arm binding is invalid")
    if raw.get("evaluation_only") is not True or \
            raw.get("canonical_memory_mutation") != "none" or \
            raw.get("production_integration") != "not_attempted":
        raise PolicyMIRError("policy MIR aggregate crosses an authority boundary")
    for key in ("cohort_count", "case_count", "known_cases", "unknown_cases",
                "routed_cases", "harmful_cases"):
        if type(raw.get(key)) is not int or raw[key] != metrics[key]:
            raise PolicyMIRError("policy MIR aggregate disagrees with cohort receipts")
    for key in ("routing_receipt_coverage", "point", "upper_ci", "confidence"):
        if raw.get(key) != metrics[key]:
            raise PolicyMIRError("policy MIR aggregate metric disagrees with cohort receipts")
    unsigned = dict(raw)
    unsigned.pop("receipt_digest", None)
    if raw.get("receipt_digest") != _digest(unsigned):
        raise PolicyMIRError("policy MIR aggregate receipt digest mismatch")


def build_routed_policy_mir(
        cohort_paths: Sequence[Path], *, policy_arm: str,
        output: Path | None = None) -> dict:
    """Aggregate multiple independently frozen typed cohort receipts."""
    if isinstance(cohort_paths, (str, bytes)) or not isinstance(cohort_paths, Sequence):
        raise PolicyMIRError("policy MIR cohort_paths must be a sequence")
    paths = tuple(Path(item).expanduser().resolve() for item in cohort_paths)
    if not paths:
        raise PolicyMIRError("policy MIR cohort_paths must not be empty")
    # Build refs first, then use the same replay validator as production
    # readiness.  The producer cannot bypass file/receipt digest checks.
    refs = []
    for path in paths:
        payload = _load_json(path)
        try:
            cohort = RtlPairedCohortReceipt.from_dict(payload)
        except (TypeError, ValueError) as exc:
            raise PolicyMIRError(f"policy MIR cohort cannot replay: {path}") from exc
        if payload.get("receipt_digest") != cohort.receipt_digest:
            raise PolicyMIRError(f"policy MIR cohort receipt digest mismatch: {path}")
        refs.append({"path": str(path), "sha256": _file_digest(path),
                     "receipt_digest": cohort.receipt_digest,
                     "campaign_id": cohort.campaign_id,
                     "case_count": len(cohort.case_receipts)})
    rows, metrics = _load_cohorts(
        refs, base=Path.cwd(), expected_policy_arm=policy_arm,
        require_aggregates=False)
    # _load_cohorts normalizes all mutable reference fields.  The normalized
    # refs are the only refs that enter the aggregate digest.
    payload = {
        "version": POLICY_MIR_VERSION, "metric": "routed_policy",
        "baseline_arm": "NO_MEMORY", "policy_arm": policy_arm,
        "cohort_receipts": rows, **{key: value for key, value in metrics.items()
                                     if key not in {"source", "baseline_arm", "policy_arm"}},
        "evaluation_only": True, "canonical_memory_mutation": "none",
        "production_integration": "not_attempted",
    }
    payload["receipt_digest"] = _digest(payload)
    if output is not None:
        output = Path(output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "policy_mir": payload,
            "receipt_id": "r3_policy_mir_" + payload["receipt_digest"].split(":", 1)[1][:24],
            "receipt_digest": payload["receipt_digest"],
            "evaluation_only": True, "canonical_memory_mutation": "none",
            "production_integration": "not_attempted",
        }
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return payload


def replay_routed_policy_mir(raw: Mapping, *, base: Path) -> dict:
    """Replay a v2 aggregate from its content-addressed cohort references."""
    if not isinstance(raw, Mapping):
        raise PolicyMIRError("policy MIR aggregate must be an object")
    policy_arm = raw.get("policy_arm")
    raw_refs = raw.get("cohort_receipts")
    refs, metrics = _load_cohorts(
        raw_refs, base=Path(base).expanduser().resolve(),
        expected_policy_arm=policy_arm)
    # Compare normalized refs as well as the aggregate scalar projection.  This
    # catches a changed path/hash/campaign even if the scalar counts are intact.
    if tuple(raw_refs) != tuple(refs):
        raise PolicyMIRError("policy MIR cohort references are not normalized")
    expected = {
        "version": POLICY_MIR_VERSION, "metric": "routed_policy",
        "baseline_arm": "NO_MEMORY", "policy_arm": policy_arm,
        "cohort_receipts": refs, **{key: value for key, value in metrics.items()
                                     if key not in {"source", "baseline_arm", "policy_arm"}},
        "evaluation_only": True, "canonical_memory_mutation": "none",
        "production_integration": "not_attempted",
    }
    expected["receipt_digest"] = _digest(expected)
    _validate_aggregate(raw, metrics=metrics)
    if dict(raw) != expected:
        raise PolicyMIRError("policy MIR aggregate replay mismatch")
    return metrics


__all__ = [
    "POLICY_MIR_VERSION", "POLICY_MIR_ARMS", "PolicyMIRError",
    "build_routed_policy_mir", "replay_routed_policy_mir",
]
