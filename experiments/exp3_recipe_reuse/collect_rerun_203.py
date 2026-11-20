#!/usr/bin/env python3
"""Run ON 203, read-only: per design of the recipe-reuse re-run, the learned recipe(s) R2G applied (from its own
reports/fix_log.jsonl) and their verdicts. Joined with collect_r2g.py's dual_clean verdict on 208.
  collect_rerun_203.py N > rerun_recipes.json"""
import glob, json, os, sys
N = sys.argv[1]; out = {}
for plat in ("nangate45", "sky130hd", "gf180"):
    for case in sorted(glob.glob(f"{N}/{plat}/measure/cases/*")):
        cid = os.path.basename(case)
        jobs = [os.path.basename(f)[:-len(".job.json")] for f in glob.glob(case + "/*.job.json")]
        rows = []
        for j in jobs:
            for fl in glob.glob(f"{N}/{plat}/runtime/workspace/{j}/*/reports/fix_log.jsonl"):
                for line in open(fl):
                    try: x = json.loads(line)
                    except ValueError: continue
                    if str(x.get("strategy", "")).startswith("learned:"):
                        rows.append({"recipe": x["strategy"][len("learned:"):], "check": x.get("check"),
                                     "verdict": x.get("verdict"), "before": x.get("before"), "after": x.get("after")})
        st = json.load(open(case + "/state.json")) if os.path.exists(case + "/state.json") else {}
        out[cid] = {"jobs": jobs, "state": st.get("status") or st.get("state"), "learned": rows}
json.dump(out, sys.stdout, indent=1)
