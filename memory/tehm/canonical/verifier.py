"""Verification evidence layering (design doc 12).

``V_t = (verdict, oracle_type, scope, confidence, evidence)``. Evidence tiers:
    F  formal equivalence / property proof   (asserts under given assumptions)
    R  frozen regression / differential sim  (no error observed in regression scope)
    T  target test only                      (current target failure fixed)
    H  compile / lint / heuristic            (structural/syntax only)

Rule confidence and activation confidence are kept separate at the activation
layer; this module owns the per-verification ``VerifierSnapshot``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tehm import SCHEMA_VERSION

VERDICTS = ("PASS", "FAIL", "UNKNOWN")
ORACLE_TYPES = (
    "FORMAL", "REGRESSION", "DIFFERENTIAL_SIM", "TARGET_TEST",
    "COMPILE", "LINT", "UNKNOWN",
)
CONFIDENCE_TIERS = ("F", "R", "T", "H")

# Design doc 12 table: oracle kind -> evidence tier.
_ORACLE_TIER = {
    "FORMAL": "F",
    "REGRESSION": "R",
    "DIFFERENTIAL_SIM": "R",
    "TARGET_TEST": "T",
    "COMPILE": "H",
    "LINT": "H",
}


@dataclass(frozen=True)
class VerifierSnapshot:
    """The evidence carried by one verified transition (or a state snapshot)."""

    verdict: str
    oracle_type: str = "UNKNOWN"
    scope: str = "unknown_scope"
    confidence_tier: str = "H"
    obligation_coverage: float | None = None
    # Completeness of the declared oracle set is distinct from whether the
    # action improved utility/Pareto metrics.
    oracle_complete: bool | None = None
    evidence_refs: list[str] = field(default_factory=list)
    extractor_version: str = "verifier-v0.1"
    tool_versions: dict | None = None
    # Optional campaign provenance checks.  These are retained in the
    # verifier receipt so a replay can explain why an otherwise passing oracle
    # was quarantined; they are deliberately excluded from ``content()`` to
    # preserve transition IDs for pre-v4 evidence that has no such fields.
    input_binding: dict | None = None
    timing_contract: dict | None = None
    # Optional expanded Batch-0 receipt.  It explains the aggregate oracle
    # decision without making every individual report part of the transition
    # content digest (and therefore preserves deterministic IDs for older
    # evidence).
    full_oracle: dict | None = None
    # A source-bound semantic contract can explain why a physically complete
    # before arm is unacceptable.  It is provenance, not a replacement for
    # the physical oracle, and is intentionally excluded from ``content()``.
    semantic_oracle: dict | None = None

    def validate(self) -> None:
        if self.verdict not in VERDICTS:
            raise ValueError(f"verdict must be one of {VERDICTS}, got {self.verdict!r}")
        if self.oracle_type not in ORACLE_TYPES:
            raise ValueError(f"oracle_type must be one of {ORACLE_TYPES}, got {self.oracle_type!r}")
        if self.confidence_tier not in CONFIDENCE_TIERS:
            raise ValueError(
                f"confidence_tier must be one of {CONFIDENCE_TIERS}, got {self.confidence_tier!r}")
        if self.obligation_coverage is not None and not (0.0 <= self.obligation_coverage <= 1.0):
            raise ValueError(f"obligation_coverage must be in [0,1], got {self.obligation_coverage}")
        if self.oracle_complete is not None and not isinstance(self.oracle_complete, bool):
            raise ValueError("oracle_complete must be bool or None")
        for name, value in (("input_binding", self.input_binding),
                            ("timing_contract", self.timing_contract),
                            ("full_oracle", self.full_oracle),
                            ("semantic_oracle", self.semantic_oracle)):
            if value is not None and not isinstance(value, dict):
                raise ValueError(f"{name} must be a mapping or None")

    @property
    def is_pass(self) -> bool:
        return self.verdict == "PASS"

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "oracle_type": self.oracle_type,
            "scope": self.scope,
            "confidence_tier": self.confidence_tier,
            "obligation_coverage": self.obligation_coverage,
            "oracle_complete": self.oracle_complete,
            "evidence_refs": list(self.evidence_refs),
            "extractor_version": self.extractor_version,
            "tool_versions": self.tool_versions,
            "input_binding": self.input_binding,
            "timing_contract": self.timing_contract,
            "full_oracle": self.full_oracle,
            "semantic_oracle": self.semantic_oracle,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "VerifierSnapshot":
        obj = cls(
            verdict=str(data.get("verdict", "UNKNOWN")),
            oracle_type=str(data.get("oracle_type", "UNKNOWN")),
            scope=str(data.get("scope", "unknown_scope")),
            confidence_tier=str(data.get("confidence_tier", "H")),
            obligation_coverage=data.get("obligation_coverage"),
            oracle_complete=data.get("oracle_complete"),
            evidence_refs=list(data.get("evidence_refs", [])),
            extractor_version=str(data.get("extractor_version", "verifier-v0.1")),
            tool_versions=data.get("tool_versions"),
            input_binding=data.get("input_binding"),
            timing_contract=data.get("timing_contract"),
            full_oracle=data.get("full_oracle"),
            semantic_oracle=data.get("semantic_oracle"),
        )
        obj.validate()
        return obj

    @classmethod
    def from_oracle_result(cls, result: dict) -> "VerifierSnapshot":
        """Build from a raw oracle result dict (e.g. reports/drc.json verdict).

        If no confidence_tier is supplied it is derived from the oracle_type
        (design doc 12); UNKNOWN oracle types default to tier H but never lie
        about the verdict.
        """
        oracle_type = str(result.get("oracle_type", "UNKNOWN"))
        tier = str(result.get("confidence_tier", _ORACLE_TIER.get(oracle_type, "H")))
        return cls(
            verdict=str(result.get("verdict", "UNKNOWN")),
            oracle_type=oracle_type,
            scope=str(result.get("scope", "unknown_scope")),
            confidence_tier=tier,
            obligation_coverage=result.get("obligation_coverage"),
            oracle_complete=result.get("oracle_complete"),
            evidence_refs=list(result.get("evidence_refs", [])),
            extractor_version=str(result.get("extractor_version", "verifier-v0.1")),
            tool_versions=result.get("tool_versions"),
            input_binding=result.get("input_binding"),
            timing_contract=result.get("timing_contract"),
        )

    def content(self) -> dict:
        """Digest-relevant content (no extractor/scope cosmetics)."""
        return {
            "verdict": self.verdict,
            "oracle_type": self.oracle_type,
            "confidence_tier": self.confidence_tier,
            "obligation_coverage": self.obligation_coverage,
            "oracle_complete": self.oracle_complete,
            "evidence_refs": self.evidence_refs,
        }


def toolchain_snapshot(tool_versions: dict | None = None,
                       oracle_available: dict | None = None) -> dict:
    """State-level verifier snapshot: the available oracle set + tool versions.

    Stored in ``tehm_states.verifier_snapshot_json``. Not a verdict — states
    carry the toolchain, transitions carry the verdict (H1).
    """
    return {
        "verdict": "UNKNOWN",
        "oracle_type": "UNKNOWN",
        "scope": "state_toolchain",
        "confidence_tier": "H",
        "tool_versions": tool_versions or {},
        "oracle_available": oracle_available or {},
        "schema_version": SCHEMA_VERSION,
    }


def result_dict(obj: Any) -> dict:
    if isinstance(obj, VerifierSnapshot):
        return obj.to_dict()
    if isinstance(obj, dict):
        return dict(obj)
    raise TypeError(f"cannot convert {type(obj).__name__} to verifier dict")
