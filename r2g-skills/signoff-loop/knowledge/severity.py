#!/usr/bin/env python3
"""Graded failure severity (R2G memory Phase G0): how far a failing situation is from clear.

Binary closure cannot rank repair components: a knob that moves a cell-overflow design
from required density 1.08 to 1.02 fails alone yet is half of the fix that works
(memory/evaluation/r2g_memory_phaseG_contract_20261002.md). Severity is lower-is-better;
<= 0 means the situation's own criterion is met (a negative value is margin).

  FLW-0024 (cell overflow)  required placement density - 1.0. The placer's own value
                            ("Placement density is X") when printed; else the estimate
                            1.14 * effective utilisation (floorplan IFP-0104) + PLACE_DENSITY_LB_ADDON.
                            The ratio 1.14 is the median of 322 (lower bound, effective
                            utilisation) pairs in the evaluation runs (range 1.00-1.19).
  GRT-0116 (route congestion) total congestion of the global router's final report (GRT-0096).
  DRC                       violation count (reports/drc.json).
  PPL-0024 (pin overflow)   required / available die perimeter - 1.

Reads only the NEWEST backend run (same selection as situation.py) and never infers a value
it cannot read: None means unknown.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

LB_PER_EFFECTIVE_UTIL = 1.14
_EFF_UTIL = re.compile(r"IFP-0104\]\s+Effective utilization:\s+([\d.]+)")
_PLACER_DENSITY = re.compile(r"Placement density is ([\d.]+), computed from PLACE_DENSITY_LB_ADDON")
_GRT_TOTAL = re.compile(r"^Total\s+\d+\s+\d+\s+[\d.]+%\s+\d+\s*/\s*\d+\s*/\s*(\d+)\s*$")
_PERIMETER = re.compile(r"die perimeter from ([\d.]+)um to ([\d.]+)um")
_LB_ADDON = re.compile(r"^\s*export\s+PLACE_DENSITY_LB_ADDON\s*[?:]?=\s*([\d.]+)")


def _newest_run(project: Path) -> Path | None:
    runs = [d for d in project.glob("backend/RUN_*") if d.is_dir()]
    return max(runs, key=lambda d: d.stat().st_mtime) if runs else None


def _lb_addon(project: Path, default: float = 0.2) -> float:
    """Last PLACE_DENSITY_LB_ADDON assignment in config.mk (the fix block wins); ORFS
    sky130 platform default otherwise."""
    value = default
    try:
        for ln in (project / "constraints" / "config.mk").read_text(errors="ignore").splitlines():
            m = _LB_ADDON.match(ln)
            if m:
                value = float(m.group(1))
    except OSError:
        pass
    return value


def placement_density_excess(log_text: str, lb_addon: float) -> float | None:
    """Required placement density - 1.0 (see module doc)."""
    printed = _PLACER_DENSITY.findall(log_text)
    if printed:
        return round(float(printed[-1]) - 1.0, 4)
    eff = _EFF_UTIL.findall(log_text)
    if not eff:
        return None
    return round(LB_PER_EFFECTIVE_UTIL * float(eff[-1]) + lb_addon - 1.0, 4)


def grt_total_congestion(log_text: str) -> float | None:
    totals = [m.group(1) for ln in log_text.splitlines() for m in [_GRT_TOTAL.match(ln.strip())] if m]
    return float(totals[-1]) if totals else None


def perimeter_excess(log_text: str) -> float | None:
    m = _PERIMETER.findall(log_text)
    if not m:
        return None
    have, need = (float(x) for x in m[-1])
    return round(need / have - 1.0, 4) if have > 0 else None


def from_project(project: Path | str, check: str, violation_class: str | None = None,
                 error_code: str | None = None) -> float | None:
    """Severity of ``project``'s current failure, or None when it cannot be read."""
    project = Path(project)
    if check == "drc":
        try:
            n = json.loads((project / "reports" / "drc.json").read_text()).get("total_violations")
            return float(n) if n is not None else None
        except (OSError, ValueError):
            return None
    run = _newest_run(project)
    if run is None:
        return None
    try:
        text = (run / "flow.log").read_text(errors="ignore")
    except OSError:
        return None
    stage = violation_class or ("route" if check == "route" else None)
    if error_code == "PPL-0024" or "PPL-0024" in text and stage == "place":
        return perimeter_excess(text)
    if stage == "place":
        return placement_density_excess(text, _lb_addon(project))
    if stage == "route":
        return grt_total_congestion(text)
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot", help="print the project's current severity (empty if unknown)")
    s.add_argument("project")
    s.add_argument("--check", required=True)
    s.add_argument("--vclass", default="")
    a = ap.parse_args(argv)
    try:
        v = from_project(a.project, a.check, a.vclass or None)
    except Exception:              # best-effort: never breaks a fix loop
        v = None
    print("" if v is None else v)
    return 0


if __name__ == "__main__":
    sys.exit(main())
