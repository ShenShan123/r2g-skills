"""Canonical verified transition (design doc 4.1, 8, 6.2).

``e_t = <S_t, A_t, S_{t+1}, O_t, V_t>``. ``CREATED_REGRESSION`` and
``NEWLY_OBSERVED_FAILURE`` are distinguished (design doc 8): a regression is
``PASS -> FAIL`` on a previously-good oracle; a newly observed failure is
``N/A -> FAIL`` (new oracle / widened scope / exposed deeper bug).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from tehm import SCHEMA_VERSION
from tehm.ids import transition_id
from tehm.canonical.verifier import VerifierSnapshot

# Action domains (design doc 26 Phase 5).
ACTION_DOMAINS = (
    "flow.CONFIG_DELTA", "flow.SDC_EDIT", "flow.STAGE_RERUN",
    "signoff.REPAIR_ACTION", "rtl.AST_REWRITE", "rtl.GUARD_STRENGTHEN",
    "rtl.RESET_RESTORE", "rtl.WIDTH_CORRECT", "rtl.PRIORITY_REORDER",
    "unknown",
)

# Outcome taxonomy used by trajectory_summary (positive / neutral / harmful).
OUTCOMES = ("PASS", "PARTIAL", "NEUTRAL", "UNKNOWN", "FAIL", "REGRESSION")
POSITIVE_OUTCOMES = frozenset({"PASS", "PARTIAL"})
NEUTRAL_OUTCOMES = frozenset({"NEUTRAL", "UNKNOWN"})
HARMFUL_OUTCOMES = frozenset({"FAIL", "REGRESSION"})

ORIGINAL_FAILURE_STATES = ("REMOVED", "PRESENT", "UNKNOWN")
EXPERIMENT_KINDS = ("REPAIR", "OBSERVATION", "UNKNOWN")
UTILITY_VERDICTS = ("PARETO_SAFE", "HARMFUL", "NEUTRAL", "UNKNOWN")


@dataclass(frozen=True)
class Action:
    """One real executed action ``A_t``."""

    domain: str
    transformation_family: str
    payload: dict = field(default_factory=dict)

    def validate(self) -> None:
        if type(self.domain) is not str or not self.domain.strip():
            raise ValueError("action.domain and action.transformation_family are required")
        if type(self.transformation_family) is not str or not self.transformation_family.strip():
            raise ValueError("action.domain and action.transformation_family are required")
        if type(self.payload) is not dict or not self.payload:
            raise ValueError("action.payload must be a non-empty dict (design doc H1)")

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "transformation_family": self.transformation_family,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Action":
        if type(data) is not dict:
            raise ValueError("action must be a mapping")
        required = ("domain", "transformation_family", "payload")
        missing = [key for key in required if key not in data]
        if missing:
            raise ValueError(f"action missing required keys: {missing}")
        obj = cls(
            domain=data["domain"],
            transformation_family=data["transformation_family"],
            payload=data["payload"],
        )
        obj.validate()
        return obj


@dataclass(frozen=True)
class ObservationDelta:
    """Observation delta ``O_t`` between ``S_t`` and ``S_{t+1}``."""

    original_failure: str = "UNKNOWN"        # REMOVED | PRESENT | UNKNOWN
    first_divergence: dict | None = None     # {before, after}
    failing_tests: dict | None = None        # {before, after}
    created_regressions: list = field(default_factory=list)    # risk 8.1
    newly_observed_failures: list = field(default_factory=list)  # risk 8.2
    # A passing oracle is not by itself a repair.  In particular, a
    # clean-before/clean-after physical pair is an observation of an action,
    # not evidence that a failure was repaired.
    experiment_kind: str = "UNKNOWN"       # REPAIR | OBSERVATION | UNKNOWN
    utility_verdict: str = "UNKNOWN"       # physical/Pareto result, separate from oracle

    def validate(self) -> None:
        if type(self.original_failure) is not str:
            raise ValueError("original_failure must be a string")
        if self.original_failure not in ORIGINAL_FAILURE_STATES:
            raise ValueError(
                f"original_failure must be one of {ORIGINAL_FAILURE_STATES}, "
                f"got {self.original_failure!r}")
        for name, val in (("first_divergence", self.first_divergence),
                          ("failing_tests", self.failing_tests)):
            if val is not None and not isinstance(val, dict):
                raise ValueError(f"{name} must be a dict or None")
        for name, val in (("created_regressions", self.created_regressions),
                          ("newly_observed_failures", self.newly_observed_failures)):
            if type(val) is not list:
                raise ValueError(f"{name} must be a list")
        if type(self.experiment_kind) is not str:
            raise ValueError("experiment_kind must be a string")
        if self.experiment_kind not in EXPERIMENT_KINDS:
            raise ValueError(
                f"experiment_kind must be one of {EXPERIMENT_KINDS}, "
                f"got {self.experiment_kind!r}")
        if type(self.utility_verdict) is not str:
            raise ValueError("utility_verdict must be a string")
        if self.utility_verdict not in UTILITY_VERDICTS:
            raise ValueError(
                f"utility_verdict must be one of {UTILITY_VERDICTS}, "
                f"got {self.utility_verdict!r}")

    def to_dict(self) -> dict:
        return {
            "original_failure": self.original_failure,
            "first_divergence": self.first_divergence,
            "failing_tests": self.failing_tests,
            "created_regressions": list(self.created_regressions),
            "newly_observed_failures": list(self.newly_observed_failures),
            "experiment_kind": self.experiment_kind,
            "utility_verdict": self.utility_verdict,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ObservationDelta":
        if type(data) is not dict:
            raise ValueError("observation_delta must be a mapping")
        if "original_failure" not in data:
            raise ValueError("observation_delta missing required key: original_failure")
        for key in ("first_divergence", "failing_tests"):
            if key in data and data[key] is not None and type(data[key]) is not dict:
                raise ValueError(f"observation_delta.{key} must be a dict or None")
        for key in ("created_regressions", "newly_observed_failures"):
            if key in data and type(data[key]) is not list:
                raise ValueError(f"observation_delta.{key} must be a list")
        for key in ("experiment_kind", "utility_verdict"):
            if key in data and type(data[key]) is not str:
                raise ValueError(f"observation_delta.{key} must be a string")
        obj = cls(
            original_failure=data["original_failure"],
            first_divergence=data.get("first_divergence"),
            failing_tests=data.get("failing_tests"),
            created_regressions=data.get("created_regressions", []),
            newly_observed_failures=data.get("newly_observed_failures", []),
            experiment_kind=data.get("experiment_kind", "UNKNOWN"),
            utility_verdict=data.get("utility_verdict", "UNKNOWN"),
        )
        obj.validate()
        return obj


def classify_outcome(delta: ObservationDelta, verifier: VerifierSnapshot) -> str:
    """Deterministic transition outcome (positive / neutral / harmful)."""
    if delta.created_regressions:
        return "REGRESSION"
    if verifier.verdict == "FAIL":
        return "FAIL"
    if verifier.verdict == "UNKNOWN":
        return "UNKNOWN"
    if delta.newly_observed_failures:
        return "PARTIAL"
    if delta.original_failure == "REMOVED":
        return "PASS"
    return "NEUTRAL"


def primary_effect_key(action: Action, delta: ObservationDelta,
                       verifier: VerifierSnapshot,
                       coarse_structural_delta: dict | None = None) -> str:
    """``K_primary`` (design doc 6.2) — the ONE canon, in crystallization.effects.

    Kept here as the capture-time seed so ``tehm_transitions.primary_effect_key``
    and the preflight grouping key always agree.
    """
    from tehm.crystallization.effects import effect_key

    return effect_key(action.to_dict(), delta.to_dict(), verifier.content(),
                      coarse_structural_delta)


@dataclass
class CanonicalTransition:
    """One verified state transition ``e_t`` (the memory atom)."""

    source_state_id: str
    target_state_id: str
    action: Action
    observation_delta: ObservationDelta
    verifier: VerifierSnapshot
    outcome: str = "UNKNOWN"
    primary_effect_key: str = ""
    created_regressions: list = field(default_factory=list)
    newly_observed: list = field(default_factory=list)
    provenance: dict = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def validate(self) -> None:
        if not self.source_state_id or not self.target_state_id:
            raise ValueError("transition needs source and target state ids (H1)")
        self.action.validate()
        self.observation_delta.validate()
        self.verifier.validate()
        if self.outcome not in OUTCOMES:
            raise ValueError(f"outcome must be one of {OUTCOMES}, got {self.outcome!r}")

    @property
    def transition_id(self) -> str:
        return transition_id(
            source_state_id=self.source_state_id,
            target_state_id=self.target_state_id,
            action=self.action.to_dict(),
            observation_delta=self.observation_delta.to_dict(),
            verifier=self.verifier.content(),
        )

    def to_dict(self) -> dict:
        return {
            "transition_id": self.transition_id,
            "source_state_id": self.source_state_id,
            "target_state_id": self.target_state_id,
            "action": self.action.to_dict(),
            "observation_delta": self.observation_delta.to_dict(),
            "verifier": self.verifier.to_dict(),
            "outcome": self.outcome,
            "primary_effect_key": self.primary_effect_key,
            "created_regressions": list(self.created_regressions),
            "newly_observed": list(self.newly_observed),
            "provenance": self.provenance,
            "schema_version": self.schema_version,
        }

    def to_row(self) -> dict:
        from tehm.ids import stable_dumps

        return {
            "transition_id": self.transition_id,
            "source_state_id": self.source_state_id,
            "target_state_id": self.target_state_id,
            "action_domain": self.action.domain,
            "action_json": stable_dumps(self.action.to_dict()),
            "observation_delta_json": stable_dumps(self.observation_delta.to_dict()),
            "verifier_json": stable_dumps(self.verifier.to_dict()),
            "primary_effect_key": self.primary_effect_key,
            "outcome": self.outcome,
            "created_regressions_json": stable_dumps(self.created_regressions),
            "newly_observed_json": stable_dumps(self.newly_observed),
            "provenance_json": stable_dumps(self.provenance),
            "schema_version": self.schema_version,
        }
