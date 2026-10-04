#!/usr/bin/env python3
"""Advance Experiment 4 from frozen converters through hidden evaluation."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time
from typing import Any


MODELS = ("gpt", "claude", "qwen")


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


def method_specs(root: Path) -> list[tuple[str, Path | None]]:
    return [
        ("r2g-frozen-v3", None),
        *[
            (f"llm-{model}-frozen", root / "converter_development" / model / "frozen_converter.py")
            for model in MODELS
        ],
    ]


def converter_manifests(root: Path) -> list[Path]:
    return [root / "converter_development" / model / "frozen_converter_manifest.json" for model in MODELS]


def pre_hidden_audit_errors(audit: dict[str, Any], cohort: Path) -> list[str]:
    errors = []
    if audit.get('status') != 'ready_for_hidden_evaluation':
        errors.append('audit status is not ready_for_hidden_evaluation')
    if audit.get('phase') != 'hidden_evaluation':
        errors.append('audit phase is not hidden_evaluation')
    if audit.get('cohort_sha256') != sha256_file(cohort):
        errors.append('audit cohort hash does not match the frozen cohort')
    if audit.get('failed_checks'):
        errors.append('audit contains failed checks')
    return errors


def semantic_usable_summary(
    native: dict[str, dict[str, Any]], semantic_summary: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    rows = [row for row in semantic_summary.get("rows", []) if "method" in row]
    result: dict[str, dict[str, Any]] = {}
    for method, aggregate in native.items():
        native_pass = {
            row["task_id"] for row in aggregate.get("scores", []) if row.get("strict_pass") is True
        }
        method_rows = [row for row in rows if row["method"] == method]
        semantic_pass = {
            row["task"] for row in method_rows if row.get("verified_core_status") == "PASS"
        }
        expected = int(aggregate.get("expected_tasks") or 0)
        if len(method_rows) != expected:
            raise ValueError(
                f"semantic audit row count differs for {method}: {len(method_rows)} != {expected}"
            )
        usable = native_pass & semantic_pass
        result[method] = {
            "expected_cases": expected,
            "native_strict_passes": len(native_pass),
            "verified_core_semantic_passes": len(semantic_pass),
            "semantic_usable_passes": len(usable),
            "semantic_usable_rate": len(usable) / expected if expected else 0.0,
        }
    return result


def run_checked(command: list[str], log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n[{utc_now()}] {' '.join(command)}\n")
        log.flush()
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"command failed with rc={result.returncode}: {' '.join(command)}")


def runner_command(args: argparse.Namespace, *tail: str) -> list[str]:
    return [
        str(args.python),
        str(args.runner),
        "--cohort", str(args.cohort),
        "--campaign-root", str(args.campaign_root),
        "--runtime-root", str(args.runtime_root),
        "--python", str(args.python),
        *tail,
    ]


def wait_for_converter_freeze(root: Path, poll_seconds: int, timeout_seconds: int) -> list[Path]:
    manifests = converter_manifests(root)
    deadline = time.monotonic() + timeout_seconds
    while True:
        missing = [path for path in manifests if not path.is_file()]
        if not missing:
            return manifests
        if time.monotonic() >= deadline:
            raise TimeoutError(f"converter freeze timeout; missing: {[str(path) for path in missing]}")
        time.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--api-env-file', type=Path, required=True)
    parser.add_argument('--route-canary', type=Path, required=True)
    parser.add_argument('--semantic-validation-root', type=Path, required=True)
    parser.add_argument('--prelaunch-auditor', type=Path)
    parser.add_argument('--semantic-auditor', type=Path)
    parser.add_argument('--semantic-reporter', type=Path)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--freeze-timeout-seconds", type=int, default=86400)
    args = parser.parse_args()

    args.campaign_root = args.campaign_root.resolve()
    args.cohort = args.cohort.resolve()
    args.runtime_root = args.runtime_root.resolve()
    args.runner = args.runner.resolve()
    args.python = args.python.resolve()
    args.repo = args.repo.resolve()
    args.api_env_file = args.api_env_file.resolve()
    args.route_canary = args.route_canary.resolve()
    args.semantic_validation_root = args.semantic_validation_root.resolve()
    args.prelaunch_auditor = (args.prelaunch_auditor or
                              args.runtime_root / 'tools/audit_experiment4_prelaunch.py').resolve()
    args.semantic_auditor = (args.semantic_auditor or
                             args.runtime_root / 'tools/experiment4_semantic_audit.py').resolve()
    args.semantic_reporter = (args.semantic_reporter or
                              args.runtime_root / 'tools/report_experiment4_semantic_audit.py').resolve()
    state_path = args.campaign_root / "formal_pipeline_state.json"
    log_path = args.campaign_root / "logs/formal_pipeline.log"
    limits = read_json(args.campaign_root / "protocol/experiment4_resource_limits.json")
    maximum_workers = int(limits["hidden_evaluation"]["workers_conditional_max"])
    if not 1 <= args.workers <= maximum_workers:
        raise SystemExit(f"workers must be within 1..{maximum_workers}")

    state: dict[str, Any] = {
        "schema_version": "experiment4-formal-pipeline-2.0.1",
        "started_at": utc_now(),
        "status": "waiting_for_converter_freeze",
        "hidden_test_materialized": False,
        "workers": args.workers,
        "completed_methods": [],
    }
    if state_path.is_file():
        previous = read_json(state_path)
        state["initial_started_at"] = previous.get("initial_started_at", previous.get("started_at"))
        state["completed_methods"] = previous.get("completed_methods", [])
    else:
        state["initial_started_at"] = state["started_at"]
    write_json(state_path, state)

    try:
        manifests = wait_for_converter_freeze(
            args.campaign_root, args.poll_seconds, args.freeze_timeout_seconds
        )
        state["status"] = "checking_freeze_gate"
        state["converter_manifests"] = {
            path.parent.name: {"path": str(path), "sha256": sha256_file(path)} for path in manifests
        }
        write_json(state_path, state)
        pre_hidden_audit = args.campaign_root / 'pre_hidden_audit.json'
        run_checked(
            [str(args.python), str(args.prelaunch_auditor),
             '--repo', str(args.repo), '--campaign-root', str(args.campaign_root),
             '--api-env-file', str(args.api_env_file), '--route-canary', str(args.route_canary),
             '--semantic-validation-root', str(args.semantic_validation_root),
             '--require-confirmatory-cohort', '--phase', 'hidden_evaluation',
             '--output', str(pre_hidden_audit)],
            log_path,
        )
        audit = read_json(pre_hidden_audit)
        audit_errors = pre_hidden_audit_errors(audit, args.cohort)
        if audit_errors:
            raise RuntimeError(f'pre-hidden audit is not bound and ready: {audit_errors}')
        state['pre_hidden_audit'] = {'path': str(pre_hidden_audit),
                                     'sha256': sha256_file(pre_hidden_audit)}
        write_json(state_path, state)
        run_checked(
            runner_command(args, "preflight", "--check-frozen-converters", "--split", "hidden_test"),
            log_path,
        )

        state["status"] = "materializing_hidden_test"
        write_json(state_path, state)
        run_checked(
            runner_command(
                args, "materialize", "--split", "hidden_test", "--workers", str(args.workers),
                "--timeout-seconds", str(limits["hidden_evaluation"]["timeout_seconds_per_case"]),
            ),
            log_path,
        )
        run_checked(
            runner_command(args, "preflight", "--check-inputs", "--split", "hidden_test"),
            log_path,
        )
        state["hidden_test_materialized"] = True
        state["hidden_test_materialized_at"] = utc_now()
        write_json(state_path, state)

        for method, converter in method_specs(args.campaign_root):
            if method in state["completed_methods"]:
                continue
            state["status"] = f"running_{method}"
            write_json(state_path, state)
            run_tail = [
                "run", "--split", "hidden_test", "--method", method,
                "--workers", str(args.workers),
                "--timeout-seconds", str(limits["hidden_evaluation"]["timeout_seconds_per_case"]),
            ]
            if converter is not None:
                run_tail.extend(["--converter", str(converter)])
            run_checked(runner_command(args, *run_tail), log_path)
            run_checked(
                runner_command(
                    args, "evaluate", "--split", "hidden_test", "--method", method,
                    "--workers", str(args.workers), "--timeout-seconds", "1200",
                ),
                log_path,
            )
            state["completed_methods"].append(method)
            write_json(state_path, state)

        aggregates = {
            method: read_json(args.campaign_root / "reports" / f"{method}.aggregate.json")
            for method, _ in method_specs(args.campaign_root)
        }
        state["status"] = "running_independent_semantic_audit"
        write_json(state_path, state)
        semantic_root = args.campaign_root / "reports/semantic_audit"
        run_checked(
            [str(args.python), str(args.semantic_auditor),
             "--campaign", str(args.campaign_root), "--output", str(semantic_root),
             "--split", "hidden_test", "--yosys", str(limits["toolchain"]["yosys_exe"]),
             "--openroad", str(limits["toolchain"]["openroad_exe"]),
             "--study-role", "confirmatory hidden evaluation"],
            log_path,
        )
        run_checked([str(args.python), str(args.semantic_reporter), str(semantic_root)], log_path)
        semantic_summary = read_json(semantic_root / "summary.json")
        semantic_aggregate = read_json(semantic_root / "aggregate.json")
        if semantic_aggregate.get("oracle_errors"):
            raise RuntimeError("independent semantic audit contains oracle/infrastructure errors")
        usable = semantic_usable_summary(aggregates, semantic_summary)
        final = {
            "schema_version": "experiment4-final-results-3.0",
            "created_at": utc_now(),
            "cohort_sha256": sha256_file(args.cohort),
            "hidden_test_cases": len(read_json(args.cohort)["splits"]["hidden_test"]),
            "primary_metric": "semantic_usable_rate",
            "methods": {
                method: {"native_evaluation": aggregates[method], **usable[method]}
                for method in aggregates
            },
            "semantic_audit": {
                "summary_sha256": sha256_file(semantic_root / "summary.json"),
                "aggregate_sha256": sha256_file(semantic_root / "aggregate.json"),
                "verified_groups": limits["semantic_evaluation"]["required_groups"],
                "full_semantic_status": semantic_aggregate.get("core_semantic_status"),
                "unverified_dimensions": semantic_aggregate.get("unverified_dimensions"),
            },
        }
        write_json(args.campaign_root / "reports/experiment4_final_results.json", final)
        state["status"] = "completed"
        state["completed_at"] = utc_now()
        write_json(state_path, state)
    except Exception as exc:
        state["status"] = "failed"
        state["failed_at"] = utc_now()
        state["error"] = repr(exc)
        write_json(state_path, state)
        raise


if __name__ == "__main__":
    main()
