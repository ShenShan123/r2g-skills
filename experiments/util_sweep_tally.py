#!/usr/bin/env python3
"""Per size class, the highest utilization at which >=80% of designs still fit.

Contention-proofing, which this measurement needs: the sweep shares the machine
with other campaigns, and a design killed by load would otherwise read as "could
not fit at this utilization" and drag the ceiling down. The two are
distinguishable by failure class:

  REAL utilization failure : place_failed / route_failed / DPL legalisation /
                             oversize -- the die genuinely could not hold it
  CONTENTION artefact      : timeout, or `Terminated` in the log tail -- a
                             signal, not a tool verdict; the driver's own kill
                             paths label themselves (oversize/timeout/
                             aborted_disk), so a kill wearing a STAGE name came
                             from outside the driver

Artefacts are excluded from the rate and listed for re-run, never counted as
either a pass or a fit-failure.
"""
import collections, glob, json, os
from pathlib import Path

D = Path("/proj/workarea/yangao")
PLAT = os.environ.get("PLAT", "nangate45")
BINS = [(0, 100), (100, 1000), (1000, 5000), (5000, 20000), (20000, 10**9)]
FIT_FAIL = {"place_failed", "route_failed", "oversize", "dpl_failed"}

root = {"nangate45": "n45_full", "gf180": "gf180_full",
        "sky130hd": "full_out"}[PLAT]
base = {}
for f in glob.glob(str(D / root / "runs" / "*" / "result.json")):
    try:
        d = json.load(open(f))
    except Exception:
        continue
    if d.get("cells"):
        base[os.path.basename(os.path.dirname(f))] = d["cells"]

# `{PLAT}_u*` also matches the scratch dirs `{PLAT}_u<N>_s`, whose suffix is not
# an int. util_sweep.sh removes each scratch after its point finishes, so this
# crashed ONLY when tallying a sweep that is still running -- which is exactly
# when a partial answer is wanted.
utils = sorted(int(p.name.split("_u")[-1])
               for p in (D / "_usweep").glob(f"{PLAT}_u*")
               if p.is_dir() and p.name.split("_u")[-1].isdigit())
res = collections.defaultdict(dict)
redo = []
for u in utils:
    for f in glob.glob(str(D / "_usweep" / f"{PLAT}_u{u}" / "runs" / "*" / "result.json")):
        n = os.path.basename(os.path.dirname(f))
        try:
            d = json.load(open(f))
        except Exception:
            continue
        ok = d.get("status") == "pass"
        fc = d.get("failure_class") or ""
        tail = d.get("log_tail") or ""
        artefact = (not ok) and (fc == "timeout" or "Terminated" in tail)
        if artefact:
            redo.append((u, n, fc))
            continue
        res[n][u] = (ok, fc)

print(f"\n=== {PLAT} 利用率扫描   利用率点 {utils}")
hdr = " ".join(f"{u:>6}%" for u in utils)
print(f"{'单元数区间':>14s} {'份数':>5s} {hdr} {'p80可达':>8s}")
for lo, hi in BINS:
    names = [n for n in res if lo <= base.get(n, -1) < hi]
    if not names:
        continue
    rates, cells = [], []
    for u in utils:
        have = [n for n in names if u in res[n]]
        ok = sum(1 for n in have if res[n][u][0])
        rates.append(ok / len(have) if have else None)
        cells.append(f"{ok}/{len(have)}")
    good = [u for u, r in zip(utils, rates) if r is not None and r >= 0.8]
    tgt = f"{max(good)}%" if good else "<最低"
    lab = f"{lo}-{hi if hi < 10**9 else 'inf'}"
    print(f"{lab:>14s} {len(names):5d} " + " ".join(f"{c:>7s}" for c in cells)
          + f" {tgt:>8s}")
print("\n(格子是 通过/有判决；p80可达 = 通过率仍 >=80% 的最高利用率)")

ff = collections.Counter()
for n, d in res.items():
    for u, (ok, fc) in d.items():
        if not ok:
            ff[(u, fc)] += 1
if ff:
    print("\n真实的塞不进去 (利用率, 原因):")
    for k, v in sorted(ff.items()):
        mark = "" if k[1] in FIT_FAIL else "   <- 不是布局/布线类，需人工看"
        print(f"  util={k[0]}%  {k[1]}  x{v}{mark}")
if redo:
    print(f"\n争抢导致、判决作废需重跑: {len(redo)} 份")
    for u, n, fc in redo[:8]:
        print(f"  util={u}%  {n[:50]}  ({fc})")
    with open(D / f"usweep_redo_{PLAT}.txt", "w") as fh:
        for u, n, fc in redo:
            fh.write(f"{u}\t{n}\n")
    print(f"  -> usweep_redo_{PLAT}.txt")
