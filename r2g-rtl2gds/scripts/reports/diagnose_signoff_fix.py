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

try:
    import fix_model
except ImportError:                       # script run outside the test sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import fix_model

BLOCK_START = "# >>> r2g signoff-fix (auto) >>>"
BLOCK_END = "# <<< r2g signoff-fix (auto) <<<"
KLAYOUT_CPP_CRASH = re.compile(r"sort_circuit|gen_log_entry|segmentation|sigsegv", re.I)

# Platforms that need the installed antenna model (tools/install_nangate45_antenna.sh)
# plus a *diode-forced* repair config to clear antennas.  On nangate45 the stock tech LEF
# ships no antenna ratios and the SC LEF has gate areas stripped, so OpenROAD sees nothing
# to repair; once the model is installed, OpenROAD's default repair fixes antennas with
# JUMPERS, which the FreePDK45 KLayout signoff deck does NOT credit (it credits diodes).
# So the strategy disables jumper repair (SKIP_ANTENNA_REPAIR) and forces diode insertion
# (MAX_REPAIR_ANTENNAS_ITER_DRT).  Validated 2026-06-02 on stream_register (489:1 → clean
# with 1 diode).  See references/signoff-fixing.md "nangate45 antenna repair".  Density
# relief stays OFF here (empirically counterproductive: fifo_basic 14→16 at util 10→5).
DIODE_FORCED_REPAIR_PLATFORMS = {"nangate45"}


def _explicit_family(name: str, families: dict) -> str | None:
    """Family from an EXPLICIT families.json mapping/pattern, or None if only the
    generic split-on-underscore fallback would apply.  Mirrors ingest_run._explicit_family
    so the recipe READER keys families the same way the WRITER does."""
    if not name:
        return None
    if name in families.get("mappings", {}):
        return families["mappings"][name]
    for entry in families.get("patterns", []):
        if re.search(entry["regex"], name, re.IGNORECASE):
            return entry["family"]
    return None


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
    """Full antenna strategy catalog (real layout fixes; regardless of applied state).

    For platforms in DIODE_FORCED_REPAIR_PLATFORMS (nangate45): a single
    `antenna_diode_repair` strategy that forces physical antenna-diode insertion (the
    only repair the FreePDK45 signoff deck credits).  Requires the antenna model to be
    installed once via tools/install_nangate45_antenna.sh.  Density relief is NOT offered
    here (counterproductive — enlarging the die lengthens nets and adds antennas).

    All other platforms (sky130, gf180, ihp — which ship a real antenna model): the
    classic [antenna_diode_iters, antenna_density_relief] pair.
    """
    platform = cfg.get("PLATFORM")
    if platform in DIODE_FORCED_REPAIR_PLATFORMS:
        return [
            {"id": "antenna_diode_repair",
             "rationale": "Force antenna-diode insertion: the FreePDK45 KLayout deck credits "
                          "diodes, NOT jumpers, so SKIP_ANTENNA_REPAIR=1 disables OpenROAD's "
                          "global-route jumper repair (which satisfies its own PAR model but "
                          "leaves the signoff deck flagging) and MAX_REPAIR_ANTENNAS_ITER_DRT "
                          "drives physical ANTENNA_X1 diode insertion during detailed routing. "
                          "Requires the antenna model: tools/install_nangate45_antenna.sh (one-time).",
             "config_edits": {"SKIP_ANTENNA_REPAIR": "1", "MAX_REPAIR_ANTENNAS_ITER_DRT": "10"},
             "rerun_from": "route", "recheck": "drc", "auto_apply": True},
        ]

    try:
        cur_util = int(float(cfg.get("CORE_UTILIZATION", "")))
    except (TypeError, ValueError):
        cur_util = None
    new_util = max(5, cur_util - 5) if cur_util is not None else 20

    return [
        {"id": "antenna_diode_iters",
         "rationale": "Raise repair_antennas iterations (GRT+DRT, default 5) so OpenROAD "
                      "inserts more antenna diodes (auto-discovered ANTENNA_X1, which the "
                      "nangate45 LEF declares CLASS CORE ANTENNACELL) and jumpers to break "
                      "long metal.",
         "config_edits": {"MAX_REPAIR_ANTENNAS_ITER_GRT": "10",
                          "MAX_REPAIR_ANTENNAS_ITER_DRT": "10"},
         "rerun_from": "route", "recheck": "drc", "auto_apply": True},
        {"id": "antenna_density_relief",
         "rationale": "Lower placement utilization so the router has room to place diodes and "
                      "spread routes across layers (breaks long single-layer runs). "
                      "PLACE_DENSITY_LB_ADDON is never touched (hard rule: never < 0.10).",
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
    # 'clean_beol' = BEOL-only run with 0 violations (FEOL+ANTENNA skipped); no
    # routing-DRC fix is available or warranted — treat as clean for fix purposes.
    if status in ("clean", "clean_beol", "skipped"):
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
                platform = cfg.get("PLATFORM")
                if platform in DIODE_FORCED_REPAIR_PLATFORMS:
                    plan["residual_reason"] = (
                        "nangate45 antenna: diode-forced repair (antenna_diode_repair) applied; "
                        "the bulk cleared but a few nets remain. Two causes: (1) the antenna model "
                        "is not installed (run tools/install_nangate45_antenna.sh) — check_antennas "
                        "would find 0; or (2) an irreducible modeling gap — OpenROAD's per-net PAR "
                        "sums gate areas over fanout, so a high-fanout net driving one tiny gate "
                        "reads << KLayout's per-gate ratio and OpenROAD won't repair it. A tighter "
                        "install (--ratio 200) clears single-gate borderline nets but not the "
                        "multi-gate ones; those are an honest residual. Deck never relaxed."
                    )
                else:
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
    if status == "crash":
        plan["status"] = "residual"
        plan["residual_reason"] = "klayout_cpp_crash_needs_upgrade (>=0.30.10)"
        return plan
    if status == "incomplete":
        plan["status"] = "residual"
        plan["residual_reason"] = (
            "lvs incomplete: extracted but no verdict/lvsdb — likely crash/kill mid-run; "
            "not auto-retried (would re-crash)"
        )
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
            # Use extract_lvs.py's lvsdb mismatch classification for an honest,
            # specific residual instead of a generic "operator review".  See
            # references/failure-patterns.md "LVS symmetric-matcher residual".
            mismatch_class = lvs.get("mismatch_class")
            if mismatch_class == "symmetric_matcher":
                plan["status"] = "residual"
                plan["residual_reason"] = (
                    "lvs_symmetric_matcher_residual: KLayout-0.30.7 mis-pairs interchangeable "
                    "instances in symmetric logic (0 net deltas, only same-cell swaps in "
                    "ambiguous groups). Layout is correct; not flow-fixable (raising "
                    "max_depth/max_branch_complexity does NOT help). Needs newer KLayout."
                )
            elif mismatch_class == "real_connectivity":
                plan["residual_reason"] = (
                    "lvs_real_connectivity_mismatch: a layout net genuinely does not match the "
                    "schematic (\"not matching any net\"). Real layout defect — inspect the "
                    "GDS/DEF at the named net; not auto-fixable."
                )
            else:
                plan["residual_reason"] = ("lvs mismatch with no auto-fix in v1; likely rule-deck "
                                           "(.lylvs) issue — operator review required")
        return plan
    plan["residual_reason"] = f"lvs status '{status}' not actionable in v1"
    return plan


def _rank_plan_strategies(plan: dict, recipes: dict | None) -> dict:
    """Reorder plan['strategies'] by fix_model and attach the full ranking."""
    if not plan.get("strategies"):
        return plan
    static_order = [s["id"] for s in plan["strategies"]]
    ranking = fix_model.rank_strategies(recipes, static_order)
    by_id = {s["id"]: s for s in plan["strategies"]}
    plan["strategies"] = [by_id[r["strategy"]] for r in ranking if r["strategy"] in by_id]
    plan["ranking"] = ranking
    return plan


def build_plan(drc: dict, lvs: dict, cfg: dict, *, check: str = "drc",
               exclude=(), recipes: dict | None = None) -> dict:
    """Pure: (drc.json, lvs.json, parsed config.mk) -> ordered fix plan dict.
    When `recipes` (a Tier-3 fix_recipes entry for this check/violation_class)
    is given, strategies are re-ranked by empirical clearance (fix_model)."""
    excl = set(exclude or ())
    plan = _drc_plan(drc or {}, cfg, excl) if check == "drc" else _lvs_plan(lvs or {}, cfg, excl)
    return _rank_plan_strategies(plan, recipes)


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


def _load_recipes(proj: Path, *, check: str, drc: dict, lvs: dict,
                  heuristics: Path | None = None) -> dict | None:
    """Look up the Tier-3 fix_recipes entry for this design's family/platform and
    the current violation_class. Returns None (cold start) if absent."""
    hp = heuristics or (Path(__file__).resolve().parents[1] / "knowledge" / "heuristics.json")
    if not hp.exists():
        return None
    cfg = parse_config((proj / "constraints" / "config.mk").read_text(encoding="utf-8")
                       if (proj / "constraints" / "config.mk").exists() else "")
    design_name = cfg.get("DESIGN_NAME", "")
    # Key the family the SAME way the WRITER does (live ingest _project_family /
    # backfill): an EXPLICIT DESIGN_NAME mapping/pattern wins, else infer from the
    # project-DIR basename (which carries the source-repo prefix DESIGN_NAME drops).
    # Mismatch otherwise hides learned recipes — see CANONICAL FAMILY RULE.
    try:
        import knowledge_db
        families = knowledge_db.load_families()
        fam = (_explicit_family(design_name, families)
               or knowledge_db.infer_family(proj.name, families))
    except Exception:
        # knowledge_db unavailable: degrade to the writer's primary path (dir
        # basename split), which is where most harvested designs land.
        fam = (proj.name or design_name or "").split("_", 1)[0].lower()
    plat = cfg.get("PLATFORM", "nangate45")
    data = json.loads(hp.read_text(encoding="utf-8"))
    entry = (data.get("families", {}).get(fam, {})
             .get("platforms", {}).get(plat, {}).get("fix_recipes"))
    if not entry:
        return None
    by_check = entry.get(check, {})
    if check == "drc":
        cats = drc.get("categories") or {}
        vclass = max(cats, key=lambda k: cats[k].get("count") or 0) if cats else None
        recipe = by_check.get(vclass)
        if recipe is None and vclass:
            # DRC coarse-bucket fallback: backfill stores historical DRC recipes
            # under coarse buckets ('antenna'/'beol'), so an exact-category miss
            # (e.g. METAL3_ANTENNA) would hide that evidence.  Only used when the
            # exact-category recipe is absent.
            coarse = "antenna" if vclass.upper().endswith("_ANTENNA") else "beol"
            recipe = by_check.get(coarse)
        return recipe
    vclass = lvs.get("mismatch_class")
    return by_check.get(vclass)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Diagnose DRC/LVS violations → real-fix plan.")
    ap.add_argument("project_dir")
    ap.add_argument("--check", choices=["drc", "lvs"], default="drc")
    ap.add_argument("--apply", metavar="STRATEGY_ID", help="write the strategy's edits into config.mk")
    ap.add_argument("--next", action="store_true", help="print one tab-separated action line for the driver")
    ap.add_argument("--list", action="store_true",
                    help="print the full priority-ranked candidate list as JSON")
    ap.add_argument("--exclude", default="", help="comma-separated strategy ids to skip")
    args = ap.parse_args(argv)

    proj = Path(args.project_dir)
    drc = _load(proj / "reports" / "drc.json")
    lvs = _load(proj / "reports" / "lvs.json")
    cfg_path = proj / "constraints" / "config.mk"
    cfg_text = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""
    cfg = parse_config(cfg_text)
    exclude = [x for x in args.exclude.split(",") if x]
    recipes = _load_recipes(proj, check=args.check, drc=drc, lvs=lvs)
    plan = build_plan(drc, lvs, cfg, check=args.check, exclude=exclude, recipes=recipes)

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

    if args.list:
        print(json.dumps(plan.get("ranking", []), indent=2))
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
