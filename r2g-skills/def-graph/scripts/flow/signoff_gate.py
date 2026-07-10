#!/usr/bin/env python3
"""Signoff gate for dataset construction: a 6_final.def alone is NOT sign-off.

DRC/LVS run in a separate post-finish step, route/antenna residuals survive a
"completed" flow, and an aborted ORFS can leave a plausible DEF behind — so the
dataset stages must not build just because the DEF exists (failure-patterns.md
"Dataset-Extraction Silent-Value Defects" #34). This gate reads the project's
signoff artifacts and decides whether a dataset may be built from this run:

  required (block in enforce mode when dirty OR unverifiable — fail-closed):
    reports/drc.json                status in {clean, clean_beol}
    reports/lvs.json                status in {clean, skipped}
    <run_dir>/stage_log.jsonl       'finish' stage recorded with status 0
                                    (fallback: run-meta.json make_status == 0)
    reports/route.json | <run_dir>/**/5_route_drc.rpt
                                    residual route/antenna violations == 0
                                    (unknown = caveat, not a block: a clean full
                                    DRC deck already covers routed geometry)
  advisory (recorded, never blocks — negative slack is a valid training label):
    reports/ppa.json summary.timing.setup_wns | reports/timing_check.json tier

Always writes reports/signoff_gate.json (atomic tmp+rename); build_graphs.py
embeds it in graph_manifest.json as `signoff_health`. Exit code:
  0  proceed  (verdict pass/pass_with_caveats, or mode warn/off)
  3  blocked  (mode enforce and a required check failed)

Fail-closed on MISSING drc/lvs reports in enforce mode: the verifier's old
vacuous pass (no report -> no check -> "clean") is the exact trap this replaces.
Overrides: R2G_SIGNOFF_GATE=warn builds anyway with the reasons recorded;
--def-overridden (R2G_DEF/R2G_ODB set) downgrades to warn — an explicit operator
override is a deliberate, recorded decision, e.g. the no-backend verifier flows.
"""
import argparse
import glob
import json
import os
import sys

# Statuses the signoff step itself treats as acceptable (fix_signoff.sh's
# clean_states) — but the gate is stricter: `skipped` is acceptable only for
# LVS (portless designs / platforms without a deck record an EXPLICIT skip),
# never for DRC, and a MISSING report is not a skip.
DRC_OK = {"clean", "clean_beol"}
LVS_OK = {"clean", "skipped"}
PROCEED = {"pass", "pass_with_caveats"}


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _check_drc(reports_dir):
    j = _load_json(os.path.join(reports_dir, "drc.json"))
    if j is None:
        return {"status": "missing", "detail": "reports/drc.json not found — DRC never ran (or ran elsewhere)"}
    st = str(j.get("status", "unknown"))
    out = {"status": st, "violations": j.get("total_violations")}
    if st not in DRC_OK:
        out["detail"] = f"drc status={st!r} violations={j.get('total_violations')}"
    elif st == "clean_beol":
        out["detail"] = "BEOL-only DRC: metal clean, FEOL/antenna not covered"
    return out


def _check_lvs(reports_dir):
    j = _load_json(os.path.join(reports_dir, "lvs.json"))
    if j is None:
        return {"status": "missing", "detail": "reports/lvs.json not found — LVS never ran (or ran elsewhere)"}
    st = str(j.get("status", "unknown"))
    out = {"status": st, "mismatch_count": j.get("mismatch_count")}
    if st not in LVS_OK:
        out["detail"] = f"lvs status={st!r} mismatch_count={j.get('mismatch_count')}"
    elif st == "skipped":
        out["detail"] = "LVS explicitly skipped by the signoff step (portless design / no deck)"
    return out


def _check_orfs(run_dir):
    """ORFS completion from the run the DEF came from: stage_log.jsonl is the
    authoritative record (one JSON line per stage, written by run_orfs.sh);
    run-meta.json make_status is the coarser fallback."""
    if not run_dir:
        return {"status": "unknown", "detail": "no backend run dir (DEF overridden or externally collected)"}
    slog = os.path.join(run_dir, "stage_log.jsonl")
    if os.path.isfile(slog):
        stages = {}
        try:
            with open(slog, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    stages[str(rec.get("stage", ""))] = rec.get("status")
        except OSError:
            stages = {}
        bad = {s: st for s, st in stages.items() if st not in (0, "0")}
        if bad:
            return {"status": "fail", "detail": f"stage(s) failed: {bad}", "stages": stages}
        if stages.get("finish") in (0, "0"):
            return {"status": "complete", "stages": stages}
        if stages:
            return {"status": "incomplete",
                    "detail": f"no clean 'finish' stage in stage_log.jsonl (saw: {sorted(stages)})",
                    "stages": stages}
    meta = _load_json(os.path.join(run_dir, "run-meta.json"))
    if meta is not None and "make_status" in meta:
        ms = meta.get("make_status")
        if ms in (0, "0"):
            return {"status": "complete", "detail": "run-meta.json make_status=0 (no stage_log.jsonl)"}
        return {"status": "fail", "detail": f"run-meta.json make_status={ms}"}
    return {"status": "unknown",
            "detail": f"no stage_log.jsonl / run-meta.json make_status under {run_dir}"}


def _check_route(reports_dir, run_dir):
    """Residual route/antenna violations. Prefer the extracted reports/route.json;
    fall back to counting markers in the run's 5_route_drc.rpt. Unknown when
    neither exists — recorded as a caveat, not a block (a clean full DRC deck
    already covers routed geometry)."""
    j = _load_json(os.path.join(reports_dir, "route.json"))
    if j is not None:
        tv = j.get("total_violations")
        st = str(j.get("status", "unknown"))
        if st == "clean" or tv == 0:
            return {"status": "clean", "violations": 0}
        return {"status": "dirty", "violations": tv,
                "detail": f"route.json status={st!r} total_violations={tv}"}
    if run_dir:
        rpts = sorted(glob.glob(os.path.join(run_dir, "**", "5_route_drc.rpt"),
                                recursive=True))
        if rpts:
            try:
                with open(rpts[-1], errors="replace", encoding="utf-8") as f:
                    n = sum(1 for line in f if "violation type" in line.lower())
            except OSError:
                n = -1
            if n == 0:
                return {"status": "clean", "violations": 0, "source": rpts[-1]}
            return {"status": "dirty", "violations": n,
                    "detail": f"{n} residual marker(s) in {rpts[-1]}", "source": rpts[-1]}
    return {"status": "unknown",
            "detail": "no reports/route.json and no 5_route_drc.rpt in the run dir"}


def _check_timing(reports_dir):
    """Advisory only: negative slack is a legitimate training label, so timing is
    recorded for downstream filtering, never a block."""
    ppa = _load_json(os.path.join(reports_dir, "ppa.json"))
    if ppa is not None:
        wns = ((ppa.get("summary") or {}).get("timing") or {}).get("setup_wns")
        if wns is not None:
            try:
                met = float(wns) >= 0.0
            except (TypeError, ValueError):
                met = None
            return {"status": ("met" if met else "violated") if met is not None else "unknown",
                    "setup_wns": wns, "source": "ppa.json"}
    tc = _load_json(os.path.join(reports_dir, "timing_check.json"))
    if tc is not None and tc.get("tier"):
        tier = str(tc["tier"])
        return {"status": "met" if tier in ("clean", "minor") else "violated",
                "tier": tier, "source": "timing_check.json"}
    return {"status": "unknown", "detail": "no reports/ppa.json timing or timing_check.json"}


def evaluate(project_dir, run_dir):
    reports_dir = os.path.join(project_dir, "reports")
    checks = {
        "drc": _check_drc(reports_dir),
        "lvs": _check_lvs(reports_dir),
        "orfs": _check_orfs(run_dir),
        "route": _check_route(reports_dir, run_dir),
        "timing": _check_timing(reports_dir),
    }
    blockers = []
    if checks["drc"]["status"] not in DRC_OK:
        blockers.append("drc")
    if checks["lvs"]["status"] not in LVS_OK:
        blockers.append("lvs")
    if checks["orfs"]["status"] not in ("complete",):
        blockers.append("orfs")
    if checks["route"]["status"] == "dirty":
        blockers.append("route")

    caveats = []
    if checks["drc"]["status"] == "clean_beol":
        caveats.append("drc=clean_beol")
    if checks["lvs"]["status"] == "skipped":
        caveats.append("lvs=skipped")
    if checks["route"]["status"] == "unknown":
        caveats.append("route=unknown")
    if checks["timing"]["status"] != "met":
        caveats.append(f"timing={checks['timing']['status']}")

    status = "dirty" if blockers else ("pass_with_caveats" if caveats else "pass")
    return {"status": status, "blockers": blockers, "caveats": caveats, "checks": checks}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("project_dir")
    ap.add_argument("--run-dir", default="", help="backend RUN_* dir the DEF came from")
    ap.add_argument("--mode", default="enforce", choices=("enforce", "warn", "off"))
    ap.add_argument("--def-overridden", action="store_true",
                    help="R2G_DEF/R2G_ODB set: downgrade enforce to warn (deliberate operator override)")
    args = ap.parse_args()

    mode = args.mode
    if args.def_overridden and mode == "enforce":
        mode = "warn"

    if mode == "off":
        verdict = {"status": "gate_off", "mode": "off"}
    else:
        verdict = evaluate(args.project_dir, args.run_dir)
        verdict["mode"] = mode
        if args.def_overridden:
            verdict["def_overridden"] = True

    reports_dir = os.path.join(args.project_dir, "reports")
    os.makedirs(reports_dir, exist_ok=True)
    out = os.path.join(reports_dir, "signoff_gate.json")
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(verdict, f, indent=1)
    os.replace(tmp, out)

    if verdict["status"] in PROCEED:
        note = f" (caveats: {', '.join(verdict['caveats'])})" if verdict.get("caveats") else ""
        print(f"signoff gate: {verdict['status']}{note}", file=sys.stderr)
        return 0
    if verdict["status"] == "gate_off":
        print("signoff gate: OFF (R2G_SIGNOFF_GATE=off) — provenance unrecorded", file=sys.stderr)
        return 0
    detail = "; ".join(
        f"{k}: {verdict['checks'][k].get('detail', verdict['checks'][k]['status'])}"
        for k in verdict["blockers"])
    print(f"signoff gate: NOT SIGNED OFF — {detail}", file=sys.stderr)
    print(f"  verdict recorded in {out}", file=sys.stderr)
    if mode == "enforce":
        return 3
    print("  proceeding anyway (mode=warn) — the manifest will carry signoff_health="
          f"{verdict['status']!r}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
