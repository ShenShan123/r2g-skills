#!/usr/bin/env python3
"""Prepare, execute, resume, and score Experiment 4 graph converters.

The referee materializes immutable four-stage inputs once.  Every method then
receives the same JSON config and must emit the same v3 four-stage dataset
contract.  Test cases are never exposed during converter development.
"""

from __future__ import annotations

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Callable


SCHEMA_VERSION = "experiment4-graph-conversion-run-1.0"
MATERIALIZER_VERSION = 4
RUNNER_VERSION = 3
DEFAULT_ORFS_ROOT = Path("/home/yangao/r2g_toolchain/OpenROAD-flow-scripts")
DEFAULT_OPENROAD_EXE = DEFAULT_ORFS_ROOT / "tools/install/OpenROAD/bin/openroad"
FORBIDDEN_CONVERTER_TEXT = (
    "/home/yangao/r2g-skills",
    "def-graph/scripts",
    "run_stage_dataset.sh",
    "01_build_base_graph.py",
    "02_extract_features.py",
    "03_extract_labels.py",
    "04_assemble_heterograph.py",
    "05_build_stage_snapshots.py",
    "subprocess",
    "os.system",
    "socket",
    "urllib",
    "requests",
)


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


def cohort_rows(cohort: dict[str, Any], split: str) -> list[dict[str, Any]]:
    splits = cohort.get("splits") or {}
    if split == "all":
        return [row for name in ("canary", "development", "hidden_test") for row in splits[name]]
    rows = splits.get(split)
    if not isinstance(rows, list):
        raise ValueError(f"unknown cohort split: {split}")
    return rows


def find_sample_config(root: Path) -> Path:
    matches: list[Path] = []
    for path in root.glob("*.json"):
        try:
            if read_json(path).get("schema") == "r2g2_four_stage_sample_v1":
                matches.append(path)
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
    if len(matches) != 1:
        raise ValueError(f"expected one four-stage config in {root}, found {len(matches)}")
    return matches[0]


def run_command(command: list[str], log_path: Path, timeout: int) -> dict[str, Any]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    resource_path = log_path.with_suffix(log_path.suffix + ".resources")
    started = time.monotonic()
    with log_path.open("w", encoding="utf-8") as log:
        log.write("command=" + json.dumps(command) + "\n")
        log.flush()
        try:
            timed_command = [
                "/usr/bin/time",
                "-f",
                "peak_rss_kib=%M\nuser_seconds=%U\nsystem_seconds=%S",
                "-o",
                str(resource_path),
                *command,
            ]
            result = subprocess.run(
                timed_command,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout,
                check=False,
            )
            returncode = result.returncode
            error = ""
        except subprocess.TimeoutExpired:
            returncode = 124
            error = "timeout"
    resources: dict[str, int | float] = {}
    if resource_path.is_file():
        for line in resource_path.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if not separator or not re.fullmatch(r"[0-9.]+", value):
                continue
            resources[key] = int(value) if key == "peak_rss_kib" else float(value)
    return {
        "command": command,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "returncode": returncode,
        "error": error,
        "log": str(log_path.resolve()),
        "resources": resources,
    }


def copy_frozen_input(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    shutil.copy2(source, temporary)
    temporary.replace(target)


def input_attestation_errors(state: dict[str, Any], row: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if state.get("status") != "ready":
        errors.append("status is not ready")
    if state.get("materializer_version") != MATERIALIZER_VERSION:
        errors.append("materializer version mismatch")
    if state.get("baseline_result_sha256") != row.get("baseline_result_sha256"):
        errors.append("baseline result digest mismatch")
    config = Path(str(state.get("config") or ""))
    if not config.is_file() or sha256_file(config) != state.get("config_sha256"):
        errors.append("config is missing or has a digest mismatch")
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    for name, artifact in artifacts.items():
        path = Path(str(artifact.get("path") or ""))
        if not path.is_file():
            errors.append(f"artifact is missing: {name}")
            continue
        if path.stat().st_size != artifact.get("bytes"):
            errors.append(f"artifact size mismatch: {name}")
        if sha256_file(path) != artifact.get("sha256"):
            errors.append(f"artifact digest mismatch: {name}")
    if not artifacts:
        errors.append("artifact attestation is empty")
    toolchain = state.get("toolchain") if isinstance(state.get("toolchain"), dict) else {}
    openroad = Path(str(toolchain.get("openroad_exe") or ""))
    if not openroad.is_file() or sha256_file(openroad) != toolchain.get("openroad_sha256"):
        errors.append("OpenROAD executable is missing or has a digest mismatch")
    return errors


def materialize_one(
    row: dict[str, Any], campaign_root: Path, runtime_root: Path, python: Path, timeout: int
) -> dict[str, Any]:
    task_id = row["task_id"]
    case_root = campaign_root / "inputs" / task_id
    state_path = case_root / "input_attestation.json"
    if state_path.is_file():
        state = read_json(state_path)
        if not input_attestation_errors(state, row):
            return state

    case_root.mkdir(parents=True, exist_ok=True)
    stage_tool = runtime_root / "r2g-skills/def-graph/scripts/stage_dataset/make_sample_config.py"
    timing_tool = runtime_root / "r2g-skills/def-graph/scripts/stage_dataset/emit_timing_reports.py"
    commands: list[dict[str, Any]] = []
    commands.append(
        run_command(
            [
                str(python),
                str(stage_tool),
                "--run-dir",
                row["run_dir"],
                "--out-dir",
                str(case_root),
                "--platform",
                row["platform"],
            ],
            case_root / "logs/materialize.log",
            timeout,
        )
    )
    if commands[-1]["returncode"] != 0:
        state = {"schema_version": SCHEMA_VERSION, "task_id": task_id, "status": "failed", "commands": commands}
        write_json(state_path, state)
        return state

    config_path = find_sample_config(case_root)
    config = read_json(config_path)
    frozen_dir = case_root / "frozen_raw"
    for field, basename in (
        ("yosys_v", "1_2_yosys.v"),
        ("spef", "6_final.spef"),
        ("sdc", "6_final.sdc"),
    ):
        source = Path(config[field])
        target = frozen_dir / basename
        copy_frozen_input(source, target)
        config[field] = str(target.resolve())

    raw_manifest_path = Path(config["raw_manifest"])
    raw_manifest = read_json(raw_manifest_path)
    manifest_artifacts = raw_manifest.get("artifacts") or {}
    frozen_manifest_paths = {
        "yosys_netlist": Path(config["yosys_v"]),
        "final_spef": Path(config["spef"]),
    }
    for artifact_name, path in frozen_manifest_paths.items():
        artifact = manifest_artifacts.get(artifact_name)
        if not isinstance(artifact, dict):
            raise ValueError(f"raw manifest missing {artifact_name}")
        artifact["path"] = os.path.relpath(path, raw_manifest_path.parent)
        artifact["sha256"] = sha256_file(path)
    write_json(raw_manifest_path, raw_manifest)

    config["orfs_run_dir"] = ""
    config["output_dir"] = str((case_root / "reference_output_not_scored").resolve())
    write_json(config_path, config)

    commands.append(
        run_command(
            [
                str(python),
                str(timing_tool),
                "--config",
                str(config_path),
                "--max-paths",
                "10000",
                "--update-config",
            ],
            case_root / "logs/timing.log",
            timeout,
        )
    )
    if commands[-1]["returncode"] != 0:
        state = {"schema_version": SCHEMA_VERSION, "task_id": task_id, "status": "failed", "commands": commands}
        write_json(state_path, state)
        return state
    config = read_json(config_path)

    artifact_fields = (
        "yosys_v",
        "floorplan_def",
        "place_def",
        "cts_def",
        "route_def",
        "spef",
        "sdc",
        "timing_max_rpt",
        "timing_min_rpt",
        "timing_manifest",
        "encode_map",
        "raw_manifest",
    )
    artifacts = {}
    for field in artifact_fields:
        path = Path(str(config.get(field) or ""))
        if not path.is_file():
            raise ValueError(f"materialized input missing {field}: {path}")
        artifacts[field] = {
            "path": str(path.resolve()),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    state = {
        "schema_version": SCHEMA_VERSION,
        "runner_version": RUNNER_VERSION,
        "materializer_version": MATERIALIZER_VERSION,
        "created_at": utc_now(),
        "task_id": task_id,
        "status": "ready",
        "split": next(
            name
            for name, rows in read_json(campaign_root / "cohort.json")["splits"].items()
            if any(item["task_id"] == task_id for item in rows)
        ),
        "mapped_cells": row["mapped_cells"],
        "baseline_result_sha256": row["baseline_result_sha256"],
        "toolchain": {
            "orfs_root": os.environ.get("ORFS_ROOT", ""),
            "openroad_exe": os.environ.get("OPENROAD_EXE", ""),
            "openroad_sha256": sha256_file(Path(os.environ["OPENROAD_EXE"])),
        },
        "config": str(config_path.resolve()),
        "config_sha256": sha256_file(config_path),
        "artifacts": artifacts,
        "commands": commands,
    }
    write_json(state_path, state)
    return state


def method_config(case_root: Path, method_root: Path) -> Path:
    input_config = find_sample_config(case_root)
    config = read_json(input_config)
    config["output_dir"] = str((method_root / "generated").resolve())
    output = method_root / "method_config.json"
    write_json(output, config)
    return output


def validate_converter(path: Path) -> None:
    if not path.is_file():
        raise ValueError(f"converter not found: {path}")
    text = path.read_text(encoding="utf-8")
    matches = [token for token in FORBIDDEN_CONVERTER_TEXT if token in text]
    if matches:
        raise ValueError(f"converter contains forbidden dependencies: {matches}")
    if len(text.encode("utf-8")) > 200_000:
        raise ValueError("converter exceeds the frozen 200 KB source limit")
    ast.parse(text, filename=str(path))


def frozen_converter_gate_errors(campaign_root: Path) -> list[str]:
    errors: list[str] = []
    limits_path = campaign_root / "protocol/experiment4_resource_limits.json"
    if not limits_path.is_file():
        return ["resource limits are missing"]
    converter_limits = read_json(limits_path)["converter_development"]
    budget = int(converter_limits["total_tokens_per_model"])
    call_budget = int(converter_limits["api_calls_per_model"])
    for model_key in ("gpt", "claude", "qwen"):
        root = campaign_root / "converter_development" / model_key
        manifest_path = root / "frozen_converter_manifest.json"
        ledger_path = root / "token_ledger.json"
        if not manifest_path.is_file() or not ledger_path.is_file():
            errors.append(f"{model_key}: frozen manifest or token ledger is missing")
            continue
        manifest = read_json(manifest_path)
        ledger = read_json(ledger_path)
        converter = Path(str(manifest.get("converter") or ""))
        expected = (root / "frozen_converter.py").resolve()
        if converter.resolve() != expected or not converter.is_file():
            errors.append(f"{model_key}: frozen converter path is invalid")
            continue
        if sha256_file(converter) != manifest.get("converter_sha256"):
            errors.append(f"{model_key}: frozen converter digest mismatch")
        if manifest.get("model_key") != model_key:
            errors.append(f"{model_key}: manifest model key mismatch")
        if manifest.get("hidden_test_exposed") is not False:
            errors.append(f"{model_key}: hidden-test exposure flag is not false")
        if int(ledger.get("consumed") or 0) > budget:
            errors.append(f"{model_key}: token budget exceeded")
        if len(ledger.get("calls") or []) > call_budget:
            errors.append(f"{model_key}: API call budget exceeded")
        try:
            validate_converter(converter)
        except (OSError, SyntaxError, ValueError) as exc:
            errors.append(f"{model_key}: converter validation failed: {exc}")
    return errors


def run_one(
    row: dict[str, Any],
    campaign_root: Path,
    runtime_root: Path,
    python: Path,
    method: str,
    converter: Path | None,
    timeout: int,
) -> dict[str, Any]:
    task_id = row["task_id"]
    case_root = campaign_root / "inputs" / task_id
    input_state = read_json(case_root / "input_attestation.json")
    if input_state.get("status") != "ready":
        raise ValueError(f"input is not ready: {task_id}")
    input_attestation_sha256 = sha256_file(case_root / "input_attestation.json")
    output_root = campaign_root / "methods" / method / row.get("split", "") / task_id
    if not row.get("split"):
        output_root = campaign_root / "methods" / method / task_id
    state_path = output_root / "run_state.json"
    if state_path.is_file():
        previous_state = read_json(state_path)
        if (
            previous_state.get("status") == "completed"
            and previous_state.get("runner_version") == RUNNER_VERSION
            and previous_state.get("input_attestation_sha256") == input_attestation_sha256
        ):
            return previous_state
    output_root.mkdir(parents=True, exist_ok=True)
    if state_path.is_file():
        shutil.rmtree(output_root / "generated", ignore_errors=True)
        shutil.rmtree(output_root / "logs", ignore_errors=True)
    config = method_config(case_root, output_root)
    commands: list[dict[str, Any]] = []

    if method == "r2g-frozen-v3":
        r2g2 = runtime_root / "r2g-skills/def-graph/scripts/r2g2"
        stages = (
            "01_build_base_graph.py",
            "02_extract_features.py",
            "03_extract_labels.py",
            "04_assemble_heterograph.py",
            "05_build_stage_snapshots.py",
        )
        for stage in stages:
            command = [str(python), str(r2g2 / stage), "--config", str(config)]
            if stage == "03_extract_labels.py":
                command.append("--skip-irdrop")
            result = run_command(command, output_root / f"logs/{stage}.log", timeout)
            commands.append(result)
            if result["returncode"] != 0:
                break
    else:
        if converter is None:
            raise ValueError("a converter path is required for non-R2G methods")
        validate_converter(converter)
        commands.append(
            run_command(
                [str(python), str(converter), "--config", str(config)],
                output_root / "logs/converter.log",
                timeout,
            )
        )

    state = {
        "schema_version": SCHEMA_VERSION,
        "runner_version": RUNNER_VERSION,
        "created_at": utc_now(),
        "task_id": task_id,
        "method": method,
        "input_attestation_sha256": input_attestation_sha256,
        "converter": str(converter.resolve()) if converter else None,
        "converter_sha256": sha256_file(converter) if converter else None,
        "config": str(config.resolve()),
        "status": "completed" if commands and commands[-1]["returncode"] == 0 else "failed",
        "commands": commands,
    }
    write_json(state_path, state)
    return state


def evaluate_one(
    row: dict[str, Any], campaign_root: Path, runtime_root: Path, python: Path, method: str, timeout: int
) -> dict[str, Any]:
    task_id = row["task_id"]
    output_root = campaign_root / "methods" / method / task_id
    config = output_root / "method_config.json"
    score_path = output_root / "score.json"
    validation_path = output_root / "generated/four_stage.validation.json"
    statistics_path = output_root / "generated/statistics/four_stage_data_statistics.json"
    lint_path = output_root / "contract_lint.json"
    commands: list[dict[str, Any]] = []
    if config.is_file():
        public_contract = campaign_root / "protocol/public_contract_v2.json"
        contract_tool = runtime_root / "tools/experiment4_contract.py"
        if public_contract.is_file() and contract_tool.is_file():
            commands.append(
                run_command(
                    [
                        str(python), str(contract_tool), "lint",
                        "--contract", str(public_contract),
                        "--output-root", str(output_root / "generated"),
                        "--output", str(lint_path),
                    ],
                    output_root / "logs/contract_lint.log",
                    timeout,
                )
            )
        checks = runtime_root / "r2g-skills/def-graph/scripts/r2g2/checks"
        commands.append(
            run_command(
                [str(python), str(checks / "validate_four_stage.py"), "--config", str(config)],
                output_root / "logs/independent_validate.log",
                timeout,
            )
        )
        generated = str(read_json(config)["output_dir"])
        commands.append(
            run_command(
                [str(python), str(checks / "summarize_four_stage_graph_data.py"), "--root", generated],
                output_root / "logs/structural_summary.log",
                timeout,
            )
        )
    validation = read_json(validation_path) if validation_path.is_file() else {}
    statistics = read_json(statistics_path) if statistics_path.is_file() else {}
    lint = read_json(lint_path) if lint_path.is_file() else {}
    checks = validation.get("checks") if isinstance(validation.get("checks"), dict) else {}
    disabled_optional_checks = sorted(
        name
        for name, value in checks.items()
        if name.startswith("configured_") and value is False
    )
    applicable_checks = {
        name: value for name, value in checks.items() if name not in disabled_optional_checks
    }
    passed_checks = sum(
        value is True or (isinstance(value, (int, float)) and not isinstance(value, bool))
        for value in applicable_checks.values()
    )
    input_config = read_json(find_sample_config(campaign_root / "inputs" / task_id))
    expected_config = dict(input_config)
    expected_config["output_dir"] = str((output_root / "generated").resolve())
    config_immutable = config.is_file() and read_json(config) == expected_config
    run_state_path = output_root / "run_state.json"
    run_state = read_json(run_state_path) if run_state_path.is_file() else {}
    run_commands = run_state.get("commands") if isinstance(run_state.get("commands"), list) else []
    wall_seconds = round(sum(float(command.get("elapsed_seconds") or 0.0) for command in run_commands), 3)
    peak_rss_kib = max(
        (int((command.get("resources") or {}).get("peak_rss_kib") or 0) for command in run_commands),
        default=0,
    )
    generated_root = output_root / "generated"
    output_bytes = sum(path.stat().st_size for path in generated_root.rglob("*") if path.is_file()) if generated_root.is_dir() else 0
    strict_pass = (
        (not lint or lint.get("status") == "PASS")
        and
        validation.get("status") == "PASS"
        and statistics.get("status") == "PASS"
        and not statistics.get("structural_issues")
        and all(command["returncode"] == 0 for command in commands)
        and config_immutable
        and passed_checks == len(applicable_checks)
    )
    score = {
        "schema_version": "experiment4-graph-conversion-score-1.0",
        "created_at": utc_now(),
        "task_id": task_id,
        "method": method,
        "strict_pass": strict_pass,
        "config_immutable": config_immutable,
        "static_contract_status": lint.get("status", "NOT_CONFIGURED"),
        "static_contract_checks_passed": lint.get("checks_passed", 0),
        "static_contract_checks_total": lint.get("checks_total", 0),
        "static_contract_fraction": lint.get("fraction", 0.0),
        "static_contract_issue_count": lint.get("issue_count", 0),
        "contract_checks_passed": passed_checks,
        "contract_checks_total": len(applicable_checks),
        "contract_fraction": round(passed_checks / len(applicable_checks), 6) if applicable_checks else 0.0,
        "disabled_optional_checks": disabled_optional_checks,
        "structural_issue_count": len(statistics.get("structural_issues") or []),
        "conversion_wall_seconds": wall_seconds,
        "peak_rss_kib": peak_rss_kib,
        "output_bytes": output_bytes,
        "validation_status": validation.get("status", "MISSING"),
        "statistics_status": statistics.get("status", "MISSING"),
        "commands": commands,
    }
    write_json(score_path, score)
    return score


def execute_rows(
    rows: list[dict[str, Any]], workers: int, operation: Callable[[dict[str, Any]], dict[str, Any]]
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_rows = {executor.submit(operation, row): row for row in rows}
        for future in as_completed(future_rows):
            row = future_rows[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {"task_id": row["task_id"], "status": "exception", "error": str(exc)}
            results.append(result)
            print(f"[experiment4] {row['task_id']}: {result.get('status', result.get('strict_pass'))}", flush=True)
    return sorted(results, key=lambda item: item["task_id"])


def aggregate(campaign_root: Path, method: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    scores = []
    for row in rows:
        path = campaign_root / "methods" / method / row["task_id"] / "score.json"
        if path.is_file():
            scores.append(read_json(path))
    payload = {
        "schema_version": "experiment4-graph-conversion-aggregate-1.0",
        "created_at": utc_now(),
        "method": method,
        "expected_tasks": len(rows),
        "scored_tasks": len(scores),
        "strict_passes": sum(score.get("strict_pass") is True for score in scores),
        "mean_contract_fraction": round(
            sum(float(score.get("contract_fraction") or 0.0) for score in scores) / len(scores), 6
        ) if scores else 0.0,
        "mean_static_contract_fraction": round(
            sum(float(score.get("static_contract_fraction") or 0.0) for score in scores) / len(scores), 6
        ) if scores else 0.0,
        "scores": scores,
    }
    write_json(campaign_root / "reports" / f"{method}.aggregate.json", payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--orfs-root", type=Path, default=DEFAULT_ORFS_ROOT)
    parser.add_argument("--openroad-exe", type=Path, default=DEFAULT_OPENROAD_EXE)
    subparsers = parser.add_subparsers(dest="command", required=True)

    materialize = subparsers.add_parser("materialize")
    materialize.add_argument("--split", choices=("canary", "development", "hidden_test", "all"), default="canary")
    materialize.add_argument("--workers", type=int, default=1)
    materialize.add_argument("--timeout-seconds", type=int, default=3600)

    run = subparsers.add_parser("run")
    run.add_argument("--split", choices=("canary", "development", "hidden_test"), required=True)
    run.add_argument("--method", required=True)
    run.add_argument("--converter", type=Path)
    run.add_argument("--workers", type=int, default=1)
    run.add_argument("--timeout-seconds", type=int, default=3600)

    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("--split", choices=("canary", "development", "hidden_test"), required=True)
    evaluate.add_argument("--method", required=True)
    evaluate.add_argument("--workers", type=int, default=1)
    evaluate.add_argument("--timeout-seconds", type=int, default=1200)

    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--check-inputs", action="store_true")
    preflight.add_argument("--check-frozen-converters", action="store_true")
    preflight.add_argument(
        "--split",
        choices=("canary", "development", "hidden_test", "all"),
        default="all",
    )

    args = parser.parse_args()
    cohort = read_json(args.cohort)
    campaign_root = args.campaign_root.resolve()
    runtime_root = args.runtime_root.resolve()
    python = args.python.resolve()
    orfs_root = args.orfs_root.resolve()
    openroad_exe = args.openroad_exe.resolve()
    os.environ["ORFS_ROOT"] = str(orfs_root)
    os.environ["OPENROAD_EXE"] = str(openroad_exe)
    campaign_root.mkdir(parents=True, exist_ok=True)
    frozen_cohort = campaign_root / "cohort.json"
    if frozen_cohort.is_file() and sha256_file(frozen_cohort) != sha256_file(args.cohort):
        raise SystemExit("campaign cohort differs from requested cohort")
    if not frozen_cohort.is_file():
        shutil.copy2(args.cohort, frozen_cohort)

    if args.command == "preflight":
        required = [
            runtime_root / "r2g-skills/def-graph/scripts/r2g2/01_build_base_graph.py",
            runtime_root / "r2g-skills/def-graph/scripts/r2g2/checks/validate_four_stage.py",
            python,
            orfs_root,
            openroad_exe,
        ]
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            raise SystemExit(f"preflight missing required paths: {missing}")
        if args.check_inputs:
            for row in cohort_rows(cohort, args.split):
                state = campaign_root / "inputs" / row["task_id"] / "input_attestation.json"
                if not state.is_file():
                    raise SystemExit(f"input attestation missing: {row['task_id']}")
                errors = input_attestation_errors(read_json(state), row)
                if errors:
                    raise SystemExit(f"input attestation failed: {row['task_id']}: {errors}")
        if args.check_frozen_converters:
            errors = frozen_converter_gate_errors(campaign_root)
            if errors:
                raise SystemExit(f"frozen converter gate failed: {errors}")
        print("[experiment4] preflight PASS")
        return

    if args.command == "materialize" and args.split in {"hidden_test", "all"}:
        errors = frozen_converter_gate_errors(campaign_root)
        if errors:
            raise SystemExit(f"refusing to expose hidden test before converter freeze: {errors}")

    rows = cohort_rows(cohort, args.split)
    if args.command == "materialize":
        results = execute_rows(
            rows,
            args.workers,
            lambda row: materialize_one(row, campaign_root, runtime_root, python, args.timeout_seconds),
        )
        if any(result.get("status") != "ready" for result in results):
            raise SystemExit(1)
        return
    if args.command == "run":
        converter = args.converter.resolve() if args.converter else None
        results = execute_rows(
            rows,
            args.workers,
            lambda row: run_one(
                row, campaign_root, runtime_root, python, args.method, converter, args.timeout_seconds
            ),
        )
        if any(result.get("status") != "completed" for result in results):
            raise SystemExit(1)
        return
    scores = execute_rows(
        rows,
        args.workers,
        lambda row: evaluate_one(row, campaign_root, runtime_root, python, args.method, args.timeout_seconds),
    )
    report = aggregate(campaign_root, args.method, rows)
    print(
        f"[experiment4] {args.method}: strict={report['strict_passes']}/{report['expected_tasks']} "
        f"contract={report['mean_contract_fraction']:.3f}"
    )
    if len(scores) != len(rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
