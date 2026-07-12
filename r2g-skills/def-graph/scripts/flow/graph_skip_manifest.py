#!/usr/bin/env python3
"""Emit a graph_dataset.json SKIP manifest enriched with the SPECIFIC upstream
backend-failure reason (failure-patterns.md #38 / codex #6).

run_graphs.sh's skip() used to write a bare
    {"status":"skipped","reason":"no 6_final.def found"}
which is useless for triaging thousands of skipped designs in a corpus sweep: a
recoverable antenna-nonconvergence stall looks identical to a genuinely
uncollected backend. This helper keeps the generic `reason` but adds a
best-effort `upstream` object built from the markers the flow already writes:

  reports/signoff_gate.json            -> signoff_blockers + per-check detail
  reports/antenna_nonconverged.json    -> the "finish interrupted because antenna
                                          repair non-converged" example, verbatim
  reports/ppa.json                     -> orfs_status / orfs_fail_stage
  <newest backend/RUN_*>/stage_log.jsonl -> first failing stage (the DEF-missing
                                          path: run_dir is empty, so scan)

Fail-soft: any unreadable/absent marker is skipped; `upstream` is omitted when
empty so the manifest is unchanged for a plain "no backend" skip. Prints the
manifest JSON to stdout (run_graphs.sh redirects it into graph_dataset.json).

Usage: graph_skip_manifest.py <design> <platform> <reason> <project_dir> [run_dir]
"""
import glob
import json
import os
import sys


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _first_failing_stage(run_dir):
    slog = os.path.join(run_dir, "stage_log.jsonl")
    if not os.path.isfile(slog):
        return None
    try:
        with open(slog, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("status") not in (0, "0"):
                    return rec.get("stage")
    except OSError:
        return None
    return None


def collect_upstream(project_dir, run_dir):
    """Best-effort specific backend-failure reason. Returns {} when nothing is
    knowable (a plain no-backend skip)."""
    reports = os.path.join(project_dir, "reports")
    upstream = {}

    gate = _load(os.path.join(reports, "signoff_gate.json"))
    if isinstance(gate, dict) and gate.get("blockers"):
        upstream["signoff_status"] = gate.get("status")
        upstream["signoff_blockers"] = gate["blockers"]
        checks = gate.get("checks") or {}
        details = {b: (checks.get(b) or {}).get("detail") or (checks.get(b) or {}).get("status")
                   for b in gate["blockers"] if b in checks}
        if details:
            upstream["signoff_detail"] = details

    ant = _load(os.path.join(reports, "antenna_nonconverged.json"))
    if isinstance(ant, dict):
        upstream["antenna_nonconverged"] = {
            "residual_count": ant.get("residual_count"),
            "strategies_tried": ant.get("strategies_tried"),
        }

    ppa = _load(os.path.join(reports, "ppa.json"))
    if isinstance(ppa, dict) and ppa.get("orfs_status") in ("fail", "partial"):
        upstream["orfs_status"] = ppa.get("orfs_status")
        upstream["orfs_fail_stage"] = ppa.get("orfs_fail_stage")

    rd = run_dir
    if not rd:
        runs = sorted(glob.glob(os.path.join(project_dir, "backend", "RUN_*")))
        rd = runs[-1] if runs else ""
    if rd:
        bad = _first_failing_stage(rd)
        if bad:
            upstream.setdefault("stage_log_fail_stage", bad)

    return upstream


def main(argv):
    if len(argv) < 5:
        print("usage: graph_skip_manifest.py <design> <platform> <reason> "
              "<project_dir> [run_dir]", file=sys.stderr)
        return 2
    design, platform, reason, project_dir = argv[1:5]
    run_dir = argv[5] if len(argv) > 5 else ""
    out = {"design": design, "platform": platform, "variants": {},
           "status": "skipped", "reason": reason}
    upstream = collect_upstream(project_dir, run_dir)
    if upstream:
        out["upstream"] = upstream
    json.dump(out, sys.stdout, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
