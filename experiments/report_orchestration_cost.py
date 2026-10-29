#!/usr/bin/env python3
"""Success-conditioned orchestration cost of one model, from an accounting ledger.

Reads only `accepted_attempts.json` (written by account_successful_repair_cost.py)
and prints a Markdown report: per platform, the repaired cases and the separately
counted already-clean cases, with model calls, provider token counters (cache
reads are an input subset), dispatch vs final-explanation split, ORFS wrapper
calls and top-level make launches, per-case distribution, and repair classes.
Run the same script over another model's ledger to compare like for like.

Money is printed only when prices are given (USD per million tokens); nothing
is estimated from a price sheet the script does not know.
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

COUNTERS = ("input_tokens", "cache_read_input_tokens", "uncached_input_tokens",
            "output_tokens", "reasoning_tokens", "total_tokens")


def _sum(rows, key, sub=None):
    vals = [(r.get(sub) or {}).get(key) if sub else r.get(key) for r in rows]
    return None if any(v is None for v in vals) else sum(vals)


def _dist(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return "n/a"
    p90 = values[min(len(values) - 1, int(round(0.9 * (len(values) - 1))))]
    return "%s / %s / %s / %s" % (f"{statistics.mean(values):,.0f}", f"{statistics.median(values):,.0f}",
                                  f"{p90:,.0f}", f"{values[-1]:,.0f}")


def _money(usage, prices):
    if not prices or usage is None:
        return None
    cached = usage.get("cache_read_input_tokens") or 0
    uncached = usage.get("uncached_input_tokens")
    if uncached is None:
        uncached = (usage.get("input_tokens") or 0) - cached
    return (cached * prices["cached_input"] + uncached * prices["input"]
            + (usage.get("output_tokens") or 0) * prices["output"]) / 1e6


def report(ledger: Path, model: str, prices: dict | None) -> str:
    rows = list(json.loads(ledger.read_text()).values())
    by = defaultdict(lambda: {"repair": [], "clean": []})
    for r in rows:
        by[r["platform"]]["repair" if r.get("repair_observed") else "clean"].append(r)
    out = [f"# Orchestration cost: {model}", "",
           f"Source: `{ledger}` ({len(rows)} accepted attempts). Success-conditioned: only the "
           "earliest eligible successful attempt per case; failed, voided and debugging attempts "
           "are excluded. Cache reads are a subset of input tokens.", ""]
    for platform in sorted(by):
        for label, key in (("repaired", "repair"), ("already clean (separate)", "clean")):
            group = by[platform][key]
            if not group:
                continue
            usage = {k: _sum(group, k, "provider_usage") for k in COUNTERS}
            disp = _sum(group, "total_tokens", "dispatch_usage")
            expl = _sum(group, "total_tokens", "explanation_usage")
            n = len(group)
            out += [f"## {platform}: {label}, {n} cases", "",
                    "| Metric | Total | Per case (mean) |", "|---|---:|---:|"]
            for name, val in (("Model calls", _sum(group, "model_calls")),
                              ("Transport retries", sum(r.get("transport_retries", 0) for r in group)),
                              ("Total tokens", usage["total_tokens"]),
                              ("Input tokens", usage["input_tokens"]),
                              ("  of which cache reads", usage["cache_read_input_tokens"]),
                              ("  of which uncached", usage["uncached_input_tokens"]),
                              ("Output tokens", usage["output_tokens"]),
                              ("  of which reasoning", usage["reasoning_tokens"]),
                              ("Dispatch tokens (job submission)", disp),
                              ("Explanation tokens (final read-only summary)", expl),
                              ("ORFS wrapper calls", _sum(group, "orfs_wrapper_calls")),
                              ("Top-level make launches", _sum(group, "orfs_make_launches"))):
                out.append(f"| {name} | {val:,} | {val / n:,.1f} |" if val is not None
                           else f"| {name} | unknown | |")
            money = _money(usage, prices)
            if money is not None:
                out.append(f"| Cost (USD, given prices) | {money:,.4f} | {money / n:,.5f} |")
            out += ["", "Per case, mean / median / p90 / max:", "",
                    f"- tokens: {_dist(r['provider_usage'].get('total_tokens') for r in group)}",
                    f"- model calls: {_dist(r.get('model_calls') for r in group)}",
                    f"- ORFS wrapper calls: {_dist(r.get('orfs_wrapper_calls') for r in group)}", ""]
            if key == "repair":
                classes = Counter(r.get("classification") for r in group)
                strategies = Counter(s for r in group for s in r.get("strategies") or [])
                out += ["Repair classes: " + ", ".join(f"{k} {v}" for k, v in classes.most_common()), "",
                        "Strategies applied: " + ", ".join(f"{k} {v}" for k, v in strategies.most_common()), ""]
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ledger", type=Path, required=True, help="accepted_attempts.json")
    ap.add_argument("--model", default="unnamed model")
    ap.add_argument("--price-input", type=float, help="USD per 1M uncached input tokens")
    ap.add_argument("--price-cached-input", type=float, help="USD per 1M cache-read input tokens")
    ap.add_argument("--price-output", type=float, help="USD per 1M output tokens")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    prices = None
    if None not in (a.price_input, a.price_cached_input, a.price_output):
        prices = {"input": a.price_input, "cached_input": a.price_cached_input, "output": a.price_output}
    text = report(a.ledger, a.model, prices)
    if a.out:
        a.out.write_text(text)
    print(text)


if __name__ == "__main__":
    main()
