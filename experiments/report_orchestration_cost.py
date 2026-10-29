#!/usr/bin/env python3
"""Success-conditioned orchestration cost of one model, from an accounting ledger.

Reads only `accepted_attempts.json` (written by account_successful_repair_cost.py)
and prints a Markdown report: per platform, the repaired cases and the separately
counted already-clean cases, with model calls, provider token counters (cache
reads are an input subset), dispatch vs final-explanation split, ORFS wrapper
calls and top-level make launches, per-case distribution, and repair classes.
Run the same script over another model's ledger to compare like for like.

Money is printed only for price sets passed with --prices (per million tokens,
uncached input / cache-hit input / output, output including reasoning); nothing
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


def effective_orfs(attempt: dict) -> dict:
    """Diagnostic view: ORFS work that contributed to the accepted result.

    Not the official cost. The protocol counts every repair round inside the
    accepted attempt; what it excludes is whole attempts voided by script-layer
    defects before they were fixed (the ledger keeps the post-fix attempt).

    Excluded as redundant (a run that picks the working strategy first would not
    pay for it): (1) a signoff repair round whose verdict is not `cleared`, with
    its reflow wrapper and every make it spawned up to the next round; (2) a
    failed synthesis wrapper superseded by a later synthesis (resynthesis starts
    over); (3) a failed flow wrapper superseded by a partial rerun: the wrapper,
    its failing make(s) and the rerun's clean makes -- the stages it completed
    are reused by the rerun and stay counted. Model tokens are unaffected: repair
    rounds are deterministic and make no model calls."""
    folder = Path(attempt["attempt"])
    audit = json.loads((folder / "orfs_audit.json").read_text())
    job = json.loads((folder / (attempt["job_ids"][0] + ".job.json")).read_text())
    detail = (job.get("designs") or [{}])[0].get("detail") or {}
    rounds = [r for r in detail.get("repair") or [] if r.get("strategy") not in (None, "none", "stop_clean")]
    events = sorted(audit["matched_events"], key=lambda e: e["timestamp"])
    ends = {e.get("invocation_id"): e.get("exit_code") for e in events if e["event"] == "make_end"}
    wend = {e.get("wrapper_id"): e.get("exit_code") for e in events if e["event"] == "wrapper_end"}
    # Group top-level makes under the latest wrapper of the same pipeline stage;
    # makes outside any wrapper (signoff checks, graph, verify) form their own group.
    wrappers, current = [], {}
    for e in events:
        stage = e.get("stage")
        if e["event"] == "wrapper_start":
            current[stage] = {"stage": stage, "id": e.get("wrapper_id"), "makes": []}
            wrappers.append(current[stage])
        elif e["event"] == "make_start" and int(e.get("make_level") or 0) == 0:
            if stage not in current:
                current[stage] = {"stage": stage, "id": None, "makes": []}
                wrappers.append(current[stage])
            current[stage]["makes"].append({"kind": e.get("kind"),
                                            "exit": ends.get(e.get("invocation_id"))})
    total_w = sum(1 for w in wrappers if w["id"])
    total_m = sum(len(w["makes"]) for w in wrappers)
    drop_w = drop_m = 0
    why = []
    repair_w = [w for w in wrappers if w["stage"] == "repair" and w["id"]]
    if len(repair_w) == len(rounds):
        for w, r in zip(repair_w, rounds):
            if r.get("verdict") != "cleared":
                drop_w += 1; drop_m += len(w["makes"]); why.append("repair:" + str(r.get("strategy")))
    for stage in ("synth", "flow"):
        ws = [w for w in wrappers if w["stage"] == stage and w["id"]]
        for i, w in enumerate(ws[:-1]):
            if wend.get(w["id"]) in (0, None):
                continue
            drop_w += 1
            if stage == "synth":
                drop_m += len(w["makes"])
            else:
                drop_m += sum(1 for m in w["makes"] if m["exit"] not in (0, None))
                drop_m += sum(1 for m in ws[i + 1]["makes"] if m["kind"] == "clean")
            why.append(stage + ":failed_superseded")
    return {"wrapper_calls": total_w, "make_launches": total_m,
            "effective_wrapper_calls": total_w - drop_w, "effective_make_launches": total_m - drop_m,
            "excluded": why, "repair_attribution_ok": len(repair_w) == len(rounds)}


def report(ledger: Path, model: str, prices: dict | None, effective: bool = False) -> str:
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
            eff = None
            if effective:
                eff = [effective_orfs(r) for r in group]
                for r, e in zip(group, eff):
                    r["_effective"] = e
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
                              ("ORFS wrapper calls (as measured)", _sum(group, "orfs_wrapper_calls")),
                              ("ORFS wrapper calls (contributing only)", eff and sum(e["effective_wrapper_calls"] for e in eff)),
                              ("Top-level make launches (as measured)", _sum(group, "orfs_make_launches")),
                              ("Top-level make launches (contributing only)", eff and sum(e["effective_make_launches"] for e in eff))):
                if val is None and "contributing" in name:
                    continue
                out.append(f"| {name} | {val:,} | {val / n:,.1f} |" if val is not None
                           else f"| {name} | unknown | |")
            for label_p, pr in (prices or {}).items():
                money = _money(usage, pr)
                out.append(f"| Cost, {label_p} | {money:,.4f} | {money / n:,.5f} |")
            out += ["", "Per case, mean / median / p90 / max:", "",
                    f"- tokens: {_dist(r['provider_usage'].get('total_tokens') for r in group)}",
                    f"- model calls: {_dist(r.get('model_calls') for r in group)}",
                    f"- ORFS wrapper calls: {_dist(r.get('orfs_wrapper_calls') for r in group)}", ""]
            if eff:
                excluded = Counter(x for e in eff for x in e["excluded"])
                out += ["Excluded as non-contributing: " + (", ".join(f"{k} {v}" for k, v in excluded.most_common()) or "none")
                        + f" (in {sum(bool(e['excluded']) for e in eff)} cases)", ""]
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
    ap.add_argument("--prices", type=Path,
                    help="JSON {label: {input, cached_input, output}} in currency per 1M tokens")
    ap.add_argument("--effective", action="store_true",
                    help="DIAGNOSTIC ONLY, not the official count: also show ORFS work excluding "
                         "rounds that did not clear. Official cost counts every round of the "
                         "accepted attempt (trying a second strategy is normal overhead); only "
                         "attempts voided by runtime/script defects are excluded, by the ledger.")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    prices = json.loads(a.prices.read_text()) if a.prices else None
    text = report(a.ledger, a.model, prices, a.effective)
    if a.out:
        a.out.write_text(text)
    print(text)


if __name__ == "__main__":
    main()
