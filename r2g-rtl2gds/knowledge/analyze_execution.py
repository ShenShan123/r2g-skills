#!/usr/bin/env python3
"""Produce structured fix proposals from a failed design run.

Usage:
  analyze_execution.py <project-dir> [--out <path>]
                       [--patterns <path>] [--candidates <path>]

Reads the project's structured artifacts, searches for similar past
failures, and emits fix proposals — a review queue of config.mk
changes ranked by confidence. Proposals are NEVER auto-applied.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import knowledge_db
import search_failures

_PATTERNS_PATH = knowledge_db.DEFAULT_KNOWLEDGE_DIR.parent / "references" / "failure-patterns.md"
_CANDIDATES_PATH = knowledge_db.DEFAULT_KNOWLEDGE_DIR / "failure_candidates.json"


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _parse_config_mk(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8", errors="ignore").replace("\\\n", " ")
    fields: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"(?:export\s+)?(\w+)\s*=\s*(.*)", line)
        if m:
            fields[m.group(1)] = m.group(2).strip()
    return fields


def _read_stage_log(path: Path) -> list[dict]:
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _derive_status(stages: list[dict]) -> tuple[str, str | None]:
    if not stages:
        return ("unknown", None)
    fail_stage = None
    stage_names_done = {s["stage"] for s in stages if s.get("status") == "pass"}
    for s in stages:
        if s.get("status") == "fail" and fail_stage is None:
            fail_stage = s.get("stage")
    if fail_stage:
        return ("fail", fail_stage)
    required = ["synth", "floorplan", "place", "cts", "route", "finish"]
    if all(name in stage_names_done for name in required):
        return ("pass", None)
    return ("partial", stages[-1].get("stage") if stages else None)


def _propose_utilization_fix(config, issues, ppa, tcheck):
    proposals = []
    util_issues = [i for i in issues
                   if i.get("kind") in ("placement_utilization_overflow",
                                         "routing_congestion")]
    if not util_issues:
        return proposals
    cu = config.get("CORE_UTILIZATION")
    if cu is None:
        return proposals
    try:
        cu_val = int(cu)
    except ValueError:
        return proposals
    suggested = max(10, int(cu_val * 0.6))
    proposals.append({
        "parameter": "CORE_UTILIZATION",
        "current": cu,
        "suggested": str(suggested),
        "rationale": f"Utilization overflow/congestion detected. Reduce from {cu}% to {suggested}%.",
        "confidence": "high",
        "source": "rule",
    })
    return proposals


def _propose_density_fix(config, issues, ppa, tcheck):
    proposals = []
    density_issues = [i for i in issues
                      if i.get("kind") in ("placement_divergence",)]
    if not density_issues:
        return proposals
    pd = config.get("PLACE_DENSITY_LB_ADDON")
    try:
        pd_val = float(pd) if pd else 0.0
    except ValueError:
        pd_val = 0.0
    if pd_val < 0.20:
        suggested = "0.20"
    elif pd_val < 0.30:
        suggested = "0.30"
    else:
        suggested = str(round(pd_val + 0.10, 2))
    proposals.append({
        "parameter": "PLACE_DENSITY_LB_ADDON",
        "current": pd or "unset",
        "suggested": suggested,
        "rationale": f"Placement divergence detected. Raise density addon to {suggested}.",
        "confidence": "high",
        "source": "rule",
    })
    return proposals


def _propose_pdn_fix(config, issues, ppa, tcheck):
    proposals = []
    pdn_issues = [i for i in issues if "pdn" in (i.get("kind") or "").lower()]
    if not pdn_issues:
        return proposals
    cu = config.get("CORE_UTILIZATION")
    if cu:
        try:
            cu_val = int(cu)
            suggested = max(10, int(cu_val * 0.7))
            proposals.append({
                "parameter": "CORE_UTILIZATION",
                "current": cu,
                "suggested": str(suggested),
                "rationale": "PDN error detected. Reduce utilization to give PDN grid more room.",
                "confidence": "medium",
                "source": "rule",
            })
        except ValueError:
            pass
    if config.get("SYNTH_HIERARCHICAL") in ("1", "true", "True"):
        proposals.append({
            "parameter": "SYNTH_HIERARCHICAL",
            "current": config["SYNTH_HIERARCHICAL"],
            "suggested": "0",
            "rationale": "PDN error with SYNTH_HIERARCHICAL=1. "
                         "Hierarchical synthesis increases cell count, "
                         "potentially exceeding die area for PDN grid.",
            "confidence": "medium",
            "source": "rule",
        })
    return proposals


def _propose_timing_fix(config, issues, ppa, tcheck):
    proposals = []
    tier = tcheck.get("tier", "")
    if tier not in ("moderate", "severe"):
        return proposals
    clock_period = config.get("CLOCK_PERIOD")
    if clock_period:
        try:
            cp_val = float(clock_period)
            suggested = round(cp_val * 1.3, 1)
            proposals.append({
                "parameter": "CLOCK_PERIOD",
                "current": clock_period,
                "suggested": str(suggested),
                "rationale": f"Timing tier={tier}. Relax clock period from {cp_val}ns to {suggested}ns.",
                "confidence": "medium" if tier == "moderate" else "low",
                "source": "rule",
            })
        except ValueError:
            pass
    return proposals


def _propose_safety_flags(config, issues, ppa, tcheck):
    proposals = []
    sigsegv_issues = [i for i in issues
                      if "sigsegv" in (i.get("kind") or "").lower()
                      or "signal 11" in (i.get("summary") or "").lower()]
    if not sigsegv_issues:
        return proposals
    if config.get("SKIP_CTS_REPAIR_TIMING") != "1":
        proposals.append({
            "parameter": "SKIP_CTS_REPAIR_TIMING",
            "current": config.get("SKIP_CTS_REPAIR_TIMING"),
            "suggested": "1",
            "rationale": "SIGSEGV in CTS/repair detected. Add safety flag to bypass crashing step.",
            "confidence": "high",
            "source": "rule",
        })
    if config.get("SKIP_LAST_GASP") != "1":
        proposals.append({
            "parameter": "SKIP_LAST_GASP",
            "current": config.get("SKIP_LAST_GASP"),
            "suggested": "1",
            "rationale": "Add SKIP_LAST_GASP to avoid similar crashes in later stages.",
            "confidence": "high",
            "source": "rule",
        })
    return proposals


_RULE_GENERATORS = [
    _propose_utilization_fix,
    _propose_density_fix,
    _propose_pdn_fix,
    _propose_timing_fix,
    _propose_safety_flags,
]


def analyze(project: Path,
            patterns_path: Path = _PATTERNS_PATH,
            candidates_path: Path = _CANDIDATES_PATH) -> dict:
    """Analyze a failed run and produce fix proposals."""
    project = Path(project)
    config = _parse_config_mk(project / "constraints" / "config.mk")
    diag = _read_json(project / "reports" / "diagnosis.json") or {}
    ppa = _read_json(project / "reports" / "ppa.json") or {}
    tcheck = _read_json(project / "reports" / "timing_check.json") or {}

    stage_log_path = project / "backend" / "stage_log.jsonl"
    if (project / "backend").is_dir():
        run_dirs = sorted(
            (d for d in (project / "backend").iterdir()
             if d.is_dir() and d.name.startswith("RUN_")),
            key=lambda d: d.stat().st_mtime, reverse=True,
        )
        for rd in run_dirs:
            candidate = rd / "stage_log.jsonl"
            if candidate.exists():
                stage_log_path = candidate
                break
    stages = _read_stage_log(stage_log_path)
    status, fail_stage = _derive_status(stages)

    issues = diag.get("issues") or []

    query_parts = [fail_stage or ""] + [
        i.get("kind", "") + " " + i.get("summary", "") for i in issues
    ]
    query = " ".join(query_parts).strip()

    similar = []
    if query:
        similar = search_failures.search(
            query,
            patterns_path=patterns_path,
            candidates_path=candidates_path,
            top_k=3,
        )

    proposals = []
    seen_params = set()
    for generator in _RULE_GENERATORS:
        for proposal in generator(config, issues, ppa, tcheck):
            if proposal["parameter"] in seen_params:
                continue
            seen_params.add(proposal["parameter"])
            proposals.append(proposal)

    return {
        "project": str(project),
        "status": status,
        "fail_stage": fail_stage,
        "diagnosis_issues": issues,
        "similar_failures": similar,
        "proposals": proposals,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("project", type=Path, help="Path to failed project directory")
    p.add_argument("--out", type=Path, default=None,
                   help="Write proposals to file (default: stdout)")
    p.add_argument("--patterns", type=Path, default=_PATTERNS_PATH)
    p.add_argument("--candidates", type=Path, default=_CANDIDATES_PATH)
    args = p.parse_args()

    result = analyze(args.project,
                     patterns_path=args.patterns,
                     candidates_path=args.candidates)

    output = json.dumps(result, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
        print(f"Wrote analysis to {args.out}")
    else:
        print(output)

    print(f"\nProject: {result['project']}", file=sys.stderr)
    print(f"Status: {result['status']} (fail_stage={result['fail_stage']})", file=sys.stderr)
    print(f"Fix proposals: {len(result['proposals'])}", file=sys.stderr)
    for prop in result["proposals"]:
        print(f"  [{prop['confidence']}] {prop['parameter']}: "
              f"{prop['current']} -> {prop['suggested']} "
              f"({prop['source']})", file=sys.stderr)

    return 0 if result["proposals"] else 1


if __name__ == "__main__":
    sys.exit(main())
