"""Replayable external governance for a non-zero MIR threshold.

The production policy remains ``0.0`` by default.  A finite Wilson interval
cannot establish that threshold, so any future non-zero threshold must be an
explicit decision outside the evidence builder.  This module validates the
shape, content digest, and authority/docs boundary of that decision; it does
not itself grant production authority or change a threshold.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from tehm.ids import stable_dumps


MIR_THRESHOLD_GOVERNANCE_VERSION = "r3-mir-threshold-governance-v1"
MIR_THRESHOLD_GOVERNANCE_REPORT_VERSION = "r3-mir-threshold-governance-report-v1"
DECISION = "APPROVE_NONZERO_MIR_THRESHOLD"
SCOPE = "r3-production-readiness"


class MIRThresholdGovernanceError(ValueError):
    """A non-zero MIR threshold governance receipt is malformed."""


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise MIRThresholdGovernanceError(f"{name} must be a non-empty string")
    return value.strip()


def _digest_text(value: object, name: str) -> str:
    text = _text(value, name)
    if len(text) != len("sha256:") + 64 or not text.startswith("sha256:"):
        raise MIRThresholdGovernanceError(f"{name} must be a sha256 digest")
    if any(char not in "0123456789abcdefABCDEF" for char in text[7:]):
        raise MIRThresholdGovernanceError(f"{name} must be a sha256 digest")
    return text


def _threshold(value: object) -> float:
    if isinstance(value, bool):
        raise MIRThresholdGovernanceError("threshold must be finite and in (0, 1]")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise MIRThresholdGovernanceError(
            "threshold must be finite and in (0, 1]") from exc
    if not math.isfinite(result) or not 0.0 < result <= 1.0:
        raise MIRThresholdGovernanceError("threshold must be finite and in (0, 1]")
    return result


@dataclass(frozen=True)
class MIRThresholdGovernanceReceipt:
    """Content-addressed attestation for one non-zero MIR threshold."""

    threshold: float
    decision_id: str
    approved_by: str
    rationale: str
    evidence_sha256: str
    decision: str = DECISION
    scope: str = SCOPE
    version: str = MIR_THRESHOLD_GOVERNANCE_VERSION
    evaluation_only: bool = True
    canonical_memory_mutation: str = "none"
    production_integration: str = "not_attempted"
    memory_docs_submitted: bool = False

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "scope": self.scope,
            "decision": self.decision,
            "decision_id": self.decision_id,
            "approved_by": self.approved_by,
            "rationale": self.rationale,
            "threshold": self.threshold,
            "evidence_sha256": self.evidence_sha256,
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
        return "r3_mir_threshold_governance_" + self.receipt_digest.split(":", 1)[1][:24]

    @classmethod
    def from_dict(cls, payload: object) -> "MIRThresholdGovernanceReceipt":
        if not isinstance(payload, Mapping):
            raise MIRThresholdGovernanceError("MIR threshold governance receipt must be an object")
        required = {
            "version", "scope", "decision", "decision_id", "approved_by",
            "rationale", "threshold", "evidence_sha256", "evaluation_only",
            "canonical_memory_mutation", "production_integration",
            "memory_docs_submitted",
        }
        missing = sorted(required - set(payload))
        if missing:
            raise MIRThresholdGovernanceError(
                "MIR threshold governance receipt missing " + ", ".join(missing))
        receipt = cls(
            threshold=_threshold(payload["threshold"]), decision_id=payload["decision_id"],
            approved_by=payload["approved_by"], rationale=payload["rationale"],
            evidence_sha256=payload["evidence_sha256"],
            decision=payload["decision"], scope=payload["scope"],
            version=payload["version"],
            evaluation_only=payload["evaluation_only"],
            canonical_memory_mutation=payload["canonical_memory_mutation"],
            production_integration=payload["production_integration"],
            memory_docs_submitted=payload["memory_docs_submitted"],
        )
        _validate(receipt)
        supplied = payload.get("receipt_digest")
        if supplied is not None and supplied != receipt.receipt_digest:
            raise MIRThresholdGovernanceError(
                "MIR threshold governance receipt digest mismatch")
        return receipt


def _validate(receipt: MIRThresholdGovernanceReceipt) -> None:
    if receipt.version != MIR_THRESHOLD_GOVERNANCE_VERSION:
        raise MIRThresholdGovernanceError("MIR threshold governance version mismatch")
    if receipt.scope != SCOPE or receipt.decision != DECISION:
        raise MIRThresholdGovernanceError("MIR threshold governance decision/scope is invalid")
    _threshold(receipt.threshold)
    _text(receipt.decision_id, "decision_id")
    _text(receipt.approved_by, "approved_by")
    _text(receipt.rationale, "rationale")
    _digest_text(receipt.evidence_sha256, "evidence_sha256")
    if receipt.evaluation_only is not True or \
            receipt.canonical_memory_mutation != "none" or \
            receipt.production_integration != "not_attempted" or \
            receipt.memory_docs_submitted is not False:
        raise MIRThresholdGovernanceError(
            "MIR threshold governance crosses an authority/docs boundary")


def replay_mir_threshold_governance(path: Path) -> MIRThresholdGovernanceReceipt:
    """Replay an external governance report and its content-bound receipt."""
    path = Path(path).expanduser().resolve()
    try:
        report = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MIRThresholdGovernanceError(
            f"MIR threshold governance report is not valid JSON: {path}") from exc
    if not isinstance(report, Mapping):
        raise MIRThresholdGovernanceError(
            "MIR threshold governance report must be an object")
    if report.get("version") != MIR_THRESHOLD_GOVERNANCE_REPORT_VERSION:
        raise MIRThresholdGovernanceError(
            "MIR threshold governance report version mismatch")
    receipt = MIRThresholdGovernanceReceipt.from_dict(
        report.get("mir_threshold_governance"))
    if report.get("receipt_id") != receipt.receipt_id or \
            report.get("receipt_digest") != receipt.receipt_digest:
        raise MIRThresholdGovernanceError(
            "MIR threshold governance report id/digest mismatch")
    if report.get("evaluation_only") is not True or \
            report.get("canonical_memory_mutation") != "none" or \
            report.get("production_integration") != "not_attempted" or \
            report.get("memory_docs_submitted") is not False:
        raise MIRThresholdGovernanceError(
            "MIR threshold governance report crosses an authority/docs boundary")
    return receipt


__all__ = [
    "MIR_THRESHOLD_GOVERNANCE_VERSION", "MIR_THRESHOLD_GOVERNANCE_REPORT_VERSION",
    "DECISION", "SCOPE",
    "MIRThresholdGovernanceError", "MIRThresholdGovernanceReceipt",
    "replay_mir_threshold_governance",
]
