"""LocalDesignGraph: the semantic design graph ``G_D`` (design doc 22.1).

v1 = RunContextGraph over the flow/signoff domain. Nodes and edges are the
typed vocabularies below; every graph is deterministic and content-digested so
equal designs map to equal ``context_graph_digest``.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from tehm.ids import stable_dumps

# Design doc 22.1 (flow/signoff v1 + RTL v2 node kinds).
NODE_KINDS = frozenset({
    "DESIGN", "PLATFORM", "RUN", "STAGE", "CHECK", "VIOLATION_CLASS",
    "CONFIG_KNOB", "TOOL", "ORACLE",
    # RTL v2 (Phase 10; vocabulary reserved now)
    "MODULE", "ALWAYS_BLOCK", "SIGNAL", "STATE_REG", "EXPRESSION",
    "FSM_TRANSITION", "CLOCK", "RESET", "ASSERTION", "TRACE_EVENT",
    "COMPATIBILITY_PROFILE",
})

# Design doc 22.1 edges + 4.4 global edge relations.
EDGE_KINDS = frozenset({
    "RAN_ON", "FAILED_AT", "HAS_VIOLATION", "MODIFIED", "RERUN_FROM",
    "RECHECKED_BY", "PRESERVED", "REGRESSED",
    "EXECUTED_FROM", "PRODUCED_STATE", "PART_OF_EPISODE", "DERIVED_FROM",
    "GENERALIZES", "SPECIALIZES", "COMPOSES_WITH", "REQUIRES",
    "CONFLICTS_WITH", "SHARES_RISK", "VERIFIED_BY", "ACTIVATED_ON",
    "CREATED_REGRESSION",
    # RTL v2 (Phase 10) edge kinds.
    "CONTROL_PATH", "DATA_PATH",
})

CLEAN_STATUSES = frozenset({"clean", "clean_beol", "complete", "skipped"})


@dataclass
class LocalDesignGraph:
    """A deterministic, content-addressed design-semantics graph."""

    nodes: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)

    def add_node(self, node_id: str, kind: str, label: str = "",
                 attrs: dict | None = None) -> None:
        if kind not in NODE_KINDS:
            raise ValueError(f"unknown node kind {kind!r}")
        if any(n["id"] == node_id for n in self.nodes):
            return
        self.nodes.append({
            "id": node_id, "kind": kind, "label": label,
            "attrs": dict(attrs or {}),
        })

    def add_edge(self, src: str, dst: str, kind: str,
                 attrs: dict | None = None) -> None:
        if kind not in EDGE_KINDS:
            raise ValueError(f"unknown edge kind {kind!r}")
        edge = {"src": src, "dst": dst, "kind": kind, "attrs": dict(attrs or {})}
        if edge not in self.edges:
            self.edges.append(edge)

    def to_dict(self) -> dict:
        nodes = sorted(self.nodes, key=lambda n: n["id"])
        edges = sorted(self.edges, key=lambda e: (e["src"], e["dst"], e["kind"]))
        return {"nodes": nodes, "edges": edges}

    def digest(self) -> str:
        payload = stable_dumps(self.to_dict())
        return f"ctx_{hashlib.sha1(payload.encode()).hexdigest()[:16]}"

    def to_artifact_bytes(self) -> bytes:
        return stable_dumps(self.to_dict()).encode()

    def node_count(self) -> int:
        return len(self.nodes)

    def edge_count(self) -> int:
        return len(self.edges)


def build_run_context_graph(reports: dict, cfg: dict | None = None, *,
                            design_id: str | None = None,
                            platform: str | None = None,
                            run_tag: str | None = None) -> LocalDesignGraph:
    """Build the flow/signoff v1 RunContextGraph from reports + config.

    ``reports``: dict keyed by report name (``drc``/``lvs``/``timing``/``route``/
    ``rcx``/``ppa``), each a dict with at least ``status``.
    ``cfg``: config.mk key/value map (strings).

    Deterministic: node/edge insertion order is driven by sorted report and
    config keys.
    """
    g = LocalDesignGraph()
    reports = dict(reports or {})
    cfg = dict(cfg or {})

    if design_id:
        g.add_node(design_id, "DESIGN", label=design_id)
    if platform:
        g.add_node(platform, "PLATFORM", label=platform)
    run_id = run_tag or f"run:{design_id or 'unknown'}"
    if design_id or run_tag:
        g.add_node(run_id, "RUN", label=run_id)
        if design_id:
            g.add_edge(run_id, design_id, "RAN_ON")
        if platform:
            g.add_edge(run_id, platform, "RAN_ON")

    for key in sorted(reports):
        report = reports[key] or {}
        status = str(report.get("status", "unknown"))
        check_id = f"{run_id}:check:{key}"
        g.add_node(check_id, "CHECK", label=key,
                   attrs={"kind": key, "status": status})
        g.add_edge(check_id, run_id, "RECHECKED_BY")

        # Stage that failed (from ppa.orfs_fail_stage or report log_info).
        failed_stage = report.get("orfs_fail_stage") or report.get("fail_stage")
        if not failed_stage and key == "ppa":
            failed_stage = report.get("orfs_fail_stage")
        if failed_stage:
            stage_id = f"{run_id}:stage:{failed_stage}"
            g.add_node(stage_id, "STAGE", label=failed_stage)
            g.add_edge(check_id, stage_id, "FAILED_AT")

        tool = report.get("tool") or report.get("checker") or \
            (report.get("log_info") or {}).get("tool")
        if tool:
            tool_id = f"{run_id}:tool:{tool}"
            g.add_node(tool_id, "TOOL", label=tool)
            g.add_edge(check_id, tool_id, "REQUIRES")

        # Violation classes -> HAS_VIOLATION edges.
        categories = report.get("categories") or {}
        if isinstance(categories, dict):
            for cls in sorted(categories):
                vc_id = f"{check_id}:vc:{cls}"
                g.add_node(vc_id, "VIOLATION_CLASS", label=cls,
                           attrs={"count": categories[cls].get("count")})
                g.add_edge(vc_id, check_id, "HAS_VIOLATION")

    for knob in sorted(cfg):
        g.add_node(f"{run_id}:knob:{knob}", "CONFIG_KNOB", label=knob,
                   attrs={"value": str(cfg[knob])})
        g.add_edge(run_id, f"{run_id}:knob:{knob}", "MODIFIED")

    return g


def load_graph_from_artifact(data: dict) -> LocalDesignGraph:
    """Reconstruct from the semantic view payload (to_dict format)."""
    g = LocalDesignGraph()
    for node in data.get("nodes", []):
        g.nodes.append(dict(node))
    for edge in data.get("edges", []):
        g.edges.append(dict(edge))
    return g
