"""RTL semantic graph (design doc 22.1 RTL v2).

Builds a LocalDesignGraph from parsed Verilog using the RTL node kinds:
MODULE / ALWAYS_BLOCK / SIGNAL / STATE_REG / EXPRESSION / FSM_TRANSITION /
CLOCK / RESET / ASSERTION / TRACE_EVENT. The digest is content-addressed so a
design's semantic graph is a stable context fingerprint.
"""
from __future__ import annotations

import hashlib

from tehm.graph.local_design_graph import LocalDesignGraph
from tehm.ids import stable_dumps
from tehm.rtl.compatibility import annotate_graph, profile_for_action

RTL_GRAPH_VERSION = "rtl-graph-v0.1"


def build_rtl_graph(module: object, *, design_id: str | None = None,
                    compatibility_profile: str | None = None) -> LocalDesignGraph:
    """Project one parsed RTL module onto the RTL semantic graph."""
    g = LocalDesignGraph()
    module_id = f"mod:{module.name}"
    g.add_node(module_id, "MODULE", label=module.name)
    if compatibility_profile:
        annotate_graph(g, compatibility_profile)

    for name, signal in module.signals.items():
        kind = "STATE_REG" if _is_state_like(name, signal) else "SIGNAL"
        g.add_node(f"{module_id}:sig:{name}", kind, label=name,
                   attrs={"kind": signal.kind, "width": signal.width})

    for block in module.always_blocks:
        block_id = f"{module_id}:always:{block.line}"
        g.add_node(block_id, "ALWAYS_BLOCK", label=f"always@{block.line}",
                   attrs={"sequential": block.is_sequential})
        g.add_edge(block_id, module_id, "PART_OF_EPISODE")
        sens = block.sensitivity
        if "posedge" in sens or "negedge" in sens:
            # the clock signal: token after posedge/negedge
            for i, token in enumerate(sens):
                if token in ("posedge", "negedge") and i + 1 < len(sens):
                    g.add_node(f"{module_id}:clock:{sens[i + 1]}", "CLOCK",
                               label=sens[i + 1], attrs={"edge": token})
                    g.add_edge(block_id, f"{module_id}:clock:{sens[i + 1]}",
                               "REQUIRES")
            if "negedge" in sens and "rst_n" in " ".join(sens):
                g.add_node(f"{module_id}:reset:rst_n", "RESET", label="rst_n",
                           attrs={"style": "async"})
                g.add_edge(block_id, f"{module_id}:reset:rst_n", "REQUIRES")
        for fsm in block.fsms:
            state_reg = f"{module_id}:sig:{fsm.reg_name}"
            g.add_node(state_reg, "STATE_REG", label=fsm.reg_name,
                       attrs={"case_expr": fsm.case_expr})
            for item in fsm.items:
                trans_id = (f"{module_id}:fsm:{fsm.reg_name}:"
                            f"{item.label}:{item.target.split('=')[-1].strip()}")
                g.add_node(trans_id, "FSM_TRANSITION",
                           label=f"{item.label}->{item.target.split('=')[-1].strip()}",
                           attrs={"guard": item.condition})
                g.add_edge(state_reg, trans_id, "CONTROL_PATH")
    return g


def design_graph_digest(graph: LocalDesignGraph) -> str:
    payload = stable_dumps(graph.to_dict())
    return f"rtl_{hashlib.sha1(payload.encode()).hexdigest()[:16]}"


def _is_state_like(name: str, signal) -> bool:
    if signal.kind == "reg" and re_search(r"(?i)(state|reg|cur|next)", name):
        return True
    return False


def re_search(pattern: str, text: str) -> bool:
    import re
    return bool(re.search(pattern, text))
