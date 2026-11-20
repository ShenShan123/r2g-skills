#!/usr/bin/env python3
"""Tally of the native-harness arms against Experiment 3, on the 150 sampled designs.

Rules (user, 2026-11-13):
  * the denominator is the 150 sampled designs for both arms;
  * the three huge designs (sky130hd_C02802, C04010, C04011) are counted as NOT fixed in both native arms, and their
    resource use is ESTIMATED from the Experiment 3 model-only run of the same design with the same model
    (DeepSeek-only for DeepSeek Harness, GPT-only for Codex), not from the partial native attempts;
  * outcome classes: dual_clean = fixed; pending_semantic_review = RTL edited, equivalence not yet reviewed (reported as
    its own column and as a bound); everything else = not fixed.
Experiment 3 columns are taken from tally_latest.csv restricted to the same 150 designs; the model-only resource
figures use the latest valid attempt per design (rule checked against the paper's all-676 averages by --check).
"""
import argparse
import collections
import csv
import glob
import json
import os
import statistics as st

GIANTS = ("sky130hd_C02802", "sky130hd_C04010", "sky130hd_C04011")
PRICE_CNY = {"uncached": 2.0, "cached": 0.04, "output": 8.0}      # DeepSeek peak, the Experiment 3 budget table
PRICE_USD_GPT = {"uncached": 2.0, "cached": 0.10, "output": 10.0}  # gpt-6.1-sol list price, notional


def money(inp, cached, out, table):
    return ((inp - cached) * table["uncached"] + cached * table["cached"] + out * table["output"]) / 1e6


def exp3_rows(inputs):
    rows = []
    for f in ("bare_203.json", "bare_208.json", "bare_216.json"):
        rows += json.load(open(os.path.join(inputs, f)))
    return rows


def pick_model_only(rows, model_prefix):
    """Latest attempt per design of the bare (model-only) arm for this model, excluding voided/interrupted copies."""
    best = {}
    for r in rows:
        if r.get("arm") != "bare" or not r["model"].startswith(model_prefix):
            continue
        root = (r.get("root") or "")
        if any(x in root for x in ("voided", "interrupted", "excluded", "harness_defect", "harness_v1")):
            continue
        if r["id"] not in best or r["mtime"] > best[r["id"]]["mtime"]:
            best[r["id"]] = r
    return best


def usage_from_exp3(r):
    return {"calls": r.get("calls") or 0, "input": r.get("inp") or 0, "cached": r.get("cached") or 0,
            "output": r.get("outp") or 0, "flows": r.get("orfs") or 0, "wall_h": (r.get("wall") or 0) / 3600}


def usage_from_native(r):
    p = r.get("provider_usage") or {}
    return {"calls": r.get("model_calls") or 0, "input": p.get("input_tokens") or 0,
            "cached": p.get("cache_read_input_tokens") or 0, "output": p.get("output_tokens") or 0,
            "flows": (r.get("orfs_audit") or {}).get("orfs_wrapper_calls") or 0,
            "wall_h": (r.get("agent_wall_seconds") or 0) / 3600}


def summarize(name, per_design, table, currency):
    """per_design: id -> (class, usage, estimated?)"""
    n = len(per_design)
    c = collections.Counter(v[0] for v in per_design.values())
    tot = collections.Counter()
    for _, u, _ in per_design.values():
        for k, v in u.items():
            tot[k] += v
    cost = money(tot["input"], tot["cached"], tot["output"], table)
    return {"arm": name, "designs": n, "fixed": c["fixed"], "pending_equivalence": c["pending"],
            "not_fixed": c["not_fixed"], "unfinished": c["unfinished"], "fixed_upper_bound": c["fixed"] + c["pending"],
            "estimated_designs": sum(1 for v in per_design.values() if v[2]),
            "calls_per_design": tot["calls"] / n, "tokens_total_M": (tot["input"] + tot["output"]) / 1e6,
            "tokens_per_design_k": (tot["input"] + tot["output"]) / n / 1e3, "cached_share": tot["cached"] / max(tot["input"], 1),
            "flow_runs_per_design": tot["flows"] / n, "wall_h_per_design": tot["wall_h"] / n,
            "cost_total": cost, "cost_per_design": cost / n, "currency": currency,
            "cost_per_fixed_design": cost / c["fixed"] if c["fixed"] else None}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--native", default="/tmp/native_results")
    ap.add_argument("--sample", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_filter_20k.json"))
    ap.add_argument("--tally-csv", default="/home/yangao/r2g-skills/design_cases/r2g_vs_bare_20261029/full/tally_latest.csv")
    ap.add_argument("--exp3-inputs", default="/tmp/tally_inputs_20261109_0756")
    ap.add_argument("--out", default="/home/yangao/r2g-skills/experiments/native_harness/native_tally.json")
    ap.add_argument("--equiv", action="append", default=[], metavar="KEY=FILE",
                    help="equivalence-review verdicts (list of rows with case_id, verdict) for arm KEY (dsh|codex); "
                         "a reviewed pending result is fixed iff its verdict is 'equivalent', else not fixed")
    ap.add_argument("--check", action="store_true", help="sanity-check the model-only selection rule against the all-676 means")
    a = ap.parse_args()
    f = json.load(open(a.sample))
    ids = [i for v in f["keep"].values() for i in v] + [g for g in GIANTS if g not in sum(f["keep"].values(), [])]
    assert len(ids) == 150, len(ids)
    verdicts = {}
    for spec in a.equiv:
        k, fn = spec.split("=", 1)
        rows_ = json.load(open(fn))
        rows_ = rows_["rows"] if isinstance(rows_, dict) else rows_
        verdicts[k] = {r["case_id"]: r["verdict"] for r in rows_}
    exp = exp3_rows(a.exp3_inputs)
    mo = {"DeepSeek": pick_model_only(exp, "deepseek"), "GPT": pick_model_only(exp, "gpt")}
    if a.check:
        for m, d in mo.items():
            print(m, "designs", len(d), "mean calls %.1f" % st.mean(r["calls"] for r in d.values()),
                  "(paper: DeepSeek 76.3, GPT 23.1)")
    out = {"sample": {"designs": 150, "by_platform": dict(collections.Counter(i.split("_")[0] for i in ids))}, "arms": []}
    for key, label, model, table, cur in (("dsh", "DeepSeek Harness (native)", "DeepSeek", PRICE_CNY, "CNY"),
                                          ("codex", "Codex (native, GPT)", "GPT", PRICE_USD_GPT, "USD")):
        per = {}
        missing = []
        for i in ids:
            if i in GIANTS:
                r = mo[model].get(i)
                per[i] = ("not_fixed", usage_from_exp3(r) if r else dict(calls=0, input=0, cached=0, output=0, flows=0, wall_h=0), True)
                continue
            p = os.path.join(a.native, key, i, "result.json")
            r = json.load(open(p)) if os.path.exists(p) else None
            if r is None or r.get("outcome") == "excluded_by_size":
                missing.append(i)
                per[i] = ("unfinished", dict(calls=0, input=0, cached=0, output=0, flows=0, wall_h=0), False)
                continue
            cls = {"dual_clean": "fixed", "pending_semantic_review": "pending"}.get(r["outcome"], "not_fixed")
            if cls == "pending" and i in verdicts.get(key, {}):
                cls = "fixed" if verdicts[key][i] == "equivalent" else "not_fixed"
            per[i] = (cls, usage_from_native(r), False)
        s = summarize(label, per, table, cur)
        s["not_finished_or_missing"] = len(missing)
        out["arms"].append(s)
        out.setdefault("per_design", {})[key] = {i: {"class": v[0], **v[1], "estimated": v[2]} for i, v in per.items()}
    # Experiment 3 reference arms on the same 150 designs
    tally = {r["id"]: r for r in csv.DictReader(open(a.tally_csv))}
    ref = []
    for col, label in (("r2g", "R2GSkills recipes"), ("r2g_plus_fallback", "R2GSkills + DeepSeek fallback"),
                       ("r2g_gpt_plus_fallback", "R2GSkills + GPT fallback"), ("deepseek_only", "DeepSeek only (Exp. 3)"),
                       ("gpt_only", "GPT only (Exp. 3)")):
        fixed = sum(tally[i][col] == "fixed" for i in ids if i in tally)
        ref.append({"arm": label, "designs": sum(i in tally for i in ids), "fixed": fixed})
    for model, label, table, cur in (("DeepSeek", "DeepSeek only (Exp. 3)", PRICE_CNY, "CNY"), ("GPT", "GPT only (Exp. 3)", PRICE_USD_GPT, "USD")):
        per = {i: ("fixed" if tally[i]["deepseek_only" if model == "DeepSeek" else "gpt_only"] == "fixed" else "not_fixed",
                   usage_from_exp3(mo[model][i]) if i in mo[model] else dict(calls=0, input=0, cached=0, output=0, flows=0, wall_h=0), False)
               for i in ids if i in tally}
        s = summarize(label, per, table, cur)
        s["no_usage_record"] = sum(1 for i in ids if i not in mo[model])
        out["arms"].append(s)
    out["exp3_reference_fixed"] = ref
    json.dump(out, open(a.out, "w"), indent=1)
    print("%-30s %5s %6s %7s %9s %6s %9s %8s %9s %7s %9s %9s" % ("arm", "n", "fixed", "pending", "not fixed", "unfin.", "calls/dsg", "tok k/dsg", "flows/dsg", "wall h", "cost/dsg", "cost/fixed"))
    for s in out["arms"]:
        print("%-30s %5d %6d %7d %9d %6d %9.1f %8.0f %9.1f %7.2f %8.3f%s %9s" % (
            s["arm"], s["designs"], s["fixed"], s["pending_equivalence"], s["not_fixed"], s["unfinished"], s["calls_per_design"],
            s["tokens_per_design_k"], s["flow_runs_per_design"], s["wall_h_per_design"], s["cost_per_design"],
            "c" if s["currency"] == "CNY" else "$", ("%.3f" % s["cost_per_fixed_design"]) if s["cost_per_fixed_design"] else "-"))
    print("Experiment 3 on the same 150:", {r["arm"]: r["fixed"] for r in ref})


if __name__ == "__main__":
    main()
