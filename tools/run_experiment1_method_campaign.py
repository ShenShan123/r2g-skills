#!/usr/bin/env python3
"""Run one Experiment 1 method through four locked batches, then score it."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.run_experiment1_rtl_acquisition import (  # noqa: E402
    METHOD_IDS,
    R2G_METHOD_IDS,
    ExperimentError,
    campaign_paths,
    find_batch,
    read_json,
    verify_bound_campaign,
)


def run_checked(command: list[str]) -> None:
    result = subprocess.run(command, cwd=REPO, check=False)
    if result.returncode:
        raise ExperimentError(
            f"campaign command failed with exit code {result.returncode}: "
            + " ".join(command)
        )


def acquire(args: argparse.Namespace) -> None:
    paths = campaign_paths(args.campaign_root)
    manifest = read_json(paths["manifest"])
    verify_bound_campaign(manifest)
    task = read_json(Path(manifest["task_spec"]["path"]))
    frozen_turns = int(task["method_budget"]["vanilla_max_turns_per_batch"])
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
    for batch_id in range(1, 5):
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
                str(REPO / "tools/run_experiment1_r2g_method.py"),
                "--method-id",
                args.method_id,
                "--campaign-root",
                str(paths["root"]),
                "--batch-id",
                str(batch_id),
                "--cores",
                str(args.cores),
            ]
            run_checked(method_command)
            method_root = (
                paths["root"]
                / "method_runs"
                / f"{args.method_id}.batch{batch_id}.formal"
            )
            submission = method_root / "submission.json"
            run_checked(
                [
                    python,
                    str(REPO / "tools/build_experiment1_r2g_submission.py"),
                    "--campaign-root",
                    str(paths["root"]),
                    "--method-id",
                    args.method_id,
                    "--batch-id",
                    str(batch_id),
                    "--output",
                    str(submission),
                ]
            )
        else:
            if args.env_file is None:
                raise ExperimentError("Vanilla acquisition requires --env-file")
            run_checked(
                [
                    python,
                    str(REPO / "tools/run_experiment1_vanilla_method.py"),
                    "--method-id",
                    args.method_id,
                    "--campaign-root",
                    str(paths["root"]),
                    "--batch-id",
                    str(batch_id),
                    "--target",
                    "25",
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
                ]
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
                str(REPO / "tools/run_experiment1_rtl_acquisition.py"),
                "accept-submission",
                "--campaign-root",
                str(paths["root"]),
                "--submission",
                str(submission),
            ]
        )
    print(
        f"{args.method_id}: all four acquisition batches are digest-locked; "
        "no formal evaluator result was exposed during acquisition."
    )


def evaluate(args: argparse.Namespace) -> None:
    paths = campaign_paths(args.campaign_root)
    manifest = read_json(paths["manifest"])
    verify_bound_campaign(manifest)
    states = {
        batch_id: find_batch(manifest, args.method_id, batch_id)["status"]
        for batch_id in range(1, 5)
    }
    if any(status not in {"submitted", "complete"} for status in states.values()):
        raise ExperimentError(
            "evaluation requires all four batches to be locked; " f"states={states}"
        )
    python = sys.executable
    for batch_id in range(1, 5):
        manifest = read_json(paths["manifest"])
        if find_batch(manifest, args.method_id, batch_id)["status"] == "complete":
            continue
        run_checked(
            [
                python,
                str(REPO / "tools/run_experiment1_rtl_acquisition.py"),
                "evaluate-batch",
                "--campaign-root",
                str(paths["root"]),
                "--method-id",
                args.method_id,
                "--batch-id",
                str(batch_id),
                "--cores",
                str(args.cores),
            ]
        )
    run_checked(
        [
            python,
            str(REPO / "tools/run_experiment1_rtl_acquisition.py"),
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
    parser.add_argument("--max-turns", type=int, default=100)
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
