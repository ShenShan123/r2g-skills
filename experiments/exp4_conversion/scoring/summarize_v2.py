#!/usr/bin/env python3
"""Exp 4 v2 summary: per arm x platform x task -- conversion success, per-group accuracy, full-pass share;
per model mean / range / best / worst over its 3 development runs; development cost; per-design time/RSS;
failure classes (first failing groups, with one example each).

    summarize_v2.py --w W --score R --out OUT [--unconstrained FILE]

Tasks: graphs (v2_defgraph_check.py, both forms b-f), images (v1_images_dg.py), four_stage
(v1_4stage_check.py, Nangate45). A missing or crashed checker result counts as not passing (reported).
Designs listed in --unconstrained (no constrained timing path) are reported, and full pass is also
given with their timing groups set aside.
"""
import argparse
import json
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path

ARMS = ["r2g", "deepseek_s1", "deepseek_s2", "deepseek_s3", "gpt_s1", "gpt_s2", "gpt_s3"]
TASKS = ["graphs", "images", "four_stage"]


def load(p, d=None):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return d


def groups_of(res, task):
    if not res or res.get("error"):
        return None
    if task == "images":
        return {k: v for k, v in res.items() if isinstance(v, dict) and k != "verdict" and v.get("applicable", True)}
    g = res.get("groups")
    return {k: v for k, v in g.items() if isinstance(v, dict) and v.get("applicable", True)} if isinstance(g, dict) else None


def counts(g):
    if "n_truth" in g:
        return 2 * (g.get("matched") or 0), (g.get("n_truth") or 0) + (g.get("n_claim") or 0)
    if "n" in g and g.get("n") is not None and "matched" in g:
        return g.get("matched") or 0, g.get("n") or 0
    # all-or-nothing checks (ordering, NaN placement: n entities but no per-entity match count) and pure flags.
    # 2026-11-09 fix: these were counted as matched=0 of n, so R2G's mean read 0.73 with 896/900 groups passing.
    return int(bool(g.get("passed"))), 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--w", type=Path, required=True)
    ap.add_argument("--score", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--unconstrained", type=Path)
    a = ap.parse_args()
    W = a.w
    designs = [l.strip() for l in (W / "v2/test_designs.txt").read_text().splitlines() if l.strip()]
    plat = {d: load(W / "data_v2/test" / d / "STAGED.json")["platform"] for d in designs}
    uncon = set()
    if a.unconstrained and a.unconstrained.is_file():
        uncon = {l.split()[1] for l in a.unconstrained.read_text().splitlines() if len(l.split()) >= 3 and l.split()[2] != "True"}
    out = {"designs": len(designs), "unconstrained_timing_designs": sorted(uncon), "arms": {}}
    for arm in ARMS:
        root = W / "v2/apply" / arm
        if not root.is_dir():
            continue
        dev = load(W / "v2/dev" / arm / "result.json", {}) if arm != "r2g" else {"model_calls": 0, "cost_usd": 0.0}
        A = {"development": {k: dev.get(k) for k in ("model", "seed", "stop_reason", "model_calls", "failed_invocations", "usage",
                                                    "cost_usd", "wall_seconds", "converter_sha256")},
             "per_platform": {}, "failure_classes": {}}
        rows = []
        for d in designs:
            ap_ = load(root / d / "apply.json", {})
            ts = ap_.get("task_success") or {}
            row = {"design": d, "platform": plat[d], "wall": ap_.get("wall_seconds"), "rss": ap_.get("peak_rss_mb"),
                   "exit_code": ap_.get("exit_code"), "timed_out": ap_.get("timed_out"), "tasks": {}}
            for t in TASKS:
                if t == "four_stage" and plat[d] != "nangate45":
                    continue
                res = load(a.score / "per_design" / d / f"{t}_{arm}.json")
                g = groups_of(res, t)
                failed = sorted(k for k, v in (g or {}).items() if not v.get("passed")) if g is not None else ["<not scorable>"]
                failed_ex_timing = [f for f in failed if not (d in uncon and "timing" in f.lower())]
                row["tasks"][t] = {"converted": bool(ts.get(t)), "groups": g, "failed": failed,
                                   "full_pass": bool(ts.get(t)) and g is not None and not failed,
                                   "full_pass_ex_uncon_timing": bool(ts.get(t)) and g is not None and not failed_ex_timing}
            rows.append(row)
        for p in ("nangate45", "sky130hd", "gf180", "all"):
            sel = [r for r in rows if p == "all" or r["platform"] == p]
            P = {"n": len(sel), "wall_s_median": None, "peak_rss_mb_median": None}
            w = [r["wall"] for r in sel if r["wall"] is not None]
            P["wall_s_median"] = round(st.median(w), 1) if w else None
            P["wall_s_max"] = round(max(w), 1) if w else None
            m = [r["rss"] for r in sel if r["rss"] is not None]
            P["peak_rss_mb_median"] = round(st.median(m), 1) if m else None
            for t in TASKS:
                tr = [r["tasks"][t] for r in sel if t in r["tasks"]]
                if not tr:
                    continue
                agg = defaultdict(lambda: [0, 0, 0])
                for x in tr:
                    if x["converted"] and x["groups"]:
                        for k, v in x["groups"].items():
                            mm, nn = counts(v)
                            agg[k][0] += mm; agg[k][1] += nn; agg[k][2] += bool(v.get("passed"))
                P[t] = {"n": len(tr), "converted": sum(x["converted"] for x in tr),
                        "scorable": sum(x["groups"] is not None for x in tr),
                        "full_pass": sum(x["full_pass"] for x in tr),
                        "full_pass_ex_uncon_timing": sum(x["full_pass_ex_uncon_timing"] for x in tr),
                        "n_groups": len(agg),
                        "groups_all_designs_pass": sum(1 for v in agg.values() if v[2] == len(tr)),
                        "mean_group_rate": round(st.mean(v[0] / v[1] for v in agg.values() if v[1]), 6) if agg else None,
                        # per scorable design: share of its groups that pass (median over designs)
                        "median_design_group_pass": round(st.median(
                            sum(1 for v in x["groups"].values() if v.get("passed")) / len(x["groups"])
                            for x in tr if x["converted"] and x["groups"]), 4) if any(x["converted"] and x["groups"] for x in tr) else None,
                        "groups": {k: {"rate": round(v[0] / v[1], 6) if v[1] else None, "designs_pass": v[2]} for k, v in sorted(agg.items())}}
            A["per_platform"][p] = P
        fc = Counter(); ex = {}
        for r in rows:
            for t, x in r["tasks"].items():
                for f in x["failed"][:200]:
                    key = f"{t}:{f.split('.', 1)[-1] if f.startswith(('homo.', 'hetero.')) else f}"
                    fc[key] += 1
                    if key not in ex:
                        gg = (x["groups"] or {}).get(f, {})
                        ex[key] = {"design": r["design"], "group": f,
                                   "example": (gg.get("examples") or gg.get("missing_examples") or gg.get("extra_examples") or gg.get("error")
                                               or gg.get("worst") or None)}
        A["failure_classes"] = {k: {"designs_x_forms": n, "example": ex[k]} for k, n in fc.most_common(60)}
        A["per_design"] = [{"design": r["design"], "platform": r["platform"], "wall": r["wall"], "rss": r["rss"],
                            "exit_code": r["exit_code"], "timed_out": r["timed_out"],
                            "tasks": {t: {k: x[k] for k in ("converted", "full_pass", "full_pass_ex_uncon_timing")} | {"n_failed": len(x["failed"])}
                                      for t, x in r["tasks"].items()}} for r in rows]
        out["arms"][arm] = A
    # per model: mean / range / best / worst over seeds (by full-pass share over all tasks)
    models = {}
    for m in ("deepseek", "gpt"):
        runs = [k for k in out["arms"] if k.startswith(m + "_")]
        if not runs:
            continue
        def score(k):
            P = out["arms"][k]["per_platform"]["all"]
            return sum(P[t]["full_pass"] for t in TASKS if t in P) / sum(P[t]["n"] for t in TASKS if t in P)
        def task_rates(k, t):
            P = out["arms"][k]["per_platform"]["all"].get(t)
            return (P["full_pass"] / P["n"], P["converted"] / P["n"], P["mean_group_rate"] or 0.0) if P else None
        mm = {"runs": runs, "best": max(runs, key=score), "worst": min(runs, key=score)}
        for t in TASKS:
            vals = [task_rates(k, t) for k in runs if task_rates(k, t)]
            if vals:
                for i, nm in enumerate(("full_pass_share", "conversion_success", "mean_group_rate")):
                    xs = [v[i] for v in vals]
                    mm[f"{t}.{nm}"] = {"mean": round(st.mean(xs), 4), "min": round(min(xs), 4), "max": round(max(xs), 4)}
        dv = [out["arms"][k]["development"] for k in runs]
        for nm in ("model_calls", "cost_usd", "wall_seconds"):
            xs = [x.get(nm) or 0 for x in dv]
            mm[f"dev.{nm}"] = {"mean": round(st.mean(xs), 3), "min": round(min(xs), 3), "max": round(max(xs), 3), "total": round(sum(xs), 3)}
        models[m] = mm
    out["models"] = models
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "summary_v2.json").write_text(json.dumps(out, indent=1, default=str))
    L = ["| arm | calls | tokens in / cached / out | USD | dev wall min | platform | graphs conv / full | images conv / full | four-stage conv / full | wall s median (max) | RSS MB median |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for arm, A in out["arms"].items():
        d = A["development"]; u = d.get("usage") or {}
        tok = f"{u.get('input_tokens', 0):,} / {u.get('cache_read_input_tokens', 0):,} / {u.get('output_tokens', 0):,}" if u else "0"
        for p in ("nangate45", "sky130hd", "gf180", "all"):
            P = A["per_platform"][p]
            cell = lambda t: f"{P[t]['converted']}/{P[t]['n']} / {P[t]['full_pass']}/{P[t]['n']}" if t in P else "—"  # noqa: E731
            L.append(f"| {arm} | {d.get('model_calls')} | {tok} | {d.get('cost_usd')} | {round((d.get('wall_seconds') or 0) / 60, 1)} | {p} "
                     f"| {cell('graphs')} | {cell('images')} | {cell('four_stage')} | {P['wall_s_median']} ({P['wall_s_max']}) | {P['peak_rss_mb_median']} |")
    L += ["", "| arm | task | platform | scorable designs | groups | groups passing on every design | median share of groups passing per design | mean group accuracy |",
          "|---|---|---|---|---|---|---|---|"]
    for arm, A in out["arms"].items():
        for t in TASKS:
            for p in ("nangate45", "sky130hd", "gf180", "all"):
                P = A["per_platform"][p].get(t)
                if P:
                    L.append(f"| {arm} | {t} | {p} | {P['scorable']}/{P['n']} | {P['n_groups']} | {P['groups_all_designs_pass']} | {P['median_design_group_pass']} | {P['mean_group_rate']} |")
    (a.out / "tables_v2.md").write_text("\n".join(L) + "\n")
    print("\n".join(L[:40]))


if __name__ == "__main__":
    main()
