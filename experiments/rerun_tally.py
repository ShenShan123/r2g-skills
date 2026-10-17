#!/usr/bin/env python3
"""Tally the frozen baseline re-run, reported dev / held-out separately.

Verdict of record is each run's result.json, not the aggregate *.json files.
Those are per-pass snapshots: full_out/netgen_lvs.json held 1475 rows against
2497 designs in the pilot, and reading it as the LVS truth undercounted
sky130hd by 669 designs.

"No verdict is not a pass" -- a timeout, a crashed checker and a design with no
layout are all NOT clean, which is how the pilot recorded them, so the frozen
number stays on the same ruler as its history.

dev and held-out are reported apart and never summed into one headline. The
split exists so a repair developed against dev can be claimed on held-out; a
single combined figure would make that claim unauditable.
"""
import argparse
import collections
import glob
import json
from pathlib import Path

D = Path("/proj/workarea/yangao")
PLATS = ("nangate45", "gf180", "sky130hd")


def load_split(name):
    f = D / "splits" / f"{name}.txt"
    return {l.strip() for l in f.read_text().splitlines() if l.strip()}


def design_key(run_dir_name):
    # runs/<design>__<platform>__p<period>  ->  <design>
    return "__".join(run_dir_name.split("__")[:-2])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stamp", required=True)
    ap.add_argument("--roots", default="",
                    help="override: comma-separated run roots")
    args = ap.parse_args(argv)

    dev, held = load_split("dev"), load_split("held_out")
    roots = ({p: D / r for p, r in
              (x.split("=") for x in args.roots.split(","))} if args.roots
             else {p: D / f"rerun_{p}_{args.stamp}" for p in PLATS})

    print(f"\n=== 冻结 baseline 重跑汇总  stamp={args.stamp}")
    print(f"划分: dev {len(dev)} 份 / held-out {len(held)} 份\n")
    hdr = (f"{'平台':11s} {'集':8s} {'产出':>6s} {'双清':>6s} "
           f"{'DRC清':>6s} {'LVS清':>6s} {'无判决':>7s} {'PDN回退':>8s}")
    print(hdr)
    grand = {}
    for plat in PLATS:
        root = roots.get(plat)
        if not root or not root.is_dir():
            print(f"{plat:11s} (缺 {root})")
            continue
        rows = []
        for f in glob.glob(str(root / "runs" / "*" / "result.json")):
            try:
                d = json.load(open(f))
            except Exception:
                continue
            d["_key"] = design_key(Path(f).parent.name)
            rows.append(d)
        for label, keys in (("dev", dev), ("held-out", held)):
            sub = [d for d in rows if d["_key"] in keys]
            if not sub:
                continue
            drc = sum(1 for d in sub if d.get("drc_status") == "clean")
            lvs = sum(1 for d in sub if d.get("lvs_status") == "clean")
            both = sum(1 for d in sub if d.get("drc_status") == "clean"
                       and d.get("lvs_status") == "clean")
            # no verdict: the checker never produced one. NOT a pass.
            nov = sum(1 for d in sub
                      if d.get("drc_status") in (None, "timeout", "drc_failed",
                                                 "no_gds", "skipped")
                      or d.get("lvs_status") in (None, "timeout", "lvs_failed",
                                                 "no_gds", "skipped"))
            # The PDN-0185 fallback retries a floorplan abort with an
            # explicit square core. This baseline writes an explicit die that
            # is ALREADY floored at spec-build time, so the abort it rescues
            # should largely not happen any more -- which is a prediction, not
            # a fact, so it is counted rather than assumed.
            #
            # The comparable pilot figure for THIS column -- designs needing
            # the fallback, all outcomes -- is 506 nangate45 / 374 gf180 / 282
            # sky130hd. (The 492/367/278 quoted elsewhere is a different
            # measurement: fallback designs among the DUAL-CLEAN only, which is
            # the stock-tool adjustment in R2G_CONTRIBUTIONS.md. Do not compare
            # the two.) A re-run count near 506/374/282 means the explicit die
            # did not pre-empt the abort it was supposed to.
            fb = sum(1 for d in sub if d.get("pdn_fallback"))
            print(f"{plat:11s} {label:8s} {len(sub):6d} {both:6d} "
                  f"{drc:6d} {lvs:6d} {nov:7d} {fb:8d}")
            grand[(plat, label)] = both
        unmatched = [d for d in rows if d["_key"] not in dev | held]
        if unmatched:
            print(f"{'':11s} {'(划分外)':8s} {len(unmatched):6d}"
                  f"  <- 这些不能作为任何结论的证据")

    print()
    for label in ("dev", "held-out"):
        tot = sum(v for (p, l), v in grand.items() if l == label)
        print(f"合计双清 ({label}): {tot}")
    print("\n（两个集不相加成一个数：划分存在的意义就是让 held-out 的声明可核）")

    for plat in PLATS:
        root = roots.get(plat)
        if not root or not root.is_dir():
            continue
        rows = [json.load(open(f)) for f in
                glob.glob(str(root / "runs" / "*" / "result.json"))
                if Path(f).is_file()]
        c = collections.Counter((d.get("drc_status"), d.get("lvs_status"))
                                for d in rows)
        print(f"\n  {plat} (drc, lvs) 组合 前 6:")
        for k, v in c.most_common(6):
            print(f"    {v:5d}  drc={k[0]}  lvs={k[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
