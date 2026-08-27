"""Feature extractor ``psi: G_D -> F`` (design doc 6.5).

Both the RoleProjector and the PredicateExtractor consume the same feature set
so role/predicate extraction cannot drift apart. All features are deterministic
and derived from the LocalDesignGraph only.
"""
from __future__ import annotations

from typing import Any

from tehm.graph.local_design_graph import LocalDesignGraph

FeatureValue = bool | int | float | str | None

_CLEAN = frozenset({"clean", "clean_beol", "complete", "skipped"})


def extract_features(graph: LocalDesignGraph) -> dict[str, FeatureValue]:
    """Flatten the graph into a typed feature map.

    Naming: ``check.<name>.status``, ``check.<name>.violations``,
    ``violation.<class>.count``, ``knob.<name>.value``, ``stage.<name>.present``,
    ``meta.*``.
    """
    features: dict[str, FeatureValue] = {}

    check_nodes: dict[str, dict] = {}
    violation_counts: dict[str, int] = {}
    knob_values: dict[str, Any] = {}
    stages: set[str] = set()

    for node in graph.nodes:
        kind = node.get("kind")
        label = node.get("label", "")
        attrs = node.get("attrs") or {}
        if kind == "DESIGN":
            features["meta.design"] = label
        elif kind == "PLATFORM":
            features["meta.platform"] = label
        elif kind == "CHECK":
            check_nodes[label] = dict(attrs)
        elif kind == "VIOLATION_CLASS":
            count = attrs.get("count")
            violation_counts[label] = int(count) if isinstance(count, int) else count
        elif kind == "CONFIG_KNOB":
            knob_values[label] = attrs.get("value")
        elif kind == "STAGE":
            stages.add(label)
        elif kind == "MODULE":
            features.setdefault("meta.modules", [])
            features["meta.modules"].append(label)  # type: ignore[union-attr]
        elif kind == "SIGNAL":
            features.setdefault("meta.signals", [])
            features["meta.signals"].append(label)  # type: ignore[union-attr]

    for check in sorted(check_nodes):
        attrs = check_nodes[check]
        features[f"check.{check}.status"] = str(attrs.get("status", "unknown"))
        vc = attrs.get("total_violations")
        features[f"check.{check}.violations"] = vc

    for vclass in sorted(violation_counts):
        features[f"violation.{vclass}.count"] = violation_counts[vclass]

    for knob in sorted(knob_values):
        features[f"knob.{knob}.value"] = knob_values[knob]

    for stage in sorted(stages):
        features[f"stage.{stage}.present"] = True

    # Graph-level aggregates.
    features["meta.num_nodes"] = graph.node_count()
    features["meta.num_edges"] = graph.edge_count()
    num_checks = len(check_nodes)
    num_failed_checks = sum(
        1 for attrs in check_nodes.values()
        if str(attrs.get("status", "")) not in _CLEAN
    )
    features["meta.num_checks"] = num_checks
    features["meta.num_failed_checks"] = num_failed_checks
    features["meta.num_violation_classes"] = len(violation_counts)
    features["meta.num_knobs"] = len(knob_values)
    return features


def check_status(graph: LocalDesignGraph, check: str) -> str | None:
    """Status of one CHECK node (by label), or None if absent."""
    for node in graph.nodes:
        if node.get("kind") == "CHECK" and node.get("label") == check:
            return str((node.get("attrs") or {}).get("status", "unknown"))
    return None
