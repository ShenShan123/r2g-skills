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
import hashlib
import json
import os
import re
import sys
from pathlib import Path

# Lifecycle statuses that BLOCK blind live auto-apply (P1-10, 2026-07-15). 'candidate'
# is awaiting A/B validation and 'shadow' is A/B-demoted — neither may be executed in a
# blind live run; only an A/B arm with --rank-first may force them. 'parked' is NOT here:
# it means the recipe's A/B arms can't diverge (a no-op edit), so the underlying static
# strategy stays a legitimate, harmless catalog action.
_LIVE_BLOCKED_LIFECYCLE = frozenset({"candidate", "shadow"})


def _effect_fp(strat: dict) -> str | None:
    """Canonical digest of a strategy's MUTATING effect (config/env/sdc edits + the
    rerun/recheck stages). Two strategy IDs with byte-identical effects share this
    fingerprint, so negative evidence against one can suppress its aliases (P1-14,
    2026-07-15). Returns None for a strategy with no declared effect (e.g. a bare test
    stub) so an 'empty effect' never collides distinct no-op strategies."""
    edits = strat.get("config_edits") or {}
    env = strat.get("env") or strat.get("env_flags") or {}
    sdc = strat.get("sdc") or strat.get("sdc_edits") or {}
    if not edits and not env and not sdc:
        return None
    payload = json.dumps({"config": edits, "env": env, "sdc": sdc,
                          "rerun_from": strat.get("rerun_from"),
                          "recheck": strat.get("recheck")}, sort_keys=True)
    return hashlib.sha1(payload.encode()).hexdigest()[:16]

try:
    import fix_model
except ImportError:                       # script run outside the test sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import fix_model

try:
    import symptom                         # symptom-indexed memory (spec 2026-06-09)
except ImportError:                       # knowledge/ not yet on the path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "knowledge"))
    import symptom

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


# Routing-geometry DRC density relief (validated 2026-06-16). Step/floor on
# CORE_UTILIZATION; 20->12 cleared eeprom_top sky130hd (4 m3.2 -> 0).
_UTIL_STEP = 8
_UTIL_FLOOR = 8


def _routing_drc_strategies(cfg: dict, exclude: set) -> list:
    """Non-antenna routing-geometry DRC (metal/via spacing, off-grid, via
    enclosure): lower CORE_UTILIZATION so the router gets more room. A REAL
    layout change (larger die, sparser routes) — the signoff deck is NEVER
    relaxed. Mirrors antenna_density_relief's lever. Validated 2026-06-16 on
    eeprom_top sky130hd (4 m3.2 -> 0 at util 20->12). Only when a CORE_UTILIZATION
    knob exists and sits above the floor; no-op for DIE_AREA-sized or
    already-sparse designs (honest residual)."""
    try:
        cur_util = int(float(cfg.get("CORE_UTILIZATION", "")))
    except (TypeError, ValueError):
        return []
    new_util = max(_UTIL_FLOOR, cur_util - _UTIL_STEP)
    if new_util >= cur_util:
        return []
    strat = {
        "id": "density_relief",
        "rationale": ("Lower CORE_UTILIZATION so the router has room to satisfy "
                      "metal/via spacing and off-grid rules on a congested small "
                      "die. Real layout change (bigger die); the routing/signoff "
                      "deck is never relaxed. PLACE_DENSITY_LB_ADDON untouched "
                      "(hard rule: never < 0.10)."),
        "config_edits": {"CORE_UTILIZATION": str(new_util)},
        "rerun_from": "floorplan", "recheck": "drc", "auto_apply": True}
    if strat["id"] in exclude or _applied(cfg, strat["config_edits"]):
        return []
    return [strat]


# Detailed-route congestion / DRT-residual / wall-clock-timeout relief (validated
# 2026-06-17; see references/failure-patterns.md "Routing Congestion" and the
# route-relief note). SAME CORE_UTILIZATION lever as density_relief, but keyed to a
# ROUTE-STAGE abort (orfs-fail-route, symptom check=orfs_stage/class=route) which
# never reaches signoff DRC — so the A/B loop was structurally blind to it until
# this strategy + fix_signoff.sh --check route wired backend aborts into the loop.
def _route_strategies(cfg: dict, exclude: set, *, to_floor: bool = False) -> list:
    """Route-stage abort relief: lower CORE_UTILIZATION so detailed routing has
    room to converge — congested / timeout routes (substitution-permutation crypto,
    dense interconnect) leave DRT grinding stubborn tiles until the wall-clock kills
    it. A REAL layout change (bigger die, sparser routes); the router/signoff deck
    is NEVER relaxed; PLACE_DENSITY_LB_ADDON untouched (hard rule: never < 0.10).
    Reruns from floorplan so the enlarged die re-places + re-routes. Only when a
    CORE_UTILIZATION knob exists above the floor; no-op for DIE_AREA-sized designs
    (honest residual -> operator enlarges DIE_AREA, a v2 lever).

    to_floor (2026-06-18): for a route TIMEOUT, drop straight to the floor in ONE
    reflow instead of shaving a single _UTIL_STEP. A timeout means detailed routing
    cannot converge at this density within the wall-clock budget; each incremental
    step burns another full timeout, and fix_one ABORTS after the first rerun that
    times out (rc=124) — so a single step was the design's only shot, escalating it
    prematurely (wbscope_avalon, verilog_ethernet_arp: timed out at util 12/17 after
    one step, never reaching the floor). Max room in one reflow is the right lever.
    A route that COMPLETED with violations keeps the gentle one-step relief (area is
    preserved and the violation count guides further iteration)."""
    try:
        cur_util = int(float(cfg.get("CORE_UTILIZATION", "")))
    except (TypeError, ValueError):
        return []
    new_util = _UTIL_FLOOR if to_floor else max(_UTIL_FLOOR, cur_util - _UTIL_STEP)
    if new_util >= cur_util:
        return []
    strat = {
        "id": "route_relief",
        "rationale": ("Lower CORE_UTILIZATION so detailed routing has room to "
                      "converge and finish within the wall-clock budget on a "
                      "congested die. Real layout change (bigger die); the "
                      "router/signoff deck is never relaxed."),
        "config_edits": {"CORE_UTILIZATION": str(new_util)},
        "rerun_from": "floorplan", "recheck": "route", "auto_apply": True}
    if strat["id"] in exclude or _applied(cfg, strat["config_edits"]):
        return []
    return [strat]


def _route_plan(route: dict, cfg: dict, exclude: set) -> dict:
    """Backend-abort plan for a route-stage failure (orfs-fail-route). Sibling of
    _drc_plan but for a stage that aborts BEFORE signoff. 'unknown' here means the
    route.json carries no route-stage outcome (the abort was earlier) -> no fix."""
    status = route.get("status", "unknown")
    plan = {"check": "route", "status": status,
            "violation_count": route.get("total_violations"),
            "dominant_category": "route", "strategies": [], "residual_reason": None}
    if status in ("clean", "skipped"):
        return plan
    if status == "unknown":
        plan["residual_reason"] = "no route-stage outcome to fix (abort earlier than route)"
        return plan
    if status in ("fail", "timeout", "residual"):
        # A pure timeout (route never completed) -> jump CORE_UTILIZATION to the
        # floor in one reflow; a route that completed WITH violations keeps the
        # gentle one-step relief. See _route_strategies (2026-06-18).
        strategies = _route_strategies(cfg, exclude, to_floor=(status == "timeout"))
        plan["strategies"] = strategies
        if not strategies:
            plan["status"] = "residual"
            if "CORE_UTILIZATION" not in cfg:
                plan["residual_reason"] = (
                    "route congestion but no CORE_UTILIZATION knob to relieve "
                    "(DIE_AREA-sized); enlarge DIE_AREA manually (v2 lever).")
            else:
                plan["residual_reason"] = (
                    f"route congestion: density relief exhausted (CORE_UTILIZATION "
                    f"at floor {_UTIL_FLOOR}); honest residual.")
        return plan
    plan["residual_reason"] = f"route status '{status}' not actionable in v1"
    return plan


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
            strategies = _routing_drc_strategies(cfg, exclude)
            plan["strategies"] = strategies
            if not strategies:
                plan["status"] = "residual"
                if "CORE_UTILIZATION" not in cfg:
                    plan["residual_reason"] = (
                        "non-antenna DRC class (" + ", ".join(non_antenna) +
                        "): no CORE_UTILIZATION knob to relieve density (DIE_AREA-sized).")
                else:
                    plan["residual_reason"] = (
                        "routing-geometry DRC (" + ", ".join(non_antenna) +
                        "): density relief exhausted (CORE_UTILIZATION at floor "
                        f"{_UTIL_FLOOR}); honest residual.")
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


def _rank_plan_strategies(plan: dict, recipes: dict | None,
                          pooled: dict | None = None) -> dict:
    """Reorder plan['strategies'] by fix_model and attach the full ranking.
    `pooled` (symptom-indexed memory, spec 2026-06-09) is the cross-platform prior
    used for strategies the local recipe has no data for; None preserves legacy
    behavior (build_plan's internal call passes None)."""
    if not plan.get("strategies"):
        return plan
    static_order = [s["id"] for s in plan["strategies"]]
    ranking = fix_model.rank_strategies(recipes, static_order, pooled=pooled)
    by_id = {s["id"]: s for s in plan["strategies"]}
    plan["strategies"] = [by_id[r["strategy"]] for r in ranking if r["strategy"] in by_id]
    plan["ranking"] = ranking
    return plan


def explain_ranking(plan: dict) -> list[str]:
    """Human rationale for WHY each recipe ranked (--explain, spec 2026-06-18).
    One line per ranked strategy: id, evidence (successes/attempts + wins),
    cross-platform corroboration (N platforms), A/B provenance, and which boost
    fired. Serves the 'transfer' mission — the engineer sees a fix is trusted
    because it carried across platforms, not one fluke. Read-only; no DB."""
    lines: list[str] = []
    for pos, r in enumerate(plan.get("ranking") or [], start=1):
        succ, att = r.get("successes", 0), r.get("attempts", 0)
        wins, pc = r.get("wins", 0), int(r.get("platform_count", 0) or 0)
        win_str = f", {wins} partial-win(s)" if wins else ""
        if pc >= 2:
            corro = (f"corroborated across {pc} platforms -> tiebreak lift")
        elif pc == 1:
            corro = "1 platform (single-platform evidence)"
        else:
            corro = "0 platforms (untried / single-design fluke)"
        line = (f"#{pos} {r['strategy']}: score={r['score']:.3f}  "
                f"evidence={succ}/{att}{win_str}  {corro}  "
                f"provenance={r.get('provenance', 'cold-start')}")
        mos = r.get("mean_outcome_score")
        if mos is not None:
            line += f"  outcome_score={mos:.3f}"
        lines.append(line)
    return lines


def _timing_plan(tcheck: dict, cfg: dict, exclude: set,
                 routing_clean: bool = False) -> dict:
    tier = tcheck.get("tier", "unknown")
    wns = tcheck.get("wns_ns")
    plan = {"check": "timing", "status": tier, "violation_count": None,
            "dominant_category": tier, "strategies": [], "residual_reason": None}
    if tier in ("clean", "unknown", None):
        return plan
    try:
        cur_util = int(float(cfg.get("CORE_UTILIZATION", "")))
    except (TypeError, ValueError):
        cur_util = 30
    strategies = []
    if tier in ("moderate", "severe") and wns is not None:
        period = tcheck.get("clock_period_ns")
        if period:
            # Absorb the negative slack then add 5% margin (proven iccad2015
            # period_relax recipe: 3 att / 2 succ, 97.5% WNS reduction).
            relaxed = round((float(period) - float(wns)) * 1.05, 3)
            strategies.append(
                {"id": "period_relax",
                 "rationale": f"Relax clock period {period} -> {relaxed} ns to "
                              "absorb WNS with 5% margin (validated recipe).",
                 "config_edits": {}, "sdc_edits": {"CLOCK_PERIOD": str(relaxed)},
                 "rerun_from": "synth", "recheck": "timing", "auto_apply": True})
    strategies.append(
        {"id": "utilization_reduce",
         "rationale": "Lower CORE_UTILIZATION to give placement/CTS slack "
                      "headroom (never touches PLACE_DENSITY_LB_ADDON).",
         "config_edits": {"CORE_UTILIZATION": str(max(5, cur_util - 5))},
         "sdc_edits": {}, "rerun_from": "floorplan", "recheck": "timing",
         "auto_apply": True})
    # Win 6 (backend-aware synthesis retune): a POST-ROUTE timing miss WITH clean
    # routing means the synth-time estimate was wrong, not the floorplan. Re-pick
    # the ABC mapping strategy (ABC_AREA off -> timing-driven) and flatten
    # (SYNTH_HIERARCHICAL off) so ABC optimizes across the full netlist, then
    # re-synthesize via the already-paved rerun_from:"synth" path; the re-run feeds
    # real routed WNS back as outcome_score (Win 1). Enters as SHADOW
    # (requires_ab_promotion): never auto-applied in a blind live run — only the
    # A/B arm (--rank-first) exercises it until it wins an LCB-gated trial (Win 2),
    # then the learned-recipe ranking surfaces it. Not auto-merged into
    # failure-patterns.md (human-review-queue invariant). See orfs-playbook.md.
    if routing_clean and tier in ("moderate", "severe"):
        strategies.append(
            {"id": "backend_aware_synth_retune",
             "rationale": "Post-route timing miss with clean routing: re-pick the "
                          "ABC map strategy (ABC_AREA=0 -> timing-driven) and "
                          "flatten (SYNTH_HIERARCHICAL=0) for cross-boundary "
                          "optimization, then re-synthesize. Closes the loop on "
                          "real routed WNS, not the synth-time estimate "
                          "(MCP4EDA / PostEDA-Bench). A/B-gated before live use.",
             "config_edits": {"ABC_AREA": "0", "SYNTH_HIERARCHICAL": "0"},
             "sdc_edits": {}, "rerun_from": "synth", "recheck": "timing",
             "auto_apply": True, "requires_ab_promotion": True})
    plan["strategies"] = [s for s in strategies if s["id"] not in exclude]
    return plan


def build_plan(drc: dict, lvs: dict, cfg: dict, *, check: str = "drc",
               exclude=(), recipes: dict | None = None,
               tcheck: dict | None = None, route: dict | None = None) -> dict:
    """Pure: (drc.json, lvs.json, parsed config.mk) -> ordered fix plan dict.
    When `recipes` (a Tier-3 fix_recipes entry for this check/violation_class)
    is given, strategies are re-ranked by empirical clearance (fix_model)."""
    excl = set(exclude or ())
    if check == "timing":
        routing_clean = (drc or {}).get("status") in ("clean", "clean_beol")
        plan = _timing_plan(tcheck or {}, cfg, excl, routing_clean=routing_clean)
    elif check == "drc":
        plan = _drc_plan(drc or {}, cfg, excl)
    elif check == "route":
        plan = _route_plan(route or {}, cfg, excl)
    else:
        plan = _lvs_plan(lvs or {}, cfg, excl)
    return _rank_plan_strategies(plan, recipes)


def _live_auto_strategy(plan: dict, rank_first: str | None = None) -> dict | None:
    """The strategy a LIVE run should auto-apply: the first auto_apply strategy,
    SKIPPING any `requires_ab_promotion` (shadow) recipe unless the caller forced
    it via --rank-first (the A/B arm-B path). This is the Win 6 gate that keeps a
    backend-aware retune out of blind live runs until it wins its A/B trial.

    Two further gates (2026-07-04, negative-evidence consumption):
    - lifecycle_status == 'shadow' (A/B-demoted): the demotion previously only
      stripped the strategy's learned boost from the INDEXED recipe path — via the
      static catalog / pooled prior / fallback paths it could still sort first and
      be auto-applied, making the demote verdict toothless. Gated HERE it holds on
      every path. ('parked' = merely unvalidatable — stays applicable.)
    - dead_here (>= R2G_FIX_DEAD_AFTER terminal failures of THIS strategy on THIS
      design+check with zero clears, annotated by _annotate_live_gates): a human
      engineer does not re-try the exact fix that failed twice on the same design;
      the loop re-abandoned one (design, symptom, strategy) triple 112 times.
      R2G_FIX_RETRY_DEAD=1 restores the old always-retry behavior.
    --rank-first bypasses ALL gates by design: an A/B arm B must be able to force
    exactly the strategy under test."""
    strategies = plan.get("strategies", [])
    # A/B arm B explicitly forces a strategy: honor it (it may be a shadow recipe).
    if rank_first:
        forced = next((s for s in strategies
                       if s["id"] == rank_first and s.get("auto_apply")), None)
        if forced is not None:
            return forced
    # P0-15 (2026-07-15): if the lifecycle store was UNREADABLE at annotation time we
    # cannot prove any static/cold-start strategy is safe to auto-apply — fail CLOSED
    # (no blind auto-apply) rather than silently falling back to un-gated selection.
    # Absent key (a caller that never annotated) keeps the prior behavior.
    if plan.get("lifecycle_gate_ok") is False:
        return None
    retry_dead = os.environ.get("R2G_FIX_RETRY_DEAD", "0") == "1"
    for s in strategies:
        if not s.get("auto_apply"):
            continue
        if s.get("requires_ab_promotion"):
            continue        # shadow recipe: never auto-applied in a blind live run
        # A/B-unvalidated ('candidate') or A/B-demoted ('shadow') recipe: never
        # auto-applied in a blind live run, on ANY lookup path (P1-10 + 2026-07-04).
        # A candidate that re-enters via the static catalog with a neutral cold-start
        # score must NOT execute before it wins its A/B trial ('parked' stays applicable).
        if s.get("lifecycle_status") in _LIVE_BLOCKED_LIFECYCLE:
            continue
        if s.get("dead_here") and not retry_dead:
            continue        # repeatedly failed on THIS design+check, never cleared
        return s
    return None


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
    """Parse a reports/*.json — a corrupt file (crash mid-write, disk full) must
    degrade to {} with a WARNING, not kill the diagnosis (2026-07-04)."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"WARNING: unreadable report {path}: {exc}; treating as empty",
              file=sys.stderr)
        return {}


def _load_heuristics(hp: Path):
    """Parse heuristics.json, degrading to None (cold start) on a corrupt or
    unreadable file instead of crashing the whole diagnosis — the fixer then
    ranks by static catalog order, exactly like a fresh clone (2026-07-04; the
    config-seeding path in suggest_config already degraded this way, the fix
    path did not)."""
    try:
        return json.loads(hp.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"WARNING: unreadable heuristics {hp}: {exc}; cold-start ranking",
              file=sys.stderr)
        return None


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
    plat = cfg.get("PLATFORM", "asap7")
    data = _load_heuristics(hp)
    if data is None:
        return None
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


def _platform_count(strat: dict) -> int:
    """Distinct platforms this strategy is CORROBORATED on = number of
    by_platform entries with successes>0 (spec 2026-06-18, the 'transfer'
    mission). Falls back to len(platforms_seen) when by_platform is absent (the
    Decision-8 indexed buckets carry only the symptom-level list). 0 when
    unknown — a single-design fluke never earns the corroboration tiebreak."""
    bp = strat.get("by_platform")
    if bp:
        return sum(1 for v in bp.values() if int(v.get("successes", 0) or 0) > 0)
    return len(strat.get("platforms_seen") or [])


def load_symptom_recipe(*, check: str, platform: str, drc: dict, lvs: dict,
                        heuristics: Path | None = None):
    """Return (recipe_entry, pooled_prior) for the current symptom, indexed by
    symptom_id (NOT family). recipe_entry = the current platform's by_platform
    stats (same-platform evidence preferred); pooled_prior = the cross-platform
    pooled stats for untried strategies, excluding platform_specific ones
    (symptom-indexed memory, spec 2026-06-09)."""
    # NOTE: parents[2] = the skill root (signoff-loop); knowledge/ is its child.
    hp = heuristics or (Path(__file__).resolve().parents[2] / "knowledge" / "heuristics.json")
    if not hp.exists():
        return None, {}
    data = _load_heuristics(hp)
    if data is None:
        return None, {}
    symptoms = data.get("symptoms") or {}
    if check == "drc":
        cats = drc.get("categories") or {}
        vclass = max(cats, key=lambda k: cats[k].get("count") or 0) if cats else None
        report = drc
    elif check == "lvs":
        vclass, report = lvs.get("mismatch_class"), lvs
    else:
        vclass, report = None, {}
    sig = symptom.canonical_signature(check, vclass, symptom.predicates_for(check, report))
    bucket = symptoms.get(symptom.symptom_id(sig))
    if not bucket:
        return None, {}
    strategies = bucket.get("strategies") or {}
    # Same-platform recipe: the by_platform slice for THIS platform.
    recipe = {"strategies": {}, "n_sessions": bucket.get("n_sessions", 0)}
    for stratid, s in strategies.items():
        bp = (s.get("by_platform") or {}).get(platform)
        if bp:
            # Annotate with the distinct-platform corroboration count so the
            # ranker's cross-platform tiebreaker (spec 2026-06-18, 'transfer')
            # can favour a fix proven across N platforms over a one-design fluke.
            recipe["strategies"][stratid] = {
                **bp, "platform_count": _platform_count(s)}
    # Pooled prior: cross-platform totals, minus platform_specific strategies.
    pooled = {stratid: {**{k: s.get(k, 0) for k in ("attempts", "successes", "wins", "failures")},
                        "platform_count": _platform_count(s)}
              for stratid, s in strategies.items() if not s.get("platform_specific")}
    return (recipe if recipe["strategies"] else None), pooled


def load_indexed_recipe(*, check: str, platform: str, design_class: str,
                        drc: dict, lvs: dict, heuristics: Path | None = None):
    """Decision-8 lookup with relaxation: recipes[sid][design_class][platform]
    -> recipes[sid]['*'][platform] (pooled class) -> recipes[sid]['*']['*']
    (pooled platform). Returns (recipe_entry|None, pooled_prior, match_level).
    pooled_prior is always the global rollup (recipes[sid]['*']['*'])."""
    hp = heuristics or (Path(__file__).resolve().parents[2]
                        / "knowledge" / "heuristics.json")
    if not hp.exists():
        return None, {}, "none"
    data = _load_heuristics(hp)
    if data is None:
        return None, {}, "none"
    recipes = data.get("recipes") or {}
    if check == "drc":
        cats = drc.get("categories") or {}
        vclass = max(cats, key=lambda k: cats[k].get("count") or 0) if cats else None
        report = drc
    else:
        vclass, report = lvs.get("mismatch_class"), lvs
    sig = symptom.canonical_signature(check, vclass,
                                      symptom.predicates_for(check, report))
    bucket = recipes.get(symptom.symptom_id(sig)) or {}
    # Cross-platform corroboration (spec 2026-06-18, 'transfer'): count the
    # DISTINCT concrete platforms (the '*' wildcard is the rollup, not a real
    # platform) on which each strategy cleared at least once, across all design
    # classes. The ranker uses this only as a tiebreaker.
    plat_count: dict[str, set] = {}
    for dclass, by_plat in bucket.items():
        for plat, node in (by_plat or {}).items():
            if plat == "*":
                continue
            for sid, v in (node.get("strategies") or {}).items():
                if int(v.get("successes", 0) or 0) > 0:
                    plat_count.setdefault(sid, set()).add(plat)
    glob = (bucket.get("*") or {}).get("*") or {}
    pooled = {s: {**{k: v.get(k, 0) for k in ("attempts", "successes",
                                              "wins", "failures")},
                  "platform_count": len(plat_count.get(s) or ())}
              for s, v in (glob.get("strategies") or {}).items()}
    for dclass, plat, level in ((design_class, platform, "exact"),
                                ("*", platform, "pooled_class"),
                                ("*", "*", "pooled_platform")):
        node = (bucket.get(dclass) or {}).get(plat)
        if node and node.get("strategies"):
            # Annotate the matched node's strategies with the corroboration count
            # so rank_strategies' local path also sees the cross-platform signal.
            strat_pc = {sid: {**v, "platform_count": len(plat_count.get(sid) or ())}
                        for sid, v in node["strategies"].items()}
            node = {**node, "strategies": strat_pc}
            return node, pooled, level
    return None, pooled, "none"


def _current_vclass(check: str, drc: dict, lvs: dict) -> str | None:
    """The dominant violation_class for the current symptom (same rule as
    load_symptom_recipe): DRC dominant category, else LVS mismatch_class."""
    if check == "drc":
        cats = drc.get("categories") or {}
        return max(cats, key=lambda k: cats[k].get("count") or 0) if cats else None
    if check == "lvs":
        return lvs.get("mismatch_class")
    return None


def _annotate_live_gates(plan: dict, proj: Path, *, check: str,
                         sid: str | None = None, design_class: str = "",
                         platform: str = "", db_path=None) -> dict:
    """Annotate plan strategies with the two negative-evidence gates
    _live_auto_strategy consumes (2026-07-04):

    - dead_here: count of terminal failures (no_change/regression) of this
      strategy on THIS design+check with ZERO clears, from fix_events — the
      cross-run memory the fixer lacked (the same dead fix was re-tried up to
      112 times across sessions). Threshold R2G_FIX_DEAD_AFTER (default 2).
    - lifecycle_status: the recipe_status verdict for this symptom key, so an
      A/B-demoted ('shadow') strategy is gated on EVERY lookup path, not only
      the indexed-recipe one filter_promoted covers.

    Best-effort by design: any DB problem (locked mid-campaign, missing tables)
    leaves the plan un-annotated with a WARNING — the gates then simply do not
    fire, which is the pre-2026-07-04 behavior, never a broken diagnosis."""
    try:
        import knowledge_db
        conn = (knowledge_db.connect(db_path) if db_path
                else knowledge_db.connect())
        knowledge_db.ensure_schema(conn)
    except Exception as exc:
        print(f"WARNING: negative-evidence gates unavailable "
              f"({type(exc).__name__}: {exc})", file=sys.stderr)
        # P0-15: the store was UNREADABLE — mark the gate unavailable so the live
        # selector fails CLOSED rather than silently proceeding with un-gated static
        # selection (which could execute a candidate/demoted strategy).
        plan["lifecycle_gate_ok"] = False
        return plan
    try:
        try:
            dead_after = max(1, int(os.environ.get("R2G_FIX_DEAD_AFTER", "2")))
        except ValueError:
            dead_after = 2
        # fix_events stores the project_path as the fixer received it; match both
        # the raw and resolved spellings.
        paths = sorted({str(proj), str(proj.resolve())})
        ph = ",".join("?" * len(paths))
        # P1-12 (2026-07-15): key dead-evidence by SYMPTOM when known — a strategy that
        # failed on DRC symptom A must NOT be blacklisted for a DIFFERENT DRC symptom B
        # on the same project (the old (project, check, strategy) key over-generalized).
        sym_sql = " AND symptom_id=?" if sid else ""
        sym_params = (sid,) if sid else ()
        rows = conn.execute(
            f"SELECT strategy, "
            f"SUM(CASE WHEN verdict IN ('no_change','regression') THEN 1 ELSE 0 END), "
            f"SUM(CASE WHEN verdict IN ('cleared','win') THEN 1 ELSE 0 END) "
            f"FROM fix_events WHERE project_path IN ({ph}) AND check_type=?{sym_sql} "
            f"GROUP BY strategy", (*paths, check, *sym_params)).fetchall()
        dead = {s: int(nf or 0) for s, nf, ns in rows
                if s and s != "none" and int(ns or 0) == 0
                and int(nf or 0) >= dead_after}
        # P1-14 (2026-07-15): dead evidence is by EFFECT, not just strategy name — an
        # alias strategy with byte-identical config/env/sdc edits inherits the dead flag,
        # so the loop can't retry the same ineffective action under a new id. Effects are
        # taken from the plan (both aliases are catalog entries), so the digests match.
        plan_strats = plan.get("strategies", [])
        fp_of = {s["id"]: _effect_fp(s) for s in plan_strats}
        dead_effects = {fp_of[s] for s in dead if fp_of.get(s)}
        statuses = {}
        if sid:
            import recipe_lifecycle
            for s in plan_strats:
                statuses[s["id"]] = recipe_lifecycle.get_status(
                    conn, symptom_id=sid, design_class=design_class,
                    platform=platform, strategy=s["id"])
        for s in plan_strats:
            fp = fp_of.get(s["id"])
            if s["id"] in dead:
                s["dead_here"] = dead[s["id"]]
            elif fp and fp in dead_effects:
                s["dead_here"] = dead_after      # alias of a dead-by-effect strategy
                s["dead_by_effect"] = True
            if statuses.get(s["id"]) and statuses[s["id"]] != "promoted":
                s["lifecycle_status"] = statuses[s["id"]]
        plan["lifecycle_gate_ok"] = True          # store read OK: gates are authoritative
    except Exception as exc:
        print(f"WARNING: negative-evidence gates unavailable "
              f"({type(exc).__name__}: {exc})", file=sys.stderr)
        plan["lifecycle_gate_ok"] = False         # partial/failed read -> fail closed
    finally:
        conn.close()
    return plan


def attach_lessons(plan: dict, *, check: str, vclass: str | None, platform: str) -> dict:
    """Attach matching ACTIVE prose lessons so the agent sees the human rationale
    in-context at the fix-decision point (symptom-indexed memory, spec 2026-06-09)."""
    try:
        import search_failures
        plan["lessons"] = search_failures.lessons_for_symptom(
            check=check, vclass=vclass, platform=platform)
    except Exception:
        plan["lessons"] = []
    return plan


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Diagnose DRC/LVS violations → real-fix plan.")
    ap.add_argument("project_dir")
    ap.add_argument("--check", choices=["drc", "lvs", "timing", "route"], default="drc")
    ap.add_argument("--apply", metavar="STRATEGY_ID", help="write the strategy's edits into config.mk")
    ap.add_argument("--next", action="store_true", help="print one tab-separated action line for the driver")
    ap.add_argument("--list", action="store_true",
                    help="print the full priority-ranked candidate list as JSON")
    ap.add_argument("--explain", action="store_true",
                    help="print a human rationale for WHY each recipe ranked "
                         "(evidence, cross-platform corroboration, provenance)")
    ap.add_argument("--exclude", default="", help="comma-separated strategy ids to skip")
    ap.add_argument("--rank-first", default=None,
                    help="force this strategy id to the head of the ranked plan (A/B arm B)")
    args = ap.parse_args(argv)

    proj = Path(args.project_dir)
    drc = _load(proj / "reports" / "drc.json")
    lvs = _load(proj / "reports" / "lvs.json")
    tcheck = _load(proj / "reports" / "timing_check.json")
    route = _load(proj / "reports" / "route.json")
    cfg_path = proj / "constraints" / "config.mk"
    cfg_text = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""
    cfg = parse_config(cfg_text)
    exclude = [x for x in args.exclude.split(",") if x]
    # Symptom-first lookup (spec 2026-06-09): prefer the symptom-indexed recipe +
    # pooled cross-platform prior; fall back to the legacy family/platform recipe.
    plat = cfg.get("PLATFORM", "asap7")
    # Decision-8 indexed lookup first (engineer-loop §5.7): exact (symptom,
    # design_class, platform) -> pooled class -> pooled platform; only PROMOTED
    # recipes rank live. Falls back to the symptom/family path when absent.
    try:
        import suggest_config as _sc
        _stats = _sc.parse_synth_stats(proj / "synth")
        _cells = _stats.get("cell_count", 0)
        _size = ("unknown" if not _cells else "tiny" if _cells < 100 else
                 "small" if _cells < 5000 else "medium" if _cells < 50000
                 else "large")
        design_class = f"{_sc.detect_design_type(proj, cfg)}/{_size}"
    except Exception:
        design_class = "unknown/unknown"
    # Route (backend-abort) symptoms index under check=orfs_stage/class=route, not
    # the drc/lvs report shape the indexed/symptom recipe readers assume. The static
    # route_relief strategy already carries the cold-start fix; learned ranking for
    # the route symptom rides through the learner -> heuristics, not this reader.
    #
    # P1-2 (recipe-lifecycle audit 2026-07-14, failure-patterns #48): this is an
    # INTENTIONAL single-strategy live path. route_relief is the ONLY live route fix and
    # is deliberately NOT lifecycle-stripped — demoting the sole route fix would leave
    # route failures unfixable, so route is grandfathered/static (unlike drc/lvs, whose
    # learned recipes ARE lifecycle-filtered). With one strategy there is no execution
    # order to reorder, so learned RANKING is a no-op for route today. The guard just
    # below fails LOUDLY the moment the route catalog grows past one strategy — at which
    # point indexed ranking + lifecycle filtering MUST be wired here like drc/lvs.
    if args.check == "route":
        recipes, pooled = None, {}
        idx_recipe = None
    else:
        recipes = pooled = None
        idx_recipe, idx_pooled, idx_level = load_indexed_recipe(
            check=args.check, platform=plat, design_class=design_class, drc=drc, lvs=lvs)
    # The symptom key for this diagnosis (drc/lvs only): shared by the lifecycle
    # filter below and the negative-evidence gates (_annotate_live_gates).
    _sid = None
    if args.check in ("drc", "lvs"):
        _vc = _current_vclass(args.check, drc, lvs)
        _report = drc if args.check == "drc" else lvs
        _sid = symptom.symptom_id(symptom.canonical_signature(
            args.check, _vc, symptom.predicates_for(args.check, _report)))
    if args.check != "route" and idx_recipe is not None:
        recipes, pooled = idx_recipe, idx_pooled
        try:
            import knowledge_db
            import recipe_lifecycle
            _kc = knowledge_db.connect()
            knowledge_db.ensure_schema(_kc)
            recipes = recipe_lifecycle.filter_promoted(
                _kc, recipes, symptom_id=_sid, design_class=design_class,
                platform=plat)
            _kc.close()
        except Exception as exc:
            # Fail CLOSED, visibly (2026-07-04): an unreadable lifecycle (DB locked
            # mid-campaign) must not hand unvalidated/demoted recipes a promoted-
            # equivalent ranking — the old silent `pass` degraded toward MORE trust.
            # Cold-start (static catalog order) is the safe floor; the fix proceeds.
            print(f"WARNING: recipe-lifecycle filter unavailable "
                  f"({type(exc).__name__}: {exc}); using cold-start ranking",
                  file=sys.stderr)
            recipes, pooled = None, {}
    elif args.check != "route":
        sym_recipe, pooled = load_symptom_recipe(check=args.check, platform=plat, drc=drc, lvs=lvs)
        recipes = sym_recipe if sym_recipe is not None else _load_recipes(
            proj, check=args.check, drc=drc, lvs=lvs)
    plan = build_plan(drc, lvs, cfg, check=args.check, exclude=exclude, recipes=recipes,
                      tcheck=tcheck, route=route)
    _rank_plan_strategies(plan, recipes, pooled=pooled)
    if args.check == "route" and len(plan.get("strategies", [])) > 1:
        # P1-2 self-announcing guard (recipe-lifecycle audit 2026-07-14): the live route
        # path is single-strategy by design (see the comment above). A route catalog that
        # now emits >1 strategy has outgrown that assumption — learned indexed ranking +
        # recipe-lifecycle filtering are NOT wired here, so execution order is static and
        # a demoted route recipe would still auto-apply. Fail loudly instead of silently
        # mis-ordering: wire load_indexed_recipe(check='orfs_stage', class='route') +
        # filter_promoted for route before shipping a second route strategy.
        print("WARNING: route catalog emitted >1 strategy but learned indexed ranking + "
              "recipe-lifecycle filtering are NOT wired for the live route path (P1-2, "
              "failure-patterns #48); route execution order is static. Wire route indexed "
              "ranking before adding a second route strategy.", file=sys.stderr)
    attach_lessons(plan, check=args.check,
                   vclass=_current_vclass(args.check, drc, lvs), platform=plat)
    _annotate_live_gates(plan, proj, check=args.check, sid=_sid,
                         design_class=design_class, platform=plat)

    if args.rank_first:
        head = [s for s in plan["strategies"] if s["id"] == args.rank_first]
        rest = [s for s in plan["strategies"] if s["id"] != args.rank_first]
        plan["strategies"] = head + rest

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
        # Lifecycle re-validation AT APPLY TIME (2026-07-16 agent-logic issue 6):
        # selection (--next) and apply are separate PROCESS invocations, so a recipe
        # demoted between them still applied — the safety system's withdrawal had no
        # effect on this branch (it looked the strategy up by id and wrote directly).
        # The gate re-reads the CURRENT lifecycle in THIS process, closing the race
        # to microseconds. --rank-first naming this exact strategy bypasses by
        # design (the A/B arm-B path MUST force the candidate under test). A
        # fallback-catalog strat (already-applied idempotent re-apply) was never
        # annotated at :991 — annotate it here through the same fail-closed path.
        if args.rank_first != strat["id"]:
            gate_plan = plan
            if "lifecycle_status" not in strat and strat not in plan.get("strategies", []):
                gate_plan = _annotate_live_gates(
                    {"strategies": [strat]}, proj, check=args.check, sid=_sid,
                    design_class=design_class, platform=plat)
            if (strat.get("lifecycle_status") in _LIVE_BLOCKED_LIFECYCLE
                    or gate_plan.get("lifecycle_gate_ok") is False):
                print(json.dumps({
                    "status": "lifecycle_blocked", "applied": None,
                    "strategy": strat["id"],
                    "lifecycle_status": strat.get("lifecycle_status"),
                    "lifecycle_gate_ok": gate_plan.get("lifecycle_gate_ok", True)}))
                print(f"ERROR: '{strat['id']}' is lifecycle-blocked "
                      f"(status={strat.get('lifecycle_status')}, "
                      f"gate_ok={gate_plan.get('lifecycle_gate_ok', True)}); an A/B "
                      f"arm may force it with --rank-first", file=sys.stderr)
                return 5
        # Verified-effect apply (2026-07-16 agent-logic issue 9): rc=0 used to mean
        # only "a strategy was identified" — a missing constraint.sdc (or an SDC with
        # no matchable period) silently skipped the edit, fix_signoff then reran a
        # full backend stage on a ZERO-EFFECT intervention and recorded the unchanged
        # failure as negative evidence AGAINST a recipe that was never applied.
        # Contract now: rc=0 ONLY when every DECLARED edit verifiably landed;
        # rc=4 ("precondition_failed" / "no_effect") otherwise, with nothing written
        # on a failed precondition (fix_signoff.sh aborts the iteration on rc!=0).
        sdc_edits = strat.get("sdc_edits") or {}
        sdc_path = proj / "constraints" / "constraint.sdc"
        sdc_new_p = str(sdc_edits["CLOCK_PERIOD"]) if sdc_edits.get("CLOCK_PERIOD") else None
        _SDC_VAR_RE = re.compile(r"(set\s+clk_period\s+)([\d.]+)")
        _SDC_LIT_RE = re.compile(r"((?:create_clock|set)\b[^\n]*-period\s+)([\d.]+)")
        if sdc_new_p is not None:
            # Precondition: the target file AND a rewritable period must exist BEFORE
            # any write, so a failed apply never leaves a half-applied strategy.
            if not sdc_path.exists():
                print(json.dumps({"status": "precondition_failed", "applied": None,
                                  "strategy": strat["id"],
                                  "unmet": [f"sdc_missing:{sdc_path}"]}))
                return 4
            sdc_text0 = sdc_path.read_text(encoding="utf-8")
            if not _SDC_VAR_RE.search(sdc_text0) and not _SDC_LIT_RE.search(sdc_text0):
                # Neither the templated `set clk_period N` var nor a literal
                # `-period N` (harvested/promoted RTL brings its own SDC style).
                print(json.dumps({"status": "precondition_failed", "applied": None,
                                  "strategy": strat["id"],
                                  "unmet": ["sdc_no_rewritable_period"]}))
                return 4
        if strat["config_edits"]:
            cfg_path.write_text(apply_edits(cfg_text, strat["config_edits"]), encoding="utf-8")
        if sdc_new_p is not None:
            sdc_text = sdc_path.read_text(encoding="utf-8")
            if _SDC_VAR_RE.search(sdc_text):
                sdc_text = _SDC_VAR_RE.sub(lambda m: m.group(1) + sdc_new_p, sdc_text)
            else:
                sdc_text = _SDC_LIT_RE.sub(lambda m: m.group(1) + sdc_new_p, sdc_text)
            sdc_path.write_text(sdc_text, encoding="utf-8")
            try:                                   # journal — never breaks apply
                import os as _os
                import subprocess as _sp
                _kdir = Path(__file__).resolve().parents[2] / "knowledge"
                _ja = [sys.executable, str(_kdir / "journal_action.py"), "action",
                       "--project", str(proj.resolve()), "--actor", "loop",
                       "--type", "sdc_edit", "--payload",
                       json.dumps({"knob": "CLOCK_PERIOD", "new": sdc_new_p,
                                   "strategy": strat["id"]})]
                _jdb = _os.environ.get("R2G_JOURNAL_DB")
                if _jdb:
                    _ja += ["--db", _jdb]
                _sp.run(_ja, check=False)
            except Exception:
                pass
        # Post-apply effect verification: re-read every touched file and confirm each
        # declared edit is REALLY there. A declared-but-unlanded edit (write raced,
        # regex drifted, block clobbered) must not report rc=0.
        unmet = []
        if strat["config_edits"]:
            cfg_after = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""
            for k, v in strat["config_edits"].items():
                if not re.search(rf"(?m)^\s*export\s+{re.escape(str(k))}\s*=\s*"
                                 rf"{re.escape(str(v))}\s*$", cfg_after):
                    unmet.append(f"config_edit_not_landed:{k}")
        if sdc_new_p is not None:
            sdc_after = sdc_path.read_text(encoding="utf-8") if sdc_path.exists() else ""
            m = _SDC_VAR_RE.search(sdc_after) or _SDC_LIT_RE.search(sdc_after)
            if not m or m.group(2) != sdc_new_p:
                unmet.append("sdc_edit_not_landed:CLOCK_PERIOD")
        if unmet:
            print(json.dumps({"status": "no_effect", "applied": None,
                              "strategy": strat["id"], "unmet": unmet}))
            return 4
        if not strat["config_edits"] and sdc_new_p is None:
            # A strategy that DECLARES no config/sdc edits (lvs_resolve_unknown:
            # recheck-only by design, NONDIVERGENT for A/B) is not a failed apply —
            # but the caller must be able to tell it wrote nothing, so it can never
            # masquerade as a material intervention in the journal/fix evidence.
            print(json.dumps({"status": "applied_no_op", "applied": strat["id"],
                              "config_edits": {}}))
            return 0
        out = {"status": "applied", "applied": strat["id"],
               "config_edits": strat["config_edits"]}
        if sdc_edits:
            out["sdc_edits"] = sdc_edits
        print(json.dumps(out))
        return 0

    if args.explain:
        # Human rationale for the ranking: WHY each recipe ranked, with the
        # cross-platform corroboration boost called out (spec 2026-06-18).
        rationale = explain_ranking(plan)
        plan["explain"] = rationale
        if rationale:
            for ln in rationale:
                print(ln)
        else:
            print(f"NO RANKED STRATEGIES\t{plan.get('status')}\t"
                  f"{plan.get('residual_reason') or 'n/a'}")
        return 0

    if args.list:
        # Print the FULL plan (ranking + attached lessons + strategies), so
        # consumers get the priority-ranked candidates AND the matching prose
        # rationale in one JSON object (spec 2026-06-09 §4.4).
        print(json.dumps(plan, indent=2))
        return 0

    if args.next:
        auto = _live_auto_strategy(plan, rank_first=args.rank_first)
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
