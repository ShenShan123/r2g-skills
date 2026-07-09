#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


import sys

_SKILL_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SKILL_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS_DIR))
from skill_env import (
    default_downloads_root,
    downloads_path,
    out_root_path,
    workspace_path,
)

DEFAULT_DOWNLOADS = default_downloads_root()
DEFAULT_AUDIT_CSV = downloads_path("downloads_expansion_audit_2026-04-12.csv")
DEFAULT_SCAN_STATE = workspace_path("scan_state/downloads_scan_state.json")
DEFAULT_INDEX_CSV = out_root_path("index.csv")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def repo_signature(repo_dir: Path) -> str:
    git_dir = repo_dir / ".git"
    if git_dir.exists():
        try:
            head = subprocess.run(
                ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
                check=False,
                text=True,
                capture_output=True,
            )
            if head.returncode == 0:
                return f"git:{head.stdout.strip()}"
        except Exception:
            pass
    stat = repo_dir.stat()
    return f"mtime_ns:{stat.st_mtime_ns}"


def load_scan_state(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    repos = payload.get("repos", {})
    return repos if isinstance(repos, dict) else {}


def write_scan_state(path: Path, repos: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"repos": repos}, indent=2, ensure_ascii=False), encoding="utf-8")


def aggregate_audit(audit_csv: Path) -> dict[str, dict]:
    grouped: dict[str, dict] = defaultdict(
        lambda: {
            "expanded_success_designs": [],
            "tried_failed": [],
            "tried_incomplete_designs": [],
            "not_run_designs": [],
        }
    )
    for row in csv.DictReader(audit_csv.open(newline="", encoding="utf-8")):
        repo = row.get("repo_family", "")
        if not repo:
            continue
        bucket = row.get("bucket", "")
        design = row.get("design", "")
        failure_reason = row.get("failure_reason", "")
        if bucket == "expanded_success":
            grouped[repo]["expanded_success_designs"].append(design)
        elif bucket == "tried_failed":
            grouped[repo]["tried_failed"].append(
                {
                    "design": design,
                    "failure_reason": failure_reason,
                }
            )
        elif bucket == "tried_incomplete":
            grouped[repo]["tried_incomplete_designs"].append(design)
        elif bucket == "not_run":
            grouped[repo]["not_run_designs"].append(design)

    for repo, payload in grouped.items():
        payload["expanded_success_designs"].sort()
        payload["tried_failed"] = sorted(payload["tried_failed"], key=lambda x: x["design"])
        payload["tried_incomplete_designs"].sort()
        payload["not_run_designs"].sort()
        payload["expanded_success_count"] = len(payload["expanded_success_designs"])
        payload["tried_failed_count"] = len(payload["tried_failed"])
        payload["tried_incomplete_count"] = len(payload["tried_incomplete_designs"])
        payload["not_run_count"] = len(payload["not_run_designs"])
    return grouped


def repo_from_source_path(downloads_root: Path, source_path: str) -> str:
    text = source_path.strip()
    if not text:
        return ""
    first = text.split(";", 1)[0].strip()
    try:
        rel = Path(first).resolve().relative_to(downloads_root.resolve())
    except Exception:
        return ""
    return rel.parts[0] if rel.parts else ""


def enrich_from_index(downloads_root: Path, index_csv: Path, grouped: dict[str, dict]) -> dict[str, dict]:
    for row in csv.DictReader(index_csv.open(newline="", encoding="utf-8")):
        repo = repo_from_source_path(downloads_root, row.get("source_path", ""))
        if not repo:
            continue
        payload = grouped.setdefault(
            repo,
            {
                "expanded_success_designs": [],
                "tried_failed": [],
                "tried_incomplete_designs": [],
                "not_run_designs": [],
            },
        )
        design = row.get("design", "")
        status = row.get("status", "")
        notes = row.get("notes", "")
        if status == "success":
            if design and design not in payload["expanded_success_designs"]:
                payload["expanded_success_designs"].append(design)
        elif status in {"synth_failed", "graph_failed"}:
            if design and design not in {item["design"] for item in payload["tried_failed"]}:
                payload["tried_failed"].append(
                    {
                        "design": design,
                        "failure_reason": notes,
                    }
                )

    for repo, payload in grouped.items():
        payload["expanded_success_designs"].sort()
        payload["tried_failed"] = sorted(payload["tried_failed"], key=lambda x: x["design"])
        payload["tried_incomplete_designs"].sort()
        payload["not_run_designs"].sort()
        payload["expanded_success_count"] = len(payload["expanded_success_designs"])
        payload["tried_failed_count"] = len(payload["tried_failed"])
        payload["tried_incomplete_count"] = len(payload["tried_incomplete_designs"])
        payload["not_run_count"] = len(payload["not_run_designs"])
    return grouped


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill and refresh repo-level downloads scan state from existing audit results.")
    parser.add_argument("--downloads-root", type=Path, default=DEFAULT_DOWNLOADS)
    parser.add_argument("--audit-csv", type=Path, default=DEFAULT_AUDIT_CSV)
    parser.add_argument("--index-csv", type=Path, default=DEFAULT_INDEX_CSV)
    parser.add_argument("--scan-state-json", type=Path, default=DEFAULT_SCAN_STATE)
    args = parser.parse_args()

    repos = load_scan_state(args.scan_state_json)
    audit = aggregate_audit(args.audit_csv)
    audit = enrich_from_index(args.downloads_root, args.index_csv, audit)
    timestamp = now_iso()

    refreshed = 0
    for repo_name, audit_payload in audit.items():
        repo_dir = args.downloads_root / repo_name
        if not repo_dir.exists():
            continue
        state = dict(repos.get(repo_name, {}))
        state["signature"] = repo_signature(repo_dir)
        state["status"] = "scanned"
        state["backfilled_from_audit"] = True
        state["last_audit_refresh_at"] = timestamp
        state["audit_source_csv"] = str(args.audit_csv)
        state["scan_count"] = max(1, int(state.get("scan_count", 0)))
        state["audit"] = audit_payload
        repos[repo_name] = state
        refreshed += 1

    write_scan_state(args.scan_state_json, repos)
    print(f"wrote {args.scan_state_json}")
    print(f"backfilled_repos {refreshed}")


if __name__ == "__main__":
    main()
