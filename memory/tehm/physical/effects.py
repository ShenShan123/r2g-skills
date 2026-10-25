"""Physical effect extraction (design doc 26 Phase 11).

``extract_deltas(before_ppa, after_ppa)`` computes the observed PHYSICAL effect
of one action from the before/after PPA snapshots:

    ΔWNS, ΔTNS, ΔArea, ΔPower, ΔCongestion, ΔDRC

A metric missing from either snapshot yields ``None`` — never a fabricated
delta (honesty H3). Congestion is optional in v1 (the flow ppa does not always
carry it; def-graph labels provide it in Phase 11 full).
"""
from __future__ import annotations

from dataclasses import dataclass, field

PHYSICAL_METRICS = ("wns_ns", "tns_ns", "area_um2", "power_w",
                    "congestion", "drc_violations")


@dataclass
class PhysicalEffect:
    transition_id: str
    action_domain: str
    transformation_family: str
    effect_key: str = ""
    domain: str = "flow.signoff"
    deltas: dict = field(default_factory=dict)
    before_ppa: dict = field(default_factory=dict)
    after_ppa: dict = field(default_factory=dict)
    evidence_refs: list = field(default_factory=list)
    graph_context: dict = field(default_factory=dict)
    graph_context_digest: str = ""
    graph_extractor_version: str = ""

    def to_row(self) -> dict:
        from tehm.ids import stable_dumps
        return {
            "transition_id": self.transition_id,
            "action_domain": self.action_domain,
            "transformation_family": self.transformation_family,
            "effect_key": self.effect_key,
            "domain": self.domain,
            "before_ppa_json": stable_dumps(self.before_ppa),
            "after_ppa_json": stable_dumps(self.after_ppa),
            "deltas_json": stable_dumps(self.deltas),
            "evidence_refs_json": stable_dumps(self.evidence_refs),
            "graph_context_json": stable_dumps(self.graph_context),
            "graph_context_digest": self.graph_context_digest,
            "graph_extractor_version": self.graph_extractor_version,
        }


def extract_deltas(before_ppa: dict, after_ppa: dict) -> dict:
    """Physical deltas (after - before) across the six metrics."""
    deltas: dict = {}
    for metric in PHYSICAL_METRICS:
        b = _metric(before_ppa, metric)
        a = _metric(after_ppa, metric)
        if b is None or a is None:
            deltas[metric] = None
        else:
            deltas[metric] = round(a - b, 6)
    return deltas


def _metric(ppa: dict, metric: str):
    summary = ppa.get("summary") or {}
    timing = summary.get("timing") or {}
    area = summary.get("area") or {}
    power = summary.get("power") or {}
    drc = summary.get("drc") or {}
    geometry = ppa.get("geometry") or {}
    if metric == "wns_ns":
        return _num(timing.get("setup_wns"))
    if metric == "tns_ns":
        return _num(timing.get("setup_tns"))
    if metric == "area_um2":
        return _num(area.get("design_area_um2") or geometry.get("die_area_um2"))
    if metric == "power_w":
        return _num(power.get("total_power_w"))
    if metric == "congestion":
        return _num(geometry.get("congestion"))
    if metric == "drc_violations":
        return _num(drc.get("drc_violations"))
    return None


def _num(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
