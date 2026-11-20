#!/usr/bin/env python3
"""Build bare-agent cases for the frozen ASAP7 repair cohort (runs on 216, read-only on the baseline).

  build_cases.py SCOPE.json OUT_DIR

For every in-scope id of SCOPE.json (frozen_scope_asap7_v1_20261119.json) this writes, like export_failures.py does
for the other platforms:
  OUT_DIR/inputs/<id>/       exactly the baseline config's VERILOG_FILES, every file they `include (recursively) and
                             the $readmem files they name, at their path relative to the source repository
  OUT_DIR/baselines/<id>/    the frozen baseline config.mk + constraint.sdc, with
                               - source paths on 216 (203-native runs named /data/yangao/asap7_baseline/... copies of
                                 the same package),
                               - the frozen ASAP7 apparatus (router tech LEF, edge-pin-window cell LEF, PDN Tcl) as
                                 /work/<file> lines with the file beside the config (run_bare.prepare carries them
                                 into the project, as it does gf180's overrides)
  OUT_DIR/cases.json         {"schema", "platform": "asap7", "population", "candidates": [...]} in the format of
                             cases_<platform>.json (id, top, input_root, source_files, include_dirs, source_sha256)
Designs whose baseline config cannot be reconstructed are listed under "problems" (never silently dropped).
"""
import hashlib
import json
import os
import re
import shutil
import sys

B = "/proj/workarea/yangao/asap7_baseline"
PKG = "/proj/workarea/yangao/rtl-certified-handoff-0002/sources/"
PKG_203 = "/data/yangao/asap7_baseline/rtl-certified-handoff-0002/sources/"
APPARATUS = {"TECH_LEF": ("asap7_tech_asym.lef", B + "/apparatus/asap7_tech_asym.lef"),
             "SC_LEF": ("asap7_pin_window.lef", B + "/apparatus/asap7sc7p5t_R_edge_pin_window.lef"),
             "PDN_TCL": ("pdn_m1m2m3.tcl", B + "/apparatus/pdn_m1m2m3.tcl")}
INC = re.compile(r'^\s*`include\s+"([^"]+)"', re.M)
MEM = re.compile(r'\$readmem[hb]\s*\(\s*"([^"]+)"')


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def mk(cfg, key):
    m = re.search(r"^\s*export\s+%s\s*=\s*(.*)$" % key, cfg, re.M)
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


CERT = "/proj/workarea/yangao/rtl-certified-handoff-0002/certified/"


def resolve_src_links(cfg, design):
    """216 runs reach make-hostile source paths (spaces, ...) through /work/_src/dN symlinks (orfs_spec.py
    _safe_paths); map each dN back to the original prefix: the hostile-component prefix of the certified filelist
    under which every config path using dN exists."""
    import glob
    fl = glob.glob(CERT + design + "/filelist.f")
    if not fl or "/work/_src/" not in cfg:
        return cfg
    base = os.path.dirname(fl[0])
    origs = []
    for ln in open(fl[0]).read().splitlines():
        ln = ln.strip()
        if ln.startswith("+incdir+"):
            ln = ln[len("+incdir+"):]
        elif ln.startswith(("+", "-")) or not ln:
            continue
        origs.append(os.path.normpath(os.path.join(base, ln)))
    prefixes = set()
    for o in origs:
        parts = o.split("/")
        for i in range(1, len(parts) + 1):
            if re.search(r"[\s#$()'\"&;|<>*?]", parts[i - 1]):
                prefixes.add("/".join(parts[:i]))
    uses = {}
    for m in re.finditer(r"/work/_src/(d\d+)((?:/[^\s]*)?)", cfg):
        uses.setdefault(m.group(1), set()).add(m.group(2))
    for d, rests in uses.items():
        hit = [t for t in sorted(prefixes) if all(os.path.exists(t + r) for r in rests)]
        if len(hit) != 1:
            raise ValueError("cannot resolve /work/_src/%s (%d candidates)" % (d, len(hit)))
        cfg = re.sub(r"/work/_src/%s(?=[/\s]|$)" % d, lambda _m: hit[0].replace(" ", "\\ "), cfg)
    return cfg


def baseline_run(tag):
    for root in ("baseline_rerun216/runs", "inbox203/runs", "baseline/runs"):       # same precedence as the freeze
        d = os.path.join(B, root, tag)
        if os.path.isfile(os.path.join(d, "result.json")):
            return d
    return None


def main():
    scope, out = json.load(open(sys.argv[1])), sys.argv[2]
    ids = scope["platforms"]["asap7"]["in_scope_ids"]
    reasons = scope["platforms"]["asap7"]["in_scope_reason"]
    tags = {}
    for root in ("baseline/runs", "inbox203/runs", "baseline_rerun216/runs"):
        for t in os.listdir(os.path.join(B, root)):
            tags["asap7_" + t.split("__")[0]] = t
    cands, problems = [], {}
    for cid in ids:
        tag = tags.get(cid)
        rdir = baseline_run(tag) if tag else None
        if not rdir or not os.path.isfile(os.path.join(rdir, "config.mk")):
            problems[cid] = "no baseline config.mk"
            continue
        cfg = open(os.path.join(rdir, "config.mk")).read().replace(PKG_203, PKG)
        if "/work/_src/" in cfg:
            # make-hostile source paths (spaces): the driver reached them through /work/_src symlinks; the harness
            # and make split file lists on whitespace, so these leave the cohort for BOTH arms, the precedent of
            # the N45 cohort_exclusions.json (inputs the harness cannot export). resolve_src_links() maps them back
            # for the record.
            problems[cid] = "make-hostile source paths (whitespace); excluded for both arms"
            continue
        files, incs = mk(cfg, "VERILOG_FILES"), mk(cfg, "VERILOG_INCLUDE_DIRS")
        top = mk(cfg, "DESIGN_NAME")[0]
        if not files or not files[0].startswith(PKG):
            problems[cid] = "sources outside the package"
            continue
        root = PKG + files[0][len(PKG):].split("/")[0]
        want, missing = collect(files, incs, root)
        if any(not f.startswith(root + "/") for f in want):
            problems[cid] = "a file outside its repository"
            continue
        rel = lambda p: os.path.relpath(p, root)                                    # noqa: E731
        inp = os.path.join(out, "inputs", cid)
        for f in want:
            d = os.path.join(inp, rel(f))
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copyfile(f, d)
        bdir = os.path.join(out, "baselines", cid)
        os.makedirs(bdir, exist_ok=True)
        lines = []
        for ln in cfg.splitlines():
            m = re.match(r"\s*(?:override\s+)?export\s+(TECH_LEF|SC_LEF|PDN_TCL)\s*=", ln)
            if m:
                name, src = APPARATUS[m.group(1)]
                shutil.copyfile(src, os.path.join(bdir, name))
                ln = "%sexport %s = /work/%s" % ("override " if m.group(1) == "TECH_LEF" else "", m.group(1), name)
            lines.append(ln)
        open(os.path.join(bdir, "config.mk"), "w").write("\n".join(lines) + "\n")
        shutil.copyfile(os.path.join(rdir, "constraint.sdc"), os.path.join(bdir, "constraint.sdc"))
        srcs = [rel(f) for f in files if os.path.isfile(f)]
        cands.append({"id": cid, "platform": "asap7", "design": tag.rsplit("__asap7__", 1)[0], "top": top,
                      "input_root": inp, "input_kind": "original_unrepaired_corpus", "source_files": srcs,
                      "include_dirs": [rel(i) for i in incs if i.startswith(root)],
                      "source_sha256": {r: sha(os.path.join(inp, r)) for r in srcs},
                      "missing_source_files": [rel(m) for m in missing], "baseline_dir": bdir,
                      "baseline_run": rdir, "baseline_reason": reasons[cid]})
    json.dump({"schema": "r2g-vs-bare-full-v1", "platform": "asap7",
               "population": "frozen ASAP7 repair cohort (%s)" % scope["version"], "candidates": cands,
               "problems": problems}, open(os.path.join(out, "cases.json"), "w"), indent=1)
    print("cases", len(cands), "problems", len(problems), problems)


if __name__ == "__main__":
    main()
