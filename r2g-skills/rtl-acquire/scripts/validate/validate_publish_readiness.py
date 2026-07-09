#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path
import re
import sys


import sys

_SKILL_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SKILL_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS_DIR))
from skill_env import (
    default_out_root,
    default_seed_root,
    out_root_path,
    seed_root_path,
    skill_reference_path,
    workspace_path,
)

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_ROOT = SCRIPT_DIR.parent
for path in (SCRIPTS_ROOT, SCRIPT_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from common.io_utils import load_json, load_rows, write_json


DEFAULT_EXTERNAL_INDEX = out_root_path("index.csv")
DEFAULT_EXTERNAL_ROOT = default_out_root()
DEFAULT_ORFS_INDEX = seed_root_path("index.csv")
DEFAULT_ORFS_ROOT = default_seed_root()
DEFAULT_DESIGN_SCORES = workspace_path("quality/design_quality_scores.csv")
DEFAULT_REPO_QUALITY = workspace_path("quality/download_repo_quality.csv")
DEFAULT_DUPLICATE_SUMMARY = workspace_path("audits/mapped_netlist_duplicate_summary.json")
DEFAULT_PUBLISH_POLICY = skill_reference_path("publish_policy.json")
DEFAULT_PUBLISH_ELIGIBLE = workspace_path("manifests/publish_eligible_designs.csv")
DEFAULT_OUT_JSON = workspace_path("quality/publish_validation.json")
DEFAULT_OUT_MD = workspace_path("quality/publish_validation.md")

def check_required_columns(rows: list[dict[str, str]], required: list[str]) -> tuple[bool, list[str]]:
    if not rows:
        return False, ["csv has no rows"]
    keys = set(rows[0].keys())
    missing = [col for col in required if col not in keys]
    return not missing, missing


def validate_success_dirs(rows: list[dict[str, str]], root: Path) -> tuple[bool, list[str]]:
    issues: list[str] = []
    for row in rows:
        if row.get("status") != "success":
            continue
        design = row.get("design", "")
        if not design:
            issues.append("success row without design")
            continue
        ddir = root / design
        mapped = ddir / "mapped_netlist.v"
        if not ddir.exists():
            issues.append(f"missing design dir: {design}")
            continue
        # netlist_graph.pt is the corpus format; the legacy per-design 30pt
        # name is tolerated so a partially-migrated corpus still validates.
        if not (ddir / "netlist_graph.pt").exists() and not (ddir / f"{design}_1_1_yosys.pt").exists():
            issues.append(f"missing pt: {design}")
        if not mapped.exists():
            issues.append(f"missing mapped_netlist.v: {design}")
    return not issues, issues


def validate_design_uniqueness(rows: list[dict[str, str]]) -> tuple[bool, list[str]]:
    counts = Counter(row.get("design", "") for row in rows if row.get("design"))
    duplicates = [design for design, count in counts.items() if count > 1]
    return not duplicates, [f"duplicate design in index: {design}" for design in duplicates]


def validate_score_csv(path: Path, keys: list[str]) -> tuple[bool, list[str]]:
    rows = load_rows(path)
    if not rows:
        return False, [f"missing or empty score file: {path}"]
    if not any(key in rows[0] for key in keys):
        return False, [f"score file missing key columns {keys}: {path}"]
    return True, []


def load_publish_eligible_designs(path: Path) -> set[str]:
    rows = load_rows(path)
    selected: set[str] = set()
    for row in rows:
        if str(row.get("publish_eligible", "")).strip().lower() == "true" and row.get("design"):
            selected.add(str(row["design"]))
    return selected


def validate_cell_stats(rows: list[dict[str, str]], root: Path) -> tuple[bool, list[str]]:
    """Every success design must carry a cell_stats.json with cells > 0.

    Replaces the retired 30pt mapping-coverage check: the cell-type vocabulary
    is now def-graph's runtime per-platform map (UNKNOWN is a first-class id),
    so 'unmapped cell' is no longer a publish defect — but a silently-empty
    graph (0 cells) under a 'success' status still is."""
    issues: list[str] = []
    for row in rows:
        if row.get("status") != "success":
            continue
        design = row.get("design", "")
        stats_path = root / design / "cell_stats.json"
        if not stats_path.exists():
            issues.append(f"missing cell_stats.json: {design}")
            continue
        payload = load_json(stats_path)
        try:
            cells = int(payload.get("cells", 0) or 0)
        except Exception:
            cells = 0
        if cells <= 0:
            issues.append(f"success design with zero cells: {design}")
    return not issues, issues


def validate_graph_stat_drift(
    path: Path,
    *,
    eligible_designs: set[str],
    min_complexity: float,
    max_dominant_gate_share: float,
) -> tuple[bool, list[str]]:
    rows = load_rows(path)
    if not rows:
        return False, [f"missing or empty design quality file: {path}"]
    issues: list[str] = []
    for row in rows:
        design = row.get("design", "")
        if eligible_designs and design not in eligible_designs:
            continue
        try:
            complexity = float(row.get("graph_complexity_score", 0.0) or 0.0)
        except Exception:
            complexity = 0.0
        try:
            dom_share = float(
                row.get("graph_dominant_gate_share", row.get("dominant_cell_share", 1.0)) or 1.0
            )
        except Exception:
            dom_share = 1.0
        action = (row.get("design_action") or "").strip().lower()
        if action == "keep" and complexity < min_complexity:
            issues.append(f"keep design below complexity threshold: {design} ({complexity})")
        if dom_share > max_dominant_gate_share:
            issues.append(f"dominant gate share too high: {design} ({dom_share})")
    return not issues, issues


def validate_duplicate_leakage(path: Path, *, max_duplicate_design_count: int) -> tuple[bool, list[str]]:
    payload = load_json(path)
    if not payload:
        return False, [f"missing duplicate summary: {path}"]
    count = int(payload.get("duplicate_design_count", 0) or 0)
    if count > max_duplicate_design_count:
        return False, [f"duplicate_design_count={count} exceeds threshold={max_duplicate_design_count}"]
    return True, []


def emit_md(path: Path, payload: dict) -> None:
    lines = [
        "# Publish Validation",
        "",
        f"- pass: `{payload['pass']}`",
        f"- external_success_rows: `{payload['external_success_rows']}`",
        f"- orfs_success_rows: `{payload['orfs_success_rows']}`",
        "",
        "## Checks",
    ]
    for item in payload["checks"]:
        lines.append(f"- `{item['name']}`: `{item['pass']}`")
        for issue in item.get("issues", []):
            lines.append(f"  - {issue}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only validation gate before publish/manifest refresh.")
    parser.add_argument("--external-index", type=Path, default=DEFAULT_EXTERNAL_INDEX)
    parser.add_argument("--external-root", type=Path, default=DEFAULT_EXTERNAL_ROOT)
    parser.add_argument("--orfs-index", type=Path, default=DEFAULT_ORFS_INDEX)
    parser.add_argument("--orfs-root", type=Path, default=DEFAULT_ORFS_ROOT)
    parser.add_argument("--design-scores", type=Path, default=DEFAULT_DESIGN_SCORES)
    parser.add_argument("--repo-quality", type=Path, default=DEFAULT_REPO_QUALITY)
    parser.add_argument("--duplicate-summary", type=Path, default=DEFAULT_DUPLICATE_SUMMARY)
    parser.add_argument("--publish-policy-json", type=Path, default=DEFAULT_PUBLISH_POLICY)
    parser.add_argument("--publish-eligible-csv", type=Path, default=DEFAULT_PUBLISH_ELIGIBLE)
    parser.add_argument("--out-json", type=Path, default=DEFAULT_OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_OUT_MD)
    args = parser.parse_args()
    publish_policy = load_json(args.publish_policy_json)
    eligible_designs = load_publish_eligible_designs(args.publish_eligible_csv)

    external_rows = load_rows(args.external_index)
    orfs_rows = load_rows(args.orfs_index)
    checks: list[dict] = []

    ok, issues = check_required_columns(external_rows, ["design", "status", "top"])
    checks.append({"name": "external_index_schema", "pass": ok, "issues": issues})

    ok, issues = validate_design_uniqueness(external_rows)
    checks.append({"name": "external_index_uniqueness", "pass": ok, "issues": issues})

    ok, issues = validate_success_dirs(external_rows, args.external_root)
    checks.append({"name": "external_success_artifacts", "pass": ok, "issues": issues[:50]})

    # The ORFS seed corpus is optional (a fresh r2g setup has none) — absent
    # index => the seed checks SKIP cleanly instead of failing the gate.
    if args.orfs_index.exists():
        ok, issues = check_required_columns(orfs_rows, ["design", "status", "top"])
        checks.append({"name": "orfs_index_schema", "pass": ok, "issues": issues})

        ok, issues = validate_success_dirs(orfs_rows, args.orfs_root)
        checks.append({"name": "orfs_success_artifacts", "pass": ok, "issues": issues[:50]})
    else:
        checks.append({"name": "orfs_index_schema", "pass": True,
                       "issues": [f"skipped: no seed index at {args.orfs_index}"]})

    ok, issues = validate_score_csv(args.design_scores, ["design"])
    checks.append({"name": "design_quality_scores", "pass": ok, "issues": issues})

    ok, issues = validate_score_csv(args.repo_quality, ["repo_key", "repo_name"])
    checks.append({"name": "repo_quality_scores", "pass": ok, "issues": issues})

    ok = bool(eligible_designs)
    issues = [] if ok else [f"missing or empty publish eligibility file: {args.publish_eligible_csv}"]
    checks.append({"name": "publish_eligible_designs", "pass": ok, "issues": issues})

    ok, issues = validate_cell_stats(external_rows, args.external_root)
    checks.append({"name": "external_cell_stats", "pass": ok, "issues": issues[:50]})

    if args.orfs_index.exists():
        ok, issues = validate_cell_stats(orfs_rows, args.orfs_root)
        checks.append({"name": "orfs_cell_stats", "pass": ok, "issues": issues[:50]})

    ok, issues = validate_graph_stat_drift(
        args.design_scores,
        eligible_designs=eligible_designs,
        min_complexity=float(publish_policy.get("min_nontrivial_complexity_score", 0.02) or 0.02),
        max_dominant_gate_share=float(publish_policy.get("max_dominant_gate_share", 0.98) or 0.98),
    )
    checks.append({"name": "graph_stat_drift", "pass": ok, "issues": issues[:50]})

    ok, issues = validate_duplicate_leakage(
        args.duplicate_summary,
        max_duplicate_design_count=int(publish_policy.get("max_duplicate_design_count", 25) or 25),
    )
    checks.append({"name": "duplicate_leakage", "pass": ok, "issues": issues})

    payload = {
        "pass": all(item["pass"] for item in checks),
        "external_success_rows": sum(1 for row in external_rows if row.get("status") == "success"),
        "orfs_success_rows": sum(1 for row in orfs_rows if row.get("status") == "success"),
        "publish_eligible_external_rows": len(eligible_designs),
        "checks": checks,
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.out_json, payload)
    emit_md(args.out_md, payload)
    print(args.out_json)


if __name__ == "__main__":
    main()
