"""Shared backend contracts (design doc 17.1).

These are the data shapes that cross the MemoryBackend seam. ``ExecutionRecord``
is the canonical one defined in ``tehm/canonical/capture.py`` (single source of
truth); the rest are backend-neutral.

Provenance rule for every candidate/proposal: ``source`` is always one of
    cold_start | legacy_memory | tehm_rule
so the runtime can attribute where a proposed action came from (design doc
28.4) — a cold-start catalog hit is never dressed up as a memory hit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from tehm.canonical.capture import ExecutionRecord, ExecutionRecordError

CANDIDATE_SOURCES = ("cold_start", "legacy_memory", "tehm_rule")

# Design doc 17.2 backend names.
BACKEND_NAMES = ("none", "legacy", "tehm")
DEFAULT_BACKEND = "legacy"


@dataclass
class RepairContext:
    """The current repair state the runtime hands to the memory plane.

    v1: flow/signoff context (project dir + reports + config + symptom).
    Phase 10 extends it with the RTL design graph.
    """

    project_dir: Path | None = None
    design_id: str | None = None
    platform: str | None = None
    check: str | None = None
    reports: dict = field(default_factory=dict)
    cfg: dict = field(default_factory=dict)
    symptom_signature: dict | None = None
    structural_graph: dict | None = None
    compatibility_profile: str | None = None
    mechanism_signature: dict | None = None
    failure_graph_digest: str | None = None
    causal_context_digest: str | None = None
    prior_action_digests: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "project_dir": str(self.project_dir) if self.project_dir else None,
            "design_id": self.design_id,
            "platform": self.platform,
            "check": self.check,
            "reports": self.reports,
            "cfg": self.cfg,
            "symptom_signature": self.symptom_signature,
            "structural_graph": self.structural_graph,
            "compatibility_profile": self.compatibility_profile,
            "mechanism_signature": self.mechanism_signature,
            "failure_graph_digest": self.failure_graph_digest,
            "causal_context_digest": self.causal_context_digest,
            "prior_action_digests": list(self.prior_action_digests),
        }


@dataclass
class MemoryQuery:
    """A typed query produced from a RepairContext (design doc 9.2)."""

    query_plan: dict = field(default_factory=dict)       # diagnostic_view: high, ...
    dominant_dimensions: dict = field(default_factory=dict)  # temporal/structural/width_type
    context_ref: str | None = None

    def to_dict(self) -> dict:
        return {
            "query_plan": self.query_plan,
            "dominant_dimensions": self.dominant_dimensions,
            "context_ref": self.context_ref,
        }


@dataclass
class CausalCandidateEvidence:
    """Backend-neutral evaluation evidence; never a production authority."""

    path_id: str
    mechanism_family: str
    evidence_level: str
    score: float


@dataclass
class CapabilityGap:
    """A diagnosed action-space gap awaiting independent asset validation."""

    gap_id: str
    mechanism_family: str
    missing_assets: list[str] = field(default_factory=list)


@dataclass
class MemoryCandidate:
    """A retrieved memory object (rule, episode, symptom strategy)."""

    candidate_id: str
    source: str                     # cold_start | legacy_memory | tehm_rule
    payload: dict = field(default_factory=dict)
    score: float | None = None
    provenance: dict = field(default_factory=dict)

    def validate(self) -> None:
        if self.source not in CANDIDATE_SOURCES:
            raise ValueError(
                f"candidate.source must be one of {CANDIDATE_SOURCES}, "
                f"got {self.source!r}")


@dataclass
class ActivationProposal:
    """Applicable / Executable / Verifiable are stored SEPARATELY (design doc 11)."""

    candidate_id: str
    activation_id: str
    applicability_status: str = "UNRESOLVED"   # APPLICABLE | INAPPLICABLE | UNRESOLVED
    binding: dict | None = None
    obligations: list = field(default_factory=list)
    obligation_coverage: float | None = None


@dataclass
class ActivationResult:
    """The recorded outcome of one executed activation."""

    activation_id: str
    outcome: str = "UNKNOWN"
    produced_transition_id: str | None = None
    created_regressions: list = field(default_factory=list)
    rollback_receipt: dict | None = None


@dataclass
class IngestReceipt:
    """Returned by ``ingest_execution`` after one execution is absorbed."""

    record_id: str
    transition_id: str | None = None
    state_ids: dict = field(default_factory=dict)
    episode_id: str | None = None
    outcome: str = "UNKNOWN"
    backend: str = ""

    def to_dict(self) -> dict:
        return {
            "record_id": self.record_id,
            "transition_id": self.transition_id,
            "state_ids": self.state_ids,
            "episode_id": self.episode_id,
            "outcome": self.outcome,
            "backend": self.backend,
        }


@dataclass
class BuildReport:
    backend: str
    frozen_source: bool = False
    rebuilt: dict = field(default_factory=dict)
    ok: bool = False
    detail: str = ""


@dataclass
class MemorySnapshot:
    """A backend-scoped snapshot id + counts (design doc 17.3 resume check)."""

    backend: str
    snapshot_id: str
    schema_version: str
    counts: dict = field(default_factory=dict)


__all__ = [
    "CANDIDATE_SOURCES", "BACKEND_NAMES", "DEFAULT_BACKEND",
    "RepairContext", "MemoryQuery", "CausalCandidateEvidence", "CapabilityGap",
    "MemoryCandidate", "ActivationProposal",
    "ActivationResult", "IngestReceipt", "BuildReport", "MemorySnapshot",
    "ExecutionRecord", "ExecutionRecordError",
]
