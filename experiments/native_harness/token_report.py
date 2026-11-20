#!/usr/bin/env python3
"""Token consumption of the native-harness campaigns, finished AND running attempts.

Finished attempts: result.json provider_usage (provider counters, summed per call).
Running attempts: read live -- DeepSeek Harness from its event stream (step usage), Codex from its session log
(rollout-*.jsonl token_count). Prints per campaign and per platform: attempts, model calls, input (of which
cached), output, reasoning tokens, and notional cost at the Experiment 3 price table.

  token_report.py [--root DIR ...] [--json OUT]
"""
import argparse
import collections
import glob
import json
import os
from pathlib import Path

PEAK_CNY = {"uncached": 2.0, "cached": 0.04, "output": 8.0}
KEYS = ("calls", "input", "cached", "output", "reasoning")


GPT_USD = {"uncached": 2.0, "cached": 0.10, "output": 10.0}     # gpt-6.1-sol list price per 1M (Experiment 3)


def usd_gpt(u):
    return ((u["input"] - u["cached"]) * GPT_USD["uncached"] + u["cached"] * GPT_USD["cached"]
            + u["output"] * GPT_USD["output"]) / 1e6


def cost(u):
    return ((u["input"] - u["cached"]) * PEAK_CNY["uncached"] + u["cached"] * PEAK_CNY["cached"]
            + u["output"] * PEAK_CNY["output"]) / 1e6


def finished(r):
    p = r.get("provider_usage") or {}
    return {"calls": r.get("model_calls") or 0, "input": p.get("input_tokens") or 0,
            "cached": p.get("cache_read_input_tokens") or 0, "output": p.get("output_tokens") or 0,
            "reasoning": p.get("reasoning_tokens") or 0}


def live_dsh(d):
    u = dict.fromkeys(KEYS, 0)
    try:
        lines = Path(d, "run.jsonl").read_text(errors="replace").splitlines()
    except OSError:
        return u
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") == "status" and e.get("phase") == "step_end" and e.get("usage"):
            s = e["usage"]
            u["calls"] += 1
            u["cached"] += s.get("cacheReadTokens") or 0
            u["input"] += (s.get("inputTokens") or 0) + (s.get("cacheReadTokens") or 0)
            u["output"] += s.get("outputTokens") or 0
    return u


def live_codex(d, codex_home):
    u = dict.fromkeys(KEYS, 0)
    tid = None
    try:
        for line in Path(d, "run.jsonl").read_text(errors="replace").splitlines()[:5]:
            if '"thread.started"' in line:
                tid = json.loads(line).get("thread_id")
    except (OSError, ValueError):
        return u
    hits = glob.glob(os.path.join(codex_home, "sessions", "**", "rollout-*-%s.jsonl" % tid), recursive=True) if tid else []
    if not hits:
        return u
    last = -1
    for line in open(hits[0], errors="replace"):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        p = e.get("payload") or {}
        if p.get("type") != "token_count":
            continue
        info = p.get("info") or {}
        tot = (info.get("total_token_usage") or {}).get("total_tokens")
        lu = info.get("last_token_usage")
        if not lu or tot is None or tot == last:
            continue
        last = tot
        u["calls"] += 1
        u["input"] += lu.get("input_tokens") or 0
        u["cached"] += lu.get("cached_input_tokens") or 0
        u["output"] += lu.get("output_tokens") or 0
        u["reasoning"] += lu.get("reasoning_output_tokens") or 0
    return u


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", action="append", default=[])
    ap.add_argument("--codex-home", default="/data/yangao/native_harness/codex_home")
    ap.add_argument("--keep", default="/tmp/keep_final.txt", help="comma list of the kept design ids")
    ap.add_argument("--json")
    a = ap.parse_args()
    roots = a.root or ["/data/yangao/native_harness/dsh_runs_20261112", "/data/yangao/native_harness/codex_runs_20261113"]
    keep = set(open(a.keep).read().replace("\n", "").split(",")) if os.path.exists(a.keep) else None
    out = {}
    for root in roots:
        codex = "codex" in os.path.basename(root)
        per = collections.defaultdict(lambda: {**dict.fromkeys(KEYS, 0), "finished": 0, "running": 0})
        plan = None
        for d in sorted(glob.glob(root + "/cases/*/")):
            cid = os.path.basename(d.rstrip("/"))
            if keep is not None and cid not in keep:
                continue
            res = os.path.join(d, "result.json")
            if os.path.exists(res):
                r = json.load(open(res))
                if r.get("outcome") == "excluded_by_size":
                    continue
                u, kind = finished(r), "finished"
                if codex and r.get("plan_used_percent_end") is not None:
                    plan = (os.path.getmtime(res), r["plan_used_percent_end"]) if not plan or os.path.getmtime(res) > plan[0] else plan
            else:
                u, kind = (live_codex(d, a.codex_home) if codex else live_dsh(d)), "running"
            g = per[cid.split("_")[0]]
            g[kind] += 1
            for k in KEYS:
                g[k] += u[k]
        tot = {**dict.fromkeys(KEYS, 0), "finished": 0, "running": 0}
        for g in per.values():
            for k in tot:
                tot[k] += g[k]
        out[os.path.basename(root)] = {"platforms": dict(per), "total": tot, "plan_used_percent": plan[1] if plan else None}
        print("== %s%s" % (os.path.basename(root), "  (weekly plan used: %s%%)" % plan[1] if plan else ""))
        print("   %-10s %8s %7s %7s %12s %12s %10s %10s %9s" % ("platform", "finished", "running", "calls", "input", "of which cached",
                                                               "output", "reasoning", "CNY*" if codex else "CNY") + ("   USD(list)" if codex else ""))
        for name, g in sorted(per.items()) + [("TOTAL", tot)]:
            print("   %-10s %8d %7d %7d %12d %12d %10d %10d %9.2f" % (name, g["finished"], g["running"], g["calls"], g["input"],
                                                                     g["cached"], g["output"], g["reasoning"], cost(g)) + ("   %9.2f" % usd_gpt(g) if codex else ""))
        if codex:
            print("   * CNY column = Experiment 3 budget table (DeepSeek peak prices), used only for the cap; USD = GPT list price,"
                  " notional (ChatGPT plan, no per-call bill)")
    if a.json:
        json.dump(out, open(a.json, "w"), indent=1)


if __name__ == "__main__":
    main()
