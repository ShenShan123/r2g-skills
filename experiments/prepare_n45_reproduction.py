#!/usr/bin/env python3
"""Prepare a bounded, deterministic Nangate45 failure-reproduction campaign."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil


SEED = "n45-independent-reproduction-v1"
QUOTAS = {
    "setup_involved": 2,
    "antenna_single_layer": 8,
    "antenna_two_layer": 10,
    "antenna_multi_layer": 12,
}


def load(path: Path):
    return json.loads(path.read_text())


def save(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def failure_class(record: dict) -> str:
    signatures = record["first_run_signatures"]
    if "SETUP_TIMING" in signatures:
        return "setup_involved"
    layers = {item for item in signatures if item.startswith("DRC:METAL")}
    if len(layers) == 1:
        return "antenna_single_layer"
    if len(layers) == 2:
        return "antenna_two_layer"
    return "antenna_multi_layer"


def size_band(record: dict) -> str:
    cells = int(record.get("mapped_cells") or 0)
    if cells <= 500:
        return "small_le_500"
    if cells <= 2000:
        return "medium_501_2000"
    return "large_gt_2000"


def stable_key(record: dict) -> str:
    material = "|".join((SEED, record["task_id"], record.get("repo_url") or ""))
    return hashlib.sha256(material.encode()).hexdigest()


def select_group(records: list[dict], quota: int) -> list[dict]:
    bands = {name: [] for name in ("small_le_500", "medium_501_2000", "large_gt_2000")}
    for record in sorted(records, key=stable_key):
        bands[size_band(record)].append(record)

    selected: list[dict] = []
    used_repos: set[str] = set()
    band_order = list(bands)
    # First pass favors source-family independence and round-robins size bands.
    while len(selected) < quota:
        progressed = False
        for band in band_order:
            for record in bands[band]:
                repo = record.get("repo_url") or record["task_id"]
                if record not in selected and repo not in used_repos:
                    selected.append(record)
                    used_repos.add(repo)
                    progressed = True
                    break
            if len(selected) == quota:
                break
        if not progressed:
            break

    # If repository diversity exhausts a stratum, fill deterministically.
    for record in sorted(records, key=lambda item: (size_band(item), stable_key(item))):
        if len(selected) == quota:
            break
        if record not in selected:
            selected.append(record)
    return selected


def select(records: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = {name: [] for name in QUOTAS}
    for record in records:
        groups[failure_class(record)].append(record)
    selected = []
    for name, quota in QUOTAS.items():
        if len(groups[name]) < quota:
            raise ValueError(f"stratum {name} has {len(groups[name])}, needs {quota}")
        selected.extend(select_group(groups[name], quota))
    assert len(selected) == sum(QUOTAS.values())
    assert len({item["task_id"] for item in selected}) == len(selected)
    return selected


def fingerprint(root: Path) -> dict[str, str]:
    paths = [root / name for name in ("plan.json", "runner.py", "protocol.md")]
    paths += [
        path
        for path in (root / "runtime").rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    ]
    return {str(path.relative_to(root)): sha256(path) for path in sorted(paths)}


def prepare(source: Path, destination: Path) -> None:
    if destination.exists():
        raise FileExistsError(f"destination already exists: {destination}")
    candidates_doc = load(source / "reports/reproduction_candidates.json")
    records = candidates_doc["records"]
    selected = select(records)
    selected_ids = {record["task_id"] for record in selected}

    source_plan = load(source / "plan.json")
    tasks_by_id = {task["task_id"]: task for task in source_plan["tasks"]}
    tasks = []
    for record in selected:
        task = dict(tasks_by_id[record["task_id"]])
        task["eligibility"] = "queued"
        task["eligibility_reason"] = "independent_same_protocol_reproduction"
        tasks.append(task)

    destination.mkdir(parents=True)
    shutil.copy2(source / "runner.py", destination / "runner.py")
    shutil.copy2(source / "protocol.md", destination / "protocol.md")
    shutil.copytree(source / "runtime", destination / "runtime", ignore=shutil.ignore_patterns("__pycache__"))

    plan = {key: value for key, value in source_plan.items() if key != "tasks"}
    plan.update(
        schema="nangate45-independent-reproduction-1",
        total_designs=len(tasks),
        runnable=len(tasks),
        clock_review=0,
        minimum_free_disk_gib=80,
        tasks=tasks,
    )
    save(destination / "plan.json", plan)
    save(destination / "code_snapshot.json", fingerprint(destination))

    class_counts: dict[str, int] = {}
    size_counts: dict[str, int] = {}
    for record in selected:
        class_counts[failure_class(record)] = class_counts.get(failure_class(record), 0) + 1
        size_counts[size_band(record)] = size_counts.get(size_band(record), 0) + 1
    manifest = {
        "schema": "nangate45-reproduction-cohort-v1",
        "purpose": "Confirm same-protocol failure stability; no repair or LLM is permitted.",
        "selection_seed": SEED,
        "selection_policy": {
            "quotas": QUOTAS,
            "size_bands": ["<=500", "501-2000", ">2000 mapped cells"],
            "diversity": "source-repository diversity first, deterministic fill second",
            "uses_repair_outcomes": False,
        },
        "source_campaign": str(source),
        "source_plan_sha256": sha256(source / "plan.json"),
        "candidate_manifest_sha256": sha256(source / "reports/reproduction_candidates.json"),
        "selected_count": len(selected),
        "selected_task_ids": sorted(selected_ids),
        "class_counts": class_counts,
        "size_band_counts": size_counts,
        "records": [
            dict(record, reproduction_class=failure_class(record), size_band=size_band(record))
            for record in selected
        ],
    }
    save(destination / "cohort_manifest.json", manifest)
    (destination / "README.md").write_text(
        "# Nangate45 Independent Reproduction Cohort\n\n"
        "This campaign reruns a deterministic, stratified subset of first-run physical failures. "
        "It uses the same Nangate45, 10 ns, 7200 s, four-core protocol and performs no repair. "
        "A task is not admitted as a Repair Challenge until its normalized failure signature "
        "is independently reproduced.\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.source.resolve(), args.destination.resolve())


if __name__ == "__main__":
    main()
