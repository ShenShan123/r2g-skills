#!/usr/bin/env python3
"""Zero-cost planning check for the small DEV: what would each arm try first, in order?

Runs the repair planner (diagnose_signoff_fix --list) on a COPY of a recorded failure state, once
per arm, with exactly the environment the DEV worker gives that arm. No flow, no model call, no
write to the original project, the stores or the policy files.

  NB      memory off
  RECIPE  R2G_RECIPE_FILE = the frozen recipe library
  TM0     TEHM store snapshot (read-only), no TEHM-v2 policy
  TM0-v2  the same store + a TEHM-v2 policy file (full, or one mechanism off)

  python3 t2_dev3_plan_check.py --project <failed project> --check route --recipe <RECIPE.json> \
      --store <TEHM store dir> --policy full=<f.json> no_typed_matching=<f.json> ... --out <report.json>
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DIAGNOSE = REPO / "r2g-skills/signoff-loop/scripts/reports/diagnose_signoff_fix.py"
CLEAN_PREFIXES = ("R2G_", "TEHM_", "DEEPSEEK_", "OPENAI_", "ANTHROPIC_", "ORFS_")


def base_env(work: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(CLEAN_PREFIXES) and "API_KEY" not in k
           and k not in ("PYTHONPATH", "FROM_STAGE")}
    skill = REPO / "r2g-skills/signoff-loop"
    env.update(R2G_MEMORY_READ_ONLY_EVAL="1", R2G_KNOWLEDGE_DB=str(skill / "knowledge/knowledge.sqlite"),
               R2G_LEGACY_KNOWLEDGE_DIR=str(skill / "knowledge"), R2G_JOURNAL_DB=str(work / "journal.sqlite"),
               PYTHONPATH=str(REPO / "memory") + ":" + str(REPO / "memory/scripts"), PYTHONDONTWRITEBYTECODE="1",
               CUDA_VISIBLE_DEVICES="", R2G_TEHM_ROOT=str(REPO / "memory"), TEHM_EVIDENCE_CALIBER="main",
               TEHM_D1_REQUIRE_VERIFIED_POSITIVE="1")
    return env


def plan(project: Path, check: str, env: dict) -> dict:
    p = subprocess.run([sys.executable, str(DIAGNOSE), str(project), "--check", check, "--list"],
                       env=env, capture_output=True, text=True, timeout=600)
    try:
        out = json.loads(p.stdout)
    except ValueError:
        return {"error": (p.stderr or p.stdout)[-600:]}
    return {"memory_backend": out.get("memory_backend"),
            "strategies": [{"id": s.get("id"), "source": s.get("source"), "edits": s.get("config_edits"),
                            "rule": s.get("rule_id")} for s in out.get("strategies") or []],
            "tehm_v2": out.get("tehm_v2"), "technical_failures": out.get("technical_failures"),
            "stderr_tail": [l for l in p.stderr.splitlines() if "TEHM-v2" in l or "INVALID" in l][-6:]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", type=Path, required=True)
    ap.add_argument("--check", default="route")
    ap.add_argument("--recipe", type=Path, required=True)
    ap.add_argument("--store", type=Path, required=True)
    ap.add_argument("--policy", nargs="+", default=[], help="name=path ...")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    if a.out.exists():
        raise SystemExit("refusing to overwrite " + str(a.out))
    result = {"project": str(a.project), "check": a.check, "arms": {}, "model_requests": 0, "eda_flows": 0}
    with tempfile.TemporaryDirectory(prefix="dev3-plan-") as tmp:
        work = Path(tmp)
        proj = work / "project"
        shutil.copytree(a.project, proj, symlinks=True)
        # a route abort needs the route report the loop writes; produce it exactly as the loop does
        env0 = base_env(work)
        subprocess.run([sys.executable, str(REPO / "r2g-skills/signoff-loop/scripts/extract/extract_route.py"),
                        str(proj), str(proj / "reports/route.json")], env=env0, capture_output=True, timeout=300)
        result["arms"]["NB"] = plan(proj, a.check, {**env0, "R2G_MEMORY_BACKEND": "none"})
        result["arms"]["RECIPE"] = plan(proj, a.check, {**env0, "R2G_MEMORY_BACKEND": "none",
                                                          "R2G_RECIPE_FILE": str(a.recipe)})
        snap = work / "store"
        sys.path.insert(0, str(REPO / "memory"))
        from tehm.snapshot import bind_snapshot
        bind_snapshot(a.store, snap)
        tenv = {**env0, "R2G_MEMORY_BACKEND": "tehm", "TEHM_DB": str(snap / "tehm.sqlite"),
                "TEHM_ARTIFACTS_ROOT": str(snap / "artifacts")}
        result["arms"]["TM0"] = plan(proj, a.check, tenv)
        for item in a.policy:
            name, path = item.split("=", 1)
            result["arms"]["TM0-v2:" + name] = plan(proj, a.check, {**tenv, "R2G_TEHM_V2_POLICY": path})
    a.out.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    for arm, r in result["arms"].items():
        order = [f"{s['id']}{s['edits']}" for s in r.get("strategies", [])] if "error" not in r else r["error"]
        print(f"{arm:32s} {order}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
