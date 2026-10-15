#!/usr/bin/env python3
"""The experiment ledger: which PROBLEM, which candidate, what the verdict was.

The point of writing this down is the refutations. Four times in one campaign a
candidate looked right on one design and was wrong on the batch -- a 200-ratio
antenna fix that cleared 1 of 7, a `repair_antennas -net` step that turned out
not to be an option the tool has, an LVS theory about mounting the RTL package,
an m3.2 rate that came from my own contaminated staging. A ledger that records
only the winners invites redoing every one of those.

Verdicts are deliberately few and blunt:
  running     launched, no result yet
  confirmed   batch-validated, ready to wire into the skill
  refuted     batch said no; the reason is recorded so it is not retried
  infeasible  the mechanism does not exist (not a tuning question)
  blocked     needs something else landed first
  stale       launched but abandoned/invalidated (e.g. harness bug)

Usage:
  exp_ledger.py add  --problem P --cand C --hypo H [--designs N] [--platform X]
  exp_ledger.py set  --id E-x-01 --verdict confirmed --result "..."
  exp_ledger.py report [--md]
"""
import argparse, json, os, sys, time

LEDGER = os.environ.get("R2G_EXP_LEDGER",
                        "/proj/workarea/yangao/experiment_ledger.jsonl")
VERDICTS = ("running", "confirmed", "refuted", "infeasible", "blocked", "stale")


def _load():
    if not os.path.exists(LEDGER):
        return []
    out = []
    for ln in open(LEDGER):
        ln = ln.strip()
        if ln:
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                pass
    return out


def _append(rec):
    with open(LEDGER, "a") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _latest():
    """Last write per experiment id wins; the file stays append-only history."""
    by = {}
    for r in _load():
        by[r["id"]] = {**by.get(r["id"], {}), **r}
    return by


def cmd_add(a):
    by = _latest()
    pre = f"E-{a.problem.upper()}"
    n = 1 + sum(1 for k in by if k.startswith(pre + "-"))
    rec = {"id": f"{pre}-{n:02d}", "problem": a.problem, "platform": a.platform,
           "candidate": a.cand, "hypothesis": a.hypo, "designs": a.designs,
           "verdict": "running", "result": "", "ts": time.strftime("%F %T")}
    _append(rec)
    print(rec["id"])
    return 0


def cmd_set(a):
    by = _latest()
    if a.id not in by:
        print(f"no such experiment: {a.id}", file=sys.stderr)
        return 1
    if a.verdict and a.verdict not in VERDICTS:
        print(f"verdict must be one of {VERDICTS}", file=sys.stderr)
        return 1
    rec = {"id": a.id, "ts": time.strftime("%F %T")}
    if a.verdict:
        rec["verdict"] = a.verdict
    if a.result:
        rec["result"] = a.result
    if a.designs:
        rec["designs"] = a.designs
    _append(rec)
    print(f"{a.id} -> {rec.get('verdict', by[a.id]['verdict'])}")
    return 0


def cmd_report(a):
    by = _latest()
    if not by:
        print("台账为空")
        return 0
    rows = sorted(by.values(), key=lambda r: (r["problem"], r["id"]))
    MARK = {"confirmed": "✅", "refuted": "❌", "infeasible": "🚫",
            "running": "🔄", "blocked": "⛔", "stale": "⚪"}
    if a.md:
        print("| 实验 | 平台 | 问题 | 候选 | 设计数 | 判决 | 结果 |")
        print("|---|---|---|---|---|---|---|")
        for r in rows:
            print(f"| {r['id']} | {r.get('platform') or '-'} | {r['problem']} | "
                  f"{r['candidate']} | {r.get('designs') or '-'} | "
                  f"{MARK.get(r['verdict'],'')} {r['verdict']} | {r.get('result') or ''} |")
    else:
        for r in rows:
            print(f"{MARK.get(r['verdict'],'  ')} {r['id']:<12s} "
                  f"{(r.get('platform') or '-'):<10s} {r['problem']:<8s} "
                  f"{r['candidate'][:34]:<34s} n={str(r.get('designs') or '-'):<5s} "
                  f"{r['verdict']}")
            if r.get("result"):
                print(f"               {r['result']}")
    n = {}
    for r in rows:
        n[r["verdict"]] = n.get(r["verdict"], 0) + 1
    print("\n合计: " + "  ".join(f"{k}={v}" for k, v in sorted(n.items())))
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add"); p.set_defaults(fn=cmd_add)
    p.add_argument("--problem", required=True)
    p.add_argument("--cand", required=True)
    p.add_argument("--hypo", required=True)
    p.add_argument("--platform", default="")
    p.add_argument("--designs", type=int, default=0)
    p = sub.add_parser("set"); p.set_defaults(fn=cmd_set)
    p.add_argument("--id", required=True)
    p.add_argument("--verdict", default="")
    p.add_argument("--result", default="")
    p.add_argument("--designs", type=int, default=0)
    p = sub.add_parser("report"); p.set_defaults(fn=cmd_report)
    p.add_argument("--md", action="store_true")
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
