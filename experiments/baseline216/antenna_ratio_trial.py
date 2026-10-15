#!/usr/bin/env python3
"""Stage the antenna-ratio trial: the designs the existing repair cannot reach,
plus clean controls that must not regress.

The subjects are the `recipe_no_op` failures -- designs where KLayout reports
antenna violations, OpenROAD reports antenna__violating__nets=0 and inserts no
diode, and `antenna_diode_repair` therefore asks OpenROAD to fix something it
cannot see. The controls are designs that already sign off clean, because
tightening a platform-wide ratio can only be adopted if it does not manufacture
diodes (or new violations) where none were needed.

Both arms flow from the design's own pristine config; the only difference is a
tech LEF whose ANTENNAAREARATIO reads 200 instead of 300, bind-mounted over the
container's copy.
"""
import argparse, glob, json, os, re, shutil, sys

D = "/proj/workarea/yangao"


def stage(src, dst_root):
    dst = os.path.join(dst_root, os.path.basename(src))
    os.makedirs(os.path.join(dst, "constraints"), exist_ok=True)
    for f in ("config.mk", "constraint.sdc"):
        p = os.path.join(src, "constraints", f)
        if os.path.isfile(p):
            shutil.copy2(p, os.path.join(dst, "constraints", f))
    cp = os.path.join(dst, "constraints", "config.mk")
    t = open(cp, errors="ignore").read()
    # drop any auto-applied fix block: the trial must start from the human baseline
    out, skip = [], False
    for ln in t.splitlines():
        s = ln.strip()
        if s.startswith("# >>> r2g signoff-fix"):
            skip = True
            continue
        if s.startswith("# <<< r2g signoff-fix"):
            skip = False
            continue
        if not skip:
            out.append(ln)
    t = "\n".join(out) + "\n"
    t = re.sub(r"(?m)^(\s*(?:override\s+)?export\s+SDC_FILE\s*=).*$",
               lambda m: f"{m.group(1)} {dst}/constraints/constraint.sdc", t)
    open(cp, "w").write(t)
    return dst


def antenna_count(p):
    for ly in glob.glob(os.path.join(p, "backend", "RUN_*", "drc", "6_drc.lyrdb")):
        t = open(ly, errors="replace").read()
        return sum(1 for c in re.findall(r"<category>'?([^'<]+?)'?</category>", t)
                   if "ANTENNA" in c.upper())
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--failures", default=f"{D}/failures.json")
    ap.add_argument("--src-root", default=f"{D}/runb_ant")
    ap.add_argument("--out", default=f"{D}/ratio_trial")
    ap.add_argument("--controls", type=int, default=10)
    a = ap.parse_args()
    rows = json.load(open(a.failures))
    subj = [r for r in rows if r["shape"] == "recipe_no_op"
            and any("ANTENNA" in k.upper() for k in (r["classes"] or {}))]
    subj_names = {r["design"] for r in subj}
    shutil.rmtree(a.out, ignore_errors=True)
    os.makedirs(a.out)

    manifest = {"subjects": [], "controls": []}
    for r in subj:
        src = os.path.join(a.src_root, r["design"])
        if not os.path.isdir(os.path.join(src, "constraints")):
            continue
        manifest["subjects"].append({
            "path": stage(src, a.out),
            "baseline_antenna": sum((r["classes"] or {}).values()),
            "classes": r["classes"]})
    # Controls come from the CORPUS, not from the repair tree: the repair tree's
    # clean results were overwritten when the batch runner reset reports/ and
    # re-ran, and that re-run was then killed by the reaper, so it holds no
    # completed-and-clean design at all. The corpus has 1,703 nangate45 designs
    # whose signoff DRC read clean under the same deck, which is the right
    # control population anyway -- untouched by any repair.
    import sqlite3
    conn = sqlite3.connect(f"{D}/knowledge_campaign.sqlite")
    rows_c = conn.execute(
        "SELECT r.project_path, r.cell_count FROM run_violations rv "
        "JOIN runs r USING(run_id) WHERE r.platform='nangate45' "
        "AND r.ingested_at>'2026-10-01' AND rv.drc_status='clean' "
        "AND r.project_path NOT LIKE '%_ab%' "
        "ORDER BY r.cell_count DESC LIMIT 400").fetchall()
    n = 0
    for proj, cells in rows_c:
        if n >= a.controls:
            break
        if not os.path.isfile(os.path.join(proj, "constraints", "config.mk")):
            continue
        manifest["controls"].append({"path": stage(proj, a.out),
                                     "baseline_antenna": 0, "cells": cells})
        n += 1
    json.dump(manifest, open(f"{a.out}_manifest.json", "w"), indent=1)
    print(f"subjects {len(manifest['subjects'])}  controls {len(manifest['controls'])}")
    print(f"subject baseline antenna violations: "
          f"{sum(s['baseline_antenna'] for s in manifest['subjects'])}")
    print(f"wrote {a.out}_manifest.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
