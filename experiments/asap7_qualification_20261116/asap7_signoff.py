#!/usr/bin/env python3
"""ASAP7 signoff for one harvested ORFS run, on memlab216 (Calibre 2025.1 + official r1p7 decks).

  asap7_signoff.py RUN_DIR OUT_DIR [--drc-budget S] [--lvs-budget S] [--keep-going]

RUN_DIR is a design directory harvested by orfs_baseline.py (6_final.gds, 6_final.odb, config.mk).

1. Inputs (inside the r2g-orfs:26Q3 container, geometry unchanged): drc.gds = final GDS with the top cell
   renamed TOPCELL (the official deck hard-codes it); lvs.gds = final GDS with top-level pin labels copied to the
   LVS text layer (metal datatype 2 -> 251); source.cdl = OpenROAD write_cdl of 6_final.odb against the official
   ASAP7 PEX-derived transistor library (parasitics removed, devices kept), zero-device tap/filler instances
   omitted. Same procedure as the qualification (prepare_design_gds.py, prepare_variant_cdl.py).
2. DRC, unchanged official deck, FLAT mode (the mode of the accepted qualification runs; hierarchical mode adds
   FIN.S.1 representation artifacts), ALL results, four zero metal offsets, run as two groups at once:
     A = the 333 rule checks other than ACTIVE.AUX.3      (minutes even on large designs)
     B = ACTIVE.AUX.3                                      (one encrypted operation that grows super-linearly)
   Validated decomposition: on every control where both finished, the per-rule result-record multisets of A+B equal
   the full deck's (validate_partition.py). Verdict:
     violations  - any result in A or B (B is stopped as soon as A has results, unless --keep-going)
     clean       - A and B both complete with zero results
     unknown     - A clean but B did not finish within the budget (never counted as clean)
     failed      - A did not complete
3. LVS, official deck, -hier -turbo 4, LVS PROPERTY RESOLUTION MAXIMUM 65536 (the qualification's recheck
   setting; tolerances unchanged). Verdict CORRECT / INCORRECT / failed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("ASAP7_QUAL_ROOT", "/proj/workarea/yangao/asap7_qualification_20261116"))
DECK_DIR = ROOT / "private_rules/calibredecks_r1p7/calibre/ruledirs"
RULES = [l.strip() for l in (ROOT / "rule_names_334.txt").read_text().splitlines() if l.strip()]
SLOW = "ACTIVE.AUX.3"
# The official PEX-derived library with ONE port-name normalisation (2026-11-16): DFFASRHQNx1's RESETn/SETn are
# spelled RESETN/SETN, as in the LEF and liberty. write_cdl matches ports to LEF pins by exact name, so with the
# official spelling it wrote both pins as _unconnected_* and every async set/reset flop read LVS INCORRECT
# (33 of 33 discrepancies on C00648). Devices and topology are unchanged; the 212-cell port audit is clean.
PEX_CDL = Path(os.environ.get("ASAP7_PEX_CDL",
                              "/proj/workarea/yangao/asap7_baseline/apparatus/asap7_R_official_pex_full_lefpins.cdl"))
IMAGE = os.environ.get("R2G_IMAGE", "r2g-orfs:26Q3")
PHYSICAL = {"TAPCELL_ASAP7_75t_R", "FILLER_ASAP7_75t_R", "FILLERxp5_ASAP7_75t_R"}
# memlab203 (2026-11-19, user-approved): the same Calibre install copied to /data, licensed from 216's server
# (MGLS_LICENSE_FILE=27020@<license-host>), decks/PDK/PEX at env-given paths, and NATIVE input preparation
# (ASAP7_SIGNOFF_NATIVE=1: 203 has no container runtime; KLayout + the native ORFS OpenROAD run the same two scripts).
NATIVE = os.environ.get("ASAP7_SIGNOFF_NATIVE") == "1"
ENV = dict(os.environ, MGLS_LICENSE_FILE=os.environ.get("MGLS_LICENSE_FILE", "27020@sv01"),
           pdk_path=os.environ.get("ASAP7_PDK_PATH", "/proj/workarea/LIB/asap7/asap7_pdk_r1p7"),
           VAR_M4_YOFFSET="0", VAR_M5_XOFFSET="0", VAR_M6_YOFFSET="0", VAR_M7_XOFFSET="0")

PREP_GDS = r'''
import json, os, pya
src, out, top_name = os.environ["SRC"], os.environ["OUT"], os.environ["TOP"]
layout = pya.Layout(); layout.read(src)
top = layout.cell(top_name)
assert top is not None and top.is_top(), "missing layout top " + top_name
top.name = "TOPCELL"; layout.write(out + "/drc.gds")
top.name = top_name
labels = 0
for metal in (19, 20, 30, 40, 50, 60, 70, 80, 90):
    index = layout.find_layer(metal, 2)
    if index is None:
        continue
    for shape in list(top.shapes(index).each()):
        if shape.is_text():
            top.shapes(layout.layer(metal, 251)).insert(shape.text); labels += 1
layout.write(out + "/lvs.gds")
json.dump({"top_level_pin_labels_copied": labels}, open(out + "/gds_prep.json", "w"))
'''


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def design_name(run: Path) -> str:
    m = re.search(r"^\s*export\s+DESIGN_NAME\s*[:?]?=\s*(\S+)", (run / "config.mk").read_text(), re.M)
    if not m:
        raise SystemExit("no DESIGN_NAME in config.mk")
    return m.group(1)


def prepare(run: Path, inp: Path, top: str) -> dict:
    inp.mkdir(parents=True, exist_ok=True)
    (inp / "prep_gds.py").write_text(PREP_GDS)
    (inp / "write_cdl.tcl").write_text(
        "read_db {/design/6_final.odb}\nwrite_cdl -include_fillers -masters {/lib/pex.cdl} {/inp/design_with_physical.cdl}\n")
    if NATIVE:
        (inp / "write_cdl.tcl").write_text(
            f"read_db {{{run}/6_final.odb}}\nwrite_cdl -include_fillers -masters {{{PEX_CDL}}} {{{inp}/design_with_physical.cdl}}\n")
        env = dict(os.environ, SRC=str(run / "6_final.gds"), OUT=str(inp), TOP=top)
        cmd = ["bash", "-c", "klayout -b -r %s/prep_gds.py && %s -no_init -exit %s/write_cdl.tcl"
               % (inp, os.environ.get("ASAP7_OPENROAD", "openroad"), inp)]
    else:
        env = None
        cmd = ["podman", "run", "--rm", "--name", "asap7prep_" + hashlib.sha1(str(inp).encode()).hexdigest()[:12],
               "-v", f"{run}:/design:ro", "-v", f"{inp}:/inp", "-v", f"{PEX_CDL}:/lib/pex.cdl:ro",
               "-e", "SRC=/design/6_final.gds", "-e", "OUT=/inp", "-e", f"TOP={top}", IMAGE, "bash", "-lc",
               "klayout -b -r /inp/prep_gds.py && openroad -no_init -exit /inp/write_cdl.tcl"]
    with open(inp / "prep.log", "w") as log:
        rc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, timeout=3600, env=env).returncode
    raw = inp / "design_with_physical.cdl"
    if rc != 0 or not (inp / "drc.gds").is_file() or not raw.is_file():
        raise RuntimeError(f"input preparation failed (rc={rc}); see {inp / 'prep.log'}")
    removed, kept = {}, []
    for line in raw.read_text().splitlines(keepends=True):
        f = line.split()
        if f and f[0].upper().startswith("X") and f[-1] in PHYSICAL:
            removed[f[-1]] = removed.get(f[-1], 0) + 1
        else:
            kept.append(line)
    # '$' starts an inline comment in Calibre's SPICE reader, so a yosys net such as `$3[0]` truncates its line
    # (Syntax Error, 2026-11-17, C01251). Rename the character inside the design's own netlist only; LVS matches
    # by connectivity, so a consistent internal rename changes nothing it compares. The library is untouched.
    renamed = sum(l.count("$") for l in kept)
    kept = [l.replace("$", "_DOLLAR_") for l in kept]
    (inp / "source.cdl").write_text(PEX_CDL.read_text() + "\n" + "".join(kept))
    info = json.loads((inp / "gds_prep.json").read_text())
    info.update(original_gds_sha256=sha(run / "6_final.gds"), drc_gds_sha256=sha(inp / "drc.gds"),
                lvs_gds_sha256=sha(inp / "lvs.gds"), source_cdl_sha256=sha(inp / "source.cdl"),
                pex_library_sha256=sha(PEX_CDL), zero_device_instances_omitted=removed,
                dollar_chars_renamed=renamed)
    return info


def start_drc(work: Path, gds: Path, select: list[str], budget: int) -> subprocess.Popen:
    work.mkdir(parents=True, exist_ok=True)
    (work / "test.gds").symlink_to(gds)
    deck = DECK_DIR / "drc/drcRules_calibre_asap7.rul"
    (work / "run.svrf").write_text("DRC MAXIMUM RESULTS ALL\n" f'INCLUDE "{deck}"\n'
                                   "DRC SELECT CHECK " + " ".join(select) + "\n")
    log = open(work / "run.log", "w")
    return subprocess.Popen(["timeout", "--kill-after=30", str(budget), "calibre", "-drc", "run.svrf"],
                            cwd=work, env=ENV, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)


def drc_counts(work: Path) -> dict | None:
    rep = work / "drc_report"
    if not rep.is_file():
        return None
    txt = rep.read_text(errors="replace")
    if not re.search(r"TOTAL DRC RuleChecks Executed:\s*\d+", txt):
        return None
    return {m[0]: int(m[2] or m[1]) for m in re.findall(
        r"^RULECHECK\s+(\S+)\s+\.+\s+TOTAL Result Count =\s*(\d+)(?:\s*\((\d+)\))?", txt, re.M)}


def stop(p: subprocess.Popen) -> None:
    if p.poll() is None:
        try:
            os.killpg(p.pid, signal.SIGTERM)
            p.wait(timeout=60)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def run_lvs(work: Path, gds: Path, cdl: Path, top: str, budget: int) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    deck = DECK_DIR / "lvs/lvsRules_calibre_asap7.rul"
    (work / "run.svrf").write_text("\n".join([
        f'LAYOUT PATH "{gds}"', f'LAYOUT PRIMARY "{top}"', "LAYOUT SYSTEM GDSII",
        f'SOURCE PATH "{cdl}"', f'SOURCE PRIMARY "{top}"', "SOURCE SYSTEM SPICE", f'INCLUDE "{deck}"',
        'LVS POWER NAME "VDD" "VDD!" "vdd!"', 'LVS GROUND NAME "VSS" "VSS!" "vss!"',
        # write_cdl keeps hierarchical names such as alu_connect/_0134_; Calibre's SPICE reader treats '/' as a
        # separator by default, splitting one net into two pins ("Wrong pin count", 2026-11-17, 5 dev designs).
        # Parsing of the source netlist only -- no rule, tolerance or device changes.
        "LVS SPICE SLASH IS SPACE NO",
        "LVS PROPERTY RESOLUTION MAXIMUM 65536"]) + "\n")
    t0 = time.monotonic()
    with open(work / "run.log", "w") as log:
        rc = subprocess.run(["timeout", "--kill-after=30", str(budget), "calibre", "-lvs", "-hier", "-turbo", "4",
                             "run.svrf"], cwd=work, env=ENV, stdout=log, stderr=subprocess.STDOUT).returncode
    m = re.search(r"LVS completed\. (CORRECT|INCORRECT)\.", (work / "run.log").read_text(errors="replace"))
    return {"verdict": m.group(1) if (rc == 0 and m) else ("timeout" if rc in (124, 137) else "failed"),
            "exit": rc, "wall_s": round(time.monotonic() - t0, 1)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--drc-budget", type=int, default=21600, help="seconds for each DRC group (frozen bound: 6 h)")
    ap.add_argument("--lvs-budget", type=int, default=21600)
    ap.add_argument("--keep-going", action="store_true", help="let group B finish even when A has results")
    a = ap.parse_args()
    run, out = a.run.resolve(), a.out.resolve()
    if (out / "signoff.json").exists():
        print(f"already done: {out}")
        return 0
    # One signer per design (2026-11-17): two batch lanes may both reach a design; without this the second would
    # rmtree the first one's live working directory. The lock is a sibling file holding the owner's pid.
    lock = out.parent / (out.name + ".lock")
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            owner = int(lock.read_text().strip() or 0)
            os.kill(owner, 0)
            print(f"in progress elsewhere (pid {owner}): {out}")
            return 0
        except (ValueError, ProcessLookupError, PermissionError):
            lock.unlink(missing_ok=True)                 # stale lock from a dead signer
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    try:
        return _sign(a, run, out)
    finally:
        lock.unlink(missing_ok=True)


def _sign(a, run: Path, out: Path) -> int:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    t0 = time.monotonic()
    top = design_name(run)
    res = {"design_dir": str(run), "top": top, "deck_drc_sha256": sha(DECK_DIR / "drc/drcRules_calibre_asap7.rul"),
           "deck_lvs_sha256": sha(DECK_DIR / "lvs/lvsRules_calibre_asap7.rul"), "drc_mode": "flat",
           "drc_groups": {"A": f"{len(RULES) - 1} rule checks", "B": SLOW}, "drc_budget_s": a.drc_budget}
    try:
        res["inputs"] = prepare(run, out / "inputs", top)
    except Exception as exc:                                   # noqa: BLE001
        res.update(drc="failed", lvs="failed", error=f"prepare: {exc}", wall_s=round(time.monotonic() - t0, 1))
        (out / "signoff.json").write_text(json.dumps(res, indent=1))
        return 1
    gds = out / "inputs/drc.gds"
    pa = start_drc(out / "drc_A", gds, [r for r in RULES if r != SLOW], a.drc_budget)
    pb = start_drc(out / "drc_B", gds, [SLOW], a.drc_budget)
    res["lvs_detail"] = run_lvs(out / "lvs", out / "inputs/lvs.gds", out / "inputs/source.cdl", top, a.lvs_budget)
    res["lvs"] = res["lvs_detail"]["verdict"]
    ta = tb = None
    stopped_b = False
    while pa.poll() is None or pb.poll() is None:
        time.sleep(5)
        now = round(time.monotonic() - t0, 1)
        if ta is None and pa.poll() is not None:
            ta = now
            ca = drc_counts(out / "drc_A")
            if ca is not None and sum(ca.values()) > 0 and not a.keep_going and pb.poll() is None:
                stop(pb)
                stopped_b = True
        if tb is None and pb.poll() is not None:
            tb = now
    ta = ta if ta is not None else round(time.monotonic() - t0, 1)
    tb = tb if tb is not None else round(time.monotonic() - t0, 1)
    ca, cb = drc_counts(out / "drc_A"), (None if stopped_b else drc_counts(out / "drc_B"))
    results = {**(ca or {}), **(cb or {})}
    violating = {k: v for k, v in results.items() if v}
    if ca is None:
        # group A never wrote its report: a budget expiry is "timeout" (no verdict), anything else "failed".
        # Neither is clean. 2026-11-17: 7 large designs ran out the 6 h budget at op 664/1009.
        drc = "timeout" if pa.returncode in (124, 137) else "failed"
    elif violating:
        drc = "violations"
    elif cb is None:
        drc = "unknown"                  # A clean, B unfinished: never clean
    else:
        drc = "clean"
    res.update(drc=drc, drc_results=sum(results.values()), drc_violations=violating,
               drc_rules_reported=len(results), drc_group_a={"exit": pa.returncode, "complete": ca is not None,
                                                             "wall_s": ta},
               drc_group_b={"exit": pb.returncode, "complete": cb is not None, "wall_s": tb,
                            "stopped_after_group_a_violations": stopped_b},
               wall_s=round(time.monotonic() - t0, 1))
    (out / "signoff.json").write_text(json.dumps(res, indent=1))
    print(json.dumps({k: res[k] for k in ("top", "drc", "drc_results", "lvs", "wall_s")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
