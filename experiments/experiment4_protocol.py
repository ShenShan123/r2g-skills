#!/usr/bin/env python3
"""Build and validate the frozen Experiment 4 graph-conversion cohort.

Selection uses only pre-conversion facts from Experiment 1: strict signoff,
source identity, mapped-cell count, and availability of the required backend
artifacts.  Graph-conversion outputs are deliberately not consulted.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "experiment4-graph-conversion-cohort-1.0"
SIZE_BANDS = {
    "small_100_499": (100, 499),
    "medium_500_1999": (500, 1999),
    "large_2000_10000": (2000, 10000),
}
TARGETS = {
    "development": {band: 2 for band in SIZE_BANDS},
    "hidden_test": {band: 8 for band in SIZE_BANDS},
}
CANARY_BAND = "medium_500_1999"
REQUIRED_RESULTS = (
    "1_2_yosys.v",
    "2_floorplan.odb",
    "3_place.odb",
    "4_cts.odb",
    "5_route.odb",
    "6_final.spef",
    "6_final.sdc",
)


def utc_now() -> str:
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


def normalized_repo(url: str) -> str:
    value = url.strip().lower().rstrip("/")
    return value[:-4] if value.endswith(".git") else value


def size_band(mapped_cells: int) -> str | None:
    for name, (minimum, maximum) in SIZE_BANDS.items():
        if minimum <= mapped_cells <= maximum:
            return name
    return None


def load_source_index(plan_path: Path) -> dict[str, dict[str, Any]]:
    plan = read_json(plan_path)
    tasks = plan.get("tasks")
    if not isinstance(tasks, list):
        raise ValueError("baseline plan has no tasks list")
    index: dict[str, dict[str, Any]] = {}
    for task in tasks:
        task_id = str(task.get("task_id") or "")
        if not task_id or task_id in index:
            raise ValueError(f"missing or duplicate baseline task id: {task_id!r}")
        index[task_id] = task
    return index


def discover_eligible(projects_root: Path, plan_path: Path) -> list[dict[str, Any]]:
    source_index = load_source_index(plan_path)
    rows: list[dict[str, Any]] = []
    for result_path in sorted(projects_root.glob("*/baseline/repair_family_probe_result.json")):
        result = read_json(result_path)
        task_id = str(result.get("task_id") or "")
        source = source_index.get(task_id)
        mapped_cells = int(result.get("mapped_cells") or 0)
        band = size_band(mapped_cells)
        run_dir = Path(str(result.get("run_dir") or ""))
        if result.get("strict_clean") is not True or band is None or source is None:
            continue
        missing = [name for name in REQUIRED_RESULTS if not (run_dir / "results" / name).is_file()]
        if missing:
            continue
        repo_url = str(source.get("repo_url") or "")
        source_group = normalized_repo(repo_url) or task_id
        rows.append(
            {
                "task_id": task_id,
                "platform": "sky130hd",
                "mapped_cells": mapped_cells,
                "size_band": band,
                "project_dir": str(result_path.parent.parent.resolve()),
                "run_dir": str(run_dir.resolve()),
                "source_repo_url": repo_url,
                "source_commit": str(source.get("commit") or ""),
                "top_module": str(source.get("top_module") or ""),
                "source_group": source_group,
                "baseline_result": str(result_path.resolve()),
                "baseline_result_sha256": sha256_file(result_path),
                "required_results": list(REQUIRED_RESULTS),
            }
        )
    return rows


def rank_key(seed: str, split: str, row: dict[str, Any]) -> str:
    material = f"{seed}|{split}|{row['size_band']}|{row['task_id']}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def choose_rows(
    candidates: list[dict[str, Any]],
    *,
    seed: str,
    split: str,
    band: str,
    count: int,
    used_groups: set[str],
) -> list[dict[str, Any]]:
    ranked = sorted(
        (row for row in candidates if row["size_band"] == band),
        key=lambda row: (rank_key(seed, split, row), row["task_id"]),
    )
    selected: list[dict[str, Any]] = []
    for row in ranked:
        if row["source_group"] in used_groups:
            continue
        selected.append(dict(row))
        used_groups.add(row["source_group"])
        if len(selected) == count:
            break
    if len(selected) != count:
        raise ValueError(f"cannot select {count} group-disjoint {split}/{band} cases")
    return selected


def build_cohort(projects_root: Path, plan_path: Path, seed: str) -> dict[str, Any]:
    eligible = discover_eligible(projects_root, plan_path)
    counts = Counter(row["size_band"] for row in eligible)
    used_groups: set[str] = set()

    canary = choose_rows(
        eligible,
        seed=seed,
        split="canary",
        band=CANARY_BAND,
        count=1,
        used_groups=used_groups,
    )
    splits: dict[str, list[dict[str, Any]]] = {"canary": canary}
    for split in ("development", "hidden_test"):
        rows: list[dict[str, Any]] = []
        for band in SIZE_BANDS:
            rows.extend(
                choose_rows(
                    eligible,
                    seed=seed,
                    split=split,
                    band=band,
                    count=TARGETS[split][band],
                    used_groups=used_groups,
                )
            )
        splits[split] = sorted(rows, key=lambda row: row["task_id"])

    payload = {
        "schema_version": SCHEMA_VERSION,
        "created_at": utc_now(),
        "status": "frozen_candidate",
        "selection_seed": seed,
        "selection_policy": {
            "uses_graph_outputs": False,
            "eligibility": "strict_clean_and_complete_four_stage_inputs",
            "cell_range": [100, 10000],
            "source_group_disjoint_across_all_splits": True,
            "size_bands": SIZE_BANDS,
            "targets": {"canary": {CANARY_BAND: 1}, **TARGETS},
        },
        "source": {
            "projects_root": str(projects_root.resolve()),
            "baseline_plan": str(plan_path.resolve()),
            "baseline_plan_sha256": sha256_file(plan_path),
            "eligible_total": len(eligible),
            "eligible_by_size_band": dict(sorted(counts.items())),
        },
        "splits": splits,
    }
    validate_cohort(payload, check_files=True)
    return payload


def validate_cohort(payload: dict[str, Any], *, check_files: bool) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unexpected Experiment 4 cohort schema")
    splits = payload.get("splits")
    if not isinstance(splits, dict):
        raise ValueError("cohort has no splits object")
    expected_counts = {"canary": 1, "development": 6, "hidden_test": 24}
    seen_tasks: set[str] = set()
    seen_groups: set[str] = set()
    for split, expected in expected_counts.items():
        rows = splits.get(split)
        if not isinstance(rows, list) or len(rows) != expected:
            raise ValueError(f"{split} must contain exactly {expected} cases")
        band_counts = Counter(row.get("size_band") for row in rows)
        expected_bands = (
            {CANARY_BAND: 1}
            if split == "canary"
            else Counter(TARGETS[split])
        )
        if band_counts != expected_bands:
            raise ValueError(f"unexpected {split} size distribution: {dict(band_counts)}")
        for row in rows:
            task_id = str(row.get("task_id") or "")
            group = str(row.get("source_group") or "")
            if not task_id or task_id in seen_tasks:
                raise ValueError(f"duplicate task across splits: {task_id}")
            if not group or group in seen_groups:
                raise ValueError(f"duplicate source group across splits: {group}")
            seen_tasks.add(task_id)
            seen_groups.add(group)
            if size_band(int(row.get("mapped_cells") or 0)) != row.get("size_band"):
                raise ValueError(f"invalid size band for {task_id}")
            if check_files:
                result_path = Path(row["baseline_result"])
                if not result_path.is_file() or sha256_file(result_path) != row["baseline_result_sha256"]:
                    raise ValueError(f"baseline evidence changed: {task_id}")
                result = read_json(result_path)
                if result.get("strict_clean") is not True:
                    raise ValueError(f"case is no longer strict clean: {task_id}")
                run_dir = Path(row["run_dir"])
                missing = [name for name in REQUIRED_RESULTS if not (run_dir / "results" / name).is_file()]
                if missing:
                    raise ValueError(f"missing four-stage inputs for {task_id}: {missing}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="build a deterministic frozen cohort")
    build.add_argument("--projects-root", type=Path, required=True)
    build.add_argument("--baseline-plan", type=Path, required=True)
    build.add_argument("--seed", default="experiment4-v1-20260906")
    build.add_argument("--output", type=Path, required=True)

    validate = subparsers.add_parser("validate", help="validate an existing cohort")
    validate.add_argument("--cohort", type=Path, required=True)
    validate.add_argument("--no-check-files", action="store_true")

    args = parser.parse_args()
    if args.command == "build":
        payload = build_cohort(args.projects_root, args.baseline_plan, args.seed)
        write_json(args.output, payload)
        print(f"[experiment4] cohort: {args.output}")
        print("[experiment4] splits: canary=1 development=6 hidden_test=24")
    else:
        validate_cohort(read_json(args.cohort), check_files=not args.no_check_files)
        print(f"[experiment4] PASS: {args.cohort}")


if __name__ == "__main__":
    main()
