"""RoleProjector: graph entity -> structural role (design doc 6.4).

Roles come from the unified LocalDesignGraph (never from a signal-name
classifier alone). First version supports the RTL role vocabulary plus
flow/signoff roles; uncertain entities project to ``UNKNOWN``.

``role_v1.yaml`` (tehm/schemas/) is the human-readable mirror of ``ROLE_SCHEMA``.
"""
from __future__ import annotations

import re

from tehm import ROLE_SCHEMA_VERSION
from tehm.graph.local_design_graph import LocalDesignGraph

# Design doc 6.4 first-version roles.
RTL_ROLES = (
    "STATE_REG", "NEXT_STATE", "TRANSITION_GUARD", "REQUEST", "ACK",
    "VALID", "READY", "RESET", "COUNTER", "COUNTER_BOUND",
    "DATA_PATH", "CONTROL_PATH",
)
# Flow/signoff roles for the v1 RunContextGraph domain.
FLOW_ROLES = ("TARGET_CHECK", "PRESERVE_CHECK", "RERUN_STAGE", "CONFIG_KNOB",
              "ORACLE", "RESOURCE", "FAILURE_SIGNATURE")
UNKNOWN_ROLE = "UNKNOWN"
ALL_ROLES = RTL_ROLES + FLOW_ROLES + (UNKNOWN_ROLE,)

# Schema: role -> {node_kinds: [...], name_patterns: [regex]}.
ROLE_SCHEMA: dict[str, dict] = {
    "STATE_REG": {"node_kinds": ["STATE_REG", "SIGNAL"],
                  "name_patterns": [r"(?i)state(_reg|reg|_cur|_q)?$",
                                    r"(?i)^(cur|curr)_state"]},
    "NEXT_STATE": {"node_kinds": ["SIGNAL", "EXPRESSION"],
                   "name_patterns": [r"(?i)next_state", r"(?i)^nxt", r"(?i)_ns$"]},
    "TRANSITION_GUARD": {"node_kinds": ["EXPRESSION", "TRANSITION_GUARD"],
                         "name_patterns": [r"(?i)guard", r"(?i)cond", r"(?i)en$"]},
    "REQUEST": {"node_kinds": ["SIGNAL"],
                "name_patterns": [r"(?i)(^|_)req(uest)?$"]},
    "ACK": {"node_kinds": ["SIGNAL"],
            "name_patterns": [r"(?i)(^|_)ack$", r"(?i)done$", r"(?i)valid_in$"]},
    "VALID": {"node_kinds": ["SIGNAL"],
              "name_patterns": [r"(?i)(^|_)valid$"]},
    "READY": {"node_kinds": ["SIGNAL"],
              "name_patterns": [r"(?i)(^|_)ready$"]},
    "RESET": {"node_kinds": ["SIGNAL", "RESET"],
              "name_patterns": [r"(?i)reset", r"(?i)^rst"]},
    "COUNTER": {"node_kinds": ["SIGNAL", "COUNTER"],
                "name_patterns": [r"(?i)count(_val)?$", r"(?i)^cnt"]},
    "COUNTER_BOUND": {"node_kinds": ["SIGNAL", "COUNTER_BOUND"],
                      "name_patterns": [r"(?i)(max|bound|limit|threshold)"]},
    "DATA_PATH": {"node_kinds": ["SIGNAL", "DATA_PATH"],
                  "name_patterns": [r"(?i)(data|din|dout|payload)"]},
    "CONTROL_PATH": {"node_kinds": ["SIGNAL", "CONTROL_PATH"],
                     "name_patterns": [r"(?i)(ctrl|control|fsm|state)"]},
    "TARGET_CHECK": {"node_kinds": ["CHECK"]},
    "PRESERVE_CHECK": {"node_kinds": ["CHECK"]},
    "RERUN_STAGE": {"node_kinds": ["STAGE"]},
    "CONFIG_KNOB": {"node_kinds": ["CONFIG_KNOB"]},
    "ORACLE": {"node_kinds": ["ORACLE", "TOOL"]},
    "RESOURCE": {"node_kinds": []},
    "FAILURE_SIGNATURE": {"node_kinds": ["VIOLATION_CLASS"]},
}

RoleMap = dict[str, str]  # entity_id -> role


class RoleProjector:
    """Project every graph entity to a structural role.

    Deterministic: kinds take priority, then name patterns, then UNKNOWN.
    """

    def __init__(self, schema: dict[str, dict] | None = None,
                 schema_version: str = ROLE_SCHEMA_VERSION):
        self.schema = schema or ROLE_SCHEMA
        self.schema_version = schema_version
        self._patterns = {
            role: [re.compile(p) for p in spec.get("name_patterns", [])]
            for role, spec in self.schema.items()
        }

    def project(self, graph: LocalDesignGraph, entity_id: str | None = None) -> str:
        """Role of one entity (all entities if ``entity_id`` is None)."""
        if entity_id is not None:
            return self._role_of(self._find_node(graph, entity_id))
        return UNKNOWN_ROLE

    def project_all(self, graph: LocalDesignGraph) -> RoleMap:
        return {node["id"]: self._role_of(node) for node in graph.nodes}

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _find_node(graph: LocalDesignGraph, entity_id: str) -> dict:
        for node in graph.nodes:
            if node.get("id") == entity_id:
                return node
        return {"id": entity_id, "kind": "", "label": "", "attrs": {}}

    def _role_of(self, node: dict) -> str:
        kind = node.get("kind", "")
        label = node.get("label", "") or node.get("id", "")
        if kind == "CHECK":
            status = str((node.get("attrs") or {}).get("status", ""))
            return "TARGET_CHECK" if status and status not in _CLEAN else "PRESERVE_CHECK"
        if kind == "VIOLATION_CLASS":
            return "FAILURE_SIGNATURE"
        if kind == "CONFIG_KNOB":
            return "CONFIG_KNOB"
        if kind == "STAGE":
            return "RERUN_STAGE"
        if kind in ("ORACLE", "TOOL"):
            return "ORACLE"
        if kind in ("STATE_REG", "RESET", "COUNTER", "COUNTER_BOUND",
                    "DATA_PATH", "CONTROL_PATH", "EXPRESSION",
                    "TRANSITION_GUARD", "FSM_TRANSITION"):
            # Kind already encodes the role for these reserved kinds.
            return kind if kind in ALL_ROLES else UNKNOWN_ROLE
        # Name-pattern fallback for SIGNAL and anything else.
        for role, patterns in self._patterns.items():
            if role in ("TARGET_CHECK", "PRESERVE_CHECK", "RERUN_STAGE",
                        "CONFIG_KNOB", "ORACLE", "RESOURCE", "FAILURE_SIGNATURE"):
                continue  # kind-driven roles only
            if any(p.search(label) for p in patterns):
                return role
        return UNKNOWN_ROLE


_CLEAN = frozenset({"clean", "clean_beol", "complete", "skipped"})
