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
    out = {}
    for line in text.splitlines():
        m = re.match(r"\s*(?:export\s+|override\s+export\s+)?(\w+)\s*([:?+]?=)\s*(.*)$", line)
        if not m or m.group(1) not in KEYS:
            continue
        val = m.group(3).split("#", 1)[0].strip()
        out[m.group(1)] = (out.get(m.group(1), "") + " " + val).strip() if m.group(2) == "+=" else val
    files = []
    for tok in out.get("VERILOG_FILES", "").split():
        w = re.fullmatch(r"\$\(wildcard\s+(.+)\)", tok)
        files += sorted(glob.glob(w.group(1))) if w else [tok]
    return {"top": out.get("DESIGN_NAME", ""), "files": files,
            "incdirs": out.get("VERILOG_INCLUDE_DIRS", "").split(),
            "defines": [d for d in out.get("VERILOG_DEFINES", "").split() if d.startswith("-D")]}


def read_cmds(cfg: dict, reader: str) -> str:
    if reader == "slang":
        # SystemVerilog the built-in reader cannot parse (the reason some
        # baselines failed); slang reads all files as one compilation unit
        opts = " ".join(["-I " + d for d in cfg["incdirs"]] + ["-D " + d[2:] for d in cfg["defines"]])
        return "read_slang %s %s" % (opts, " ".join(cfg["files"]))
    # -overwrite: a module defined twice (the `include-plus-file-list bug some
    # agents fixed) is read once instead of aborting the original's parse
    opts = " ".join(["-sv", "-overwrite"] + ["-I" + d for d in cfg["incdirs"]] + cfg["defines"])
    return "\n".join("read_verilog %s %s" % (opts, f) for f in cfg["files"])


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
        gate = read_config(proj / "constraints/config.mk")
        if gold["top"] != gate["top"]:
            return {**out, "verdict": "not_proven", "reason": "top changed %s -> %s" % (gold["top"], gate["top"])}
        ys = Path(tmp) / "equiv.ys"
        for reader in ("verilog", "slang"):
            # one reader for BOTH sides, so a frontend difference cannot pose as a design one
            ys.write_text(script(gold, gate, reader))
            pre = ["-m", "slang"] if reader == "slang" else []
            try:
                p = subprocess.run([yosys, *pre, "-q", "-l", str(Path(tmp) / "y.log"), "-s", str(ys)],
                                   capture_output=True, text=True, timeout=timeout)
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
