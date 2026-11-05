#!/usr/bin/env python3
"""Semantics review of bare-arm RTL edits by formal equivalence (Yosys).

A design the harness left `pending_semantic_review` (dual-clean, but the model
edited RTL beyond what semantic_review() clears automatically) passes only if
its edited RTL is proven equivalent to the original. The original is rebuilt
exactly as the attempt started (prepare(): original inputs + remapped baseline
config.mk); the edited design is read from the attempt's final config.mk. Both
are flattened under the same top and compared with equiv_make / equiv_simple /
equiv_induct. Anything not proven (unproven cells, a parse error, a renamed
register the matcher cannot pair) is NOT a pass: the same rule R2G's front-end
edits were held to (the aldff counterexamples). Unproven cases are listed for
a human, who may only downgrade, never upgrade without a proof.

  python3 equiv_review.py --root COHORT [--root ...] --baselines DIR --yosys PATH --out review.json
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_bare  # noqa: E402

KEYS = ("DESIGN_NAME", "VERILOG_FILES", "VERILOG_INCLUDE_DIRS", "VERILOG_DEFINES")


def read_config(path: Path) -> dict:
    """The few make variables the front end needs (last assignment wins; `+=` appends)."""
    text = re.sub(r"\\\n", " ", path.read_text(errors="replace"))
    out, allv = {}, {}

    def expand(v):
        # a model may define its own make variables (C03980: $(PATCHED)/x.sv)
        for _ in range(5):
            v2 = re.sub(r"\$[({](\w+)[)}]", lambda m: allv.get(m.group(1), m.group(0)), v)
            if v2 == v:
                break
            v = v2
        return v
    for line in text.splitlines():
        m = re.match(r"\s*(?:export\s+|override\s+export\s+|override\s+)?(\w+)\s*([:?+]?=)\s*(.*)$", line)
        if not m:
            continue
        val = expand(m.group(3).split("#", 1)[0].strip())
        allv[m.group(1)] = (allv.get(m.group(1), "") + " " + val).strip() if m.group(2) == "+=" else val
        if m.group(1) not in KEYS:
            continue
        out[m.group(1)] = (out.get(m.group(1), "") + " " + val).strip() if m.group(2) == "+=" else val
    files = []
    for tok in paths(out.get("VERILOG_FILES", "")):
        w = re.fullmatch(r"\$\(wildcard\s+(.+)\)", tok)
        files += sorted(glob.glob(w.group(1))) if w else [tok]
    return {"top": out.get("DESIGN_NAME", ""), "files": files,
            "incdirs": paths(out.get("VERILOG_INCLUDE_DIRS", "")),
            "defines": [d for d in out.get("VERILOG_DEFINES", "").split() if d.startswith("-D")]}


def paths(value: str) -> list:
    """Whitespace-separated make paths, rejoined where a file or directory name itself
    contains spaces (`src/RSIC-V core/Extend.v` split into two tokens made the original
    fail to parse and every such review read not_proven, 2026-11-04). `\\ ` is honoured;
    otherwise the longest run of adjacent tokens that names an existing path wins."""
    toks = [t.replace("\0", " ") for t in value.replace("\\ ", "\0").split()]
    out, i = [], 0
    while i < len(toks):
        if "$(" in toks[i] or Path(toks[i]).exists():
            out.append(toks[i]); i += 1; continue
        for j in range(min(len(toks), i + 8), i + 1, -1):
            cand = " ".join(toks[i:j])
            if Path(cand).exists():
                out.append(cand); i = j; break
        else:
            out.append(toks[i]); i += 1
    return out


def design_sources(rtl: Path) -> dict:
    """The original's neutral description (rtl/design.json: top, files, include dirs,
    defines) -- the authoritative source list when its config cannot name a file."""
    d = json.loads((rtl / "design.json").read_text())
    return {"top": d.get("top", ""), "files": [str(rtl / f) for f in d.get("files", [])],
            "incdirs": [str(rtl / x) for x in d.get("include_dirs", [])],
            "defines": ["-D" + x if not x.startswith("-D") else x for x in (d.get("defines") or [])]}


def q(path: str) -> str:
    """A Yosys argument: quoted so a path with spaces stays one argument."""
    return '"%s"' % path


def read_cmds(cfg: dict, reader: str) -> str:
    if reader == "slang":
        # SystemVerilog the built-in reader cannot parse (the reason some
        # baselines failed); slang reads all files as one compilation unit
        opts = " ".join(["-I " + q(d) for d in cfg["incdirs"]] + ["-D " + d[2:] for d in cfg["defines"]])
        return "read_slang %s %s" % (opts, " ".join(q(f) for f in cfg["files"]))
    # -overwrite: a module defined twice (the `include-plus-file-list bug some
    # agents fixed) is read once instead of aborting the original's parse
    opts = " ".join(["-sv", "-overwrite"] + [q("-I" + d) for d in cfg["incdirs"]] + cfg["defines"])
    return "\n".join("read_verilog %s %s" % (opts, q(f)) for f in cfg["files"])


def script(gold: dict, gate: dict, reader: str = "verilog") -> str:
    top = gold["top"]
    prep = "hierarchy -check -top %s\nproc\nflatten\nmemory -nomap\nmemory_map\nopt_clean\n" % top
    return "\n".join([
        read_cmds(gold, reader), prep + "rename %s gold\ndesign -stash gold" % top,
        read_cmds(gate, reader), prep + "rename %s gate\ndesign -stash gate" % top,
        "design -copy-from gold -as gold gold", "design -copy-from gate -as gate gate",
        "equiv_make gold gate equiv", "hierarchy -top equiv", "async2sync",
        "equiv_simple -seq 5", "equiv_induct -seq 5", "equiv_status"]) + "\n"


def review(case_dir: Path, baselines: Path, yosys: str, timeout: int) -> dict:
    r = json.loads((case_dir / "result.json").read_text())
    case = json.loads((case_dir / "case.json").read_text())
    proj = case_dir / (r["case_id"] + "_bare")
    out = {"case_id": r["case_id"], "pending": r.get("semantic_review", {}).get("pending")}
    with tempfile.TemporaryDirectory() as tmp:
        orig = Path(tmp) / (r["case_id"] + "_orig")
        run_bare.prepare(case, baselines / case["id"], orig)
        gold = read_config(orig / "constraints/config.mk")
        if not gold["files"] or any(not Path(f).exists() for f in gold["files"]):
            # the baseline config cannot carry such names (its remap splits them too)
            gold = {**design_sources(orig / "rtl"), "top": gold["top"] or design_sources(orig / "rtl")["top"]}
        gate = read_config(proj / "constraints/config.mk")
        if gold["top"] != gate["top"]:
            return {**out, "verdict": "not_proven", "reason": "top changed %s -> %s" % (gold["top"], gate["top"])}
        ys = Path(tmp) / "equiv.ys"
        for reader in ("verilog", "slang"):
            # one reader for BOTH sides, so a frontend difference cannot pose as a design one
            ys.write_text(script(gold, gate, reader))
            pre = ["-m", "slang"] if reader == "slang" else []
            try:
                # from the original source root: a relative $readmem path in the
                # original resolves the way it does in its repository
                p = subprocess.run([yosys, *pre, "-q", "-l", str(Path(tmp) / "y.log"), "-s", str(ys)],
                                   capture_output=True, text=True, timeout=timeout, cwd=orig / "rtl")
                log = (Path(tmp) / "y.log").read_text(errors="replace") if (Path(tmp) / "y.log").is_file() else ""
            except subprocess.TimeoutExpired:
                return {**out, "verdict": "not_proven", "reason": "timeout %ds" % timeout}
            parse_failed = p.returncode != 0 and re.search(r"syntax error|Re-definition|unexpected", p.stderr + log)
            if not parse_failed:
                break
        out["reader"] = reader
    m = re.search(r"Found (\d+) \$equiv cells.*?Of those cells (\d+) are proven and (\d+) are unproven", log, re.S)
    if p.returncode != 0 or not m:
        err = [l for l in (p.stderr + log).splitlines() if "ERROR" in l][-1:]
        return {**out, "verdict": "not_proven", "reason": "yosys failed: %s" % (err[0][:300] if err else p.returncode)}
    total, proven, unproven = map(int, m.groups())
    return {**out, "verdict": "equivalent" if unproven == 0 and total > 0 else "not_proven",
            "equiv_cells": total, "proven": proven, "unproven": unproven}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, action="append", required=True)
    ap.add_argument("--baselines", type=Path, required=True)
    ap.add_argument("--yosys", required=True)
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    rows = []
    for root in a.root:
        for res in sorted(root.glob("cases/*/result.json")):
            if json.loads(res.read_text()).get("outcome") != "pending_semantic_review":
                continue
            try:
                row = review(res.parent, a.baselines, a.yosys, a.timeout)
            except Exception as e:                      # noqa: BLE001
                row = {"case_id": res.parent.name, "verdict": "not_proven", "reason": "%s: %s" % (type(e).__name__, e)}
            rows.append(row)
            print(json.dumps(row), flush=True)
    a.out.write_text(json.dumps({"method": __doc__.split("\n\n")[1], "rows": rows}, indent=1))


if __name__ == "__main__":
    main()
