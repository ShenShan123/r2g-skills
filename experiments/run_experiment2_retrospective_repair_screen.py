#!/usr/bin/env python3
"""Screen Experiment 2 fixtures for fixed-target repair-needed behavior.

The screen is intentionally deterministic.  For each fixture it runs Default
ORFS twice at the frozen initial utilization, then runs one independently
materialized project with the preregistered bounded-utilization action.  No LLM,
R2G Recipe, or historical output is consulted during execution or grading.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


REPO = Path(__file__).resolve().parents[1]
CONTROLLER = REPO / "tools" / "run_experiment2_signoff.py"
PRIMARY_NAMESPACE = "openai-vanilla"
REPEAT_NAMESPACE = "qwen-vanilla"
FEASIBILITY_NAMESPACE = "deepseek-vanilla"
PHYSICAL_GATES = {"flow", "route", "drc", "lvs", "timing", "antenna", "rcx"}
ENVIRONMENT_MARKERS = (
    "command not found",
    "no such file or directory",
    "license checkout failed",
    "modulenotfounderror:",
    "importerror:",
    "error: can't open display",
)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_action(
    cohort: Path,
    task_spec: Path,
    campaign_root: Path,
    method: str,
    fixture: str,
    action: str,
    log_path: Path,
    *extra: str,
) -> None:
    command = [
        sys.executable,
        str(CONTROLLER),
        "--cohort", str(cohort),
        "--task-spec", str(task_spec),
        action,
        "--campaign-root", str(campaign_root),
        "--method", method,
        "--fixture", fixture,
        *extra,
    ]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as stream:
        result = subprocess.run(command, cwd=REPO, stdout=stream, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f"{action} failed for {method}/{fixture}; see {log_path}")


def latest_validation(campaign_root: Path, method: str, fixture: str) -> Path:
    paths = sorted(
        (campaign_root / "methods" / method / fixture / "validations").glob("validation_*.json")
    )
    if not paths:
        raise RuntimeError(f"no validation produced for {method}/{fixture}")
    return paths[-1]


def latest_flow_event(campaign_root: Path, method: str, fixture: str) -> dict[str, Any]:
    path = campaign_root / "methods" / method / fixture / "attempts.jsonl"
    if not path.is_file():
        return {}
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("event") == "flow":
            rows.append(row)
    return rows[-1] if rows else {}


def normalized_signature(result: dict[str, Any]) -> list[str]:
    """Return only observed physical failures, not absent downstream reports."""
    metrics = result.get("metrics") or {}
    signature: list[str] = []
    numeric_failures = (
        ("route", "route_violations", lambda value: value > 0),
        ("drc", "drc_violations", lambda value: value > 0),
        ("lvs", "lvs_mismatches", lambda value: value > 0),
        ("setup", "setup_wns_ns", lambda value: value < 0),
        ("hold", "hold_wns_ns", lambda value: value < 0),
        ("antenna", "antenna_violations", lambda value: value > 0),
    )
    for label, key, predicate in numeric_failures:
        value = metrics.get(key)
        if isinstance(value, (int, float)) and predicate(value):
            signature.append(label)
    if signature:
        return sorted(signature)
    failed = {
        name for name, gate in (result.get("gates") or {}).items()
        if gate.get("status") != "pass"
    }
    # A completed flow with a nonnumeric check failure is still a physical result.
    return sorted((failed & PHYSICAL_GATES) - {"flow"})


def validation_record(
    campaign_root: Path,
    method: str,
    fixture: str,
    frequency_mhz: float,
    utilization: float,
) -> dict[str, Any]:
    path = latest_validation(campaign_root, method, fixture)
    result = read_json(path)
    flow = latest_flow_event(campaign_root, method, fixture)
    stage_rows: list[dict[str, Any]] = []
    run_dir = Path(str(flow.get("run_dir") or ""))
    stage_log = run_dir / "stage_log.jsonl"
    if stage_log.is_file():
        for line in stage_log.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                stage_rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    failed_stage = None
    for stage in reversed(stage_rows):
        if int(stage.get("status") or 0) != 0:
            failed_stage = str(stage.get("stage") or "unknown")
            break
    log_dir = campaign_root / "methods" / method / fixture / "logs"
    recent_logs = sorted(log_dir.glob("flow_*.log"))
    log_tail = ""
    if recent_logs:
        log_tail = recent_logs[-1].read_text(encoding="utf-8", errors="replace")[-20000:].lower()
    environment_failure = any(marker in log_tail for marker in ENVIRONMENT_MARKERS)
    failed_gates = sorted(
        name for name, gate in (result.get("gates") or {}).items()
        if gate.get("status") != "pass"
    )
    signature = normalized_signature(result)
    if flow.get("returncode") not in (None, 0):
        signature = [] if failed_stage == "synth" else [f"flow:{failed_stage or 'unknown'}"]
    return {
        "method_namespace": method,
        "frequency_mhz": frequency_mhz,
        "core_utilization": utilization,
        "flow_returncode": flow.get("returncode"),
        "flow_failure_stage": failed_stage,
        "flow_elapsed_seconds": flow.get("elapsed_seconds"),
        "strict_clean": result.get("strict_clean") is True,
        "environment_failure": environment_failure,
        "normalized_physical_signature": signature,
        "failed_gates": failed_gates,
        "metrics": result.get("metrics") or {},
        "failures": result.get("failures") or [],
        "validation_path": str(path.resolve()),
        "validation_sha256": sha256_file(path),
    }


def execute_point(
    cohort: Path,
    task_spec: Path,
    campaign_root: Path,
    method: str,
    fixture: str,
    frequency_mhz: float,
    utilization: float,
    label: str,
) -> dict[str, Any]:
    logs = campaign_root / "retrospective_screen" / "logs" / fixture / label
    run_action(
        cohort, task_spec, campaign_root, method, fixture, "set-frequency",
        logs / "set_frequency.log", "--frequency-mhz", str(frequency_mhz),
    )
    run_action(
        cohort, task_spec, campaign_root, method, fixture, "set-core-utilization",
        logs / "set_core_utilization.log", "--core-utilization", str(utilization),
    )
    run_action(
        cohort, task_spec, campaign_root, method, fixture, "run-flow",
        logs / "run_flow.log", "--timeout-seconds", "7200",
    )
    run_action(
        cohort, task_spec, campaign_root, method, fixture, "validate-checkpoint",
        logs / "validate.log",
    )
    return validation_record(campaign_root, method, fixture, frequency_mhz, utilization)


def baseline_rejection(primary: dict[str, Any], repeat: dict[str, Any] | None = None) -> str | None:
    if primary["environment_failure"] or (repeat and repeat["environment_failure"]):
        return "rejected_environment_failure"
    if "protected_inputs" in primary["failed_gates"] or (
        repeat and "protected_inputs" in repeat["failed_gates"]
    ):
        return "rejected_protected_input_failure"
    if primary["strict_clean"] or (repeat and repeat["strict_clean"]):
        return "rejected_baseline_failure_not_reproduced"
    if primary.get("flow_failure_stage") == "synth" or (
        repeat and repeat.get("flow_failure_stage") == "synth"
    ):
        return "rejected_frontend_or_synthesis_failure"
    if repeat is None:
        return None
    first = set(primary["normalized_physical_signature"])
    second = set(repeat["normalized_physical_signature"])
    if not first or first != second:
        return "rejected_inconsistent_physical_failure"
    return None


def answerability_rejection(primary: dict[str, Any], task_spec: dict[str, Any]) -> str | None:
    """Reject a task whose reference baseline leaves no useful repair budget."""
    total = (task_spec.get("budgets") or {}).get("wall_time_seconds_per_method_design")
    elapsed = primary.get("flow_elapsed_seconds")
    if not isinstance(total, (int, float)) or total <= 0:
        raise ValueError("task spec lacks a positive per-method/design wall-time budget")
    if isinstance(elapsed, (int, float)) and elapsed > total / 3:
        return "rejected_reference_baseline_exceeds_answerability_budget"
    return None


def classify(
    primary: dict[str, Any],
    repeat: dict[str, Any],
    feasibility: dict[str, Any],
    *,
    controlled: bool = False,
) -> str:
    rejection = baseline_rejection(primary, repeat)
    if rejection:
        return rejection
    if feasibility["environment_failure"]:
        return "rejected_environment_failure"
    if "protected_inputs" in feasibility["failed_gates"]:
        return "rejected_protected_input_failure"
    if not feasibility["strict_clean"]:
        return "rejected_no_independent_strict_clean_feasibility"
    return "admitted_controlled_repair_challenge" if controlled else "admitted_repair_needed"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--task-spec", type=Path, required=True)
    parser.add_argument("--fixture", action="append", dest="fixtures")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    campaign_root = args.campaign_root.resolve()
    cohort_path = args.cohort.resolve()
    task_spec = args.task_spec.resolve()
    cohort = read_json(cohort_path)
    task_spec_document = read_json(task_spec)
    by_id = {item["id"]: item for item in cohort["fixtures"]}
    fixture_ids = args.fixtures or [item["id"] for item in cohort["fixtures"]]
    output = args.output or campaign_root / "retrospective_screen" / "screen_results.json"
    prior = read_json(output) if output.is_file() else {}
    completed = {item["fixture_id"]: item for item in prior.get("results", [])}
    report = {
        "schema_version": "1.0",
        "screening_method": "two_default_orfs_repeats_plus_preregistered_feasibility",
        "campaign_root": str(campaign_root),
        "cohort": {"path": str(cohort_path), "sha256": sha256_file(cohort_path)},
        "task_spec": {"path": str(task_spec), "sha256": sha256_file(task_spec)},
        "answerability_policy": {
            "reference_baseline_max_fraction_of_method_budget": 1 / 3,
            "method_design_wall_time_seconds": (
                task_spec_document.get("budgets") or {}
            ).get("wall_time_seconds_per_method_design"),
        },
        "namespaces": {
            "baseline_primary": PRIMARY_NAMESPACE,
            "baseline_repeat": REPEAT_NAMESPACE,
            "feasibility": FEASIBILITY_NAMESPACE,
        },
        "results": [],
    }
    for fixture_id in fixture_ids:
        controlled = bool((by_id.get(fixture_id) or {}).get("controlled_challenge"))
        if fixture_id in completed:
            row = completed[fixture_id]
            for key in ("baseline_primary", "baseline_repeat", "independent_feasibility"):
                point = row.get(key)
                if point:
                    row[key] = validation_record(
                        campaign_root,
                        point["method_namespace"],
                        fixture_id,
                        float(point["frequency_mhz"]),
                        float(point["core_utilization"]),
                    )
            primary = row["baseline_primary"]
            repeat = row.get("baseline_repeat")
            feasibility = row.get("independent_feasibility")
            rejection = answerability_rejection(primary, task_spec_document) or baseline_rejection(
                primary, repeat
            )
            row["admission_status"] = (
                rejection or classify(primary, repeat, feasibility, controlled=controlled)
                if repeat and feasibility else rejection or "screen_incomplete"
            )
            print(f"[screen] {fixture_id}: rescored {row['admission_status']}", flush=True)
            report["results"].append(row)
            continue
        fixture = by_id[fixture_id]
        screening = fixture["screening"]
        initial = float(fixture["footprint_policy"]["initial"])
        repair = float(screening["feasibility_core_utilization"])
        frequency = float(screening["target_frequency_mhz"])
        print(f"[screen] {fixture_id}: baseline primary", flush=True)
        primary = execute_point(
            cohort_path, task_spec, campaign_root, PRIMARY_NAMESPACE,
            fixture_id, frequency, initial, "baseline_primary",
        )
        rejection = answerability_rejection(primary, task_spec_document) or baseline_rejection(primary)
        if rejection:
            row = {
                "fixture_id": fixture_id,
                "size_bin": fixture["size_bin"],
                "historical_nomination": screening.get("historical_symptom"),
                "baseline_primary": primary,
                "baseline_repeat": None,
                "independent_feasibility": None,
                "admission_status": rejection,
            }
            report["results"].append(row)
            write_json_atomic(output, report)
            print(f"[screen] {fixture_id}: {rejection}", flush=True)
            continue
        print(f"[screen] {fixture_id}: baseline repeat", flush=True)
        repeat = execute_point(
            cohort_path, task_spec, campaign_root, REPEAT_NAMESPACE,
            fixture_id, frequency, initial, "baseline_repeat",
        )
        rejection = baseline_rejection(primary, repeat)
        if rejection:
            row = {
                "fixture_id": fixture_id,
                "size_bin": fixture["size_bin"],
                "historical_nomination": screening.get("historical_symptom"),
                "baseline_primary": primary,
                "baseline_repeat": repeat,
                "independent_feasibility": None,
                "admission_status": rejection,
            }
            report["results"].append(row)
            write_json_atomic(output, report)
            print(f"[screen] {fixture_id}: {rejection}", flush=True)
            continue
        print(f"[screen] {fixture_id}: independent feasibility", flush=True)
        feasibility = execute_point(
            cohort_path, task_spec, campaign_root, FEASIBILITY_NAMESPACE,
            fixture_id, frequency, repair, "independent_feasibility",
        )
        row = {
            "fixture_id": fixture_id,
            "size_bin": fixture["size_bin"],
            "historical_nomination": screening.get("historical_symptom"),
            "baseline_primary": primary,
            "baseline_repeat": repeat,
            "independent_feasibility": feasibility,
            "admission_status": classify(primary, repeat, feasibility, controlled=controlled),
        }
        report["results"].append(row)
        write_json_atomic(output, report)
        print(f"[screen] {fixture_id}: {row['admission_status']}", flush=True)
    write_json_atomic(output, report)
    print(json.dumps({"output": str(output.resolve()), "results": len(report["results"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
