#!/usr/bin/env python3
"""LLM + R2G arm, stage 2: hand R2G's unfinished designs to the LLM.

R2G runs first (measure_agent_repair_cost.py, one job per design). For each
design this script reads R2G's verdict and, unless R2G reached dual-clean,
queues an LLM fallback that starts from R2G's final project
(run_bare.py --from-project) with R2G's diagnosis as context.

R2G writes no explicit "no recipe" flag, so the stop is classified from
reports/fix_log.jsonl:
  no_recipe      no repair rows (synth/flow failure), or only strategy "none"
                 rows with a stop_* verdict;
  recipe_failed  at least one real strategy id was tried and the final
                 signoff is still not dual-clean.
Context per design (r2g_context/ in the fallback project): the diagnosis plan
of diagnose_signoff_fix.py --list for DRC and LVS, plus R2G's last drc.json,
lvs.json, fix_log.jsonl and repair.log.

  python3 hybrid.py --r2g-root COHORT --cases CASES.json --out QUEUE.json --repo RUNTIME
Writes QUEUE.json (cases for launch_bare.py, each with from_project/context/
reason) and r2g_outcomes.json (every design: r2g_pass / no_recipe /
recipe_failed / no_project).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def r2g_outcome(case_dir: Path) -> dict:
    """R2G's verdict for one design of an R2G cost-measurement cohort."""
    s = case_dir / "summary.json"
    if not s.is_file():
        return {"outcome": "no_summary"}
    designs = json.loads(s.read_text()).get("designs") or []
    if not designs:
        return {"outcome": "no_summary"}
    d = designs[0]
    proj = Path(d["project"]) if d.get("project") else None
    if (d.get("signoff") or {}).get("dual_clean"):
        return {"outcome": "r2g_pass", "project": str(proj) if proj else None}
    if not proj or not proj.is_dir():
        return {"outcome": "no_project", "stage": d.get("stage")}
    return {"outcome": classify(proj), "project": str(proj), "stage": d.get("stage")}


def classify(proj: Path) -> str:
    log = proj / "reports" / "fix_log.jsonl"
    rows = []
    if log.is_file():
        for line in log.read_text(errors="replace").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    tried = [r for r in rows if r.get("strategy") not in (None, "", "none")]
    return "recipe_failed" if tried else "no_recipe"


def diagnosis(proj: Path, platform: str, repo: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    script = repo / "r2g-skills/signoff-loop/scripts/reports/diagnose_signoff_fix.py"
    for check in ("drc", "lvs"):
        if not (proj / "reports" / ("%s.json" % check)).is_file():
            continue
        p = subprocess.run([sys.executable, str(script), str(proj), "--check", check, "--list"],
                           capture_output=True, text=True, timeout=600)
        out = p.stdout.strip()
        try:
            json.loads(out)
        except ValueError:
            out = json.dumps({"error": "diagnosis failed", "rc": p.returncode, "stderr": p.stderr[-2000:]})
        (dest / ("diagnosis_%s.json" % check)).write_text(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--r2g-root", type=Path, required=True, help="R2G cohort dir with cases/<id>/summary.json")
    ap.add_argument("--cases", type=Path, required=True, help="population entries (JSON list)")
    ap.add_argument("--out", type=Path, required=True, help="fallback queue for launch_bare.py")
    ap.add_argument("--repo", type=Path, required=True, help="frozen runtime source root")
    a = ap.parse_args()
    cases = json.loads(a.cases.read_text())
    queue, outcomes = [], {}
    for c in cases:
        cd = a.r2g_root / "cases" / c["id"]
        o = r2g_outcome(cd)
        outcomes[c["id"]] = o
        if o["outcome"] in ("no_recipe", "recipe_failed"):
            ctx = a.out.parent / "r2g_context" / c["id"]
            diagnosis(Path(o["project"]), c["platform"], a.repo, ctx)
            queue.append({**c, "from_project": o["project"], "context": str(ctx),
                          "reason": "%s (R2G stopped at %s)" % (o["outcome"], o.get("stage") or "signoff")})
    a.out.write_text(json.dumps(queue, indent=1, ensure_ascii=False))
    (a.out.parent / "r2g_outcomes.json").write_text(json.dumps(outcomes, indent=1))
    from collections import Counter
    print(json.dumps(Counter(o["outcome"] for o in outcomes.values())), "fallback queue", len(queue))


if __name__ == "__main__":
    main()
