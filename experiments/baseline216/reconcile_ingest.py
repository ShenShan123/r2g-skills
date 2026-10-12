#!/usr/bin/env python3
"""Ingest-bridge acceptance oracle for the 2026-10 three-platform campaign.

WHY THIS EXISTS
---------------
The campaign drivers (chain45.sh / chain7.sh / chain180.sh -> orfs_baseline.py,
signoff_pass.py, lvs130.py) write per-design `result.json`. The learning loop
reads `reports/{ppa,drc,lvs,timing_check}.json` + `stage_log.jsonl`. The bridge
closes that gap -- and the way a bridge fails is NOT an empty table, it is a
full table holding wrong values (same failure mode as the dataset side's
"plausible CSV with wrong values", CLAUDE.md "Silent-value defect catalog").

So the expected numbers are measured ONCE from the raw corpus, frozen here, and
re-checked after ingest. Two modes:

  measure   read the corpus, print/emit the expected counts (run on host 216)
  verify    compare a knowledge.sqlite against the frozen expectations

MEASURED FACTS THIS ENCODES (2026-10-11/12, host 216)
-----------------------------------------------------
* KLayout `<value>` is NOT the violation count. nangate45 measured
  item 6652 / value 46546 = exactly 7.00x, because an antenna item carries 7
  values (the deck's adiodes/diode_factors/ametal/max_ratio/ratio/agate tags).
  gf180 = 1.02x, sky130hd = 1.00x. ORFS flow/Makefile:711 and
  run_drc.sh:311 both count `<value>`, so a bridge that trusts
  result.json's `drc_violations_signoff` on nangate45 inflates 7x.
  The bridge must carry the ITEM count.
* `<category>` is quoted for some rules and bare for others --
  `<category>'METAL1.5'</category>` vs `<category>METAL3_ANTENNA</category>`.
  A regex that only matches the quoted form silently reports 9 antenna classes
  as 1 class. (Cost me one wrong answer; hence CATEGORY_RE.)
* gf180's 54 `no_log_harvested` are NOT a harvest failure: they are the SAME 54
  designs that read `synth_error` on nangate45 and sky130hd (set intersection
  54/54/54). The bridge must relabel them `synth_error`, else the learner gains
  a fictional failure class and loses 54 real synth failures.
* The frontend classes are corpus quality, not platform behaviour: the SAME
  designs fail on all three platforms (canonicalize 55/55/55 int 55,
  parse_error 52/52/52 int 52, 1_2_yosys 20/20/21 int 20). They belong to
  rtl-acquire's `synth-frontend-*` namespace, not to a signoff recipe.
"""
import argparse, json, os, re, sys, glob, collections

CORPORA = [("nangate45", "n45_full"), ("sky130hd", "full_out"), ("gf180", "gf180_full")]
# bare OR single-quoted rule name -- see module docstring
CATEGORY_RE = re.compile(r"<category>'?([^'<]+?)'?</category>")
FRONTEND = {"1_1_yosys_canonicalize_error", "parse_error", "1_2_yosys_error", "synth_error"}
BACKEND = {"timeout", "place_failed", "route_failed", "oom", "oversize",
           "2_1_floorplan_error", "5_1_grt_error"}


def measure(runs_root):
    out = {}
    for plat, d in CORPORA:
        root = os.path.join(runs_root, d, "runs")
        if not os.path.isdir(root):
            continue
        rec = {"flow": collections.Counter(), "drc_verdict": collections.Counter(),
               "lvs_verdict": collections.Counter(), "drc_class": collections.Counter(),
               "drc_class_designs": collections.Counter(),
               "fail_class": collections.Counter(), "items": 0, "values": 0,
               "designs": 0}
        for rj in glob.glob(os.path.join(root, "*", "result.json")):
            try:
                j = json.load(open(rj))
            except Exception:
                continue
            rec["designs"] += 1
            st = j.get("status")
            rec["flow"][st] += 1
            if st != "pass":
                rec["fail_class"][(j.get("failure_class") or "").strip() or "(empty)"] += 1
                continue
            rec["drc_verdict"][j.get("drc_status") or "(none)"] += 1
            rec["lvs_verdict"][j.get("lvs_status") or "(none)"] += 1
            ly = os.path.join(os.path.dirname(rj), "6_drc.lyrdb")
            if os.path.isfile(ly):
                t = open(ly, errors="replace").read()
                rec["items"] += t.count("<item>")
                rec["values"] += t.count("<value>")
                cs = CATEGORY_RE.findall(t)
                for c in cs:
                    rec["drc_class"][c] += 1
                for c in set(cs):
                    rec["drc_class_designs"][c] += 1
        out[plat] = {k: (dict(v) if isinstance(v, collections.Counter) else v)
                     for k, v in rec.items()}
    return out


def cross_platform(runs_root):
    """The identity checks: which failures are the SAME designs across platforms."""
    per = {}
    for plat, d in CORPORA:
        root = os.path.join(runs_root, d, "runs")
        f = collections.defaultdict(set)
        for rj in glob.glob(os.path.join(root, "*", "result.json")):
            try:
                j = json.load(open(rj))
            except Exception:
                continue
            if j.get("status") == "pass":
                continue
            key = re.sub(r"__" + plat + r"__p\d+$", "",
                         os.path.basename(os.path.dirname(rj)))
            f[(j.get("failure_class") or "").strip()].add(key)
        per[plat] = f
    n45, s130, gf = per.get("nangate45", {}), per.get("sky130hd", {}), per.get("gf180", {})
    res = {}
    for fc in ("1_1_yosys_canonicalize_error", "parse_error", "1_2_yosys_error"):
        a, b, c = n45.get(fc, set()), s130.get(fc, set()), gf.get(fc, set())
        res[fc] = {"nangate45": len(a), "sky130hd": len(b), "gf180": len(c),
                   "all_three": len(a & b & c)}
    g, a, b = gf.get("no_log_harvested", set()), n45.get("synth_error", set()), s130.get("synth_error", set())
    res["gf180_no_log_harvested_is_synth_error"] = {
        "gf180_no_log_harvested": len(g), "nangate45_synth_error": len(a),
        "sky130hd_synth_error": len(b), "all_three": len(g & a & b)}
    return res


def verify(expected, db_path):
    """Re-check a post-ingest knowledge.sqlite against the frozen expectations."""
    import sqlite3
    conn = sqlite3.connect(db_path)
    q = lambda s, *a: conn.execute(s, a).fetchall()
    bad = []

    def check(name, got, want):
        ok = got == want
        print(f"  [{'OK  ' if ok else 'FAIL'}] {name}: got={got} want={want}")
        if not ok:
            bad.append(name)

    for plat, exp in expected["per_platform"].items():
        n = q("select count(*) from runs where platform=?", plat)[0][0]
        check(f"{plat}: runs ingested", n, exp["designs"])
        nf = q("select count(*) from runs where platform=? and orfs_status='fail'", plat)[0][0]
        check(f"{plat}: fail runs", nf, exp["flow"].get("fail", 0) or
              sum(v for k, v in exp["flow"].items() if k != "pass"))
        # the 7x trap: violations must be the ITEM count, never the value count
        for cls, cnt in sorted(exp["drc_class"].items(), key=lambda x: -x[1])[:3]:
            got = q("select count(*) from run_violations rv join runs r on r.run_id=rv.run_id "
                    "where r.platform=? and rv.violation_class=?", plat, cls)[0][0]
            check(f"{plat}: run_violations {cls}", got, cnt)

    # the relabel: a fictional class must not exist
    n = q("select count(*) from failure_events where signature like '%no_log_harvested%'")[0][0]
    check("no_log_harvested must not survive the bridge", n, 0)

    # CLAUDE.md honesty invariant H1
    a = q("select count(*) from runs where orfs_status='fail'")[0][0]
    b = q("select count(distinct run_id) from failure_events where signature like 'orfs-fail-%'")[0][0]
    check("every fail run carries an orfs-fail-% event", b, a)

    print("\n" + ("ALL RECONCILIATION CHECKS GREEN." if not bad
                  else f"{len(bad)} CHECK(S) FAILED: {bad}"))
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("measure", "verify"))
    ap.add_argument("--runs-root", default="/proj/workarea/yangao",
                    help="parent of n45_full/ full_out/ gf180_full/")
    ap.add_argument("--out", default=None, help="measure: write JSON here")
    ap.add_argument("--expected", default=None, help="verify: the JSON from measure")
    ap.add_argument("--db", default=None, help="verify: knowledge.sqlite to check")
    a = ap.parse_args()

    if a.mode == "measure":
        exp = {"per_platform": measure(a.runs_root), "cross_platform": cross_platform(a.runs_root)}
        for plat, r in exp["per_platform"].items():
            ratio = r["values"] / r["items"] if r["items"] else 0.0
            r["value_per_item"] = round(ratio, 4)
            print(f"\n### {plat}: {r['designs']} designs  flow={r['flow']}")
            print(f"  DRC verdicts {r['drc_verdict']}")
            print(f"  LVS verdicts {r['lvs_verdict']}")
            print(f"  lyrdb item={r['items']} value={r['values']} ratio={ratio:.2f}x"
                  + ("   <-- 7x antenna inflation, bridge MUST use item" if ratio > 1.5 else ""))
            print(f"  DRC classes ({len(r['drc_class'])}): "
                  + ", ".join(f"{k}={v}" for k, v in
                              sorted(r['drc_class'].items(), key=lambda x: -x[1])[:6]))
            back = {k: v for k, v in r["fail_class"].items() if k in BACKEND}
            front = {k: v for k, v in r["fail_class"].items() if k in FRONTEND}
            print(f"  backend-fixable {sum(back.values())} {back}")
            print(f"  frontend/corpus {sum(front.values())} {front}")
        print("\n### cross-platform identity")
        for k, v in exp["cross_platform"].items():
            print(f"  {k}: {v}")
        if a.out:
            json.dump(exp, open(a.out, "w"), indent=1, sort_keys=True)
            print(f"\nwrote {a.out}")
        return 0

    if not (a.expected and a.db):
        ap.error("verify needs --expected and --db")
    return verify(json.load(open(a.expected)), a.db)


if __name__ == "__main__":
    sys.exit(main())
