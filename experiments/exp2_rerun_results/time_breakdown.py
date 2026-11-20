#!/usr/bin/env python3
"""Where the time to 100 designs went, per Experiment 2 arm, from the archived run record.

Reads exp2_rerun_record.tar.gz (extracted with --record) and reports/*.m100.report.json. No new runs.

R2G arms (rtl-acquire + rtl-expander):
  discovery  = start -> first expander batch (seed discovery, planner call, planned discovery)
  expander   = expander batches (repository download + the expander's own screening); PASS and FAIL batches
               separately; per-repository download time is NOT in the archive (expander stdout was not kept)
  precheck   = the serial qualification synthesis of each candidate (precheck_log 'seconds', one job at a time)
  other      = remainder (snapshot import, retries' gaps, bookkeeping)
Model-alone arms: each event's time = its timestamp minus the previous event's, attributed to the model turn or to
the tool that ended (clone_repository = download, validate_candidate = qualification synthesis), up to the 100th design.
Workload (independent of the method): the frozen scorer's resynthesis seconds, mapped cells and RTL bytes of the
100 selected designs.

  time_breakdown.py --record /tmp/exp2rec [--json out.json]
"""
import argparse
import collections
import datetime as dt
import glob
import json
import os
import statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
ARMS = {"r2g-expander-gpt": "exp2-rerun-20261110", "r2g-expander-deepseek": "exp2-rerun-20261111-ds",
        "r2g-expander-cold": "exp2-rerun-20261110", "openai-vanilla": "exp2-rerun-20261110",
        "deepseek-vanilla": "exp2-rerun-20261111-ds"}
LABEL = {"r2g-expander-gpt": "R2G + GPT", "r2g-expander-deepseek": "R2G + DeepSeek", "r2g-expander-cold": "R2G, no model",
         "openai-vanilla": "GPT alone", "deepseek-vanilla": "DeepSeek alone"}
TOOL_PHASE = {"clone_repository": "download", "validate_candidate": "qualification synth", "search_repositories": "search",
              "list_repository_files": "read repo", "read_repository_file": "read repo", "run_readonly_command": "read repo",
              "submit_candidates": "submit"}


def ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def json_tail(path):
    """Expander/discovery logs: a COMMAND line followed by one JSON document."""
    txt = open(path).read()
    i = txt.find("\n{")
    return json.loads(txt[i + 1:]) if i >= 0 else {}


def launch_start(campaign_dir, arm):
    for line in open(os.path.join(campaign_dir, "launch_history.tsv")):
        t, what, name = line.split()[:3]
        if what == "start" and name == arm:
            return ts(t)
    raise KeyError(arm)


def workload(arm):
    r = json.load(open(os.path.join(HERE, "reports", arm + ".m100.report.json")))
    rows = r["results"]
    syn = [x.get("synthesis") or {} for x in rows]
    cells = [s.get("mapped_cells") for s in syn if s.get("mapped_cells")]
    rtl = [sum(f.get("size") or 0 for f in (x.get("source_evidence") or {}).get("files", [])) for x in rows]
    return {"scorer_synth_s_total": round(sum(s.get("elapsed_seconds") or 0 for s in syn)),
            "scorer_eval_s_total": round(sum(x.get("evaluation_seconds") or 0 for x in rows)),
            "mapped_cells_total": sum(cells), "mapped_cells_median": st.median(cells) if cells else None,
            "rtl_kb_total": round(sum(rtl) / 1e3), "rtl_kb_median": round(st.median(rtl) / 1e3, 1)}


def r2g_arm(root, arm):
    run = glob.glob(os.path.join(root, ARMS[arm], "campaign/method_runs", arm + ".batch1.formal"))[0]
    ms = json.load(open(os.path.join(run, "milestone_100.json")))
    rs = json.load(open(os.path.join(run, "resume_state.json")))
    start, end = ts(rs["started_at"]), ts(ms["recorded_at"])
    batches = {}
    for f in sorted(glob.glob(os.path.join(run, "logs", "expander_round*.log"))):
        for b in json_tail(f).get("batches", []):
            if b.get("completed_at"):
                batches[b["round_id"]] = b
    exp = collections.Counter()
    first = min((ts(b["started_at"]) for b in batches.values()), default=end)
    for b in batches.values():
        a, z = ts(b["started_at"]), min(ts(b["completed_at"]), end)
        if z > a:
            exp["expander " + ("ok" if b.get("state") == "PASS" else "failed")] += z - a
    pre = [json.loads(l) for l in open(os.path.join(run, "precheck_log.jsonl"))]
    pre = [p for p in pre if ts(p["at"]) <= end + 1]
    ph = {"discovery": first - start, **exp, "precheck synth": sum(p["seconds"] or 0 for p in pre)}
    ph["other"] = (end - start) - sum(ph.values())
    planner = os.path.join(run, "planner_calls.jsonl")
    calls = sum(1 for _ in open(planner)) if os.path.exists(planner) else 0
    return {"elapsed_s": end - start, "phases_s": ph, "prechecks": len(pre),
            "prechecks_qualified": sum(bool(p.get("qualified")) for p in pre),
            "precheck_s_median": st.median([p["seconds"] for p in pre if p.get("seconds")]),
            "expander_batches": len(batches), "planner_calls": calls,
            "repository_revisions": ms.get("repository_revisions")}


def model_arm(root, arm, hours_to_100):
    camp = os.path.join(root, ARMS[arm])
    run = glob.glob(os.path.join(camp, "campaign/method_runs", arm + ".batch1.formal"))[0]
    start = launch_start(camp, arm)
    end = start + hours_to_100 * 3600
    ev = [json.loads(l) for l in open(os.path.join(run, "logs/events.jsonl"))]
    ph, n = collections.Counter(), collections.Counter()
    prev = start
    for e in ev:
        t = ts(e["timestamp"])
        if t > end:
            break
        d = max(t - prev, 0)
        prev = t
        if e["kind"] == "model_turn":
            ph["model"] += d
        elif e["kind"] == "tool_call":
            ph[TOOL_PHASE.get(e["tool"], e["tool"])] += d
            n[e["tool"]] += 1
        else:
            ph["retry/recovery"] += d
    ph["other"] = (end - start) - sum(ph.values())
    return {"elapsed_s": end - start, "phases_s": dict(ph), "tool_calls": dict(n)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--record", required=True, help="directory the record tarball was extracted into (has runs/)")
    ap.add_argument("--json", default=os.path.join(HERE, "time_breakdown_m100.json"))
    a = ap.parse_args()
    root = os.path.join(a.record, "runs")
    tt = json.load(open(os.path.join(HERE, "time_tokens_m100.json")))
    out = {}
    for arm in ARMS:
        b = r2g_arm(root, arm) if arm.startswith("r2g") else model_arm(root, arm, tt[arm]["hours_to_100"])
        b["workload"] = workload(arm)
        out[arm] = b
    json.dump(out, open(a.json, "w"), indent=1)
    for arm, b in out.items():
        w = b["workload"]
        print("== %-15s %.2f h to 100 | scorer resynth of its 100: %.2f h, cells total %dk (median %d), RTL %d kB (median %.1f)"
              % (LABEL[arm], b["elapsed_s"] / 3600, w["scorer_synth_s_total"] / 3600, w["mapped_cells_total"] / 1e3,
                 w["mapped_cells_median"], w["rtl_kb_total"], w["rtl_kb_median"]))
        for k, v in sorted(b["phases_s"].items(), key=lambda kv: -kv[1]):
            print("     %-22s %6.2f h  %4.0f%%" % (k, v / 3600, 100 * v / b["elapsed_s"]))
        extra = {k: b[k] for k in ("prechecks", "prechecks_qualified", "precheck_s_median", "expander_batches",
                                   "planner_calls", "repository_revisions", "tool_calls") if k in b}
        print("     ", extra)


if __name__ == "__main__":
    main()
