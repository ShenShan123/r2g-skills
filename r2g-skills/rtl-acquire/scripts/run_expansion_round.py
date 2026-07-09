#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from common.io_utils import load_json, now_iso
from common.manifest_utils import changed_global_targets, snapshot_global_targets, write_run_manifest
from common.state_utils import compact_status_log, pid_is_alive, refresh_design_stage_index
from skill_env import (
    default_data_root,
    default_downloads_root,
    default_merged_manifest,
    default_out_root,
    default_python_bin,
    default_seed_root,
    default_workspace_root,
    graph_python,
    resolve_path_env,
)

DATA_ROOT = default_data_root()
WORKSPACE_ROOT = default_workspace_root()
PYTHON_BIN = default_python_bin()
DOWNLOADS_ROOT = default_downloads_root()
DISCOVER_SCRIPT = SCRIPT_DIR / "acquire" / "discover_download_candidates.py"
CLASSIFY_SCRIPT = SCRIPT_DIR / "repair" / "classify_failed_candidates.py"
FAILURE_KB_CANDIDATES_SCRIPT = SCRIPT_DIR / "repair" / "extract_failure_kb_candidates.py"
REFRESH_FAILURE_KB_SCRIPT = SCRIPT_DIR / "repair" / "refresh_failure_knowledge_base.py"
AUTO_FIX_SCRIPT = SCRIPT_DIR / "repair" / "auto_fix_failures.py"
AUTO_FIX_PLAN = WORKSPACE_ROOT / "failures" / "auto_fix_plan.json"
CLONE_REPO_SCRIPT = SCRIPT_DIR / "acquire" / "clone_repo_manifest.py"
RETRY_CANDIDATES = WORKSPACE_ROOT / "failures" / "failed_candidates_retry_candidates.csv"
REBUILD_INDEX_SCRIPT = SCRIPT_DIR / "publish" / "rebuild_external_index_from_dirs.py"
REFRESH_SCRIPT = SCRIPT_DIR / "publish" / "refresh_expanded_raw_manifest.py"
BUILD_PUBLISH_CANDIDATES_SCRIPT = SCRIPT_DIR / "publish" / "build_publish_candidates.py"
DEDUP_SCRIPT = SCRIPT_DIR / "validate" / "check_mapped_netlist_duplicates.py"
SUMMARIZE_SCRIPT = SCRIPT_DIR / "report" / "summarize_external_index.py"
SCALE_SCRIPT = SCRIPT_DIR / "report" / "summarize_dataset_scale.py"
SCORE_SCRIPT = SCRIPT_DIR / "report" / "score_download_repos.py"
CLEANUP_SCRIPT = SCRIPT_DIR / "hygiene" / "cleanup_rejected_download_repos.py"
DESIGN_SCORE_SCRIPT = SCRIPT_DIR / "report" / "score_design_quality.py"
UPDATE_STRATEGY_SCRIPT = SCRIPT_DIR / "repair" / "update_failure_strategy_scores.py"
AUTO_SIGNATURE_ACTIONS = SCRIPT_DIR / "repair" / "auto_generate_signature_actions.py"
VALIDATE_PUBLISH_SCRIPT = SCRIPT_DIR / "validate" / "validate_publish_readiness.py"
FAILURE_CASEBOOK_SCRIPT = SCRIPT_DIR / "repair" / "build_failure_casebook.py"
FAILURE_DIAGNOSIS_SCRIPT = SCRIPT_DIR / "repair" / "build_failure_diagnosis.py"
LLM_REPAIR_CASES_SCRIPT = SCRIPT_DIR / "repair" / "build_llm_repair_cases.py"
DATASET_SNAPSHOT_SCRIPT = SCRIPT_DIR / "publish" / "record_dataset_snapshot.py"
PROJECT_DIAGNOSIS_SCRIPT = SCRIPT_DIR / "knowledge" / "project_frontend_diagnosis.py"
EXPAND_SCRIPT = SCRIPT_DIR / "execute" / "expand_candidates.py"
OUT_ROOT = default_out_root()
ORFS_SEED_ROOT = default_seed_root()
DEFAULT_STATUS_JSON = resolve_path_env("R2G_ACQUIRE_STATUS_JSON", DATA_ROOT / "rtl_acquire_status.json")
DEFAULT_STATUS_LOG = resolve_path_env("R2G_ACQUIRE_STATUS_LOG", DATA_ROOT / "rtl_acquire_status.log")
SCOPED_RETRY_CANDIDATES = WORKSPACE_ROOT / "failures" / "failed_candidates_retry_candidates_scoped.csv"
POLICY_DIR = SCRIPT_DIR.parent / "references"
RUN_MANIFEST_LATEST = WORKSPACE_ROOT / "runs" / "run_manifest_latest.json"
COMMAND_HISTORY: list[dict] = []

def write_status(status_path: Path, payload: dict) -> None:
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def read_policy(path: Path) -> dict:
    payload = load_json(path)
    return payload if isinstance(payload, dict) else {}


def tracked_global_targets() -> dict[str, Path]:
    return {
        "failure_kb": POLICY_DIR / "failure_knowledge_base.md",
        "failure_strategy": POLICY_DIR / "failure_strategy.json",
        "scan_state": WORKSPACE_ROOT / "scan_state" / "downloads_scan_state.json",
        "failed_candidates_exclude": WORKSPACE_ROOT / "failures" / "failed_candidates_exclude.csv",
        "failed_candidates_retry": WORKSPACE_ROOT / "failures" / "failed_candidates_retry_candidates.csv",
        "auto_fix_plan": WORKSPACE_ROOT / "failures" / "auto_fix_plan.json",
        "repair_action_log": WORKSPACE_ROOT / "failures" / "repair_action_log.json",
        "failure_signatures": WORKSPACE_ROOT / "failures" / "failure_signatures.json",
        "failure_signature_actions": WORKSPACE_ROOT / "failures" / "failure_signature_actions.json",
        "failure_families": WORKSPACE_ROOT / "failures" / "failure_families.json",
        "failure_casebook": WORKSPACE_ROOT / "failures" / "failure_casebook.json",
        "failure_diagnosis": WORKSPACE_ROOT / "failures" / "failure_diagnosis.json",
        "design_quality_scores": WORKSPACE_ROOT / "quality" / "design_quality_scores.csv",
        "repo_quality_scores": WORKSPACE_ROOT / "quality" / "download_repo_quality.csv",
        "publish_validation": WORKSPACE_ROOT / "quality" / "publish_validation.json",
        "publish_eligible_designs": WORKSPACE_ROOT / "manifests" / "publish_eligible_designs.csv",
        "merged_manifest": default_merged_manifest(),
    }


def run(
    cmd: list[str],
    *,
    status_path: Path,
    log_path: Path,
    payload: dict,
    extra_env: dict[str, str] | None = None,
) -> None:
    printable = " ".join(cmd)
    print("+", printable, flush=True)
    COMMAND_HISTORY.append({"phase": payload.get("phase", ""), "cmd": cmd[:]})
    payload["active_command"] = printable
    payload["updated_at"] = now_iso()
    write_status(status_path, payload)

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as logf:
        logf.write(f"\n[{now_iso()}] CMD {printable}\n")
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env={**os.environ, **(extra_env or {})},
        )
        last_line = ""
        assert proc.stdout is not None
        for line in proc.stdout:
            logf.write(line)
            logf.flush()
            last_line = line.rstrip()
            payload["last_output_line"] = last_line
            payload["updated_at"] = now_iso()
            write_status(status_path, payload)
        ret = proc.wait()
        payload["last_return_code"] = ret
        payload["last_output_line"] = last_line
        payload["updated_at"] = now_iso()
        write_status(status_path, payload)
        if payload.get("phase") in {"batch_expand", "retry_expand"}:
            compact_status_log(log_path)
            refresh_design_stage_index(OUT_ROOT)
        if ret != 0:
            raise subprocess.CalledProcessError(ret, cmd)


def has_rows(path: Path) -> bool:
    if not path.exists():
        return False
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    return len(lines) > 1


def candidate_count(path: Path) -> int:
    if not path.exists():
        return 0
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    return max(0, len(lines) - 1)


def load_candidate_designs(path: Path) -> list[str]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return [row.get("design", "") for row in csv.DictReader(handle) if row.get("design", "")]


def load_index_statuses(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        return {row.get("design", ""): row.get("status", "") for row in csv.DictReader(handle) if row.get("design", "")}


def load_csv_designs(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as handle:
        return {row.get("design", "") for row in csv.DictReader(handle) if row.get("design", "")}


def reconcile_stale_status(previous: dict, candidate_hint: Path) -> tuple[str, str, str]:
    if candidate_count(candidate_hint) == 0:
        return (
            "completed",
            "recovered_completed",
            "previous expansion round was no longer alive and candidate list was empty; status auto-recovered to completed",
        )

    candidate_designs = load_candidate_designs(candidate_hint)
    if not candidate_designs:
        return (
            "completed",
            "recovered_completed",
            "previous expansion round was no longer alive and candidate csv had no rows; status auto-recovered to completed",
        )

    index_statuses = load_index_statuses(OUT_ROOT / "index.csv")
    if not all(design in index_statuses for design in candidate_designs):
        return (
            "stale_interrupted",
            "stale_detected",
            "previous expansion round was no longer alive and not all candidate designs were indexed; status marked stale",
        )

    quality_designs = load_csv_designs(WORKSPACE_ROOT / "quality" / "design_quality_scores.csv")
    publish_designs = load_csv_designs(WORKSPACE_ROOT / "manifests" / "publish_eligible_designs.csv")
    validation_ready = (WORKSPACE_ROOT / "quality" / "publish_validation.json").exists()

    if all(design in quality_designs for design in candidate_designs) and all(design in publish_designs for design in candidate_designs) and validation_ready:
        return (
            "completed",
            "recovered_completed",
            "previous expansion round was no longer alive, candidate designs were indexed, and downstream quality/publish artifacts were present; status auto-recovered to completed",
        )

    return (
        "stale_after_batch",
        "recovered_batch_complete",
        "previous expansion round was no longer alive but candidate designs were indexed; batch expansion completed and downstream post-processing was incomplete",
    )


def build_scoped_retry_candidates(original_candidate_csv: Path, retry_candidates_csv: Path, out_csv: Path) -> Path | None:
    if not original_candidate_csv.exists() or not retry_candidates_csv.exists():
        return None

    with original_candidate_csv.open(newline="", encoding="utf-8") as f:
        original_rows = list(csv.DictReader(f))
    if not original_rows:
        return None

    original_by_design = {row.get("design", ""): row for row in original_rows if row.get("design", "")}
    original_by_source = {row.get("source_path", ""): row for row in original_rows if row.get("source_path", "")}

    with retry_candidates_csv.open(newline="", encoding="utf-8") as f:
        retry_rows = list(csv.DictReader(f))

    fieldnames = [
        "source_group",
        "design",
        "priority",
        "expected_top",
        "source_path",
        "rtl_files",
        "include_dirs",
        "notes",
    ]
    scoped_rows: list[dict[str, str]] = []
    seen_designs: set[str] = set()

    for retry_row in retry_rows:
        design = retry_row.get("design", "")
        source_path = retry_row.get("source_path", "")
        original = original_by_design.get(design) or original_by_source.get(source_path)
        if not original:
            continue
        scoped_design = original.get("design", design)
        if not scoped_design or scoped_design in seen_designs:
            continue
        seen_designs.add(scoped_design)
        scoped_rows.append(
            {
                "source_group": original.get("source_group") or original.get("source") or "",
                "design": scoped_design,
                "priority": original.get("priority") or retry_row.get("priority") or "high",
                "expected_top": original.get("expected_top") or retry_row.get("expected_top") or "top",
                "source_path": original.get("source_path") or source_path,
                "rtl_files": original.get("rtl_files", ""),
                "include_dirs": original.get("include_dirs", ""),
                "notes": retry_row.get("notes") or original.get("notes", ""),
            }
        )

    if not scoped_rows:
        return None

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(scoped_rows)
    return out_csv


def select_retry_candidates(
    *,
    candidate_csv: Path | None,
    retry_candidates_csv: Path,
    auto_fix_retry_csv: Path | None,
    scoped_out_csv: Path,
    retry_scope: str,
) -> Path | None:
    source = (
        auto_fix_retry_csv
        if auto_fix_retry_csv is not None and has_rows(auto_fix_retry_csv)
        else retry_candidates_csv
    )
    if retry_scope == "scoped" and candidate_csv is not None:
        return build_scoped_retry_candidates(candidate_csv, source, scoped_out_csv)
    return source if has_rows(source) else None


def main() -> None:
    parser = argparse.ArgumentParser(description="One-shot orchestration for nangate45 raw graph expansion.")
    parser.add_argument("--candidate-csv", type=Path, default=None)
    parser.add_argument("--repo-manifest-csv", type=Path, default=None)
    parser.add_argument("--clone-missing", action="store_true", help="Clone missing repos from a repo manifest into _downloads before discover/expand.")
    parser.add_argument("--discover", action="store_true", help="Discover new candidates from _downloads before running.")
    parser.add_argument("--downloads-root", type=Path, default=DOWNLOADS_ROOT)
    parser.add_argument("--discovered-out", type=Path, default=WORKSPACE_ROOT / "candidates" / "downloads_discovered_candidates.csv")
    parser.add_argument("--priorities", nargs="+", default=["high", "medium", "low"])
    parser.add_argument("--round-index", type=int, default=0)
    parser.add_argument("--status-json", type=Path, default=DEFAULT_STATUS_JSON)
    parser.add_argument("--status-log", type=Path, default=DEFAULT_STATUS_LOG)
    parser.add_argument("--skip-batch", action="store_true")
    parser.add_argument("--skip-classify", action="store_true")
    parser.add_argument("--run-retry", action="store_true", help="If retry candidates are emitted, rerun them once.")
    parser.add_argument("--skip-refresh", action="store_true")
    parser.add_argument("--skip-rebuild-index", action="store_true")
    parser.add_argument("--skip-duplicates", action="store_true")
    parser.add_argument("--skip-summary", action="store_true")
    parser.add_argument("--skip-scale-report", action="store_true")
    parser.add_argument("--skip-quality-report", action="store_true")
    parser.add_argument("--skip-design-score", action="store_true")
    parser.add_argument("--cleanup-rejected", action="store_true")
    parser.add_argument("--delete-rejected", action="store_true")
    parser.add_argument("--allow-high-mem", action="store_true", help="Allow candidates tagged resource_tier=high to run on this node.")
    parser.add_argument("--candidate-policy-json", type=Path, default=POLICY_DIR / "candidate_policy.json")
    parser.add_argument("--repair-policy-json", type=Path, default=POLICY_DIR / "repair_policy.json")
    parser.add_argument("--quality-policy-json", type=Path, default=POLICY_DIR / "quality_policy.json")
    parser.add_argument("--mutation-policy-json", type=Path, default=POLICY_DIR / "mutation_policy.json")
    parser.add_argument("--publish-policy-json", type=Path, default=POLICY_DIR / "publish_policy.json")
    parser.add_argument("--llm-repair-policy-json", type=Path, default=POLICY_DIR / "llm_repair_policy.json")
    parser.add_argument("--versioning-policy-json", type=Path, default=POLICY_DIR / "versioning_policy.json")
    parser.add_argument("--skip-validation-gate", action="store_true")
    args = parser.parse_args()
    candidate_policy = read_policy(args.candidate_policy_json)
    repair_policy = read_policy(args.repair_policy_json)
    quality_policy = read_policy(args.quality_policy_json)
    mutation_policy = read_policy(args.mutation_policy_json)
    publish_policy = read_policy(args.publish_policy_json)
    llm_repair_policy = read_policy(args.llm_repair_policy_json)
    versioning_policy = read_policy(args.versioning_policy_json)
    if args.status_json.exists():
        try:
            previous = json.loads(args.status_json.read_text(encoding="utf-8"))
        except Exception:
            previous = {}
        if previous.get("state") == "running" and not pid_is_alive(previous.get("pid")):
            candidate_hint = previous.get("candidate_csv") or str(args.discovered_out)
            state, phase, message = reconcile_stale_status(previous, Path(candidate_hint))
            previous["state"] = state
            previous["phase"] = phase
            previous["last_output_line"] = message
            previous["updated_at"] = now_iso()
            write_status(args.status_json, previous)

    payload = {
        "workflow": "nangate45-graph-expander",
        "state": "running",
        "pid": os.getpid(),
        "round_index": args.round_index,
        "phase": "init",
        "repo_manifest_csv": str(args.repo_manifest_csv) if args.repo_manifest_csv else "",
        "candidate_csv": str(args.candidate_csv) if args.candidate_csv else "",
        "priorities": args.priorities,
        "active_command": "",
        "last_output_line": "",
        "last_return_code": 0,
        "updated_at": now_iso(),
    }
    global_before = snapshot_global_targets(tracked_global_targets())
    run_manifest = {
        "workflow": "nangate45-graph-expander",
        "started_at": now_iso(),
        "status_json": str(args.status_json),
        "status_log": str(args.status_log),
        "workspace_root": str(WORKSPACE_ROOT),
        "orfs_seed_root": str(ORFS_SEED_ROOT),
        "out_root": str(OUT_ROOT),
        "round_index": args.round_index,
        "policies": {
            "candidate_policy_json": str(args.candidate_policy_json),
            "repair_policy_json": str(args.repair_policy_json),
            "quality_policy_json": str(args.quality_policy_json),
            "mutation_policy_json": str(args.mutation_policy_json),
            "publish_policy_json": str(args.publish_policy_json),
            "llm_repair_policy_json": str(args.llm_repair_policy_json),
            "versioning_policy_json": str(args.versioning_policy_json),
            "candidate_policy": candidate_policy,
            "repair_policy": repair_policy,
            "quality_policy": quality_policy,
            "mutation_policy": mutation_policy,
            "publish_policy": publish_policy,
            "llm_repair_policy": llm_repair_policy,
            "versioning_policy": versioning_policy,
        },
        "commands": [],
        "global_side_effects": {},
        "publish_gate": {"eligible_for_publish": False, "validation_pass": None},
        "dataset_snapshot": {},
    }
    write_status(args.status_json, payload)

    candidate_csv = args.candidate_csv
    publish_blocked = False
    try:
        if args.delete_rejected and not mutation_policy.get("allow_delete_rejected", False):
            raise SystemExit("delete_rejected is blocked by mutation_policy.json")

        if args.clone_missing and args.repo_manifest_csv is None:
            payload["state"] = "failed"
            payload["phase"] = "error"
            payload["last_output_line"] = "--clone-missing requires --repo-manifest-csv"
            payload["updated_at"] = now_iso()
            write_status(args.status_json, payload)
            raise SystemExit("--clone-missing requires --repo-manifest-csv")

        if args.clone_missing and args.repo_manifest_csv is not None:
            payload["phase"] = "clone_repos"
            run(
                [
                    PYTHON_BIN,
                    str(CLONE_REPO_SCRIPT),
                    "--repo-manifest-csv",
                    str(args.repo_manifest_csv),
                    "--downloads-root",
                    str(args.downloads_root),
                ],
                status_path=args.status_json,
                log_path=args.status_log,
                payload=payload,
            )
        if args.discover:
            payload["phase"] = "discover"
            run(
                [
                    PYTHON_BIN,
                    str(DISCOVER_SCRIPT),
                    "--downloads-root",
                    str(args.downloads_root),
                    "--out-csv",
                    str(args.discovered_out),
                    *(["--repo-manifest-csv", str(args.repo_manifest_csv)] if args.repo_manifest_csv else []),
                ],
                status_path=args.status_json,
                log_path=args.status_log,
                payload=payload,
            )
            candidate_csv = args.discovered_out
            payload["candidate_csv"] = str(candidate_csv)
            payload["updated_at"] = now_iso()
            write_status(args.status_json, payload)

        if candidate_csv is None and not args.skip_batch:
            payload["state"] = "failed"
            payload["phase"] = "error"
            payload["last_output_line"] = "need --candidate-csv or --discover unless --skip-batch is set"
            payload["updated_at"] = now_iso()
            write_status(args.status_json, payload)
            raise SystemExit("need --candidate-csv or --discover unless --skip-batch is set")

        if not args.skip_batch:
            if candidate_csv and candidate_csv.exists() and not args.allow_high_mem:
                try:
                    rows = list(csv.DictReader(candidate_csv.open(newline="", encoding="utf-8")))
                except Exception:
                    rows = []
                if any((row.get("resource_tier") or "").strip().lower() == "high" for row in rows):
                    payload["state"] = "blocked_high_mem"
                    payload["phase"] = "resource_guard"
                    payload["last_output_line"] = "resource_tier=high candidates detected; rerun with --allow-high-mem on a high-memory node"
                    payload["updated_at"] = now_iso()
                    write_status(args.status_json, payload)
                    raise SystemExit(payload["last_output_line"])
            payload["phase"] = "batch_expand"
            batch_cmd = [
                PYTHON_BIN,
                str(EXPAND_SCRIPT),
                "--candidate-csv",
                str(candidate_csv),
                "--out-root",
                str(OUT_ROOT),
                "--priorities",
                *args.priorities,
            ]
            run(
                batch_cmd,
                status_path=args.status_json,
                log_path=args.status_log,
                payload=payload,
            )
            run_manifest["commands"].append({"phase": payload["phase"], "cmd": batch_cmd})

        if not args.skip_classify and repair_policy.get("enable_failure_classification", True):
            payload["phase"] = "classify_failures"
            run([PYTHON_BIN, str(CLASSIFY_SCRIPT)], status_path=args.status_json, log_path=args.status_log, payload=payload)
            if repair_policy.get("enable_auto_fix", True):
                payload["phase"] = "auto_fix_failures"
                run([PYTHON_BIN, str(AUTO_FIX_SCRIPT)], status_path=args.status_json, log_path=args.status_log, payload=payload)
            if repair_policy.get("enable_failure_kb_candidate_refresh", True):
                payload["phase"] = "failure_kb_candidates"
                run([PYTHON_BIN, str(FAILURE_KB_CANDIDATES_SCRIPT)], status_path=args.status_json, log_path=args.status_log, payload=payload)
            if mutation_policy.get("allow_failure_kb_refresh", True) and repair_policy.get("enable_failure_kb_refresh", True):
                payload["phase"] = "refresh_failure_knowledge_base"
                run([PYTHON_BIN, str(REFRESH_FAILURE_KB_SCRIPT)], status_path=args.status_json, log_path=args.status_log, payload=payload)

        auto_fix_retry_csv: Path | None = None
        if AUTO_FIX_PLAN.exists():
            try:
                plan = json.loads(AUTO_FIX_PLAN.read_text(encoding="utf-8"))
            except Exception:
                plan = {}
            plan_retry = (plan.get("retry_candidates_csv") or "").strip()
            if plan_retry:
                auto_fix_retry_csv = Path(plan_retry)
        retry_csv: Path | None = None
        if args.run_retry:
            retry_csv = select_retry_candidates(
                candidate_csv=candidate_csv,
                retry_candidates_csv=RETRY_CANDIDATES,
                auto_fix_retry_csv=auto_fix_retry_csv,
                scoped_out_csv=SCOPED_RETRY_CANDIDATES,
                retry_scope=str(repair_policy.get("retry_scope_for_curated_wave", "scoped")),
            )

        if args.run_retry and repair_policy.get("enable_retry_wave", True) and retry_csv is not None and has_rows(retry_csv):
            payload["phase"] = "retry_expand"
            run(
                [
                    PYTHON_BIN,
                    str(EXPAND_SCRIPT),
                    "--candidate-csv",
                    str(retry_csv),
                    "--out-root",
                    str(OUT_ROOT),
                    "--priorities",
                    "high",
                    "medium",
                    "low",
                ],
                status_path=args.status_json,
                log_path=args.status_log,
                payload=payload,
            )
            if not args.skip_classify:
                payload["phase"] = "reclassify_failures"
                run([PYTHON_BIN, str(CLASSIFY_SCRIPT)], status_path=args.status_json, log_path=args.status_log, payload=payload)
                if repair_policy.get("enable_failure_kb_candidate_refresh", True):
                    payload["phase"] = "refresh_failure_kb_candidates"
                    run([PYTHON_BIN, str(FAILURE_KB_CANDIDATES_SCRIPT)], status_path=args.status_json, log_path=args.status_log, payload=payload)
                if mutation_policy.get("allow_failure_kb_refresh", True) and repair_policy.get("enable_failure_kb_refresh", True):
                    payload["phase"] = "refresh_failure_knowledge_base"
                    run([PYTHON_BIN, str(REFRESH_FAILURE_KB_SCRIPT)], status_path=args.status_json, log_path=args.status_log, payload=payload)

        # Project frontend failure classes into each failed candidate's
        # reports/diagnosis.json and re-ingest, so knowledge.sqlite
        # failure_events carry the synth-frontend class (not just
        # orfs-fail-synth). Journal->knowledge promoter contract: the JSON
        # casebook stays the hypothesis side; distilled classes land in tables.
        if not args.skip_classify:
            payload["phase"] = "project_frontend_diagnosis"
            run([PYTHON_BIN, str(PROJECT_DIAGNOSIS_SCRIPT)], status_path=args.status_json, log_path=args.status_log, payload=payload)

        if not args.skip_rebuild_index:
            payload["phase"] = "rebuild_external_index"
            run([PYTHON_BIN, str(REBUILD_INDEX_SCRIPT)], status_path=args.status_json, log_path=args.status_log, payload=payload)

        if not args.skip_duplicates and quality_policy.get("enable_duplicate_audit", True):
            payload["phase"] = "deduplicate_netlists"
            run([PYTHON_BIN, str(DEDUP_SCRIPT)], status_path=args.status_json, log_path=args.status_log, payload=payload)

        if not args.skip_summary and quality_policy.get("enable_external_summary", True):
            payload["phase"] = "summarize"
            run([PYTHON_BIN, str(SUMMARIZE_SCRIPT)], status_path=args.status_json, log_path=args.status_log, payload=payload)

        if not args.skip_scale_report and quality_policy.get("enable_dataset_scale_report", True):
            # The scale report loads .pt graphs -> needs the torch venv, same
            # SKIP-with-HINT semantics as def-graph's graph stage.
            gpython = graph_python()
            if gpython:
                payload["phase"] = "dataset_scale_report"
                run([gpython, str(SCALE_SCRIPT)], status_path=args.status_json, log_path=args.status_log, payload=payload)
            else:
                print("HINT: R2G_GRAPH_PYTHON unset — skipping dataset_scale_report (needs torch).", flush=True)

        if not args.skip_design_score and quality_policy.get("enable_design_quality_scoring", True):
            payload["phase"] = "design_quality_report"
            run([PYTHON_BIN, str(DESIGN_SCORE_SCRIPT)], status_path=args.status_json, log_path=args.status_log, payload=payload)

        payload["phase"] = "build_publish_candidates"
        run(
            [PYTHON_BIN, str(BUILD_PUBLISH_CANDIDATES_SCRIPT), "--publish-policy-json", str(args.publish_policy_json)],
            status_path=args.status_json,
            log_path=args.status_log,
            payload=payload,
        )

        if not args.skip_quality_report and quality_policy.get("enable_repo_quality_scoring", True):
            payload["phase"] = "quality_report"
            score_cmd = [PYTHON_BIN, str(SCORE_SCRIPT)]
            if candidate_policy.get("discover_defaults", {}).get("reject_if_all_small", False):
                score_cmd.append("--reject-if-all-small")
            run(score_cmd, status_path=args.status_json, log_path=args.status_log, payload=payload)
            if args.cleanup_rejected and mutation_policy.get("allow_cleanup_rejected", True):
                payload["phase"] = "cleanup_rejected_repos"
                cleanup_cmd = [PYTHON_BIN, str(CLEANUP_SCRIPT)]
                if args.delete_rejected:
                    cleanup_cmd.append("--delete")
                run(cleanup_cmd, status_path=args.status_json, log_path=args.status_log, payload=payload)

        if not args.skip_validation_gate and publish_policy.get("enable_validation_gate", True):
            payload["phase"] = "validation_gate"
            run(
                [PYTHON_BIN, str(VALIDATE_PUBLISH_SCRIPT), "--publish-policy-json", str(args.publish_policy_json)],
                status_path=args.status_json,
                log_path=args.status_log,
                payload=payload,
            )
            validation = load_json(WORKSPACE_ROOT / "quality" / "publish_validation.json")
            validation_pass = bool(validation.get("pass", False))
            run_manifest["publish_gate"]["validation_pass"] = validation_pass
            if publish_policy.get("require_validation_pass_for_manifest_refresh", True) and not validation_pass:
                publish_blocked = True
                payload["state"] = "blocked_validation"
                payload["phase"] = "validation_gate"
                payload["last_output_line"] = "publish validation failed; merged manifest refresh skipped"
                payload["updated_at"] = now_iso()
                write_status(args.status_json, payload)
                run_manifest["publish_gate"]["eligible_for_publish"] = False
            elif not publish_blocked:
                run_manifest["publish_gate"]["eligible_for_publish"] = True
        else:
            run_manifest["publish_gate"]["validation_pass"] = None
            run_manifest["publish_gate"]["eligible_for_publish"] = not publish_blocked

        if not args.skip_refresh and run_manifest["publish_gate"]["eligible_for_publish"]:
            payload["phase"] = "refresh_manifest"
            run(
                [PYTHON_BIN, str(REFRESH_SCRIPT), "--use-publish-eligible", "--publish-eligible-csv", str(WORKSPACE_ROOT / "manifests" / "publish_eligible_designs.csv")],
                status_path=args.status_json,
                log_path=args.status_log,
                payload=payload,
            )

        payload["phase"] = "failure_casebook"
        run([PYTHON_BIN, str(FAILURE_CASEBOOK_SCRIPT)], status_path=args.status_json, log_path=args.status_log, payload=payload)

        payload["phase"] = "failure_diagnosis"
        run([PYTHON_BIN, str(FAILURE_DIAGNOSIS_SCRIPT)], status_path=args.status_json, log_path=args.status_log, payload=payload)

        if repair_policy.get("enable_llm_long_tail_queue", True):
            payload["phase"] = "build_llm_repair_cases"
            run(
                [PYTHON_BIN, str(LLM_REPAIR_CASES_SCRIPT), "--policy-json", str(args.llm_repair_policy_json)],
                status_path=args.status_json,
                log_path=args.status_log,
                payload=payload,
            )

        if mutation_policy.get("allow_failure_strategy_score_update", True):
            payload["phase"] = "update_failure_strategy"
            run([PYTHON_BIN, str(UPDATE_STRATEGY_SCRIPT)], status_path=args.status_json, log_path=args.status_log, payload=payload)
        if mutation_policy.get("allow_signature_action_apply", True):
            payload["phase"] = "auto_signature_actions"
            run([PYTHON_BIN, str(AUTO_SIGNATURE_ACTIONS), "--apply"], status_path=args.status_json, log_path=args.status_log, payload=payload)
        if mutation_policy.get("record_dataset_snapshot", True) and versioning_policy.get("record_dataset_snapshot", True):
            payload["phase"] = "record_dataset_snapshot"
            run(
                [PYTHON_BIN, str(DATASET_SNAPSHOT_SCRIPT), "--policy-json", str(args.versioning_policy_json)],
                status_path=args.status_json,
                log_path=args.status_log,
                payload=payload,
            )
            run_manifest["dataset_snapshot"] = {
                "json": str(WORKSPACE_ROOT / "runs" / "dataset_snapshot_latest.json"),
                "md": str(WORKSPACE_ROOT / "runs" / "dataset_snapshot_latest.md"),
            }
        payload["phase"] = "refresh_design_stage_index"
        refresh_design_stage_index(OUT_ROOT)

        payload["state"] = "blocked_validation" if publish_blocked else "completed"
        payload["phase"] = "done"
        payload["active_command"] = ""
        payload["updated_at"] = now_iso()
        write_status(args.status_json, payload)
    except Exception as exc:
        payload["state"] = "failed"
        payload["phase"] = "error"
        payload["active_command"] = ""
        payload["last_output_line"] = f"{type(exc).__name__}: {exc}"
        payload["updated_at"] = now_iso()
        write_status(args.status_json, payload)
        run_manifest["state"] = "failed"
        run_manifest["error"] = f"{type(exc).__name__}: {exc}"
        run_manifest["ended_at"] = now_iso()
        run_manifest["commands"] = COMMAND_HISTORY[:]
        run_manifest["global_side_effects"] = changed_global_targets(global_before, snapshot_global_targets(tracked_global_targets()))
        write_run_manifest(WORKSPACE_ROOT / "runs", RUN_MANIFEST_LATEST, run_manifest)
        raise
    run_manifest["state"] = payload["state"]
    run_manifest["ended_at"] = now_iso()
    run_manifest["commands"] = COMMAND_HISTORY[:]
    run_manifest["global_side_effects"] = changed_global_targets(global_before, snapshot_global_targets(tracked_global_targets()))
    write_run_manifest(WORKSPACE_ROOT / "runs", RUN_MANIFEST_LATEST, run_manifest)


if __name__ == "__main__":
    main()
