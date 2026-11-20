#!/usr/bin/env python3
"""Export the 26 Nangate45 designs with >10k post-synthesis cells from the Exp3 cleanup set into
/data/r2g_n45_pilot500/nangate45_large26/, in the pilot500 directory layout (user, 2026-11-12).

These are NOT a pilot500-style tight-clock re-run: each design is exported from its best existing
Exp3 run, choosing (1) DRC+LVS clean with reports bound to that run and all stage databases kept,
(2) clean and bound, (3) all stages kept, (4) the latest run that has a final layout. Only files that
exist are written, under their true names (no 6_final.def renamed to 5_route.def). result.json records
the source run, its arm, signoff status and cell count.

  export_large26.py --survey /tmp/survey26.json --cells /tmp/size_scan_cells.json --out DIR [--openroad BIN]
"""
import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

STAGE_DEF = {"2_floorplan.odb": "2_floorplan.def", "3_place.odb": "3_place.def",
             "4_cts.odb": "4_cts.def", "5_route.odb": "5_route.def"}
COPY_ODB = ("5_1_grt.odb", "5_2_route.odb")


def arm_of(run: str) -> str:
    if "/rerun_C04009" in run:
        return "rerun (model-only DeepSeek, C04009)"
    if "/recipe_reuse_" in run:
        return "R2G recipe-reuse rerun"
    if "/r2g_gpt_pilot_" in run:
        return "R2G (GPT pilot)"
    if "/r2g_full_ds_" in run or "/r2g_full_gf180_" in run:
        return "R2G (DeepSeek)"
    if "/fallback_gpt_" in run:
        return "R2G then GPT fallback"
    if "/fallback_" in run:
        return "R2G then DeepSeek fallback"
    if "/r2g_gpt_full_203/bare" in run or "bare_gpt" in run:
        return "GPT only (model-only arm)"
    return "other"


def clean_bound(r):
    s = r.get("signoff") or {}
    return all((s.get(n) or [None, False])[0] == "clean" and (s.get(n) or [None, False])[1] for n in ("drc", "lvs"))


def full(r):
    return all(s in r["have"] for s in STAGE_DEF)


def pick(runs):
    for key in (lambda r: clean_bound(r) and full(r), clean_bound, full, lambda r: "6_final.def" in r["have"]):
        c = [r for r in runs if key(r) and "6_final.def" in r["have"]
             and "/voided/" not in r["run"] and "/interrupted/" not in r["run"]]
        if c:
            return max(c, key=lambda r: r["mtime"])
    return None


def find(run, name):
    hits = glob.glob(run + "/**/" + name, recursive=True)
    return Path(sorted(hits, key=len)[0]) if hits else None


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--survey", required=True)
    ap.add_argument("--cells", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--openroad", default="/home/yangao/r2g_toolchain/bin/openroad")
    a = ap.parse_args()
    survey = json.load(open(a.survey))
    cells = json.load(open(a.cells))
    a.out.mkdir(parents=True, exist_ok=True)
    rows = []
    for did, runs in sorted(survey.items()):
        r = pick(runs)
        d = a.out / did
        if d.exists():
            shutil.rmtree(d)
        d.mkdir()
        if r is None:
            rows.append((did, cells.get(did), "no usable run"))
            (d / "result.json").write_text(json.dumps({"design": did, "status": "no exportable run"}, indent=1))
            continue
        run, proj = r["run"], r["proj"]
        files = {}
        for odb, de in STAGE_DEF.items():
            src = find(run, odb)
            if src:
                tcl = d / ".w.tcl"
                tcl.write_text("read_db %s\nwrite_def %s\n" % (src, d / de))
                subprocess.run([a.openroad, "-no_splash", "-exit", str(tcl)], capture_output=True, timeout=7200)
                tcl.unlink()
                if (d / de).is_file():
                    files[de] = "write_def of " + odb
        for name in COPY_ODB + ("6_final.def", "6_final.spef", "1_2_yosys.v"):
            src = find(run, name)
            if src:
                shutil.copyfile(src, d / name)
                files[name] = str(src)
        if not (d / "6_final.spef").exists():
            sp = sorted(glob.glob(proj + "/rcx/*.spef"))
            if sp:
                shutil.copyfile(sp[0], d / "6_final.spef")
                files["6_final.spef"] = sp[0]
        for sub, srcdir in (("logs", run + "/logs"), ("reports", run + "/reports_orfs")):
            if os.path.isdir(srcdir):
                shutil.copytree(srcdir, d / sub, dirs_exist_ok=True)
        # the project's signoff/PPA summaries (drc.json, lvs.json, ppa.json, ...) next to the ORFS reports
        sj = glob.glob(proj + "/reports/*.json")
        if sj:
            (d / "reports").mkdir(exist_ok=True)
            for f in sj:
                shutil.copyfile(f, d / "reports" / ("signoff_" + os.path.basename(f)))
        cons = Path(proj) / "constraints"
        for f in ("config.mk", "constraint.sdc"):
            if (cons / f).is_file():
                shutil.copyfile(cons / f, d / f)
        rtl = Path(proj) / "rtl"
        if rtl.is_dir():
            shutil.copytree(rtl, d / "rtl", ignore=shutil.ignore_patterns(".git"))
            desc = {}
            if (rtl / "design.json").is_file():
                desc = json.loads((rtl / "design.json").read_text())
            (d / "rtl/MANIFEST.txt").write_text(
                "top: %s\nfiles (read order):\n%s\ninclude dirs: %s\nsource: %s/rtl (RTL as used by this run)\n"
                % (desc.get("top"), "\n".join("  " + x for x in desc.get("files", [])),
                   " ".join(desc.get("include_dirs", [])), proj))
        period = None
        if (d / "constraint.sdc").is_file():
            m = re.search(r"create_clock[^\n]*-period\s+([0-9.]+)", (d / "constraint.sdc").read_text())
            period = float(m.group(1)) if m else None
        wns = tns = None
        rep = find(run, "6_report.json")
        if rep:
            try:
                j = json.loads(rep.read_text())
                wns = j.get("finish__timing__setup__ws")
                tns = j.get("finish__timing__setup__tns")
            except ValueError:
                pass
        s = r.get("signoff") or {}
        res = {"design": did, "post_synthesis_cells": cells.get(did), "source_run": run, "source_arm": arm_of(run),
               "drc": (s.get("drc") or [None])[0], "lvs": (s.get("lvs") or [None])[0],
               "signoff_bound_to_this_run": clean_bound(r) or bool(s.get("drc") and s["drc"][1] and s.get("lvs") and s["lvs"][1]),
               "dual_clean": clean_bound(r), "clock_period_ns": period, "wns_ns": wns, "tns_ns": tns,
               "files": files, "sha256": {k: sha(d / k) for k in files if (d / k).is_file()},
               "note": "exported from an existing Exp3 run, not a pilot500 tight-clock re-run"}
        (d / "result.json").write_text(json.dumps(res, indent=1, ensure_ascii=False))
        rows.append((did, cells.get(did), "%s; %s; drc=%s lvs=%s; %d files" % (
            arm_of(run), "dual-clean" if res["dual_clean"] else "not dual-clean", res["drc"], res["lvs"], len(files))))
    with open(a.out.parent / "large26_cells.tsv", "w") as f:
        for did, c, _ in rows:
            f.write("%s\t%s\n" % (did, c))
    for row in rows:
        print("\t".join(str(x) for x in row))


if __name__ == "__main__":
    main()
