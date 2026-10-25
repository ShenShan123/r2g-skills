"""Step 3: structural binding (design doc 10, 7.2 / 23.3 V1).

``theta_L : Theta -> Entities(G_q)`` resolves the rule's holes (``$H``) against
the target state's entities. Concrete match slots must already be satisfied by
the context (enforced by Step 2); hole slots are bound from the caller-provided
structural knowledge (``provided_binding``) — a hole that cannot be bound is
honestly UNRESOLVED, never silently defaulted (design doc 9.4 / 7.2).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib

from contracts import RepairContext
from tehm.ids import is_hole, stable_dumps

BINDING_VERSION = "binding-v0.1"
BINDING_STATUSES = ("BOUND", "PARTIAL", "UNRESOLVED")


@dataclass
class Binding:
    status: str = "UNRESOLVED"
    substitutions: dict = field(default_factory=dict)      # hole -> concrete
    unresolved_holes: list = field(default_factory=list)
    bound_entities: dict = field(default_factory=dict)     # slot path -> entity
    proof: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "substitutions": self.substitutions,
            "unresolved_holes": self.unresolved_holes,
            "bound_entities": self.bound_entities,
            "proof": self.proof,
        }


def bind_rule(rule: dict, context: RepairContext, *,
              provided_binding: dict | None = None) -> Binding:
    """Resolve every hole in the rule's before/after patterns against the target.

    ``provided_binding`` maps hole -> concrete value (the caller's structural
    knowledge of the target design). A concrete match slot (e.g. target_check)
    is recorded as a bound entity; a hole with no provided value stays
    UNRESOLVED.
    """
    provided_binding = provided_binding or {}
    holes = _collect_holes(rule)
    substitutions: dict = {}
    unresolved: list = []
    proof_candidates: dict = {}
    constraints = (rule.get("provenance") or {}).get("hole_constraints") or {}
    constraints = {**_pattern_constraints(rule), **constraints}
    for hole in sorted(holes):
        if hole in provided_binding:
            substitutions[hole] = provided_binding[hole]
            proof_candidates[hole] = {
                "value": provided_binding[hole], "source": "provided",
                "constraint": constraints.get(hole, {})}
        else:
            candidates = _graph_candidates(hole, constraints.get(hole, {}), context)
            if len(candidates) == 1:
                substitutions[hole] = candidates[0]
                proof_candidates[hole] = {
                    "value": candidates[0], "source": "structural_graph",
                    "constraint": constraints.get(hole, {})}
            else:
                unresolved.append(hole)
                proof_candidates[hole] = {
                    "source": "structural_graph",
                    "candidate_count": len(candidates),
                    "candidates": candidates[:20],
                    "constraint": constraints.get(hole, {}),
                }

    bound_entities: dict = {}
    before = rule.get("before_pattern") or {}
    for key, value in before.items():
        if key in ("type", "domain", "action_domain"):
            continue
        if isinstance(value, str) and is_hole(value):
            if value in substitutions:
                bound_entities[f"match.{key}"] = substitutions[value]
        elif value is not None:
            bound_entities[f"match.{key}"] = value

    status = "BOUND" if not unresolved else (
        "PARTIAL" if substitutions else "UNRESOLVED")
    target_digest = hashlib.sha256(
        stable_dumps(context.to_dict()).encode()).hexdigest()
    proof = {
        "version": BINDING_VERSION,
        "target_context_digest": target_digest,
        "resolution": proof_candidates,
        "unresolved_holes": list(unresolved),
        "provided_keys": sorted(provided_binding),
    }
    return Binding(status=status, substitutions=substitutions,
                   unresolved_holes=unresolved, bound_entities=bound_entities,
                   proof=proof)


def _collect_holes(rule: dict) -> set:
    holes: set = set()
    for pattern in (rule.get("before_pattern") or {}, rule.get("after_pattern") or {}):
        for value in pattern.values():
            if isinstance(value, str) and is_hole(value):
                holes.add(value)
    return holes


def _pattern_constraints(rule: dict) -> dict:
    """Recover slot paths when a persisted retrieval row omits provenance."""
    result = {}
    for key, value in (rule.get("before_pattern") or {}).items():
        if isinstance(value, str) and is_hole(value):
            result.setdefault(value, {"path": f"match.{key}"})
    for key, value in (rule.get("after_pattern") or {}).items():
        if isinstance(value, str) and is_hole(value):
            result.setdefault(value, {"path": key})
    return result


def _graph_candidates(hole: str, constraint: dict, context: RepairContext) -> list:
    """Return deterministic, typed candidates from the target context.

    v1 deliberately supports only unambiguous structural facts.  It never
    selects the first arbitrary config key or graph node; ambiguity remains
    unresolved and is visible in the binding proof.
    """
    path = str(constraint.get("path") or "")
    if path == "match.knob":
        cfg = context.cfg or {}
        reserved = {"PLATFORM", "DESIGN_NAME", "RUN_TAG"}
        return sorted(str(key) for key in cfg if str(key) not in reserved)

    graph = context.structural_graph or {}
    entities = graph.get("entities") or graph.get("nodes") or []
    if not isinstance(entities, list):
        return []
    suffix = path.split(".")[-1] if path else ""
    candidates = []
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        value = entity.get(suffix)
        if value is None and suffix == "module" and entity.get("kind") == "MODULE":
            value = entity.get("label")
        if value is None:
            value = entity.get("value") if suffix in {"target", "source"} else None
        if value is not None:
            candidates.append(value)
    return sorted({stable_dumps(value): value for value in candidates}.values(),
                  key=stable_dumps)
