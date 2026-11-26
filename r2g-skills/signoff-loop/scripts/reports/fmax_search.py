#!/usr/bin/env python3
"""Automated loose-first Fmax characterization for signoff-loop.

Finds the minimum clock period a design can close at, using placement-stage
timing as the search signal and a learnable per-family slack-deterioration model
(see references/orfs-playbook.md "Fmax Search"; the 2026-06-04 design spec was
retired 2026-07-09 — implemented, durable content lives in the playbook). Reports a
predicted-signoff Fmax proxy; --verify runs one full signoff flow at the winner.

Usage:
  fmax_search.py <project-dir> [platform] [--verify] [--keep-variants]
                 [--place-fast] [--probe-timeout N]
  fmax_search.py --record-relax OLD NEW SOURCE <project-dir>
      record a post-search SDC loosening so the manifest can bind it to the winner

The search is sequential (one ORFS probe at a time). Cross-design parallelism
is achieved by running multiple fmax_search.py invocations concurrently
(future: fmax-drain --workers).
"""
from __future__ import annotations
import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Make sibling skill modules importable when run as a CLI. conftest.py seeds these
# paths for pytest; this makes `python3 fmax_search.py <project>` work standalone
# too. MUST run BEFORE `import extract_ppa` — _add_paths() (defined below) executes
# inside main()/probes, far too late for these module-level imports. (Bug surfaced
# 2026-06-19: the feature had only ever been exercised via pytest, never as a CLI.)
SKILL_ROOT = Path(__file__).resolve().parents[2]
for _sub in (SKILL_ROOT / "scripts" / "extract", SKILL_ROOT / "knowledge"):
    if str(_sub) not in sys.path:
        sys.path.insert(0, str(_sub))

import extract_ppa          # noqa: E402  (depends on the sys.path bootstrap above)
import fmax_model as fm     # noqa: E402

RUN_ORFS = SKILL_ROOT / "scripts" / "flow" / "run_orfs.sh"


def _config_value(config_mk: Path, key: str) -> str | None:
    if not config_mk.exists():
        return None
    m = re.search(rf"(?:export\s+)?{re.escape(key)}\s*=\s*(.*)",
                  config_mk.read_text(encoding="utf-8", errors="ignore"))
    return m.group(1).strip() if m else None


def assert_safe_knobs(project: Path) -> None:
    """Hard-rule guard: the search must never run with PLACE_DENSITY_LB_ADDON
    below 0.10 (irrecoverable placer divergence, per CLAUDE.md)."""
    v = _config_value(project / "constraints" / "config.mk", "PLACE_DENSITY_LB_ADDON")
    if v is not None:
        try:
            if float(v) < 0.10:
                raise ValueError(
                    f"PLACE_DENSITY_LB_ADDON={v} < 0.10 — refusing to run Fmax search "
                    "(placer divergence is irrecoverable). Fix config.mk first.")
        except ValueError as e:
            if "PLACE_DENSITY_LB_ADDON" in str(e):
                raise
            # non-numeric value: leave to ORFS, don't block here.


def clone_variant(base: Path, period: float) -> Path:
    """Lean clone of <base> into a sibling <base>_fmax_p<NNNN>: symlink rtl/,
    copy constraints/, rewrite the SDC clk_period. Unique basename => unique
    FLOW_VARIANT (hard-rule isolation)."""
    base = Path(base)
    name = fm.variant_name(base.name, period)
    variant = base.parent / name
    if variant.exists():
        shutil.rmtree(variant)
    (variant / "constraints").mkdir(parents=True)
    # Write the variant's rewritten SDC (tightened clk_period).
    variant_sdc = (variant / "constraints" / "constraint.sdc").resolve()
    sdc_text = (base / "constraints" / "constraint.sdc").read_text(encoding="utf-8")
    variant_sdc.write_text(fm.rewrite_clk_period(sdc_text, period), encoding="utf-8")
    # Copy config.mk but REPOINT SDC_FILE at THIS variant's rewritten SDC. The source
    # config.mk pins SDC_FILE to an ABSOLUTE path in the ORIGINAL design dir, so a
    # verbatim copy makes every probe silently reuse the ORIGINAL clock period — the
    # variant SDC is ignored, place-stage slack stays constant across periods, and the
    # search floors at the tightest period (bug surfaced 2026-06-19 by the Fmax pilot:
    # 15/20 designs reported an unphysical ~20 GHz). VERILOG_FILES etc. stay absolute
    # (RTL is period-invariant); only the clock constraint must follow the variant.
    cfg_text = (base / "constraints" / "config.mk").read_text(encoding="utf-8")
    cfg_text, n = re.subn(r"(?m)^(\s*(?:export\s+)?SDC_FILE\s*=).*$",
                          rf"\g<1> {variant_sdc}", cfg_text)
    if n == 0:   # no explicit SDC_FILE -> pin one so ORFS uses the variant's SDC
        cfg_text = cfg_text.rstrip("\n") + f"\nexport SDC_FILE = {variant_sdc}\n"
    (variant / "constraints" / "config.mk").write_text(cfg_text, encoding="utf-8")
    # Symlink rtl/ (read-only, large); fall back to copy if symlink unsupported.
    src_rtl = base / "rtl"
    if src_rtl.exists():
        try:
            (variant / "rtl").symlink_to(src_rtl.resolve(), target_is_directory=True)
        except OSError:
            shutil.copytree(src_rtl, variant / "rtl")
    return variant


def _latest_run_dir(project: Path) -> Path | None:
    backend = project / "backend"
    if not backend.is_dir():
        return None
    runs = sorted((d for d in backend.iterdir()
                   if d.is_dir() and d.name.startswith("RUN_")),
                  key=lambda d: d.stat().st_mtime, reverse=True)
    return runs[0] if runs else None


def run_probe(variant: Path, platform: str, stages: str, *,
              timeout_s: int = 3600, place_fast: bool = False,
              env: dict | None = None) -> dict:
    """Run run_orfs.sh for the variant through `stages` and read the proxy slack.
    Returns {'place_ws','place_tns','floorplan_ws','status','completed'}."""
    e = dict(os.environ if env is None else env)
    e["ORFS_STAGES"] = stages
    e["ORFS_TIMEOUT"] = str(timeout_s)
    if place_fast:
        e["PLACE_FAST"] = "1"
    proc = subprocess.run(
        ["bash", str(RUN_ORFS), str(variant), platform, variant.name],
        env=e, capture_output=True, text=True)
    completed = proc.returncode == 0
    rd = _latest_run_dir(variant)
    fp = extract_ppa.parse_stage_metrics(rd, "floorplan") if rd else {}
    pl = extract_ppa.parse_stage_metrics(rd, "place") if rd else {}
    place_ws = pl.get("setup_wns")
    place_tns = pl.get("setup_tns")
    return {
        "floorplan_ws": fp.get("setup_wns"),
        "place_ws": place_ws,
        "place_tns": place_tns,
        "completed": completed,
        "returncode": proc.returncode,
    }


def _add_paths() -> None:
    """Make sibling skill modules importable when run as a CLI."""
    for sub in (SKILL_ROOT / "scripts" / "extract", SKILL_ROOT / "knowledge"):
        if str(sub) not in sys.path:
            sys.path.insert(0, str(sub))


def _heuristics_kw() -> dict:
    """Read heuristics from R2G_HEURISTICS_PATH when set, like suggest_config,
    ingest_run, fix_log_manager and engineer_loop do (2026-11-25: the search read only
    the shipped heuristics.json, so a model re-learned into a private store -- what
    knowledge_db's write guard tells users to set up -- never reached the search)."""
    hp = os.environ.get("R2G_HEURISTICS_PATH")
    return {"heuristics_path": hp} if hp else {}


def seed_period(project: Path, platform: str, family: str | None = None) -> float:
    """Tier-0 seed: aggressive end of the family's learned closing_period if
    available, else the design's nominal SDC period."""
    try:
        _add_paths()
        import knowledge_db
        if family is None:
            cfg_name = _config_value(project / "constraints" / "config.mk", "DESIGN_NAME") or ""
            fams = knowledge_db.load_families()
            family = knowledge_db.infer_family(cfg_name, fams)
        cp = knowledge_db.get_closing_period(family, platform, **_heuristics_kw())
        if cp and cp.get("min"):
            return float(cp["min"])
    except Exception:
        pass
    # Fallback: nominal SDC period.
    sdc = (project / "constraints" / "constraint.sdc").read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"set\s+clk_period\s+([\d.]+)", sdc)
    return float(m.group(1)) if m else 10.0


# OpenSTA adopts each platform's liberty time_unit, so the SDC clk_period and every reported
# slack/period is in THAT unit: nangate45/sky130/gf180/ihp use 1ns, but asap7 uses 1ps. The Fmax
# search is unit-agnostic and SELF-CONSISTENT internally (period and slack share the unit, and
# rewrite_clk_period writes the closing period back in the same unit the flow reads) -- so the SDC
# stamping and the learned closing-period seed stay correct WITHOUT conversion. ONLY the human /
# recorded Fmax must be normalized to ns/GHz: otherwise asap7's 0.41 ns (=409.6 ps) closing period
# was reported as 0.00244 GHz instead of 2.44 GHz -- a silent 1000x under-report under a green flow
# (2026-06-30). This factor is ns-per-STA-unit: 1.0 for ns platforms (identity -> byte-identical for
# the completed nangate45 round), 0.001 for asap7 (1ps). Mirrors the ORFS liberty time_unit; extend
# this map if a future platform ships a non-ns liberty. See failure-patterns.md "ASAP7 Fmax ... ps".
_PLATFORM_TIME_UNIT_NS = {"asap7": 0.001}


def _platform_time_unit_ns(platform: str) -> float:
    """ns per the platform's STA/liberty time unit (1.0 for ns platforms; 0.001 for asap7=1ps)."""
    return _PLATFORM_TIME_UNIT_NS.get((platform or "").strip(), 1.0)


def build_labels(result: dict, model_provenance: str, place_fast: bool,
                 time_unit_ns: float = 1.0) -> list[str]:
    # t_star / t_place_proxy are in the platform's STA time unit; convert to ns for the
    # human-facing Fmax + period (1.0 = ns platforms, unchanged; 0.001 = asap7 ps). See
    # _platform_time_unit_ns.
    t_ns = result["t_star"] * time_unit_ns
    proxy_ns = result["t_place_proxy"] * time_unit_ns
    labels = [
        f"Fmax_predicted_signoff: {1.0 / t_ns:.4g} (period {t_ns:.4g} ns) [proxy, UNVERIFIED]",
        f"Fmax_place_proxy: {1.0 / proxy_ns:.4g} "
        f"(period {proxy_ns:.4g} ns)",
        "+CTS-skew-unmodeled",
        f"deterioration: {model_provenance}",
    ]
    if place_fast:
        labels.append("PLACE_FAST-lower-bound")
    return labels


def search(project: Path, platform: str, *, seed_period: float,
           floorplan_probe, place_probe, model=None,
           model_provenance: str = "default-static",
           place_fast: bool = False) -> dict:
    """Run the search loop with the given probes and write reports/fmax_search.json."""
    import json
    res = fm.search_loop(seed_period, floorplan_probe, place_probe, model=model)
    report = {
        "design": Path(project).name,
        "platform": platform,
        "seed_period": seed_period,
        "status": res["status"],
        "model_provenance": model_provenance,
        "place_fast": place_fast,
        "log": res.get("log", []),
    }
    tu = _platform_time_unit_ns(platform)   # ns per STA unit (1.0 ns-platforms, 0.001 asap7=ps)
    if res["status"] == "ok":
        report["winner"] = {
            # raw STA-unit period: what rewrite_clk_period writes to the SDC + seeds the next
            # search; kept unconverted so the flow builds at the right frequency.
            "period": res["t_star"],
            # human-facing, normalized to ns/GHz so asap7 (ps) is not under-reported 1000x.
            "period_ns": res["t_star"] * tu,
            "fmax_predicted_signoff": 1.0 / (res["t_star"] * tu),
            "fmax_place_proxy": 1.0 / (res["t_place_proxy"] * tu),
        }
        report["labels"] = build_labels(res, model_provenance, place_fast, tu)
        res["fmax_predicted_signoff_period"] = res["t_star"]
    else:
        report["labels"] = [f"status: {res['status']}"]
        if res.get("reason"):                     # surface WHY (floorplan_unconstrained, ...)
            report["reason"] = res["reason"]
    out = Path(project) / "reports" / "fmax_search.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    res["report_path"] = str(out)
    return res


def confirm_grid(t_star: float, place_probe, model=None, *, width=0.02, n=3,
                 lazy: bool = False, stats: dict | None = None) -> float:
    """Probe a small grid around t_star; return the looser (larger-period)
    passing edge (or the loosest grid point when none passes). Sequential.

    lazy=True (R2G_FMAX_LAZY_CONFIRM=1 / --lazy-confirm, 2026-11-25): the answer is
    max(passing periods), so probing the grid loosest-FIRST and stopping at the first
    pass returns the identical edge for any deterministic probe while skipping the
    tighter points — typically 1 place probe instead of n. `stats` (optional dict)
    receives {'probes': k, 'periods': [...]} for the report."""
    periods = fm.confirm_order(t_star, width=width, n=n)
    hi = periods[-1]
    probed: list[float] = []
    best_pass = None
    if lazy:
        for p in sorted(periods, reverse=True):      # loosest first
            probed.append(p)
            if place_probe(p).get("status") == "pass":
                best_pass = p
                break
    else:
        for p in sorted(periods):  # ascending = looser last
            probed.append(p)
            r = place_probe(p)
            if r.get("status") == "pass":
                best_pass = p if best_pass is None else max(best_pass, p)
    if stats is not None:
        stats.update(probes=len(probed), periods=probed, lazy=lazy)
    return best_pass if best_pass is not None else hi


def _orfs_scratch_dirs(variant: Path) -> list[Path]:
    """ORFS results/logs/objects/reports dirs a probe variant wrote, read from its
    run-meta.json `orfs_results` (<flow>/results/<platform>/<design>/<variant>)."""
    import json
    out = []
    for meta in Path(variant).glob("backend/RUN_*/run-meta.json"):
        try:
            res = Path(json.loads(meta.read_text(encoding="utf-8")).get("orfs_results") or "")
        except (OSError, ValueError):
            continue
        # Only a <flow>/results/<platform>/<design>/<this-variant> path is ours to delete.
        if res.name != Path(variant).name or len(res.parts) < 4 or res.parents[2].name != "results":
            continue
        flow = res.parents[3]
        rel = res.relative_to(flow / "results")
        out += [flow / sub / rel for sub in ("results", "logs", "objects", "reports")]
    return out


def cleanup_variants(variants: list[Path]) -> None:
    """Remove each probe variant AND the ORFS scratch it wrote: dropping only the
    project dir leaked ~1 GB/probe under ORFS flow/{results,logs,objects,reports}
    (2026-09-28/30 AIC runs on 203 filled the root fs; failure-patterns P0-2d)."""
    for v in variants:
        for d in _orfs_scratch_dirs(v):
            shutil.rmtree(d, ignore_errors=True)
        try:
            shutil.rmtree(v)
        except OSError:
            pass


def _make_real_probes(base: Path, platform: str, *, timeout_s: int,
                      place_fast: bool, created: list[Path],
                      memo: bool = False, stats: dict | None = None):
    """Build floorplan_probe/place_probe that clone a variant per period, run
    ORFS, read the proxy, and record the variant dir for cleanup.

    memo=True (with --lazy-confirm): a place probe at a period already probed in
    this search returns the recorded result instead of re-running ORFS (a
    deterministic flow gives the same answer). `stats` accumulates ORFS probe
    runs and their wall time for the report."""
    import time as _time
    cache: dict[str, dict] = {}
    if stats is not None:
        stats.setdefault("orfs_runs", 0)
        stats.setdefault("orfs_wall_s", 0.0)
        stats.setdefault("memo_hits", 0)

    def _account(t0):
        if stats is not None:
            stats["orfs_runs"] += 1
            stats["orfs_wall_s"] = round(stats["orfs_wall_s"] + _time.monotonic() - t0, 1)

    def floorplan_probe(period):
        v = clone_variant(base, period)
        created.append(v)
        t0 = _time.monotonic()
        out = run_probe(v, platform, "synth floorplan",
                        timeout_s=timeout_s, place_fast=place_fast)
        _account(t0)
        return out.get("floorplan_ws")

    def place_probe(period):
        key = f"{float(period):.6g}"
        if memo and key in cache:
            if stats is not None:
                stats["memo_hits"] += 1
            return dict(cache[key])
        v = clone_variant(base, period)
        if v not in created:
            created.append(v)
        t0 = _time.monotonic()
        out = run_probe(v, platform, "synth floorplan place",
                        timeout_s=timeout_s, place_fast=place_fast)
        _account(t0)
        status = fm.classify_probe(out.get("place_ws"), out.get("place_tns"),
                                   period, completed=out.get("completed", True))
        res = {"place_ws": out.get("place_ws"), "place_tns": out.get("place_tns"),
               "status": status}
        cache[key] = dict(res)
        return res
    return floorplan_probe, place_probe


def record_verify_triple(conn, *, design_name, design_family, platform, period,
                         floorplan_ws, place_ws, finish_ws) -> str:
    """Append a verified (floorplan, place, finish) slack triple so the
    deterioration model self-corrects. A signoff-positive row (drc/lvs clean)
    so it counts toward learning; tagged eval_arm='fmax_verify' for provenance.
    The N>=8 min-sample gate in fmax_model.select_model means one triple cannot
    move the active estimator."""
    import datetime as _dt
    rid = "fmaxverify_" + _dt.datetime.now().astimezone().strftime("%Y%m%d%H%M%S%f")
    conn.execute(
        "INSERT OR REPLACE INTO runs (run_id, project_path, design_name, "
        "design_family, platform, ingested_at, clock_period_ns, floorplan_setup_ws, "
        "place_setup_ws, finish_setup_ws, wns_ns, drc_status, lvs_status, eval_arm) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (rid, f"verify:{design_name}", design_name, design_family, platform,
         _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
         period, floorplan_ws, place_ws, finish_ws, finish_ws,
         "clean" if finish_ws is not None and finish_ws >= 0 else "fail",
         "clean", "fmax_verify"))
    conn.commit()
    return rid


def record_period_relax(project: Path, old: float, new: float, source: str) -> bool:
    """Append one post-search loosening (old -> new) to reports/fmax_search.json so the
    signoff manifest can bind a relaxed SDC period back to the search winner
    (fmax_model.resolve_confirmed_period). No-op (False) when the project never ran an
    Fmax search — a fixed-period task has no chain to extend. Atomic tmp -> rename."""
    import json
    import datetime as _dt
    rep = Path(project) / "reports" / "fmax_search.json"
    try:
        data = json.loads(rep.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if data.get("status") != "ok" or not isinstance(data.get("winner"), dict):
        return False
    ts = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    data = fm.add_relaxation(data, old, new, source, ts=ts)
    tmp = rep.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, rep)
    return True


def verify_winner(base: Path, platform: str, period: float) -> dict:
    """Run ONE full signoff flow at the winning period, read finish timing, and
    record the verified triple. Returns {'closed', 'finish_ws', ...}."""
    import time as _time
    v = clone_variant(base, period)
    _t0 = _time.monotonic()
    out = run_probe(v, platform, "synth floorplan place cts route finish",
                    timeout_s=int(os.environ.get("FMAX_VERIFY_TIMEOUT", "14400")))
    wall_s = round(_time.monotonic() - _t0, 1)
    rd = _latest_run_dir(v)
    # Read finish timing from 6_report.json. Gate on setup AND hold (spec §6).
    import json
    fin_ws = fin_tns = hold_ws = None
    if rd:
        rj = rd / "logs" / "6_report.json"
        if rj.exists():
            d = json.loads(rj.read_text(encoding="utf-8", errors="ignore"))
            fin_ws = d.get("finish__timing__setup__ws")
            fin_tns = d.get("finish__timing__setup__tns")
            hold_ws = d.get("finish__timing__hold__ws")
    # A flow that did not complete (non-zero run_orfs exit, timeout) never counts as
    # closed even if a 6_report.json with passing numbers exists (Codex review 2026-11-25).
    closed = (out.get("completed", True) and fin_ws is not None and fin_ws >= 0
              and fin_tns is not None and fin_tns >= 0
              and (hold_ws is None or hold_ws >= 0))
    _add_paths()
    import knowledge_db
    fam = knowledge_db.infer_family(
        _config_value(base / "constraints" / "config.mk", "DESIGN_NAME") or "",
        knowledge_db.load_families())
    # The verified triple is a by-product for the deterioration model; failing to
    # store it (e.g. knowledge_db's guard refusing writes to the shipped, git-tracked
    # store when R2G_KNOWLEDGE_DB is unset) must not throw away the full flow that was
    # just paid for -- before 2026-11-25 it crashed here and the verify outcome was lost.
    import sqlite3 as _sqlite3
    try:
        conn = knowledge_db.connect()
        try:
            knowledge_db.ensure_schema(conn)
            record_verify_triple(conn, design_name=base.name, design_family=fam,
                                 platform=platform, period=period,
                                 floorplan_ws=out.get("floorplan_ws"),
                                 place_ws=out.get("place_ws"), finish_ws=fin_ws)
        finally:
            conn.close()
    except _sqlite3.Error as exc:
        print(f"WARNING: verify triple not recorded in the knowledge store ({exc})",
              file=sys.stderr)
    print(f"Verify @ {period:.4g} ns: finish_ws={fin_ws} -> "
          f"{'CONFIRMED' if closed else 'MISS (back off one notch)'}")
    return {"closed": closed, "finish_ws": fin_ws, "finish_tns": fin_tns,
            "hold_ws": hold_ws, "period": period, "variant": v, "wall_s": wall_s}


def _env_flag(name: str, default: bool = False) -> bool:
    v = os.environ.get(name, "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


def _update_report(project: Path, mutate) -> None:
    """Apply `mutate(dict)` to reports/fmax_search.json in place (atomic tmp -> rename)."""
    import json
    rep = Path(project) / "reports" / "fmax_search.json"
    try:
        data = json.loads(rep.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    mutate(data)
    tmp = rep.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, rep)


def _verify_entry(v: dict) -> dict:
    return {k: v.get(k) for k in ("period", "closed", "finish_ws", "finish_tns", "hold_ws",
                                  "wall_s")}


def _record_verify(base: Path, platform: str, v: dict, *, ecp_refine: bool,
                   margin: float, verify_fn=None) -> dict:
    """Write the --verify outcome into the report and, with ecp_refine, run ONE more
    full flow at the ECP-corrected period (fmax_model.ecp_refine_period).

    With ecp_refine the winner becomes the tightest period a FULL flow verified as
    closed (label 'verified'); the proxy winner is kept as `proxy_winner`. Without it
    only an additive `verify` block is written (winner unchanged = legacy behavior)."""
    verify_fn = verify_fn or verify_winner
    tries = [_verify_entry(v)]
    best = v["period"] if v.get("closed") else None
    best_src = "verify_confirm_edge" if best is not None else None
    if ecp_refine:
        t2 = fm.ecp_refine_period(v["period"], v.get("finish_ws"), closed=bool(v.get("closed")),
                                  hold_ws=v.get("hold_ws"), margin=margin)
        if t2 is not None:
            print(f"ECP refine: {v['period']:.4g} -> {t2:.4g} "
                  f"(slack {v.get('finish_ws')}, margin {margin:g})")
            v2 = verify_fn(base, platform, t2)
            tries.append(_verify_entry(v2))
            if v2.get("closed") and (best is None or t2 < best):
                best, best_src = t2, "ecp_refine_verified"
    tu = _platform_time_unit_ns(platform)

    def _mut(d):
        d["verify"] = {"tries": tries, "ecp_refine": ecp_refine, "ecp_margin": margin,
                       "verified_period": best}
        if ecp_refine and best is not None and d.get("status") == "ok":
            d.setdefault("proxy_winner", d.get("winner"))
            d["winner"] = {"period": best, "period_ns": best * tu,
                           "fmax_verified": 1.0 / (best * tu),
                           "fmax_predicted_signoff": 1.0 / (best * tu),
                           "fmax_place_proxy": (d.get("proxy_winner") or {}).get("fmax_place_proxy"),
                           # which full flow won: the ECP try, or the confirm-edge verify
                           # when the tighter ECP try missed (Codex review 2026-11-25)
                           "source": best_src}
            how = "ECP-refined" if best_src == "ecp_refine_verified" else "confirm edge; ECP try missed"
            d["labels"] = [f"Fmax_verified: {1.0 / (best * tu):.4g} (period {best * tu:.4g} ns) "
                           f"[full-flow verified, {how}]"] + list(d.get("labels") or [])
        elif ecp_refine and best is None and isinstance(d.get("winner"), dict):
            # No full flow closed: the winner stays the UNVERIFIED proxy, said explicitly.
            d["winner"]["source"] = "proxy_unverified"
            d["labels"] = list(d.get("labels") or []) + [
                f"verify: no full flow closed ({len(tries)} tries) - winner is the "
                "unverified proxy"]
    _update_report(base, _mut)
    return {"tries": tries, "verified_period": best}


def main() -> int:
    _add_paths()
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("project", type=Path)
    p.add_argument("platform", nargs="?", default="sky130hd")
    p.add_argument("--verify", action="store_true",
                   help="Run one full signoff flow at the winning period.")
    p.add_argument("--keep-variants", action="store_true")
    p.add_argument("--place-fast", action="store_true",
                   help="Whole-search PLACE_FAST mode (conservative lower bound).")
    p.add_argument("--probe-timeout", type=int, default=3600)
    p.add_argument("--record-relax", nargs=3, metavar=("OLD", "NEW", "SOURCE"),
                   help="append a post-search period loosening to fmax_search.json and exit")
    # Default ON since the 2026-11-25 A/B (28/28 nangate45 designs: identical t* and
    # edge, -29 % search ORFS wall); --eager-confirm / R2G_FMAX_LAZY_CONFIRM=0 restores
    # the legacy 3-probe grid.
    p.add_argument("--lazy-confirm", dest="lazy_confirm", action="store_true",
                   default=_env_flag("R2G_FMAX_LAZY_CONFIRM", default=True),
                   help="Probe the confirm grid loosest-first and stop at the first pass "
                        "(same edge, fewer ORFS probes); also memoizes repeated periods. "
                        "Default on; env R2G_FMAX_LAZY_CONFIRM=0 to disable.")
    p.add_argument("--eager-confirm", dest="lazy_confirm", action="store_false",
                   help="Legacy confirm grid: probe all three points.")
    p.add_argument("--ecp-refine", action="store_true",
                   default=_env_flag("R2G_FMAX_ECP_REFINE"),
                   help="With --verify: run ONE more full flow at the effective clock "
                        "period (period - verified slack, +margin) and make the tightest "
                        "VERIFIED-closed period the winner. Env: R2G_FMAX_ECP_REFINE=1.")
    p.add_argument("--ecp-margin", type=float,
                   default=float(os.environ.get("R2G_FMAX_ECP_MARGIN", "0.02")),
                   help="Fractional guard band added to the ECP target (default 0.02).")
    args = p.parse_args()

    if args.record_relax:
        old, new, source = args.record_relax
        ok = record_period_relax(args.project, float(old), float(new), source)
        print(f"fmax relax {old} -> {new} ({source}): "
              f"{'recorded' if ok else 'skipped (no ok Fmax search report)'}")
        return 0

    base = args.project.resolve()
    assert_safe_knobs(base)

    import knowledge_db
    fam = knowledge_db.infer_family(
        _config_value(base / "constraints" / "config.mk", "DESIGN_NAME") or "",
        knowledge_db.load_families())
    model, provenance = fm.select_model(
        knowledge_db.get_family_heuristics(fam, args.platform, **_heuristics_kw()),
        platform_entry=knowledge_db.get_platform_heuristics(args.platform, **_heuristics_kw()))
    seed = seed_period(base, args.platform, family=fam)

    created: list[Path] = []
    probe_stats: dict = {}
    fp_probe, pl_probe = _make_real_probes(
        base, args.platform, timeout_s=args.probe_timeout,
        place_fast=args.place_fast, created=created,
        memo=args.lazy_confirm, stats=probe_stats)
    try:
        res = search(base, args.platform, seed_period=seed,
                     floorplan_probe=fp_probe, place_probe=pl_probe,
                     model=model, model_provenance=provenance, place_fast=args.place_fast)
    except ValueError as exc:
        if "no 'set clk_period' line found" in str(exc):
            import json
            out = base / "reports" / "fmax_search.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({
                "design": base.name,
                "platform": args.platform,
                "status": "no_clock_constraint",
                "labels": ["status: no_clock_constraint"],
            }, indent=2), encoding="utf-8")
            print("Fmax search status: no_clock_constraint — SDC has no 'set clk_period' line.")
            return 0
        raise

    if res["status"] == "ok":
        search_stats = dict(probe_stats)          # probes spent before the confirm grid
        cstats: dict = {}
        edge = confirm_grid(res["t_star"], pl_probe, model=model,
                            lazy=args.lazy_confirm, stats=cstats)
        print(f"Fmax (predicted-signoff) ~ {1.0 / edge:.4g}  (period {edge:.4g} ns)  [{provenance}]")
        # Additive telemetry: what the confirm grid decided and what the search cost.
        _update_report(base, lambda d: d.update(
            confirm={"edge": edge, **cstats},
            probe_stats={**probe_stats,
                         "search_orfs_runs": search_stats.get("orfs_runs", 0),
                         "search_orfs_wall_s": search_stats.get("orfs_wall_s", 0.0)}))
        if args.verify:
            print("Running full signoff verify at the winning period…")
            v = verify_winner(base, args.platform, edge)  # Task 12
            _record_verify(base, args.platform, v, ecp_refine=args.ecp_refine,
                           margin=args.ecp_margin)
    else:
        _update_report(base, lambda d: d.update(probe_stats=dict(probe_stats)))
        print(f"Fmax search status: {res['status']} — see reports/fmax_search.json")

    if not args.keep_variants:
        cleanup_variants(created)
    return 0 if res["status"] in ("ok",) else 1


if __name__ == "__main__":
    main()
