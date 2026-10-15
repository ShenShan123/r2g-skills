#!/usr/bin/env python3
"""Harvest the designs where r2g's EXISTING recipes ran and still failed.

The point is not the repair rate -- it is the residue. A design that the matcher
had a strategy for, that the loop actually applied, and that still does not sign
off is evidence about a gap in r2g; a design the matcher declines by design is
evidence about a different gap. So each failure is reported with what was tried,
how many iterations it took, what is left, and which of four shapes it has:

  recipe_exhausted   strategies were applied, iterations ran, violations remain
  recipe_no_op       a strategy was proposed but zero iterations ran
  checker_incomplete the checker never produced a verdict (skipped/stuck/missing)
  flow_aborted       the flow never reached signoff

`residual_reason` is read from the loop's own diagnosis where present, rather
than inferred, so the report says what r2g said about itself.
"""
import argparse, collections, glob, json, os, sys


def read(p, name):
    try:
        return json.load(open(os.path.join(p, "reports", name)))
    except Exception:
        return None


def fixlog(p):
    f = os.path.join(p, "reports", "fix_log.jsonl")
    out = []
    if os.path.isfile(f):
        for ln in open(f, errors="replace"):
            try:
                out.append(json.loads(ln))
            except Exception:
                pass
    return out


def classify(drc, lvs, iters, strat):
    ds = (drc or {}).get("status")
    if ds in (None, "skipped", "stuck", "timeout", "missing", "no_drc_deck_for_platform"):
        return "checker_incomplete"
    if ds in ("clean", "clean_beol"):
        return None                      # not a failure
    if not strat:
        return "recipe_no_op"
    if iters == 0:
        return "recipe_no_op"
    return "recipe_exhausted"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("roots", nargs="+", help="staged corpus dirs to scan")
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args()
    rows = []
    for root in a.roots:
        for p in sorted(glob.glob(os.path.join(root, "*"))):
            if not os.path.isdir(os.path.join(p, "constraints")):
                continue
            if "_abA_" in p or "_abB_" in p:
                continue
            drc, lvs = read(p, "drc.json"), read(p, "lvs.json")
            diag = read(p, "diagnosis.json") or {}
            it = fixlog(p)
            strat = [x.get("strategy") for x in it
                     if x.get("strategy") and x["strategy"] != "none"]
            nflow = len(glob.glob(os.path.join(p, "backend", "RUN_*")))
            if nflow == 0:
                shape = "flow_aborted"
            else:
                shape = classify(drc, lvs, len(it), strat)
            if shape is None:
                continue
            cats = (drc or {}).get("categories") or {}
            rows.append({
                "design": os.path.basename(p),
                "root": os.path.basename(root),
                "shape": shape,
                "drc_status": (drc or {}).get("status"),
                "violations": (drc or {}).get("total_violations"),
                "classes": {k: (v or {}).get("count") for k, v in cats.items()},
                "lvs_status": (lvs or {}).get("status"),
                "strategies": list(dict.fromkeys(strat)),
                "fix_iters": len(it),
                "flows": nflow,
                "residual_reason": diag.get("residual_reason")
                                   or (drc or {}).get("reason"),
            })
    by = collections.Counter(r["shape"] for r in rows)
    print(f"扫描 {len(a.roots)} 个目录，失败 {len(rows)} 份")
    for k, v in by.most_common():
        print(f"  {k:19s} {v}")
    print()
    for shape in ("recipe_exhausted", "recipe_no_op", "checker_incomplete", "flow_aborted"):
        sel = [r for r in rows if r["shape"] == shape]
        if not sel:
            continue
        print(f"=== {shape} ({len(sel)}) ===")
        cls = collections.Counter()
        for r in sel:
            for k, v in (r["classes"] or {}).items():
                cls[k] += v or 0
        if cls:
            print(f"  残留违例按类: {dict(cls.most_common(6))}")
        st = collections.Counter(s for r in sel for s in r["strategies"])
        if st:
            print(f"  试过的策略: {dict(st)}")
        rr = collections.Counter(r["residual_reason"] for r in sel if r["residual_reason"])
        for k, v in rr.most_common(3):
            print(f"  r2g 自述: [{v}] {str(k)[:110]}")
        for r in sel[:6]:
            d = r["design"].split("__")
            nm = d[3] if len(d) > 3 else d[0]
            print(f"    {nm[:26]:28s} {str(r['drc_status']):8s} viol={str(r['violations']):>5s} "
                  f"it={r['fix_iters']} flow={r['flows']} {','.join(r['strategies'])[:28]}")
        print()
    if a.json_out:
        json.dump(rows, open(a.json_out, "w"), indent=1, ensure_ascii=False)
        print(f"wrote {a.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
