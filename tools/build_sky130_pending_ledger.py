#!/usr/bin/env python3
"""Build the authoritative 'pending complete-sky130-signoff' campaign ledger.

Source of truth = knowledge.sqlite (on-disk reports/GDS are cleaned to save space).
A design is a campaign CANDIDATE iff:
  * its latest nangate45 run passed ORFS (it is a real, buildable design), AND
  * it has NO sky130hd run that is fully signed off (orfs pass + drc clean + lvs clean).

Each candidate is classified by its best (most-progressed) sky130hd attempt state:
  no_signoff | orfs_fail_<stage> | drc_fail | lvs_<x> | never_run

Writes JSONL (one row per candidate) to the path given as argv[1].
"""
import sqlite3, json, os, sys, glob, re

# Leftover A/B arm-copy dirs (<design>_abA_<strat>_<r>) are transient campaign
# scratch, never real designs to "complete". Exclude them from the pending set.
ARM_DIR = re.compile(r"_ab[AB]_")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "r2g-rtl2gds/knowledge/knowledge.sqlite")
FLOW = "/proj/workarea/user5/OpenROAD-flow-scripts/flow"
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "design_cases/_batch/sky130_campaign_20260617.jsonl")

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row

def latest_per_project(platform):
    rows = con.execute(
        "SELECT * FROM runs WHERE platform=? ORDER BY ingested_at ASC", (platform,)
    ).fetchall()
    by_proj = {}
    for r in rows:  # ascending -> last write wins = latest
        by_proj[r["project_path"]] = r
    return by_proj

n_latest = latest_per_project("nangate45")
s_latest = latest_per_project("sky130hd")

# nangate45-clean design_names (the buildable universe)
nan_pass = {r["design_name"] for r in n_latest.values() if r["orfs_status"] == "pass"}

# sky130hd: group latest-project rows by design_name
sky_by_design = {}
for r in s_latest.values():
    sky_by_design.setdefault(r["design_name"], []).append(r)

def is_complete(r):
    return r["orfs_status"] == "pass" and r["drc_status"] == "clean" and r["lvs_status"] == "clean"

sky_complete = {d for d, rs in sky_by_design.items() if any(is_complete(r) for r in rs)}

def state_of(r):
    if r["orfs_status"] == "fail":
        return "orfs_fail_" + (r["orfs_fail_stage"] or "unknown")
    if r["drc_status"] == "fail":
        return "drc_fail"
    if r["lvs_status"] in ("fail", "mismatch"):
        return "lvs_" + r["lvs_status"]
    if not r["drc_status"]:
        return "no_signoff"
    return "other"

# rank: which attempt is "best" / most informative to resume
STATE_RANK = {"no_signoff": 0, "drc_fail": 1, "lvs_mismatch": 1, "lvs_fail": 1}

candidates = []
for d in sorted(nan_pass):
    if d in sky_complete:
        continue
    attempts = [r for r in sky_by_design.get(d, []) if not ARM_DIR.search(r["project_path"] or "")]
    if attempts:
        # pick the attempt whose project dir still exists on disk, else first
        ranked = sorted(attempts, key=lambda r: (not os.path.isdir(r["project_path"]),
                                                  STATE_RANK.get(state_of(r), 9)))
        best = ranked[0]
        st = state_of(best)
        proj = best["project_path"]
        on_disk = os.path.isdir(proj)
        # ORFS preserved results?
        rec = {
            "design_name": d, "state": st, "project_path": proj,
            "on_disk": on_disk, "platform": "sky130hd",
            "orfs_status": best["orfs_status"], "orfs_fail_stage": best["orfs_fail_stage"],
            "drc_status": best["drc_status"], "drc_violations": best["drc_violations"],
            "lvs_status": best["lvs_status"], "core_utilization": best["core_utilization"],
            "design_class": best["design_class"],
        }
    else:
        # never attempted on sky130hd -> source from nangate45 project
        nproj = next((r["project_path"] for r in n_latest.values()
                      if r["design_name"] == d and r["orfs_status"] == "pass"), None)
        rec = {
            "design_name": d, "state": "never_run", "project_path": nproj,
            "on_disk": os.path.isdir(nproj) if nproj else False, "platform": "sky130hd",
            "nangate_source": nproj, "design_class": None,
        }
    candidates.append(rec)

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w") as f:
    for c in candidates:
        f.write(json.dumps(c) + "\n")

# summary
from collections import Counter
by_state = Counter(c["state"] for c in candidates)
by_disk = Counter(("on_disk" if c["on_disk"] else "missing_dir") for c in candidates)
print(f"candidates (nangate-clean, sky130 NOT complete): {len(candidates)}")
print("by state:")
for k, v in by_state.most_common():
    print(f"  {v:4d}  {k}")
print("on-disk project dir:")
for k, v in by_disk.most_common():
    print(f"  {v:4d}  {k}")
print(f"\nledger written: {OUT}")
