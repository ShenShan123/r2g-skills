#!/usr/bin/env python3
"""Prepare, execute, validate, lock, and grade Experiment 2 Pilot campaigns."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from experiments.experiment2_signoff import (  # noqa: E402
    DEFAULT_COHORT,
    DEFAULT_TASK_SPEC,
    METHOD_IDS,
    Experiment2Error,
    append_jsonl,
    copy_checkpoint,
    evaluate_checkpoint,
    fixture_by_id,
    git_text,
    import_experiment1_verifier,
    latest_backend_run,
    lint_cohort,
    load_cohort,
    materialize_project,
    method_runtime_state,
    now_iso,
    project_paths,
    protected_input_failures,
    public_fixture_id,
    read_json,
    resource_intervention,
    run,
    run_strict_measurement,
    set_bounded_core_utilization,
    set_target_frequency,
    sha256_file,
    sha256_tree,
    signoff_environment,
    target_period,
    verify_campaign_bindings,
    write_json_atomic,
)


DEFAULT_ROUTES = REPO / "docs" / "experiments" / "signoff" / "experiment2_model_routes.json"


def campaign_manifest(root: Path) -> dict:
    value = read_json(root / "experiment2_campaign.json")
    if not isinstance(value, dict):
        raise Experiment2Error(f"campaign is not prepared: {root}")
    return value


def runtime_skills(
    root: Path,
    method_id: str | None = None,
    fixture_id: str | None = None,
) -> Path:
    if method_id == "full-r2g" and fixture_id:
        return project_paths(root, method_id, fixture_id)["method"] / "agent_runtime" / "r2g-skills"
    return root / "runtime" / "r2g-skills"


def file_record(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": sha256_file(path)}


def copy_writable_seed(source: Path, destination: Path) -> None:
    """Copy an immutable seed into a private, owner-writable working file."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    destination.chmod(stat.S_IRUSR | stat.S_IWUSR)


def full_r2g_environment(root: Path, fixture_id: str, project: Path) -> dict[str, str]:
    """Bind every Full R2G knowledge entrypoint to one fixture-private store."""
    skills = runtime_skills(root, "full-r2g", fixture_id)
    env = signoff_environment(skills, root, project)
    knowledge = skills / "signoff-loop" / "knowledge"
    env["R2G_KNOWLEDGE_DB"] = str(knowledge / "knowledge.sqlite")
    env["R2G_HEURISTICS_PATH"] = str(knowledge / "heuristics.json")
    env["R2G_EXP2_CAMPAIGN_ROOT"] = str(root)
    env["R2G_EXP2_FIXTURE_ID"] = fixture_id
    env["R2G_EXP2_CONTROLLER"] = str(Path(__file__).resolve())
    repair_policy = project_paths(root, "full-r2g", fixture_id)["method"] / "repair_action_policy.json"
    if repair_policy.is_file():
        env["R2G_REPAIR_ACTION_POLICY_FILE"] = str(repair_policy)
    return env


def materialize_full_r2g_repair_policy(root: Path, fixture: dict) -> None:
    """Translate the frozen cohort policy into the Agent's exact action contract.

    Explicit-area bounds remain enforced by Experiment 2's existing perimeter
    policy-check because the Agent contract intentionally accepts only exact
    config values or scalar numeric ranges.
    """
    source = fixture.get("action_policy") or {}
    if source.get("explicit_area"):
        return
    footprint = fixture.get("footprint_policy") or {}
    numeric = {}
    for knob in source.get("allowed_numeric_knobs") or []:
        bounds = {}
        if knob == "CORE_UTILIZATION":
            if footprint.get("minimum") is not None:
                bounds["minimum"] = footprint["minimum"]
            if footprint.get("maximum") is not None:
                bounds["maximum"] = footprint["maximum"]
        numeric[knob] = bounds
    policy = {
        "schema_version": "r2g-repair-action-policy-1.0",
        "allowed_numeric_knobs": numeric,
        "allowed_string_knobs": source.get("allowed_string_knobs") or {},
        "allowed_sdc_edits": {},
    }
    destination = project_paths(root, "full-r2g", fixture["id"])["method"] / "repair_action_policy.json"
    write_json_atomic(destination, policy)


def install_full_r2g_policy_guards(skills: Path) -> None:
    """Put the experiment policy in front of every native Full R2G flow entry."""
    flow = skills / "signoff-loop" / "scripts" / "flow"
    if not (flow / "run_orfs.sh").is_file():
        raise Experiment2Error("Full R2G runtime lacks the required run_orfs.sh entrypoint")
    for name in ("run_orfs.sh", "resume_orfs.sh"):
        entry = flow / name
        real = flow / f".experiment2_{name[:-3]}_real.sh"
        if real.exists():
            continue
        if not entry.is_file():
            continue
        entry.rename(real)
        entry.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            ': "${R2G_EXP2_CAMPAIGN_ROOT:?missing Experiment 2 campaign binding}"\n'
            ': "${R2G_EXP2_FIXTURE_ID:?missing Experiment 2 fixture binding}"\n'
            ': "${R2G_EXP2_CONTROLLER:?missing Experiment 2 controller binding}"\n'
            '"${PYTHON_BIN:-python3}" "$R2G_EXP2_CONTROLLER" policy-check '
            '--campaign-root "$R2G_EXP2_CAMPAIGN_ROOT" --method full-r2g '
            '--fixture "$R2G_EXP2_FIXTURE_ID" --record-flow-attempt\n'
            f'exec bash "{real}" "$@"\n',
            encoding="utf-8",
        )
        entry.chmod(0o755)


def clone_fixture(root: Path, fixture: dict) -> Path:
    destination = root / "sources" / fixture["id"]
    candidate = fixture["candidate"]
    clone_source = candidate["repo_url"]
    local_checkout = candidate.get("source_checkout_path")
    clone_options = ["git", "clone", "--filter=blob:none", "--no-checkout"]
    if local_checkout:
        mirror = Path(local_checkout).resolve()
        if not (mirror / ".git").exists():
            raise Experiment2Error(f"source checkout is not a Git checkout: {mirror}")
        expected = candidate["repo_url"].removesuffix(".git").rstrip("/")
        observed = git_text("remote", "get-url", "origin", cwd=mirror).removesuffix(".git").rstrip("/")
        if observed != expected:
            raise Experiment2Error(
                f"source checkout origin mismatch for {fixture['id']}: {observed} != {expected}"
            )
        clone_source = str(mirror)
        clone_options = ["git", "clone", "--shared", "--no-checkout"]
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        result = run([*clone_options, clone_source, str(destination)])
        if result.returncode:
            raise Experiment2Error((result.stderr or result.stdout or "clone failed").strip())
    if not (destination / ".git").exists():
        raise Experiment2Error(f"source is not a Git checkout: {destination}")
    result = run(["git", "checkout", "--detach", candidate["commit"]], cwd=destination)
    if result.returncode:
        raise Experiment2Error((result.stderr or result.stdout or "checkout failed").strip())
    if git_text("rev-parse", "HEAD", cwd=destination) != candidate["commit"]:
        raise Experiment2Error(f"commit mismatch for {fixture['id']}")
    return destination


def prepare(args: argparse.Namespace) -> None:
    root = args.campaign_root.resolve()
    if root.exists() and any(root.iterdir()):
        raise Experiment2Error(f"campaign root must be absent or empty: {root}")
    cohort = load_cohort(args.cohort)
    task = read_json(args.task_spec)
    if not isinstance(task, dict):
        raise Experiment2Error(f"cannot read task spec: {args.task_spec}")
    if not args.routes.is_file():
        raise Experiment2Error(f"cannot read model routes: {args.routes}")
    root.mkdir(parents=True, exist_ok=True)
    v2_protocol = task.get("protocol_version") == "baseline_first_v2"
    runtime = root / "runtime" / "r2g-skills"
    shutil.copytree(REPO / "r2g-skills", runtime, symlinks=True)
    shipped_knowledge_dir = runtime / "signoff-loop" / "knowledge"
    shipped_knowledge = shipped_knowledge_dir / "knowledge.sqlite"
    shipped_heuristics = shipped_knowledge_dir / "heuristics.json"
    if not shipped_knowledge.is_file() or not shipped_heuristics.is_file():
        raise Experiment2Error("shipped Full R2G knowledge or heuristics seed is missing")
    frozen_dir = root / "frozen" / "full_r2g_knowledge"
    frozen_dir.mkdir(parents=True, exist_ok=True)
    frozen_knowledge = frozen_dir / "knowledge.sqlite"
    frozen_heuristics = frozen_dir / "heuristics.json"
    shutil.copy2(shipped_knowledge, frozen_knowledge)
    shutil.copy2(shipped_heuristics, frozen_heuristics)
    frozen_knowledge.chmod(0o444)
    frozen_heuristics.chmod(0o444)
    verifier = import_experiment1_verifier()
    source_records = {}
    for fixture in cohort["fixtures"]:
        print(f"[prepare] {fixture['id']}: materializing pinned source", flush=True)
        source = clone_fixture(root, fixture)
        evidence = verifier.verify_candidate_inputs(fixture["candidate"], source)
        expected = fixture["qualification"]["closure_sha256"]
        if evidence.get("closure_sha256") != expected:
            raise Experiment2Error(
                f"{fixture['id']}: source closure differs from Experiment 1 qualification"
            )
        source_records[fixture["id"]] = {
            "path": str(source),
            "commit": fixture["candidate"]["commit"],
            "closure_sha256": evidence["closure_sha256"],
        }
        for method_id in METHOD_IDS:
            paths = project_paths(root, method_id, fixture["id"])
            materialize_project(source, paths["project"], fixture)
            state_dir = method_runtime_state(paths["project"])
            state_dir.mkdir(parents=True, exist_ok=True)
            if method_id == "full-r2g":
                isolated_runtime = runtime_skills(root, method_id, fixture["id"])
                shutil.copytree(runtime, isolated_runtime, symlinks=True)
                if v2_protocol:
                    install_full_r2g_policy_guards(isolated_runtime)
                working_knowledge = isolated_runtime / "signoff-loop" / "knowledge" / "knowledge.sqlite"
                working_heuristics = isolated_runtime / "signoff-loop" / "knowledge" / "heuristics.json"
                copy_writable_seed(frozen_knowledge, working_knowledge)
                copy_writable_seed(frozen_heuristics, working_heuristics)
                initial_knowledge = {
                    "environment_db": file_record(working_knowledge),
                    "environment_heuristics": file_record(working_heuristics),
                    "runtime_default_db": file_record(working_knowledge),
                    "runtime_default_heuristics": file_record(working_heuristics),
                }
                materialize_full_r2g_repair_policy(root, fixture)
            else:
                initial_knowledge = {"state": "empty"}
            write_json_atomic(
                paths["method"] / "campaign_state.json",
                {
                    "schema_version": "1.0",
                    "method_id": method_id,
                    "fixture_id": fixture["id"],
                    "status": "prepared",
                    "prepared_at": now_iso(),
                    "best_checkpoint": None,
                    "initial_knowledge": initial_knowledge,
                    "initial_config_sha256": sha256_file(paths["project"] / "constraints" / "config.mk"),
                    "protocol_version": task.get("protocol_version") or "legacy",
                },
            )
        print(f"[prepare] {fixture['id']}: closure verified and four projects created", flush=True)
    orfs = Path.home() / "r2g_toolchain" / "OpenROAD-flow-scripts"
    manifest = {
        "schema_version": "1.0",
        "experiment_id": "r2g-exp2-orfs-signoff-pilot",
        "campaign_id": root.name,
        "prepared_at": now_iso(),
        "agent_commit": git_text("rev-parse", "HEAD", cwd=REPO),
        "agent_dirty": bool(git_text("status", "--porcelain", cwd=REPO)),
        "agent_diff_sha256": hashlib.sha256(
            git_text("diff", "--binary", cwd=REPO).encode("utf-8")
        ).hexdigest(),
        "cohort": {"path": str(args.cohort.resolve()), "sha256": sha256_file(args.cohort)},
        "task_spec": {"path": str(args.task_spec.resolve()), "sha256": sha256_file(args.task_spec)},
        "model_routes": {"path": str(args.routes.resolve()), "sha256": sha256_file(args.routes)},
        "implementations": {
            name: {"path": str(path.resolve()), "sha256": sha256_file(path)}
            for name, path in {
                "evaluator": REPO / "tools" / "experiment2_signoff.py",
                "controller": Path(__file__),
                "vanilla_runner": REPO / "tools" / "run_experiment2_vanilla_method.py",
                "batch_runner": REPO / "tools" / "run_experiment2_batch.py",
            }.items()
        },
        "platform": "sky130hd",
        "methods": list(METHOD_IDS),
        "sources": source_records,
        "toolchain": {
            "orfs_root": str(orfs),
            "orfs_commit": git_text("rev-parse", "HEAD", cwd=orfs),
            "cpu_cores": 4,
        },
        "knowledge_policy": {
            "full_r2g_seed": file_record(frozen_knowledge),
            "full_r2g_heuristics_seed": file_record(frozen_heuristics),
            "vanilla_initial_state": "empty",
            "isolation_unit": "method_x_fixture",
            "cross_fixture_learning": False,
            "full_r2g_runtime_isolated_per_fixture": True,
        },
        "protocol_version": task.get("protocol_version") or "legacy",
    }
    write_json_atomic(root / "experiment2_campaign.json", manifest)
    print(f"Prepared Experiment 2 Pilot: {root}")


def ensure_bound(root: Path, args: argparse.Namespace) -> tuple[dict, dict]:
    manifest = verify_campaign_bindings(root)
    cohort_path = Path(manifest["cohort"]["path"])
    return manifest, load_cohort(cohort_path)


def fixture_and_project(args: argparse.Namespace) -> tuple[Path, dict, Path]:
    root = args.campaign_root.resolve()
    _, cohort = ensure_bound(root, args)
    if args.method not in METHOD_IDS:
        raise Experiment2Error(f"unknown method: {args.method}")
    fixture = fixture_by_id(cohort, args.fixture)
    return root, fixture, project_paths(root, args.method, args.fixture)["project"]


def result_context(fixture: dict, method_dir: Path) -> dict:
    """Attach cohort identity and measured method cost to every grade row."""
    submission_name = (
        "full_r2g_submission.json"
        if method_dir.parent.name == "full-r2g"
        else "vanilla_submission.json"
    )
    submission = read_json(method_dir / submission_name, {}) or {}
    return {
        "role": fixture.get("role"),
        "family_id": fixture.get("family_id"),
        "public_id": public_fixture_id(fixture),
        "objective_track": fixture.get("objective_track") or "fixed_goal",
        "strict_clean_scope": fixture.get("strict_clean_scope"),
        "resource_usage": submission.get("resource_usage") or {},
    }


def set_frequency(args: argparse.Namespace) -> None:
    root, fixture, project = fixture_and_project(args)
    value = set_target_frequency(project, fixture, args.frequency_mhz)
    append_jsonl(
        project_paths(root, args.method, args.fixture)["attempts"],
        {"event": "set_frequency", "at": now_iso(), **value},
    )
    print(json.dumps(value, indent=2))


def set_core_utilization(args: argparse.Namespace) -> None:
    root, fixture, project = fixture_and_project(args)
    value = set_bounded_core_utilization(project, fixture, args.core_utilization)
    append_jsonl(
        project_paths(root, args.method, args.fixture)["attempts"],
        {"event": "set_core_utilization", "at": now_iso(), **value},
    )
    print(json.dumps(value, indent=2))


def run_flow(args: argparse.Namespace) -> None:
    root, fixture, project = fixture_and_project(args)
    failures = protected_input_failures(project, fixture)
    if failures:
        raise Experiment2Error("protected input violation:\n- " + "\n- ".join(failures))
    paths = project_paths(root, args.method, args.fixture)
    period = target_period(project)
    attempt = sum(1 for _ in paths["attempts"].open(encoding="utf-8")) + 1 if paths["attempts"].exists() else 1
    variant = f"exp2_{args.method.replace('-', '_')}_{args.fixture}_{attempt:02d}"
    command = [
        "bash", str(runtime_skills(root) / "signoff-loop" / "scripts" / "flow" / "run_orfs.sh"),
        str(project), "sky130hd", variant,
    ]
    env = signoff_environment(runtime_skills(root), root, project)
    env["ORFS_TIMEOUT"] = str(args.timeout_seconds)
    started = time.monotonic()
    result = run(command, cwd=root, env=env, log=paths["logs"] / f"flow_{attempt:02d}.log")
    row = {
        "event": "flow", "at": now_iso(), "attempt": attempt,
        "period_ns": period, "frequency_mhz": 1000.0 / period if period else None,
        "flow_variant": variant, "returncode": result.returncode,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "run_dir": str(latest_backend_run(project)) if latest_backend_run(project) else None,
    }
    append_jsonl(paths["attempts"], row)
    print(json.dumps(row, indent=2))


def policy_check(args: argparse.Namespace) -> None:
    root, fixture, project = fixture_and_project(args)
    failures = protected_input_failures(project, fixture)
    if failures:
        raise Experiment2Error("protected input violation:\n- " + "\n- ".join(failures))
    response = {"status": "pass", "public_id": public_fixture_id(fixture)}
    if args.record_flow_attempt:
        if args.method != "full-r2g":
            raise Experiment2Error("flow-attempt authorization is only used by Full R2G")
        manifest = verify_campaign_bindings(root)
        task = read_json(Path(manifest["task_spec"]["path"]), {}) or {}
        maximum = int((task.get("budgets") or {}).get("max_orfs_flows_per_method_design", 4))
        paths = project_paths(root, args.method, args.fixture)
        ledger = paths["method"] / "policy_flow_attempts.jsonl"
        prior = []
        if ledger.is_file():
            for line in ledger.read_text(encoding="utf-8").splitlines():
                try:
                    prior.append(json.loads(line))
                except json.JSONDecodeError:
                    raise Experiment2Error("Full R2G flow-attempt ledger is malformed")
        if len(prior) >= maximum:
            raise Experiment2Error(f"Full R2G ORFS flow-attempt limit reached ({maximum})")
        state = read_json(paths["method"] / "campaign_state.json", {}) or {}
        config_digest = sha256_file(project / "constraints" / "config.mk")
        if not prior and config_digest != state.get("initial_config_sha256"):
            raise Experiment2Error(
                "baseline-first protocol: Full R2G changed config before the mandatory baseline"
            )
        record = {
            "event": "flow_authorized",
            "at": now_iso(),
            "attempt": len(prior) + 1,
            "maximum": maximum,
            "config_sha256": config_digest,
            "baseline": not prior,
        }
        append_jsonl(ledger, record)
        response["flow_authorization"] = record
    print(json.dumps(response, indent=2))


def validate(args: argparse.Namespace) -> None:
    root, fixture, project = fixture_and_project(args)
    paths = project_paths(root, args.method, args.fixture)
    index = len(list(paths["validations"].glob("validation_*.json"))) + 1
    started = time.monotonic()
    result = run_strict_measurement(
        project, fixture, runtime_skills(root, args.method, args.fixture), root,
        paths["logs"] / f"validation_{index:02d}",
    )
    result["validation_elapsed_seconds"] = round(time.monotonic() - started, 3)
    output = paths["validations"] / f"validation_{index:02d}.json"
    write_json_atomic(output, result)
    append_jsonl(paths["attempts"], {"event": "validate", "at": now_iso(), "result": str(output), "strict_clean": result["strict_clean"], "metrics": result["metrics"]})
    print(json.dumps(result, indent=2))


def lock(args: argparse.Namespace) -> None:
    root, fixture, project = fixture_and_project(args)
    result = evaluate_checkpoint(project, fixture)
    if not result["strict_clean"]:
        raise Experiment2Error("only a currently strict-clean checkpoint can be locked")
    frequency = result["metrics"]["frequency_mhz"]
    paths = project_paths(root, args.method, args.fixture)
    destination = paths["checkpoints"] / f"{frequency:.6f}MHz" / "project"
    if not destination.exists():
        copy_checkpoint(project, destination)
    record = {
        "locked_at": now_iso(), "method_id": args.method,
        "fixture_id": args.fixture, "frequency_mhz": frequency,
        "project": str(destination), "tree_sha256": sha256_tree(destination),
    }
    write_json_atomic(destination.parent / "checkpoint.json", record)
    state_path = paths["method"] / "campaign_state.json"
    state = read_json(state_path, {}) or {}
    if not state.get("best_checkpoint"):
        state["best_checkpoint"] = record
    state["status"] = "checkpoint_locked"
    write_json_atomic(state_path, state)
    print(json.dumps(record, indent=2))


def grade(args: argparse.Namespace) -> None:
    root, cohort = ensure_bound(args.campaign_root.resolve(), args)
    report = {"schema_version": "1.0", "graded_at": now_iso(), "campaign": str(args.campaign_root.resolve()), "results": []}
    for method_id in METHOD_IDS:
        for fixture in cohort["fixtures"]:
            paths = project_paths(args.campaign_root.resolve(), method_id, fixture["id"])
            state = read_json(paths["method"] / "campaign_state.json", {}) or {}
            checkpoint = state.get("best_checkpoint") or {}
            context = result_context(fixture, paths["method"])
            if not checkpoint:
                report["results"].append({
                    "method_id": method_id,
                    "fixture_id": fixture["id"],
                    **context,
                    "strict_clean": False,
                    "status": "no_locked_checkpoint",
                })
                continue
            source = Path(checkpoint["project"])
            final_root = args.campaign_root.resolve() / "final_evaluation" / method_id / fixture["id"]
            final_project = final_root / "project"
            if final_project.exists():
                shutil.rmtree(final_project)
            copy_checkpoint(source, final_project)
            shutil.rmtree(final_project / "reports", ignore_errors=True)
            (final_project / "reports").mkdir(parents=True, exist_ok=True)
            result = run_strict_measurement(
                final_project,
                fixture,
                runtime_skills(args.campaign_root.resolve(), method_id, fixture["id"]),
                args.campaign_root.resolve(),
                final_root / "logs",
            )
            result.update({
                "method_id": method_id,
                **context,
                "locked_tree_sha256": checkpoint["tree_sha256"],
                "claimed_frequency_mhz": checkpoint["frequency_mhz"],
                "resource_intervention": resource_intervention(final_project, fixture),
            })
            write_json_atomic(final_root / "final_evaluation.json", result)
            report["results"].append(result)
    rows = report["results"]
    report["summary"] = {
        "strict_clean_scope": "fixed_target_physical_signoff",
        "development_only": cohort.get("cohort_kind") == "family_contract_smoke",
        "by_method": {
            method_id: {
                "strict_clean": sum(row.get("strict_clean") is True for row in rows if row["method_id"] == method_id),
                "total": sum(row["method_id"] == method_id for row in rows),
                "repair_recovered": sum(row.get("strict_clean") is True and row.get("role") == "repair_needed" for row in rows if row["method_id"] == method_id),
                "repair_total": sum(row.get("role") == "repair_needed" for row in rows if row["method_id"] == method_id),
                "sentinel_preserved": sum(row.get("strict_clean") is True and row.get("role") == "clean_sentinel" for row in rows if row["method_id"] == method_id),
                "sentinel_total": sum(row.get("role") == "clean_sentinel" for row in rows if row["method_id"] == method_id),
            }
            for method_id in METHOD_IDS
        },
        "by_objective_track": {
            track: {
                method_id: {
                    "strict_clean": sum(
                        row.get("strict_clean") is True
                        and row.get("objective_track") == track
                        and row["method_id"] == method_id
                        for row in rows
                    ),
                    "total": sum(
                        row.get("objective_track") == track and row["method_id"] == method_id
                        for row in rows
                    ),
                }
                for method_id in METHOD_IDS
            }
            for track in ("fixed_goal", "resource_tradeoff", "clean_sentinel")
        },
    }
    write_json_atomic(args.campaign_root.resolve() / "reports" / "experiment2_pilot_results.json", report)
    print(json.dumps(report, indent=2))


def run_full_r2g(args: argparse.Namespace) -> None:
    root, fixture, project = fixture_and_project(args)
    if args.method != "full-r2g":
        raise Experiment2Error("run-full-r2g requires --method full-r2g")
    skills = runtime_skills(root, args.method, args.fixture)
    loop = skills / "signoff-loop" / "scripts" / "loop" / "engineer_loop.py"
    ledger = project_paths(root, args.method, args.fixture)["method"] / "engineer_ledger.jsonl"
    env = full_r2g_environment(root, args.fixture, project)
    set_target_frequency(project, fixture, 100.0)
    commands = [
        [sys.executable, str(loop), "add", "--ledger", str(ledger), "--project", str(project), "--platform", "sky130hd"],
        [sys.executable, str(loop), "run", "--ledger", str(ledger), "--workers", "1"],
    ]
    started = time.monotonic()
    command_results = []
    for index, command in enumerate(commands, 1):
        result = run(command, cwd=root, env=env, log=project_paths(root, args.method, args.fixture)["logs"] / f"full_r2g_{index}.log")
        command_results.append({"index": index, "returncode": result.returncode})
        if result.returncode:
            break
    policy_attempts_path = project_paths(root, args.method, args.fixture)["method"] / "policy_flow_attempts.jsonl"
    policy_attempts = []
    if policy_attempts_path.is_file():
        for line in policy_attempts_path.read_text(encoding="utf-8").splitlines():
            try:
                policy_attempts.append(json.loads(line))
            except json.JSONDecodeError:
                command_results.append({
                    "index": "policy",
                    "returncode": 2,
                    "error": "malformed Full R2G flow-attempt ledger",
                })
                break
    manifest = verify_campaign_bindings(root)
    if manifest.get("protocol_version") == "baseline_first_v2" and not policy_attempts:
        command_results.append({
            "index": "policy",
            "returncode": 2,
            "error": "no mandatory Full R2G baseline flow was authorized",
        })
    result = run_strict_measurement(project, fixture, skills, root, project_paths(root, args.method, args.fixture)["logs"] / "full_r2g_final_validation")
    output = project_paths(root, args.method, args.fixture)["validations"] / "full_r2g_final.json"
    write_json_atomic(output, result)
    if result["strict_clean"]:
        lock(args)
    state = read_json(project_paths(root, args.method, args.fixture)["method"] / "campaign_state.json", {}) or {}
    submission = {
        "schema_version": "1.0",
        "experiment_id": "r2g-exp2-orfs-signoff-pilot",
        "method_id": args.method,
        "fixture_id": args.fixture,
        "ended_at": now_iso(),
        "command_results": command_results,
        "flow_attempts": policy_attempts,
        "target": {"policy": "fixed", "frequency_mhz": 100.0, "period_ns": 10.0},
        "resource_usage": {
            "wall_time_seconds": round(time.monotonic() - started, 3),
            "total_tokens": 0,
            "human_interventions": 0,
        },
        "resource_intervention": resource_intervention(project, fixture),
        "strict_clean_before_independent_final_evaluation": result["strict_clean"],
        "submission": {"best_checkpoint": state.get("best_checkpoint")},
        "knowledge_state": {
            "initial": state.get("initial_knowledge"),
            "final": {
                "environment_db": file_record(skills / "signoff-loop" / "knowledge" / "knowledge.sqlite"),
                "environment_heuristics": file_record(skills / "signoff-loop" / "knowledge" / "heuristics.json"),
                "runtime_default_db": file_record(skills / "signoff-loop" / "knowledge" / "knowledge.sqlite"),
                "runtime_default_heuristics": file_record(skills / "signoff-loop" / "knowledge" / "heuristics.json"),
            },
        },
    }
    write_json_atomic(project_paths(root, args.method, args.fixture)["method"] / "full_r2g_submission.json", submission)
    failed = [item for item in command_results if item["returncode"] != 0]
    if failed:
        raise Experiment2Error(
            f"Full R2G command {failed[0]['index']} failed with return code "
            f"{failed[0]['returncode']}"
        )
    print(json.dumps(result, indent=2))


def status(args: argparse.Namespace) -> None:
    root = args.campaign_root.resolve()
    _, cohort = ensure_bound(root, args)
    for method_id in METHOD_IDS:
        print(method_id)
        for fixture in cohort["fixtures"]:
            paths = project_paths(root, method_id, fixture["id"])
            state = read_json(paths["method"] / "campaign_state.json", {}) or {}
            attempts = sum(1 for _ in paths["attempts"].open(encoding="utf-8")) if paths["attempts"].exists() else 0
            best = state.get("best_checkpoint") or {}
            print(f"  {fixture['id']:48s} events={attempts:2d} best={best.get('frequency_mhz')}")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--cohort", type=Path, default=DEFAULT_COHORT)
    result.add_argument("--task-spec", type=Path, default=DEFAULT_TASK_SPEC)
    sub = result.add_subparsers(dest="command", required=True)
    lint = sub.add_parser("lint")
    lint.set_defaults(func=lambda args: print("Experiment 2 cohort OK") if not lint_cohort(load_cohort(args.cohort)) else None)
    prep = sub.add_parser("prepare")
    prep.add_argument("--campaign-root", type=Path, required=True)
    prep.add_argument("--routes", type=Path, default=DEFAULT_ROUTES)
    prep.set_defaults(func=prepare)
    for name, function in (("set-frequency", set_frequency), ("set-core-utilization", set_core_utilization), ("run-flow", run_flow), ("validate-checkpoint", validate), ("lock-checkpoint", lock), ("run-full-r2g", run_full_r2g), ("policy-check", policy_check)):
        item = sub.add_parser(name)
        item.add_argument("--campaign-root", type=Path, required=True)
        item.add_argument("--method", required=True)
        item.add_argument("--fixture", required=True)
        if name == "set-frequency":
            item.add_argument("--frequency-mhz", type=float, required=True)
        if name == "set-core-utilization":
            item.add_argument("--core-utilization", type=float, required=True)
        if name == "run-flow":
            item.add_argument("--timeout-seconds", type=int, default=7200)
        if name == "policy-check":
            item.add_argument("--record-flow-attempt", action="store_true")
        item.set_defaults(func=function)
    grade_cmd = sub.add_parser("grade")
    grade_cmd.add_argument("--campaign-root", type=Path, required=True)
    grade_cmd.set_defaults(func=grade)
    stat = sub.add_parser("status")
    stat.add_argument("--campaign-root", type=Path, required=True)
    stat.set_defaults(func=status)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        args.func(args)
    except Experiment2Error as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
