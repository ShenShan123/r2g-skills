#!/usr/bin/env python3
"""Export baseline-failing designs as agent input folders (runs on 216).

Same selection of files as agent_repro/export_corpus.py: exactly the
VERILOG_FILES of the frozen baseline config.mk, every file they `include
(recursively; includer's dir, then the include dirs) and the $readmem files
they name, at their path relative to the source repository, plus design.json
{top, files, include_dirs, origin}. The baseline's own mistakes stay in the
file list. Each design also carries its baseline config.mk and constraint.sdc
under _baseline/<design>/ for the bare-agent arm.

  python3 -I export_failures.py RUNS_DIR DESIGNS.json OUT.tar

DESIGNS.json is a list of short names (Cxxxxx__top__repo).
"""
import io
import json
import os
import re
import sys
import tarfile

PKG = "/proj/workarea/yangao/rtl-certified-handoff-0002/sources/"
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


def add_bytes(tar, name, data):
    ti = tarfile.TarInfo(name)
    ti.size = len(data)
    tar.addfile(ti, io.BytesIO(data))


def main():
    runs_dir, designs, out = sys.argv[1], json.load(open(sys.argv[2])), sys.argv[3]
    runs = {"__".join(n.split("__")[:3]): n for n in os.listdir(runs_dir)}
    index, problems = {}, {}
    with tarfile.open(out, "w") as tar:
        for short in sorted(designs):
            run = runs.get(short)
            if not run:
                problems[short] = "no baseline run"
                continue
            rdir = os.path.join(runs_dir, run)
            cfg = open(os.path.join(rdir, "config.mk")).read()
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
                  "origin": {"design": short, "baseline_run": rdir, "repo": os.path.basename(root),
                             "missing": [rel(m) for m in missing]}}
            for f in want:
                tar.add(f, arcname="%s/%s" % (short, rel(f)))
            add_bytes(tar, "%s/design.json" % short, json.dumps(dj, indent=1).encode())
            for name in ("config.mk", "constraint.sdc"):
                add_bytes(tar, "_baseline/%s/%s" % (short, name), open(os.path.join(rdir, name), "rb").read())
            index[short] = rdir
        add_bytes(tar, "_index.json", json.dumps({"designs": index, "problems": problems}, indent=1).encode())
    print("exported %d designs, %d problems" % (len(index), len(problems)))
    for k, v in problems.items():
        print("  %s: %s" % (k, v))


if __name__ == "__main__":
    main()
