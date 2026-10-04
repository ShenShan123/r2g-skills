#!/usr/bin/env python3
"""Run provenance-bound Experiment 3 A/B repair trials and ablation arms."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
from pathlib import Path
from queue import Queue
import re
import shutil
import subprocess
import sys
from typing import Any, Iterable


REPO = Path(__file__).resolve().parents[1]
PROBE = REPO / "tools/run_repair_family_probe.py"
POLICY = REPO / "docs/experiments/signoff/sky130hd_100mhz_fixed_task_repair_action_policy.json"
DEFAULT_CHALLENGE_ROOT = Path("/home/yangao/r2g_exp2_baseline_revalidation_20260904/projects")
DEFAULT_SENTINEL_ROOT = Path("/home/yangao/r2g_exp2_default_baseline_cpu_fixed_2026_08_30/projects")
INFRASTRUCTURE_FLAGS = (
    "environment_failure",
    "execution_interrupted",
    "input_qualification_failure",
    "constraint_coverage_incomplete",
    "timing_evaluation_incomplete",
    "unclassified_execution_failure",
    "runtime_budget_failure",
    "scale_ineligible",
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


def portable_file_record(path: Path, campaign_root: Path) -> dict[str, Any]:
    resolved = path.resolve()
    for scope, root in (("campaign", campaign_root.resolve()), ("repo", REPO.resolve())):
        try:
            relative = resolved.relative_to(root)
        except ValueError:
            continue
        return {
            "scope": scope,
            "relative_path": str(relative),
            "path_at_freeze": str(resolved),
            "sha256": sha256_file(resolved),
        }
    return {
        "scope": "absolute",
        "path_at_freeze": str(resolved),
        "sha256": sha256_file(resolved),
    }


def _source_relative(path: str) -> str:
    value = Path(path)
    if not value.parts or value.parts[0] != "rtl":
        raise ValueError(f"frozen project input is not below rtl/: {path}")
    return str(Path(*value.parts[1:]))


def _source_snapshot(baseline: Path, frozen: dict[str, Any]) -> Path:
    """Resolve the original source or the portable RTL closure bundled with a baseline."""
    relative_inputs = [
        _source_relative(path)
        for path in [
            *(frozen.get("compilation_units") or []),
            *(frozen.get("dependency_inputs") or []),
        ]
    ]
    original = Path(frozen["source_snapshot"])
    if original.is_dir() and all((original / path).is_file() for path in relative_inputs):
        return original.resolve()
    portable = baseline / "rtl"
    if portable.is_dir() and all((portable / path).is_file() for path in relative_inputs):
        return portable.resolve()
    raise FileNotFoundError(
        f"neither original nor portable source closure is complete for {baseline}"
    )


def _trial_slug(candidate: dict[str, Any] | None) -> str:
    if candidate is None:
        return "m0_baseline"
    return f"{candidate['candidate_id']}_v{candidate['candidate_version']}_{candidate['candidate_hash'][:12]}"


def materialize_command(
    baseline: Path,
    project: Path,
    candidate: dict[str, Any] | None,
    variant: str,
) -> list[str]:
    frozen = read_json(baseline / "repair_family_probe_input.json")
    protected = frozen["protected_task"]
    edits = {str(key): str(value) for key, value in (frozen.get("config_edits") or {}).items()}
    if candidate:
        edits.update({str(key): str(value) for key, value in candidate["config_edits"].items()})
    command = [
        sys.executable,
        str(PROBE),
        "materialize",
        "--source",
        str(_source_snapshot(baseline, frozen)),
        "--source-repo-url",
        str(frozen["source_repo_url"]),
        "--source-commit",
        str(frozen["source_commit"]),
        "--project",
        str(project.resolve()),
        "--family",
        str(frozen["family_id"]),
        "--task-id",
        str(frozen["task_id"]),
        "--variant",
        variant,
        "--platform",
        str(protected["platform"]),
        "--top-module",
        str(protected["top_module"]),
        "--clock-port",
        str(protected["clock_port"]),
        "--frequency-mhz",
        str(protected["target_frequency_mhz"]),
    ]
    for path in frozen["compilation_units"]:
        command.extend(["--rtl-file", _source_relative(path)])
    for path in frozen.get("dependency_inputs") or []:
        command.extend(["--dependency-file", _source_relative(path)])
    for key, value in sorted(edits.items()):
        command.extend(["--set", f"{key}={value}"])
    for key in sorted(set(frozen.get("config_unsets") or [])):
        command.extend(["--unset", key])
    return command


def bind_frozen_baseline_constraints(baseline: Path, project: Path) -> None:
    """Restore the exact baseline SDC and protected-task identity after materialization."""
    baseline_manifest = read_json(baseline / "repair_family_probe_input.json")
    trial_path = project / "repair_family_probe_input.json"
    trial_manifest = read_json(trial_path)
    baseline_protected = baseline_manifest["protected_task"]
    trial_protected = trial_manifest["protected_task"]
    identity_fields = (
        "source_digest",
        "compilation_units",
        "dependency_inputs",
        "source_repo_url",
        "source_commit",
        "top_module",
        "platform",
        "clock_port",
        "target_frequency_mhz",
        "signoff_mode",
        "check_set",
    )
    unordered_path_fields = {"compilation_units", "dependency_inputs"}

    def identity_matches(key: str) -> bool:
        baseline_value = baseline_protected.get(key)
        trial_value = trial_protected.get(key)
        if key in unordered_path_fields:
            return sorted(baseline_value or []) == sorted(trial_value or [])
        return trial_value == baseline_value

    mismatches = [key for key in identity_fields if not identity_matches(key)]
    if mismatches:
        raise ValueError(f"materialized protected task differs from baseline: {mismatches}")
    source_sdc = baseline / "constraints/constraint.sdc"
    target_sdc = project / "constraints/constraint.sdc"
    if not source_sdc.is_file():
        raise FileNotFoundError(source_sdc)
    shutil.copy2(source_sdc, target_sdc)
    trial_manifest["protected_task"] = baseline_protected
    trial_manifest["protected_task_digest"] = baseline_manifest["protected_task_digest"]
    trial_manifest["config_sha256"] = sha256_file(project / "constraints/config.mk")
    write_json(trial_path, trial_manifest)


def metrics_delta(baseline: dict[str, Any], action: dict[str, Any]) -> dict[str, float | None]:
    before = baseline.get("metrics") or {}
    after = action.get("metrics") or {}

    def delta(key: str) -> float | None:
        if before.get(key) is None or after.get(key) is None:
            return None
        return float(after[key]) - float(before[key])

    return {
        "wns_delta_ns": delta("setup_wns_ns"),
        "drc_delta": delta("drc_violations"),
        "route_delta": delta("route_violations"),
        "lvs_delta": delta("lvs_mismatches"),
    }


def trial_evidence(
    baseline: dict[str, Any],
    action: dict[str, Any],
    candidate: dict[str, Any] | None,
    *,
    split: str,
    evidence_role: str,
    project: Path,
    arm_id: str | None = None,
) -> dict[str, Any]:
    infrastructure_complete = not any(action.get(flag) is True for flag in INFRASTRUCTURE_FLAGS)
    protected_match = baseline.get("protected_task_digest") == action.get("protected_task_digest")
    infrastructure_complete = infrastructure_complete and protected_match
    baseline_clean = baseline.get("strict_clean") is True
    action_clean = action.get("strict_clean") is True
    if not infrastructure_complete:
        verdict = "inconclusive"
    elif baseline_clean:
        verdict = "sentinel_pass" if action_clean else "loss"
    else:
        verdict = "win" if action_clean else "loss"
    deltas = metrics_delta(baseline, action)
    baseline_signatures = set(baseline.get("normalized_failure_signature") or [])
    action_signatures = set(action.get("normalized_failure_signature") or [])
    hard_regression = bool(
        infrastructure_complete
        and not action_clean
        and action_signatures - baseline_signatures
    )
    elapsed = sum(float(row.get("elapsed_seconds") or 0.0) for row in action.get("commands") or [])
    return {
        "schema_version": "experiment3-trial-evidence-1.0",
        "created_at": now(),
        "task_id": baseline.get("task_id"),
        "rtl_family_id": baseline.get("protected_task_digest"),
        "split": split,
        "evidence_role": evidence_role,
        "arm_id": arm_id,
        "failure_domain": candidate.get("failure_domain") if candidate else "m0",
        "candidate_id": candidate.get("candidate_id") if candidate else None,
        "candidate_hash": candidate.get("candidate_hash") if candidate else None,
        "project": str(project),
        "baseline_result_sha256": None,
        "action_result_sha256": sha256_file(project / "repair_family_probe_result.json"),
        "protected_task_digest_match": protected_match,
        "provenance_complete": infrastructure_complete,
        "infrastructure_complete": infrastructure_complete,
        "verdict": verdict,
        "baseline_strict_clean": baseline_clean,
        "strict_clean_after_repair": action_clean,
        "clean_sentinel_regression": baseline_clean and infrastructure_complete and not action_clean,
        "hard_regression": hard_regression,
        "baseline_failure_signature": sorted(baseline_signatures),
        "action_failure_signature": sorted(action_signatures),
        "baseline_metrics": baseline.get("metrics") or {},
        "action_metrics": action.get("metrics") or {},
        **deltas,
        "elapsed_seconds": round(elapsed, 3),
    }


def _copy_if_file(source: Path, target: Path) -> None:
    if source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def compact_project(project: Path) -> dict[str, Any]:
    """Keep reproducibility inputs and compact evidence, discard regenerable EDA bulk."""
    evidence_root = project / "evidence"
    run_dirs = sorted((project / "backend").glob("RUN_*"))
    if run_dirs:
        latest = run_dirs[-1]
        for relative in ("flow.log", "run-meta.json", "stage_artifact_manifest.jsonl"):
            _copy_if_file(latest / relative, evidence_root / relative)
    for source in sorted((project.parent / "logs" / project.name).glob("*.log")):
        _copy_if_file(source, evidence_root / "logs" / source.name)
    removed_bytes = 0
    for name in ("backend", "drc", "lvs", "rcx"):
        path = project / name
        if not path.exists():
            continue
        removed_bytes += sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
        shutil.rmtree(path)
    external_logs = project.parent / "logs" / project.name
    if external_logs.exists():
        removed_bytes += sum(item.stat().st_size for item in external_logs.rglob("*") if item.is_file())
        shutil.rmtree(external_logs)
    manifest = {
        "schema_version": "experiment3-trial-compaction-1.0",
        "created_at": now(),
        "removed_regenerable_bytes": removed_bytes,
        "retained": [
            "frozen RTL closure",
            "constraints and configuration",
            "probe input/result/metadata",
            "JSON reports",
            "flow and strict-signoff logs",
        ],
    }
    write_json(project / "compaction_manifest.json", manifest)
    return manifest


def candidate_applies(candidate: dict[str, Any], baseline: dict[str, Any]) -> bool:
    applicability = candidate["applicability"]
    signatures = set(baseline.get("normalized_failure_signature") or [])
    required = set(applicability.get("required_failure_signatures") or [])
    if not any(
        fnmatch.fnmatchcase(signature, pattern)
        for signature in signatures
        for pattern in required
    ):
        return False
    metrics = baseline.get("metrics") or {}
    if applicability.get("requires_negative_setup_wns") and not float(metrics.get("setup_wns_ns") or 0) < 0:
        return False
    if applicability.get("requires_edge_concentration") and not any(
        signature.startswith("DRC:m3.") for signature in signatures
    ):
        return False
    return True


def run_trial(
    baseline: Path,
    project: Path,
    candidate: dict[str, Any] | None,
    *,
    split: str,
    evidence_role: str,
    cores: int,
    cpu_set: str | None,
    timeout_seconds: int,
    compact: bool,
    arm_id: str | None = None,
) -> dict[str, Any]:
    evidence_path = project / "trial_evidence.json"
    if evidence_path.is_file():
        existing = read_json(evidence_path)
        expected_hash = candidate.get("candidate_hash") if candidate else None
        if existing.get("candidate_hash") == expected_hash and existing.get("provenance_complete") is True:
            return existing
    if project.exists():
        archive = project.with_name(f"{project.name}.incomplete.{datetime.now().strftime('%Y%m%dT%H%M%S')}")
        project.rename(archive)
    baseline_result_path = baseline / "repair_family_probe_result.json"
    baseline_result = read_json(baseline_result_path)
    if any(baseline_result.get(flag) is True for flag in INFRASTRUCTURE_FLAGS):
        raise ValueError(f"baseline is not formal-complete: {baseline}")
    variant = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"exp3_{split}_{_trial_slug(candidate)}")
    materialize = subprocess.run(
        materialize_command(baseline, project, candidate, variant),
        text=True,
        capture_output=True,
    )
    if materialize.returncode != 0:
        raise RuntimeError(f"materialize failed: {materialize.stderr[-2000:]}")
    bind_frozen_baseline_constraints(baseline, project)
    execute_command = [
            sys.executable,
            str(PROBE),
            "execute",
            "--project",
            str(project),
            "--cores",
            str(cores),
            "--timeout-seconds",
            str(timeout_seconds),
            "--min-mapped-cells",
            "100",
            "--max-mapped-cells",
            "100000",
        ]
    if cpu_set:
        execute_command.extend(["--cpu-set", cpu_set])
    execute = subprocess.run(
        execute_command,
        text=True,
        capture_output=True,
    )
    if not (project / "repair_family_probe_result.json").is_file():
        raise RuntimeError(f"probe produced no result: {execute.stderr[-2000:]}")
    action_result = read_json(project / "repair_family_probe_result.json")
    evidence = trial_evidence(
        baseline_result,
        action_result,
        candidate,
        split=split,
        evidence_role=evidence_role,
        project=project,
        arm_id=arm_id,
    )
    evidence["baseline_result_sha256"] = sha256_file(baseline_result_path)
    evidence["probe_returncode"] = execute.returncode
    write_json(evidence_path, evidence)
    if compact:
        evidence["compaction"] = compact_project(project)
        write_json(evidence_path, evidence)
    return evidence


def _assignments(split_manifest: dict[str, Any], split: str) -> list[str]:
    return sorted(row["task_id"] for row in split_manifest["assignments"] if row["split"] == split)


def _restrict_task_ids(task_ids: list[str], requested_ids: list[str] | None, split: str) -> list[str]:
    if not requested_ids:
        return task_ids
    requested = set(requested_ids)
    unknown = requested - set(task_ids)
    if unknown:
        raise ValueError(f"requested tasks are not in split {split}: {sorted(unknown)}")
    return [task_id for task_id in task_ids if task_id in requested]


def _candidate_records(bank: dict[str, Any], mode: str) -> list[dict[str, Any]]:
    if mode in {"a_explore", "pure_llm"}:
        if bank.get("schema_version") != "experiment3-proposal-round-plan-1.0":
            raise ValueError(f"{mode} requires a frozen proposal round plan")
        return [
            {
                "candidate_hash": item["candidate"]["candidate_hash"],
                "candidate": item["candidate"],
                "proposal_sources": item.get("proposal_sources") or [],
            }
            for item in bank.get("selected") or []
        ]
    by_hash = {
        row["candidate_hash"]: row
        for row in bank.get("candidate_records") or []
        if row.get("admitted") is True
    }
    if mode == "m1":
        hashes = bank.get("m1_order") or []
    elif mode == "m2":
        hashes = bank.get("m2_order") or []
    elif mode == "m3":
        promoted = bank.get("promotion_records") or []
        promoted_hashes = [
            row["candidate_hash"]
            for row in promoted
            if (row.get("promotion") or {}).get("operational_promoted") is True
        ]
        hashes = bank.get("m3_order") or promoted_hashes
        if set(hashes) != set(promoted_hashes):
            raise ValueError("M3 order must contain exactly the promoted recipes")
    else:
        hashes = list(by_hash)
    return [by_hash[item] for item in hashes if item in by_hash]


def _task_baseline(
    task_id: str,
    sentinel_ids: set[str],
    challenge_root: Path,
    sentinel_root: Path,
) -> Path:
    root = sentinel_root if task_id in sentinel_ids else challenge_root
    return root / task_id / "baseline"


def _applicable_candidates(
    task_id: str,
    sentinel_ids: set[str],
    candidates: list[dict[str, Any]],
    baseline_result: dict[str, Any],
    mode: str,
) -> list[dict[str, Any] | None]:
    if mode == "m0":
        return [None]
    if task_id in sentinel_ids:
        # Sentinels intentionally have no failure signature. Every candidate
        # must still be replayed on them so the no-regression gate is real.
        return [row["candidate"] for row in candidates]
    return [
        row["candidate"]
        for row in candidates
        if candidate_applies(row["candidate"], baseline_result)
    ]


def _matrix_run_signature(
    *,
    mode: str,
    split: str,
    arm_id: str,
    work: list[tuple[str, Path, list[dict[str, Any] | None]]],
    cores: int,
    timeout_seconds: int,
) -> str:
    identity = {
        "mode": mode,
        "split": split,
        "arm_id": arm_id,
        "cores": cores,
        "timeout_seconds": timeout_seconds,
        "work": [
            {
                "task_id": task_id,
                "candidate_hashes": [
                    candidate.get("candidate_hash") if candidate else None
                    for candidate in applicable
                ],
            }
            for task_id, _baseline, applicable in work
        ],
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _terminal_checkpoint_result(result: dict[str, Any]) -> bool:
    if result.get("status") == "no_applicable_candidate":
        return True
    attempts = result.get("attempts") or []
    return (
        result.get("status") == "completed"
        and bool(attempts)
        and all(attempt.get("provenance_complete") is True for attempt in attempts)
    )


def _copy_portable_baseline(source: Path, destination: Path) -> dict[str, Any]:
    required_files = ("repair_family_probe_input.json", "repair_family_probe_result.json", "metadata.json")
    for name in required_files:
        if not (source / name).is_file():
            raise FileNotFoundError(source / name)
    destination.mkdir(parents=True, exist_ok=True)
    for name in required_files:
        shutil.copy2(source / name, destination / name)
    for name in ("rtl", "constraints", "input", "reports", "evidence"):
        path = source / name
        if path.is_dir():
            shutil.copytree(path, destination / name, dirs_exist_ok=True)
    frozen = read_json(destination / "repair_family_probe_input.json")
    _source_snapshot(destination, frozen)
    return {
        "baseline": str(destination.resolve()),
        "protected_task_digest": frozen.get("protected_task_digest"),
        "input_sha256": sha256_file(destination / "repair_family_probe_input.json"),
        "result_sha256": sha256_file(destination / "repair_family_probe_result.json"),
    }


def bundle_inputs(args: argparse.Namespace) -> None:
    split_manifest = read_json(args.split_manifest)
    sentinel_manifest = read_json(args.sentinel_manifest)
    sentinel_ids = {row["task_id"] for row in sentinel_manifest["records"]}
    task_ids = sorted({row["task_id"] for row in split_manifest["assignments"]} | sentinel_ids)
    records = []
    for task_id in task_ids:
        is_sentinel = task_id in sentinel_ids
        source = _task_baseline(
            task_id,
            sentinel_ids,
            args.challenge_root,
            args.sentinel_root,
        )
        kind = "sentinels" if is_sentinel else "challenges"
        destination = args.campaign_root / "inputs" / kind / task_id / "baseline"
        record = _copy_portable_baseline(source, destination)
        record.update({"task_id": task_id, "kind": kind})
        records.append(record)
    manifest = {
        "schema_version": "experiment3-portable-input-bundle-1.0",
        "created_at": now(),
        "task_count": len(records),
        "records": records,
    }
    write_json(args.campaign_root / "inputs" / "bundle_manifest.json", manifest)
    print(args.campaign_root / "inputs" / "bundle_manifest.json")


def run_matrix(args: argparse.Namespace) -> None:
    split_manifest = read_json(args.split_manifest)
    sentinel_manifest = read_json(args.sentinel_manifest)
    sentinel_ids = {row["task_id"] for row in sentinel_manifest["records"]}
    if args.mode != "m0" and args.candidate_bank is None:
        raise ValueError("--candidate-bank is required for non-M0 modes")
    bank = read_json(args.candidate_bank) if args.candidate_bank else {}
    candidates = [] if args.mode == "m0" else _candidate_records(bank, args.mode)
    if args.sentinels_only:
        task_ids = sorted(sentinel_ids)
    else:
        task_ids = _assignments(split_manifest, args.split)
    task_ids = _restrict_task_ids(task_ids, args.task_ids, args.split)
    if args.include_sentinels and not args.sentinels_only:
        task_ids.extend(sorted(sentinel_ids))
    work: list[tuple[str, Path, list[dict[str, Any] | None]]] = []
    arm_id = args.arm_id or args.mode
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,63}", arm_id):
        raise ValueError(f"invalid arm id: {arm_id}")
    for task_id in task_ids:
        baseline = _task_baseline(
            task_id,
            sentinel_ids,
            args.challenge_root,
            args.sentinel_root,
        )
        baseline_result = read_json(baseline / "repair_family_probe_result.json")
        applicable = _applicable_candidates(
            task_id, sentinel_ids, candidates, baseline_result, args.mode
        )
        if args.mode in {"m1", "m2"}:
            applicable = applicable[:3]
        work.append((task_id, baseline, applicable))
    state_output = args.state_output or (
        args.campaign_root
        / "state"
        / f"{args.mode}_{'clean_sentinel' if args.sentinels_only else args.split}_execution.json"
    )
    run_signature = _matrix_run_signature(
        mode=args.mode,
        split=args.split,
        arm_id=arm_id,
        work=work,
        cores=args.cores,
        timeout_seconds=args.timeout_seconds,
    )
    completed_by_task: dict[str, dict[str, Any]] = {}
    if state_output.is_file():
        checkpoint = read_json(state_output)
        if checkpoint.get("run_signature") != run_signature:
            raise ValueError(
                f"checkpoint run signature mismatch for {state_output}; "
                "refusing to mix different task/candidate/resource plans"
            )
        completed_by_task = {
            result["task_id"]: result
            for result in checkpoint.get("results") or []
            if result.get("task_id") and _terminal_checkpoint_result(result)
        }
    results: list[dict[str, Any]] = list(completed_by_task.values())
    pending_work = [item for item in work if item[0] not in completed_by_task]

    cpu_sets = list(args.cpu_sets or [])
    if cpu_sets and len(cpu_sets) < args.workers:
        raise ValueError("provide at least one --cpu-set for each worker")
    cpu_slots: Queue[str | None] = Queue()
    for index in range(args.workers):
        cpu_slots.put(cpu_sets[index] if cpu_sets else None)

    def execute_task(item: tuple[str, Path, list[dict[str, Any] | None]]) -> dict[str, Any]:
        task_id, baseline, task_candidates = item
        cpu_set = cpu_slots.get()
        try:
            attempts: list[dict[str, Any]] = []
            for candidate in task_candidates:
                project = args.campaign_root / "trials" / arm_id / task_id / _trial_slug(candidate)
                evidence = run_trial(
                    baseline,
                    project,
                    candidate,
                    split=("clean_sentinel" if task_id in sentinel_ids else args.split),
                    evidence_role=args.evidence_role,
                    cores=args.cores,
                    cpu_set=cpu_set,
                    timeout_seconds=args.timeout_seconds,
                    compact=args.compact,
                    arm_id=arm_id,
                )
                attempts.append(evidence)
                if args.mode in {"m0", "m1", "m2", "m3"} and evidence.get("strict_clean_after_repair") is True:
                    break
        finally:
            cpu_slots.put(cpu_set)
        return {
            "task_id": task_id,
            "attempt_count": len(attempts),
            "strict_clean": any(row.get("strict_clean_after_repair") is True for row in attempts),
            "attempts": attempts,
            "status": (
                "no_applicable_candidate"
                if not task_candidates
                else "completed"
                if all(row.get("provenance_complete") is True for row in attempts)
                else "infrastructure_incomplete"
            ),
        }

    def checkpoint() -> None:
        write_json(
            state_output,
            {
                "schema_version": "experiment3-matrix-execution-1.1",
                "updated_at": now(),
                "mode": args.mode,
                "split": args.split,
                "arm_id": arm_id,
                "run_signature": run_signature,
                "planned_tasks": len(work),
                "planned_trial_upper_bound": sum(len(item[2]) for item in work),
                "completed_records": len(results),
                "resumed_terminal_records": len(completed_by_task),
                "results": sorted(results, key=lambda row: str(row.get("task_id"))),
            },
        )

    checkpoint()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(execute_task, item): item for item in pending_work}
        for future in as_completed(futures):
            item = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {"task_id": item[0], "status": "runner_error", "error": str(exc)}
            results.append(result)
            checkpoint()
            print(
                f"[{len(results)}/{len(work)}] {result.get('task_id')}: "
                f"{result.get('status')} clean={result.get('strict_clean')}",
                flush=True,
            )
    incomplete = [
        row.get("task_id")
        for row in results
        if row.get("status") in {"runner_error", "infrastructure_incomplete"}
    ]
    if incomplete:
        raise RuntimeError(
            "matrix contains retryable infrastructure-incomplete tasks: "
            + ", ".join(sorted(str(item) for item in incomplete))
        )


def build_manifest(args: argparse.Namespace) -> None:
    source_paths = {
        "protocol": args.protocol,
        "candidate_schema": args.candidate_schema,
        "action_policy": args.action_policy,
        "split_manifest": args.split_manifest,
        "baseline_covariates": args.baseline_covariates,
        "clean_sentinels": args.sentinel_manifest,
        "model_routes": args.model_routes,
        "resource_limits": args.resource_limits,
        "a_propose_prompt": args.a_propose_prompt,
        "pure_llm_prompt": args.pure_llm_prompt,
        "route_canary_summary": args.campaign_root / "canary/routes/route_canary_summary.json",
        "clean_sentinel_execution": args.campaign_root / "state/m0_clean_sentinel_execution.json",
        "portable_input_bundle": args.campaign_root / "inputs/bundle_manifest.json",
        "model_context_manifest": args.campaign_root / "model_contexts/context_manifest.json",
        "deployment_203": args.campaign_root / "deployment_203.json",
        "resolved_model_routes": args.campaign_root / "resolved_model_routes.json",
    }
    missing = [name for name, path in source_paths.items() if not path.is_file()]
    resource_payload = read_json(args.resource_limits)
    route_payload = (
        read_json(source_paths["route_canary_summary"])
        if source_paths["route_canary_summary"].is_file()
        else {}
    )
    sentinel_payload = (
        read_json(source_paths["clean_sentinel_execution"])
        if source_paths["clean_sentinel_execution"].is_file()
        else {}
    )
    sentinel_results = sentinel_payload.get("results") or []
    unresolved = []
    if resource_payload.get("status") != "frozen":
        unresolved.append("proposal_and_pure_llm_token_budgets")
    if route_payload.get("status") != "passed" or {
        row.get("model_key")
        for row in route_payload.get("results") or []
        if row.get("status") == "passed"
    } != {"gpt", "claude", "qwen"}:
        unresolved.append("fresh_route_canary")
    if not (
        sentinel_payload.get("planned_tasks") == 4
        and sentinel_payload.get("completed_records") == 4
        and len(sentinel_results) == 4
        and all(
            row.get("status") == "completed" and row.get("strict_clean") is True
            for row in sentinel_results
        )
    ):
        unresolved.append("clean_sentinel_replay")
    unresolved.extend(f"missing_artifact:{name}" for name in missing)
    if args.freeze and unresolved:
        raise ValueError(f"cannot freeze execution manifest: {unresolved}")
    runner_paths = {
        "ablation": Path(__file__),
        "model_proposals": REPO / "tools/run_experiment3_llm_proposals.py",
        "candidate_bank": REPO / "tools/experiment3_candidate_bank.py",
        "data_protocol": REPO / "tools/experiment3_protocol.py",
        "formal_start_audit": REPO / "tools/audit_experiment3_campaign.py",
        "campaign_orchestrator": REPO / "tools/run_experiment3_campaign.py",
    }
    repo_commit = subprocess.check_output(
        ["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True
    ).strip()
    manifest = {
        "schema_version": "experiment3-execution-manifest-1.0",
        "campaign_id": args.campaign_root.name,
        "created_at": now(),
        "status": "frozen" if args.freeze else "prepared_not_frozen",
        "fixed_task": {
            "platform": args.platform,
            "frequency_mhz": args.frequency_mhz,
            "mapped_cells_min": 100,
            "mapped_cells_max": 100000,
            "cores_per_trial": 4,
            "signoff": "strict",
            "fixed_clock_and_footprint": True,
        },
        "arms": ["M0", "M1", "M2", "hybrid_M3", "pure_llm_gpt", "pure_llm_claude", "pure_llm_qwen"],
        "hybrid_m3_gate": {
            "independent_wins_min": 2,
            "wins_must_exceed_losses": True,
            "validation_wins_min": 1,
            "clean_sentinel_regression_allowed": False,
            "strict_validation_supported_at": 2,
        },
        "source_artifacts": {
            name: portable_file_record(path, args.campaign_root)
            for name, path in source_paths.items()
            if path.is_file()
        },
        "runners": {
            name: {
                **portable_file_record(path, args.campaign_root),
                "repo_commit": repo_commit,
            }
            for name, path in runner_paths.items()
        },
        "unresolved_freeze_items": unresolved,
    }
    write_json(args.campaign_root / "execution_manifest.json", manifest)
    print(args.campaign_root / "execution_manifest.json")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subparsers = result.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare-manifest")
    prepare.add_argument("--campaign-root", type=Path, required=True)
    prepare.add_argument("--protocol", type=Path, required=True)
    prepare.add_argument("--candidate-schema", type=Path, required=True)
    prepare.add_argument("--action-policy", type=Path, default=POLICY)
    prepare.add_argument("--platform", default="sky130hd")
    prepare.add_argument("--frequency-mhz", type=float, default=100.0)
    prepare.add_argument("--split-manifest", type=Path, required=True)
    prepare.add_argument("--baseline-covariates", type=Path, required=True)
    prepare.add_argument("--sentinel-manifest", type=Path, required=True)
    prepare.add_argument("--model-routes", type=Path, required=True)
    prepare.add_argument("--resource-limits", type=Path, required=True)
    prepare.add_argument("--a-propose-prompt", type=Path, required=True)
    prepare.add_argument("--pure-llm-prompt", type=Path, required=True)
    prepare.add_argument("--freeze", action="store_true")
    prepare.set_defaults(func=build_manifest)
    matrix = subparsers.add_parser("run-matrix")
    matrix.add_argument("--campaign-root", type=Path, required=True)
    matrix.add_argument("--split-manifest", type=Path, required=True)
    matrix.add_argument("--sentinel-manifest", type=Path, required=True)
    matrix.add_argument("--candidate-bank", type=Path)
    matrix.add_argument("--challenge-root", type=Path, default=DEFAULT_CHALLENGE_ROOT)
    matrix.add_argument("--sentinel-root", type=Path, default=DEFAULT_SENTINEL_ROOT)
    matrix.add_argument("--split", choices=("a_propose", "a_validation", "b_heldout"), required=True)
    matrix.add_argument(
        "--mode",
        choices=("m0", "a_explore", "a_formal", "m1", "m2", "m3", "pure_llm"),
        required=True,
    )
    matrix.add_argument("--evidence-role", required=True)
    matrix.add_argument(
        "--arm-id",
        help="isolated result namespace (required to distinguish Pure-LLM model arms)",
    )
    matrix.add_argument(
        "--task-id",
        dest="task_ids",
        action="append",
        help="restrict execution to named tasks already assigned to the selected split",
    )
    matrix.add_argument("--include-sentinels", action="store_true")
    matrix.add_argument(
        "--sentinels-only",
        action="store_true",
        help="run only the frozen clean sentinels (normally with --mode m0)",
    )
    matrix.add_argument("--workers", type=int, default=1)
    matrix.add_argument("--cores", type=int, default=4)
    matrix.add_argument(
        "--cpu-set",
        dest="cpu_sets",
        action="append",
        help="one taskset-compatible CPU list per worker; repeat for each worker",
    )
    matrix.add_argument("--timeout-seconds", type=int, default=7200)
    matrix.add_argument(
        "--state-output",
        type=Path,
        help="explicit checkpoint path for independently resumable domain/round runs",
    )
    matrix.add_argument("--compact", action=argparse.BooleanOptionalAction, default=True)
    matrix.set_defaults(func=run_matrix)
    bundle = subparsers.add_parser("bundle-inputs")
    bundle.add_argument("--campaign-root", type=Path, required=True)
    bundle.add_argument("--split-manifest", type=Path, required=True)
    bundle.add_argument("--sentinel-manifest", type=Path, required=True)
    bundle.add_argument("--challenge-root", type=Path, default=DEFAULT_CHALLENGE_ROOT)
    bundle.add_argument("--sentinel-root", type=Path, default=DEFAULT_SENTINEL_ROOT)
    bundle.set_defaults(func=bundle_inputs)
    return result


def main() -> int:
    args = parser().parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
