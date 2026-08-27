"""PredicateExtractor: tri-valued design predicates (design doc 6.5).

``TruthValue = TRUE | FALSE | UNKNOWN`` and ``UNKNOWN != FALSE`` (H3). A missing
observation yields UNKNOWN, never negative evidence. Predicates and roles share
the feature extractor; the extractor version is stamped into every observation
for traceability.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from tehm import PREDICATE_SCHEMA_VERSION
from tehm.graph.feature_extractor import FeatureValue, extract_features
from tehm.graph.local_design_graph import LocalDesignGraph

TRUTH_VALUES = ("TRUE", "FALSE", "UNKNOWN")
TruthValue = str

_CLEAN = frozenset({"clean", "clean_beol", "complete", "skipped"})

# Predicate schema (mirror: tehm/schemas/predicate_v1.yaml).
# Each spec says how to evaluate against the feature map ``F``.
PREDICATE_SCHEMA: dict[str, dict] = {
    # target failing check (any non-clean CHECK present)
    "target_check_failed": {"kind": "any_check_failed"},
    # DRC / LVS / timing cleanliness
    "drc_clean": {"kind": "check_clean", "check": "drc"},
    "lvs_clean": {"kind": "check_clean", "check": "lvs"},
    "timing_clean": {"kind": "check_clean", "check": "timing"},
    "route_clean": {"kind": "check_clean", "check": "route"},
    # antenna violation class present
    "antenna_present": {"kind": "violation_class_prefix", "prefix": "antenna"},
    # density / route-related violation present
    "density_violation_present": {"kind": "violation_class_prefix", "prefix": "density"},
    # Not observable in the flow/signoff v1 graph -> always UNKNOWN unless a
    # caller supplies evidence (design doc 22.2 RTL diagnostics).
    "single_clock_domain": {"kind": "unobserved"},
    "synchronous_reset": {"kind": "unobserved"},
    "handshake_completion_precedes_transition": {"kind": "unobserved"},
    "reset_semantics_preserved": {"kind": "unobserved"},
}

EXTRACTOR_VERSION = "predicates-v0.1"


@dataclass(frozen=True)
class PredicateObservation:
    value: TruthValue
    evidence_refs: list = field(default_factory=list)
    coverage_scope: str = "graph"
    extractor_version: str = EXTRACTOR_VERSION

    def validate(self) -> None:
        if self.value not in TRUTH_VALUES:
            raise ValueError(f"value must be one of {TRUTH_VALUES}, got {self.value!r}")

    def __post_init__(self) -> None:
        self.validate()


@dataclass(frozen=True)
class PredicateSnapshot:
    observations: dict = field(default_factory=dict)  # name -> PredicateObservation
    schema_version: str = PREDICATE_SCHEMA_VERSION
    extractor_version: str = EXTRACTOR_VERSION

    def __getitem__(self, name: str) -> PredicateObservation:
        return self.observations[name]

    def get(self, name: str) -> PredicateObservation | None:
        return self.observations.get(name)

    def value_of(self, name: str) -> TruthValue:
        obs = self.observations.get(name)
        return obs.value if obs else "UNKNOWN"

    def to_dict(self) -> dict:
        return {
            "observations": {
                name: {
                    "value": obs.value,
                    "evidence_refs": list(obs.evidence_refs),
                    "coverage_scope": obs.coverage_scope,
                    "extractor_version": obs.extractor_version,
                }
                for name, obs in self.observations.items()
            },
            "schema_version": self.schema_version,
            "extractor_version": self.extractor_version,
        }


def extract_predicates(graph: LocalDesignGraph,
                       schema: dict[str, dict] | None = None,
                       feature_map: dict[str, FeatureValue] | None = None,
                       ) -> PredicateSnapshot:
    """Evaluate the predicate schema against the graph's features.

    ``UNKNOWN`` is returned whenever the graph carries no evidence — never
    ``FALSE`` (design doc H3). Features are computed from the graph unless a
    caller supplies ``feature_map`` directly.
    """
    schema = schema or PREDICATE_SCHEMA
    features = feature_map if feature_map is not None else extract_features(graph)
    observations: dict[str, PredicateObservation] = {}

    for name, spec in schema.items():
        observations[name] = _evaluate(name, spec, features)

    return PredicateSnapshot(observations=observations)


def support(values: list[TruthValue]) -> float | None:
    """``support(p) = n_T / (n_T + n_F)``; None when no TRUE/FALSE observed."""
    n_t = sum(1 for v in values if v == "TRUE")
    n_f = sum(1 for v in values if v == "FALSE")
    if n_t + n_f == 0:
        return None
    return n_t / (n_t + n_f)


def coverage(values: list[TruthValue]) -> float:
    """``coverage(p) = (n_T + n_F) / (n_T + n_F + n_U)``."""
    if not values:
        return 0.0
    n_tf = sum(1 for v in values if v in ("TRUE", "FALSE"))
    return n_tf / len(values)


# -- internals ---------------------------------------------------------------

def _evaluate(name: str, spec: dict, features: dict) -> PredicateObservation:
    kind = spec.get("kind")
    refs: list[str] = []

    if kind == "any_check_failed":
        n_failed = features.get("meta.num_failed_checks", 0)
        n_checks = features.get("meta.num_checks", 0)
        if isinstance(n_checks, int) and n_checks > 0:
            refs = ["graph.checks"]
            value = "TRUE" if n_failed else "FALSE"
        else:
            value = "UNKNOWN"

    elif kind == "check_clean":
        check = spec["check"]
        status = features.get(f"check.{check}.status")
        if status is None:
            value, refs = "UNKNOWN", []
        else:
            refs = [f"graph.check.{check}"]
            value = "TRUE" if str(status) in _CLEAN else "FALSE"

    elif kind == "violation_class_prefix":
        prefix = spec["prefix"]
        classes = {
            key[len("violation."):].split(".")[0]
            for key in features if key.startswith("violation.")
        }
        matching = [c for c in classes if re.match(prefix, c, re.IGNORECASE)]
        any_violations = features.get("meta.num_violation_classes", 0)
        if matching:
            value, refs = "TRUE", [f"graph.violation.{matching[0]}"]
        elif isinstance(any_violations, int) and any_violations > 0:
            value, refs = "FALSE", ["graph.violations"]
        else:
            value = "UNKNOWN"

    elif kind == "unobserved":
        value = "UNKNOWN"

    else:
        value = "UNKNOWN"

    return PredicateObservation(
        value=value,
        evidence_refs=refs,
        coverage_scope="graph" if refs else "insufficient_observation",
    )
