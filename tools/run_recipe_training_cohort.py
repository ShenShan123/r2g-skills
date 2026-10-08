#!/usr/bin/env python3
"""Materialize and run a bounded Recipe-training cohort from an R2G candidate CSV."""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
from typing import Any


REPO = Path(__file__).resolve().parents[1]
PROBE = REPO / "tools/run_repair_family_probe.py"
ENGINEER_LOOP = REPO / "r2g-skills/signoff-loop/scripts/loop/engineer_loop.py"
PROMOTE_SCRIPTS = REPO / "r2g-skills/rtl-acquire/scripts/promote"
EXECUTE_SCRIPTS = REPO / "r2g-skills/rtl-acquire/scripts/execute"
sys.path.insert(0, str(PROMOTE_SCRIPTS))
sys.path.insert(0, str(EXECUTE_SCRIPTS))
from promote_candidates import detect_clock_port  # noqa: E402
from expand_candidates import (  # noqa: E402
    _INCLUDE_RE,
    _compile_collateral,
    _header_closure,
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def source_identity(path: Path) -> tuple[Path, str, str]:
    """Return source root, canonical repository URL, and pinned commit."""
    resolved = path.resolve()
    source = next((parent for parent in (resolved, *resolved.parents) if parent.name == "source"), None)
    if source is None or not re.fullmatch(r"[0-9a-fA-F]{40}", source.parent.name):
        raise ValueError(f"path is not in an Expander commit snapshot: {path}")
    commit = source.parent.name.lower()
    repo = source.parent.parent.name
    owner = source.parent.parent.parent.name
    provider = source.parent.parent.parent.parent.name
    if provider != "github" or not owner or not repo:
        raise ValueError(f"unsupported Expander repository identity: {path}")
    return source, f"https://github.com/{owner}/{repo}.git", commit


def _split_paths(value: str) -> list[Path]:
    return [Path(item.strip()) for item in re.split(r"[;|]", value) if item.strip()]


def _require_inside_source(paths: list[Path], source: Path, kind: str) -> None:
    for path in paths:
        try:
            path.resolve().relative_to(source.resolve())
        except ValueError as exc:
            raise ValueError(f"{kind} escapes pinned source snapshot: {path}") from exc


def _unresolved_includes(source_files: list[Path], include_dirs: list[Path]) -> list[dict[str, str]]:
    search_dirs = list(dict.fromkeys(
        path.resolve() for path in [*include_dirs, *(item.parent for item in source_files)]
    ))
    unresolved: list[dict[str, str]] = []
    for consumer in source_files:
        try:
            text = consumer.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for reference in _INCLUDE_RE.findall(text):
            if not any((directory / reference).is_file() for directory in search_dirs):
                unresolved.append({"consumer": str(consumer), "reference": reference})
    return unresolved


def row_inputs(row: dict[str, str]) -> tuple[Path, list[Path], list[Path]]:
    """Resolve immutable compilation units separately from their dependencies."""
    entries = _split_paths(row.get("rtl_files") or "")
    if not entries:
        raise ValueError("candidate has no rtl_files closure")
    source, _, _ = source_identity(entries[0])
    for entry in entries:
        other_source, _, _ = source_identity(entry)
        if other_source != source or not entry.is_file():
            raise ValueError(f"unbound or missing compilation input: {entry}")
    include_dirs = _split_paths(row.get("include_dirs") or "")
    for include_dir in include_dirs:
        if not include_dir.is_dir():
            raise ValueError(f"missing include directory: {include_dir}")
        _require_inside_source([include_dir], source, "include directory")

    headers = _header_closure(entries, include_dirs)
    all_sources = list(dict.fromkeys(path.resolve() for path in [*entries, *headers]))
    missing_includes = _unresolved_includes(all_sources, include_dirs)
    if missing_includes:
        raise ValueError(f"unresolved include closure: {missing_includes[:3]}")
    collateral, unresolved = _compile_collateral(all_sources, source)
    if unresolved:
        raise ValueError(f"unresolved compilation collateral: {unresolved[:3]}")
    collateral_paths = [Path(item["path"]).resolve() for item in collateral]
    closure = list(dict.fromkeys([*all_sources, *collateral_paths]))
    _require_inside_source(closure, source, "compilation input")
    header_suffixes = {".vh", ".svh"}
    compilation_units = [
        path.resolve() for path in entries if path.suffix.lower() not in header_suffixes
    ]
    if not compilation_units:
        raise ValueError("candidate has no Verilog/SystemVerilog compilation units")
    compilation_set = set(compilation_units)
    dependencies = [path for path in closure if path not in compilation_set]
    return source, compilation_units, dependencies


def family_id(row: dict[str, str]) -> str:
    match = re.search(r"(?:^|;\s*)family_id=([^;\s]+)", row.get("notes") or "")
    return match.group(1) if match else f"family_{row['design']}"


def prepare(args: argparse.Namespace) -> None:
    campaign = args.campaign_root.resolve()
    projects = campaign / "projects"
    records: list[dict[str, Any]] = []
    selected = 0
    with args.candidate_csv.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    requested_designs = set(args.design or [])
    for row in rows:
        if requested_designs and row.get("design") not in requested_designs:
            continue
        record: dict[str, Any] = {
            "design": row.get("design"),
            "top_module": row.get("expected_top"),
            "status": "skipped",
        }
        try:
            source, rtl_files, dependencies = row_inputs(row)
            _, repo_url, commit = source_identity(rtl_files[0])
            clock = detect_clock_port(row["expected_top"], rtl_files)
            if not clock:
                raise ValueError("no unambiguous top-level clock port")
            project = projects / row["design"]
            command = [
                sys.executable,
                str(PROBE),
                "materialize",
                "--source",
                str(source),
                "--source-repo-url",
                repo_url,
                "--source-commit",
                commit,
                "--project",
                str(project),
                "--family",
                family_id(row),
                "--task-id",
                row["design"],
                "--variant",
                "baseline",
                "--platform",
                args.platform,
                "--top-module",
                row["expected_top"],
                "--clock-port",
                clock,
                "--frequency-mhz",
                str(args.frequency_mhz),
            ]
            for rtl_file in rtl_files:
                command.extend(["--rtl-file", str(rtl_file.relative_to(source))])
            for dependency in dependencies:
                command.extend(["--dependency-file", str(dependency.relative_to(source))])
            if project.exists():
                existing = project / "repair_family_probe_input.json"
                if not existing.is_file():
                    raise ValueError("project exists without a frozen probe manifest")
                action = "reused"
            else:
                subprocess.run(command, check=True, text=True, capture_output=True)
                action = "materialized"
            selected += 1
            record.update(
                {
                    "status": "ready",
                    "action": action,
                    "project": str(project),
                    "family_id": family_id(row),
                    "clock_port": clock,
                    "source_repo_url": repo_url,
                    "source_commit": commit,
                }
            )
        except (KeyError, OSError, ValueError, subprocess.CalledProcessError) as exc:
            record["reason"] = str(exc)
        records.append(record)
        if selected >= args.limit:
            break
    manifest = {
        "schema_version": "recipe-training-cohort-1.0",
        "created_at": now(),
        "candidate_csv": str(args.candidate_csv.resolve()),
        "candidate_csv_sha256": sha256_file(args.candidate_csv),
        "platform": args.platform,
        "target_frequency_mhz": args.frequency_mhz,
        "requested_ready": args.limit,
        "ready_count": selected,
        "records": records,
    }
    write_json(campaign / "state/cohort_manifest.json", manifest)
    print(json.dumps({"ready_count": selected, "record_count": len(records)}, indent=2))


def _probe_command(project: Path, args: argparse.Namespace, *, orfs_stages: str | None = None) -> list[str]:
    command = [
        sys.executable,
        str(PROBE),
        "execute",
        "--project",
        str(project),
        "--cores",
        str(args.cores),
        "--timeout-seconds",
        str(args.timeout_seconds),
        "--min-mapped-cells",
        str(getattr(args, "min_mapped_cells", 100)),
        "--max-mapped-cells",
        str(getattr(args, "max_mapped_cells", 100000)),
    ]
    if orfs_stages:
        command.extend(["--orfs-stages", orfs_stages])
    return command


def force_rerun_args(args: argparse.Namespace) -> argparse.Namespace:
    """Clone parsed arguments while forcing a full run after an admitted preflight."""
    return argparse.Namespace(**(vars(args) | {"rerun": True}))


def has_complete_constraint_coverage(result: dict[str, Any]) -> bool:
    """Return true only for a result carrying current explicit SDC coverage proof."""
    coverage = result.get("constraint_coverage")
    return isinstance(coverage, dict) and coverage.get("status") == "complete"


def execute_one(project: Path, args: argparse.Namespace) -> dict[str, Any]:
    result_path = project / "repair_family_probe_result.json"
    if result_path.is_file() and not args.rerun:
        return {"project": str(project), "status": "reused", "returncode": 0}
    command = _probe_command(project, args)
    completed = subprocess.run(command, text=True, capture_output=True)
    return {
        "project": str(project),
        "status": "completed" if completed.returncode == 0 else "runner_failed",
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-2000:],
        "stderr_tail": completed.stderr[-2000:],
    }


def execute(args: argparse.Namespace) -> None:
    campaign = args.campaign_root.resolve()
    manifest_path = campaign / "state/cohort_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    projects = [Path(item["project"]) for item in manifest["records"] if item["status"] == "ready"]
    preflight_admitted: set[Path] = set()
    if getattr(args, "synth_floorplan_preflight", True):
        admitted: list[Path] = []
        preflight_results: list[dict[str, Any]] = []
        for project in projects:
            result_path = project / "repair_family_probe_result.json"
            if result_path.is_file() and not args.rerun:
                existing = read_json(result_path, {})
                if existing.get("scale_ineligible") is True:
                    preflight_results.append(
                        {"project": str(project), "status": "scale_ineligible", "returncode": 0}
                    )
                    continue
                if existing.get("execution_interrupted") is True:
                    preflight_results.append(
                        {"project": str(project), "status": "execution_interrupted", "returncode": 0}
                    )
                    continue
                if existing.get("constraint_coverage_incomplete") is True:
                    preflight_results.append(
                        {"project": str(project), "status": "constraint_ineligible", "returncode": 0}
                    )
                    continue
                if has_complete_constraint_coverage(existing):
                    admitted.append(project)
                    if existing.get("orfs_stages") != "synth floorplan place cts route finish":
                        preflight_admitted.add(project)
                    continue
                # Pre-coverage records are legacy evidence, not an implicit pass.
                # Re-run the cheap synth+floorplan preflight below under the current
                # fail-closed coverage policy before permitting a full physical flow.
            command = _probe_command(project, args, orfs_stages="synth floorplan")
            completed = subprocess.run(command, text=True, capture_output=True)
            result = read_json(result_path, {})
            preflight_record = {
                "project": str(project),
                "status": "completed" if completed.returncode == 0 else "runner_failed",
                "returncode": completed.returncode,
                "mapped_cells": result.get("mapped_cells"),
                "scale_ineligible": result.get("scale_ineligible") is True,
                "constraint_coverage": result.get("constraint_coverage"),
                "stdout_tail": completed.stdout[-2000:],
                "stderr_tail": completed.stderr[-2000:],
            }
            preflight_results.append(preflight_record)
            write_json(campaign / "state/scale_preflight" / f"{project.name}.json", result)
            if (
                completed.returncode == 0
                and result.get("scale_ineligible") is not True
                and result.get("constraint_coverage_incomplete") is not True
            ):
                admitted.append(project)
                preflight_admitted.add(project)
        projects = admitted
        write_json(
            campaign / "state/scale_preflight_summary.json",
            {
                "schema_version": "recipe-training-scale-preflight-1.0",
                "updated_at": now(),
                "min_mapped_cells": getattr(args, "min_mapped_cells", 100),
                "max_mapped_cells": getattr(args, "max_mapped_cells", 100000),
                "results": preflight_results,
            },
        )
    results: list[dict[str, Any]] = []
    full_flow_args = force_rerun_args(args)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(execute_one, project, full_flow_args if project in preflight_admitted else args): project
            for project in projects
        }
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)
            write_json(
                campaign / "state/cohort_execution.json",
                {"schema_version": "recipe-training-execution-1.0", "updated_at": now(), "results": results},
            )
            print(f"[{len(results)}/{len(projects)}] {Path(result['project']).name}: {result['status']}", flush=True)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def stable_replay_projects(campaign: Path) -> tuple[bool, list[Path]]:
    evidence_root = campaign / "state/failure_replay"
    eligible_count = sum(1 for path in evidence_root.glob("*/attempt_1.json") if path.is_file())
    summary = read_json(campaign / "state/failure_replay_summary.json", {})
    results = summary.get("results", []) if isinstance(summary, dict) else []
    explicit_complete = summary.get("complete") if isinstance(summary, dict) else None
    reported_eligible = summary.get("eligible_count") if isinstance(summary, dict) else None
    if explicit_complete is not None:
        complete = (
            explicit_complete is True
            and reported_eligible == eligible_count
            and len(results) == eligible_count
        )
    else:
        # Backward compatibility for replay summaries written before the explicit
        # completion marker existed.  Legacy zero-task summaries were ambiguous and
        # remain incomplete; new writers stamp that case explicitly.
        complete = eligible_count > 0 and len(results) == eligible_count
    stable = [
        Path(item["project"])
        for item in results
        if item.get("status") == "stable_repair_challenge" and item.get("project")
    ]
    return complete, stable


def stable_replay_flow_evidence(campaign: Path, project: Path) -> tuple[int, Path]:
    """Return the verified second baseline's ORFS return code and evidence path.

    A probe records the ORFS run followed by checker/signoff commands.  The latter can
    deliberately return nonzero for a real physical symptom, so they must never be
    replayed as an ORFS crash by the repair loop.
    """
    evidence = campaign / "state/failure_replay" / project.name / "attempt_2.json"
    result = read_json(evidence, {})
    commands = result.get("commands", []) if isinstance(result, dict) else []
    flow_commands = [
        item for item in commands
        if isinstance(item, dict)
        and any(Path(str(part)).name == "run_orfs.sh" for part in item.get("command", []))
    ]
    if len(flow_commands) != 1:
        raise ValueError(f"stable replay has no reusable flow command: {evidence}")
    return int(flow_commands[0]["returncode"]), evidence


def quarantine_preexisting_candidates(db_path: Path, platform: str) -> list[dict[str, Any]]:
    """Park inherited A/B backlog in an isolated campaign database.

    Promoted recipes remain available to the live repair loop. Only candidates
    that predate this campaign are parked, so candidates learned by the campaign
    itself remain eligible for its subsequent A/B drain.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT symptom_id, design_class, platform, strategy, status_version "
            "FROM recipe_status WHERE status='candidate' AND platform=? "
            "ORDER BY symptom_id, design_class, strategy",
            (platform,),
        ).fetchall()
        evidence = [dict(row) for row in rows]
        if rows:
            updated_at = now()
            conn.executemany(
                "UPDATE recipe_status SET status='parked', provenance=?, updated_at=?, "
                "status_version=COALESCE(status_version, 0)+1 "
                "WHERE symptom_id=? AND design_class=? AND platform=? AND strategy=? "
                "AND status='candidate'",
                [
                    (
                        "campaign_quarantine_preexisting_candidate",
                        updated_at,
                        row["symptom_id"],
                        row["design_class"],
                        row["platform"],
                        row["strategy"],
                    )
                    for row in rows
                ],
            )
            conn.commit()
        return evidence
    finally:
        conn.close()


def protect_fixed_clock_target(env: dict[str, str]) -> dict[str, str]:
    """Return an environment that cannot select the clock-relaxation strategy."""
    protected = dict(env)
    excluded = [item for item in protected.get("R2G_FIX_EXCLUDE", "").split(",") if item]
    if "period_relax" not in excluded:
        excluded.append("period_relax")
    protected["R2G_FIX_EXCLUDE"] = ",".join(excluded)
    return protected


_FOOTPRINT_CHANGING_STRATEGIES = (
    "core_util_relief",
    "pin_perimeter_floor",
    "pdn_die_floor",
    "density_relief",
    "route_relief",
    "antenna_density_relief",
    "utilization_reduce",
    "synth_memory_relax",
)


def protect_fixed_footprint(env: dict[str, str]) -> dict[str, str]:
    """Exclude every known strategy that changes the registered die/core footprint."""
    protected = dict(env)
    excluded = [item for item in protected.get("R2G_FIX_EXCLUDE", "").split(",") if item]
    for strategy in _FOOTPRINT_CHANGING_STRATEGIES:
        if strategy not in excluded:
            excluded.append(strategy)
    protected["R2G_FIX_EXCLUDE"] = ",".join(excluded)
    return protected


def run_existing_recipes(args: argparse.Namespace) -> None:
    campaign = args.campaign_root.resolve()
    started = time.monotonic()
    while True:
        complete, projects = stable_replay_projects(campaign)
        if complete:
            break
        if args.max_wait_seconds and time.monotonic() - started >= args.max_wait_seconds:
            raise TimeoutError("stable-failure replay did not complete before wait budget")
        time.sleep(args.poll_seconds)

    if not projects:
        write_json(
            campaign / "state/existing_recipe_execution.json",
            {
                "schema_version": "existing-recipe-execution-1.1",
                "updated_at": now(),
                "status": "no_stable_repair_challenges",
                "stable_projects": [],
                "knowledge_db": str(args.runtime_db.resolve()),
                "heuristics": str(args.heuristics.resolve()),
                "fixed_clock_target": bool(args.fixed_clock_target),
                "fixed_footprint": bool(args.fixed_footprint),
                "frozen_knowledge": bool(args.frozen_knowledge),
                "quarantined_preexisting_candidates": [],
                "records": [],
            },
        )
        return

    quarantined: list[dict[str, Any]] = []
    if args.quarantine_preexisting_candidates:
        quarantined = quarantine_preexisting_candidates(args.runtime_db, args.platform)
        write_json(
            campaign / "state/preexisting_candidate_quarantine.json",
            {
                "schema_version": "recipe-training-candidate-quarantine-1.0",
                "created_at": now(),
                "knowledge_db": str(args.runtime_db.resolve()),
                "platform": args.platform,
                "records": quarantined,
            },
        )

    ledger = campaign / "state/existing_recipe_ledger.jsonl"
    known: set[str] = set()
    if ledger.is_file():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            try:
                known.add(json.loads(line)["design"])
            except (KeyError, json.JSONDecodeError):
                continue
    for project in projects:
        if project.name in known:
            continue
        flow_returncode, replay_evidence = stable_replay_flow_evidence(campaign, project)
        subprocess.run(
            [
                sys.executable,
                str(ENGINEER_LOOP),
                "add",
                "--ledger",
                str(ledger),
                "--project",
                str(project),
                "--platform",
                args.platform,
                "--reuse-flow-returncode",
                str(flow_returncode),
                "--replay-evidence",
                str(replay_evidence),
            ],
            check=True,
        )

    env = dict(os.environ)
    env.update(
        {
            "R2G_KNOWLEDGE_DB": str(args.runtime_db.resolve()),
            "R2G_HEURISTICS_PATH": str(args.heuristics.resolve()),
            "R2G_JOURNAL_DB": str(args.journal_db.resolve()),
            "NUM_CORES": str(args.cores),
            "R2G_AB_WORKERS": str(args.workers),
        }
    )
    if args.fixed_clock_target:
        env = protect_fixed_clock_target(env)
    if args.fixed_footprint:
        env = protect_fixed_footprint(env)
    run_command = [
        sys.executable, str(ENGINEER_LOOP), "run", "--ledger", str(ledger),
        "--workers", str(args.workers),
    ]
    if args.frozen_knowledge:
        run_command.append("--no-learn")
    commands = [run_command]
    if not args.frozen_knowledge:
        commands.append(
            [sys.executable, str(ENGINEER_LOOP), "ab-drain", "--ledger", str(ledger),
             "--workers", str(args.workers)]
        )
    records = []
    for command in commands:
        before = time.monotonic()
        completed = subprocess.run(command, env=env)
        records.append(
            {
                "command": command,
                "returncode": completed.returncode,
                "elapsed_seconds": round(time.monotonic() - before, 3),
            }
        )
        write_json(
            campaign / "state/existing_recipe_execution.json",
            {
                "schema_version": "existing-recipe-execution-1.0",
                "updated_at": now(),
                "stable_projects": [str(project) for project in projects],
                "ledger": str(ledger),
                "knowledge_db": str(args.runtime_db.resolve()),
                "heuristics": str(args.heuristics.resolve()),
                "fixed_clock_target": bool(args.fixed_clock_target),
                "fixed_footprint": bool(args.fixed_footprint),
                "frozen_knowledge": bool(args.frozen_knowledge),
                "quarantined_preexisting_candidates": quarantined,
                "records": records,
            },
        )
        if completed.returncode != 0:
            raise subprocess.CalledProcessError(completed.returncode, command)


def replay_failures(args: argparse.Namespace) -> None:
    campaign = args.campaign_root.resolve()
    manifest = read_json(campaign / "state/cohort_manifest.json", {})
    projects = [Path(item["project"]) for item in manifest.get("records", []) if item.get("status") == "ready"]
    eligible: list[Path] = []
    evidence_root = campaign / "state/failure_replay"
    for project in projects:
        first = read_json(project / "repair_family_probe_result.json")
        if not isinstance(first, dict) or first.get("strict_clean") is True:
            continue
        if first.get("environment_failure") is True:
            continue
        if first.get("input_qualification_failure") is True:
            continue
        if first.get("capacity_infeasible") is True:
            continue
        if first.get("scale_ineligible") is True:
            continue
        if first.get("constraint_coverage_incomplete") is True:
            continue
        if first.get("timing_evaluation_incomplete") is True:
            continue
        if first.get("execution_interrupted") is True:
            continue
        if first.get("unclassified_execution_failure") is True:
            continue
        target = evidence_root / project.name
        target.mkdir(parents=True, exist_ok=True)
        write_json(target / "attempt_1.json", first)
        eligible.append(project)

    replay_args = argparse.Namespace(
        cores=args.cores,
        timeout_seconds=args.timeout_seconds,
        rerun=True,
    )
    completed: list[dict[str, Any]] = []
    summary_path = campaign / "state/failure_replay_summary.json"
    write_json(
        summary_path,
        {
            "schema_version": "repair-training-replay-1.1",
            "updated_at": now(),
            "eligible_count": len(eligible),
            "completed_count": 0,
            "complete": not eligible,
            "results": [],
        },
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(execute_one, project, replay_args): project for project in eligible}
        for future in concurrent.futures.as_completed(futures):
            project = futures[future]
            execution = future.result()
            first = read_json(evidence_root / project.name / "attempt_1.json", {})
            second = read_json(project / "repair_family_probe_result.json", {})
            write_json(evidence_root / project.name / "attempt_2.json", second)
            first_signature = first.get("normalized_failure_signature") or []
            second_signature = second.get("normalized_failure_signature") or []
            stable = (
                execution["returncode"] == 0
                and first.get("strict_clean") is False
                and second.get("strict_clean") is False
                and first.get("environment_failure") is not True
                and second.get("environment_failure") is not True
                and first.get("execution_interrupted") is not True
                and second.get("execution_interrupted") is not True
                and first.get("unclassified_execution_failure") is not True
                and second.get("unclassified_execution_failure") is not True
                and first.get("protected_task_digest") == second.get("protected_task_digest")
                and first_signature == second_signature
            )
            item = {
                "project": str(project),
                "status": "stable_repair_challenge" if stable else "unstable_or_ineligible",
                "attempt_1_run_id": first.get("run_id"),
                "attempt_2_run_id": second.get("run_id"),
                "attempt_1_signature": first_signature,
                "attempt_2_signature": second_signature,
                "execution": execution,
            }
            completed.append(item)
            write_json(
                summary_path,
                {
                    "schema_version": "repair-training-replay-1.1",
                    "updated_at": now(),
                    "eligible_count": len(eligible),
                    "completed_count": len(completed),
                    "complete": len(completed) == len(eligible),
                    "results": completed,
                },
            )
            print(f"[{len(completed)}/{len(eligible)}] {project.name}: {item['status']}", flush=True)


def summarize(args: argparse.Namespace) -> None:
    campaign = args.campaign_root.resolve()
    manifest = read_json(campaign / "state/cohort_manifest.json", {})
    summary = {
        "schema_version": "recipe-training-summary-1.0",
        "generated_at": now(),
        "ready": 0,
        "pending": 0,
        "strict_clean": 0,
        "repair_challenge": 0,
        "environment_failure": 0,
        "input_qualification_failure": 0,
        "capacity_infeasible": 0,
        "scale_ineligible": 0,
        "constraint_ineligible": 0,
        "execution_interrupted": 0,
        "unclassified_execution_failure": 0,
        "runner_failure": 0,
        "failure_signatures": {},
        "records": [],
    }
    for record in manifest.get("records", []):
        if record.get("status") != "ready":
            continue
        summary["ready"] += 1
        project = Path(record["project"])
        result = read_json(project / "repair_family_probe_result.json")
        item = {"design": record.get("design"), "project": str(project)}
        if not isinstance(result, dict):
            status = "pending"
        elif result.get("environment_failure") is True:
            status = "environment_failure"
        elif result.get("input_qualification_failure") is True:
            status = "input_qualification_failure"
        elif result.get("capacity_infeasible") is True:
            status = "capacity_infeasible"
        elif result.get("scale_ineligible") is True:
            status = "scale_ineligible"
        elif result.get("constraint_coverage_incomplete") is True:
            status = "constraint_ineligible"
        elif result.get("timing_evaluation_incomplete") is True:
            status = "constraint_ineligible"
        elif result.get("execution_interrupted") is True:
            status = "execution_interrupted"
        elif result.get("unclassified_execution_failure") is True:
            status = "unclassified_execution_failure"
        elif result.get("strict_clean") is True:
            status = "strict_clean"
        else:
            status = "repair_challenge"
            for signature in result.get("normalized_failure_signature") or ["UNCLASSIFIED"]:
                counts = summary["failure_signatures"]
                counts[signature] = counts.get(signature, 0) + 1
        summary[status] += 1
        item.update({"status": status, "result": result})
        summary["records"].append(item)
    execution = read_json(campaign / "state/cohort_execution.json", {})
    summary["runner_failure"] = sum(
        item.get("returncode", 0) != 0 for item in execution.get("results", [])
    )
    write_json(campaign / "state/cohort_summary.json", summary)
    print(json.dumps({key: value for key, value in summary.items() if key not in {"records"}}, indent=2, sort_keys=True))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subparsers = result.add_subparsers(dest="command", required=True)
    materialize = subparsers.add_parser("materialize")
    materialize.add_argument("--candidate-csv", type=Path, required=True)
    materialize.add_argument("--campaign-root", type=Path, required=True)
    materialize.add_argument("--limit", type=int, default=20)
    materialize.add_argument("--platform", default="sky130hd", choices=("sky130hd", "sky130hs", "nangate45"))
    materialize.add_argument("--frequency-mhz", type=float, default=100.0)
    materialize.add_argument(
        "--design",
        action="append",
        help="materialize only this design ID; repeat for multiple designs",
    )
    materialize.set_defaults(func=prepare)
    run = subparsers.add_parser("execute")
    run.add_argument("--campaign-root", type=Path, required=True)
    run.add_argument("--workers", type=int, default=2)
    run.add_argument("--cores", type=int, default=4)
    run.add_argument("--timeout-seconds", type=int, default=7200)
    run.add_argument("--min-mapped-cells", type=int, default=100)
    run.add_argument("--max-mapped-cells", type=int, default=100000)
    run.add_argument(
        "--synth-floorplan-preflight",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="admit only tasks at or below --max-mapped-cells before full P&R",
    )
    run.add_argument("--rerun", action="store_true")
    run.set_defaults(func=execute)
    replay = subparsers.add_parser("replay-failures")
    replay.add_argument("--campaign-root", type=Path, required=True)
    replay.add_argument("--workers", type=int, default=2)
    replay.add_argument("--cores", type=int, default=4)
    replay.add_argument("--timeout-seconds", type=int, default=7200)
    replay.set_defaults(func=replay_failures)
    repair = subparsers.add_parser(
        "run-existing-recipes",
        help="wait for stable replay evidence, then run the isolated R2G repair loop",
    )
    repair.add_argument("--campaign-root", type=Path, required=True)
    repair.add_argument("--runtime-db", type=Path, required=True)
    repair.add_argument("--heuristics", type=Path, required=True)
    repair.add_argument("--journal-db", type=Path, required=True)
    repair.add_argument("--platform", default="sky130hd")
    repair.add_argument("--workers", type=int, default=2)
    repair.add_argument("--cores", type=int, default=4)
    repair.add_argument("--poll-seconds", type=int, default=60)
    repair.add_argument("--max-wait-seconds", type=int, default=0)
    repair.add_argument(
        "--fixed-clock-target",
        action="store_true",
        help="protect the registered clock target by excluding period_relax",
    )
    repair.add_argument(
        "--fixed-footprint",
        action="store_true",
        help="protect the registered die/core footprint by excluding area-changing repairs",
    )
    repair.add_argument(
        "--frozen-knowledge",
        action="store_true",
        help="read existing Recipe evidence but do not learn, enqueue, or run A/B in this pass",
    )
    repair.add_argument(
        "--quarantine-preexisting-candidates",
        action="store_true",
        help="park inherited candidate backlog in the isolated runtime DB before this campaign",
    )
    repair.set_defaults(func=run_existing_recipes)
    report = subparsers.add_parser("summarize")
    report.add_argument("--campaign-root", type=Path, required=True)
    report.set_defaults(func=summarize)
    return result


def main() -> int:
    args = parser().parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
