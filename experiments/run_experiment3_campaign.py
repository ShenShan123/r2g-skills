#!/usr/bin/env python3
"""Checkpointed orchestration for the frozen Experiment 3 protocol."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any


REPO = Path(__file__).resolve().parents[1]
PROPOSER = REPO / "experiments/run_experiment3_llm_proposals.py"
ABLATION = REPO / "experiments/run_experiment3_ablation.py"
BANK = REPO / "experiments/experiment3_candidate_bank.py"
MODELS = ("gpt", "claude", "qwen")
DEFAULT_DOMAINS = ("drc_edge_pin", "setup_timing")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_env(path: Path | None) -> dict[str, str]:
    env = dict(os.environ)
    if not path:
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if not value or value.startswith("#") or "=" not in value:
            continue
        key, raw = value.split("=", 1)
        key = key.strip()
        if key and key.replace("_", "").isalnum():
            env[key] = raw.strip().strip('"').strip("'")
    return env


class Campaign:
    def __init__(self, args: argparse.Namespace):
        self.root = args.campaign_root.resolve()
        self.runtime_env = args.runtime_env
        self.api_env = load_env(args.api_env_file)
        self.routes = args.routes.resolve()
        self.resources = args.resource_limits.resolve()
        self.action_policy = args.action_policy.resolve()
        self.a_propose_prompt = args.a_propose_prompt.resolve()
        self.pure_llm_prompt = args.pure_llm_prompt.resolve()
        self.eda_host = args.eda_host
        self.remote_repo = args.remote_repo
        self.remote_python = args.remote_python
        self.limits = read_json(self.resources)
        self.domains = tuple(self.limits.get("failure_domains") or DEFAULT_DOMAINS)
        initial_workers = int(self.limits["eda"]["concurrent_trials_initial"])
        conditional_max = int(self.limits["eda"]["concurrent_trials_conditional_max"])
        self.eda_workers = args.eda_workers or initial_workers
        if self.eda_workers < 1 or self.eda_workers > conditional_max:
            raise ValueError(
                f"EDA workers must be within 1..{conditional_max}, got {self.eda_workers}"
            )
        self.split = self.root / "data/split_manifest.json"
        self.sentinels = self.root / "data/clean_sentinels.json"
        self.challenge_root = self.root / "inputs/challenges"
        self.sentinel_root = self.root / "inputs/sentinels"
        self.events = self.root / "state/orchestrator_events.jsonl"

    def require_frozen_resources(self) -> None:
        if self.limits.get("status") != "frozen":
            raise ValueError("campaign execution requires a frozen resource-limit file")

    def event(self, kind: str, **fields: Any) -> None:
        self.events.parent.mkdir(parents=True, exist_ok=True)
        with self.events.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"created_at": now(), "kind": kind, **fields}, sort_keys=True) + "\n")

    def run(self, command: list[str], *, api: bool = False) -> None:
        display = [str(value) for value in command]
        self.event("command_start", command=display)
        env = self.api_env if api else dict(os.environ)
        if self.runtime_env:
            env["R2G_ENV_FILE"] = str(self.runtime_env.resolve())
        completed = subprocess.run(display, env=env)
        self.event("command_end", command=display, returncode=completed.returncode)
        if completed.returncode != 0:
            raise subprocess.CalledProcessError(completed.returncode, display)

    @staticmethod
    def _option_path(command: list[str], option: str) -> Path | None:
        if option not in command:
            return None
        return Path(command[command.index(option) + 1])

    def _remote_matrix_command(self, command: list[str]) -> list[str]:
        if not self.remote_repo:
            raise ValueError("--remote-repo is required with --eda-host")
        result = [str(value) for value in command]
        result[0] = str(self.remote_python)
        result[1] = str(self.remote_repo / "experiments/run_experiment3_ablation.py")
        if self.runtime_env:
            result = ["env", f"R2G_ENV_FILE={self.runtime_env.resolve()}", *result]
        return result

    def _remote_file_exists(self, path: Path) -> bool:
        completed = subprocess.run(
            ["ssh", self.eda_host, "test", "-f", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return completed.returncode == 0

    def _copy_to_remote(self, path: Path) -> None:
        self.run(["ssh", self.eda_host, "mkdir", "-p", str(path.parent)])
        self.run(["scp", "-q", str(path), f"{self.eda_host}:{path}"])

    def _copy_from_remote(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.run(["scp", "-q", f"{self.eda_host}:{path}", str(path)])

    def run_eda(self, command: list[str]) -> None:
        if not self.eda_host:
            self.run(command)
            return
        candidate_bank = self._option_path(command, "--candidate-bank")
        state_output = self._option_path(command, "--state-output")
        if state_output is None:
            raise ValueError("distributed EDA requires an explicit --state-output")
        if candidate_bank:
            self._copy_to_remote(candidate_bank)
        if self._remote_file_exists(state_output):
            self._copy_from_remote(state_output)
        elif state_output.is_file():
            self._copy_to_remote(state_output)
        self.run(["ssh", self.eda_host, "mkdir", "-p", str(state_output.parent)])
        remote = self._remote_matrix_command(command)
        self.event("remote_eda_start", host=self.eda_host, command=remote)
        completed = subprocess.run(["ssh", self.eda_host, shlex.join(remote)])
        self.event(
            "remote_eda_end",
            host=self.eda_host,
            command=remote,
            returncode=completed.returncode,
        )
        if self._remote_file_exists(state_output):
            self._copy_from_remote(state_output)
        if completed.returncode != 0:
            raise subprocess.CalledProcessError(completed.returncode, remote)

    @property
    def a_root(self) -> Path:
        return self.root / "state/a_learning"

    def proposal_files(self, through_round: int | None = None) -> list[Path]:
        paths = sorted((self.a_root / "proposals").glob("round-*/*/*.json"))
        if through_round is None:
            return paths
        return [path for path in paths if int(path.parts[-3].split("-")[-1]) <= through_round]

    def execution_files(self, through_round: int | None = None) -> list[Path]:
        paths = sorted((self.a_root / "execution").glob("round-*/*.json"))
        if through_round is None:
            return paths
        return [path for path in paths if int(path.parent.name.split("-")[-1]) <= through_round]

    @staticmethod
    def flatten_evidence(paths: list[Path]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in paths:
            if not path.is_file():
                continue
            for result in read_json(path).get("results") or []:
                rows.extend(result.get("attempts") or [])
        return rows

    def aggregate_a(self, through_round: int | None = None) -> tuple[Path, Path]:
        proposals: list[dict[str, Any]] = []
        for path in self.proposal_files(through_round):
            proposals.extend(read_json(path).get("accepted") or [])
        evidence = self.flatten_evidence(self.execution_files(through_round))
        suffix = "all" if through_round is None else f"through-round-{through_round}"
        proposal_path = self.a_root / "aggregate" / f"proposals-{suffix}.json"
        evidence_path = self.a_root / "aggregate" / f"exploratory-evidence-{suffix}.json"
        write_json(proposal_path, proposals)
        write_json(evidence_path, evidence)
        return proposal_path, evidence_path

    def build_bank(
        self,
        output: Path,
        *,
        through_round: int | None = None,
        formal_evidence: Path | None = None,
    ) -> None:
        proposals, exploratory = self.aggregate_a(through_round)
        command = [
            sys.executable,
            str(BANK),
            "build-bank",
            "--proposals",
            str(proposals),
            "--exploratory-evidence",
            str(exploratory),
            "--output",
            str(output),
            "--policy",
            str(self.action_policy),
        ]
        if formal_evidence:
            command.extend(["--formal-a-evidence", str(formal_evidence)])
        self.run(command)

    def active_hashes(self, bank: Path, domain: str, output: Path) -> None:
        payload = read_json(bank)
        hashes = [
            row["candidate_hash"]
            for row in payload.get("candidate_records") or []
            if row.get("admitted") is True
            and (row.get("candidate") or {}).get("failure_domain") == domain
        ]
        write_json(output, {"candidate_hashes": sorted(hashes)})

    def attempted_hashes(self, domain: str, output: Path, before_round: int) -> None:
        hashes: set[str] = set()
        for path in sorted((self.a_root / "plans").glob(f"round-*/*{domain}*.json")):
            round_index = int(path.parent.name.split("-")[-1])
            if round_index >= before_round:
                continue
            for item in read_json(path).get("selected") or []:
                hashes.add(item["candidate"]["candidate_hash"])
        write_json(output, {"candidate_hashes": sorted(hashes)})

    @staticmethod
    def feedback_rows(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
        allowed = (
            "task_id",
            "candidate_id",
            "candidate_hash",
            "failure_domain",
            "verdict",
            "baseline_failure_signature",
            "action_failure_signature",
            "wns_delta_ns",
            "drc_delta",
            "route_delta",
            "lvs_delta",
            "hard_regression",
            "clean_sentinel_regression",
            "infrastructure_complete",
            "elapsed_seconds",
        )
        return [{key: row.get(key) for key in allowed} for row in evidence]

    def matrix_base(self, *, workers: int | None = None) -> list[str]:
        worker_count = workers or self.eda_workers
        cpu_sets = tuple(
            self.limits.get("eda", {}).get("cpu_sets")
            or ("160-163", "164-167", "168-171")
        )
        if worker_count < 1 or worker_count > len(cpu_sets):
            raise ValueError(f"unsupported EDA worker count: {worker_count}")
        command = [
            sys.executable,
            str(ABLATION),
            "run-matrix",
            "--campaign-root",
            str(self.root),
            "--split-manifest",
            str(self.split),
            "--sentinel-manifest",
            str(self.sentinels),
            "--challenge-root",
            str(self.challenge_root),
            "--sentinel-root",
            str(self.sentinel_root),
            "--workers",
            str(worker_count),
            "--cores",
            str(self.limits["eda"]["cores_per_trial"]),
        ]
        for cpu_set in cpu_sets[:worker_count]:
            command.extend(["--cpu-set", cpu_set])
        command.extend(
            [
                "--timeout-seconds",
                str(self.limits["eda"]["timeout_seconds_per_trial"]),
                "--compact",
            ]
        )
        return command

    def run_a_learning(self) -> None:
        self.require_frozen_resources()
        limits = self.limits["a_propose"]
        max_output = int(limits["max_output_tokens_per_call"])
        total_budget = int(limits["total_tokens_per_model"])
        for round_index in range(int(limits["rounds"])):
            for domain in self.domains:
                round_root = self.a_root / "proposals" / f"round-{round_index}" / domain
                prior_bank = self.a_root / "banks" / f"through-round-{round_index - 1}.json"
                if round_index == 0 and not prior_bank.is_file():
                    write_json(prior_bank, {"candidate_records": []})
                active_path = self.a_root / "hashes" / f"active-before-{round_index}-{domain}.json"
                self.active_hashes(prior_bank, domain, active_path)
                active_count = len(read_json(active_path)["candidate_hashes"])
                attempted_path = self.a_root / "hashes" / f"attempted-before-{round_index}-{domain}.json"
                self.attempted_hashes(domain, attempted_path, round_index)
                plan = self.a_root / "plans" / f"round-{round_index}" / f"{domain}.json"
                execution = self.a_root / "execution" / f"round-{round_index}" / f"{domain}.json"
                if active_count >= int(limits["active_candidates_per_domain"]):
                    write_json(
                        plan,
                        {
                            "schema_version": "experiment3-proposal-round-plan-1.0",
                            "created_at": now(),
                            "status": "skipped_active_candidate_limit",
                            "active_count": active_count,
                            "selected_count": 0,
                            "selected": [],
                        },
                    )
                    write_json(
                        execution,
                        {
                            "schema_version": "experiment3-matrix-execution-1.0",
                            "updated_at": now(),
                            "status": "skipped_active_candidate_limit",
                            "mode": "a_explore",
                            "split": "a_propose",
                            "planned_tasks": 0,
                            "planned_trial_upper_bound": 0,
                            "completed_records": 0,
                            "results": [],
                        },
                    )
                    bank = self.a_root / "banks" / f"through-round-{round_index}.json"
                    self.build_bank(bank, through_round=round_index)
                    self.event(
                        "a_round_domain_skipped_active_limit",
                        round=round_index,
                        failure_domain=domain,
                        active_count=active_count,
                    )
                    continue
                feedback = self.a_root / "feedback" / f"round-{round_index}-{domain}.json"
                prior_evidence = self.flatten_evidence(self.execution_files(round_index - 1)) if round_index else []
                write_json(
                    feedback,
                    [
                        row
                        for row in self.feedback_rows(prior_evidence)
                        if row.get("failure_domain") == domain
                    ],
                )
                for model in MODELS:
                    output = round_root / f"{model}.json"
                    self.run(
                        [
                            sys.executable,
                            str(PROPOSER),
                            "propose",
                            "--routes",
                            str(self.routes),
                            "--system-prompt",
                            str(self.a_propose_prompt),
                            "--model",
                            model,
                            "--phase",
                            "a_propose",
                            "--failure-domain",
                            domain,
                            "--action-policy",
                            str(self.action_policy),
                            "--round",
                            str(round_index),
                            "--context",
                            str(self.root / "model_contexts/a_propose" / f"{domain}.json"),
                            "--feedback",
                            str(feedback),
                            "--max-output-tokens",
                            str(max_output),
                            "--max-proposals",
                            str(limits["raw_proposals_per_call"]),
                            "--budget-ledger",
                            str(self.a_root / "token_ledgers" / f"{model}.json"),
                            "--total-token-budget",
                            str(total_budget),
                            "--resource-limits",
                            str(self.resources),
                            "--require-frozen-resources",
                            "--resume",
                            "--output",
                            str(output),
                        ],
                        api=True,
                    )
                plan_command = [
                    sys.executable,
                    str(PROPOSER),
                    "build-round-plan",
                    "--active-hashes",
                    str(active_path),
                    "--attempted-hashes",
                    str(attempted_path),
                    "--execution-limit",
                    str(limits["pooled_executions_per_domain_per_round"]),
                    "--active-limit",
                    str(limits["active_candidates_per_domain"]),
                    "--output",
                    str(plan),
                ]
                for model in MODELS:
                    plan_command.extend(["--proposal-file", str(round_root / f"{model}.json")])
                self.run(plan_command)
                context = read_json(self.root / "model_contexts/a_propose" / f"{domain}.json")
                matrix = self.matrix_base() + [
                    "--candidate-bank",
                    str(plan),
                    "--split",
                    "a_propose",
                    "--mode",
                    "a_explore",
                    "--arm-id",
                    f"a_explore_r{round_index}_{domain}",
                    "--evidence-role",
                    "a_propose_exploration",
                    "--state-output",
                    str(execution),
                ]
                tasks = context["tasks"]
                exploratory_count = int(limits.get("exploratory_tasks_per_round") or len(tasks))
                if exploratory_count < len(tasks):
                    start = (round_index * exploratory_count) % len(tasks)
                    tasks = [
                        tasks[(start + offset) % len(tasks)]
                        for offset in range(exploratory_count)
                    ]
                for task in tasks:
                    matrix.extend(["--task-id", task["task_id"]])
                self.run_eda(matrix)
                bank = self.a_root / "banks" / f"through-round-{round_index}.json"
                self.build_bank(bank, through_round=round_index)
                self.event("a_round_domain_complete", round=round_index, failure_domain=domain)

    def formal_a_evidence(self) -> Path:
        paths = [
            self.root / "state/a_formal/a_propose_and_sentinels.json",
            self.root / "state/a_formal/a_validation.json",
        ]
        output = self.root / "state/a_formal/formal_a_evidence.json"
        write_json(output, self.flatten_evidence(paths))
        return output

    def run_a_formal(self) -> None:
        self.require_frozen_resources()
        final_round = int(self.limits["a_propose"]["rounds"]) - 1
        bank = self.a_root / "banks" / f"through-round-{final_round}.json"
        if not bank.is_file():
            raise ValueError(f"A-learning is incomplete: {bank.name} is missing")
        jobs = (
            ("a_propose", True, self.root / "state/a_formal/a_propose_and_sentinels.json"),
            ("a_validation", False, self.root / "state/a_formal/a_validation.json"),
        )
        for split, include_sentinels, state in jobs:
            command = self.matrix_base() + [
                "--candidate-bank",
                str(bank),
                "--split",
                split,
                "--mode",
                "a_formal",
                "--arm-id",
                f"a_formal_{split}",
                "--evidence-role",
                split,
                "--state-output",
                str(state),
            ]
            if include_sentinels:
                command.append("--include-sentinels")
            self.run_eda(command)
        formal = self.formal_a_evidence()
        self.build_bank(self.root / "state/frozen_candidate_bank.json", formal_evidence=formal)
        self.event("a_formal_complete")

    def run_b_ablations(self) -> None:
        self.require_frozen_resources()
        bank = self.root / "state/frozen_candidate_bank.json"
        if not bank.is_file():
            raise ValueError("frozen candidate bank is missing")
        for mode in ("m0", "m1", "m2", "m3"):
            command = self.matrix_base() + [
                "--split",
                "b_heldout",
                "--mode",
                mode,
                "--arm-id",
                mode,
                "--evidence-role",
                "b_heldout_locked",
                "--state-output",
                str(self.root / "state/b_heldout" / f"{mode}.json"),
            ]
            if mode != "m0":
                command.extend(["--candidate-bank", str(bank), "--include-sentinels"])
            self.run_eda(command)
        self.event("b_ablations_complete")

    def run_pure_llm(self, model: str) -> None:
        self.require_frozen_resources()
        if model not in MODELS:
            raise ValueError(f"unknown model: {model}")
        limits = self.limits["pure_llm"]
        max_output = int(limits["max_output_tokens_per_call"])
        total_budget = int(limits["total_tokens_per_model_per_task"])
        assignments = read_json(self.split)["assignments"]
        task_ids = sorted(row["task_id"] for row in assignments if row["split"] == "b_heldout")
        for task_id in task_ids:
            context_path = self.root / "model_contexts/b_heldout" / f"{task_id}.json"
            context = read_json(context_path)
            domain = context["tasks"][0]["failure_domain"]
            task_root = self.root / "state/pure_llm" / model / task_id
            feedback_path = task_root / "feedback.json"
            feedback: list[dict[str, Any]] = []
            for attempt in range(int(limits["api_calls_per_task"])):
                write_json(feedback_path, feedback)
                proposal_path = task_root / f"proposal-{attempt}.json"
                self.run(
                    [
                        sys.executable,
                        str(PROPOSER),
                        "propose",
                        "--routes",
                        str(self.routes),
                        "--system-prompt",
                        str(self.pure_llm_prompt),
                        "--model",
                        model,
                        "--phase",
                        "pure_llm",
                        "--failure-domain",
                        domain,
                        "--action-policy",
                        str(self.action_policy),
                        "--round",
                        str(attempt),
                        "--context",
                        str(context_path),
                        "--feedback",
                        str(feedback_path),
                        "--max-output-tokens",
                        str(max_output),
                        "--max-proposals",
                        "1",
                        "--budget-ledger",
                        str(task_root / "token_ledger.json"),
                        "--total-token-budget",
                        str(total_budget),
                        "--resource-limits",
                        str(self.resources),
                        "--require-frozen-resources",
                        "--resume",
                        "--output",
                        str(proposal_path),
                    ],
                    api=True,
                )
                accepted = read_json(proposal_path).get("accepted") or []
                if not accepted:
                    feedback.append(
                        {
                            "attempt": attempt,
                            "contract_valid": read_json(proposal_path).get("contract_valid"),
                            "executed": False,
                            "strict_clean": False,
                        }
                    )
                    continue
                plan = task_root / f"plan-{attempt}.json"
                write_json(
                    plan,
                    {
                        "schema_version": "experiment3-proposal-round-plan-1.0",
                        "created_at": now(),
                        "selected_count": 1,
                        "selected": [{"candidate": accepted[0], "proposal_sources": [model]}],
                    },
                )
                state = task_root / f"execution-{attempt}.json"
                command = self.matrix_base(workers=1) + [
                    "--candidate-bank",
                    str(plan),
                    "--split",
                    "b_heldout",
                    "--task-id",
                    task_id,
                    "--mode",
                    "pure_llm",
                    "--arm-id",
                    f"pure_llm_{model}",
                    "--evidence-role",
                    f"pure_llm_{model}",
                    "--state-output",
                    str(state),
                ]
                self.run_eda(command)
                state_payload = read_json(state)
                rows = self.flatten_evidence([state])
                feedback.extend(self.feedback_rows(rows))
                if any(row.get("strict_clean") is True for row in state_payload.get("results") or []):
                    break
            self.event("pure_llm_task_complete", model=model, task_id=task_id)

    def status(self) -> dict[str, Any]:
        return {
            "schema_version": "experiment3-orchestrator-status-1.0",
            "created_at": now(),
            "campaign_root": str(self.root),
            "controller_host": os.uname().nodename,
            "eda_host": self.eda_host or os.uname().nodename,
            "eda_workers": self.eda_workers,
            "remote_python": str(self.remote_python),
            "resource_status": self.limits.get("status"),
            "a_proposal_artifacts": len(self.proposal_files()),
            "a_execution_checkpoints": len(self.execution_files()),
            "a_final_bank": (self.root / "state/frozen_candidate_bank.json").is_file(),
            "b_checkpoints": sorted(path.name for path in (self.root / "state/b_heldout").glob("*.json")),
            "pure_llm_models_started": sorted(
                path.name for path in (self.root / "state/pure_llm").iterdir()
            ) if (self.root / "state/pure_llm").is_dir() else [],
        }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--campaign-root", type=Path, required=True)
    result.add_argument("--runtime-env", type=Path)
    result.add_argument("--api-env-file", type=Path)
    result.add_argument(
        "--eda-host",
        help="SSH host used only for EDA trials; model calls remain on the controller",
    )
    result.add_argument(
        "--remote-repo",
        type=Path,
        help="experiment runtime repository path on --eda-host",
    )
    result.add_argument(
        "--remote-python",
        type=Path,
        default=Path("/usr/bin/python3"),
        help="Python interpreter installed on --eda-host",
    )
    result.add_argument(
        "--eda-workers",
        type=int,
        help="EDA concurrency, bounded by the frozen conditional maximum",
    )
    result.add_argument(
        "--routes",
        type=Path,
        default=REPO / "docs/experiments/experiment3/experiment3_model_routes.json",
    )
    result.add_argument(
        "--resource-limits",
        type=Path,
        default=REPO / "docs/experiments/experiment3/experiment3_resource_limits.json",
    )
    result.add_argument(
        "--action-policy",
        type=Path,
        default=REPO / "docs/experiments/signoff/sky130hd_100mhz_fixed_task_repair_action_policy.json",
    )
    result.add_argument(
        "--a-propose-prompt",
        type=Path,
        default=REPO / "docs/experiments/experiment3/a_propose_system_prompt.md",
    )
    result.add_argument(
        "--pure-llm-prompt",
        type=Path,
        default=REPO / "docs/experiments/experiment3/pure_llm_system_prompt.md",
    )
    subparsers = result.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run-a-learning")
    subparsers.add_parser("run-a-formal")
    subparsers.add_parser("run-b-ablations")
    pure = subparsers.add_parser("run-pure-llm")
    pure.add_argument("--model", choices=MODELS, required=True)
    subparsers.add_parser("status")
    return result


def main() -> int:
    args = parser().parse_args()
    campaign = Campaign(args)
    if args.command == "run-a-learning":
        campaign.run_a_learning()
    elif args.command == "run-a-formal":
        campaign.run_a_formal()
    elif args.command == "run-b-ablations":
        campaign.run_b_ablations()
    elif args.command == "run-pure-llm":
        campaign.run_pure_llm(args.model)
    else:
        print(json.dumps(campaign.status(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
