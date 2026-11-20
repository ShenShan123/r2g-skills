#!/usr/bin/env python3
"""Exp 4 (conversion): aggregate development cost, application and V1-checker correctness,
separately for the graph and the image task.

    summarize_v1.py --w W --res-root R --out OUT_DIR [--r2g-graph APPLY_ROOT] [--r2g-image APPLY_ROOT]

Graph groups: every v1_defgraph_check.py group of views b and c (views d/e/f and `present_views`
are out of scope, PROTOCOL.md section 3). Image groups: every v1_images_dg.py group except the
non-applicable diagnostics. A design FULLY passes iff its conversion succeeded and every in-scope
group passed. Pooled rate = sum(matched)/sum(n) (set groups: pooled F1 = 2*matched/(n_claim+n_truth)).
"""
import argparse
import json
import statistics as st
from pathlib import Path


def load(p, default=None):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return default


def groups_of(res, task):
    if not res or res.get("error") or (task == "graph" and "groups" not in res) or (task == "image" and "verdict" not in res):
        return None
    if task == "graph":
        return {k: v for k, v in res["groups"].items() if k[:2] in ("b.", "c.")}
    return {k: v for k, v in res.items() if isinstance(v, dict) and k != "verdict" and v.get("applicable", True)}


def counts(g):
    if "n_truth" in g:                       # set_compare
        return 2 * (g.get("matched") or 0), (g.get("n_truth") or 0) + (g.get("n_claim") or 0)
    if "n" in g:
        return g.get("matched") or 0, g.get("n") or 0
    return int(bool(g.get("passed"))), 1


def q(v, f):
    v = [x for x in v if x is not None]
    return round(f(v), 2) if v else None


def arm(task, label, dev_dir, apply_root, res_root, designs):
    dev = load(Path(dev_dir) / "result.json", {}) if dev_dir else {"model_calls": 0, "cost_usd": 0.0}
    rows = []
    for d in designs:
        ap = load(Path(apply_root) / d / "apply.json", {"success": False})
        res = load(Path(res_root) / "per_design" / d / f"{task}_{label}.json")
        g = groups_of(res, task)
        rows.append(dict(design=d, success=bool(ap.get("success")), wall=ap.get("wall_seconds"),
                         cpu=(ap.get("user_seconds") or 0) + (ap.get("sys_seconds") or 0) if ap.get("user_seconds") is not None else None,
                         rss=ap.get("peak_rss_mb"), groups=g,
                         failed=sorted(k for k, v in (g or {}).items() if not v.get("passed")) if g is not None else ["<not scored>"]))
    names = sorted({k for r in rows if r["groups"] for k in r["groups"]})
    corr = {}
    for k in names:
        m = n = p = 0
        for r in rows:
            gg = (r["groups"] or {}).get(k)
            if r["success"] and gg is not None:
                a, b = counts(gg); m += a; n += b; p += bool(gg.get("passed"))
        corr[k] = dict(pooled_rate=round(m / n, 6) if n else None, designs_pass=p)
    full = sum(1 for r in rows if r["success"] and r["groups"] is not None and not r["failed"])
    ok = [r for r in rows if r["success"]]
    return dict(task=task, label=label,
                development={k: dev.get(k) for k in ("model", "stop_reason", "model_calls", "failed_invocations", "usage",
                                                     "cost_usd", "wall_seconds", "converter_sha256")},
                application=dict(n=len(rows), success=len(ok), wall_s_median=q([r["wall"] for r in ok], st.median),
                                 wall_s_max=q([r["wall"] for r in ok], max), wall_s_total=q([r["wall"] for r in ok], sum),
                                 cpu_s_median=q([r["cpu"] for r in ok], st.median),
                                 peak_rss_mb_median=q([r["rss"] for r in ok], st.median), peak_rss_mb_max=q([r["rss"] for r in ok], max)),
                full_pass=full, correctness=corr,
                per_design=[{k: r[k] for k in ("design", "success", "wall", "rss", "failed")} for r in rows])


def table(arms):
    L = ["| arm | model calls | tokens in / cached / out | USD | dev wall min | success | wall s median (max) | peak RSS MB median | full pass |",
         "|---|---|---|---|---|---|---|---|---|"]
    for r in arms:
        d, a = r["development"], r["application"]
        u = d.get("usage") or {}
        tok = f"{u.get('input_tokens', 0):,} / {u.get('cache_read_input_tokens', 0):,} / {u.get('output_tokens', 0):,}" if u else "0"
        L.append(f"| {r['label']} | {d.get('model_calls')} | {tok} | {d.get('cost_usd')} | {round((d.get('wall_seconds') or 0) / 60, 1)} "
                 f"| {a['success']}/{a['n']} | {a['wall_s_median']} ({a['wall_s_max']}) | {a['peak_rss_mb_median']} | {r['full_pass']}/{a['n']} |")
    names = sorted({k for r in arms for k in r["correctness"]})
    L += ["", "| group | " + " | ".join(r["label"] for r in arms) + " |", "|---|" + "---|" * len(arms)]
    for k in names:
        cells = []
        for r in arms:
            c = r["correctness"].get(k)
            cells.append(f"{c['pooled_rate']} ({c['designs_pass']}/{r['application']['n']})" if c and c["pooled_rate"] is not None else "n/a")
        L.append(f"| {k} | " + " | ".join(cells) + " |")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--w", type=Path, required=True)
    ap.add_argument("--res-root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--r2g-graph", type=Path)
    ap.add_argument("--r2g-image", type=Path)
    a = ap.parse_args()
    W = a.w
    designs = [l.strip() for l in (W / "data/test_designs.txt").read_text().splitlines() if l.strip()]
    out = {}
    for task, devroot, aproot, r2g in (("graph", "dev", "apply", a.r2g_graph), ("image", "dev_img", "apply_img", a.r2g_image)):
        arms = []
        if r2g:
            arms.append(arm(task, "r2g", None, r2g, a.res_root, designs))
        for m in ("deepseek", "gpt"):
            arms.append(arm(task, m, W / devroot / m, W / aproot / m, a.res_root, designs))
        out[task] = arms
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "summary.json").write_text(json.dumps(out, indent=1))
    md = "\n\n".join(f"## {t}\n\n" + table(arms) for t, arms in out.items())
    (a.out / "tables.md").write_text(md + "\n")
    print(md)


if __name__ == "__main__":
    main()
