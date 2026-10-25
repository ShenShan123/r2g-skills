#!/usr/bin/env python3
"""Hard safety policy for config.mk edits proposed by MEMORY or an LLM (not the catalog).

One whitelist with ranges (R2G memory redesign B4, 2026-10-01). An edit outside it is
dropped and the reason logged — never clamped into range, because a clamped value is
an action no evidence ever verified. The catalogue strategies in
diagnose_signoff_fix.py carry their own hand-reviewed edits and do not pass through
here; `scripts/project/validate_config.py` PARAM_RANGES stays the ADVISORY
(warning-level) range check for a whole config.

Rules: PLACE_DENSITY_LB_ADDON >= 0.10 (placer divergence is irrecoverable below it —
CLAUDE.md hard rule); the clock period / SDC is never a memory edit (that is timing
relaxation, not repair); CORE_UTILIZATION 5..90.
"""
from __future__ import annotations

import re

# knob -> (lo, hi) numeric bounds, or None for a structured value checked by shape.
KNOB_BOUNDS: dict[str, tuple[float, float] | None] = {
    "CORE_UTILIZATION": (5, 90),
    "PLACE_DENSITY": (0.2, 0.99),
    "PLACE_DENSITY_LB_ADDON": (0.10, 0.6),
    "DIE_AREA": None,
    "CORE_AREA": None,
    "CORE_ASPECT_RATIO": (0.3, 3.0),
    "CORE_MARGIN": (1, 50),
    "ROUTING_LAYER_ADJUSTMENT": (0.0, 0.7),
    "CELL_PAD_IN_SITES_GLOBAL_PLACEMENT": (0, 8),
    "CELL_PAD_IN_SITES_DETAIL_PLACEMENT": (0, 8),
    "GPL_TIMING_DRIVEN": (0, 1),
    "GPL_ROUTABILITY_DRIVEN": (0, 1),
    "TNS_END_PERCENT": (0, 100),
    "MIN_ROUTING_LAYER": None,
    "MAX_ROUTING_LAYER": None,
}
# Enum knobs of the common catalogue (t2a task contracts publish them; failure-patterns
# "T2a contract/executor knob mismatch", 2026-10-16). One table: memory and LLM edits
# share these values, and memory/scripts/t2a_task_manifests.allowlist publishes them.
KNOB_ENUMS: dict[str, tuple[str, ...]] = {
    "ABC_AREA": ("0", "1"),
    "SYNTH_HIERARCHICAL": ("0", "1"),
    "ENABLE_PLACE_REPAIR_TIMING": ("0", "1"),
    "SETUP_SLACK_MARGIN": ("0", "0.2"),
    "ABC_CLOCK_PERIOD_IN_PS": ("8000",),
    "SKIP_ANTENNA_REPAIR": ("0", "1"),
    "MAX_REPAIR_ANTENNAS_ITER_GRT": ("10",),
    "MAX_REPAIR_ANTENNAS_ITER_DRT": ("10",),
    "R2G_ROUTE_SLEW_MARGIN": ("10", "20", "40"),
    "R2G_ROUTE_CAP_MARGIN": ("10", "20", "40"),
}
# A knob a fix REMOVES (drop DIE_AREA so ORFS auto-sizes the die). Same literal as
# memory/contracts.CONFIG_UNSET (tests/test_memory_gates.py pins the equality).
UNSET = "<unset>"
_AREA_RE = re.compile(r"^\s*\d+(\.\d+)?(\s+\d+(\.\d+)?){3}\s*$")
_LAYER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def edit_violation(knob: str, value) -> str | None:
    """Why ``knob = value`` is not an allowed memory/LLM edit, or None if it is."""
    if knob not in KNOB_BOUNDS and knob not in KNOB_ENUMS:
        return f"{knob} is not a whitelisted knob"
    if value is None or str(value).strip() == "":
        return f"{knob} has an empty value"
    if str(value) == UNSET:
        return None                       # removing a whitelisted knob is allowed
    text = str(value).strip()
    if knob in KNOB_ENUMS:
        return None if text in KNOB_ENUMS[knob] else f"{knob}={text!r} is not one of {list(KNOB_ENUMS[knob])}"
    bounds = KNOB_BOUNDS[knob]
    if knob in ("DIE_AREA", "CORE_AREA"):
        return None if _AREA_RE.match(text) else f"{knob}={text!r} is not 'x0 y0 x1 y1'"
    if bounds is None:
        return None if _LAYER_RE.match(text) else f"{knob}={text!r} is not a layer name"
    try:
        v = float(text)
    except ValueError:
        return f"{knob}={text!r} is not numeric"
    lo, hi = bounds
    if not lo <= v <= hi:
        return f"{knob}={text} outside [{lo:g}, {hi:g}]"
    return None


def edits_violations(edits: dict) -> list[str]:
    """Every violation in an edit map (empty list = the whole map is allowed)."""
    if not isinstance(edits, dict) or not edits:
        return ["empty edit map"]
    return [r for k, v in sorted(edits.items()) if (r := edit_violation(k, v))]
