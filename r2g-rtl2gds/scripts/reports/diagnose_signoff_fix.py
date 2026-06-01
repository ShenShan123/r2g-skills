#!/usr/bin/env python3
"""Diagnose DRC/LVS signoff violations and emit an ordered real-layout-fix plan.

Sibling of knowledge/analyze_execution.py (which proposes fixes for *backend*
stage failures); this module handles *signoff* (DRC/LVS) violations only.

Real-fixes-only policy: strategies apply genuine layout/config changes (antenna
diode insertion + repair iters, route effort, density/area relief) and NEVER
relax the DRC rule deck. See references/signoff-fixing.md.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

BLOCK_START = "# >>> r2g signoff-fix (auto) >>>"
BLOCK_END = "# <<< r2g signoff-fix (auto) <<<"
ANTENNA_DIODE_CELL = "ANTENNA_X1"  # nangate45 ships MACRO ANTENNA_X1
KLAYOUT_CPP_CRASH = re.compile(r"sort_circuit|gen_log_entry|segmentation|sigsegv", re.I)


def parse_config(text: str) -> dict:
    """Parse `export VAR = value` / `VAR := value` lines (last assignment wins)."""
    cfg = {}
    for line in text.splitlines():
        m = re.match(r"\s*(?:override\s+)?(?:export\s+)?([A-Z0-9_]+)\s*[:?]?=\s*(.*?)\s*$", line)
        if m:
            cfg[m.group(1)] = m.group(2).strip()
    return cfg


def _all_antenna(categories: dict) -> bool:
    keys = list(categories or {})
    return bool(keys) and all(k.upper().endswith("_ANTENNA") for k in keys)


def _applied(cfg: dict, edits: dict) -> bool:
    if not edits:
        return False
    return all(str(cfg.get(k)) == str(v) for k, v in edits.items())


def _antenna_catalog(cfg: dict) -> list:
    """Return the full antenna strategy catalog (all entries, regardless of applied state)."""
    try:
        cur_util = int(float(cfg.get("CORE_UTILIZATION", "")))
    except (TypeError, ValueError):
        cur_util = None
    new_util = max(5, cur_util - 5) if cur_util is not None else 20
    return [
        {"id": "antenna_diode_iters",
         "rationale": "Wire ANTENNA_X1 as the antenna diode and raise repair_antennas "
                      "iterations so OpenROAD inserts diodes / jumpers to break long metal.",
         "config_edits": {"CORE_ANTENNACELL": ANTENNA_DIODE_CELL,
                          "MAX_REPAIR_ANTENNAS_ITER_GRT": "10",
                          "MAX_REPAIR_ANTENNAS_ITER_DRT": "10"},
         "rerun_from": "route", "recheck": "drc", "auto_apply": True},
        {"id": "antenna_route_effort",
         "rationale": "Give the detailed router more end iterations to reroute long metal "
                      "onto additional layers.",
         "config_edits": {"DETAILED_ROUTE_ARGS": "-droute_end_iteration 10"},
         "rerun_from": "route", "recheck": "drc", "auto_apply": True},
        {"id": "antenna_density_relief",
         "rationale": "Lower placement utilization so the router has room to spread routes "
                      "across layers (reduces long single-layer runs). "
                      "PLACE_DENSITY_LB_ADDON is left untouched (hard rule: never < 0.10).",
         "config_edits": {"CORE_UTILIZATION": str(new_util)},
         "rerun_from": "floorplan", "recheck": "drc", "auto_apply": True},
    ]


def _antenna_strategies(cfg: dict) -> list:
    return [s for s in _antenna_catalog(cfg) if not _applied(cfg, s["config_edits"])]


def _drc_plan(drc: dict, cfg: dict, exclude: set) -> dict:
    status = drc.get("status", "unknown")
    cats = drc.get("categories") or {}
    dominant = max(cats, key=lambda k: cats[k].get("count") or 0) if cats else None
    plan = {"check": "drc", "status": status, "violation_count": drc.get("total_violations"),
            "dominant_category": dominant, "strategies": [], "residual_reason": None}
    if status in ("clean", "skipped"):
        return plan
    if status in ("stuck", "timeout"):
        plan["residual_reason"] = f"drc_{status}_tooling_out_of_v1_scope"
        return plan
    if status in ("fail", "failed"):
        if _all_antenna(cats):
            strategies = [s for s in _antenna_strategies(cfg) if s["id"] not in exclude]
            plan["strategies"] = strategies
            if not strategies:
                plan["status"] = "residual"
                plan["residual_reason"] = "antenna: all real-fix strategies exhausted"
        else:
            non_antenna = sorted(k for k in cats if not k.upper().endswith("_ANTENNA"))
            plan["residual_reason"] = "non-antenna DRC class not handled in v1: " + ", ".join(non_antenna)
        return plan
    plan["residual_reason"] = "drc status unknown — no report yet"
    return plan


def _lvs_plan(lvs: dict, cfg: dict, exclude: set) -> dict:
    status = lvs.get("status", "unknown")
    plan = {"check": "lvs", "status": status, "violation_count": lvs.get("mismatch_count"),
            "dominant_category": None, "strategies": [], "residual_reason": None}
    if status in ("clean", "skipped"):
        return plan
    if status == "unknown":
        s = {"id": "lvs_resolve_unknown",
             "rationale": "Re-extract / inspect the LVS log to resolve the ambiguous status "
                          "to clean or fail before attempting any fix.",
             "config_edits": {}, "rerun_from": None, "recheck": "lvs", "auto_apply": True}
        if s["id"] not in exclude:
            plan["strategies"].append(s)
        return plan
    if status in ("fail", "failed"):
        errors = " ".join((lvs.get("log_info") or {}).get("errors", []))
        if KLAYOUT_CPP_CRASH.search(errors):
            plan["residual_reason"] = "klayout_cpp_crash_needs_upgrade (>=0.30.10)"
            return plan
        blob = (cfg.get("VERILOG_FILES", "") + " " + cfg.get("CDL_FILE", "")
                + " " + cfg.get("ADDITIONAL_LEFS", "")).lower()
        if "fakeram" in blob:
            s = {"id": "lvs_macro_cdl",
                 "rationale": "Macro design: point CDL_FILE at a combined CDL (std cells + "
                              "fakeram stubs) via `override export` so KLayout sees macro subckts.",
                 "config_edits": {}, "rerun_from": None, "recheck": "lvs", "auto_apply": False,
                 "operator_note": "Generate combined.cdl and add `override export CDL_FILE = "
                                  "<combined.cdl>`; see failure-patterns.md 'LVS CDL_FILE Override'."}
            if s["id"] not in exclude:
                plan["strategies"].append(s)
        else:
            plan["residual_reason"] = ("lvs mismatch with no auto-fix in v1; likely rule-deck "
                                       "(.lylvs) issue — operator review required")
        return plan
    plan["residual_reason"] = f"lvs status '{status}' not actionable in v1"
    return plan


def build_plan(drc: dict, lvs: dict, cfg: dict, *, check: str = "drc", exclude=()) -> dict:
    """Pure: (drc.json, lvs.json, parsed config.mk) -> ordered fix plan dict."""
    excl = set(exclude or ())
    return _drc_plan(drc or {}, cfg, excl) if check == "drc" else _lvs_plan(lvs or {}, cfg, excl)


def apply_edits(config_text: str, edits: dict) -> str:
    """Replace the marked auto-block with `edits` (idempotent; re-apply replaces)."""
    out, skip = [], False
    for ln in config_text.splitlines():
        s = ln.strip()
        if s == BLOCK_START:
            skip = True
            continue
        if s == BLOCK_END:
            skip = False
            continue
        if not skip:
            out.append(ln)
    body = "\n".join(out).rstrip("\n")
    block = [BLOCK_START] + [f"export {k} = {v}" for k, v in edits.items()] + [BLOCK_END]
    prefix = (body + "\n\n") if body else ""
    return prefix + "\n".join(block) + "\n"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Diagnose DRC/LVS violations → real-fix plan.")
    ap.add_argument("project_dir")
    ap.add_argument("--check", choices=["drc", "lvs"], default="drc")
    ap.add_argument("--apply", metavar="STRATEGY_ID", help="write the strategy's edits into config.mk")
    ap.add_argument("--next", action="store_true", help="print one tab-separated action line for the driver")
    ap.add_argument("--exclude", default="", help="comma-separated strategy ids to skip")
    args = ap.parse_args(argv)

    proj = Path(args.project_dir)
    drc = _load(proj / "reports" / "drc.json")
    lvs = _load(proj / "reports" / "lvs.json")
    cfg_path = proj / "constraints" / "config.mk"
    cfg_text = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""
    cfg = parse_config(cfg_text)
    exclude = [x for x in args.exclude.split(",") if x]
    plan = build_plan(drc, lvs, cfg, check=args.check, exclude=exclude)

    if args.apply:
        strat = next((s for s in plan["strategies"] if s["id"] == args.apply), None)
        if strat is None:
            # Strategy may be filtered out because it was already applied (idempotent
            # re-apply). Search the full unfiltered catalog to distinguish "already
            # applied" from "unknown id".
            full_catalog = _antenna_catalog(cfg) if args.check == "drc" else []
            strat = next((s for s in full_catalog if s["id"] == args.apply), None)
            if strat is None:
                print(f"ERROR: strategy '{args.apply}' not in current plan", file=sys.stderr)
                return 2
        if not strat.get("auto_apply", False):
            print(f"ERROR: '{args.apply}' is operator-only: {strat.get('operator_note','')}", file=sys.stderr)
            return 3
        if strat["config_edits"]:
            cfg_path.write_text(apply_edits(cfg_text, strat["config_edits"]), encoding="utf-8")
        print(json.dumps({"applied": strat["id"], "config_edits": strat["config_edits"]}))
        return 0

    if args.next:
        auto = next((s for s in plan["strategies"] if s.get("auto_apply")), None)
        if auto is None:
            reason = plan.get("residual_reason") or "no_auto_strategy"
            print(f"STOP\t{plan['status']}\t{reason}")
        else:
            print(f"{auto['id']}\t{auto.get('rerun_from') or ''}\t{auto['recheck']}")
        return 0

    print(json.dumps(plan, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
