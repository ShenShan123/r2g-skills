#!/usr/bin/env python3
"""Export nangate45 dev designs as agent input folders (runs on 216).

Each design becomes <short>/ holding exactly what the frozen baseline compiled:
the VERILOG_FILES of its baseline config.mk, every file they `include
(recursively; includer's dir, then the include dirs -- yosys' order) and the
$readmem files they name, at their path relative to the repository root, plus
design.json {top, files, include_dirs, origin}. The file list is the baseline's
own, including its mistakes (a .vh or .hex listed as a source): the agent has
to fix those the way the repair arm did, or the comparison is not like for like.

  python3 -I export_corpus.py SETS.json OUT.tar [N_CONTROL]

SETS.json is tally_n45_repair.py --json. Selected: every r2g_extra design (the
148 the repair arm won), every apparatus rescue, and N_CONTROL (default 40)
frozen-baseline passes drawn with a fixed seed as the regression control.
"""
import io
import json
import os
import random
import re
import sys
import tarfile

D = "/proj/workarea/yangao"
RUNS = D + "/rerun_nangate45_20261018/runs"
PKG = D + "/rtl-certified-handoff-0002/sources/"
INC = re.compile(r'^\s*`include\s+"([^"]+)"', re.M)
MEM = re.compile(r'\$readmem[hb]\s*\(\s*"([^"]+)"')


def mk(cfg, key):
    m = re.search(r"^export %s\s*=\s*(.*)$" % key, cfg, re.M)
    return m.group(1).split() if m else []


def collect(files, incs, root):
    want, todo, seen, missing = [], list(files), set(), []
    while todo:
        f = os.path.normpath(todo.pop(0))
        if f in seen:
            continue
        seen.add(f)
        if not os.path.isfile(f):
            missing.append(f)
            continue
        want.append(f)
        txt = open(f, errors="replace").read()
        for inc in INC.findall(txt):
            for base in [os.path.dirname(f)] + incs:
                c = os.path.normpath(os.path.join(base, inc))
                if os.path.isfile(c):
                    todo.append(c)
                    break
        for mf in MEM.findall(txt):
            for base in [os.path.dirname(f), root]:
                c = os.path.normpath(os.path.join(base, mf))
                if os.path.isfile(c) and c not in seen:
                    seen.add(c)
                    want.append(c)
                    break
    return want, missing


def main():
    sets = json.load(open(sys.argv[1]))
    out, n_ctl = sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 40
    rng = random.Random(20261023)
    control = sorted(rng.sample(sets["baseline_frozen"], n_ctl))
    groups = {**{k: "control" for k in control}, **{k: "apparatus" for k in sets["apparatus"]},
              **{k: "r2g:" + v for k, v in sets["r2g_extra"].items()}}
    runs = {"__".join(n.split("__")[:3]): n for n in os.listdir(RUNS)}
    index, problems = {}, {}
    with tarfile.open(out, "w") as tar:
        for short, group in sorted(groups.items()):
            run = runs.get(short)
            if not run:
                problems[short] = "no baseline run"
                continue
            cfg = open(os.path.join(RUNS, run, "config.mk")).read()
            files, incs = mk(cfg, "VERILOG_FILES"), mk(cfg, "VERILOG_INCLUDE_DIRS")
            top = mk(cfg, "DESIGN_NAME")[0]
            if not files or not files[0].startswith(PKG):
                problems[short] = "sources outside the package"
                continue
            root = PKG + files[0][len(PKG):].split("/")[0]
            want, missing = collect(files, incs, root)
            if any(not f.startswith(root + "/") for f in want):
                problems[short] = "a file outside its repository"
                continue
            rel = lambda p: os.path.relpath(p, root)
            dj = {"top": top, "files": [rel(f) for f in files if os.path.isfile(f)],
                  "include_dirs": [rel(i) for i in incs if i.startswith(root)],
                  "origin": {"design": short, "baseline_run": run, "group": group,
                             "repo": os.path.basename(root), "missing": [rel(m) for m in missing]}}
            for f in want:
                tar.add(f, arcname="%s/%s" % (short, rel(f)))
            data = json.dumps(dj, indent=1).encode()
            ti = tarfile.TarInfo("%s/design.json" % short)
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))
            index[short] = group
        data = json.dumps({"designs": index, "problems": problems}, indent=1).encode()
        ti = tarfile.TarInfo("_index.json")
        ti.size = len(data)
        tar.addfile(ti, io.BytesIO(data))
    print("exported %d designs, %d problems" % (len(index), len(problems)))
    for k, v in problems.items():
        print("  %s: %s" % (k, v))


if __name__ == "__main__":
    main()
