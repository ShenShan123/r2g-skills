#!/usr/bin/env python3
"""Heuristics payoff A/B harness — emit paired arms, summarize cost/quality.

Two subcommands:

  emit       For each pair in eval_set.json, generate TWO config.mk bodies via
             suggest_config.recommend — arm `naive` (use_learned=False) and arm
             `learned` (use_learned=True) — into
             <out-dir>/<design>_<arm>/constraints/config.mk. Writes
             eval_plan.json recording the arm dirs and which knob(s) differ.
             Does NOT run any flow.

  summarize  For each design with both <design>_naive and <design>_learned arm
             dirs present, read each arm's reports/{ppa,drc,lvs,rcx,
             timing_check}.json and backend/**/stage_log.jsonl, compute per-arm
             cost (wall-clock) + quality (knowledge_db.is_success semantics),
             join naive-vs-learned, classify win/regression/no_change, persist
             INCREMENTALLY to eval_results.jsonl, then write eval_summary.json
             as a PURE re-aggregate over that jsonl.

HONESTY NOTE (verified infra fact): the flow's stage_log.jsonl captures ONLY
wall-clock per stage ({"stage","status","elapsed_s"}). CPU-time and peak-RAM
are NOT captured anywhere in the current infrastructure. The cost metric is
therefore wall-clock seconds. We NEVER fabricate CPU-hours from wall-clock.
The reader is forward-compatible: if a future stage entry carries `cpu_s` /
`peak_rss_kb`, those are preferred and cost_metric reflects what was used.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

import knowledge_db
import suggest_config

# Cost metric tokens (forward-compatible). Wall-clock is the only metric the
# current flow instrumentation actually captures.
COST_WALL = "wall_clock_s"
COST_CPU = "cpu_s"

CPU_RAM_NOTE = (
    "CPU-hours/peak-RAM are NOT captured by the current flow instrumentation "
    "(stage_log.jsonl records only wall-clock elapsed_s per stage); cost is "
    "wall-clock seconds. Forward-compatible: cpu_s/peak_rss_kb are preferred "
    "if a future flow emits them. CPU-hours are never fabricated from "
    "wall-clock."
)

ARMS = ("naive", "learned")

# Knobs we surface as the per-arm config diff. CORE_UTILIZATION /
# PLACE_DENSITY_LB_ADDON are the only two the learned override touches.
_DIFF_KNOBS = ("CORE_UTILIZATION", "PLACE_DENSITY_LB_ADDON")


# --------------------------------------------------------------------------- #
# emit
# --------------------------------------------------------------------------- #
def _load_eval_set(path: Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    pairs = data.get("pairs") or []
    if not isinstance(pairs, list):
        raise ValueError(f"{path}: 'pairs' must be a list")
    return pairs


def _recommend_for_pair(pair: dict[str, Any], use_learned: bool,
                        projects_root: Path | None) -> dict[str, Any]:
    """Run suggest_config.recommend for one pair/arm.

    emit needs a real project to read synth stats from. If a materialized
    project dir exists under projects_root/<design_name>, we recommend from it.
    Otherwise we synthesize a minimal stand-in project (design-name + platform
    in config.mk, no synth.log) so recommend() still produces a config.mk from
    design-type + family heuristics — cell_count is then 0/unknown and the
    size_class is 'unknown'. We never crash on a missing project.
    """
    design = pair["design_name"]
    platform = pair.get("platform", "nangate45")

    real_proj = None
    if projects_root is not None:
        cand = Path(projects_root) / design
        if (cand / "constraints" / "config.mk").exists():
            real_proj = cand

    if real_proj is not None:
        return suggest_config.recommend(real_proj, use_learned=use_learned)

    # Synthesize a minimal stand-in so recommend() resolves family + design
    # type from DESIGN_NAME alone. cell_count stays unknown. recommend() returns
    # a plain dict with no lazy file handles, so it is safe to tear the stub
    # down in finally once the recommendation has been computed — otherwise
    # emitting the 6-design eval_set without --projects-root would leak 12
    # /tmp/evalstub_* dirs per invocation.
    stub = Path(tempfile.mkdtemp(prefix=f"evalstub_{design}_"))
    try:
        (stub / "constraints").mkdir(parents=True)
        (stub / "rtl").mkdir()
        (stub / "constraints" / "config.mk").write_text(
            f"export DESIGN_NAME = {design}\nexport PLATFORM = {platform}\n",
            encoding="utf-8",
        )
        # A tiny RTL stub lets detect_design_type see the design-name token
        # (e.g. 'aes' -> crypto) consistently for both arms.
        (stub / "rtl" / f"{design}.v").write_text(
            f"module {design}(input clk); endmodule\n", encoding="utf-8",
        )
        return suggest_config.recommend(stub, use_learned=use_learned)
    finally:
        shutil.rmtree(stub, ignore_errors=True)


def _render_config_mk(pair: dict[str, Any], arm: str, rec: dict[str, Any]) -> str:
    """Render a config.mk body with the EVAL_ARM marker + recommended knobs."""
    design = pair["design_name"]
    platform = pair.get("platform", "nangate45")
    family = pair.get("family", "")
    cell_count = rec.get("cell_count", 0)
    recs = rec.get("recommendations", {})

    if cell_count:
        cell_comment = f"# cell_count={cell_count}"
    else:
        cell_comment = ("# cell_count=unknown (project not materialized; "
                        "emitted from heuristics only)")

    lines = [
        f"# Payoff A/B harness arm: {arm}",
        f"# family={family} learned_source={rec.get('learned_source')}",
        cell_comment,
        f"export DESIGN_NAME = {design}",
        f"export PLATFORM = {platform}",
        f"export EVAL_ARM = {arm}",
    ]
    for k, v in recs.items():
        lines.append(f"export {k} = {v}")
    return "\n".join(lines) + "\n"


def _knob_diff(naive_rec: dict[str, Any], learned_rec: dict[str, Any]) -> dict[str, Any]:
    """Diff the two recommendation dicts, restricted to the tunable knobs the
    learned override can touch. Returns {knob: {"naive": v, "learned": v}} with
    values normalized to strings so eval_plan.json (native rec types) and
    eval_results.jsonl (config.mk-parsed strings) report knob_diff identically."""
    nr = naive_rec.get("recommendations", {})
    lr = learned_rec.get("recommendations", {})
    diff: dict[str, Any] = {}
    for k in _DIFF_KNOBS:
        nv, lv = nr.get(k), lr.get(k)
        if nv != lv:
            diff[k] = {"naive": str(nv), "learned": str(lv)}
    return diff


def cmd_emit(args: argparse.Namespace) -> int:
    eval_set_path = Path(args.eval_set)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    projects_root = Path(args.projects_root) if args.projects_root else None

    pairs = _load_eval_set(eval_set_path)
    plan_entries = []

    for pair in pairs:
        design = pair["design_name"]
        recs_by_arm: dict[str, dict[str, Any]] = {}
        for arm in ARMS:
            rec = _recommend_for_pair(pair, use_learned=(arm == "learned"),
                                      projects_root=projects_root)
            recs_by_arm[arm] = rec
            arm_dir = out_dir / f"{design}_{arm}"
            (arm_dir / "constraints").mkdir(parents=True, exist_ok=True)
            (arm_dir / "constraints" / "config.mk").write_text(
                _render_config_mk(pair, arm, rec), encoding="utf-8",
            )

        diff = _knob_diff(recs_by_arm["naive"], recs_by_arm["learned"])
        cell_count = recs_by_arm["learned"].get("cell_count", 0)
        plan_entries.append({
            "design_name": design,
            "platform": pair.get("platform", "nangate45"),
            "family": pair.get("family", ""),
            "cell_count": cell_count if cell_count else None,
            "cell_count_known": bool(cell_count),
            "naive_dir": str((out_dir / f"{design}_naive").resolve()),
            "learned_dir": str((out_dir / f"{design}_learned").resolve()),
            "naive_learned_source": recs_by_arm["naive"].get("learned_source"),
            "learned_learned_source": recs_by_arm["learned"].get("learned_source"),
            "knob_diff": diff,
        })

    plan = {
        "version": 1,
        "eval_set": str(eval_set_path.resolve()),
        "out_dir": str(out_dir.resolve()),
        "cost_metric": COST_WALL,
        "note": CPU_RAM_NOTE,
        "arms": list(ARMS),
        "designs": plan_entries,
    }
    plan_path = out_dir / "eval_plan.json"
    plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")

    # Operator instruction.
    print(f"Emitted {len(pairs)} design(s) x {len(ARMS)} arms into {out_dir}")
    for e in plan_entries:
        kd = e["knob_diff"] or "(no knob differs — would be a no-op A/B)"
        print(f"  {e['design_name']}: knob_diff={kd}")
    print()
    print("Next (operator-driven; multi-hour EDA flows — NOT run here):")
    print(f"  1. Materialize each <design> via init_project + the design's RTL")
    print(f"     (copy each arm's constraints/config.mk into the run dir so "
          f"FLOW_VARIANT is isolated by basename).")
    print(f"  2. Run both arms through the flow, e.g. via tools/batch_flow.sh")
    print(f"     (distinct <design>_<arm> basenames keep ORFS FLOW_VARIANT "
          f"isolated — never run same DESIGN_NAME+FLOW_VARIANT concurrently).")
    print(f"  3. eval_heuristics.py summarize --arms-dir {out_dir} "
          f"--out-dir {out_dir}")
    print(f"\nPlan: {plan_path}")
    return 0


# --------------------------------------------------------------------------- #
# summarize
# --------------------------------------------------------------------------- #
def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_stage_log(arm_dir: Path) -> list[dict[str, Any]]:
    """Find the most-recent backend/RUN_*/stage_log.jsonl (falling back to the
    legacy flat backend/stage_log.jsonl) and parse it line-by-line."""
    backend = arm_dir / "backend"
    path = backend / "stage_log.jsonl"
    if backend.is_dir():
        run_dirs = sorted(
            (d for d in backend.iterdir()
             if d.is_dir() and d.name.startswith("RUN_")),
            key=lambda d: d.stat().st_mtime, reverse=True,
        )
        for rd in run_dirs:
            cand = rd / "stage_log.jsonl"
            if cand.exists():
                path = cand
                break
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _stage_cost(entry: dict[str, Any]) -> tuple[float, str]:
    """Return (cost, metric) for one stage entry. Prefer cpu_s if present
    (future instrumentation), else fall back to wall-clock elapsed_s. peak_rss_kb
    is recorded elsewhere but does not contribute to the scalar cost."""
    cpu = entry.get("cpu_s")
    if cpu is not None:
        try:
            return (float(cpu), COST_CPU)
        except (TypeError, ValueError):
            pass
    el = entry.get("elapsed_s")
    try:
        return (float(el) if el is not None else 0.0, COST_WALL)
    except (TypeError, ValueError):
        return (0.0, COST_WALL)


def _arm_cost(stage_log: list[dict[str, Any]]) -> dict[str, Any]:
    """Total + per-stage cost, plus the cost_metric actually used. If ANY stage
    lacked cpu_s, the metric degrades to wall_clock_s (we never mix-and-claim
    cpu)."""
    per_stage: dict[str, float] = {}
    total = 0.0
    metric = COST_CPU  # optimistic; degrades to wall on first wall-only stage
    saw_any = False
    peak_rss_kb = None
    for s in stage_log:
        cost, m = _stage_cost(s)
        if m == COST_WALL:
            metric = COST_WALL
        name = s.get("stage", "?")
        per_stage[name] = per_stage.get(name, 0.0) + cost
        total += cost
        saw_any = True
        rss = s.get("peak_rss_kb")
        if rss is not None:
            try:
                peak_rss_kb = max(peak_rss_kb or 0, int(rss))
            except (TypeError, ValueError):
                pass
    if not saw_any:
        metric = COST_WALL  # nothing read; default honest metric
    return {
        "total_cost": total,
        "per_stage_cost": per_stage,
        "cost_metric": metric,
        "peak_rss_kb": peak_rss_kb,  # None unless future instrumentation present
    }


def _is_finish_reached(stage_log: list[dict[str, Any]]) -> bool:
    return any(s.get("stage") == "finish" and s.get("status") == "pass"
              for s in stage_log)


def _arm_quality(arm_dir: Path, stage_log: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a knowledge_db.is_success-shaped row from the arm's report JSONs
    and REUSE the exact predicate. Returns signoff_ok + raw violation counts."""
    ppa = _read_json(arm_dir / "reports" / "ppa.json") or {}
    drc = _read_json(arm_dir / "reports" / "drc.json") or {}
    lvs = _read_json(arm_dir / "reports" / "lvs.json") or {}
    rcx = _read_json(arm_dir / "reports" / "rcx.json") or {}

    finish_reached = _is_finish_reached(stage_log)
    # orfs_status: mirror is_success's strict path requirement. is_success uses
    # orfs_status=='pass' OR the relaxed positive-signoff path; we feed 'pass'
    # only when the flow actually reached finish so the strict branch is honest.
    orfs_status = "pass" if finish_reached else "partial"

    row = {
        "orfs_status": orfs_status,
        "drc_status": drc.get("status"),
        "lvs_status": lvs.get("status"),
        "rcx_status": rcx.get("status"),
        "lvs_mismatch_class": lvs.get("mismatch_class"),
    }
    signoff_ok = knowledge_db.is_success(row)

    geometry = ppa.get("geometry", {}) if isinstance(ppa, dict) else {}
    summary = ppa.get("summary", {}) if isinstance(ppa, dict) else {}
    power = summary.get("power", {}) if isinstance(summary, dict) else {}

    def _f(v: Any) -> float | None:
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def _i(v: Any) -> int | None:
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    return {
        "signoff_ok": bool(signoff_ok),
        "finish_reached": finish_reached,
        "orfs_status": orfs_status,
        "drc_status": drc.get("status"),
        "drc_violations": _i(drc.get("total_violations")),
        "lvs_status": lvs.get("status"),
        "lvs_mismatch_class": lvs.get("mismatch_class"),
        "lvs_mismatch_count": _i(lvs.get("mismatch_count")),
        "rcx_status": rcx.get("status"),
        "die_area_um2": _f(geometry.get("die_area_um2")),
        "total_power_w": _f(power.get("total_power_w")),
    }


def _config_knobs(arm_dir: Path) -> dict[str, str]:
    """Parse the emitted config.mk's tunable knobs for the knob_diff."""
    cfg = suggest_config.parse_config_mk(arm_dir / "constraints" / "config.mk")
    return {k: cfg[k] for k in _DIFF_KNOBS if k in cfg}


def _knob_diff_from_dirs(naive_dir: Path, learned_dir: Path) -> dict[str, Any]:
    """Diff the emitted config.mk knobs. Values are already config-string typed;
    wrap in str() so the shape matches emit's _knob_diff exactly (both string)."""
    nk = _config_knobs(naive_dir)
    lk = _config_knobs(learned_dir)
    diff: dict[str, Any] = {}
    for k in _DIFF_KNOBS:
        nv, lv = nk.get(k), lk.get(k)
        if nv != lv:
            diff[k] = {"naive": str(nv), "learned": str(lv)}
    return diff


def _effective_lvs_mismatch(q: dict) -> int | None:
    """LVS mismatch count that counts against quality. A symmetric_matcher
    'fail' is a KLayout limitation on a clean layout (is_success treats it as
    not-failed), so its raw mismatch_count must NOT count as a real defect."""
    if q.get("lvs_mismatch_class") == "symmetric_matcher":
        return 0
    return q.get("lvs_mismatch_count")


def _violations_held(naive_q: dict, learned_q: dict) -> bool:
    """learned violation counts <= naive (treating None as unknown -> not worse
    only when both None; a known increase is a regression). symmetric_matcher
    LVS mismatches are not real defects and are normalized to 0 first."""
    pairs = [
        (naive_q.get("drc_violations"), learned_q.get("drc_violations")),
        (_effective_lvs_mismatch(naive_q), _effective_lvs_mismatch(learned_q)),
    ]
    for nv, lv in pairs:
        if nv is None or lv is None:
            continue  # can't compare; don't penalize
        if lv > nv:
            return False
    return True


def _classify(cost_delta_pct: float | None,
              naive_q: dict, learned_q: dict) -> str:
    """win | regression | inconclusive | no_change | worse.

    A "win" is an HONEST headline: the learned config must produce a usable,
    signed-off result that is cheaper — never merely cheaper garbage. So a win
    requires the learned arm to actually pass signoff (learned_usable), not just
    to be "no worse than a failing naive arm".

    learned_usable = learned signoff_ok AND known violation counts did not
                     increase vs naive.

    win          cheaper AND learned_usable.
    inconclusive NEITHER arm produced a usable signoff — a cheaper-but-unusable
                 learned arm is NOT a win and NOT a regression (there was no
                 usable baseline to break). Tested BEFORE regression.
    regression   cheaper AND naive_ok AND NOT learned_usable — the learned
                 config broke a usable baseline (or added violations).
    no_change    NOT cheaper AND learned_usable — usable but no cost gain.
    worse        everything else (not cheaper, learned not usable while there
                 was a usable baseline to preserve).
    """
    cheaper = cost_delta_pct is not None and cost_delta_pct > 0
    naive_ok = naive_q.get("signoff_ok", False)
    learned_ok = learned_q.get("signoff_ok", False)
    learned_usable = learned_ok and _violations_held(naive_q, learned_q)

    # Order matters: win first, then both-fail (inconclusive) before regression,
    # so a both-fail-cheaper case returns inconclusive, NOT win and NOT regression.
    if cheaper and learned_usable:
        return "win"
    if (not naive_ok) and (not learned_ok):
        return "inconclusive"
    if cheaper and naive_ok and not learned_usable:
        return "regression"
    if (not cheaper) and learned_usable:
        return "no_change"
    return "worse"


def _discover_designs(arms_dir: Path) -> list[str]:
    """Designs with BOTH <design>_naive and <design>_learned arm dirs."""
    naive = set()
    learned = set()
    for d in arms_dir.iterdir():
        if not d.is_dir():
            continue
        if d.name.endswith("_naive"):
            naive.add(d.name[: -len("_naive")])
        elif d.name.endswith("_learned"):
            learned.add(d.name[: -len("_learned")])
    return sorted(naive & learned)


def _platform_of(arm_dir: Path) -> str:
    cfg = suggest_config.parse_config_mk(arm_dir / "constraints" / "config.mk")
    return cfg.get("PLATFORM", "nangate45")


def _evaluate_design(arms_dir: Path, design: str) -> dict[str, Any]:
    naive_dir = arms_dir / f"{design}_naive"
    learned_dir = arms_dir / f"{design}_learned"

    naive_log = _read_stage_log(naive_dir)
    learned_log = _read_stage_log(learned_dir)
    naive_cost = _arm_cost(naive_log)
    learned_cost = _arm_cost(learned_log)
    naive_q = _arm_quality(naive_dir, naive_log)
    learned_q = _arm_quality(learned_dir, learned_log)

    # Cost metric for the design: degrade to wall if EITHER arm used wall.
    cost_metric = COST_WALL
    if naive_cost["cost_metric"] == COST_CPU and learned_cost["cost_metric"] == COST_CPU:
        cost_metric = COST_CPU

    nc = naive_cost["total_cost"]
    lc = learned_cost["total_cost"]
    cost_delta_pct: float | None = None
    if nc and nc > 0:
        cost_delta_pct = (nc - lc) / nc * 100.0

    knob_diff = _knob_diff_from_dirs(naive_dir, learned_dir)
    classification = _classify(cost_delta_pct, naive_q, learned_q)

    return {
        "design_name": design,
        "platform": _platform_of(naive_dir),
        "cost_metric": cost_metric,
        "classification": classification,
        "cost_delta_pct": cost_delta_pct,
        "knob_diff": knob_diff,
        "naive": {
            "total_cost": nc,
            "per_stage_cost": naive_cost["per_stage_cost"],
            "cost_metric": naive_cost["cost_metric"],
            "peak_rss_kb": naive_cost["peak_rss_kb"],
            **naive_q,
        },
        "learned": {
            "total_cost": lc,
            "per_stage_cost": learned_cost["per_stage_cost"],
            "cost_metric": learned_cost["cost_metric"],
            "peak_rss_kb": learned_cost["peak_rss_kb"],
            **learned_q,
        },
    }


def _result_key(rec: dict[str, Any]) -> tuple[str, str]:
    return (rec.get("design_name", ""), rec.get("platform", ""))


def _load_results_jsonl(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """Load existing results keyed by (design, platform) for idempotent merge."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            # summarize rewrites the whole file, so a silently-dropped line is
            # permanently erased — warn loudly instead of swallowing it.
            print(f"warning: skipping malformed eval_results line: {line[:80]}",
                  file=sys.stderr)
            continue
        out[_result_key(rec)] = rec
    return out


def _write_results_jsonl(path: Path,
                         results: dict[tuple[str, str], dict[str, Any]]) -> None:
    """Write the jsonl deterministically (sorted by key), one record per line."""
    lines = []
    for key in sorted(results):
        lines.append(json.dumps(results[key], sort_keys=True))
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    if n % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


def reaggregate(results_path: Path) -> dict[str, Any]:
    """PURE deterministic re-aggregate over eval_results.jsonl. Reads the jsonl
    back from disk — never hand-computed from in-memory evaluation. This is the
    single source of truth for eval_summary.json."""
    results = _load_results_jsonl(results_path)
    recs = [results[k] for k in sorted(results)]

    # Cost metric for the summary: cpu_s only if EVERY design used cpu_s.
    metrics = {r.get("cost_metric", COST_WALL) for r in recs}
    summary_metric = COST_CPU if metrics == {COST_CPU} else COST_WALL

    n_designs = len(recs)
    n_wins = sum(1 for r in recs if r.get("classification") == "win")
    n_regressions = sum(1 for r in recs if r.get("classification") == "regression")
    # inconclusive = neither arm produced a usable signoff; neither a win nor a
    # regression (kept distinct so cheaper-but-unusable is never a headline win).
    n_inconclusive = sum(1 for r in recs if r.get("classification") == "inconclusive")

    all_deltas = [r["cost_delta_pct"] for r in recs
                  if r.get("cost_delta_pct") is not None]
    win_deltas = [r["cost_delta_pct"] for r in recs
                  if r.get("classification") == "win"
                  and r.get("cost_delta_pct") is not None]

    return {
        "version": 1,
        "cost_metric": summary_metric,
        "n_designs": n_designs,
        "n_wins": n_wins,
        "n_regressions": n_regressions,
        "n_inconclusive": n_inconclusive,
        "median_cost_delta_pct_all": _median(all_deltas),
        "median_cost_delta_pct_wins": _median(win_deltas),
        "note": CPU_RAM_NOTE,
        "designs": recs,
    }


def cmd_summarize(args: argparse.Namespace) -> int:
    arms_dir = Path(args.arms_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "eval_results.jsonl"
    summary_path = out_dir / "eval_summary.json"

    if not args.reaggregate_only:
        if not arms_dir.is_dir():
            print(f"arms-dir not found: {arms_dir}", file=sys.stderr)
            return 1
        designs = _discover_designs(arms_dir)
        if not designs:
            print(f"No paired <design>_naive/<design>_learned dirs under "
                  f"{arms_dir}", file=sys.stderr)
        # Incremental + idempotent: merge into existing results keyed by
        # (design, platform); re-running REPLACES a design's line.
        results = _load_results_jsonl(results_path)
        for design in designs:
            rec = _evaluate_design(arms_dir, design)
            results[_result_key(rec)] = rec
        _write_results_jsonl(results_path, results)

    # eval_summary.json is ALWAYS a pure re-aggregate over the jsonl on disk.
    summary = reaggregate(results_path)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n",
                            encoding="utf-8")

    print(f"cost_metric={summary['cost_metric']} "
          f"n_designs={summary['n_designs']} "
          f"n_wins={summary['n_wins']} "
          f"n_regressions={summary['n_regressions']}")
    print(f"median_cost_delta_pct (wins)={summary['median_cost_delta_pct_wins']} "
          f"(all)={summary['median_cost_delta_pct_all']}")
    for r in summary["designs"]:
        print(f"  {r['design_name']}/{r['platform']}: {r['classification']} "
              f"(cost_delta_pct={r['cost_delta_pct']}, knob_diff={r['knob_diff']})")
    print(f"\nResults: {results_path}\nSummary: {summary_path}")
    return 0


# --------------------------------------------------------------------------- #
# fix-loop A/B arm: empirical (ranked) vs static catalog ordering
# --------------------------------------------------------------------------- #
# The fix loop records each fixing episode as a fix_trajectories row. To A/B the
# empirically-ranked strategy order (fix_model.rank_strategies) against the static
# catalog order, it tags the episode with an arm: "ranked" or "static". There is
# NO eval_arm column on fix_trajectories (it is re-derivable from fix_events and we
# do not widen the schema). The tag is encoded INSIDE the trajectory's existing
# winning_config_json JSON string under key "eval_arm".
FIX_ARMS = ("ranked", "static")


def _trajectory_arm(winning_config_json: str | None) -> str | None:
    """Decode the A/B arm from a trajectory's winning_config_json string.
    Returns 'ranked'|'static' or None if absent/unparseable."""
    if not winning_config_json:
        return None
    try:
        cfg = json.loads(winning_config_json)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(cfg, dict):
        return None
    arm = cfg.get("eval_arm")
    return arm if arm in FIX_ARMS else None


def _score_fix_pair(ranked: dict[str, Any] | None,
                    static: dict[str, Any] | None) -> str | None:
    """Winner for one (family, check) pair on payoff = (iters-to-resolve,
    total_elapsed_s). Only an arm that REACHED outcome 'resolved' can win, and a
    winner requires BOTH arms present to compare. The ranked arm wins when it
    resolves in fewer iterations (ties broken by lower total_elapsed_s). None
    when there is no comparable pair or neither arm resolved."""
    if ranked is None or static is None:
        return None
    r_resolved = ranked.get("outcome") == "resolved"
    s_resolved = static.get("outcome") == "resolved"
    if not r_resolved and not s_resolved:
        return None
    if r_resolved and not s_resolved:
        return "ranked"
    if s_resolved and not r_resolved:
        return "static"

    # Both resolved — compare iters-to-resolve, then wall-clock.
    def _payoff(t: dict[str, Any]) -> tuple[float, float]:
        n = t.get("n_iters")
        el = t.get("total_elapsed_s")
        n_f = float(n) if n is not None else float("inf")
        el_f = float(el) if el is not None else float("inf")
        return (n_f, el_f)

    rp, sp = _payoff(ranked), _payoff(static)
    if rp < sp:
        return "ranked"
    if sp < rp:
        return "static"
    return None  # genuine tie — no win for either arm


def summarize_fix_arms(db_path: Path | str,
                       out_path: Path | str | None = None) -> dict[str, Any]:
    """Compare fix_trajectories tagged 'ranked' vs 'static' (the arm is read from
    winning_config_json["eval_arm"]) on payoff = (iters-to-resolve, elapsed).

    Groups by (design_family, platform, check_type, violation_class), picks the
    best (fewest-iter resolved) trajectory per arm, scores a winner per pair, and
    writes fix_eval_summary.json (next to the DB by default, or to out_path).
    """
    db_path = Path(db_path)
    if out_path is None:
        out_path = db_path.parent / "fix_eval_summary.json"
    out_path = Path(out_path)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT fix_session_id, design_family, platform, check_type, "
            "       violation_class, n_iters, outcome, total_elapsed_s, "
            "       winning_config_json "
            "FROM fix_trajectories"
        ).fetchall()
    finally:
        conn.close()

    # group key -> arm -> best trajectory (fewest iters among resolved, else any)
    groups: dict[tuple[str, str, str, str], dict[str, dict[str, Any]]] = {}
    for r in rows:
        arm = _trajectory_arm(r["winning_config_json"])
        if arm is None:
            continue
        key = (r["design_family"], r["platform"], r["check_type"],
               r["violation_class"])
        traj = {
            "fix_session_id": r["fix_session_id"],
            "n_iters": r["n_iters"],
            "outcome": r["outcome"],
            "total_elapsed_s": r["total_elapsed_s"],
        }
        by_arm = groups.setdefault(key, {})
        prev = by_arm.get(arm)
        if prev is None or _better_episode(traj, prev):
            by_arm[arm] = traj

    pairs: list[dict[str, Any]] = []
    n_ranked_wins = 0
    n_static_wins = 0
    for key in sorted(groups):
        fam, plat, check, vclass = key
        by_arm = groups[key]
        ranked = by_arm.get("ranked")
        static = by_arm.get("static")
        winner = _score_fix_pair(ranked, static)
        if winner == "ranked":
            n_ranked_wins += 1
        elif winner == "static":
            n_static_wins += 1
        pairs.append({
            "design_family": fam,
            "platform": plat,
            "check_type": check,
            "violation_class": vclass,
            "winner": winner,
            "ranked_session": ranked["fix_session_id"] if ranked else None,
            "ranked_outcome": ranked["outcome"] if ranked else None,
            "ranked_n_iters": ranked["n_iters"] if ranked else None,
            "ranked_elapsed_s": ranked["total_elapsed_s"] if ranked else None,
            "static_session": static["fix_session_id"] if static else None,
            "static_outcome": static["outcome"] if static else None,
            "static_n_iters": static["n_iters"] if static else None,
            "static_elapsed_s": static["total_elapsed_s"] if static else None,
        })

    summary = {
        "version": 1,
        "arms": list(FIX_ARMS),
        "payoff": "iters_to_resolve_then_total_elapsed_s",
        "n_pairs": len(pairs),
        "n_ranked_wins": n_ranked_wins,
        "n_static_wins": n_static_wins,
        "pairs": pairs,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    return summary


def _better_episode(cand: dict[str, Any], cur: dict[str, Any]) -> bool:
    """Prefer the better trajectory per arm: a resolved episode beats a
    non-resolved one; among same-outcome episodes, fewer iters (then lower
    elapsed) wins."""
    cand_resolved = cand.get("outcome") == "resolved"
    cur_resolved = cur.get("outcome") == "resolved"
    if cand_resolved != cur_resolved:
        return cand_resolved  # resolved beats not-resolved

    def _payoff(t: dict[str, Any]) -> tuple[float, float]:
        n = t.get("n_iters")
        el = t.get("total_elapsed_s")
        return (float(n) if n is not None else float("inf"),
                float(el) if el is not None else float("inf"))

    return _payoff(cand) < _payoff(cur)


def cmd_summarize_fix(args: argparse.Namespace) -> int:
    summary = summarize_fix_arms(args.db, out_path=args.out)
    out_path = (Path(args.out) if args.out
                else Path(args.db).parent / "fix_eval_summary.json")
    print(f"n_pairs={summary['n_pairs']} "
          f"n_ranked_wins={summary['n_ranked_wins']} "
          f"n_static_wins={summary['n_static_wins']}")
    for p in summary["pairs"]:
        print(f"  {p['design_family']}/{p['platform']}/{p['check_type']}/"
              f"{p['violation_class']}: winner={p['winner']} "
              f"(ranked n_iters={p['ranked_n_iters']}, "
              f"static n_iters={p['static_n_iters']})")
    print(f"\nSummary: {out_path}")
    return 0


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("emit", help="Generate paired naive/learned config.mk")
    pe.add_argument("--eval-set", required=True,
                    help="Path to eval_set.json")
    pe.add_argument("--out-dir", required=True,
                    help="Directory for <design>_<arm>/ arm dirs + eval_plan.json")
    pe.add_argument("--projects-root", default=None,
                    help="Optional root of materialized projects "
                         "(<root>/<design_name>) to read synth stats from. "
                         "If absent or a project is missing, emit from "
                         "design-name/platform heuristics with cell_count "
                         "unknown.")
    pe.set_defaults(func=cmd_emit)

    ps = sub.add_parser("summarize",
                        help="Join naive vs learned arms, classify, summarize")
    ps.add_argument("--arms-dir", required=True,
                    help="Directory containing <design>_<arm>/ run dirs")
    ps.add_argument("--out-dir", required=True,
                    help="Directory for eval_results.jsonl + eval_summary.json")
    ps.add_argument("--reaggregate-only", action="store_true",
                    help="Skip re-reading arm dirs; just re-aggregate "
                         "eval_summary.json from the existing eval_results.jsonl.")
    ps.set_defaults(func=cmd_summarize)

    pf = sub.add_parser("summarize-fix",
                        help="A/B the fix loop's ranked vs static strategy "
                             "ordering on payoff (iters-to-resolve, elapsed)")
    pf.add_argument("--db", required=True,
                    help="Path to knowledge.sqlite (reads fix_trajectories)")
    pf.add_argument("--out", default=None,
                    help="Output path for fix_eval_summary.json "
                         "(default: next to the DB)")
    pf.set_defaults(func=cmd_summarize_fix)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
