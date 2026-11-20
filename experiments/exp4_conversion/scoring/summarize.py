#!/usr/bin/env python3
"""Exp 4 (conversion): aggregate development cost, application and correctness into
results/summary.json + results/RESULTS_tables.md.

    summarize.py --root W --test LIST --dev LIST --arm LABEL[:DEV_DIR]=APPLY_ROOT ... --res-root R
"""
import argparse
import json
import statistics as st
from pathlib import Path

GROUPS = ["ID_gates", "ID_nets", "ID_iopins", "ID_pins", "ID_c_nodes", "ORDER", "EDGES_b", "EDGES_c",
          "F_gate_categorical", "F_gate_liberty", "F_gate_position", "F_net_integer", "F_net_hpwl", "F_iopin",
          "F_pin", "F_c_pin_edges", "L_congestion", "L_timing", "L_wirelength", "L_ground_cap"]


def load(p, default=None):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return default


def q(vals, f):
    vals = [v for v in vals if v is not None]
    return round(f(vals), 2) if vals else None


def arm_block(label, dev_dir, apply_root, designs, res_root):
    out = {"label": label}
    if dev_dir:
        r = load(Path(dev_dir) / "result.json", {})
        out["development"] = {k: r.get(k) for k in ("model", "stop_reason", "model_calls", "failed_invocations",
                                                     "usage", "usage_complete", "cost_usd", "wall_seconds",
                                                     "shell_commands", "compactions", "converter_sha256", "summary")}
    else:
        out["development"] = {"model_calls": 0, "cost_usd": 0.0}
    rows = []
    for d in designs:
        ap = load(Path(apply_root) / d / "apply.json", {"design": d, "success": False, "missing": True})
        sc = load(Path(res_root) / "per_design" / d / f"score_{label}.json", {})
        rows.append({"design": d, **{k: ap.get(k) for k in ("success", "exit_code", "timed_out", "format_ok", "format_failed",
                                                             "wall_seconds", "user_seconds", "sys_seconds", "peak_rss_mb")},
                     "scored": "groups" in sc, "score_error": sc.get("error"),
                     "all_groups_pass": bool(sc.get("all_groups_pass")), "failed_groups": sc.get("failed_groups"),
                     "groups": {g: {k: v.get(k) for k in ("passed", "rate", "n", "matched", "n_truth", "n_claim", "f1")}
                                for g, v in (sc.get("groups") or {}).items()}})
    n = len(rows)
    succ = [r for r in rows if r["success"]]
    out["application"] = {
        "n_designs": n, "n_success": len(succ), "success_rate": round(len(succ) / n, 4) if n else None,
        "wall_s_median": q([r["wall_seconds"] for r in rows if r["success"]], st.median),
        "wall_s_mean": q([r["wall_seconds"] for r in rows if r["success"]], st.mean),
        "wall_s_max": q([r["wall_seconds"] for r in rows if r["success"]], max),
        "wall_s_total": q([r["wall_seconds"] for r in rows], sum),
        "cpu_s_median": q([(r["user_seconds"] or 0) + (r["sys_seconds"] or 0) for r in rows if r["success"]], st.median),
        "peak_rss_mb_median": q([r["peak_rss_mb"] for r in rows if r["success"]], st.median),
        "peak_rss_mb_max": q([r["peak_rss_mb"] for r in rows if r["success"]], max),
        "failures": [{k: r[k] for k in ("design", "exit_code", "timed_out", "format_failed")} for r in rows if not r["success"]]}
    corr = {}
    for g in GROUPS:
        tot_n = tot_m = 0
        passed = 0
        for r in rows:
            gr = r["groups"].get(g)
            if r["success"] and gr:
                if gr.get("n_truth") is not None:     # set group: pooled F1 via matched counts
                    tot_n += (gr.get("n_truth") or 0) + (gr.get("n_claim") or 0)
                    tot_m += 2 * (gr.get("matched") or 0)
                else:
                    tot_n += gr.get("n") or 0
                    tot_m += gr.get("matched") or 0
                passed += bool(gr.get("passed"))
        corr[g] = {"pooled_rate": round(tot_m / tot_n, 6) if tot_n else None,
                   "designs_pass": passed, "designs_pass_share": round(passed / n, 4) if n else None}
    out["correctness"] = corr
    full = sum(1 for r in rows if r["success"] and r["all_groups_pass"])
    out["full_pass"] = {"n": full, "share": round(full / n, 4) if n else None}
    out["per_design"] = rows
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", type=Path, required=True)
    ap.add_argument("--arm", action="append", required=True, help="LABEL[:DEV_DIR]=APPLY_ROOT")
    ap.add_argument("--res-root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    designs = [l.strip() for l in a.test.read_text().splitlines() if l.strip()]
    arms = []
    for s in a.arm:
        lhs, root = s.split("=", 1)
        label, _, dev = lhs.partition(":")
        arms.append(arm_block(label, dev or None, root, designs, a.res_root))
    summ = {"n_test_designs": len(designs), "arms": arms}
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "summary.json").write_text(json.dumps(summ, indent=1))
    L = ["| arm | model calls | tokens in / cached / out | USD | dev wall h | success | wall s median (max) | peak RSS MB median | full pass |",
         "|---|---|---|---|---|---|---|---|---|"]
    for r in arms:
        d, ap_ = r["development"], r["application"]
        u = d.get("usage") or {}
        tok = f"{u.get('input_tokens', 0):,} / {u.get('cache_read_input_tokens', 0):,} / {u.get('output_tokens', 0):,}" if u else "0"
        wall = round((d.get("wall_seconds") or 0) / 3600, 2)
        L.append(f"| {r['label']} | {d.get('model_calls')} | {tok} | {d.get('cost_usd')} | {wall} | {ap_['n_success']}/{ap_['n_designs']} "
                 f"| {ap_['wall_s_median']} ({ap_['wall_s_max']}) | {ap_['peak_rss_mb_median']} | {r['full_pass']['n']}/{ap_['n_designs']} |")
    L += ["", "| group | " + " | ".join(r["label"] for r in arms) + " |", "|---|" + "---|" * len(arms)]
    for g in GROUPS:
        cells = []
        for r in arms:
            c = r["correctness"][g]
            cells.append(f"{c['pooled_rate']} ({c['designs_pass']}/{r['application']['n_designs']})" if c["pooled_rate"] is not None else "n/a")
        L.append(f"| {g} | " + " | ".join(cells) + " |")
    (a.out / "RESULTS_tables.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
