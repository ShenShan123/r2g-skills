#!/usr/bin/env python3
"""Whole-trajectory report for the small DEV: per episode, then per task and arm, then paired with NB.

Reads only what the episode recorded (episode.json, fix log, resource ledger, run directories). A
missing or non-numeric value stays UNKNOWN and is never counted as zero. No flow, no model call.

  python3 t2_dev3_report.py --root <dev package root> --out <dir>
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ann_v2 import fields as FD  # noqa: E402

BAD = ("regression", "no_improvement", "apply_failed", "inconclusive")
BAD_PREFIX = ("rerun_failed", "memory_rerun_failed")      # the rerun did not complete or left the failure in place


def is_invalid(row: dict) -> bool:
    """A wasted attempt: regressed, no improvement, failed to apply, a rerun that failed or stayed
    congested, or rolled back (the contract's definition; the first report missed the rerun-failed
    verdicts)."""
    v = str(row.get("verdict") or "")
    return v in BAD or v.startswith(BAD_PREFIX) or row.get("rolled_back") is True
SOURCE = {"initial": "initial", "catalogue": "catalogue", "memory": "memory", "llm_repair": "model",
          "recovery": "recovery", "rollback_recovery": "recovery"}


def _jl(path: Path) -> list:
    out = []
    try:
        for line in path.read_text().splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    return out


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def run_state(run_dir: Path) -> dict:
    """The failure state a run ended in (typed fields from ann-v2; UNKNOWN when unmeasured)."""
    if not run_dir.is_dir():
        return {"state": "UNKNOWN", "reason": "run_dir_missing"}
    st = FD.measure(run_dir)["stage"]
    if not st.get("measured"):
        return {"state": "UNKNOWN", "reason": "stage_log_absent"}
    if st["present"] is False:
        return {"state": "completed"}
    f = st["fields"]
    return {"state": "aborted", "stage": (f["stage"] or {}).get("value"), "error_code": (f["error_code"] or {}).get("value"),
            "timeout": (f["timeout"] or {}).get("value"), "grt_overflow": (f.get("grt_overflow") or {}).get("value")}


def outcome_class(ep: dict, ledger: list, resources: dict) -> str:
    """ACCEPTED / MEASURED_NOT_ACCEPTED / BUDGET_CONSTRAINED / TECHNICALLY_TERMINATED / INCOMPLETE.
    A stop caused by a budget, a halt on UNKNOWN usage, a watchdog or an accounting failure is never a
    repair failure (amendment 1, rule E)."""
    status = ep.get("status")
    if status in (None, "RUNNING"):
        return "INCOMPLETE"
    refused = _num((resources or {}).get("refused")) or 0
    budget_stop = refused > 0 or any(e.get("budget_stop") in (True, "True", "true") for e in ledger if e.get("type") == "flow_run")
    if budget_stop:
        return "BUDGET_CONSTRAINED"
    if (status in ("ACCOUNTING_FAILURE", "UNVERIFIED_OR_SAFETY_STOP") or ep.get("technical_failures")
            or ep.get("error") or (ep.get("accounting") or {}).get("passed") is not True):
        return "TECHNICALLY_TERMINATED"
    return "ACCEPTED" if status == "ACCEPTED" else "MEASURED_NOT_ACCEPTED"


def load_episode(d: Path) -> dict:
    ep = json.loads((d / "episode.json").read_text())
    ledger = _jl(d / "reports/resource_ledger.jsonl")
    fix = [r for r in _jl(d / "reports/fix_log.jsonl") if r.get("strategy") not in (None, "none")
           and not str(r.get("verdict") or "").startswith("stop")]
    by_tag = {r.get("post_run_tag"): r for r in fix if r.get("post_run_tag")}
    flows = []
    for e in ledger:
        if e.get("type") != "flow_run":
            continue
        tag = e.get("run_tag")
        row = by_tag.get(tag)
        flows.append({"n": len(flows) + 1, "component": e.get("component"), "source": SOURCE.get(e.get("component"), "other"),
                      "run_tag": tag, "rc": e.get("rc"), "timeout": e.get("timeout"),
                      "action": row.get("strategy") if row else None,
                      "edit": row.get("config_delta") if row else None,
                      "verdict": row.get("verdict") if row else None,
                      "rolled_back": row.get("rolled_back") if row else None,
                      "ended_in": run_state(d / "backend" / str(tag))})
    final = ep.get("final") if isinstance(ep.get("final"), dict) else {}
    acc = final.get("acceptance") if isinstance(final.get("acceptance"), dict) else {}
    env = final.get("envelope") if isinstance(final.get("envelope"), dict) else {}
    validation = {"accepted": acc.get("accepted") if acc else "UNKNOWN", "failed_checks": acc.get("failed") if acc else "UNKNOWN",
                  "unknown_checks": acc.get("unknown") if acc else "UNKNOWN", "envelope": env.get("status") or "UNKNOWN"}
    events = collections.Counter(e.get("type") for e in ledger)
    recovery = {"recovery_flows": sum(1 for f in flows if f["source"] == "recovery"),
                "rolled_back_actions": sum(1 for r in fix if r.get("rolled_back") is True),
                "restored_state_reflows": events.get("restored_state_reflow", 0),
                "restored_execution_quality_failures": events.get("restored_execution_quality_failure", 0)}
    failures = {"flows_aborted": sum(1 for f in flows if f["ended_in"].get("state") == "aborted"),
                "flows_timed_out": sum(1 for f in flows if f["ended_in"].get("timeout") is True),
                "technical_failures": ep.get("technical_failures") or [], "error": ep.get("error")}
    res = ep.get("resources") if isinstance(ep.get("resources"), dict) else {}
    usage = ep.get("token_usage") or {}
    # a repeat is the same action id with the same NON-empty recorded edit; a row whose edit was not
    # recorded (a rolled-back rerun) is never matched to another row
    keys = collections.Counter(json.dumps([r.get("strategy"), {k: str(v) for k, v in sorted(_edit(r).items())}],
                                          sort_keys=True) for r in fix if _edit(r))
    return {
        "episode": d.name, "task": ep.get("task"), "arm": ep.get("arm"), "repeat": ep.get("repeat"),
        "outcome_class": outcome_class(ep, ledger, res), "status": ep.get("status"), "accepted": ep.get("accepted"), "terminal_reason": ep.get("terminal_reason"),
        "wall_min": None if _num(ep.get("wall_s")) is None else round(ep["wall_s"] / 60, 1),
        "flows": _num(res.get("spent", {}).get("flows")), "flows_by_component": res.get("flows_by_component"),
        "requests": _num(usage.get("llm_requests")), "tokens": _num(usage.get("total_tokens")),
        "tokens_complete": usage.get("measurement_complete"), "unknown_usage_requests": usage.get("unknown_usage_requests"),
        "tool_calls": sum(1 for e in ledger if e.get("type") == "tool_call"),
        "validation": validation, "recovery_events": recovery, "failure_events": failures,
        "fix_actions": len(fix),
        "actions_in_order": [{"iter": r.get("iter"), "action": r.get("strategy"), "edit": _edit(r), "verdict": r.get("verdict"),
                              "rolled_back": r.get("rolled_back"), "linked": bool(r.get("post_run_tag"))} for r in fix],
        "invalid_attempts": sum(1 for r in fix if is_invalid(r)),
        "repeated_executions": sum(v - 1 for v in keys.values() if v > 1),
        "memory_actions": [{"action": r.get("strategy"), "verdict": r.get("verdict")} for r in fix
                           if str(r.get("strategy", "")).startswith(("tehm", "recipe"))],
        "memory_retrievals": sum(1 for e in ledger if e.get("type") == "memory_retrieval"),
        "model_turns": [(t.get("comparison") or {}).get("gate") for t in (ep.get("repair_turns") or [])],
        "tehm_v2_rejections": _v2_lines(d),
        "trajectory": flows,
    }


def _edit(row: dict) -> dict:
    d = row.get("config_delta")
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except ValueError:
            d = {}
    return {k: (v.get("after") if isinstance(v, dict) else v) for k, v in (d or {}).items()}


def _v2_lines(d: Path) -> list:
    out = []
    for log in (d / "eval_arm.log", *sorted((d / "reports").glob("*.log"))):
        try:
            out += [l.strip()[:240] for l in log.read_text(errors="ignore").splitlines() if "TEHM-v2 rejected" in l]
        except OSError:
            pass
    return out


def mean(xs):
    xs = [x for x in xs if x is not None]
    return None if not xs else round(sum(xs) / len(xs), 2)


def denominator(root: Path, eps: list) -> list:
    """Every registered episode: executed, or why not. A withheld episode is never dropped."""
    try:
        reg = json.loads((root / "registration.json").read_text())
    except (OSError, ValueError):
        return []
    try:
        amend = json.loads((root / "amendment-1.json").read_text())
        withheld = {(r["task"], r["arm"], r["repeat"]) for r in (amend.get("A_withheld_episodes") or {}).get("episodes") or []}
    except (OSError, ValueError):
        withheld = set()
    done = {(e["task"], e["arm"], e["repeat"]): e for e in eps}
    rows = []
    for r in reg["plan"]:
        key = (r["task"], r["arm"], r["repeat"])
        e = done.get(key)
        status = "EXECUTED" if e else ("WITHHELD_NOT_EXECUTED" if key in withheld else "NOT_EXECUTED")
        rows.append({"task": r["task"], "arm": r["arm"], "repeat": r["repeat"], "status": status})
    return rows


def summarise(eps: list) -> dict:
    groups = collections.defaultdict(list)
    for e in eps:
        groups[(e["task"], e["arm"])].append(e)
    table = []
    for (task, arm), rows in sorted(groups.items()):
        cls = collections.Counter(r["outcome_class"] for r in rows)
        table.append({"task": task, "arm": arm, "episodes": len(rows),
                      "accepted": sum(1 for r in rows if r["accepted"]),
                      "measured_not_accepted": cls.get("MEASURED_NOT_ACCEPTED", 0),
                      "stopped_budget_or_technical": cls.get("BUDGET_CONSTRAINED", 0) + cls.get("TECHNICALLY_TERMINATED", 0),
                      "incomplete": cls.get("INCOMPLETE", 0),
                      "flows_mean": mean(r["flows"] for r in rows),
                      "invalid_attempts_mean": mean(r["invalid_attempts"] for r in rows),
                      "repeated_mean": mean(r["repeated_executions"] for r in rows),
                      "requests_mean": mean(r["requests"] for r in rows), "tokens_mean": mean(r["tokens"] for r in rows),
                      "wall_min_mean": mean(r["wall_min"] for r in rows),
                      "unknown_cells": sum(1 for r in rows if r["flows"] is None or r["requests"] is None)})
    by = {(e["task"], e["repeat"], e["arm"]): e for e in eps}
    paired = []
    for (task, rep, arm), e in sorted(by.items()):
        nb = by.get((task, rep, "NB"))
        if arm == "NB" or nb is None:
            continue
        d = (lambda a, b: None if a is None or b is None else round(a - b, 2))
        paired.append({"task": task, "repeat": rep, "arm": arm, "accepted": e["accepted"], "nb_accepted": nb["accepted"],
                       "flows_delta_vs_nb": d(e["flows"], nb["flows"]), "invalid_delta_vs_nb": d(e["invalid_attempts"], nb["invalid_attempts"]),
                       "requests_delta_vs_nb": d(e["requests"], nb["requests"]), "wall_min_delta_vs_nb": d(e["wall_min"], nb["wall_min"])})
    return {"by_task_arm": table, "paired_vs_nb": paired}


def to_markdown(eps: list, summ: dict, denom: list | None = None) -> str:
    L = ["# Small DEV: whole-trajectory report", "",
         "UNKNOWN stays UNKNOWN. Means are over the executed episodes of the cell; withheld episodes are listed "
         "below and are not dropped from the denominator.", ""]
    if denom:
        c = collections.Counter((d["task"], d["status"]) for d in denom)
        L += ["## Registered episodes", "", "| Task | Status | Episodes |", "| --- | --- | --- |"]
        L += [f"| {t} | {st} | {n} |" for (t, st), n in sorted(c.items())] + [""]
    L += [
         "## Per task and arm", "",
         "| Task | Arm | Episodes | Accepted | Not accepted (measured) | Stopped (budget or technical) | Flows | Invalid attempts | Repeats | Requests | Tokens | Wall (min) | UNKNOWN cells |",
         "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in summ["by_task_arm"]:
        L.append(f"| {r['task']} | {r['arm']} | {r['episodes']} | {r['accepted']} | {r['measured_not_accepted']} | "
                 f"{r['stopped_budget_or_technical']} | {r['flows_mean']} | {r['invalid_attempts_mean']} | "
                 f"{r['repeated_mean']} | {r['requests_mean']} | {r['tokens_mean']} | {r['wall_min_mean']} | {r['unknown_cells']} |")
    L += ["", "## Paired with NB (same task and repeat)", "",
          "| Task | Repeat | Arm | Accepted | NB accepted | Flows vs NB | Invalid vs NB | Requests vs NB | Wall vs NB (min) |",
          "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in summ["paired_vs_nb"]:
        L.append(f"| {r['task']} | {r['repeat']} | {r['arm']} | {r['accepted']} | {r['nb_accepted']} | {r['flows_delta_vs_nb']} | "
                 f"{r['invalid_delta_vs_nb']} | {r['requests_delta_vs_nb']} | {r['wall_min_delta_vs_nb']} |")
    L += ["", "## Trajectories", ""]
    for e in sorted(eps, key=lambda x: (x["task"], x["repeat"], x["arm"])):
        v = e["validation"]
        L += [f"### {e['episode']}: {e['outcome_class']} ({e['status']}, accepted={e['accepted']}, {e['terminal_reason']})", "",
              f"Strict validation: accepted={v['accepted']}, failed checks {v['failed_checks']}, unknown checks "
              f"{v['unknown_checks']}, area envelope {v['envelope']}.",
              f"Cost: flows {e['flows']} {e['flows_by_component']}; requests {e['requests']}; tokens {e['tokens']}; "
              f"tool invocations {e['tool_calls']}; wall {e['wall_min']} min.",
              f"Failure events: {e['failure_events']}. Recovery events: {e['recovery_events']}.",
              f"Memory actions {e['memory_actions']}; TEHM-v2 logged rejections {len(e['tehm_v2_rejections'])} "
              f"(ranking and deferral are not recorded by this runtime: not observable).", "",
              "Actions in order: " + ("; ".join(f"{a['action']} {a['edit']} -> {a['verdict']}"
                                                + (" (rolled back)" if a["rolled_back"] else "") for a in e["actions_in_order"]) or "none"),
              "",
              "| # | Source | Action | Edit | Verdict | Rolled back | Run ended in |", "| --- | --- | --- | --- | --- | --- | --- |"]
        for f in e["trajectory"]:
            end = f["ended_in"]
            ended = end["state"] if end["state"] != "aborted" else f"aborted {end.get('stage')} {end.get('error_code')}" + (
                " (timeout)" if end.get("timeout") else "")
            L.append(f"| {f['n']} | {f['source']} | {f['action']} | {f['edit']} | {f['verdict']} | {f['rolled_back']} | {ended} |")
        L.append("")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    if a.out.exists():
        raise SystemExit("refusing to overwrite " + str(a.out))
    eps = [load_episode(d) for d in sorted((a.root / "episodes").glob("*")) if (d / "episode.json").is_file()]
    summ = summarise(eps)
    denom = denominator(a.root, eps)
    a.out.mkdir(parents=True)
    (a.out / "report.json").write_text(json.dumps({"episodes": eps, "summary": summ, "registered": denom}, indent=1,
                                                  sort_keys=True, default=str) + "\n")
    (a.out / "report.md").write_text(to_markdown(eps, summ, denom))
    print(json.dumps(summ["by_task_arm"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
