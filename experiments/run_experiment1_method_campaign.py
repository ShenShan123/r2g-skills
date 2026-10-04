#!/usr/bin/env python3
"""Run one continuous Experiment 1 acquisition method, then score it."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
from typing import Any


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from experiments.run_experiment1_rtl_acquisition import (  # noqa: E402
    METHOD_IDS,
    R2G_METHOD_IDS,
    ExperimentError,
    campaign_paths,
    find_batch,
    read_json,
    verify_bound_campaign,
)


CPU_SET_PATTERN = re.compile(r"^[0-9]+(?:-[0-9]+)?(?:,[0-9]+(?:-[0-9]+)?)*$")


def run_checked(command: list[str], *, cpu_set: str | None = None) -> None:
    if cpu_set is not None:
        command = ["taskset", "--cpu-list", cpu_set, *command]
    process = subprocess.Popen(command, cwd=REPO, start_new_session=True)
    previous_handlers: dict[signal.Signals, Any] = {}

    def forward_termination(signum: int, _frame: Any) -> None:
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            pass
        raise SystemExit(128 + signum)

    for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        previous_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, forward_termination)
    try:
        returncode = process.wait()
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=30)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        raise
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    if returncode:
        raise ExperimentError(
            f"campaign command failed with exit code {returncode}: "
            + " ".join(command)
        )


def configured_cpu_set(task: dict[str, Any], method_id: str) -> str:
    policy = task.get("method_aggregate_policy") or {}
    configured = (policy.get("method_cpu_sets") or {}).get(method_id)
    if not isinstance(configured, str) or not CPU_SET_PATTERN.fullmatch(configured):
        raise ExperimentError(f"missing or invalid frozen CPU set for {method_id}")
    return configured


@contextmanager
def github_api_rate_limit_env(root: Path, task: dict[str, Any]):
    """Share only GitHub REST pacing; acquisition workspaces remain independent."""
    policy = task.get("method_aggregate_policy") or {}
    interval = policy.get("github_api_min_interval_seconds")
    if not isinstance(interval, (int, float)) or interval <= 0:
        raise ExperimentError("missing frozen GitHub API pacing interval")
    updates = {
        "R2G_EXPERIMENT1_GITHUB_API_LOCK": str(root / "locks" / "github_api.lock"),
        "R2G_EXPERIMENT1_GITHUB_API_MIN_INTERVAL_SECONDS": str(interval),
    }
    old = {key: os.environ.get(key) for key in updates}
    os.environ.update(updates)
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _acquire_batches(args: argparse.Namespace) -> None:
    paths = campaign_paths(args.campaign_root)
    manifest = read_json(paths["manifest"])
    verify_bound_campaign(manifest)
    task = read_json(Path(manifest["task_spec"]["path"]))
    frozen_turns = int(task["method_budget"]["vanilla_max_turns_per_run"])
    batch_count = int(task["batch_policy"]["batches_per_method"])
    target = int(task["batch_policy"]["target_candidates_per_batch"])
    cpu_set = configured_cpu_set(task, args.method_id)
    frozen_output = int(
        task["method_budget"]["vanilla_max_output_tokens_per_turn"]
    )
    if manifest["campaign_mode"] == "formal" and (
        args.max_turns != frozen_turns or args.max_output_tokens != frozen_output
    ):
        raise ExperimentError(
            "formal Vanilla turn/output budgets must match the frozen task spec"
        )
    python = sys.executable
    for batch_id in range(1, batch_count + 1):
        manifest = read_json(paths["manifest"])
        batch = find_batch(manifest, args.method_id, batch_id)
        if batch["status"] in {"submitted", "complete"}:
            continue
        if batch["status"] != "pending":
            raise ExperimentError(
                f"cannot acquire {args.method_id}/batch{batch_id}: "
                f"status={batch['status']}"
            )

        if args.method_id in R2G_METHOD_IDS:
            method_command = [
                python,
                str(REPO / "experiments/run_experiment1_r2g_method.py"),
                "--method-id",
                args.method_id,
                "--campaign-root",
                str(paths["root"]),
                "--batch-id",
                str(batch_id),
                "--cores",
                str(args.cores),
            ]
            run_checked(method_command, cpu_set=cpu_set)
            method_root = (
                paths["root"]
                / "method_runs"
                / f"{args.method_id}.batch{batch_id}.formal"
            )
            submission = method_root / "submission.json"
            run_checked(
                [
                    python,
                    str(REPO / "experiments/build_experiment1_r2g_submission.py"),
                    "--campaign-root",
                    str(paths["root"]),
                    "--method-id",
                    args.method_id,
                    "--batch-id",
                    str(batch_id),
                    "--output",
                    str(submission),
                ],
                cpu_set=cpu_set,
            )
        else:
            if args.env_file is None:
                raise ExperimentError("Vanilla acquisition requires --env-file")
            run_checked(
                [
                    python,
                    str(REPO / "experiments/run_experiment1_vanilla_method.py"),
                    "--method-id",
                    args.method_id,
                    "--campaign-root",
                    str(paths["root"]),
                    "--batch-id",
                    str(batch_id),
                    "--target",
                    str(target),
                    "--run-kind",
                    "formal",
                    "--max-turns",
                    str(
                        frozen_turns
                        if manifest["campaign_mode"] == "formal"
                        else args.max_turns
                    ),
                    "--max-output-tokens",
                    str(
                        frozen_output
                        if manifest["campaign_mode"] == "formal"
                        else args.max_output_tokens
                    ),
                    "--env-file",
                    str(args.env_file.resolve()),
                ],
                cpu_set=cpu_set,
            )
            submission = (
                paths["root"]
                / "method_runs"
                / f"{args.method_id}.batch{batch_id}.formal"
                / "submission.json"
            )

        run_checked(
            [
                python,
                str(REPO / "experiments/run_experiment1_rtl_acquisition.py"),
                "accept-submission",
                "--campaign-root",
                str(paths["root"]),
                "--submission",
                str(submission),
            ]
        )
    print(
        f"{args.method_id}: the continuous acquisition submission is digest-locked; "
        "no formal evaluator result was exposed during acquisition."
    )


def acquire(args: argparse.Namespace) -> None:
    paths = campaign_paths(args.campaign_root)
    manifest = read_json(paths["manifest"])
    verify_bound_campaign(manifest)
    if manifest["campaign_mode"] == "formal" and not (
        os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    ):
        raise ExperimentError(
            "formal acquisition requires authenticated GITHUB_TOKEN or GH_TOKEN"
        )
    task = read_json(Path(manifest["task_spec"]["path"]))
    cpu_set = configured_cpu_set(task, args.method_id)
    print(f"{args.method_id}: starting independent acquisition on CPU set {cpu_set}", flush=True)
    with github_api_rate_limit_env(paths["root"], task):
        _acquire_batches(args)


def evaluate(args: argparse.Namespace) -> None:
    paths = campaign_paths(args.campaign_root)
    manifest = read_json(paths["manifest"])
    verify_bound_campaign(manifest)
    task = read_json(Path(manifest["task_spec"]["path"]))
    cpu_set = configured_cpu_set(task, args.method_id)
    batch_count = int(task["batch_policy"]["batches_per_method"])
    states = {
        batch_id: find_batch(manifest, args.method_id, batch_id)["status"]
        for batch_id in range(1, batch_count + 1)
    }
    if any(status not in {"submitted", "complete"} for status in states.values()):
        raise ExperimentError(
            "evaluation requires the acquisition submission to be locked; " f"states={states}"
        )
    python = sys.executable
    for batch_id in range(1, batch_count + 1):
        manifest = read_json(paths["manifest"])
        if find_batch(manifest, args.method_id, batch_id)["status"] == "complete":
            continue
        run_checked(
            [
                python,
                str(REPO / "experiments/run_experiment1_rtl_acquisition.py"),
                "evaluate-batch",
                "--campaign-root",
                str(paths["root"]),
                "--method-id",
                args.method_id,
                "--batch-id",
                str(batch_id),
                "--cores",
                str(args.cores),
            ],
            cpu_set=cpu_set,
        )
    run_checked(
        [
            python,
            str(REPO / "experiments/run_experiment1_rtl_acquisition.py"),
            "summarize-method",
            "--campaign-root",
            str(paths["root"]),
            "--method-id",
            args.method_id,
        ]
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("acquire", "evaluate"))
    parser.add_argument("--method-id", choices=sorted(METHOD_IDS), required=True)
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--cores", type=int, default=4)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--max-turns", type=int, default=1600)
    parser.add_argument("--max-output-tokens", type=int, default=4096)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.cores < 1:
            raise ExperimentError("--cores must be positive")
        if args.command == "acquire":
            acquire(args)
        else:
            evaluate(args)
    except ExperimentError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
