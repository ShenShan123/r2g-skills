"""Revision3 P15-B production-readiness preflight.

This module binds the evidence required before a production shadow mirror can
even be discussed.  It is intentionally stricter than a convenience summary:
all input files are hashed, typed receipts are replayed, and missing evidence
remains ``NOT_ESTABLISHED``.  The result is an evaluation receipt only;
``eligible`` never grants a lifecycle status or a runtime load permission.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from tehm.ids import stable_dumps
from tehm.schema_contract import replay_schema_contract
from tehm.canonical.transition import HARMFUL_OUTCOMES, POSITIVE_OUTCOMES

from .no_skill_calibration import (
    CALIBRATION_REASONS, NoSkillCalibrationError, NoSkillCalibrationReceipt,
    wilson_interval,
)
from .rtl_cohort import RtlPairedCohortReceipt
from .policy_mir import PolicyMIRError, replay_routed_policy_mir
from tehm.retrieval.production_gate import evaluate_production_gate
from tehm.lifecycle.promotion_gates import REQUIRED_GATES


PRODUCTION_READINESS_VERSION = "r3-production-readiness-v1"
READINESS_GATES = (
    "multi_lineage", "reason_stratified_calibration", "mir_upper_ci",
    "repair_pareto", "anti_forgetting", "authority_replay", "rollback",
)


class ProductionReadinessError(ValueError):
    """Malformed or contradictory production-readiness evidence."""


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _file_digest(path: Path) -> str:
    try:
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ProductionReadinessError(f"readiness evidence is unreadable: {path}") from exc


def _load(path: Path, label: str) -> dict:
    try:
        payload = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProductionReadinessError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(payload, Mapping):
        raise ProductionReadinessError(f"{label} must be a JSON object: {path}")
    return dict(payload)


def _path(raw: object, *, base: Path, label: str) -> Path:
    if type(raw) is not str or not raw.strip():
        raise ProductionReadinessError(f"{label} path is required")
    value = Path(raw).expanduser()
    return (value if value.is_absolute() else base / value).resolve()


def _ref(path: Path, name: str) -> dict:
    return {"name": name, "path": str(path.resolve()), "sha256": _file_digest(path)}


def _verify_embedded_refs(report: Mapping, *, base: Path) -> list[dict]:
    """Replay report-level evidence refs without trusting their summary fields."""
    refs = report.get("evidence_refs")
    if refs is None:
        return []
    if not isinstance(refs, list):
        raise ProductionReadinessError("embedded evidence_refs must be a list")
    checked = []
    for item in refs:
        if not isinstance(item, Mapping):
            raise ProductionReadinessError("embedded evidence_ref is malformed")
        path = _path(item.get("path"), base=base, label="embedded evidence")
        expected = item.get("sha256")
        actual = _file_digest(path)
        if expected != actual:
            raise ProductionReadinessError(f"embedded evidence digest mismatch: {path}")
        checked.append({"name": item.get("id") or item.get("receipt_id") or path.name,
                        "path": str(path), "sha256": actual})
    return checked


def _calibration(path: Path) -> tuple[dict, dict, set[str]]:
    report = _load(path, "calibration report")
    _verify_embedded_refs(report, base=path.parent)
    payload = report.get("no_skill_calibration")
    try:
        receipt = NoSkillCalibrationReceipt.from_dict(payload)
    except (NoSkillCalibrationError, TypeError, ValueError) as exc:
        raise ProductionReadinessError(f"calibration receipt cannot replay: {exc}") from exc
    if report.get("canonical_memory_mutation") != "none" or report.get("production_runtime_imported") is True:
        raise ProductionReadinessError("calibration report crosses production boundary")
    if receipt.evaluation_only is not True or receipt.status != "PASS":
        raise ProductionReadinessError("calibration receipt is not an eligible evaluation receipt")
    reason_support = {
        reason: int((receipt.per_reason.get(reason) or {}).get("support", 0))
        for reason in CALIBRATION_REASONS
    }
    reason_gate = all(value >= receipt.minimum_reason_cases for value in reason_support.values())

    # The P15 cohort receipt is an independently hashed input referenced by
    # the report manifest.  It is the authority for lineage, not a summary
    # boolean in the calibration report.
    cohort_ref = next((item for item in report.get("evidence_refs", [])
                       if isinstance(item, Mapping) and item.get("id") == "p15-cohort"), None)
    if cohort_ref is None:
        manifest = _path(report.get("manifest"), base=path.parent, label="calibration manifest")
        manifest_payload = _load(manifest, "calibration manifest")
        cohort_ref = next((item for item in manifest_payload.get("evidence_refs", [])
                           if isinstance(item, Mapping) and item.get("id") == "p15-cohort"), None)
    if not isinstance(cohort_ref, Mapping):
        raise ProductionReadinessError("calibration cohort receipt reference is missing")
    cohort_path = _path(cohort_ref.get("path"), base=path.parent, label="calibration cohort")
    if cohort_ref.get("sha256") != _file_digest(cohort_path):
        raise ProductionReadinessError("calibration cohort digest mismatch")
    cohort = _load(cohort_path, "calibration cohort")
    try:
        typed_cohort = RtlPairedCohortReceipt.from_dict(cohort)
    except (TypeError, ValueError) as exc:
        raise ProductionReadinessError(f"calibration cohort cannot replay: {exc}") from exc
    lineages = set(typed_cohort.lineage_ids.values())
    if ((report.get("campaign_id") is not None and
         typed_cohort.campaign_id != report.get("campaign_id")) or
            set(receipt.sample_ids) != set(typed_cohort.case_receipts)):
        raise ProductionReadinessError("calibration sample/cohort case binding mismatch")
    if typed_cohort.evaluation_only is not True or typed_cohort.source_disjoint is not True:
        raise ProductionReadinessError("calibration cohort is not evaluation-only/source-disjoint")
    metrics = {
        "campaign_id": typed_cohort.campaign_id,
        "sample_count": receipt.sample_count,
        "lineage_count": len(lineages),
        "reason_support": reason_support,
        "minimum_reason_cases": receipt.minimum_reason_cases,
        "calibration_error": receipt.calibration_error,
        "precision_lower_ci": (receipt.overall.get("precision") or {}).get("lower"),
        "recall_lower_ci": (receipt.overall.get("recall") or {}).get("lower"),
    }
    gates = {
        "multi_lineage": len(lineages) >= 2,
        "reason_stratified_calibration": reason_gate and receipt.eligible,
    }
    return gates, metrics, lineages


def _execution_complete(receipt) -> bool:
    """Require a complete, evaluation-only executable oracle receipt."""
    return (
        receipt.evaluation_only is True and
        receipt.metadata.get("oracle_available") is True and
        receipt.compile_result != "UNKNOWN" and
        receipt.functional_result != "UNKNOWN" and
        receipt.signoff_result not in {None, "UNKNOWN"} and
        receipt.outcome != "UNKNOWN")


def _routed_policy_mir(report: Mapping, *, path: Path) -> tuple[bool, dict]:
    """Replay an explicit routed-policy MIR witness from a typed cohort.

    The older interference summary only measured ``ALWAYS_MEMORY`` as a
    deliberately harmful counterfactual.  A production MIR must instead be
    derived from the arm that the revised router actually executed.  This
    witness is optional for backwards-compatible reports, but when present it
    is never trusted as a scalar: the post-revision cohort, route coverage,
    source semantics, oracle completeness, and aggregate counts are all
    recomputed here.
    """
    raw = report.get("policy_mir")
    if raw is None:
        return False, {}
    if not isinstance(raw, Mapping):
        raise ProductionReadinessError("policy MIR witness is malformed")
    if raw.get("version") == "r3-policy-mir-v2":
        try:
            metrics = replay_routed_policy_mir(raw, base=path.parent)
        except PolicyMIRError as exc:
            raise ProductionReadinessError(
                f"policy MIR aggregate cannot replay: {exc}") from exc
        return True, metrics
    if raw.get("version") != "r3-policy-mir-v1":
        raise ProductionReadinessError("policy MIR witness version mismatch")
    if raw.get("metric") != "routed_policy" or \
            raw.get("evaluation_only") is not True or \
            raw.get("canonical_memory_mutation") != "none" or \
            raw.get("production_integration") != "not_attempted":
        raise ProductionReadinessError("policy MIR witness crosses an authority boundary")
    baseline_arm = raw.get("baseline_arm")
    policy_arm = raw.get("policy_arm")
    if baseline_arm != "NO_MEMORY" or policy_arm not in {
            "APPLICABILITY_GATED", "CAUSAL_NO_SKILL"}:
        raise ProductionReadinessError("policy MIR arm binding is invalid")
    cohort_path = _path(raw.get("cohort_receipt"), base=path.parent,
                        label="policy MIR cohort")
    expected_file_digest = raw.get("cohort_receipt_sha256")
    actual_file_digest = _file_digest(cohort_path)
    if expected_file_digest != actual_file_digest:
        raise ProductionReadinessError("policy MIR cohort digest mismatch")
    cohort_payload = _load(cohort_path, "policy MIR cohort")
    try:
        cohort = RtlPairedCohortReceipt.from_dict(cohort_payload)
    except (TypeError, ValueError) as exc:
        raise ProductionReadinessError(
            f"policy MIR cohort cannot replay: {exc}") from exc
    if cohort_payload.get("receipt_digest") != cohort.receipt_digest:
        raise ProductionReadinessError("policy MIR cohort receipt digest mismatch")
    if raw.get("cohort_receipt_digest") != cohort.receipt_digest:
        raise ProductionReadinessError("policy MIR cohort binding drifted")
    if cohort.evaluation_only is not True or cohort.source_disjoint is not True or \
            cohort.source_restore_verified is not True:
        raise ProductionReadinessError("policy MIR cohort is not evaluation-only/source-disjoint")
    expected_cases = raw.get("case_count")
    if (type(expected_cases) is not int or expected_cases <= 0 or
            expected_cases != len(cohort.case_receipts)):
        raise ProductionReadinessError("policy MIR case count mismatch")

    harmful = 0
    known = 0
    unknown = 0
    routed = 0
    for case_id, bundle in sorted(cohort.case_receipts.items()):
        baseline = bundle.arm_receipts[baseline_arm]
        policy = bundle.arm_receipts[policy_arm]
        if baseline.source != "no_memory":
            raise ProductionReadinessError(
                f"policy MIR baseline is not no-memory: {case_id}")
        if bundle.routing_receipt_id is None:
            raise ProductionReadinessError(
                f"policy MIR route receipt is missing: {case_id}")
        routed += 1
        if not _execution_complete(baseline) or not _execution_complete(policy):
            unknown += 1
            continue
        known += 1
        harmful += int(
            baseline.outcome in POSITIVE_OUTCOMES and
            (policy.outcome in HARMFUL_OUTCOMES or
             bool(policy.created_regressions)))

    if (type(raw.get("known_cases")) is not int or
            type(raw.get("unknown_cases")) is not int or
            type(raw.get("harmful_cases")) is not int or
            type(raw.get("routed_cases")) is not int or
            raw.get("known_cases") != known or raw.get("unknown_cases") != unknown or \
            raw.get("harmful_cases") != harmful or raw.get("routed_cases") != routed):
        raise ProductionReadinessError("policy MIR aggregate disagrees with cohort")
    if known <= 0:
        raise ProductionReadinessError("policy MIR has no complete paired oracle cases")
    if isinstance(raw.get("routing_receipt_coverage"), bool) or \
            type(raw.get("routing_receipt_coverage")) not in (int, float) or \
            float(raw["routing_receipt_coverage"]) != round(routed / expected_cases, 6):
        raise ProductionReadinessError("policy MIR routing coverage disagrees with cohort")
    interval = wilson_interval(harmful, known)
    metrics = {
        "source": "routed_policy",
        "baseline_arm": baseline_arm,
        "policy_arm": policy_arm,
        "harmful_cases": harmful,
        "total_cases": known,
        "unknown_cases": unknown,
        "routed_cases": routed,
        "routing_receipt_coverage": round(routed / expected_cases, 6),
        "point": interval["point"],
        "upper_ci": interval["upper"],
        "confidence": interval["confidence"],
        "cohort_receipt_digest": cohort.receipt_digest,
    }
    # A finite Wilson upper bound is intentionally not rounded down to zero;
    # callers still need a sufficiently large independent cohort to establish
    # the configured production threshold.
    return True, metrics


def _interference(path: Path, *, max_upper_ci: float = 0.0) -> tuple[bool, dict]:
    if isinstance(max_upper_ci, bool):
        raise ProductionReadinessError("MIR upper-CI threshold must be a finite number")
    try:
        max_upper_ci = float(max_upper_ci)
    except (TypeError, ValueError) as exc:
        raise ProductionReadinessError(
            "MIR upper-CI threshold must be a finite number") from exc
    if not math.isfinite(max_upper_ci) or not 0.0 <= max_upper_ci <= 1.0:
        raise ProductionReadinessError(
            "MIR upper-CI threshold must be between 0 and 1")
    report = _load(path, "interference summary")
    if report.get("reason") != "MEMORY_INTERFERENCE":
        raise ProductionReadinessError("interference summary has no typed MEMORY_INTERFERENCE reason")
    if report.get("canonical_memory_mutation") != "none" or report.get("production_authority_changed") is True:
        raise ProductionReadinessError("interference summary crosses production boundary")
    has_policy_mir, policy_metrics = _routed_policy_mir(report, path=path)
    if has_policy_mir:
        # ``mir_upper_ci`` is a statistical safety gate.  Keep the existing
        # zero-harm policy threshold and let the Wilson upper bound establish
        # whether the independent routed-policy cohort is large enough.
        policy_metrics = {**policy_metrics,
                          "upper_ci_threshold": max_upper_ci}
        return policy_metrics["upper_ci"] < max_upper_ci, policy_metrics
    total = (report.get("risk_update") or {}).get("before", {}).get("memory_interference_cases")
    harmful = total
    # The summary's denominator is the source-disjoint challenge case count;
    # require the typed aggregate to agree instead of accepting a free scalar.
    denominator = report.get("case_count")
    if type(total) is not int or type(denominator) is not int or total < 0 or total > denominator or denominator <= 0:
        raise ProductionReadinessError("interference MIR denominator is incomplete")
    pre_outcome = (report.get("pre_revision_outcomes") or {}).get("ALWAYS_MEMORY")
    if not isinstance(pre_outcome, Mapping) or pre_outcome.get("FAIL") != harmful or \
            pre_outcome.get("UNKNOWN", 0) != 0 or pre_outcome.get("PASS", 0) != 0:
        raise ProductionReadinessError("interference MIR does not replay the forced-memory outcome")
    interval = wilson_interval(harmful, denominator)
    metrics = {"harmful_cases": harmful, "total_cases": denominator,
               "point": interval["point"], "upper_ci": interval["upper"],
               "confidence": interval["confidence"],
               "upper_ci_threshold": max_upper_ci}
    return interval["upper"] < max_upper_ci, metrics


def _repair_pareto(path: Path | None) -> tuple[str, dict]:
    if path is None:
        return "NOT_ESTABLISHED", {"reason": "heldout Delta-M report is required"}
    report = _load(path, "heldout Delta-M report")
    if report.get("canonical_memory_mutation") != "none" or report.get("production_runtime_imported") is True:
        return "FAIL", {"reason": "heldout Delta-M report crosses production boundary"}
    cases = report.get("cases")
    if not isinstance(cases, list) or not cases:
        return "NOT_ESTABLISHED", {"reason": "heldout Delta-M cases are required"}
    lineages = set()
    valid = True
    for item in cases:
        if not isinstance(item, Mapping):
            valid = False
            continue
        case = item.get("case") or {}
        lineages.add(case.get("lineage_id"))
        baseline = item.get("M_t") or {}
        candidate = item.get("M_t+1") or {}
        removed = item.get("M_t+1_minus_delta_M") or {}
        oracle = (candidate.get("metadata") or {}).get("oracle_metadata") or {}
        valid = valid and (
            baseline.get("outcome") == "FAIL" and
            candidate.get("outcome") == "PASS" and
            removed.get("outcome") == "FAIL" and
            oracle.get("oracle_complete") is True and
            candidate.get("created_regressions") == [] and
            candidate.get("evaluation_only") is True)
    valid = valid and None not in lineages and len(lineages) >= 2
    return ("PASS" if valid else "FAIL"), {
        "case_count": len(cases), "lineage_count": len(lineages),
        "gain_with_delta_m": valid, "gain_removed_without_delta_m": valid,
    }


def _anti_forgetting(path: Path | None) -> tuple[str, dict]:
    if path is None:
        return "NOT_ESTABLISHED", {"reason": "anti-forgetting witness is required"}
    report = _load(path, "anti-forgetting witness")
    if report.get("canonical_memory_mutation") != "none" or report.get("production_runtime_imported") is True:
        return "FAIL", {"reason": "anti-forgetting witness crosses production boundary"}
    witness = report.get("witness")
    if not isinstance(witness, Mapping):
        return "FAIL", {"reason": "anti-forgetting witness projection is missing"}
    checks = {
        "eligible": report.get("eligible") is True,
        "target_replay": witness.get("target_replay_passed") is True,
        "heldout_audit": witness.get("heldout_audit_passed") is True,
        "non_target_regression": witness.get("non_target_regression_free") is True,
        "rollback": witness.get("rollback_verified") is True,
        "production_integration": report.get("production_integration") == "not_attempted",
    }
    return ("PASS" if all(checks.values()) else "FAIL"), {"checks": checks}


def _authority(path: Path | None) -> tuple[str, dict]:
    if path is None:
        return "NOT_ESTABLISHED", {"reason": "independent authority replay receipt is required"}
    report = _load(path, "authority replay report")
    # A production-readiness input must be the output of the read-only
    # ``replay_rule_authority`` boundary.  A caller-owned ``verified=true``
    # bit (or a recorded authority receipt that was never replayed) is not
    # evidence.  Keep every check literal and fail closed so malformed or
    # incomplete authority reports remain distinguishable from an absent one.
    expected_version = "tehm-rule-authority-replay-v1"
    version_ok = report.get("version") == expected_version
    receipt = report.get("receipt")
    if not isinstance(receipt, Mapping):
        receipt = {}
    receipt_id = receipt.get("authority_receipt_id")
    receipt_digest = receipt.get("receipt_digest")
    receipt_id_ok = type(receipt_id) is str and bool(receipt_id.strip())
    receipt_digest_ok = (type(receipt_digest) is str and
                         receipt_digest.startswith("sha256:") and
                         len(receipt_digest) > len("sha256:"))
    gate_status = report.get("gate_status")
    gates_ok = (isinstance(gate_status, Mapping) and
                set(gate_status) == set(REQUIRED_GATES) and
                all(gate_status.get(gate) == "PASS" for gate in REQUIRED_GATES))
    hashes_equal = (
        type(report.get("authority_database_sha256_before")) is str and
        type(report.get("authority_database_sha256_after")) is str and
        report.get("authority_database_sha256_before") ==
        report.get("authority_database_sha256_after"))
    replay_ok = (
        version_ok and report.get("authority_replay_status") == "PASS" and
        report.get("eligible") is True and
        report.get("all_gates_established") is True and gates_ok and
        report.get("database_unchanged") is True and
        report.get("read_only") is True and hashes_equal and
        report.get("decision") == "ALLOW_AUTHORITY_REVIEW" and
        report.get("promotion_attempted") is False and
        report.get("canonical_memory_mutation") == "none" and
        report.get("production_runtime_imported") is not True and
        receipt.get("eligible_stored") is True and receipt_id_ok and
        receipt_digest_ok)
    metrics = {
        "verified": replay_ok,
        "version": report.get("version"),
        "authority_replay_status": report.get("authority_replay_status"),
        "receipt_id": receipt_id,
        "receipt_digest": receipt_digest,
        "gate_status": dict(gate_status) if isinstance(gate_status, Mapping) else None,
        "database_unchanged": report.get("database_unchanged"),
        "read_only": report.get("read_only"),
    }
    if not replay_ok:
        missing = []
        if not version_ok:
            missing.append("replay_version")
        if report.get("authority_replay_status") != "PASS":
            missing.append("authority_replay_status")
        if report.get("eligible") is not True:
            missing.append("eligible")
        if not gates_ok:
            missing.append("six_rule_gates")
        if report.get("database_unchanged") is not True or not hashes_equal:
            missing.append("database_unchanged")
        if report.get("read_only") is not True:
            missing.append("read_only")
        if report.get("decision") != "ALLOW_AUTHORITY_REVIEW":
            missing.append("authority_review_decision")
        if report.get("promotion_attempted") is not False:
            missing.append("promotion_not_attempted")
        if report.get("canonical_memory_mutation") != "none":
            missing.append("canonical_memory_mutation")
        if report.get("production_runtime_imported") is True:
            missing.append("production_runtime")
        if not receipt_id_ok or not receipt_digest_ok or receipt.get("eligible_stored") is not True:
            missing.append("content_bound_receipt")
        metrics["reason"] = "authority_replay_incomplete:" + ",".join(sorted(set(missing)))
    return ("PASS" if replay_ok else "FAIL"), metrics


def _rollback(anti_status: str, anti_metrics: Mapping) -> tuple[str, dict]:
    checks = anti_metrics.get("checks") if isinstance(anti_metrics, Mapping) else None
    verified = anti_status == "PASS" and isinstance(checks, Mapping) and checks.get("rollback") is True
    return ("PASS" if verified else "NOT_ESTABLISHED" if anti_status == "NOT_ESTABLISHED" else "FAIL"), {
        "verified": verified,
    }


@dataclass(frozen=True)
class ProductionReadinessReceipt:
    """Content-addressed, evaluation-only P15-B readiness result."""

    campaign_id: str
    input_refs: tuple[dict, ...]
    gates: dict
    gate_status: dict[str, str]
    metrics: dict
    production_gate: dict
    eligible: bool
    reasons: tuple[str, ...]
    schema_contract_status: str
    version: str = PRODUCTION_READINESS_VERSION
    evaluation_only: bool = True
    canonical_memory_mutation: str = "none"
    production_integration: str = "not_attempted"
    memory_docs_submitted: bool = False

    def to_dict(self) -> dict:
        return {
            "version": self.version, "campaign_id": self.campaign_id,
            "input_refs": [dict(item) for item in self.input_refs],
            "gates": dict(self.gates), "gate_status": dict(self.gate_status),
            "metrics": dict(self.metrics), "production_gate": dict(self.production_gate),
            "eligible": self.eligible, "reasons": list(self.reasons),
            "schema_contract_status": self.schema_contract_status,
            "evaluation_only": self.evaluation_only,
            "canonical_memory_mutation": self.canonical_memory_mutation,
            "production_integration": self.production_integration,
            "memory_docs_submitted": self.memory_docs_submitted,
        }

    @property
    def receipt_digest(self) -> str:
        return _digest(self.to_dict())

    @property
    def receipt_id(self) -> str:
        return "r3_production_readiness_" + self.receipt_digest.split(":", 1)[1][:24]

    @classmethod
    def from_dict(cls, payload: Mapping) -> "ProductionReadinessReceipt":
        if not isinstance(payload, Mapping):
            raise ProductionReadinessError("readiness receipt must be an object")
        required = ("version", "campaign_id", "input_refs", "gates", "gate_status",
                    "metrics", "production_gate", "eligible", "reasons",
                    "schema_contract_status", "evaluation_only",
                    "canonical_memory_mutation", "production_integration",
                    "memory_docs_submitted")
        missing = [key for key in required if key not in payload]
        if missing:
            raise ProductionReadinessError("readiness receipt missing " + ", ".join(missing))
        receipt = cls(
            campaign_id=payload["campaign_id"], input_refs=tuple(dict(item) for item in payload["input_refs"]),
            gates=dict(payload["gates"]), gate_status=dict(payload["gate_status"]),
            metrics=dict(payload["metrics"]), production_gate=dict(payload["production_gate"]),
            eligible=payload["eligible"], reasons=tuple(payload["reasons"]),
            schema_contract_status=payload["schema_contract_status"], version=payload["version"],
            evaluation_only=payload["evaluation_only"],
            canonical_memory_mutation=payload["canonical_memory_mutation"],
            production_integration=payload["production_integration"],
            memory_docs_submitted=payload["memory_docs_submitted"],
        )
        if type(receipt.eligible) is not bool or type(receipt.gates) is not dict or \
                type(receipt.metrics) is not dict or type(receipt.production_gate) is not dict:
            raise ProductionReadinessError("readiness receipt fields are malformed")
        if any(value not in {"PASS", "FAIL", "NOT_ESTABLISHED"}
               for value in receipt.gate_status.values()):
            raise ProductionReadinessError("readiness gate status value is malformed")
        if any(type(item) is not str or not item.strip() for item in receipt.reasons):
            raise ProductionReadinessError("readiness reasons are malformed")
        if receipt.version != PRODUCTION_READINESS_VERSION or receipt.evaluation_only is not True or \
                receipt.canonical_memory_mutation != "none" or receipt.production_integration != "not_attempted" or \
                receipt.memory_docs_submitted is not False:
            raise ProductionReadinessError("readiness receipt crosses production/docs boundary")
        if set(receipt.gate_status) != set(READINESS_GATES):
            raise ProductionReadinessError("readiness gate projection is malformed")
        if receipt.eligible is not all(value == "PASS" for value in receipt.gate_status.values()) or \
                receipt.schema_contract_status != "PASS":
            if receipt.eligible:
                raise ProductionReadinessError("readiness eligible projection is malformed")
        if payload.get("receipt_digest") is not None and payload["receipt_digest"] != receipt.receipt_digest:
            raise ProductionReadinessError("readiness receipt digest mismatch")
        return receipt


def build_production_readiness(*, calibration_report: Path,
                               interference_summary: Path,
                               anti_forgetting: Path | None = None,
                               heldout_delta_m: Path | None = None,
                               authority_report: Path | None = None,
                               schema_contract: Path | None = None,
                               max_mir_upper_ci: float = 0.0,
                               output: Path | None = None) -> dict:
    """Build a read-only P15-B readiness report from explicit evidence files.

    ``max_mir_upper_ci`` is an explicit, content-bound policy parameter.  The
    default remains the historical zero-harm policy (and therefore remains
    fail-closed for any finite Wilson interval); a future campaign may choose a
    non-zero threshold only by recording it in the replayable production-gate
    receipt.
    """
    calibration_report = calibration_report.expanduser().resolve()
    interference_summary = interference_summary.expanduser().resolve()
    for path in (calibration_report, interference_summary, anti_forgetting,
                 heldout_delta_m, authority_report, schema_contract):
        if path is not None and not path.is_file():
            raise ProductionReadinessError(f"readiness input is not a file: {path}")
    refs = [_ref(calibration_report, "calibration_report"),
            _ref(interference_summary, "interference_summary")]
    optional = ((anti_forgetting, "anti_forgetting"), (heldout_delta_m, "heldout_delta_m"),
                (authority_report, "authority_report"), (schema_contract, "schema_contract"))
    refs.extend(_ref(path, name) for path, name in optional if path is not None)
    cal_gates, cal_metrics, _ = _calibration(calibration_report)
    mir_gate, mir_metrics = _interference(
        interference_summary, max_upper_ci=max_mir_upper_ci)
    repair_status, repair_metrics = _repair_pareto(heldout_delta_m)
    anti_status, anti_metrics = _anti_forgetting(anti_forgetting)
    authority_status, authority_metrics = _authority(authority_report)
    rollback_status, rollback_metrics = _rollback(anti_status, anti_metrics)
    schema_status = "NOT_ESTABLISHED"
    schema_metrics = {"reason": "P16 schema contract is required"}
    if schema_contract is not None:
        try:
            schema_receipt = replay_schema_contract(schema_contract)
            schema_status = "PASS"
            schema_metrics = {"receipt_id": schema_receipt.receipt_id,
                              "receipt_digest": schema_receipt.receipt_digest}
        except (OSError, ValueError) as exc:
            schema_status = "FAIL"
            schema_metrics = {"reason": str(exc)}
    gates = {**cal_gates, "mir_upper_ci": mir_gate,
             "repair_pareto": repair_status == "PASS",
             "anti_forgetting": anti_status == "PASS",
             "authority_replay": authority_status == "PASS",
             "rollback": rollback_status == "PASS"}
    status = {name: ("PASS" if gates[name] else
                     "NOT_ESTABLISHED" if name in {"multi_lineage", "reason_stratified_calibration"} and
                     not (cal_gates.get(name) is False) else "FAIL")
              for name in READINESS_GATES}
    # Optional/absent evidence has its own status; do not collapse it into a
    # measured failure.  This distinction is what keeps the preflight from
    # becoming a hidden authority token.
    status["repair_pareto"] = repair_status
    status["anti_forgetting"] = anti_status
    status["authority_replay"] = authority_status
    status["rollback"] = rollback_status
    reasons = tuple(f"{name}:{status[name].lower()}" for name in READINESS_GATES
                    if status[name] != "PASS")
    evidence = {
        "no_skill_calibration": _load(calibration_report, "calibration report").get("no_skill_calibration"),
        "paired_cases": mir_metrics["total_cases"],
        "memory_interference_cases": mir_metrics["harmful_cases"],
        "memory_interference_rate": mir_metrics["point"],
        # Diversity is deliberately not inferred from challenge case count.
        "evidence_refs": refs,
    }
    if rollback_metrics.get("verified"):
        evidence.update({"rollback_verified": True, "rollback_receipt_id": "anti-forgetting-rollback",
                         "rollback_receipt_digest": next((r["sha256"] for r in refs
                                                           if r["name"] == "anti_forgetting"), "")})
    production_gate = evaluate_production_gate(
        evidence, max_memory_interference_rate=max_mir_upper_ci)
    eligible = all(value == "PASS" for value in status.values()) and schema_status == "PASS" and \
        production_gate.eligible
    receipt = ProductionReadinessReceipt(
        campaign_id=cal_metrics.get("campaign_id") or calibration_report.stem,
        input_refs=tuple(refs), gates=gates, gate_status=status,
        metrics={"calibration": cal_metrics, "mir": mir_metrics,
                 "repair_pareto": repair_metrics, "anti_forgetting": anti_metrics,
                 "authority": authority_metrics, "rollback": rollback_metrics,
                 "schema_contract": schema_metrics},
        production_gate={**production_gate.to_dict(), "receipt_digest": production_gate.receipt_digest},
        eligible=eligible, reasons=reasons + (("production_gate:ineligible",)
                                               if not production_gate.eligible else ()),
        schema_contract_status=schema_status)
    report = {"receipt": {**receipt.to_dict(), "receipt_id": receipt.receipt_id,
                           "receipt_digest": receipt.receipt_digest},
              "receipt_id": receipt.receipt_id, "receipt_digest": receipt.receipt_digest,
              "readiness": receipt.to_dict(), "production_integration": "not_attempted",
              "canonical_memory_mutation": "none", "memory_docs_submitted": False}
    if output is not None:
        output = output.expanduser().resolve()
        if output in {calibration_report, interference_summary} or output in {
                path for path, _ in optional if path is not None}:
            raise ProductionReadinessError("readiness output cannot overwrite an input")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def replay_production_readiness(report_path: Path) -> ProductionReadinessReceipt:
    """Replay a readiness report using its content-addressed input paths."""
    report_path = report_path.expanduser().resolve()
    report = _load(report_path, "readiness report")
    payload = report.get("readiness") or report.get("receipt")
    receipt = ProductionReadinessReceipt.from_dict(payload)
    if report.get("receipt_digest") != receipt.receipt_digest:
        raise ProductionReadinessError("readiness receipt digest mismatch")
    if report.get("memory_docs_submitted") is not False or report.get("production_integration") != "not_attempted":
        raise ProductionReadinessError("readiness report crosses production/docs boundary")
    by_name = {item.get("name"): Path(item.get("path")) for item in receipt.input_refs}
    required = {"calibration_report", "interference_summary"}
    if not required <= set(by_name):
        raise ProductionReadinessError("readiness report input refs are incomplete")
    for item in receipt.input_refs:
        path = Path(item["path"])
        if _file_digest(path) != item.get("sha256"):
            raise ProductionReadinessError(f"readiness input digest drifted: {path}")
    production_gate_thresholds = (receipt.production_gate or {}).get("thresholds")
    max_mir_upper_ci = 0.0
    if isinstance(production_gate_thresholds, Mapping) and \
            production_gate_thresholds.get("max_memory_interference_rate") is not None:
        max_mir_upper_ci = production_gate_thresholds[
            "max_memory_interference_rate"]
    result = build_production_readiness(
        calibration_report=by_name["calibration_report"],
        interference_summary=by_name["interference_summary"],
        anti_forgetting=by_name.get("anti_forgetting"),
        heldout_delta_m=by_name.get("heldout_delta_m"),
        authority_report=by_name.get("authority_report"),
        schema_contract=by_name.get("schema_contract"),
        max_mir_upper_ci=max_mir_upper_ci)
    replayed = ProductionReadinessReceipt.from_dict(result["receipt"])
    if replayed.to_dict() != receipt.to_dict():
        raise ProductionReadinessError("readiness replay mismatch")
    return receipt


__all__ = [
    "PRODUCTION_READINESS_VERSION", "READINESS_GATES", "ProductionReadinessError",
    "ProductionReadinessReceipt", "build_production_readiness",
    "replay_production_readiness",
]
