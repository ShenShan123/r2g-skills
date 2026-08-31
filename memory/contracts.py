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
from collections.abc import Mapping
import hashlib
import json

from tehm.canonical.capture import ExecutionRecord, ExecutionRecordError
from tehm.ids import stable_dumps

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


MEMORY_ROUTING_DECISIONS = (
    "APPLY", "CONSIDER", "ABSTAIN", "INAPPLICABLE", "NO_SKILL",
)


def _routing_mapping(value: object, field_name: str) -> dict:
    """Validate a routing receipt field without accepting opaque objects."""
    if not isinstance(value, Mapping):
        raise ValueError(f"memory routing {field_name} must be an object")
    try:
        decoded = json.loads(stable_dumps(dict(value)))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"memory routing {field_name} must be JSON-serializable") from exc
    if not isinstance(decoded, dict):  # pragma: no cover - serializer guarantee
        raise ValueError(f"memory routing {field_name} must be an object")
    return decoded


@dataclass(frozen=True)
class MemoryRoutingDecision:
    """Content-addressed NO_SKILL/memory-router shadow decision.

    This is deliberately a backend-neutral receipt.  It describes what the
    memory plane *would* contribute to candidate generation; it does not carry
    an executable action and cannot grant lifecycle or runtime authority.
    """

    decision: str
    resolved_state_id: str
    selected_rule_ids: tuple[str, ...]
    selected_path_ids: tuple[str, ...]
    selected_asset_ids: tuple[str, ...]
    applicability: dict
    causal_support: dict
    risk: dict
    abstain_reasons: tuple[str, ...]
    no_memory_budget: int
    memory_budget: int

    def __post_init__(self) -> None:
        if self.decision not in MEMORY_ROUTING_DECISIONS:
            raise ValueError(
                f"memory routing decision must be one of "
                f"{MEMORY_ROUTING_DECISIONS}, got {self.decision!r}")
        for field_name in ("resolved_state_id",):
            value = getattr(self, field_name)
            if type(value) is not str or not value:
                raise ValueError(f"memory routing {field_name} must be non-empty")
        for field_name in (
                "selected_rule_ids", "selected_path_ids", "selected_asset_ids",
                "abstain_reasons"):
            values = getattr(self, field_name)
            if not isinstance(values, tuple):
                raise ValueError(f"memory routing {field_name} must be a tuple")
            if any(type(item) is not str or not item for item in values):
                raise ValueError(
                    f"memory routing {field_name} must contain non-empty strings")
            if len(set(values)) != len(values):
                raise ValueError(f"memory routing {field_name} must not contain duplicates")
        _routing_mapping(self.applicability, "applicability")
        _routing_mapping(self.causal_support, "causal_support")
        _routing_mapping(self.risk, "risk")
        for field_name in ("no_memory_budget", "memory_budget"):
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise ValueError(f"memory routing {field_name} must be a non-negative integer")
        # Every decision retains an unbiased no-memory arm.  The second slot
        # allowed by the APPLY policy may be a causal candidate; memory never
        # consumes the complete candidate budget.
        if self.no_memory_budget < 1:
            raise ValueError("memory routing requires at least one no-memory candidate")
        if self.memory_budget > 2:
            raise ValueError("memory routing shadow budget allows at most two memory/causal candidates")
        if self.decision in {"ABSTAIN", "INAPPLICABLE", "NO_SKILL"} and self.memory_budget:
            raise ValueError(
                f"{self.decision} cannot allocate a memory candidate budget")
        if self.decision == "NO_SKILL" and self.selected_asset_ids:
            raise ValueError("NO_SKILL cannot select assets")

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "resolved_state_id": self.resolved_state_id,
            "selected_rule_ids": list(self.selected_rule_ids),
            "selected_path_ids": list(self.selected_path_ids),
            "selected_asset_ids": list(self.selected_asset_ids),
            "applicability": _routing_mapping(self.applicability, "applicability"),
            "causal_support": _routing_mapping(self.causal_support, "causal_support"),
            "risk": _routing_mapping(self.risk, "risk"),
            "abstain_reasons": list(self.abstain_reasons),
            "no_memory_budget": self.no_memory_budget,
            "memory_budget": self.memory_budget,
        }

    @property
    def decision_digest(self) -> str:
        return "sha256:" + hashlib.sha256(
            stable_dumps(self.to_dict()).encode()).hexdigest()

    @property
    def routing_receipt_id(self) -> str:
        """Stable receipt reference used when attributing candidate outcomes."""
        return "routing_" + self.decision_digest.split(":", 1)[1][:24]

    @classmethod
    def from_dict(cls, payload: object) -> "MemoryRoutingDecision":
        if not isinstance(payload, Mapping):
            raise ValueError("memory routing decision must be an object")
        required = {
            "decision", "resolved_state_id", "selected_rule_ids",
            "selected_path_ids", "selected_asset_ids", "applicability",
            "causal_support", "risk", "abstain_reasons", "no_memory_budget",
            "memory_budget",
        }
        if any(key not in payload for key in required):
            raise ValueError("memory routing decision is missing required fields")
        for field_name in (
                "selected_rule_ids", "selected_path_ids", "selected_asset_ids",
                "abstain_reasons"):
            value = payload[field_name]
            if (not isinstance(value, (list, tuple)) or
                    isinstance(value, (str, bytes))):
                raise ValueError(
                    f"memory routing {field_name} must be a sequence")
        for field_name in ("applicability", "causal_support", "risk"):
            if not isinstance(payload[field_name], Mapping):
                raise ValueError(f"memory routing {field_name} must be an object")
        try:
            decision = cls(
                decision=payload["decision"],
                resolved_state_id=payload["resolved_state_id"],
                selected_rule_ids=tuple(payload["selected_rule_ids"]),
                selected_path_ids=tuple(payload["selected_path_ids"]),
                selected_asset_ids=tuple(payload["selected_asset_ids"]),
                applicability=dict(payload["applicability"]),
                causal_support=dict(payload["causal_support"]),
                risk=dict(payload["risk"]),
                abstain_reasons=tuple(payload["abstain_reasons"]),
                no_memory_budget=payload["no_memory_budget"],
                memory_budget=payload["memory_budget"],
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("memory routing decision is malformed") from exc
        supplied = payload.get("decision_digest")
        if supplied is not None and supplied != decision.decision_digest:
            raise ValueError("memory routing decision digest mismatch")
        return decision


@dataclass(frozen=True)
class NoSkillReceipt:
    """Small explicit receipt for a deliberate no-memory route."""

    routing_receipt_id: str
    resolved_state_id: str
    reason: str
    no_memory_budget: int
    memory_budget: int = 0

    def __post_init__(self) -> None:
        if (type(self.routing_receipt_id) is not str or
                not self.routing_receipt_id):
            raise ValueError("no-skill routing_receipt_id is required")
        if type(self.resolved_state_id) is not str or not self.resolved_state_id:
            raise ValueError("no-skill resolved_state_id is required")
        if type(self.reason) is not str or not self.reason:
            raise ValueError("no-skill reason is required")
        if type(self.no_memory_budget) is not int or self.no_memory_budget < 1:
            raise ValueError("no-skill no_memory_budget must be positive")
        if self.memory_budget != 0:
            raise ValueError("no-skill memory_budget must be zero")

    def to_dict(self) -> dict:
        return {
            "routing_receipt_id": self.routing_receipt_id,
            "resolved_state_id": self.resolved_state_id,
            "reason": self.reason,
            "no_memory_budget": self.no_memory_budget,
            "memory_budget": self.memory_budget,
        }


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
    "MemoryCandidate", "MEMORY_ROUTING_DECISIONS", "MemoryRoutingDecision",
    "NoSkillReceipt", "ActivationProposal",
    "ActivationResult", "IngestReceipt", "BuildReport", "MemorySnapshot",
    "ExecutionRecord", "ExecutionRecordError",
]
