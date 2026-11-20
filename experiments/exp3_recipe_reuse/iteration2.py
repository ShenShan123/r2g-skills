#!/usr/bin/env python3
"""Iteration 2 of the Exp3 loop, simulated (no OpenROAD): the designs R2G did NOT fix in iteration 1 (Exp3) but an
LLM fallback DID are met again from their baseline failure. R2G's pipeline is deterministic given the job (Exp3
replay: identical jobs, 46/47 identical verdicts), so it reaches the same stop state; there it now queries the
knowledge base accumulated from iteration 1 (simulate.py stage C: only gate-passing recipes verified on >= 2
independent units are offered) through its real selection path.

Per fallback model m (the LLM that both orchestrates R2G and runs the fallback in that arm):
  iteration 1 cost = R2G orchestration by m (measured) + m's fallback attempt (measured)
  iteration 2 cost = R2G orchestration by m (same job: same cost)  -- if R2G selects a recipe that is
                     FIX-EQUIVALENT to the design's own verified fix (identical edits + byte-identical hooks), the
                     fallback is not needed: the recorded verified signoff of that identical layout applies;
                   = iteration 1 cost otherwise (the fallback is still needed; a selected-but-different recipe is
                     charged its extra flow and counted as a failed reuse).
  iteration2.py --sim RESULTS.json --tally-inputs DIR --tally-csv CSV [--scenario same_or_similar|similar]
"""
import argparse, collections, csv, json, statistics

FX = 7.10
ap = argparse.ArgumentParser()
ap.add_argument("--sim", required=True); ap.add_argument("--tally-inputs", required=True); ap.add_argument("--tally-csv", required=True)
ap.add_argument("--scenario", default="same_or_similar")
a = ap.parse_args()
sim = json.load(open(a.sim))["second_both_" + a.scenario]["rows"]
tal = {r["id"]: r for r in csv.DictReader(open(a.tally_csv))}
r2g = {}
for f in ("r2g_216.json", "r2g_203.json"):
    for k, v in json.load(open(a.tally_inputs + "/" + f)).items():
        if v.get("status") == "complete":
            r2g[k] = v
rep = {}
for f in ("r2g_gpt_replay_216.json", "r2g_gpt_replay_203.json"):
    for k, v in json.load(open(a.tally_inputs + "/" + f)).items():
        if v.get("status") == "complete" and v.get("complete_usage") is not False:
            rep[k] = v


def cost_ds(r):
    c = r.get("cached") or 0; u = r.get("uncached") if r.get("uncached") is not None else max((r.get("inp") or 0) - c, 0)
    return (u * 2.0 + c * 0.04 + (r.get("outp") or 0) * 8.0) / 1e6 / FX


def cost_gpt(r):
    c = r.get("cached") or 0; u = r.get("uncached") if r.get("uncached") is not None else max((r.get("inp") or 0) - c, 0)
    return (u * 2.0 + c * 0.10 + (r.get("outp") or 0) * 10.0) / 1e6


pop = {k: v for k, v in tal.items() if v["r2g"] == "not_fixed" and "fixed" in (v["r2g_plus_fallback"], v["r2g_gpt_plus_fallback"])}
fixed_by = {k: tuple(m for m, col in (("DeepSeek", "r2g_plus_fallback"), ("GPT", "r2g_gpt_plus_fallback")) if v[col] == "fixed") for k, v in pop.items()}
row = {(r["case_id"], r["model"]): r for r in sim}
out = {"population": len(pop), "fixed_by": collections.Counter("+".join(v) for v in fixed_by.values()),
       "per_platform": collections.Counter(k.split("_")[0] for k in pop)}
per_design = []
for m in ("DeepSeek", "GPT"):
    designs = sorted(k for k, v in fixed_by.items() if m in v)
    it1 = collections.Counter(); it2 = collections.Counter(); sel = collections.Counter()
    for d in designs:
        s = row.get((d, m)) or {}
        rc = r2g.get(d) if m == "DeepSeek" else rep.get(d)
        r2g_calls, r2g_tok = (rc or {}).get("calls") or 0, (rc or {}).get("tokens") or 0
        r2g_usd = (cost_ds if m == "DeepSeek" else cost_gpt)(rc) if rc else 0.0
        fb = s.get("llm_cost") or {}
        i1 = {"calls": r2g_calls + fb.get("calls", 0), "tokens": r2g_tok + fb.get("tokens", 0), "usd": r2g_usd + fb.get("usd", 0),
              "fallbacks": 1, "fallback_flows": fb.get("flows", 0), "fallback_wall_h": fb.get("wall_h", 0)}
        status = "no recipe selected" if s.get("status") != "applied" else ("selected, fix-equivalent" if s.get("verified") else "selected, different fix")
        sel[status] += 1
        if s.get("verified"):
            rc2 = s.get("reuse_cost") or {}
            i2 = {"calls": r2g_calls, "tokens": r2g_tok, "usd": r2g_usd, "fallbacks": 0, "fallback_flows": 0,
                  "fallback_wall_h": 0, "reuse_flow_h": rc2.get("wall_h", 0)}
        else:
            i2 = dict(i1, reuse_flow_h=(s.get("reuse_cost") or {}).get("wall_h", 0) if s.get("status") == "applied" else 0)
        for k, v in i1.items(): it1[k] += v
        for k, v in i2.items(): it2[k] += v
        per_design.append({"design": d, "model": m, "status": status, "recipe": s.get("recipe"), "symptom": s.get("symptom"),
                           "it1": i1, "it2": i2})
    out[m] = {"designs": len(designs), "selection": dict(sel), "iteration1": dict(it1), "iteration2": dict(it2)}
out["per_design"] = per_design
print(json.dumps({k: v for k, v in out.items() if k != "per_design"}, indent=1, default=str))
json.dump(out, open(a.sim.replace("results.json", "iteration2_%s.json" % a.scenario), "w"), indent=1, default=str)
