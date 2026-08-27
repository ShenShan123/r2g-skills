#!/usr/bin/env python3
"""Re-run infrastructure-excluded ORFS projects without touching TEHM memory.

This lane consumes a frozen project target manifest, records process/stage
receipts, and classifies timeout/infrastructure/tool failures.  It deliberately
does not call capture, crystallization, graph attachment, or lifecycle code.
"""
from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

MEMORY_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MEMORY_ROOT.parent
sys.path.insert(0, str(MEMORY_ROOT))

from run_orfs_diversity_campaign import (  # noqa: E402
    SUPERVISOR_GRACE_S,
    _classify_attempt,
    _has_run,
    _load,
    _reusable_success,
    _resume_stage,
    _run_bounded,
    _sha,
    _stage_checkpoint,
    _write,
)
from orfs_storage import default_work_root, enforce_work_root  # noqa: E402

VERSION = "orfs-recovery-v0.1"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--targets", type=Path,
                    default=MEMORY_ROOT / "evaluation/orfs_infra_recovery_v1.json")
    ap.add_argument("--root", type=Path,
                    default=default_work_root("orfs-infra-recovery-v1"))
    ap.add_argument("--orfs-root", type=Path,
                    default=Path(os.environ.get("ORFS_ROOT", "/opt/EDA4AI/OpenROAD-flow-scripts")))
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--cpus-per-run", type=int, default=2)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--supervisor-grace", type=int, default=SUPERVISOR_GRACE_S)
    ap.add_argument("--report-only", action="store_true",
                    help="aggregate existing recovery/campaign receipts without running flows")
    ap.add_argument("--task", action="append", default=None,
                    help="restrict to one or more task_id values")
    args = ap.parse_args(argv)
    target_doc = _load(args.targets)
    targets = target_doc.get("tasks") or []
    selected = set(args.task or [])
    if selected:
        targets = [row for row in targets if row.get("task_id") in selected]
    if not targets:
        raise SystemExit("no recovery targets selected")
    projects = []
    for task in targets:
        for item in task.get("projects", []):
            project = Path(item["path"]).resolve()
            if not (project / "constraints" / "config.mk").is_file():
                raise SystemExit(f"project config missing: {project}")
            projects.append({**item, "task_id": task["task_id"],
                             "project": str(project),
                             "platform": item.get("platform") or _platform(project)})
    root = enforce_work_root(args.root)
    root.mkdir(parents=True, exist_ok=True)
    state_path = root / "recovery_state.json"
    state = _load(state_path) or {"version": VERSION, "runs": {}, "attempts": {}}
    state.setdefault("version", VERSION)
    state.setdefault("runs", {})
    state.setdefault("attempts", {})
    if args.report_only:
        for item in projects:
            project = Path(item["project"])
            receipt = _load(project / "recovery-run-receipt.json")
            if not receipt:
                receipt = _load(project / "campaign-run-receipt.json")
            if receipt:
                state["runs"][str(project)] = receipt
        report = _report(targets, state)
        _write(root / "recovery_report_all.json", report)
        print(f"[recovery] report-only wrote {root / 'recovery_report_all.json'}")
        return 0

    def one(item):
        project = Path(item["project"])
        old = state["runs"].get(str(project), {})
        digest = _sha(project / "constraints" / "config.mk")
        if _reusable_success(old, digest, project):
            return {**old, "task_id": item["task_id"], "project": str(project),
                    "reused": True}
        checkpoint = _stage_checkpoint(project)
        resume_from = _resume_stage(checkpoint, project=project)
        env = dict(os.environ, ORFS_ROOT=str(args.orfs_root.resolve()),
                   ORFS_TIMEOUT=str(args.timeout), ORFS_MAX_CPUS=str(max(1, args.cpus_per_run)))
        if resume_from:
            env.update(FROM_STAGE=resume_from, R2G_RESUME_NO_CLEAN="1")
        log = project / "recovery_flow.log"
        cmd = ["bash", str(REPO_ROOT / "r2g-skills/signoff-loop/scripts/flow/run_orfs.sh"),
               str(project), item["platform"], project.name]
        rc, supervisor_timeout = _run_bounded(
            cmd, log, env=env, timeout=max(1, args.timeout),
            grace=max(1, args.supervisor_grace))
        checkpoint = _stage_checkpoint(project)
        completed = rc == 0 and _has_run(project)
        failure_class, failure_domain = _classify_attempt(
            rc, supervisor_timeout, completed, checkpoint, log)
        result = {
            "version": VERSION, "task_id": item["task_id"],
            "role": item.get("role", "subject"), "project": str(project),
            "platform": item["platform"], "config_sha256": digest,
            "flow_rc": rc, "completed": completed, "resumable": completed,
            "supervisor_timeout": supervisor_timeout,
            "resume_from": resume_from, "failure_class": failure_class,
            "failure_domain": failure_domain, "last_checkpoint": checkpoint,
            "timeout_s": args.timeout, "supervisor_grace_s": args.supervisor_grace,
            "log": str(log), "log_sha256": _sha(log),
        }
        receipt = {**result, "command": cmd,
                   "receipt_path": str((project / "recovery-run-receipt.json").resolve())}
        _write(project / "recovery-run-receipt.json", receipt)
        with lock:
            state["runs"][str(project)] = result
            state["attempts"].setdefault(str(project), []).append(result)
            _write(state_path, state)
        return result

    import threading
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(one, item) for item in projects]
        for future in as_completed(futures):
            result = future.result()
            print(f"[recovery] {Path(result['project']).name}: "
                  f"rc={result.get('flow_rc')} class={result.get('failure_class')}",
                  flush=True)
    _write(root / "recovery_report.json", _report(targets, state))
    return 0


def _platform(project: Path) -> str:
    for line in (project / "constraints" / "config.mk").read_text().splitlines():
        if line.strip().startswith("export PLATFORM"):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"PLATFORM missing from {project}/constraints/config.mk")


def _report(targets: list[dict], state: dict) -> dict:
    runs = state.get("runs") or {}
    task_rows = []
    for task in targets:
        rows = [runs.get(str(Path(item["path"]).resolve()), {})
                for item in task.get("projects", [])]
        classes = sorted({row.get("failure_class") for row in rows if row.get("failure_class")})
        task_rows.append({"task_id": task["task_id"],
                          "projects": rows,
                          "all_projects_completed": bool(rows) and all(
                              row.get("completed") is True for row in rows),
                          "flow_complete": bool(rows) and all(
                              row.get("completed") is True for row in rows),
                          "failure_classes": classes,
                          # A process receipt is not an A/B trial receipt.
                          "evaluable": False})
    counts = {}
    for row in runs.values():
        key = row.get("failure_class") or ("SUCCESS" if row.get("completed") else "UNKNOWN")
        counts[key] = counts.get(key, 0) + 1
    return {"version": VERSION, "tasks": task_rows,
            "latest_class_counts": dict(sorted(counts.items())),
            "tehm_mutation": False}


if __name__ == "__main__":
    raise SystemExit(main())
