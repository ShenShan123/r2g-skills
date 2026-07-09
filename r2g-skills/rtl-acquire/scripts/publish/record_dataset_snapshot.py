#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path
import sys

import sys

_SKILL_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SKILL_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS_DIR))
from skill_env import (
    default_merged_manifest,
    default_workspace_root,
    skill_reference_path,
    workspace_path,
)

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_ROOT = SCRIPT_DIR.parent
for path in (SCRIPTS_ROOT, SCRIPT_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from common.io_utils import load_json, now_iso, write_json
from common.manifest_utils import file_fingerprint


DEFAULT_POLICY = skill_reference_path("versioning_policy.json")
DEFAULT_OUT_JSON = workspace_path("runs/dataset_snapshot_latest.json")
DEFAULT_OUT_MD = workspace_path("runs/dataset_snapshot_latest.md")


def main() -> None:
    parser = argparse.ArgumentParser(description="Record a dataset snapshot and optionally DVC-track key artifacts.")
    parser.add_argument("--policy-json", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--out-json", type=Path, default=DEFAULT_OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_OUT_MD)
    args = parser.parse_args()

    policy = load_json(args.policy_json)
    workspace = default_workspace_root()
    named_artifacts = {
        "merged_manifest": default_merged_manifest(),
        "publish_eligible_designs": workspace / "manifests" / "publish_eligible_designs.csv",
        "publish_validation": workspace / "quality" / "publish_validation.json",
    }
    tracked_paths = [
        named_artifacts.get(str(item), Path(str(item)).expanduser())
        for item in (policy.get("tracked_artifacts") or [])
    ]
    snapshot = {
        "generated_at": now_iso(),
        "policy_json": str(args.policy_json),
        "tracked_artifacts": [],
        "dvc_available": bool(shutil.which("dvc")),
        "dvc_enabled": bool(policy.get("enable_dvc_if_available", False)),
        "dvc_tracked": False,
    }
    for path in tracked_paths:
        snapshot["tracked_artifacts"].append({"path": str(path), "fingerprint": file_fingerprint(path)})

    if snapshot["dvc_available"] and snapshot["dvc_enabled"]:
        existing = [item["path"] for item in snapshot["tracked_artifacts"] if item["fingerprint"].get("exists")]
        if existing:
            subprocess.run(["dvc", "add", *existing], check=False)
            snapshot["dvc_tracked"] = True

    write_json(args.out_json, snapshot)
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    with args.out_md.open("w", encoding="utf-8") as fh:
        fh.write("# Dataset Snapshot\n\n")
        fh.write(f"- generated_at: {snapshot['generated_at']}\n")
        fh.write(f"- policy_json: {args.policy_json}\n")
        fh.write(f"- dvc_available: {snapshot['dvc_available']}\n")
        fh.write(f"- dvc_enabled: {snapshot['dvc_enabled']}\n")
        fh.write(f"- dvc_tracked: {snapshot['dvc_tracked']}\n\n")
        fh.write("| artifact | exists | size | sha16 |\n")
        fh.write("|---|---|---|---|\n")
        for item in snapshot["tracked_artifacts"]:
            fp = item["fingerprint"]
            fh.write(f"| {item['path']} | {fp.get('exists', False)} | {fp.get('size', '')} | {fp.get('sha16', '')} |\n")

    print(f"wrote {args.out_json}")
    print(f"wrote {args.out_md}")


if __name__ == "__main__":
    main()
