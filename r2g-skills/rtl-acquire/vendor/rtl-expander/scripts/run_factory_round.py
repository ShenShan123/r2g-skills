#!/usr/bin/env python3
"""Thin orchestration for discovery, acquisition, processing, and storage publication."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from frontier import canonical_repository_identity
from phase2_round_delta import atomic as atomic_json, capture as capture_round, finalize as finalize_round
from corpus_state import digest_file, digest_tree
from process_runner import run_streamed


RUN_LOG_DIR: Path | None = None
RUN_HEARTBEAT_PATH: Path | None = None
RUN_ATTEMPT_ID: str | None = None


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def materialize_acquired_intake(corpus_root: Path) -> tuple[Path, int]:
    intake = corpus_root / "state" / "acquired_intake"
    intake.mkdir(parents=True, exist_ok=True)
    # This directory is a disposable queue view, never an archive.  Removing only
    # its symlinks prevents already-published revisions from being reconsidered.
    for entry in intake.iterdir():
        if entry.is_symlink():
            entry.unlink()
    frontier = corpus_root / "state" / "frontier.sqlite"
    if not frontier.exists():
        return intake, 0
    published: set[tuple[str, str]] = set()
    for row in load_jsonl(corpus_root / "manifests" / "repositories.jsonl"):
        remote = str(row.get("repository_url") or "")
        commit = str(row.get("commit_sha") or "")
        if not remote or remote == "UNKNOWN" or not commit or commit == "UNKNOWN":
            continue
        try:
            published.add((canonical_repository_identity(remote)["repository_key"], commit.lower()))
        except ValueError:
            continue
    connection = sqlite3.connect(frontier)
    rows = connection.execute(
        """SELECT r.repository_key,r.provider,r.namespace,r.repo_name,rr.commit_sha,rr.source_path
           FROM repository_revisions rr JOIN repositories r USING(repository_key)
           ORDER BY r.repository_key,rr.commit_sha"""
    ).fetchall()
    connection.close()
    count = 0
    for repository_key, provider, namespace, repo_name, commit_sha, source_path in rows:
        if (repository_key, commit_sha.lower()) in published:
            continue
        name = "__".join((provider, namespace.replace("/", "-"), repo_name, commit_sha[:16]))
        link = intake / name
        target = Path(source_path)
        if link.is_symlink() and link.resolve() == target.resolve():
            count += 1
            continue
        if link.exists() or link.is_symlink():
            continue
        os.symlink(target, link, target_is_directory=True)
        count += 1
    return intake, count


def run_stage(name: str, command: list[str], dry_run: bool, blocking: bool = True) -> dict[str, Any]:
    if dry_run:
        return {"stage": name, "state": "DRY_RUN", "command": command, "blocking": blocking}
    def heartbeat(progress: dict[str, Any]) -> None:
        if RUN_HEARTBEAT_PATH is not None:
            atomic_json(RUN_HEARTBEAT_PATH, {
                "schema": "rtl_factory_heartbeat_v1", "stage": name,
                "command": command, "finalization_attempt_id": RUN_ATTEMPT_ID, **progress,
            })
    result = run_streamed(
        command, RUN_LOG_DIR or Path.cwd() / "logs", name, heartbeat=heartbeat,
    )
    return {
        "stage": name, "state": "PASS" if result["returncode"] == 0 else "FAIL",
        "returncode": result["returncode"], "command": command,
        "stdout_tail": result["stdout_tail"][-8000:], "stderr_tail": result["stderr_tail"][-8000:],
        "stdout_log": result["stdout_log"], "stderr_log": result["stderr_log"],
        "started_at": result["started_at"], "completed_at": result["completed_at"],
        "last_output_at": result["last_output_at"], "blocking": blocking,
    }


TRANSIENT_FINALIZATION_MARKERS = (
    "database is locked", "database is busy", "disk i/o error",
    "input/output error", "resource temporarily unavailable",
    "interrupted system call", "timeout acquiring manifest lock",
)


def is_transient_finalization_failure(result: dict[str, Any]) -> bool:
    if result.get("state") != "FAIL":
        return False
    detail = "\n".join((
        str(result.get("stdout_tail") or ""),
        str(result.get("stderr_tail") or ""),
        str(result.get("detail") or ""),
    )).lower()
    return any(marker in detail for marker in TRANSIENT_FINALIZATION_MARKERS)


def run_finalization_stage(
    name: str, command: list[str], dry_run: bool, *, max_attempts: int = 3,
    base_backoff_seconds: float = 2.0,
) -> dict[str, Any]:
    """Retry only recognized transient storage failures in the same attempt."""
    subattempts: list[dict[str, Any]] = []
    for number in range(1, max_attempts + 1):
        stage_name = name if number == 1 else f"{name}_retry_{number:02d}"
        try:
            result = run_stage(stage_name, command, dry_run, blocking=True)
        except OSError as exc:
            result = {
                "stage": stage_name, "state": "FAIL", "blocking": True,
                "command": command, "detail": f"{type(exc).__name__}: {exc}",
            }
        subattempts.append({
            "number": number, "state": result.get("state"),
            "returncode": result.get("returncode"),
            "detail": result.get("detail"),
            "stderr_tail": str(result.get("stderr_tail") or "")[-2000:],
        })
        if result.get("state") in {"PASS", "DRY_RUN"}:
            result["stage"] = name
            result["transient_subattempts"] = subattempts
            return result
        if not is_transient_finalization_failure(result) or number == max_attempts:
            result["stage"] = name
            result["transient_subattempts"] = subattempts
            return result
        time.sleep(base_backoff_seconds * (2 ** (number - 1)))
    raise AssertionError("unreachable finalization retry state")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def recorded_view_digest(path: Path) -> str:
    receipt_path = path.with_name(path.name + ".admission.json")
    if not receipt_path.is_file():
        return "MISSING_ADMISSION_DIGEST"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    digest = str(receipt.get("sha256") or "")
    if (
        receipt.get("schema") != "rtl_materialized_view_admission_v1"
        or receipt.get("object_id") != path.name
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest.lower())
        or receipt.get("rehash_required") is not False
    ):
        return "INVALID_ADMISSION_DIGEST"
    return digest.lower()


def start_attempt(corpus: Path, round_id: str, contract: str) -> tuple[str, Path]:
    root = corpus / "runs/factory/attempts" / round_id
    root.mkdir(parents=True, exist_ok=True)
    sequence = max(
        (int(path.stem.removeprefix("attempt_")) for path in root.glob("attempt_*.json")
         if path.stem.removeprefix("attempt_").isdigit()),
        default=0,
    ) + 1
    attempt_id = f"attempt_{sequence:04d}"
    path = root / f"{attempt_id}.json"
    atomic_json(path, {
        "schema": "rtl_factory_finalization_attempt_v1",
        "factory_round_id": round_id,
        "finalization_attempt_id": attempt_id,
        "finalization_contract": contract,
        "state": "RUNNING",
        "started_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
    })
    return attempt_id, path


def snapshot_id_for_attempt(corpus: Path, round_id: str) -> str:
    """Keep failed/certified snapshot attempts immutable during safe retries."""
    base = f"{round_id}-final"
    if not (corpus / "snapshots" / base).exists():
        return base
    if not RUN_ATTEMPT_ID:
        raise RuntimeError("snapshot retry requires a finalization attempt identity")
    return f"{base}-{RUN_ATTEMPT_ID}"


def write_id_file(path: Path, values: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text("".join(f"{value}\n" for value in sorted(set(values))), encoding="utf-8")
    os.replace(temporary, path)


def completion_identity(corpus: Path, round_id: str) -> dict[str, str]:
    round_dir = corpus / "quality/phase2/rounds" / round_id
    paths = {
        "start_sha256": round_dir / "start.json",
        "cohort_lock_sha256": round_dir / "cohort_lock.json",
        "final_delta_sha256": round_dir / "phase2_round_delta_summary.json",
    }
    if not all(path.is_file() for path in paths.values()):
        return {}
    return {
        "factory_round_id": round_id,
        "pipeline_schema": "rtl_factory_round_v3",
        **{key: sha256_file(path) for key, path in paths.items()},
        "skill_source_hash": digest_tree(Path(__file__).parents[1] / "scripts"),
        "benchmark_registry_hash": digest_tree(corpus / "benchmark_registry"),
        "quality_policy_hash": digest_tree(Path(__file__).parents[1] / "references"),
    }


def completion_is_final(
    summary: dict[str, Any], corpus: Path | None = None
) -> bool:
    final = summary.get("state") == "PASS" and any(
        stage.get("stage") == "phase2_round_delta"
        and stage.get("state") == "PASS"
        and stage.get("yield_status") == "FINAL"
        for stage in summary.get("stages", [])
    )
    if not final or not summary.get("completion_invariants", {}).get("valid"):
        return False
    if corpus is not None:
        expected = completion_identity(corpus, str(summary.get("factory_round_id") or ""))
        return bool(expected) and summary.get("completion_identity") == expected
    return True


def evaluate_completion_invariants(
    corpus: Path, round_id: str, delta: dict[str, Any] | None
) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    cohort = (delta or {}).get("acquisition_cohort", {})
    checks["cohort_lock_present"] = (corpus / "quality/phase2/rounds" / round_id / "cohort_lock.json").is_file()
    cohort_lock_path = corpus / "quality/phase2/rounds" / round_id / "cohort_lock.json"
    cohort_lock = json.loads(cohort_lock_path.read_text(encoding="utf-8")) if cohort_lock_path.is_file() else {}
    requested = int(cohort_lock.get("requested_revision_target", cohort_lock.get(
        "target_new_acquired_revisions", -1
    )))
    actual = int(cohort_lock.get("actual_cohort_size", cohort_lock.get(
        "acquired_revision_count", -1
    )))
    early_evidence = cohort_lock.get("early_close_evidence") or {}
    checks["cohort_target_contract_valid"] = (
        actual >= requested > 0
        or (
            cohort_lock.get("early_close") is True
            and cohort_lock.get("early_close_reason")
                == "ELIGIBLE_PRODUCTION_FRONTIER_EXHAUSTED"
            and 0 < actual < requested
            and early_evidence.get("eligible") is True
            and bool(early_evidence.get("checks"))
            and all(early_evidence["checks"].values())
        )
    )
    checks["final_delta"] = (delta or {}).get("yield_status") == "FINAL"
    checks["processing_coverage_100pct"] = cohort.get("processing_coverage") == 1.0
    checks["pending_processing_zero"] = cohort.get("pending_processing_revisions") == 0
    checks["terminal_set_matches_cohort"] = cohort.get("terminal_revision_set_matches_cohort") is True

    connection = sqlite3.connect(corpus / "state/frontier.sqlite")
    try:
        duplicate_revisions = int(connection.execute(
            "SELECT COUNT(*) FROM (SELECT repository_revision_key FROM repository_revisions GROUP BY repository_revision_key HAVING COUNT(*)>1)"
        ).fetchone()[0])
        cohort_repos = [str(value).rsplit("@", 1)[0] for value in (delta or {}).get("new_revision_keys", [])]
        active_claims = 0
        for offset in range(0, len(cohort_repos), 500):
            chunk = cohort_repos[offset:offset + 500]
            if chunk:
                active_claims += int(connection.execute(
                    f"SELECT COUNT(*) FROM repositories WHERE repository_key IN ({','.join('?' for _ in chunk)}) AND (claimed_by IS NOT NULL OR acquisition_status='ACQUIRING')",
                    chunk,
                ).fetchone()[0])
    finally:
        connection.close()
    checks["duplicate_repository_revision_zero"] = duplicate_revisions == 0
    checks["cohort_active_claims_zero"] = active_claims == 0

    scale_path = corpus / "quality/scale_pilot_summary.json"
    scale = json.loads(scale_path.read_text(encoding="utf-8")) if scale_path.is_file() else {}
    integrity = scale.get("integrity", {})
    for key in (
        "corrupt_manifest_rows", "duplicate_design_ids", "family_split_violations",
        "split_group_violations", "immutable_source_hash_mismatches",
        "immutable_source_rehash_required",
        "published_without_elaboration", "storage_layout_missing_design_json",
    ):
        checks[f"{key}_zero"] = integrity.get(key) == 0
    checks["publish_invariants_valid"] = integrity.get("publish_invariants", {}).get("valid") is True
    conservation_reports = []
    for group in scale.get("stage_conservation", {}).values():
        if isinstance(group, dict):
            conservation_reports.extend(
                value for value in group.values()
                if isinstance(value, dict) and "residual" in value
            )
    checks["funnel_conservation_zero_residual"] = bool(conservation_reports) and all(
        report.get("conserved") is True and report.get("residual") == 0
        for report in conservation_reports
    )
    checks["formal_missing_repository_revision_zero"] = integrity.get("formal_missing_repository_revision") == 0
    checks["legacy_unresolved_provenance_quarantined"] = (
        integrity.get("legacy_unresolved_provenance_designs", 0)
        == integrity.get("legacy_unresolved_provenance_quarantined", -1)
    )
    reconciliation_invariants = integrity.get("publish_invariants", {}).get(
        "split_reconciliation_invariants", {}
    )
    for key in (
        "cross_split_closure_components",
        "superseded_split_groups_without_lineage",
        "merged_component_member_loss",
        "merged_component_multi_split_assignment",
        "split_lineage_cycles",
        "nonunique_terminal_canonical_targets",
    ):
        checks[f"{key}_zero"] = reconciliation_invariants.get(key) == 0

    contamination_path = corpus / "quality/phase1_5/benchmark_contamination_audit.json"
    contamination = json.loads(contamination_path.read_text(encoding="utf-8")) if contamination_path.is_file() else {}
    design_count = len(load_jsonl(corpus / "manifests/all_designs.jsonl"))
    checks["contamination_audit_current"] = (
        contamination.get("apply") is True
        and contamination.get("registry_ready") is True
        and contamination.get("designs_checked") == design_count
        and contamination.get("benchmark_registry_hash") == digest_tree(corpus / "benchmark_registry")
    )
    checks["gold_manifests_regenerated"] = all(
        (corpus / "manifests" / name).is_file()
        for name in ("training_gold.jsonl", "training_gold_families.jsonl")
    )
    gold_meta_path = corpus / "manifests/training_gold.meta.json"
    gold_meta = json.loads(gold_meta_path.read_text(encoding="utf-8")) if gold_meta_path.is_file() else {}
    checks["gold_manifest_content_identity_current"] = (
        gold_meta.get("manifest_sha256") == recorded_view_digest(
            corpus / "manifests/training_gold.jsonl"
        )
        and gold_meta.get("provenance_complete_only") is True
    )
    failed = sorted(key for key, value in checks.items() if not value)
    return {"schema": "rtl_factory_completion_invariants_v1", "valid": not failed, "checks": checks, "failed": failed}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, default=Path.home() / "work" / "data" / "rtl_corpus")
    parser.add_argument("--local-source-root", type=Path, default=Path.home() / "work" / "_downloads")
    parser.add_argument("--providers", default="github,gitlab,codeberg,fusesoc")
    parser.add_argument("--discover-budget", type=int, default=5000)
    parser.add_argument("--acquire-budget", type=int, default=500)
    parser.add_argument("--process-budget", type=int, default=500)
    parser.add_argument("--process-all-acquired", action="store_true", help="Drain the complete unpublished acquired backlog so marginal acquisition yield can become final")
    parser.add_argument("--max-repo-seconds", type=int, default=900)
    parser.add_argument("--skip-discovery", action="store_true")
    parser.add_argument("--skip-acquisition", action="store_true")
    parser.add_argument("--skip-processing", action="store_true")
    parser.add_argument("--skip-local-processing", action="store_true", help="Process only unpublished acquired revisions")
    parser.add_argument("--skip-yield-recovery", action="store_true")
    parser.add_argument("--skip-quality-maintenance", action="store_true")
    parser.add_argument("--r1-budget", type=int, default=10)
    parser.add_argument("--ingest-local", action="store_true")
    parser.add_argument("--split-reconciliation-plan", type=Path)
    parser.add_argument(
        "--incremental-finalization", action="store_true",
        help="Consume exact terminal staging artifacts instead of reprocessing acquired revisions",
    )
    parser.add_argument("--cohort-lock", type=Path)
    parser.add_argument(
        "--incremental-shadow-compare", action="store_true",
        help="Compare indexed and compatibility-view semantics after incremental publication",
    )
    parser.add_argument(
        "--certification-retry", action="store_true",
        help="Retry only compatibility-view refresh and snapshot certification after a FINAL round",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--factory-round-id", help="Stable Phase-2 batch identifier; generated when omitted")
    return parser.parse_args()


def main() -> int:
    global RUN_LOG_DIR, RUN_HEARTBEAT_PATH, RUN_ATTEMPT_ID
    args = parse_args()
    scripts = Path(__file__).parent
    py = sys.executable
    round_id = args.factory_round_id or ("p2r_" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    RUN_LOG_DIR = args.corpus_root / "runs/factory/logs" / round_id
    RUN_HEARTBEAT_PATH = args.corpus_root / "runs/factory" / f"{round_id}.heartbeat.json"
    completed_path = args.corpus_root / "runs/factory" / f"{round_id}.json"
    if not args.dry_run and completed_path.is_file():
        completed = json.loads(completed_path.read_text(encoding="utf-8"))
        if completion_is_final(completed, args.corpus_root):
            completed["idempotent_cache_hit"] = True
            print(json.dumps(completed, indent=2, sort_keys=True))
            return 0
    finalization_contract = (
        "incremental_finalization_v1" if args.incremental_finalization
        else "legacy_full_materialization_v1"
    )
    attempt_path: Path | None = None
    if not args.dry_run:
        RUN_ATTEMPT_ID, attempt_path = start_attempt(
            args.corpus_root, round_id, finalization_contract,
        )
    start_path = args.corpus_root / "quality/phase2/rounds" / round_id / "start.json"
    round_start = None if args.dry_run else (json.loads(start_path.read_text(encoding="utf-8")) if start_path.is_file() else capture_round(args.corpus_root, round_id))
    stages: list[dict[str, Any]] = []
    if args.certification_retry:
        if args.dry_run:
            raise ValueError("--certification-retry does not support --dry-run")
        delta_path = args.corpus_root / "quality/phase2/rounds" / round_id / "phase2_round_delta_summary.json"
        if not completed_path.is_file() or not delta_path.is_file():
            raise ValueError("certification retry requires an existing factory summary and FINAL delta")
        prior = json.loads(completed_path.read_text(encoding="utf-8"))
        delta = json.loads(delta_path.read_text(encoding="utf-8"))
        if delta.get("yield_status") != "FINAL" or not prior.get("completion_invariants", {}).get("valid"):
            raise ValueError("certification retry cannot bypass a non-FINAL delta or failed completion invariants")
        failed_blocking = [
            stage.get("stage") for stage in prior.get("stages", [])
            if stage.get("blocking", True) and stage.get("state") != "PASS"
        ]
        if failed_blocking != ["corpus_snapshot"]:
            raise ValueError(f"certification retry requires corpus_snapshot-only failure, got {failed_blocking}")
        stages.append(run_stage("quality_contamination", [
            py, str(scripts / "audit_benchmark_contamination.py"),
            "--corpus-root", str(args.corpus_root), "--apply",
        ], False, blocking=True))
        if stages[-1]["state"] == "PASS":
            stages.append(run_stage("quality_scale_report", [
                py, str(scripts / "summarize_scale_pilot.py"),
                "--corpus-root", str(args.corpus_root),
                "--local-source-root", str(args.local_source_root),
            ], False, blocking=True))
        stages.append({
            "stage": "phase2_round_delta", "state": "PASS", "blocking": True,
            "yield_status": "FINAL", "reused": True, "path": str(delta_path),
        })
        stages.append({
            "stage": "scheduler_round_calibration", "state": "PASS", "blocking": True,
            "reused": True,
        })
        if all(not stage.get("blocking", True) or stage.get("state") == "PASS" for stage in stages):
            stages.append(run_stage("corpus_snapshot", [
                py, str(scripts / "build_corpus_snapshot.py"),
                "--corpus-root", str(args.corpus_root),
                "--snapshot-id", snapshot_id_for_attempt(args.corpus_root, round_id),
            ], False, blocking=True))
        stages.append(run_stage("quality_phase2_dashboard", [
            py, str(scripts / "summarize_phase2.py"), "--corpus-root", str(args.corpus_root),
        ], False, blocking=False))
        completion_invariants = evaluate_completion_invariants(args.corpus_root, round_id, delta)
        blocking_pass = all(
            not stage.get("blocking", True) or stage.get("state") == "PASS" for stage in stages
        ) and completion_invariants["valid"]
        summary = {
            "schema": "rtl_factory_round_v3", "factory_round_id": round_id,
            "state": "PASS" if blocking_pass else "FAIL", "stages": stages,
            "completion_invariants": completion_invariants,
            "finalization_contract": "certification_retry_v1",
            "finalization_attempt_id": RUN_ATTEMPT_ID,
            "completion_identity": completion_identity(args.corpus_root, round_id),
        }
        if attempt_path is not None:
            atomic_json(attempt_path, {
                "schema": "rtl_factory_finalization_attempt_v1",
                "factory_round_id": round_id,
                "finalization_attempt_id": RUN_ATTEMPT_ID,
                "finalization_contract": "certification_retry_v1",
                "state": summary["state"],
                "completed_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
                "summary": summary,
            })
        atomic_json(completed_path, summary)
        atomic_json(args.corpus_root / "runs/factory/latest.json", summary)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0 if summary["state"] == "PASS" else 1
    if not args.skip_discovery:
        stages.append(run_stage("discovery", [
            py, str(scripts / "discover_repositories.py"), "--corpus-root", str(args.corpus_root),
            "--source-root", str(args.local_source_root), "--providers", args.providers,
            "--budget", str(args.discover_budget), "--seed-local",
        ], args.dry_run))
    if not args.skip_acquisition:
        acquire = [py, str(scripts / "acquire_frontier.py"), "--corpus-root", str(args.corpus_root), "--max-repos", str(args.acquire_budget)]
        if args.ingest_local:
            acquire.extend(["--ingest-local-root", str(args.local_source_root)])
        stages.append(run_stage("acquisition", acquire, args.dry_run))
    if not args.dry_run and all(stage["state"] == "PASS" for stage in stages):
        stages.append(run_stage("frontier_export", [py, str(scripts / "export_frontier_snapshot.py"), "--corpus-root", str(args.corpus_root)], False))
    if not args.skip_processing:
        split_reconciliation_args = (
            ["--split-reconciliation-plan", str(args.split_reconciliation_plan)]
            if args.split_reconciliation_plan else []
        )
        if args.incremental_finalization:
            cohort_lock = args.cohort_lock or (
                args.corpus_root / "quality/phase2/rounds" / round_id / "cohort_lock.json"
            )
            change_set = args.corpus_root / "quality/phase2/rounds" / round_id / "round_change_set.json"
            finalization_plan = change_set.with_name("finalization_plan.json")
            preflight_command = [
                py, str(scripts / "finalize_staged_round.py"),
                "--corpus-root", str(args.corpus_root),
                "--round-id", round_id,
                "--cohort-lock", str(cohort_lock),
                "--preflight-only",
                "--finalization-plan-output", str(finalization_plan),
            ]
            if args.split_reconciliation_plan:
                preflight_command.extend(["--split-reconciliation-plan", str(args.split_reconciliation_plan)])
            stages.append(run_finalization_stage(
                "finalization_preflight", preflight_command, args.dry_run,
            ))
            if stages[-1]["state"] in {"PASS", "DRY_RUN"}:
                commit_command = [
                    py, str(scripts / "finalize_staged_round.py"),
                    "--corpus-root", str(args.corpus_root),
                    "--round-id", round_id,
                    "--cohort-lock", str(cohort_lock),
                    "--commit-plan", str(finalization_plan),
                    "--change-set-output", str(change_set),
                ]
                if args.split_reconciliation_plan:
                    commit_command.extend([
                        "--split-reconciliation-plan", str(args.split_reconciliation_plan),
                    ])
                stages.append(run_finalization_stage(
                    "incremental_staging_commit", commit_command, args.dry_run,
                ))
            if not args.dry_run and stages[-1]["state"] == "PASS" and change_set.is_file():
                change = json.loads(change_set.read_text(encoding="utf-8"))
                changed_ids = list(map(str, change.get("changed_design_ids", [])))
                changed_id_path = change_set.with_name("changed_design_ids.txt")
                write_id_file(changed_id_path, changed_ids)
                stages.append(run_stage("storage_incremental", [
                    py, str(scripts / "materialize_storage_layout.py"),
                    "--corpus-root", str(args.corpus_root),
                    "--design-id-file", str(changed_id_path),
                    "--trust-admission-hashes",
                ], False))
        elif not args.skip_local_processing:
            stages.append(run_stage("processing_local", [
                py, str(scripts / "run_expansion_round.py"), "--source-root", str(args.local_source_root),
                "--corpus-root", str(args.corpus_root), "--max-repos", str(args.process_budget),
                "--max-repo-seconds", str(args.max_repo_seconds), "--synthesize",
                *split_reconciliation_args,
            ], args.dry_run))
        processing_ready = not stages or all(stage["state"] in {"PASS", "DRY_RUN"} for stage in stages)
        if not args.incremental_finalization and not args.dry_run and processing_ready:
            acquired_intake, acquired_count = materialize_acquired_intake(args.corpus_root)
            acquired_budget = acquired_count if args.process_all_acquired else min(args.process_budget, acquired_count)
            stages.append(run_stage("processing_acquired", [
                py, str(scripts / "run_expansion_round.py"), "--source-root", str(acquired_intake),
                "--corpus-root", str(args.corpus_root), "--max-repos", str(acquired_budget),
                "--max-repo-seconds", str(args.max_repo_seconds), "--synthesize",
                *split_reconciliation_args,
            ], False))
        if not args.incremental_finalization and not args.dry_run and stages and stages[-1]["state"] == "PASS":
            stages.append(run_stage("storage", [py, str(scripts / "materialize_storage_layout.py"), "--corpus-root", str(args.corpus_root), "--archive-linked-legacy"], False))
    publication_allowed = all(
        not stage.get("blocking", True) or stage.get("state") in {"PASS", "DRY_RUN"}
        for stage in stages
    )
    if not args.skip_yield_recovery and publication_allowed:
        stages.append(run_stage("yield_recovery_r1", [py, str(scripts / "run_online_r1_recovery.py"), "--corpus-root", str(args.corpus_root), "--max-cases", str(args.r1_budget)], args.dry_run, blocking=False))
        if not args.dry_run and stages[-1]["state"] == "PASS":
            if args.incremental_finalization:
                recovery_summary_path = args.corpus_root / "quality/phase2/online_r1_recovery_summary.json"
                recovery_summary = json.loads(recovery_summary_path.read_text(encoding="utf-8")) if recovery_summary_path.is_file() else {}
                recovered_ids = list(map(str, recovery_summary.get("published_design_ids", [])))
                if recovered_ids:
                    recovered_path = args.corpus_root / "quality/phase2/rounds" / round_id / "r1_recovered_design_ids.txt"
                    write_id_file(recovered_path, recovered_ids)
                    stages.append(run_stage("storage_after_recovery_incremental", [
                        py, str(scripts / "materialize_storage_layout.py"),
                        "--corpus-root", str(args.corpus_root),
                        "--design-id-file", str(recovered_path),
                        "--trust-admission-hashes",
                    ], False, blocking=False))
                else:
                    stages.append({
                        "stage": "storage_after_recovery_incremental", "state": "SKIPPED",
                        "blocking": False, "reason": "NO_RECOVERED_DESIGNS",
                    })
            else:
                stages.append(run_stage("storage_after_recovery", [py, str(scripts / "materialize_storage_layout.py"), "--corpus-root", str(args.corpus_root)], False, blocking=False))
    elif not args.skip_yield_recovery:
        stages.append({"stage": "yield_recovery_r1", "state": "SKIPPED",
                       "blocking": False, "reason": "BLOCKING_STAGE_WRITE_BARRIER"})
    if not args.skip_quality_maintenance and publication_allowed:
        quality_commands = (
            ("quality_license", [py, str(scripts / "recover_license_evidence.py"), "--corpus-root", str(args.corpus_root), "--apply"], False),
            ("quality_ontology", [py, str(scripts / "apply_function_ontology.py"), "--corpus-root", str(args.corpus_root), "--apply"], False),
            ("quality_contamination", [py, str(scripts / "audit_benchmark_contamination.py"), "--corpus-root", str(args.corpus_root), "--apply"], True),
            ("quality_scale_report", [py, str(scripts / "summarize_scale_pilot.py"), "--corpus-root", str(args.corpus_root), "--local-source-root", str(args.local_source_root)], True),
        )
        for name, command, blocking in quality_commands:
            stages.append(run_stage(name, command, args.dry_run, blocking=blocking))
            if blocking and stages[-1]["state"] not in {"PASS", "DRY_RUN"}:
                publication_allowed = False
                break
        if args.incremental_finalization and args.incremental_shadow_compare and publication_allowed:
            stages.append(run_stage("incremental_shadow_compare", [
                py, str(scripts / "compare_incremental_state.py"),
                "--corpus-root", str(args.corpus_root),
                "--round-id", round_id,
            ], args.dry_run, blocking=True))
            if stages[-1]["state"] not in {"PASS", "DRY_RUN"}:
                publication_allowed = False
    elif not args.skip_quality_maintenance:
        stages.append({"stage": "quality_maintenance", "state": "SKIPPED",
                       "blocking": False, "reason": "BLOCKING_STAGE_WRITE_BARRIER"})
    delta = None
    if not args.dry_run and round_start is not None and publication_allowed:
        try:
            delta = finalize_round(args.corpus_root, round_start, stages)
            stages.append({"stage": "phase2_round_delta", "state": "PASS", "blocking": True, "yield_status": delta["yield_status"]})
            if delta["yield_status"] == "FINAL":
                report_path = args.corpus_root / "quality/phase2/rounds" / round_id / "phase2_round_delta_summary.json"
                stages.append(run_stage("scheduler_round_calibration", [py, str(scripts / "scheduler.py"), "--corpus-root", str(args.corpus_root), "--phase2-round-report", str(report_path)], False, blocking=True))
                if stages[-1]["state"] == "PASS":
                    stages.append(run_stage("corpus_snapshot", [
                        py, str(scripts / "build_corpus_snapshot.py"),
                        "--corpus-root", str(args.corpus_root),
                        "--snapshot-id", snapshot_id_for_attempt(args.corpus_root, round_id),
                    ], False, blocking=True))
        except Exception as exc:
            stages.append({"stage": "phase2_round_delta", "state": "FAIL", "blocking": True, "detail": f"{type(exc).__name__}: {exc}"})
    elif not args.dry_run and round_start is not None:
        stages.append({"stage": "phase2_round_delta", "state": "SKIPPED", "blocking": True,
                       "reason": "BLOCKING_STAGE_WRITE_BARRIER"})
    if not args.skip_quality_maintenance and publication_allowed:
        stages.append(run_stage("quality_phase2_dashboard", [py, str(scripts / "summarize_phase2.py"), "--corpus-root", str(args.corpus_root)], args.dry_run, blocking=False))
    elif not args.skip_quality_maintenance:
        stages.append({"stage": "quality_phase2_dashboard", "state": "SKIPPED",
                       "blocking": False, "reason": "BLOCKING_STAGE_WRITE_BARRIER"})
    completion_invariants = evaluate_completion_invariants(args.corpus_root, round_id, delta) if not args.dry_run else {"valid": True, "checks": {}}
    blocking_pass = all(not stage.get("blocking", True) or stage["state"] in {"PASS", "DRY_RUN"} for stage in stages) and completion_invariants["valid"]
    delta_stage = next((stage for stage in stages if stage.get("stage") == "phase2_round_delta"), None)
    if not blocking_pass:
        round_state = "FAIL"
    elif delta_stage and delta_stage.get("yield_status") != "FINAL":
        round_state = "INCOMPLETE"
    else:
        round_state = "PASS"
    summary = {
        "schema": "rtl_factory_round_v3", "factory_round_id": round_id,
        "state": round_state, "stages": stages,
        "completion_invariants": completion_invariants,
        "finalization_contract": finalization_contract,
        "finalization_attempt_id": RUN_ATTEMPT_ID,
    }
    if not args.dry_run:
        summary["completion_identity"] = completion_identity(args.corpus_root, round_id)
        if attempt_path is not None:
            atomic_json(attempt_path, {
                "schema": "rtl_factory_finalization_attempt_v1",
                "factory_round_id": round_id,
                "finalization_attempt_id": RUN_ATTEMPT_ID,
                "finalization_contract": finalization_contract,
                "state": round_state,
                "completed_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
                "summary": summary,
            })
        atomic_json(args.corpus_root / "runs/factory" / f"{round_id}.json", summary)
        atomic_json(args.corpus_root / "runs/factory/latest.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["state"] == "PASS" else (2 if summary["state"] == "INCOMPLETE" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
