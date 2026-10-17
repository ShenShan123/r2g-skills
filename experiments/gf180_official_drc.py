#!/usr/bin/env python3
"""gf180 DRC signoff through the PDK's OWN run_drc.py.

Replaces the 7884-line monolithic deck assembled for the pilot. That deck's
JUDGMENT was sound -- on C00006 it reported exactly the same 35 CO.6a as the
official runner's flat default -- but its EXECUTION was not: a single monolithic
pass timed out on 180 designs, where the official runner splits the work across
58 rule tables and finishes.

Three things the official runner does that the caller must respect:

  * It writes ONE lyrdb PER TABLE (`<stem>_<table>.lyrdb`), not one combined
    report. Reading a single file -- which `find ... | head -1` does -- reports
    whatever that one table found and silently drops the other 50+. That is how
    a run holding 35 CO.6a violations first read as "clean" here.
  * `--split_deep` is MEASURED LOSSY and stays off: it runs CO.6/6a/6b in deep
    mode and reported 0 CO.6a where the flat default reported 35 on the same
    design. The rule text is identical; only the execution mode differs.
  * `--antenna` is OFF by default. It is turned ON, because the pilot ran with
    antenna checks included and both other platforms check antennas by default.

Needs `docopt` and `klayout` (the Python module) on PYTHONPATH. The module is
only used to read the GDS for topcell resolution -- the verdict comes from
`klayout -b -r <deck>`, the same binary the pilot used (0.30.7).
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

D = Path("/proj/workarea/yangao")
PDK = D / "miniconda3/envs/eda/share/pdk"
RUNNER = PDK / "gf180mcuC/libs.tech/klayout/drc/run_drc.py"
IMAGE = "r2g-orfs:26Q3"
PYLIBS = D / "_pylibs"
VARIANT = "C"            # metal_top 9K / mim_option B / metal_level 5LM -- the
                         # combination the pilot corpus was graded with
ITEM = re.compile(r"<item>")


def grade(run_dir: Path, out_root: Path, timeout: int, mp: int,
          thr: int) -> dict:
    name = run_dir.name
    res = {"design": name, "drc_status": "skipped", "violations": None,
           "seconds": 0.0, "tables_nonempty": 0, "tables_total": 0}
    gds = run_dir / "6_final.gds"
    cfg = run_dir / "config.mk"
    if not gds.is_file() or not cfg.is_file():
        res["drc_status"] = "no_gds"
        return res
    m = re.search(r"DESIGN_NAME\s*=\s*(\S+)", cfg.read_text(errors="ignore"))
    if not m:
        res["drc_status"] = "no_top"
        return res
    top = m.group(1)
    out = out_root / name
    subprocess.run(["rm", "-rf", str(out)], check=False)
    out.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    cmd = ["podman", "run", "--rm", "--name", f"offdrc_{name}"[:60],
           "-v", f"{PDK}:{PDK}:ro,Z", "-v", f"{run_dir}:/src:ro,Z",
           "-v", f"{out}:/out:Z", "-v", f"{PYLIBS}:/pylibs:ro,Z",
           IMAGE, "bash", "-lc",
           "export PYTHONPATH=/pylibs; "
           f"python3 {RUNNER} --path=/src/6_final.gds --variant={VARIANT} "
           f"--topcell={top} --run_dir=/out --mp={mp} --thr={thr} --antenna"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        rc = r.returncode
        tail = ((r.stdout or "") + (r.stderr or ""))[-2000:]
    except subprocess.TimeoutExpired:
        subprocess.run(["podman", "rm", "-f", f"offdrc_{name}"[:60]],
                       capture_output=True, check=False)
        res["drc_status"] = "timeout"
        res["seconds"] = round(time.time() - t0, 1)
        return res
    res["seconds"] = round(time.time() - t0, 1)

    # Union EVERY table's report. A missing report set is not "clean".
    reports = sorted(glob.glob(str(out / "*.lyrdb")))
    res["tables_total"] = len(reports)
    if not reports:
        res["drc_status"] = "drc_failed"
        res["log_tail"] = tail[-400:]
        return res
    total = 0
    per_table = {}
    for f in reports:
        n = ITEM.findall(Path(f).read_text(errors="replace"))
        if n:
            per_table[Path(f).stem] = len(n)
            total += len(n)
    res["violations"] = total
    res["tables_nonempty"] = len(per_table)
    res["per_table"] = per_table
    res["drc_status"] = "clean" if total == 0 else "violations"
    if rc != 0 and total == 0:
        # exited non-zero yet every table empty: do not call that clean
        res["drc_status"] = "drc_failed"
        res["log_tail"] = tail[-400:]
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", required=True)
    ap.add_argument("--out", required=True, help="verdict json")
    ap.add_argument("--work", default=str(D / "_offdrc_work"))
    ap.add_argument("--workers", type=int, default=6,
                    help="designs in parallel; each uses --mp tables in parallel")
    ap.add_argument("--mp", type=int, default=4)
    ap.add_argument("--thr", type=int, default=2)
    ap.add_argument("--timeout", type=int, default=21600)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--select", default="", help="file of design dir names")
    args = ap.parse_args(argv)

    for need in (RUNNER, PYLIBS / "docopt.py", PYLIBS / "klayout"):
        if not Path(need).exists():
            print(f"ERROR 缺少 {need}", file=sys.stderr)
            return 2

    dirs = sorted(Path(p) for p in glob.glob(args.runs.rstrip("/") + "/*")
                  if Path(p).is_dir())
    if args.select:
        keep = {l.strip() for l in open(args.select) if l.strip()}
        dirs = [d for d in dirs if d.name in keep]
    if args.limit:
        dirs = dirs[:args.limit]
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    print(f"官方 run_drc.py 签核: {len(dirs)} 份, {args.workers} 并发 x "
          f"--mp {args.mp}, variant={VARIANT}, --antenna 开, --split_deep 关",
          flush=True)

    out_rows = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(grade, d, work, args.timeout, args.mp, args.thr)
                for d in dirs]
        for i, f in enumerate(futs, 1):
            r = f.result()
            out_rows.append(r)
            print(f"  [{i}/{len(dirs)}] {r['drc_status']:11s} "
                  f"viol={str(r['violations']):>6s} "
                  f"表 {r['tables_nonempty']}/{r['tables_total']} "
                  f"{r['seconds']:8.0f}s  {r['design'][:48]}", flush=True)
            Path(args.out).write_text(json.dumps(out_rows, indent=1) + "\n")
    c = collections.Counter(r["drc_status"] for r in out_rows)
    print(f"\n判决分布: {dict(c)}")
    secs = sorted(r["seconds"] for r in out_rows if r["seconds"])
    if secs:
        print(f"耗时 s: min={secs[0]:.0f} 中位={secs[len(secs)//2]:.0f} "
              f"max={secs[-1]:.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
