#!/usr/bin/env python3
"""Build and execute the fixed Default-ORFS baseline for Experiment 1 outputs.

The input is the independently-qualified, immutable Experiment 1 evaluation
reports.  This driver never invokes a repair action or touches the R2G
knowledge database.  It materializes each unique (repo URL, commit, top)
source closure, then uses the existing strict-signoff probe at Sky130HD/100MHz.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any


REPO = Path(__file__).resolve().parents[1]
PROBE = REPO / "tools" / "run_repair_family_probe.py"
METHODS = (
    "deepseek-vanilla",
    "gemini-vanilla",
    "kimi-vanilla",
    "openai-vanilla",
    "qwen-vanilla",
)
CLOCK_CANDIDATES = (
    "clk", "clock", "i_clk", "i_clock", "clock_i", "clk_i", "wb_clk_i",
    "wb_clk", "clock_in", "core_clk", "CK",
)
COMPILE_SUFFIXES = {".v", ".sv"}
DEPENDENCY_SUFFIXES = {".vh", ".svh", ".mem", ".hex", ".dat"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"expected object in {path}")
    return value


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


def infer_clock_port(ports: list[str]) -> str | None:
    for candidate in CLOCK_CANDIDATES:
        if candidate in ports:
            return candidate
    for port in ports:
        lowered = port.lower()
        if "clk" in lowered or "clock" in lowered:
            return port
    return None


def source_root_from_staged(project: Path) -> Path:
    staged = sorted((project / "rtl_staged").glob("*"))
    if not staged:
        raise ValueError(f"missing staged RTL in {project}")
    target = staged[0].resolve()
    parts = target.parts
    try:
        index = parts.index("_source_cache")
    except ValueError as exc:
        raise ValueError(f"staged RTL does not point into evaluation source cache: {target}") from exc
    if index + 1 >= len(parts):
        raise ValueError(f"malformed source cache target: {target}")
    return Path(*parts[: index + 2])


def slug(value: str) -> str:
    compact = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()
    return compact[:48] or "candidate"


def load_plan(args: argparse.Namespace) -> list[dict[str, Any]]:
    reports = args.source_campaign / "reports"
    selected: dict[tuple[str, str, str], dict[str, Any]] = {}
    for method in METHODS:
        report = read_json(reports / f"{method}.batch1.evaluation.json")
        for row in report.get("results", []):
            if not row.get("publishable_qualified"):
                continue
            key = tuple(row["key"])
            if len(key) != 3:
                raise ValueError(f"invalid evaluation key for {method}: {key}")
            origin = {
                "method_id": method,
                "candidate_id": row["candidate_id"],
                "evaluation_project": str(
                    args.source_campaign / "evaluation" / f"{method}.batch1" / row["candidate_id"] / "project"
                ),
                "source_evidence": row.get("source_evidence") or {},
                "synthesis": row.get("synthesis") or {},
            }
            if key not in selected:
                selected[key] = {"key": list(key), "origins": [origin]}
            else:
                selected[key]["origins"].append(origin)

    tasks: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for key in sorted(selected):
        entry = selected[key]
        origin = entry["origins"][0]
        project = Path(origin["evaluation_project"])
        try:
            source = source_root_from_staged(project)
            evidence = origin["source_evidence"]
            records = evidence.get("files") or []
            rtl_files: list[str] = []
            dependency_files: list[str] = []
            for record in records:
                relative = Path(record["path"])
                candidate = source / relative
                if not candidate.is_file():
                    raise ValueError(f"missing frozen source file: {relative}")
                if candidate.stat().st_size != record.get("size") or sha256_file(candidate) != record.get("sha256"):
                    raise ValueError(f"frozen source digest mismatch: {relative}")
                suffix = relative.suffix.lower()
                if suffix in COMPILE_SUFFIXES:
                    rtl_files.append(relative.as_posix())
                elif suffix in DEPENDENCY_SUFFIXES:
                    dependency_files.append(relative.as_posix())
            if not rtl_files:
                raise ValueError("no explicit Verilog/SystemVerilog compilation inputs")
            clock_port = infer_clock_port(origin["synthesis"].get("input_ports") or [])
            if clock_port is None:
                raise ValueError("no clock-like input port")
            identity = json.dumps(key, separators=(",", ":"))
            digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
            tasks.append(
                {
                    "task_id": f"exp1_{digest}_{slug(key[2])}",
                    "key": list(key),
                    "repo_url": key[0],
                    "commit": key[1],
                    "top_module": key[2],
                    "clock_port": clock_port,
                    "source_root": str(source),
                    "rtl_files": sorted(rtl_files),
                    "dependency_files": sorted(dependency_files),
                    "source_closure_sha256": evidence.get("closure_sha256"),
                    "mapped_cells_precheck": origin["synthesis"].get("mapped_cells"),
                    "origins": entry["origins"],
                }
            )
        except (KeyError, OSError, ValueError) as exc:
            failures.append({"key": list(key), "origins": entry["origins"], "reason": str(exc)})

    plan = {
        "schema_version": "exp1-formal-baseline-plan-1.0",
        "created_at": now(),
        "source_campaign": str(args.source_campaign.resolve()),
        "methods": list(METHODS),
        "platform": "sky130hd",
        "frequency_mhz": 100.0,
        "repair_actions": "disabled",
        "strict_signoff": True,
        "tasks": tasks,
        "unresolved": failures,
    }
    write_json(args.campaign_root / "baseline_plan.json", plan)
    print(json.dumps({"tasks": len(tasks), "unresolved": len(failures)}, indent=2))
    return tasks


def load_tasks(root: Path) -> list[dict[str, Any]]:
    plan = read_json(root / "baseline_plan.json")
    tasks = plan.get("tasks")
    if not isinstance(tasks, list):
        raise ValueError("baseline plan has no tasks")
    return tasks


def materialize_task(root: Path, task: dict[str, Any], resume: bool) -> None:
    project = root / "projects" / task["task_id"] / "baseline"
    if project.exists():
        if resume:
            return
        raise ValueError(f"project already exists: {project}")
    command = [
        sys.executable, str(PROBE), "materialize",
        "--source", task["source_root"],
        "--source-repo-url", task["repo_url"],
        "--source-commit", task["commit"],
        "--project", str(project),
        "--family", "exp1_formal_default_orfs",
        "--task-id", task["task_id"],
        "--variant", "baseline",
        "--platform", "sky130hd",
        "--top-module", task["top_module"],
        "--clock-port", task["clock_port"],
        "--frequency-mhz", "100",
        "--set", "SYNTH_MEMORY_MAX_BITS=131072",
    ]
    for path in task["rtl_files"]:
        command.extend(["--rtl-file", path])
    for path in task["dependency_files"]:
        command.extend(["--dependency-file", path])
    subprocess.run(command, cwd=REPO, check=True)


def prepare(args: argparse.Namespace) -> None:
    tasks = load_tasks(args.campaign_root)
    failures = []
    for task in tasks:
        try:
            materialize_task(args.campaign_root, task, args.resume)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            failures.append({"task_id": task["task_id"], "reason": str(exc)})
    write_json(args.campaign_root / "prepare_results.json", {"completed_at": now(), "failures": failures})
    print(json.dumps({"prepared": len(tasks) - len(failures), "failures": len(failures)}, indent=2))
    if failures:
        raise SystemExit(2)


def run_worker(args: argparse.Namespace) -> None:
    tasks = load_tasks(args.campaign_root)
    selected = [task for index, task in enumerate(tasks) if index % args.workers == args.worker_index]
    worker_root = args.campaign_root / "workers" / f"worker_{args.worker_index + 1:02d}"
    worker_root.mkdir(parents=True, exist_ok=True)
    cpu_start = args.cpu_base + args.worker_index * args.cores
    cpu_set = f"{cpu_start}-{cpu_start + args.cores - 1}"
    records = []
    for ordinal, task in enumerate(selected, start=1):
        project = args.campaign_root / "projects" / task["task_id"] / "baseline"
        result = project / "repair_family_probe_result.json"
        if args.resume and result.is_file():
            records.append({"task_id": task["task_id"], "status": "resume_skip"})
            continue
        started = now()
        command = [
            sys.executable, str(PROBE), "execute",
            "--project", str(project),
            "--cores", str(args.cores),
            "--cpu-set", cpu_set,
            "--timeout-seconds", str(args.timeout_seconds),
        ]
        with (worker_root / f"{ordinal:03d}_{task['task_id']}.log").open("w", encoding="utf-8") as stream:
            completed = subprocess.run(command, cwd=REPO, stdout=stream, stderr=subprocess.STDOUT)
        records.append(
            {
                "task_id": task["task_id"],
                "started_at": started,
                "completed_at": now(),
                "returncode": completed.returncode,
                "result_path": str(result),
                "cpu_set": cpu_set,
            }
        )
        write_json(worker_root / "status.json", {"updated_at": now(), "records": records, "assigned": len(selected), "cpu_set": cpu_set})
        print(f"[{ordinal}/{len(selected)}] rc={completed.returncode} {task['task_id']}", flush=True)
    write_json(worker_root / "status.json", {"updated_at": now(), "records": records, "assigned": len(selected), "cpu_set": cpu_set, "complete": True})


def run_task_list(args: argparse.Namespace) -> None:
    """Run an explicit tail queue without changing already-running worker shards."""
    tasks = {task["task_id"]: task for task in load_tasks(args.campaign_root)}
    queue = read_json(args.task_list).get("task_ids")
    if not isinstance(queue, list) or not all(isinstance(value, str) for value in queue):
        raise ValueError("task-list must contain a task_ids string array")
    missing = [task_id for task_id in queue if task_id not in tasks]
    if missing:
        raise ValueError(f"task-list references unknown task: {missing[0]}")
    worker_root = args.campaign_root / "workers" / args.worker_name
    worker_root.mkdir(parents=True, exist_ok=True)
    records = []
    for ordinal, task_id in enumerate(queue, start=1):
        task = tasks[task_id]
        project = args.campaign_root / "projects" / task_id / "baseline"
        result = project / "repair_family_probe_result.json"
        if args.resume and result.is_file():
            records.append({"task_id": task_id, "status": "resume_skip"})
            continue
        started = now()
        command = [
            sys.executable, str(PROBE), "execute",
            "--project", str(project),
            "--cores", str(args.cores),
            "--timeout-seconds", str(args.timeout_seconds),
        ]
        if args.cpu_set:
            command.extend(["--cpu-set", args.cpu_set])
        with (worker_root / f"{ordinal:03d}_{task_id}.log").open("w", encoding="utf-8") as stream:
            completed = subprocess.run(command, cwd=REPO, stdout=stream, stderr=subprocess.STDOUT)
        records.append({
            "task_id": task_id,
            "started_at": started,
            "completed_at": now(),
            "returncode": completed.returncode,
            "result_path": str(result),
            "cpu_set": args.cpu_set,
        })
        write_json(worker_root / "status.json", {
            "updated_at": now(),
            "records": records,
            "assigned": len(queue),
            "cpu_set": args.cpu_set,
        })
        print(f"[{ordinal}/{len(queue)}] rc={completed.returncode} {task_id}", flush=True)
    write_json(worker_root / "status.json", {
        "updated_at": now(),
        "records": records,
        "assigned": len(queue),
        "cpu_set": args.cpu_set,
        "complete": True,
    })


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build-plan")
    build.add_argument("--source-campaign", type=Path, required=True)
    build.add_argument("--campaign-root", type=Path, required=True)
    build.set_defaults(func=load_plan)
    materialize = sub.add_parser("prepare")
    materialize.add_argument("--campaign-root", type=Path, required=True)
    materialize.add_argument("--resume", action="store_true")
    materialize.set_defaults(func=prepare)
    worker = sub.add_parser("run-worker")
    worker.add_argument("--campaign-root", type=Path, required=True)
    worker.add_argument("--worker-index", type=int, required=True)
    worker.add_argument("--workers", type=int, default=7)
    worker.add_argument("--cores", type=int, default=4)
    worker.add_argument(
        "--cpu-base", type=int, default=32,
        help="first host CPU assigned to worker 0; later workers receive disjoint ranges",
    )
    worker.add_argument("--timeout-seconds", type=int, default=7200)
    worker.add_argument("--resume", action="store_true")
    worker.set_defaults(func=run_worker)
    tail = sub.add_parser("run-task-list")
    tail.add_argument("--campaign-root", type=Path, required=True)
    tail.add_argument("--task-list", type=Path, required=True)
    tail.add_argument("--worker-name", required=True)
    tail.add_argument("--cores", type=int, default=4)
    tail.add_argument(
        "--cpu-set",
        help="explicit taskset-compatible CPU list forwarded to each ORFS flow",
    )
    tail.add_argument("--timeout-seconds", type=int, default=7200)
    tail.add_argument("--resume", action="store_true")
    tail.set_defaults(func=run_task_list)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        args.func(args)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
