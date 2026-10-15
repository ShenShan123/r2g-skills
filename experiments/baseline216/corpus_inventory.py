#!/usr/bin/env python3
"""One table over a baseline corpus: per design, its metrics and its artifacts.

Shipping 49G of run directories without an index makes the recipient parse 2497
result.json files to learn what is in them. This writes that index once, next to
the data, so the corpus arrives self-describing.

Two columns deserve care because they are the ones a reader will mis-trust:

  * `drc_violations_signoff` is the SIGNOFF deck's count; `drc_violations` is
    the router's own. They answer different questions and routinely disagree --
    keeping both, named apart, is the point.
  * `has_*` records what is actually on disk, not what the flow should have
    produced. A design whose status reads `pass` but whose `has_spef` is 0 is a
    real condition the status field alone will not tell you.

  corpus_inventory.py <corpus_runs_dir> <out.csv> [--split split_frozen.json]
"""
import argparse, collections, csv, json, os, sys

FIELDS = ["design", "family", "platform", "period", "status", "stage",
          "failure_class", "seconds", "cells", "area_um2",
          "wns", "tns", "worst_slack", "timing_path_constrained",
          "clock_kind", "clock_port",
          "drc_status", "drc_violations_signoff", "drc_violations",
          "drc_seconds", "drc_checker",
          "lvs_status", "lvs_seconds", "lvs_checker", "lvs_netlist_source"]
ARTIFACTS = {"has_def": "6_final.def", "has_spef": "6_final.spef",
             "has_sdc": "6_final.sdc", "has_lyrdb": "6_drc.lyrdb",
             "has_lvs_rpt": "netgen_lvs.rpt", "has_config": "config.mk"}


def family(dirname):
    f = dirname.split("__")
    return f[2] if len(f) >= 3 else dirname


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs_dir")
    ap.add_argument("out_csv")
    ap.add_argument("--split", default=None)
    a = ap.parse_args()

    split = {}
    if a.split and os.path.exists(a.split):
        split = json.load(open(a.split)).get("families", {})

    rows, miss = [], 0
    for d in sorted(os.listdir(a.runs_dir)):
        p = os.path.join(a.runs_dir, d)
        if not os.path.isdir(p):
            continue
        rj = os.path.join(p, "result.json")
        rec = {}
        if os.path.exists(rj):
            try:
                rec = json.load(open(rj))
            except Exception:
                miss += 1
        else:
            miss += 1
        row = {k: rec.get(k, "") for k in FIELDS}
        row["design"] = rec.get("design") or d
        row["family"] = family(d)
        row["run_dir"] = d
        for col, fn in ARTIFACTS.items():
            row[col] = int(os.path.exists(os.path.join(p, fn)))
        sz = 0
        for root, _, fs in os.walk(p):
            for f in fs:
                try:
                    sz += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        row["bytes"] = sz
        if split:
            row["split"] = split.get(row["family"], "")
        rows.append(row)

    cols = ["run_dir"] + FIELDS + list(ARTIFACTS) + ["bytes"] + (["split"] if split else [])
    with open(a.out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    n = len(rows)
    st = collections.Counter(r["status"] for r in rows)
    dr = collections.Counter(r["drc_status"] for r in rows)
    lv = collections.Counter(r["lvs_status"] for r in rows)
    both = sum(1 for r in rows
               if r["drc_status"] in ("clean", "clean_beol") and r["lvs_status"] == "clean")
    print(f"写出 {a.out_csv}：{n} 行，{len(cols)} 列"
          + (f"（{miss} 份无 result.json）" if miss else ""))
    print(f"  流程   {dict(st.most_common(4))}")
    print(f"  DRC    {dict(dr.most_common(4))}")
    print(f"  LVS    {dict(lv.most_common(4))}")
    print(f"  双清   {both} / {n} ({100.0*both/n:.1f}%)")
    print(f"  产物齐 def={sum(r['has_def'] for r in rows)} "
          f"spef={sum(r['has_spef'] for r in rows)} "
          f"lyrdb={sum(r['has_lyrdb'] for r in rows)} "
          f"lvs={sum(r['has_lvs_rpt'] for r in rows)}")
    print(f"  总体积 {sum(r['bytes'] for r in rows)/2**30:.1f} GiB")
    if split:
        sp = collections.Counter(r["split"] for r in rows)
        print(f"  切分   {dict(sp)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
