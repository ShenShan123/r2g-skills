#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path


import sys

_SKILL_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SKILL_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS_DIR))
from skill_env import (
    out_root_path,
    workspace_path,
)

DEFAULT_SCAN_STATE = workspace_path("scan_state/downloads_scan_state.json")
DEFAULT_INDEX = out_root_path("index.csv")
DEFAULT_OUT_CSV = workspace_path("quality/download_repo_quality.csv")
DEFAULT_OUT_MD = workspace_path("quality/download_repo_quality.md")
DEFAULT_REJECT_CSV = workspace_path("quality/download_repo_rejects.csv")
DEFAULT_QUARANTINE_MD = workspace_path("quality/download_repo_quarantine.md")


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def infer_repo_name(row: dict[str, str]) -> str:
    notes = row.get("notes", "")
    m = re.search(r"(?:^|[;,\s])repo=([^;,\s]+)", notes)
    if m:
        return m.group(1)
    source_path = row.get("source_path", "")
    marker = "/_downloads/"
    if marker in source_path:
        tail = source_path.split(marker, 1)[1]
        return tail.split("/", 1)[0]
    parts = source_path.split(";")
    if parts and marker in parts[0]:
        tail = parts[0].split(marker, 1)[1]
        return tail.split("/", 1)[0]
    return ""


def size_bucket(cells: int) -> str:
    if cells >= 10000:
        return "large"
    if cells >= 1000:
        return "medium"
    return "small"


def decide_repo(
    success_count: int,
    medium_count: int,
    large_count: int,
    fail_count: int,
    not_run_count: int,
    reject_if_all_small: bool,
    min_repo_success: int,
    min_medium_large_success: int,
    min_failures_before_reject: int,
    max_fail_ratio: float,
) -> tuple[str, str]:
    medium_large = medium_count + large_count
    if success_count == 0 and fail_count >= min_failures_before_reject and not_run_count == 0:
        return "reject", "repeated failures without any successful expansion"
    if reject_if_all_small and success_count > 0 and medium_large == 0 and not_run_count == 0:
        return "reject", "only small successful designs and no remaining unrun designs"
    allowed_failures = max(int(success_count / max(1e-9, 1.0 - max_fail_ratio)), min_failures_before_reject)
    if medium_large > 0 and fail_count <= allowed_failures:
        return "keep", "contains medium/large successes and acceptable failure ratio"
    if success_count >= min_repo_success and medium_large >= min_medium_large_success:
        return "keep", "meets success and medium/large thresholds"
    if success_count > 0:
        return "conditional", "has successes but quality is mostly small or limited"
    return "conditional", "insufficient evidence to keep or reject automatically"


def main() -> None:
    parser = argparse.ArgumentParser(description="Score repo quality from scan-state and expanded index.")
    parser.add_argument("--scan-state-json", type=Path, default=DEFAULT_SCAN_STATE)
    parser.add_argument("--index-csv", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--out-csv", type=Path, default=DEFAULT_OUT_CSV)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_OUT_MD)
    parser.add_argument("--reject-csv", type=Path, default=DEFAULT_REJECT_CSV)
    parser.add_argument("--quarantine-md", type=Path, default=DEFAULT_QUARANTINE_MD)
    parser.add_argument("--reject-if-all-small", action="store_true")
    parser.add_argument("--min-repo-success", type=int, default=2)
    parser.add_argument("--min-medium-large-success", type=int, default=1)
    parser.add_argument("--min-failures-before-reject", type=int, default=3)
    parser.add_argument("--max-fail-ratio", type=float, default=0.85)
    args = parser.parse_args()

    payload = load_json(args.scan_state_json)
    repos = payload.get("repos", {})
    if not isinstance(repos, dict):
        repos = {}
    rows = load_rows(args.index_csv)

    success_by_repo: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        if row.get("status") != "success":
            continue
        repo_name = infer_repo_name(row)
        if not repo_name:
            continue
        success_by_repo.setdefault(repo_name, []).append(row)

    out_rows: list[dict[str, str]] = []
    reject_rows: list[dict[str, str]] = []
    for repo_name in sorted(repos):
        state = dict(repos[repo_name])
        audit = state.get("audit", {})
        success_designs = list(audit.get("expanded_success_designs", []))
        failed_designs = list(audit.get("tried_failed", []))
        not_run_designs = list(audit.get("not_run_designs", []))
        success_rows = success_by_repo.get(repo_name, [])

        small = medium = large = 0
        cells_total = 0
        for row in success_rows:
            cells = int(row.get("cells", 0) or 0)
            cells_total += cells
            bucket = size_bucket(cells)
            if bucket == "small":
                small += 1
            elif bucket == "medium":
                medium += 1
            else:
                large += 1

        expanded_success_count = len(success_designs) if success_designs else len(success_rows)
        avg_cells = int(cells_total / len(success_rows)) if success_rows else 0
        repo_decision, reason = decide_repo(
            expanded_success_count,
            medium,
            large,
            len(failed_designs),
            len(not_run_designs),
            args.reject_if_all_small,
            args.min_repo_success,
            args.min_medium_large_success,
            args.min_failures_before_reject,
            args.max_fail_ratio,
        )

        state["repo_decision"] = repo_decision
        state["quality"] = {
            "decision_reason": reason,
            "expanded_success_count": expanded_success_count,
            "tried_failed_count": len(failed_designs),
            "not_run_count": len(not_run_designs),
            "small_success_count": small,
            "medium_success_count": medium,
            "large_success_count": large,
            "avg_success_cells": avg_cells,
        }
        repos[repo_name] = state

        out_row = {
            "repo_name": repo_name,
            "repo_decision": repo_decision,
            "decision_reason": reason,
            "expanded_success_count": str(expanded_success_count),
            "tried_failed_count": str(len(failed_designs)),
            "not_run_count": str(len(not_run_designs)),
            "small_success_count": str(small),
            "medium_success_count": str(medium),
            "large_success_count": str(large),
            "avg_success_cells": str(avg_cells),
            "scan_count": str(state.get("scan_count", 0)),
            "skip_unchanged_count": str(state.get("skip_unchanged_count", 0)),
        }
        out_rows.append(out_row)
        if repo_decision == "reject":
            reject_rows.append(
                {
                    "repo_name": repo_name,
                    "repo_decision": repo_decision,
                    "decision_reason": reason,
                }
            )

    payload["repos"] = repos
    args.scan_state_json.parent.mkdir(parents=True, exist_ok=True)
    args.scan_state_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    fieldnames = [
        "repo_name",
        "repo_decision",
        "decision_reason",
        "expanded_success_count",
        "tried_failed_count",
        "not_run_count",
        "small_success_count",
        "medium_success_count",
        "large_success_count",
        "avg_success_cells",
        "scan_count",
        "skip_unchanged_count",
    ]
    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(out_rows)

    with args.reject_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["repo_name", "repo_decision", "decision_reason"])
        writer.writeheader()
        writer.writerows(reject_rows)

    with args.out_md.open("w", encoding="utf-8") as fh:
        fh.write("# Repo Quality Scores\n\n")
        fh.write(f"- repo_count: {len(out_rows)}\n")
        fh.write(f"- reject_count: {len(reject_rows)}\n\n")
        fh.write("| repo | decision | success | failed | not_run | small | medium | large | avg_cells | reason |\n")
        fh.write("|---|---|---:|---:|---:|---:|---:|---:|---:|---|\n")
        for row in out_rows:
            fh.write(
                f"| {row['repo_name']} | {row['repo_decision']} | {row['expanded_success_count']} | "
                f"{row['tried_failed_count']} | {row['not_run_count']} | {row['small_success_count']} | "
                f"{row['medium_success_count']} | {row['large_success_count']} | {row['avg_success_cells']} | "
                f"{row['decision_reason']} |\n"
            )

    with args.quarantine_md.open("w", encoding="utf-8") as fh:
        fh.write("# Repo Quarantine Candidates\n\n")
        if not reject_rows:
            fh.write("- none\n")
        else:
            for row in reject_rows:
                fh.write(f"- {row['repo_name']}: {row['decision_reason']}\n")

    print(f"wrote {args.out_csv}")
    print(f"wrote {args.out_md}")
    print(f"wrote {args.reject_csv}")
    print(f"wrote {args.quarantine_md}")


if __name__ == "__main__":
    main()
