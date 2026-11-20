#!/usr/bin/env python3
"""Freeze the Exp-4 v2 conversion-study test sample (seed 20261111).

30 designs per platform, uniform random without strata, from these pools:

  nangate45  memlab203:/data/r2g_n45_pilot500/select_cells.tsv (the pilot500
             designs, all dual-clean by construction). The pool is restricted to
             300-30,000 cells, the same range as the other two platforms. It
             excludes the 6 dev designs (dev_designs.json) and the 15 designs
             def-graph was debugged on (defgraph/check_set.json, the 3 edge
             cases). That way R2G is not scored on the designs whose failures
             produced its fixes. The 10-design confirmation set
             (confirm_set.json) received no fixes, so it stays in the pool.
  sky130hd   memlab216 rerun_sky130hd_20261022/runs
  gf180      memlab216 rerun_gf180_20261026/runs
             Verdict of record (rerun_tally.py): drc_status == clean, or a
             recovered 6_drc.lyrdb that is newer than the GDS and has 0 items;
             AND lvs_status == clean. The pool keeps dev-split designs
             (splits/dev.txt) with 300 <= result.json cells <= 30,000. Every
             kept run holds 6_final.{def,spef,v,sdc}, 5_route.odb, 3_place.odb,
             config.mk and constraint.sdc.

Inputs are frozen under sample_v2_inputs/. rows216.json comes from
collect216.py, which was run on 216 on 2026-11-07. Each platform draws with
its own random.Random(20261111) over the sorted pool, so a platform's draw does
not depend on the others.
"""
import hashlib, json, random
from pathlib import Path
H = Path(__file__).resolve().parent
SEED, N, LO, HI = 20261111, 30, 300, 30000
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()

dev6 = json.load(open(H / "dev_designs.json"))["designs"]
chk = json.load(open(H / "defgraph/check_set.json"))["designs"]
EDGE = ["C00180__gift_core__gift__nangate45__p2",
        "C02816__rggen_native_adapter__rggen-verilog-rtl__nangate45__p2",
        "C00293__tiled_dma_shell__axi4-systolic-array-accelerator__nangate45__p2"]
excl = set(dev6) | set(chk) | set(EDGE)
tsv = H / "sample_v2_inputs/select_cells.tsv"
cells = {n: int(c) for n, c in (l.split("\t") for l in tsv.read_text().splitlines() if l.strip())}
pools, meta = {}, {}
pools["nangate45"] = {n: dict(design=n, cells=c,
                              source_dir=f"memlab203:/data/r2g_n45_pilot500/nangate45_pilot500/{n}")
                      for n, c in cells.items() if LO <= c <= HI and n not in excl}
meta["nangate45"] = dict(source="memlab203:/data/r2g_n45_pilot500/select_cells.tsv",
                         source_sha256=sha(tsv), population=len(cells),
                         excluded_dev_or_defgraph_debug=sorted(excl),
                         pool=len(pools["nangate45"]), four_stage=True)

src = json.load(open(H / "sample_v2_inputs/rows216.json"))
dev = set(src["dev"])
for plat in ("sky130hd", "gf180"):
    rows = [r for r in src["rows"] if r["platform"] == plat]
    keep, why = {}, {}
    for r in rows:
        drc = r["drc_recovered"] or r["drc_status"]
        reason = ("not_dev" if r["key"] not in dev else
                  "not_dual_clean" if not (drc == "clean" and r["lvs_status"] == "clean") else
                  "cells_out_of_range" if not (isinstance(r["cells"], int) and LO <= r["cells"] <= HI) else
                  "missing_files" if r["missing"] else None)
        if reason:
            why[reason] = why.get(reason, 0) + 1
            continue
        keep[r["run"]] = dict(design=r["run"], key=r["key"], cells=r["cells"], period=r["period"],
                              drc_verdict=drc, drc_recovered=bool(r["drc_recovered"]),
                              drc_checker=r["drc_checker"],
                              source_dir=f"memlab216:{r['root']}/runs/{r['run']}")
    pools[plat] = keep
    meta[plat] = dict(source=f"memlab216:{rows[0]['root']}/runs", runs=len(rows),
                      dropped=why, pool=len(keep), four_stage=False)
meta["dev_split_sha256"] = src["dev_sha"]

out = dict(seed=SEED, n_per_platform=N, cell_range=[LO, HI], frozen="2026-11-07",
           generator="make_sample_v2.py",
           inputs={p.name: sha(p) for p in sorted((H / "sample_v2_inputs").iterdir())},
           meta=meta, platforms={})
for plat, pool in pools.items():
    pick = random.Random(SEED).sample(sorted(pool), N)
    out["platforms"][plat] = [pool[k] for k in pick]
    # per platform, 3 designs to prove the checker on R2G: the first three draws
    out["meta"][plat]["checker_proof"] = pick[:3]
assert not {d["design"] for d in out["platforms"]["nangate45"]} & excl
json.dump(out, open(H / "sample_v2.json", "w"), indent=1)
for p, v in out["platforms"].items():
    cs = sorted(d["cells"] for d in v)
    print(p, meta[p]["pool"], "cells min/med/max", cs[0], cs[len(cs)//2], cs[-1], meta[p].get("dropped"))
