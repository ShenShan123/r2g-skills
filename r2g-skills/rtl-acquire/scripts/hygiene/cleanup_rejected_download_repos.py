#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


import sys

_SKILL_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SKILL_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS_DIR))
from skill_env import (
    default_downloads_root,
    workspace_path,
)

DEFAULT_SCAN_STATE = workspace_path("scan_state/downloads_scan_state.json")
DEFAULT_DOWNLOADS = default_downloads_root()


def load_scan_state(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    repos = payload.get("repos", {})
    return repos if isinstance(repos, dict) else {}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dry-run or delete rejected repos from _downloads while keeping scan-state records."
    )
    parser.add_argument("--scan-state-json", type=Path, default=DEFAULT_SCAN_STATE)
    parser.add_argument("--downloads-root", type=Path, default=DEFAULT_DOWNLOADS)
    parser.add_argument("--delete", action="store_true", help="Actually delete repo directories. Default is dry-run.")
    args = parser.parse_args()

    repos = load_scan_state(args.scan_state_json)
    rejected = []
    for repo_name, state in sorted(repos.items()):
        if state.get("repo_decision") != "reject":
            continue
        repo_dir = args.downloads_root / repo_name
        rejected.append(
            (
                repo_name,
                repo_dir,
                bool(repo_dir.exists()),
                state.get("quality", {}).get("decision_reason", ""),
            )
        )

    print(f"rejected_repo_count {len(rejected)}")
    for repo_name, repo_dir, exists, reason in rejected:
        print(f"{repo_name},{'present' if exists else 'missing'},{repo_dir},{reason}")
        if args.delete and exists:
            shutil.rmtree(repo_dir)


if __name__ == "__main__":
    main()
