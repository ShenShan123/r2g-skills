#!/usr/bin/env python3
"""Audit and freeze the prepared Nangate45 Experiment 3 campaign."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any


MODELS = ("gpt", "claude", "qwen")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--remote-runner-sha256", required=True)
    args = parser.parse_args()
    root = args.campaign_root.resolve()
    repo = args.repo.resolve()

    sources = {
        "split_manifest": root / "data/split_manifest.json",
        "clean_sentinels": root / "data/clean_sentinels.json",
        "preparation_manifest": root / "preparation_manifest.json",
        "context_manifest": root / "model_contexts/context_manifest.json",
        "action_policy": root / "protocol/action_policy.json",
        "candidate_schema": root / "protocol/candidate.schema.json",
        "a_propose_prompt": root / "protocol/a_propose_system_prompt.md",
        "pure_llm_prompt": root / "protocol/pure_llm_system_prompt.md",
        "resource_limits": root / "protocol/resource_limits.json",
        "model_routes": root / "protocol/model_routes.json",
        "runtime_203": root / "protocol/runtime_203.env",
        "split_protocol": root / "protocol/split_and_reproduction_protocol.md",
        "resolved_model_routes": root / "resolved_model_routes.json",
    }
    missing = [name for name, path in sources.items() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"missing frozen artifacts: {missing}")

    assignments = read_json(sources["split_manifest"])["assignments"]
    counts = Counter(row["split"] for row in assignments)
    sentinel_count = len(read_json(sources["clean_sentinels"])["records"])
    expected = {"a_propose": 12, "a_validation": 8, "b_heldout": 32}
    if dict(counts) != expected or sentinel_count != 8:
        raise ValueError(f"unexpected cohort counts: {dict(counts)}, sentinels={sentinel_count}")

    resources = read_json(sources["resource_limits"])
    policy = read_json(sources["action_policy"])
    if resources.get("status") != "frozen":
        raise ValueError("resource limits are not frozen")
    if resources.get("failure_domains") != ["antenna_drc"]:
        raise ValueError("A-learning domain is not frozen to antenna_drc")
    if policy.get("platform") != "nangate45":
        raise ValueError("action policy is not bound to Nangate45")

    canary_results = []
    for model in MODELS:
        path = root / "canary/routes_attempt2" / f"{model}.json"
        payload = read_json(path)
        accepted = len(payload.get("accepted") or [])
        rejected = len(payload.get("rejected") or [])
        passed = (
            payload.get("status") == "completed"
            and payload.get("contract_valid") is True
            and accepted >= 1
            and rejected == 0
        )
        canary_results.append(
            {
                "model_key": model,
                "model_id": payload.get("model_id"),
                "resolved_endpoint": payload.get("resolved_endpoint"),
                "status": "passed" if passed else "failed",
                "accepted_candidates": accepted,
                "rejected_candidates": rejected,
                "total_tokens": (payload.get("usage") or {}).get("total_tokens"),
                "artifact_sha256": sha256_file(path),
            }
        )
    if any(row["status"] != "passed" for row in canary_results):
        raise ValueError(f"route canary failed: {canary_results}")
    write_json(
        root / "canary/routes/route_canary_summary.json",
        {
            "schema_version": "experiment3-route-canary-summary-1.0",
            "created_at": now(),
            "status": "passed",
            "scoring": False,
            "results": canary_results,
        },
    )

    runners = {
        "campaign": repo / "experiments/run_experiment3_campaign.py",
        "ablation": repo / "experiments/run_experiment3_ablation.py",
        "proposals": repo / "experiments/run_experiment3_llm_proposals.py",
        "candidate_bank": repo / "experiments/experiment3_candidate_bank.py",
        "protocol": repo / "experiments/experiment3_protocol.py",
        "preparer": repo / "experiments/prepare_nangate45_experiment3.py",
        "freezer": Path(__file__).resolve(),
    }
    local_ablation_hash = sha256_file(runners["ablation"])
    if local_ablation_hash != args.remote_runner_sha256:
        raise ValueError("remote EDA runner hash differs from controller runner")
    commit = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    write_json(
        root / "execution_manifest.json",
        {
            "schema_version": "nangate45-experiment3-execution-manifest-1.0",
            "created_at": now(),
            "status": "frozen",
            "campaign_id": root.name,
            "controller_host": "memlab-gpu2-208",
            "eda_host": "memlab-gpu-203",
            "cohort_counts": {**expected, "clean_sentinel": sentinel_count},
            "fixed_task": {
                "platform": "nangate45",
                "frequency_mhz": 100,
                "input_output_delay_fraction": 0.2,
                "cores_per_trial": 4,
                "timeout_seconds_per_trial": 7200,
                "signoff": "strict",
            },
            "arms": [
                "M0",
                "M1",
                "M2",
                "M3",
                "pure_llm_gpt",
                "pure_llm_claude",
                "pure_llm_qwen",
            ],
            "source_artifacts": {
                name: {"path": str(path), "sha256": sha256_file(path)}
                for name, path in sources.items()
            },
            "runners": {
                name: {"path": str(path), "sha256": sha256_file(path), "repo_commit": commit}
                for name, path in runners.items()
            },
            "remote_eda_runner_sha256": args.remote_runner_sha256,
            "route_canary_summary_sha256": sha256_file(
                root / "canary/routes/route_canary_summary.json"
            ),
            "credentials_persisted": False,
            "b_heldout_visible_during_learning": False,
            "a_validation_visible_to_models": False,
        },
    )
    print(root / "execution_manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
