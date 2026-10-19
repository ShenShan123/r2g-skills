#!/usr/bin/env python3
"""Full Netgen LVS pass over the sky130hd corpus.

Calls r2g's own run_netgen_lvs.sh inside the ORFS container with the conda env
(magic, netgen, sky130A) mounted at its own path. The netlist comes from
read_lef + read_def because this corpus kept no 6_final.odb; the GDS is streamed
from the DEF first, the same def2stream step the DRC pass used.

Verdict parsing covers "Top level cell failed pin matching" as a FAILURE -- a
grep for "match uniquely|do not match" alone scored two detected corruptions as
no verdict when the negative controls were run.

Smallest first: runtime is near-linear (8 s at 1 cell, 48 s at 6,921), so the
tail cannot hold workers while the bulk waits, and a stalled giant is visible
as the only thing left.
"""
from __future__ import annotations

import argparse, collections, json, os, re, shutil, subprocess, sys, tempfile, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

IMAGE = "r2g-orfs:26Q3"
D = Path("/proj/workarea/yangao")
R2G = D / "r2g" / "signoff-loop"
CONDA = D / "miniconda3" / "envs" / "eda"

# Inside the container: stream the GDS out of the DEF, then hand the project to
# r2g's checker. conda goes LAST on PATH -- first, it shadows the image's
# openroad and _env.sh then reports "ORFS not found".
#
# Substituted with plain replace(), not str.format: the snippet's own `{ ...; }`
# shell groups are replacement fields to format() and it raises KeyError.
INNER = r"""
set -uo pipefail
export PATH=$PATH:@@CONDA@@/bin
V=@@VARIANT@@
R=$ORFS_ROOT/flow/results/@@PLAT@@/@@TOP@@/$V
mkdir -p "$R" $ORFS_ROOT/flow/logs/@@PLAT@@/@@TOP@@/$V \
         $ORFS_ROOT/flow/reports/@@PLAT@@/@@TOP@@/$V \
         $ORFS_ROOT/flow/objects/@@PLAT@@/@@TOP@@/$V
cp @@PROJ@@/backend/RUN_1/results/6_final.def "$R/"
cd $ORFS_ROOT/flow
make DESIGN_CONFIG=@@PROJ@@/constraints/config.mk FLOW_VARIANT=$V do-gds >/tmp/gds.log 2>&1 \
  || { echo R2G_FAIL=do_gds; tail -3 /tmp/gds.log; exit 1; }
cp "$R/6_final.gds" @@PROJ@@/backend/RUN_1/results/ 2>/dev/null
[ -s @@PROJ@@/backend/RUN_1/results/6_final.gds ] || { echo R2G_FAIL=no_gds; exit 1; }
bash /r2g/scripts/flow/run_netgen_lvs.sh @@PROJ@@ @@PLAT@@
"""


def _kill(name: str) -> None:
    subprocess.run(["podman", "rm", "-f", name], capture_output=True, timeout=120)


def verdict(text: str) -> str:
    """Map Netgen's wording onto a status. `failed pin matching` is a FAILURE."""
    if re.search(r"Circuits match uniquely|Netlists match uniquely", text):
        return "clean"
    if re.search(r"Circuits match with warnings", text):
        return "clean_with_warnings"
    if re.search(r"Top level cell failed pin matching", text):
        return "pin_mismatch"
    if re.search(r"do not match|\*\*MISMATCH\*\*", text):
        return "mismatch"
    return ""


def one(run_dir: Path, projroot: Path, timeout: int, platform: str) -> dict:
    out = {"design": run_dir.name, "lvs_status": "skipped", "cells": None,
           "seconds": 0.0}
    try:
        res = json.loads((run_dir / "result.json").read_text())
    except Exception as exc:                                     # noqa: BLE001
        out["lvs_status"] = "unreadable_result"; out["message"] = str(exc)[:120]
        return out
    out["cells"] = res.get("cells")
    if res.get("status") != "pass":
        out["lvs_status"] = "not_a_pass"; return out
    if not (run_dir / "6_final.def").is_file():
        out["lvs_status"] = "no_def"; return out

    t0 = time.time()
    proj = projroot / run_dir.name
    shutil.rmtree(proj, ignore_errors=True)
    (proj / "constraints").mkdir(parents=True)
    (proj / "backend" / "RUN_1" / "results").mkdir(parents=True)
    try:
        shutil.copy2(run_dir / "config.mk", proj / "constraints" / "config.mk")
        sdc = run_dir / "constraint.sdc"
        if sdc.is_file():
            shutil.copy2(sdc, proj / "constraints" / "constraint.sdc")
            p = proj / "constraints" / "config.mk"
            p.write_text(re.sub(r"(?m)^export SDC_FILE\s*=.*$",
                                f"export SDC_FILE = @@PROJ@@/constraints/constraint.sdc",
                                p.read_text()))
        shutil.copy2(run_dir / "6_final.def", proj / "backend" / "RUN_1" / "results")
        top = re.search(r"DESIGN_NAME\s*=\s*(\S+)",
                        (run_dir / "config.mk").read_text()).group(1)

        tag = f"ng_{abs(hash(run_dir.name)) % 10**9}"
        inner = INNER
        for key, val in (("@@CONDA@@", str(CONDA)), ("@@VARIANT@@", run_dir.name),
                         ("@@PLAT@@", platform), ("@@TOP@@", top),
                         ("@@PROJ@@", str(proj))):
            inner = inner.replace(key, val)
        try:
            r = subprocess.run(
                ["podman", "run", "--rm", "--name", tag,
                 "-v", f"{R2G}:/r2g:ro,Z",
                 "-v", f"{projroot}:{projroot}:Z",
                 "-v", f"{CONDA}:{CONDA}:ro,Z",
                 "-e", "NUM_CORES=4",
                 "-e", f"MAGIC_EXE={CONDA}/bin/magic",
                 "-e", f"NETGEN_EXE={CONDA}/bin/netgen",
                 "-e", f"PDK_ROOT={CONDA}/share/pdk",
                 IMAGE, "bash", "-lc", inner],
                capture_output=True, text=True, timeout=timeout)
            log = (r.stdout or "") + (r.stderr or "")
        except subprocess.TimeoutExpired:
            _kill(tag)
            out["lvs_status"] = "timeout"
            out["seconds"] = round(time.time() - t0, 1)
            return out

        for extra in ("lvs/netgen_lvs.log", "lvs/netgen_lvs.rpt"):
            f = proj / extra
            if f.is_file():
                log += "\n" + f.read_text(errors="replace")[-40000:]
        (run_dir / "netgen_lvs.log").write_text(log[-30000:])
        v = verdict(log)
        if v:
            out["lvs_status"] = v
        elif (m := re.search(r"R2G_FAIL=(\S+)", log)):
            out["lvs_status"] = f"failed_{m.group(1)}"
        else:
            # the script's own json is the last word on why nothing was graded
            try:
                j = json.loads((proj / "lvs" / "netgen_lvs_result.json").read_text())
                out["lvs_status"] = f"error_{j.get('reason', 'unknown')}"
            except Exception:                                    # noqa: BLE001
                out["lvs_status"] = "no_verdict"
        for art in ("lvs/netgen_lvs.rpt",):
            f = proj / art
            if f.is_file():
                shutil.copy2(f, run_dir / "netgen_lvs.rpt")
    finally:
        shutil.rmtree(proj, ignore_errors=True)

    out["seconds"] = round(time.time() - t0, 1)
    return out


def record(run_dir: Path, out: dict) -> dict:
    """Write the verdict into result.json. Called for EVERY outcome.

    one() returns early on not_a_pass, no_def and timeout. In the DRC pass built
    from this same template those paths bypassed a write-back that lived at the
    end of one(), and three designs that hit the signoff cap vanished from the
    funnel -- the log said `timeout 7229s`, result.json said nothing. Recording
    from the wrapper means no branch can skip it.
    """
    if out.get("lvs_status") in (None, "", "not_a_pass", "unreadable_result"):
        return out          # nothing was graded; leave the record untouched
    try:
        cur = json.loads((run_dir / "result.json").read_text())
        cur.update(lvs_status=out["lvs_status"],
                   lvs_seconds=out.get("seconds"),
                   lvs_checker="r2g_run_netgen_lvs.sh",
                   lvs_netlist_source="lef_def")
        fd, tmp = tempfile.mkstemp(dir=str(run_dir))
        with os.fdopen(fd, "w") as fh:
            json.dump(cur, fh, indent=1); fh.write("\n")
        os.replace(tmp, run_dir / "result.json")
    except Exception as exc:                                     # noqa: BLE001
        out["message"] = f"result.json update failed: {exc}"[:200]
    return out


def one_and_record(run_dir: Path, projroot: Path, timeout: int,
                   platform: str) -> dict:
    return record(run_dir, one(run_dir, projroot, timeout, platform))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", default=str(D / "full_out" / "runs"))
    ap.add_argument("--projroot", default=str(D / "lvs130_proj"))
    ap.add_argument("--out", default=str(D / "full_out" / "netgen_lvs.json"))
    ap.add_argument("--platform", default="sky130hd")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--timeout", type=int, default=5400)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    projroot = Path(args.projroot); projroot.mkdir(parents=True, exist_ok=True)
    todo = []
    for d in sorted(Path(args.runs).iterdir()):
        f = d / "result.json"
        if not f.is_file():
            continue
        try: res = json.loads(f.read_text())
        except Exception: continue                               # noqa: BLE001
        if res.get("status") != "pass":
            continue
        if res.get("lvs_status") and not args.force:
            continue
        todo.append((res.get("cells") or 0, d))
    todo.sort(key=lambda t: t[0])
    if args.limit:
        todo = todo[:args.limit]
    print(f"{time.strftime('%H:%M:%S')} {args.platform} Netgen LVS: {len(todo)} "
          f"designs, {args.workers} workers, smallest-first", flush=True)

    done = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for r in ex.map(lambda t: one_and_record(t[1], projroot, args.timeout, args.platform),
                        todo):
            done.append(r)
            print(f"{time.strftime('%H:%M:%S')} {r['lvs_status']:<22} "
                  f"cells={str(r['cells']):<8} {r['seconds']:>6.0f}s "
                  f"{r['design'][:40]}", flush=True)
            Path(args.out).write_text(json.dumps(done, indent=1) + "\n")

    print(f"\n  LVS {dict(collections.Counter(r['lvs_status'] for r in done))}")


if __name__ == "__main__":
    main()
