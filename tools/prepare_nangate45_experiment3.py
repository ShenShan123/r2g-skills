#!/usr/bin/env python3
"""Prepare the frozen Nangate45 Experiment 3 campaign from reproduced baselines."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any


PORTABLE_DIRS = ("rtl", "constraints", "input", "reports", "evidence")
PORTABLE_FILES = (
    "repair_family_probe_input.json",
    "repair_family_probe_result.json",
    "metadata.json",
)


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


def copy_baseline(source: Path, destination: Path) -> dict[str, Any]:
    destination.mkdir(parents=True, exist_ok=True)
    for name in PORTABLE_FILES:
        path = source / name
        if not path.is_file():
            raise FileNotFoundError(path)
        shutil.copy2(path, destination / name)
    for name in PORTABLE_DIRS:
        path = source / name
        if path.is_dir():
            shutil.copytree(path, destination / name, dirs_exist_ok=True)
    frozen = read_json(destination / "repair_family_probe_input.json")
    result = read_json(destination / "repair_family_probe_result.json")
    if frozen.get("protected_task", {}).get("platform") != "nangate45":
        raise ValueError(f"non-Nangate45 baseline: {source}")
    return {
        "task_id": result["task_id"],
        "input_sha256": sha256_file(destination / "repair_family_probe_input.json"),
        "result_sha256": sha256_file(destination / "repair_family_probe_result.json"),
        "protected_task_digest": result.get("protected_task_digest"),
    }


def failure_domain(signatures: list[str]) -> str:
    return "setup_timing" if "SETUP_TIMING" in signatures else "antenna_drc"


def model_task(task_id: str, baseline: Path) -> dict[str, Any]:
    result = read_json(baseline / "repair_family_probe_result.json")
    frozen = read_json(baseline / "repair_family_probe_input.json")
    signatures = list(result.get("normalized_failure_signature") or [])
    protected = frozen.get("protected_task") or {}
    return {
        "task_id": task_id,
        "failure_domain": failure_domain(signatures),
        "baseline_failure_signatures": signatures,
        "baseline_metrics": result.get("metrics") or {},
        "mapped_cells": (result.get("metrics") or {}).get("mapped_cells"),
        "top_module": protected.get("top_module"),
        "clock_port": protected.get("clock_port"),
        "target_frequency_mhz": protected.get("target_frequency_mhz"),
        "platform": protected.get("platform"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a-source", type=Path, required=True)
    parser.add_argument("--b-source", type=Path, required=True)
    parser.add_argument("--campaign-root", type=Path, required=True)
    args = parser.parse_args()

    root = args.campaign_root.resolve()
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"refusing to overwrite non-empty campaign: {root}")
    root.mkdir(parents=True, exist_ok=True)

    split_results_path = args.a_source / "reports/split_reproduction_results.json"
    b_results_path = args.b_source / "reports/reproduction_results.json"
    split_results = read_json(split_results_path)
    b_results = read_json(b_results_path)
    if split_results.get("decision_counts") != {
        "admitted_matching_failure": 20,
        "stable_clean": 8,
    }:
        raise ValueError("A/validation/sentinel reproduction is not fully admitted")
    if b_results.get("decision_counts") != {"admitted_matching_failure": 32}:
        raise ValueError("B reproduction is not fully admitted")

    assignments: list[dict[str, Any]] = []
    sentinel_records: list[dict[str, Any]] = []
    bundle_records: list[dict[str, Any]] = []
    context_tasks: dict[str, list[dict[str, Any]]] = {
        "a_propose": [],
        "a_validation": [],
        "b_heldout": [],
    }

    for row in split_results["records"]:
        partition = row["partition"]
        task_id = row["task_id"]
        expected = "stable_clean" if partition == "clean_sentinel" else "admitted_matching_failure"
        if row.get("admission_decision") != expected:
            raise ValueError(f"unadmitted split task: {task_id}")
        source = args.a_source / "jobs" / task_id / "project"
        kind = "sentinels" if partition == "clean_sentinel" else "challenges"
        destination = root / "inputs" / kind / task_id / "baseline"
        record = copy_baseline(source, destination)
        record.update({"kind": kind, "source_reproduction": str(args.a_source)})
        bundle_records.append(record)
        if partition == "clean_sentinel":
            sentinel_records.append({"task_id": task_id, "source_partition": partition})
        else:
            assignments.append({"task_id": task_id, "split": partition})
            context_tasks[partition].append(model_task(task_id, destination))

    for row in b_results["records"]:
        if row.get("admission_decision") != "admitted_matching_failure":
            raise ValueError(f"unadmitted B task: {row['task_id']}")
        task_id = row["task_id"]
        source = args.b_source / "jobs" / task_id / "project"
        destination = root / "inputs/challenges" / task_id / "baseline"
        record = copy_baseline(source, destination)
        record.update({"kind": "challenges", "source_reproduction": str(args.b_source)})
        bundle_records.append(record)
        assignments.append({"task_id": task_id, "split": "b_heldout"})
        context_tasks["b_heldout"].append(model_task(task_id, destination))

    write_json(
        root / "data/split_manifest.json",
        {
            "schema_version": "experiment3-split-manifest-1.0",
            "created_at": now(),
            "platform": "nangate45",
            "selection_uses_repair_outcomes": False,
            "assignments": sorted(assignments, key=lambda row: (row["split"], row["task_id"])),
        },
    )
    write_json(
        root / "data/clean_sentinels.json",
        {
            "schema_version": "experiment3-clean-sentinels-1.0",
            "created_at": now(),
            "records": sorted(sentinel_records, key=lambda row: row["task_id"]),
        },
    )
    write_json(
        root / "inputs/bundle_manifest.json",
        {
            "schema_version": "experiment3-portable-input-bundle-1.0",
            "created_at": now(),
            "task_count": len(bundle_records),
            "records": sorted(bundle_records, key=lambda row: row["task_id"]),
        },
    )

    a_tasks = sorted(context_tasks["a_propose"], key=lambda row: row["task_id"])
    write_json(
        root / "model_contexts/a_propose/antenna_drc.json",
        {
            "schema_version": "experiment3-model-context-1.0",
            "split": "a_propose",
            "failure_domain": "antenna_drc",
            "repair_outcomes_included": False,
            "tasks": a_tasks,
        },
    )
    for task in context_tasks["b_heldout"]:
        write_json(
            root / "model_contexts/b_heldout" / f"{task['task_id']}.json",
            {
                "schema_version": "experiment3-model-context-1.0",
                "split": "b_heldout",
                "task_isolation": "single_task_only",
                "failure_domain": task["failure_domain"],
                "repair_outcomes_included": False,
                "tasks": [task],
            },
        )
    write_json(
        root / "model_contexts/context_manifest.json",
        {
            "schema_version": "experiment3-context-manifest-1.0",
            "created_at": now(),
            "a_propose_tasks": len(a_tasks),
            "a_validation_hidden_from_models": len(context_tasks["a_validation"]),
            "b_heldout_isolated_contexts": len(context_tasks["b_heldout"]),
            "repair_outcomes_included": False,
        },
    )
    write_json(
        root / "preparation_manifest.json",
        {
            "schema_version": "nangate45-experiment3-preparation-1.0",
            "created_at": now(),
            "source_artifacts": {
                str(split_results_path): sha256_file(split_results_path),
                str(b_results_path): sha256_file(b_results_path),
            },
            "counts": {
                "a_propose": len(a_tasks),
                "a_validation": len(context_tasks["a_validation"]),
                "b_heldout": len(context_tasks["b_heldout"]),
                "clean_sentinel": len(sentinel_records),
            },
            "portable_bundle_tasks": len(bundle_records),
        },
    )
    print(root / "preparation_manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
