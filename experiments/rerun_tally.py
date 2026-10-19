#!/usr/bin/env python3
"""Tally the frozen baseline re-run, reported dev / held-out separately.

Verdict of record is each run's result.json, not the aggregate *.json files.
Those are per-pass snapshots: full_out/netgen_lvs.json held 1475 rows against
2497 designs in the pilot, and reading it as the LVS truth undercounted
sky130hd by 669 designs.

"No verdict is not a pass" -- a timeout, a crashed checker and a design with no
layout are all NOT clean, which is how the pilot recorded them, so the frozen
number stays on the same ruler as its history.

TWO RULERS ON gf180, REPORTED SEPARATELY. This chain runs a second DRC pass
(the PDK's own run_drc.py) over designs the fast assembled deck could not grade;
the pilot ran no such pass, so 180 of its gf180 designs -- 86 in dev -- carry no
DRC verdict at all and count as not-clean. Backfilling them can only ADD clean
designs, up to +86 against a pilot dev baseline of 754, and none of that comes
from repair: it is the measurement improving. Each design records which checker
produced its verdict in `drc_checker`, so the tally reports dual-clean twice:

  same_ruler   verdicts from the assembled deck only (drc_checker is the fast
               deck or absent) -- directly comparable with the pilot
  full         every verdict including the official backfill -- the better
               measurement, not comparable with the pilot without saying so

Quote `same_ruler` against the pilot. Quote `full` as this run's own number.
Never quote one as the other.

A pass is `DRC clean AND LVS clean`. Timing is NOT a term: a design that misses
its period still passes if it is dual-clean, because the period is a pressure
knob rather than a gate (measured on the pilot: 237 sky130hd designs missed
their period and 224 of those were still dual-clean). Adding a `WNS >= 0` term
would silently change the ruler the pilot was measured with. Timing is reported
beside the verdict, never folded into it.

dev and held-out are reported apart and never summed into one headline. The
split exists so a repair developed against dev can be claimed on held-out; a
single combined figure would make that claim unauditable.

The headline pass rate is a NET of two parameter changes pulling opposite ways,
and must be reported decomposed or not at all. Measured on gf180's 1248
comparable dev designs:

    pilot pass 1115 (89.3%)  ->  frozen-spec pass 1123 (90.0%)   net +8

    +18  pilot timeout -> pass   the 3 h -> 6 h budget. 33 pilot timeouts,
                                 18 now finish; 15 of those took over 3 h, so
                                 the old ceiling was cutting them off, not the
                                 designs failing.
     +2  pilot place/route fail -> pass   a 20% utilization die is wrong in
                                 both directions; proper sizing recovers some.
    -12  pass -> place/route fail        the cost of 50% utilization and
                                 binned periods.

"The frozen spec raised the pass rate" is therefore true and misleading: the
pressure cost 12 designs and a separate change gained 18.

BEWARE THE PARTIAL-CORPUS ARTIFACT. The same decomposition over the first 363
designs read -6 (2 rescued, 8 lost), the opposite sign. The corpus runs in NAME
order and the rescued designs are overwhelmingly large, so an early slice is not
a random sample of it -- it is the small-design end, where almost no timeouts
live. Restricting the comparison to designs BOTH runs completed is necessary but
not sufficient; the completed set itself is biased until the run finishes. Do
not quote a mid-run rate as the corpus rate.
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


FRONTEND_MARKERS = ("yosys", "synth", "parse")
_ITEM = __import__("re").compile(r"<item>")


def recovered_drc(run_dir: Path, recorded: str):
    """A verdict that exists on disk but not in result.json.

    86 gf180 dev designs record drc_status='timeout' while a completed
    6_drc.lyrdb sits beside them, written AFTER the 6_final.gds it judges and
    with no repaired layout in the directory -- a re-run of the baseline DRC
    against the untouched baseline GDS whose result was never folded back.
    Counting those as "no verdict, therefore fail" is wrong: the verdict exists.

    Two gates, because "I found a file" is not evidence:
      * the report must be NEWER than the GDS it judges, or it describes some
        other layout
      * the method itself was controlled first -- on the 1029 designs where
        result.json HAS a verdict and a report exists, reading the report
        reproduces the recorded verdict 1029 times out of 1029.

    That control had to be redone once. The first version compared
    result.json's `drc_violations`, which is broken -- only 23 of ~1249 gf180
    designs carry a non-zero value, and C00006 records
    drc_status='violations' with drc_violations=0 while its log says 35. A
    control is only as good as the field it reads; drc_status is the field
    that carries the verdict.
    """
    if recorded in ("clean", "violations"):
        return None                      # already has a verdict
    rep, gds = run_dir / "6_drc.lyrdb", run_dir / "6_final.gds"
    if not (rep.is_file() and gds.is_file()):
        return None
    if rep.stat().st_mtime < gds.stat().st_mtime:
        return None
    try:
        n = len(_ITEM.findall(rep.read_text(errors="replace")))
    except Exception:                                            # noqa: BLE001
        return None
    return "clean" if n == 0 else "violations"


def is_frontend(d):
    """Synthesis never produced a netlist, so no layout exists to repair.

    These are RTL problems, platform-independent (the same designs fail on all
    three platforms), and signoff-loop has no lever on them. They are reported
    on their own line rather than folded into the denominator: folding them in
    makes the backend look worse than it is AND credits the repair arm with a
    pool it cannot touch.
    """
    blob = (str(d.get("stage") or "") + " " +
            str(d.get("failure_class") or "")).lower()
    return any(t in blob for t in FRONTEND_MARKERS)


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
    hdr = (f"{'平台':11s} {'集':8s} {'产出':>6s} {'后端分母':>8s} {'双清':>6s} "
           f"{'DRC清':>6s} {'LVS清':>6s} {'无判决':>7s} {'PDN回退':>8s} "
           f"{'前端失败':>8s} {'判决补齐':>8s}")
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
            rec = recovered_drc(Path(f).parent, d.get("drc_status"))
            if rec:
                d["drc_status"] = rec
                d["_drc_recovered"] = True
            rows.append(d)
        for label, keys in (("dev", dev), ("held-out", held)):
            sub = [d for d in rows if d["_key"] in keys]
            if not sub:
                continue
            # The official backfill is identifiable per design, so the
            # pilot-comparable count is recoverable without a second run.
            OFFICIAL = "pdk_run_drc.py"
            both_same = sum(1 for d in sub
                            if d.get("drc_status") == "clean"
                            and d.get("lvs_status") == "clean"
                            and d.get("drc_checker") != OFFICIAL)
            backfilled = sum(1 for d in sub
                             if d.get("drc_checker") == OFFICIAL
                             and d.get("drc_status") == "clean"
                             and d.get("lvs_status") == "clean")
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
            fe = sum(1 for d in sub if is_frontend(d))
            rc_ = sum(1 for d in sub if d.get("_drc_recovered"))
            backend = len(sub) - fe          # the honest denominator
            print(f"{plat:11s} {label:8s} {len(sub):6d} {backend:8d} {both:6d} "
                  f"{drc:6d} {lvs:6d} {nov:7d} {fb:8d} {fe:8d} {rc_:8d}")
            if backfilled:
                # Printed only when it is non-zero, so a run with no backfill
                # reads exactly as before and nobody has to subtract a zero.
                print(f"{'':11s} {'':8s} {'':6s} {'':8s} "
                      f"同尺 {both_same:5d}  官方补判另加 {backfilled:4d}"
                      f"   (与试跑比用「同尺」)")
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
