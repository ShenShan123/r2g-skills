#!/usr/bin/env python3
"""Build and validate the frozen Experiment 3 data split and lifecycle evidence.

The split builder deliberately consumes only pre-treatment baseline covariates.  Repair
actions, Recipe status, and repair outcomes are never used to choose a partition.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import itertools
import json
import math
from pathlib import Path
import statistics
from typing import Any, Iterable


SPLIT_TARGETS = {
    "a_propose": {"DRC": 3, "SETUP_TIMING": 3},
    "a_validation": {"DRC": 3, "SETUP_TIMING": 3},
    "b_heldout": {"DRC": 7, "SETUP_TIMING": 6},
}
SPLIT_ORDER = tuple(SPLIT_TARGETS)
BALANCE_FIELDS = ("design_domain", "size_band", "severity_band")
FORBIDDEN_SPLIT_FIELDS = {
    "strategy",
    "evidence_level",
    "lifecycle_status",
    "blind_action",
    "blind_executable",
    "config_edits",
    "sdc_edits",
    "heldout_learning_votes",
    "heldout_promotion_votes",
    "repair_success",
    "strict_clean_after_repair",
}


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


def failure_family(signature: str) -> str:
    if signature.startswith("DRC:"):
        return "DRC"
    if signature == "SETUP_TIMING":
        return "SETUP_TIMING"
    raise ValueError(f"unsupported Experiment 3 failure signature: {signature}")


def severity_band(family: str, signature: str, drc: int, wns: float) -> str:
    if family == "DRC":
        if "+" in signature or "ROUTE_VIOLATIONS" in signature:
            return "mixed"
        if drc <= 10:
            return "low"
        if drc <= 100:
            return "medium"
        return "high"
    magnitude = abs(wns)
    if magnitude <= 1.0:
        return "mild"
    if magnitude <= 5.0:
        return "moderate"
    return "severe"


def _coverage_tasks(path: Path) -> list[dict[str, str]]:
    """Extract only identity and baseline labels from the pre-existing 25-task roster."""
    payload = read_json(path)
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        raise ValueError("coverage matrix has no tasks list")
    allowed = ("task_id", "failure_signature", "check", "design_class", "platform")
    extracted = [{key: str(task.get(key) or "") for key in allowed} for task in tasks]
    if len(extracted) != 25 or len({row["task_id"] for row in extracted}) != 25:
        raise ValueError("Experiment 3 requires exactly 25 unique frozen Repair Challenges")
    return extracted


def build_covariates(
    coverage_matrix: Path,
    revalidation_summary: Path,
    projects_root: Path,
) -> list[dict[str, Any]]:
    summary = read_json(revalidation_summary)
    summary_rows = {row["task_id"]: row for row in summary.get("rows", [])}
    covariates: list[dict[str, Any]] = []
    for task in _coverage_tasks(coverage_matrix):
        task_id = task["task_id"]
        baseline = projects_root / task_id / "baseline"
        metadata_path = baseline / "metadata.json"
        result_path = baseline / "repair_family_probe_result.json"
        if task_id not in summary_rows or not metadata_path.is_file() or not result_path.is_file():
            raise ValueError(f"missing frozen baseline evidence for {task_id}")
        summary_row = summary_rows[task_id]
        result = read_json(result_path)
        metadata = read_json(metadata_path)
        if summary_row.get("outcome") != "stable_nonclean_exact":
            raise ValueError(f"{task_id} is not a stable exact Repair Challenge")
        if result.get("strict_clean") or result.get("publication_strict_clean"):
            raise ValueError(f"{task_id} unexpectedly became strict clean")
        signature = task["failure_signature"]
        family = failure_family(signature)
        mapped_cells = int(result["mapped_cells"])
        metrics = result.get("metrics") or {}
        drc = int(metrics.get("drc_violations") or 0)
        wns = float(metrics.get("setup_wns_ns") or 0.0)
        design_class = task["design_class"]
        design_domain, _, size_band = design_class.partition("/")
        repo = normalized_repo(metadata["source_repo_url"])
        covariates.append(
            {
                "task_id": task_id,
                "failure_family": family,
                "failure_signature": signature,
                "platform": task["platform"],
                "design_class": design_class,
                "design_domain": design_domain,
                "size_band": size_band or "unknown",
                "mapped_cells": mapped_cells,
                "setup_wns_ns": wns,
                "drc_violations": drc,
                "severity_band": severity_band(family, signature, drc, wns),
                "baseline_elapsed_seconds": float(summary_row["revalidation_elapsed_seconds"]),
                "source_repo_url": metadata["source_repo_url"],
                "source_commit": metadata["source_commit"],
                "source_digest": metadata["source_digest"],
                "source_group": repo,
                "baseline_result_sha256": sha256_file(result_path),
                "baseline_metadata_sha256": sha256_file(metadata_path),
            }
        )
    counts = Counter(row["failure_family"] for row in covariates)
    if counts != Counter({"DRC": 13, "SETUP_TIMING": 12}):
        raise ValueError(f"unexpected failure-family distribution: {dict(counts)}")
    return sorted(covariates, key=lambda row: row["task_id"])


def _group_rows(rows: Iterable[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["source_group"]].append(row)
    return [sorted(group, key=lambda row: row["task_id"]) for _, group in sorted(grouped.items())]


def _partitions_for_family(
    rows: list[dict[str, Any]], propose_n: int, validation_n: int
) -> Iterable[dict[str, list[dict[str, Any]]]]:
    groups = _group_rows(rows)
    indexes = range(len(groups))
    for propose_idx in itertools.chain.from_iterable(
        itertools.combinations(indexes, count) for count in range(1, len(groups) + 1)
    ):
        propose = [row for idx in propose_idx for row in groups[idx]]
        if len(propose) != propose_n or len(propose_idx) != propose_n:
            continue
        remaining = [idx for idx in indexes if idx not in propose_idx]
        for validation_idx in itertools.chain.from_iterable(
            itertools.combinations(remaining, count) for count in range(1, len(remaining) + 1)
        ):
            validation = [row for idx in validation_idx for row in groups[idx]]
            if len(validation) != validation_n or len(validation_idx) != validation_n:
                continue
            heldout_idx = [idx for idx in remaining if idx not in validation_idx]
            yield {
                "a_propose": propose,
                "a_validation": validation,
                "b_heldout": [row for idx in heldout_idx for row in groups[idx]],
            }


def _numeric_value(row: dict[str, Any], field: str) -> float:
    if field == "log_cells":
        return math.log1p(row["mapped_cells"])
    if field == "log_runtime":
        return math.log1p(row["baseline_elapsed_seconds"])
    if field == "severity":
        return float(row["drc_violations"] if row["failure_family"] == "DRC" else abs(row["setup_wns_ns"]))
    raise KeyError(field)


def balance_score(partition: dict[str, list[dict[str, Any]]]) -> float:
    all_rows = [row for split in SPLIT_ORDER for row in partition[split]]
    total = len(all_rows)
    score = 0.0
    for field in BALANCE_FIELDS:
        global_counts = Counter(row[field] for row in all_rows)
        for split in SPLIT_ORDER:
            split_rows = partition[split]
            local = Counter(row[field] for row in split_rows)
            for value, count in global_counts.items():
                expected = len(split_rows) * count / total
                score += abs(local[value] - expected) / max(1.0, expected)
    for field in ("log_cells", "log_runtime", "severity"):
        values = [_numeric_value(row, field) for row in all_rows]
        spread = statistics.pstdev(values) or 1.0
        global_mean = statistics.fmean(values)
        for split in SPLIT_ORDER:
            local_mean = statistics.fmean(_numeric_value(row, field) for row in partition[split])
            score += 0.35 * abs(local_mean - global_mean) / spread
    return round(score, 12)


def choose_partition(rows: list[dict[str, Any]], family: str, seed: str) -> dict[str, list[dict[str, Any]]]:
    family_rows = [row for row in rows if row["failure_family"] == family]
    candidates = _partitions_for_family(
        family_rows,
        SPLIT_TARGETS["a_propose"][family],
        SPLIT_TARGETS["a_validation"][family],
    )
    best: tuple[float, str, dict[str, list[dict[str, Any]]]] | None = None
    for partition in candidates:
        if len(partition["b_heldout"]) != SPLIT_TARGETS["b_heldout"][family]:
            continue
        identity = "|".join(
            f"{split}:{','.join(sorted(row['task_id'] for row in partition[split]))}"
            for split in SPLIT_ORDER
        )
        tie_break = hashlib.sha256(f"{seed}|{family}|{identity}".encode()).hexdigest()
        item = (balance_score(partition), tie_break, partition)
        if best is None or item[:2] < best[:2]:
            best = item
    if best is None:
        raise ValueError(f"no group-safe split satisfies targets for {family}")
    return best[2]


def build_split(covariates: list[dict[str, Any]], seed: str) -> dict[str, Any]:
    combined = {split: [] for split in SPLIT_ORDER}
    family_scores: dict[str, float] = {}
    for family in ("DRC", "SETUP_TIMING"):
        partition = choose_partition(covariates, family, seed)
        family_scores[family] = balance_score(partition)
        for split in SPLIT_ORDER:
            combined[split].extend(partition[split])
    assignment: dict[str, str] = {}
    for split, rows in combined.items():
        for row in rows:
            if row["task_id"] in assignment:
                raise ValueError(f"duplicate split assignment for {row['task_id']}")
            assignment[row["task_id"]] = split
    source_splits: dict[str, set[str]] = defaultdict(set)
    for row in covariates:
        source_splits[row["source_group"]].add(assignment[row["task_id"]])
    leaked = {group: sorted(splits) for group, splits in source_splits.items() if len(splits) > 1}
    if leaked:
        raise ValueError(f"source groups cross split boundaries: {leaked}")
    return {
        "schema_version": "experiment3-split-1.0",
        "generated_at": utc_now(),
        "seed": seed,
        "selection_policy": "baseline_only_group_aware_minimum_balance_error",
        "targets": SPLIT_TARGETS,
        "family_balance_scores": family_scores,
        "forbidden_outcome_fields": sorted(FORBIDDEN_SPLIT_FIELDS),
        "assignments": [
            {
                "task_id": row["task_id"],
                "split": assignment[row["task_id"]],
                "failure_family": row["failure_family"],
                "source_group": row["source_group"],
            }
            for row in covariates
        ],
    }


def judge_promotion(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply the frozen hybrid-M3 rule to one immutable candidate payload."""
    candidate_hashes = {str(row.get("candidate_hash") or "") for row in evidence}
    if "" in candidate_hashes or len(candidate_hashes) != 1:
        raise ValueError("promotion evidence must bind one non-empty candidate hash")
    family_votes: dict[str, int] = defaultdict(int)
    family_roles: dict[str, set[str]] = defaultdict(set)
    regression = False
    for row in evidence:
        if not row.get("provenance_complete"):
            continue
        family_id = str(row.get("rtl_family_id") or "")
        if not family_id:
            continue
        verdict = row.get("verdict")
        if verdict == "win":
            family_votes[family_id] += 1
        elif verdict == "loss":
            family_votes[family_id] -= 1
        family_roles[family_id].add(str(row.get("evidence_role") or ""))
        regression = regression or bool(row.get("clean_sentinel_regression"))
    wins = sum(value > 0 for value in family_votes.values())
    losses = sum(value < 0 for value in family_votes.values())
    validation_wins = {
        family_id
        for family_id, value in family_votes.items()
        if value > 0 and "a_validation" in family_roles[family_id]
    }
    operational = wins >= 2 and wins > losses and bool(validation_wins) and not regression
    strict = operational and len(validation_wins) >= 2
    return {
        "candidate_hash": next(iter(candidate_hashes)),
        "independent_wins": wins,
        "independent_losses": losses,
        "validation_wins": len(validation_wins),
        "clean_sentinel_regression": regression,
        "operational_promoted": operational,
        "strict_validation_supported": strict,
        "status": "promoted" if operational else ("shadow" if losses > wins or regression else "candidate"),
    }


def command_build_split(args: argparse.Namespace) -> None:
    covariates = build_covariates(args.coverage_matrix, args.revalidation_summary, args.projects_root)
    split = build_split(covariates, args.seed)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    covariates_path = args.output_dir / "baseline_covariates.json"
    split_path = args.output_dir / "split_manifest.json"
    write_json(
        covariates_path,
        {
            "schema_version": "experiment3-baseline-covariates-1.0",
            "generated_at": utc_now(),
            "source_fields_used": [
                "task identity",
                "baseline failure signature",
                "design class",
                "mapped cells",
                "baseline WNS/DRC",
                "baseline runtime",
                "source repository/commit/digest",
            ],
            "repair_outcomes_used_for_partitioning": False,
            "source_hashes": {
                "coverage_matrix": sha256_file(args.coverage_matrix),
                "revalidation_summary": sha256_file(args.revalidation_summary),
            },
            "tasks": covariates,
        },
    )
    split["baseline_covariates_sha256"] = sha256_file(covariates_path)
    write_json(split_path, split)
    print(split_path)


def command_build_sentinels(args: argparse.Namespace) -> None:
    records: list[dict[str, Any]] = []
    source_groups: set[str] = set()
    for task_id in args.task_id:
        baseline = args.projects_root / task_id / "baseline"
        result_path = baseline / "repair_family_probe_result.json"
        metadata_path = baseline / "metadata.json"
        input_path = baseline / "repair_family_probe_input.json"
        if not all(path.is_file() for path in (result_path, metadata_path, input_path)):
            raise ValueError(f"missing sentinel evidence for {task_id}")
        result = read_json(result_path)
        metadata = read_json(metadata_path)
        disqualifying = any(
            result.get(field) is True
            for field in (
                "environment_failure",
                "execution_interrupted",
                "input_qualification_failure",
                "constraint_coverage_incomplete",
                "timing_evaluation_incomplete",
                "unclassified_execution_failure",
            )
        )
        if result.get("strict_clean") is not True or disqualifying:
            raise ValueError(f"sentinel baseline is not complete strict clean: {task_id}")
        source_group = normalized_repo(metadata["source_repo_url"])
        if source_group in source_groups:
            raise ValueError(f"sentinels must use independent source groups: {source_group}")
        source_groups.add(source_group)
        records.append(
            {
                "task_id": task_id,
                "role": "clean_sentinel",
                "source_group": source_group,
                "source_repo_url": metadata["source_repo_url"],
                "source_commit": metadata["source_commit"],
                "source_digest": metadata["source_digest"],
                "mapped_cells": int(result["mapped_cells"]),
                "protected_task_digest": result["protected_task_digest"],
                "baseline_project": str(baseline.resolve()),
                "baseline_result_sha256": sha256_file(result_path),
                "baseline_metadata_sha256": sha256_file(metadata_path),
                "baseline_input_sha256": sha256_file(input_path),
            }
        )
    if len(records) != 4:
        raise ValueError("Experiment 3 requires exactly four clean sentinels")
    write_json(
        args.output,
        {
            "schema_version": "experiment3-clean-sentinels-1.0",
            "generated_at": utc_now(),
            "selection_policy": "pre_treatment_strict_clean_distinct_repo_scale_coverage",
            "formal_start_gate": "fresh_baseline_replay_must_remain_strict_clean",
            "records": records,
        },
    )
    print(args.output)


def _model_visible_baseline(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": row["task_id"],
        "failure_domain": "drc_edge_pin" if row["failure_family"] == "DRC" else "setup_timing",
        "failure_signature": row["failure_signature"],
        "design_domain": row["design_domain"],
        "size_band": row["size_band"],
        "mapped_cells": row["mapped_cells"],
        "setup_wns_ns": row["setup_wns_ns"],
        "drc_violations": row["drc_violations"],
        "severity_band": row["severity_band"],
        "baseline_elapsed_seconds": row["baseline_elapsed_seconds"],
    }


def command_build_contexts(args: argparse.Namespace) -> None:
    covariates = {row["task_id"]: row for row in read_json(args.baseline_covariates)["tasks"]}
    assignments = read_json(args.split_manifest)["assignments"]
    by_split: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for assignment in assignments:
        by_split[assignment["split"]].append(covariates[assignment["task_id"]])
    a_propose = [_model_visible_baseline(row) for row in by_split["a_propose"]]
    for domain in ("drc_edge_pin", "setup_timing"):
        tasks = sorted(
            (row for row in a_propose if row["failure_domain"] == domain),
            key=lambda row: row["task_id"],
        )
        write_json(
            args.output_dir / "a_propose" / f"{domain}.json",
            {
                "schema_version": "experiment3-model-context-1.0",
                "split": "a_propose",
                "failure_domain": domain,
                "repair_outcomes_included": False,
                "tasks": tasks,
            },
        )
    for row in by_split["b_heldout"]:
        visible = _model_visible_baseline(row)
        write_json(
            args.output_dir / "b_heldout" / f"{row['task_id']}.json",
            {
                "schema_version": "experiment3-model-context-1.0",
                "split": "b_heldout",
                "task_isolation": "single_task_only",
                "repair_outcomes_included": False,
                "tasks": [visible],
            },
        )
    write_json(
        args.output_dir / "context_manifest.json",
        {
            "schema_version": "experiment3-model-context-manifest-1.0",
            "generated_at": utc_now(),
            "a_validation_context_generated": False,
            "a_propose_task_count": len(by_split["a_propose"]),
            "b_heldout_task_count": len(by_split["b_heldout"]),
            "policy": "A models see A-propose only; Pure LLM sees one B task per invocation",
        },
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subparsers = result.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build-split")
    build.add_argument("--coverage-matrix", type=Path, required=True)
    build.add_argument("--revalidation-summary", type=Path, required=True)
    build.add_argument("--projects-root", type=Path, required=True)
    build.add_argument("--output-dir", type=Path, required=True)
    build.add_argument("--seed", required=True)
    build.set_defaults(func=command_build_split)
    sentinels = subparsers.add_parser("build-sentinels")
    sentinels.add_argument("--projects-root", type=Path, required=True)
    sentinels.add_argument("--task-id", action="append", required=True)
    sentinels.add_argument("--output", type=Path, required=True)
    sentinels.set_defaults(func=command_build_sentinels)
    contexts = subparsers.add_parser("build-contexts")
    contexts.add_argument("--baseline-covariates", type=Path, required=True)
    contexts.add_argument("--split-manifest", type=Path, required=True)
    contexts.add_argument("--output-dir", type=Path, required=True)
    contexts.set_defaults(func=command_build_contexts)
    return result


def main() -> int:
    args = parser().parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
