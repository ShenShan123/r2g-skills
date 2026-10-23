#!/usr/bin/env python3
"""Per-design rtl/ folder for the pilot500 delivery.

For each design: the source files its config.mk lists, every file they
`include (recursively, resolved against the includer's dir then the include
dirs -- yosys' order), $readmem data files they name when present, and the
upstream repository's license files. Files keep their path relative to the
repository root so includes resolve unchanged. MANIFEST.txt records the top,
the file list, include dirs and the upstream URL/commit/license.
"""
import json
import os
import re
import shutil
import sys

P = "/home/yangao/p500_tight"
MIRROR = P + "/mirror"
PKG = "/proj/workarea/yangao/rtl-certified-handoff-0002/sources/"
OUT = P + "/rtl_out"
prov = json.load(open(P + "/provenance.json"))
INC = re.compile(r'^\s*`include\s+"([^"]+)"', re.M)
MEM = re.compile(r'\$readmem[hb]\s*\(\s*"([^"]+)"')
LIC = re.compile(r"^(LICEN[CS]E|COPYING|NOTICE|COPYRIGHT)", re.I)


def local(p):
    return MIRROR + p


def main():
    stats = {"designs": 0, "files": 0, "includes": 0, "memfiles": 0, "missing": []}
    for d in sorted(prov):
        cfg = open(f"{P}/cfg/{d}/config.mk").read()
        files = re.search(r"^export VERILOG_FILES\s*=\s*(.*)$", cfg, re.M).group(1).split()
        m = re.search(r"^export VERILOG_INCLUDE_DIRS\s*=\s*(.*)$", cfg, re.M)
        incs = m.group(1).split() if m else []
        repo = files[0][len(PKG):].split("/")[0]
        root = PKG + repo
        want, todo = [], list(files)
        seen = set()
        while todo:
            f = todo.pop(0)
            if f in seen:
                continue
            seen.add(f)
            lf = local(f)
            if not os.path.isfile(lf):
                stats["missing"].append((d, f))
                continue
            want.append(f)
            txt = open(lf, errors="replace").read()
            for inc in INC.findall(txt):
                for base in [os.path.dirname(f)] + incs:
                    cand = os.path.normpath(os.path.join(base, inc))
                    if os.path.isfile(local(cand)):
                        if cand not in seen:
                            todo.append(cand)
                            stats["includes"] += 1
                        break
            for mf in MEM.findall(txt):
                for base in [os.path.dirname(f), root]:
                    cand = os.path.normpath(os.path.join(base, mf))
                    if os.path.isfile(local(cand)) and cand not in seen:
                        seen.add(cand)
                        want.append(cand)
                        stats["memfiles"] += 1
                        break
        dst = f"{OUT}/{d}/rtl"
        if os.path.isdir(dst):
            shutil.rmtree(dst)
        os.makedirs(dst)
        rel = []
        for f in want:
            r = f[len(root) + 1:]
            os.makedirs(os.path.dirname(os.path.join(dst, r)) or dst, exist_ok=True)
            shutil.copy2(local(f), os.path.join(dst, r))
            rel.append(r)
        lic = []
        for e in sorted(os.listdir(local(root))):
            if LIC.match(e) and os.path.isfile(local(root) + "/" + e):
                shutil.copy2(local(root) + "/" + e, os.path.join(dst, e))
                lic.append(e)
        v = prov[d]
        srcs = [f[len(root) + 1:] for f in files]
        with open(os.path.join(dst, "MANIFEST.txt"), "w") as fh:
            fh.write("design      %s\ntop module  %s\nupstream    %s\ncommit      %s\nlicense     %s\n\n"
                     % (d, v["top"], v["url"], v["commit"], v["license"]))
            fh.write("source files (in the order synthesis read them):\n")
            fh.writelines("  %s\n" % s for s in srcs)
            fh.write("\ninclude dirs (relative to this folder):\n")
            fh.writelines("  %s\n" % (os.path.relpath(i, root) if i != root else ".") for i in incs)
            extra = sorted(set(rel) - set(srcs))
            if extra:
                fh.write("\nalso here (`include'd or $readmem'd by the sources):\n")
                fh.writelines("  %s\n" % e for e in extra)
            fh.write("\nlicense files: %s\n" % (", ".join(lic) or "none in the upstream repository root"))
        stats["designs"] += 1
        stats["files"] += len(rel)
    print(json.dumps({k: (v if k != "missing" else v[:10]) for k, v in stats.items()}, indent=1))
    print("missing:", len(stats["missing"]))
    return 1 if stats["missing"] else 0


if __name__ == "__main__":
    sys.exit(main())
