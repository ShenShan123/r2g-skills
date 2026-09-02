"""Source-disjoint, fixed-environment P12 ORFS cohort harness.

The cohort layer only assembles evaluation receipts.  It delegates each arm to
``OrfsCandidateOracle`` and never writes canonical memory, lifecycle state, or
production policy.  External RTL/SDC inputs are explicitly digest-bound so a
project config that points outside its directory cannot bypass the cohort's
source-disjoint gate.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tehm.ids import stable_dumps
from tehm.retrieval.structured_candidate import StructuredRepairCandidate

from .candidate_executor import (
    P12_ARMS, PairedCandidateExecutionReceipt, execute_paired_candidates,
)
from .orfs_candidate_oracle import (
    OrfsCandidateOracle, _source_binding, _source_content_binding, _source_inputs,
    _verify_external_source_inputs,
)


ORFS_COHORT_VERSION = "orfs-p12-cohort-v0.1"


class OrfsCohortError(ValueError):
    """A frozen ORFS cohort is malformed or violates a fixed-environment gate."""


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise OrfsCohortError(f"ORFS cohort {name} is required")
    return value.strip()


def _sha256_text(value: object, name: str) -> str:
    text = _text(value, name)
    if not text.startswith("sha256:") or len(text) <= len("sha256:"):
        raise OrfsCohortError(f"ORFS cohort {name} must be a sha256 digest")
    return text


def _budget_value(value: int | Mapping) -> int:
    if isinstance(value, Mapping):
        raw = value.get("candidate_budget", value.get("total_budget", 3))
    else:
        raw = value
    if type(raw) is not int or not 1 <= raw <= 3:
        raise OrfsCohortError("ORFS cohort candidate budget must be between one and three")
    return raw


def _source_digest(case: Mapping) -> str:
    project_value = case.get("project_dir")
    if type(project_value) is not str or not project_value.strip():
        raise OrfsCohortError("ORFS cohort case requires project_dir")
    project = Path(project_value).expanduser().resolve()
    if not project.is_dir():
        raise OrfsCohortError("ORFS cohort case project_dir is not a directory")
    try:
        source_inputs = _source_inputs(case.get("source_inputs"))
        actual = _source_binding(project, source_inputs)
    except Exception as exc:
        if isinstance(exc, OrfsCohortError):
            raise
        raise OrfsCohortError(str(exc)) from exc
    expected = _sha256_text(case.get("source_digest"), "source_digest")
    if not source_inputs:
        raise OrfsCohortError(
            "ORFS cohort requires explicit source_inputs for source-disjoint evidence")
    if expected != actual:
        raise OrfsCohortError("ORFS cohort source freeze digest mismatch")
    return actual


def _normalize_oracle(oracle: object) -> OrfsCandidateOracle:
    if oracle is None:
        return OrfsCandidateOracle()
    if isinstance(oracle, OrfsCandidateOracle):
        return oracle
    raise OrfsCohortError("ORFS cohort oracle must be OrfsCandidateOracle")


def _outcome_counts(receipts: Mapping[str, PairedCandidateExecutionReceipt]) -> dict:
    counts = {
        arm: {"PASS": 0, "FAIL": 0, "UNKNOWN": 0, "PARTIAL": 0}
        for arm in P12_ARMS
    }
    for bundle in receipts.values():
        for arm in P12_ARMS:
            outcome = bundle.arm_receipts[arm].outcome
            counts[arm][outcome] = counts[arm].get(outcome, 0) + 1
    return counts


@dataclass(frozen=True)
class OrfsPairedCohortReceipt:
    """Replayable receipt for a fixed, source-disjoint ORFS cohort."""

    campaign_id: str
    case_receipts: dict[str, PairedCandidateExecutionReceipt]
    source_digests: dict[str, str]
    source_content_digests: dict[str, str]
    candidate_budget: int
    toolchain_digest: str
    oracle_digest: str
    platform_digest: str
    pdk_digest: str
    campaign_manifest_digest: str
    source_disjoint: bool = True
    source_restore_verified: bool = True
    evaluation_only: bool = True
    version: str = ORFS_COHORT_VERSION

    def __post_init__(self) -> None:
        _text(self.campaign_id, "campaign_id")
        if not isinstance(self.case_receipts, dict) or not self.case_receipts:
            raise OrfsCohortError("ORFS cohort requires at least one case receipt")
        if (not isinstance(self.source_digests, dict) or
                set(self.source_digests) != set(self.case_receipts)):
            raise OrfsCohortError("ORFS cohort source digests do not match cases")
        if (not isinstance(self.source_content_digests, dict) or
                set(self.source_content_digests) != set(self.case_receipts)):
            raise OrfsCohortError(
                "ORFS cohort source content digests do not match cases")
        if any(not isinstance(value, PairedCandidateExecutionReceipt)
               for value in self.case_receipts.values()):
            raise OrfsCohortError("ORFS cohort case receipt is invalid")
        if any(bundle.case_id != case_id or
               bundle.candidate_budget != self.candidate_budget
               for case_id, bundle in self.case_receipts.items()):
            raise OrfsCohortError("ORFS cohort case or budget mismatch")
        if any(not _sha256_text(value, "source_digest")
               for value in self.source_digests.values()):
            raise OrfsCohortError("ORFS cohort source digest is invalid")
        if len(set(self.source_digests.values())) != len(self.source_digests):
            raise OrfsCohortError("ORFS cohort sources are not disjoint")
        if any(not _sha256_text(value, "source_content_digest")
               for value in self.source_content_digests.values()):
            raise OrfsCohortError("ORFS cohort source content digest is invalid")
        if (len(set(self.source_content_digests.values())) !=
                len(self.source_content_digests)):
            raise OrfsCohortError("ORFS cohort source content is not disjoint")
        if not self.source_disjoint or not self.source_restore_verified:
            raise OrfsCohortError("ORFS cohort source-disjoint/restore gates are false")
        if self.evaluation_only is not True:
            raise OrfsCohortError("ORFS cohort must be evaluation-only")
        if type(self.candidate_budget) is not int or not 1 <= self.candidate_budget <= 3:
            raise OrfsCohortError("ORFS cohort candidate budget is invalid")
        for name in ("toolchain_digest", "oracle_digest", "platform_digest",
                     "pdk_digest", "campaign_manifest_digest"):
            _sha256_text(getattr(self, name), name)
        if any(bundle.toolchain_digest != self.toolchain_digest or
               bundle.oracle_digest != self.oracle_digest
               for bundle in self.case_receipts.values()):
            raise OrfsCohortError("ORFS cohort toolchain/oracle digest mismatch")

    @property
    def outcome_counts(self) -> dict:
        return _outcome_counts(self.case_receipts)

    @property
    def no_skill_reason_counts(self) -> dict[str, int]:
        counts = {"NO_MATCH": 0, "STATE_SHIFT": 0, "RISK": 0}
        for bundle in self.case_receipts.values():
            if bundle.no_skill_reason is not None:
                counts[bundle.no_skill_reason] += 1
        return counts

    @property
    def lineage_ids(self) -> dict[str, str]:
        return {case_id: (bundle.lineage_id or case_id)
                for case_id, bundle in sorted(self.case_receipts.items())}

    @property
    def lineage_count(self) -> int:
        return len(set(self.lineage_ids.values()))

    @property
    def receipt_digest(self) -> str:
        return _digest(self.to_dict())

    @property
    def legacy_receipt_digest(self) -> str:
        payload = self.to_dict()
        payload.pop("no_skill_reason_counts", None)
        payload.pop("lineage_ids", None)
        for value in payload["case_receipts"].values():
            value.pop("no_skill_reason", None)
            value.pop("state_shift_receipt_id", None)
            value.pop("risk_receipt_id", None)
            value.pop("lineage_id", None)
            value.pop("routing_receipt_id", None)
            value.pop("routing_decision", None)
        return _digest(payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "campaign_id": self.campaign_id,
            "case_receipts": {
                case_id: receipt.to_dict()
                for case_id, receipt in sorted(self.case_receipts.items())
            },
            "source_digests": dict(sorted(self.source_digests.items())),
            "source_content_digests": dict(
                sorted(self.source_content_digests.items())),
            "candidate_budget": self.candidate_budget,
            "toolchain_digest": self.toolchain_digest,
            "oracle_digest": self.oracle_digest,
            "platform_digest": self.platform_digest,
            "pdk_digest": self.pdk_digest,
            "campaign_manifest_digest": self.campaign_manifest_digest,
            "source_disjoint": self.source_disjoint,
            "source_restore_verified": self.source_restore_verified,
            "evaluation_only": self.evaluation_only,
            "outcome_counts": self.outcome_counts,
            "no_skill_reason_counts": self.no_skill_reason_counts,
            "lineage_ids": self.lineage_ids,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "OrfsPairedCohortReceipt":
        if not isinstance(payload, Mapping):
            raise OrfsCohortError("ORFS cohort receipt must be an object")
        required = {
            "campaign_id", "case_receipts", "source_digests", "candidate_budget",
            "source_content_digests",
            "toolchain_digest", "oracle_digest", "platform_digest", "pdk_digest",
            "campaign_manifest_digest", "source_disjoint", "source_restore_verified",
            "evaluation_only",
        }
        if not required <= set(payload):
            raise OrfsCohortError("ORFS cohort receipt is missing fields")
        raw_cases = payload["case_receipts"]
        if not isinstance(raw_cases, Mapping):
            raise OrfsCohortError("ORFS cohort case receipts are missing")
        receipt = cls(
            campaign_id=payload["campaign_id"],
            case_receipts={
                str(case_id): PairedCandidateExecutionReceipt.from_dict(value)
                for case_id, value in raw_cases.items()
            },
            source_digests=dict(payload["source_digests"]),
            source_content_digests=dict(payload["source_content_digests"]),
            candidate_budget=payload["candidate_budget"],
            toolchain_digest=payload["toolchain_digest"],
            oracle_digest=payload["oracle_digest"],
            platform_digest=payload["platform_digest"],
            pdk_digest=payload["pdk_digest"],
            campaign_manifest_digest=payload["campaign_manifest_digest"],
            source_disjoint=payload["source_disjoint"],
            source_restore_verified=payload["source_restore_verified"],
            evaluation_only=payload["evaluation_only"],
            version=payload.get("version", ORFS_COHORT_VERSION),
        )
        supplied = payload.get("receipt_digest")
        if supplied is not None and supplied not in {
                receipt.receipt_digest, receipt.legacy_receipt_digest}:
            raise OrfsCohortError("ORFS cohort receipt digest mismatch")
        return receipt


def execute_orfs_paired_cohort(
        cases: Sequence[Mapping],
        arm_candidates: Mapping[str, Mapping[str, StructuredRepairCandidate | None]],
        *, campaign_id: str, campaign_manifest_digest: str,
        platform_digest: str, pdk_digest: str,
        oracle: OrfsCandidateOracle | None = None,
        budget: int | Mapping = 3,
        toolchain_digest: str | None = None,
        oracle_digest: str | None = None,
        min_lineages: int = 1) -> OrfsPairedCohortReceipt:
    """Execute a fixed-environment, source-disjoint ORFS P12 cohort."""
    campaign_id = _text(campaign_id, "campaign_id")
    campaign_manifest_digest = _sha256_text(
        campaign_manifest_digest, "campaign_manifest_digest")
    platform_digest = _sha256_text(platform_digest, "platform_digest")
    pdk_digest = _sha256_text(pdk_digest, "pdk_digest")
    if (not isinstance(cases, Sequence) or isinstance(cases, (str, bytes)) or
            not cases):
        raise OrfsCohortError("ORFS cohort cases must be a non-empty sequence")
    if not isinstance(arm_candidates, Mapping):
        raise OrfsCohortError("ORFS cohort arm_candidates must be an object")
    budget_value = _budget_value(budget)
    if type(min_lineages) is not int or not 1 <= min_lineages:
        raise OrfsCohortError("ORFS cohort min_lineages must be a positive integer")
    runner = _normalize_oracle(oracle)
    seen_ids: set[str] = set()
    seen_sources: set[str] = set()
    frozen_cases: dict[str, Mapping] = {}
    source_digests: dict[str, str] = {}
    source_content_digests: dict[str, str] = {}
    declared_lineages: set[str] = set()
    expected_toolchain = toolchain_digest
    expected_oracle = oracle_digest

    for raw_case in cases:
        if not isinstance(raw_case, Mapping):
            raise OrfsCohortError("ORFS cohort case must be an object")
        case_id = _text(raw_case.get("case_id"), "case_id")
        if case_id in seen_ids:
            raise OrfsCohortError("ORFS cohort case IDs must be unique")
        seen_ids.add(case_id)
        lineage_id = raw_case.get("lineage_id")
        if lineage_id is None:
            if min_lineages > 1:
                raise OrfsCohortError(
                    "ORFS cohort min_lineages requires explicit lineage_id for every case")
        elif (type(lineage_id) is not str or not lineage_id.strip() or
              lineage_id != lineage_id.strip()):
            raise OrfsCohortError("ORFS cohort case lineage_id is invalid")
        else:
            declared_lineages.add(lineage_id.strip())
        source_digest = _source_digest(raw_case)
        if source_digest in seen_sources:
            raise OrfsCohortError("ORFS cohort source files/content must be disjoint")
        seen_sources.add(source_digest)
        try:
            source_content_digest = _source_content_binding(
                Path(str(raw_case["project_dir"])).expanduser().resolve(),
                _source_inputs(raw_case.get("source_inputs")))
        except Exception as exc:
            raise OrfsCohortError(str(exc)) from exc
        if source_content_digest in source_content_digests.values():
            raise OrfsCohortError("ORFS cohort source content is not disjoint")
        case_toolchain = _sha256_text(raw_case.get("toolchain_digest"),
                                      "case toolchain_digest")
        case_oracle = _sha256_text(raw_case.get("oracle_digest"),
                                   "case oracle_digest")
        expected_toolchain = expected_toolchain or case_toolchain
        expected_oracle = expected_oracle or case_oracle
        if (case_toolchain != expected_toolchain or
                case_oracle != expected_oracle):
            raise OrfsCohortError("ORFS cohort toolchain/oracle digest is not fixed")
        if (_sha256_text(raw_case.get("platform_digest"), "case platform_digest")
                != platform_digest):
            raise OrfsCohortError("ORFS cohort platform digest is not fixed")
        if (_sha256_text(raw_case.get("pdk_digest"), "case pdk_digest")
                != pdk_digest):
            raise OrfsCohortError("ORFS cohort PDK digest is not fixed")
        frozen_cases[case_id] = raw_case
        source_digests[case_id] = source_digest
        source_content_digests[case_id] = source_content_digest

    if set(arm_candidates) != seen_ids:
        raise OrfsCohortError("ORFS cohort candidates must cover exactly all cases")
    if min_lineages > 1 and len(declared_lineages) < min_lineages:
        raise OrfsCohortError(
            "ORFS cohort does not contain the required distinct lineages")
    receipts: dict[str, PairedCandidateExecutionReceipt] = {}
    for case_id, case in frozen_cases.items():
        arms = arm_candidates[case_id]
        if not isinstance(arms, Mapping) or set(arms) != set(P12_ARMS):
            raise OrfsCohortError(
                f"ORFS cohort case {case_id} lacks exactly four P12 arms")
        bundle = execute_paired_candidates(
            case, arms, oracle=runner, budget=budget,
            no_skill_reason=case.get("no_skill_reason"),
            state_shift_receipt_id=case.get("state_shift_receipt_id"),
            risk_receipt_id=case.get("risk_receipt_id"),
            risk_receipt=case.get("risk_receipt"),
            lineage_id=case.get("lineage_id"),
            routing_receipt_id=case.get("routing_receipt_id"),
            routing_decision=case.get("routing_decision"))
        if (bundle.toolchain_digest != expected_toolchain or
                bundle.oracle_digest != expected_oracle):
            raise OrfsCohortError("ORFS cohort execution digest drift")
        project = Path(str(case["project_dir"])).expanduser().resolve()
        source_inputs = _source_inputs(case.get("source_inputs"))
        if (_source_binding(project, source_inputs) != source_digests[case_id] or
                _source_content_binding(project, source_inputs) !=
                source_content_digests[case_id]):
            raise OrfsCohortError(
                f"ORFS cohort project source changed during execution: {case_id}")
        _verify_external_source_inputs(source_inputs)
        receipts[case_id] = bundle

    return OrfsPairedCohortReceipt(
        campaign_id=campaign_id, case_receipts=receipts,
        source_digests=source_digests, candidate_budget=budget_value,
        source_content_digests=source_content_digests,
        toolchain_digest=_sha256_text(expected_toolchain, "toolchain_digest"),
        oracle_digest=_sha256_text(expected_oracle, "oracle_digest"),
        platform_digest=platform_digest, pdk_digest=pdk_digest,
        campaign_manifest_digest=campaign_manifest_digest)


__all__ = [
    "ORFS_COHORT_VERSION", "OrfsCohortError", "OrfsPairedCohortReceipt",
    "execute_orfs_paired_cohort",
]
