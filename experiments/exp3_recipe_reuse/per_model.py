#!/usr/bin/env python3
"""Iteration 2, evaluated PER FALLBACK MODEL with memoized outcomes (no new OpenROAD run).

For model m in {DeepSeek, GPT}: the knowledge base is accumulated ONLY from m's successful fallbacks of iteration 1
(Exp3) -- distill -> gate -> validate (>= 2 independent units). Every design m's fallback repaired is met again:
R2G (deterministic given the job) reaches its recorded stop state and runs its REAL selection + apply path against
m's store. The outcome of the resulting project state is then looked up, never guessed:
  recorded   the identical project state (same edits, byte-identical hooks, untouched RTL) was already signed off
             dual-clean in a verified fallback run of this design (either model's)       -> known success
  re-run     the 203 recipe-reuse re-run (R2G from the baseline with the same recipe) produced a final verdict
             for this design + recipe                                                       -> known (real) outcome
  pending    neither exists -> needs a real run; never counted as a success or a saving
Costs: iteration 1 = R2G orchestration by m (Exp3, measured) + m's fallback (measured). Iteration 2 = R2G
orchestration by m when the recipe is a known success; iteration-1 cost (fallback still needed) when no recipe is
selected or the recipe is a known failure. The memoized flow is not re-executed: its recorded flow time is reported.
  per_model.py --inputs DIR --tally-inputs DIR --tally-csv CSV --rerun-recipes J --rerun-verdicts J --out OUT.json
"""
import argparse, collections, json, sqlite3, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import simulate as S
lr = S.lr
FX = 7.10
MODELS = {"DeepSeek": "deepseek-flash", "GPT": "gpt-6.1-sol"}


def orch_cost(model, r):
    if not r:
        return {"calls": 0, "tokens": 0, "usd": 0.0}
    c = r.get("cached") or 0; u = r.get("uncached") if r.get("uncached") is not None else max((r.get("inp") or 0) - c, 0)
    usd = ((u * 2.0 + c * 0.04 + (r.get("outp") or 0) * 8.0) / 1e6 / FX) if model == "DeepSeek" else \
          ((u * 2.0 + c * 0.10 + (r.get("outp") or 0) * 10.0) / 1e6)
    return {"calls": r.get("calls") or 0, "tokens": r.get("tokens") or 0, "usd": usd}


def main():
    ap = argparse.ArgumentParser()
    for k in ("--inputs", "--tally-inputs", "--tally-csv", "--rerun-recipes", "--rerun-verdicts", "--out"):
        ap.add_argument(k, required=True)
    ap.add_argument("--splits-dev", default=str(S.REPO / "experiments" / "splits" / "dev.txt"))
    ap.add_argument("--population", choices=["fallback_fixed", "r2g_failed"], default="fallback_fixed",
                    help="fallback_fixed: designs m's fallback repaired; r2g_failed: every R2G failure m's fallback attempted")
    a = ap.parse_args()
    A = [x for x in S.build_attempts(a.inputs, a.tally_inputs, a.tally_csv) if not x.get("missing_inputs")]
    units = S.units_of([x["case_id"] for x in A], a.splits_dev, {x["case_id"]: x.get("rtl_hash") for x in A if x.get("rtl_hash")})
    by = {(x["case_id"], x["model"]): x for x in A}
    rr = json.load(open(a.rerun_recipes)); rv = json.load(open(a.rerun_verdicts))
    r2g, rep = {}, {}
    for f in ("r2g_216.json", "r2g_203.json"):
        r2g.update({k: v for k, v in json.load(open(a.tally_inputs + "/" + f)).items() if v.get("status") == "complete"})
    for f in ("r2g_gpt_replay_216.json", "r2g_gpt_replay_203.json"):
        rep.update({k: v for k, v in json.load(open(a.tally_inputs + "/" + f)).items()
                    if v.get("status") == "complete" and v.get("complete_usage") is not False})
    import csv
    col = {"deepseek-flash": "r2g_plus_fallback", "gpt-6.1-sol": "r2g_gpt_plus_fallback"}
    verdict = {r["id"]: r for r in csv.DictReader(open(a.tally_csv))}
    # population = the Exp3 tally's own verdict (e.g. a memory-bound design is not fixed whatever its attempt says)
    tally_fixed = lambda x: (verdict.get(x["case_id"]) or {}).get(col[x["model"]]) == "fixed"
    work = Path(tempfile.mkdtemp(prefix="per_model_"))
    res = {}
    for m, mid in MODELS.items():
        donors = [x for x in A if x["model"] == mid and "recipe" in x.get("distill", {})]
        db = work / ("store_%s.sqlite" % m); c = sqlite3.connect(db); lr.ensure_schema(c)
        for d in donors:
            lr.record(c, d["distill"]["recipe"], d["case_id"] + "/" + m, units[d["case_id"]], {})
        validated = [r[0] for r in c.execute(
            "SELECT recipe_id FROM learned_recipe_evidence WHERE kind='distilled' GROUP BY recipe_id "
            "HAVING COUNT(DISTINCT unit) >= ?", (lr.ADMIT_UNITS,))]
        c.close()
        rows = []
        pop_ok = (lambda x: x["fixed"] and tally_fixed(x)) if a.population == "fallback_fixed" else (lambda x: True)
        for t in sorted((x for x in A if x["model"] == mid and pop_ok(x)), key=lambda x: x["case_id"]):
            r = S.r2g_select_apply(t, db, work / ("proj_" + m))
            row = {"design": t["case_id"], "llm_fixed": bool(t["fixed"] and tally_fixed(t)),
                   "symptom": t["symptom"][0] if t.get("symptom") else None,
                   "selection": r["status"], "recipe": None, "outcome": None, "source": None}
            if r["status"] == "applied":
                rid = r["recipe"][len("learned:"):]
                rec = next(d["distill"]["recipe"] for d in donors if d["distill"]["recipe"]["recipe_id"] == rid)
                row["recipe"] = rid
                for other in (mid, "deepseek-flash" if mid != "deepseek-flash" else "gpt-6.1-sol"):
                    o = by.get((t["case_id"], other))
                    if o and o["fixed"] and S.fix_equivalent(r, rec, o)[0]:
                        row.update(outcome="success", source="recorded verified run (%s fallback)" % S.MODEL[other]); break
                if row["outcome"] is None:
                    lrn = [x for x in (rr.get(t["case_id"]) or {}).get("learned", []) if x["recipe"] == rid]
                    v = rv.get(t["case_id"]) or {}
                    if lrn and v.get("status") == "complete":
                        row.update(outcome="success" if v.get("dual_clean") else "failure",
                                   source="203 re-run (R2G from baseline, same recipe)")
                    else:
                        row.update(outcome="pending", source="no recorded run of this state yet")
                row["reuse_flow_h"] = S.reuse_cost(t, r.get("rerun_from"))["wall_h"]
            orch = orch_cost(m, r2g.get(t["case_id"]) if m == "DeepSeek" else rep.get(t["case_id"]))
            fb = {k: t["cost"][k] for k in ("calls", "tokens", "usd", "flows", "wall_h")}
            it1 = {"calls": orch["calls"] + fb["calls"], "tokens": orch["tokens"] + fb["tokens"], "usd": orch["usd"] + fb["usd"],
                   "fallbacks": 1, "fallback_flows": fb["flows"], "fallback_wall_h": fb["wall_h"]}
            it2 = dict(orch, fallbacks=0, fallback_flows=0, fallback_wall_h=0) if row["outcome"] == "success" else dict(it1)
            row.update(it1=it1, it2=it2)
            rows.append(row)
        def tot(key, sel=lambda r: True):
            return {k: sum(r[key][k] for r in rows if sel(r)) for k in rows[0][key]} if rows else {}
        known = lambda r: r["outcome"] != "pending"
        res[m] = {"store": {"donor_attempts": len(donors), "recipes": len({d["distill"]["recipe"]["recipe_id"] for d in donors}),
                            "validated": validated},
                  "designs": len(rows),
                  "selection": dict(collections.Counter(r["selection"] for r in rows)),
                  "outcome": dict(collections.Counter((r["outcome"] or "no recipe") + " / " + (r["source"] or "-") for r in rows)),
                  "repaired_directly": sum(r["outcome"] == "success" for r in rows),
                  "pending": sum(r["outcome"] == "pending" for r in rows),
                  "iteration1_all": tot("it1"), "iteration2_all": tot("it2"),
                  "iteration1_known": tot("it1", known), "iteration2_known": tot("it2", known),
                  "rows": rows}
    json.dump(res, open(a.out, "w"), indent=1, default=str)
    for m, v in res.items():
        print("==", m, {k: v[k] for k in ("designs", "store", "selection", "outcome", "repaired_directly", "pending")})
        for k in ("iteration1_all", "iteration2_all"):
            print("  ", k, {kk: round(vv, 3) for kk, vv in v[k].items()})


if __name__ == "__main__":
    main()
