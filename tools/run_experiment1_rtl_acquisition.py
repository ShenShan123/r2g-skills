#!/usr/bin/env python3
"""Freeze, validate, and independently grade Experiment 1 RTL candidates."""

from __future__ import annotations

import argparse
from collections import Counter
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
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


REPO = Path(__file__).resolve().parents[1]
EXPERIMENT_DIR = REPO / "docs/experiments/rtl-acquisition"
PROTOCOL = REPO / "docs/experiments/formal_experiment_1_2_key_design_zh.md"
TASK_SPEC = EXPERIMENT_DIR / "experiment1_task_spec.json"
SUBMISSION_SCHEMA = EXPERIMENT_DIR / "experiment1_submission.schema.json"
MANIFEST_SCHEMA = EXPERIMENT_DIR / "experiment1_execution_manifest.schema.json"
DEFAULT_BENCHMARK_REGISTRY = EXPERIMENT_DIR / "benchmark_registry_v1"
METHOD_IDS = {
    "openai-vanilla",
    "anthropic-vanilla",
    "deepseek-vanilla",
    "qwen-vanilla",
    "glm-vanilla",
    "kimi-vanilla",
    "r2g-expander-cold",
}
R2G_METHOD_IDS = {"r2g-expander-cold"}
VANILLA_METHOD_IDS = METHOD_IDS - R2G_METHOD_IDS
MINIMUM_MAPPED_CELLS = 100
MAXIMUM_MAPPED_CELLS_EXCLUSIVE = 100000
HEX40 = re.compile(r"^[0-9a-f]{40}$")
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
CELL_RE = re.compile(
    r"(?ms)^\s*(sky130_fd_sc_hd__[A-Za-z0-9_]+)\s+"
    r"(?:\\[^\s]+|[A-Za-z_$][A-Za-z0-9_$]*)\s*\((.*?)^\s*\);"
)
PORT_RE = {
    "input": re.compile(r"(?ms)^\s*input\b(.*?);"),
    "output": re.compile(r"(?ms)^\s*output\b(.*?);"),
}
UNRESOLVED_RE = re.compile(
    r"(?i)(module\s+[`'\\]?[A-Za-z_$][A-Za-z0-9_$]*[`']?\s+"
    r"(?:referenced|is referenced).*(?:not part|not found)|"
    r"cannot find module|unknown module|module.*not found)"
)
INCLUDE_RE = re.compile(r'(?m)^\s*`include\s+"([^"]+)"')
PREPROCESSOR_DIRECTIVE_RE = re.compile(
    r"^\s*`(ifdef|ifndef|elsif|else|endif|define|undef)\b"
    r"(?:\s+([A-Za-z_][A-Za-z0-9_]*))?"
)
READMEM_CALL_RE = re.compile(r"\$(?:readmemh|readmemb)\s*\((.*?)\)", re.DOTALL)
LITERAL_STRING_RE = re.compile(r'^\s*"([^"]+)"')
SPDX_RE = re.compile(
    r"(?im)SPDX-License-Identifier:\s*([A-Za-z0-9.+-]+)"
)


class ExperimentError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExperimentError(f"cannot read JSON {path}: {exc}") from exc


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_tree(root: Path) -> str:
    """Hash a directory by relative path, type, and file content."""
    if not root.is_dir():
        raise ExperimentError(f"bound directory does not exist: {root}")
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        kind = "D" if path.is_dir() else "L" if path.is_symlink() else "F"
        digest.update(f"{kind}\0{relative}\0".encode("utf-8"))
        if path.is_symlink():
            digest.update(os.readlink(path).encode("utf-8"))
        elif path.is_file():
            digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def infer_spdx_identifier(text: str) -> str | None:
    explicit = SPDX_RE.search(text)
    if explicit:
        return explicit.group(1)
    lowered = " ".join(text.lower().split())
    lead = lowered[:2000]
    later = "later version" in lead or "any later" in lead
    label = re.search(
        r"(?im)^\s*(?://|/\*|\*|#|\|)?\s*License:\s*(L?GPL)\b"
        r"(?:[,\s]*v?([0-9.]+))?",
        text,
    )
    if label:
        family = label.group(1).upper()
        version = label.group(2)
        if version is None:
            match = re.search(r"\bversion\s+(2\.1|2|3)\b", lowered)
            version = match.group(1) if match else None
        later = "later version" in lowered or "any later" in lowered
        if family == "LGPL" and version in {"2.1", "3"}:
            normalized = "2.1" if version == "2.1" else "3.0"
            return f"LGPL-{normalized}-{'or-later' if later else 'only'}"
        if family == "GPL" and version in {"2", "3"}:
            return f"GPL-{version}.0-{'or-later' if later else 'only'}"
    if "apache license" in lowered and (
        "version 2.0" in lowered or "apache-2.0" in lowered
    ):
        return "Apache-2.0"
    if (
        "cern open hardware licence version 2" in lowered
        and "weakly reciprocal" in lowered
    ):
        return "CERN-OHL-W-2.0"
    if "gnu lesser general public license" in lead or "license: lgpl" in lead:
        if "version 3" in lead or "lgpl, v3" in lead:
            return (
                "LGPL-3.0-or-later"
                if later
                else "LGPL-3.0-only"
            )
        if "version 2.1" in lead:
            return (
                "LGPL-2.1-or-later"
                if later
                else "LGPL-2.1-only"
            )
    if "gnu general public license" in lead or "license: gpl" in lead:
        if "version 3" in lead:
            return (
                "GPL-3.0-or-later"
                if later
                else "GPL-3.0-only"
            )
        if "version 2" in lead:
            return (
                "GPL-2.0-or-later"
                if later
                else "GPL-2.0-only"
            )
    if "permission is hereby granted, free of charge" in lowered:
        return "MIT"
    if (
        "redistribution and use in source and binary forms" in lowered
        and "this software is provided by the copyright holders and contributors"
        in lowered
    ):
        if (
            "neither the name of the copyright holder" in lowered
            or "neither the name of" in lowered
            and "may be used to endorse or promote" in lowered
        ):
            return "BSD-3-Clause"
        return "BSD-2-Clause"
    if "isc license" in lowered:
        return "ISC"
    return None


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = False,
    stdout: Any = subprocess.PIPE,
    stderr: Any = subprocess.PIPE,
) -> subprocess.CompletedProcess[Any]:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=stdout,
        stderr=stderr,
        check=False,
    )
    if check and result.returncode:
        message = (result.stderr or result.stdout or "").strip()
        raise ExperimentError(f"command failed ({result.returncode}): {' '.join(command)}\n{message}")
    return result


def git_text(*args: str, cwd: Path = REPO) -> str:
    return run(["git", *args], cwd=cwd, check=True).stdout.strip()


def validate_json(instance: Any, schema_path: Path | dict[str, Any]) -> list[str]:
    schema = read_json(schema_path) if isinstance(schema_path, Path) else schema_path
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(instance), key=lambda item: list(item.absolute_path))
    return [
        f"{'/'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
        for error in errors
    ]


def safe_relative(value: str) -> Path:
    if value == ".":
        return Path(".")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ExperimentError(f"unsafe repository-relative path: {value!r}")
    return path


def submission_key(candidate: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(candidate["repo_url"]).rstrip("/"),
        str(candidate["commit"]).lower(),
        str(candidate["top_module"]),
    )


def normalized_repo_url(value: str) -> str:
    return value.strip().rstrip("/").removesuffix(".git").lower()


def semantic_submission_errors(
    submission: dict[str, Any],
    *,
    prior_keys: set[tuple[str, str, str]] | None = None,
    target_candidates: int = 25,
) -> list[str]:
    errors: list[str] = []
    method_id = submission.get("method_id")
    if method_id not in METHOD_IDS:
        errors.append(f"unknown method_id: {method_id!r}")
    candidates = submission.get("candidates") or []
    keys = [submission_key(item) for item in candidates]
    if len(keys) != len(set(keys)):
        errors.append("candidate keys must be unique within the batch")
    overlap = set(keys) & (prior_keys or set())
    if overlap:
        errors.append(f"{len(overlap)} candidate key(s) duplicate an earlier batch")
    ids = [item.get("candidate_id") for item in candidates]
    if len(ids) != len(set(ids)):
        errors.append("candidate_id values must be unique within the batch")
    repository_counts = Counter(normalized_repo_url(str(item["repo_url"])) for item in candidates)
    over_cap = {repo: count for repo, count in repository_counts.items() if count > 4}
    if over_cap:
        errors.append(
            "at most four candidates may be submitted from one repository: "
            + ", ".join(f"{repo}={count}" for repo, count in sorted(over_cap.items()))
        )
    if (
        submission.get("stop_reason") == "target_reached"
        and len(candidates) != target_candidates
    ):
        errors.append(
            "target_reached requires exactly "
            f"{target_candidates} in-scope candidates"
        )
    usage = submission.get("resource_usage") or {}
    queries = submission.get("queries") or []
    if usage.get("search_requests") != len(queries):
        errors.append("resource_usage.search_requests must equal the query log length")
    token_fields = ("input_tokens", "output_tokens", "reasoning_tokens")
    components = [usage.get(key) for key in token_fields]
    total = usage.get("total_tokens")
    if all(value is not None for value in components) and total is not None:
        if sum(int(value) for value in components) != int(total):
            errors.append("total_tokens must equal input + output + reasoning tokens")
    route = submission.get("model_route") or {}
    if method_id in R2G_METHOD_IDS:
        if total not in (0, None):
            errors.append("R2G-Expander model tokens must be zero or null")
        if any(route.get(key) is not None for key in ("requested_model", "actual_model", "api_provider")):
            errors.append("R2G-Expander must not declare an LLM route")
        if route.get("endpoint_kind") != "none":
            errors.append("R2G-Expander endpoint_kind must be none")
    else:
        if not route.get("requested_model") or not route.get("actual_model"):
            errors.append("Vanilla LLM submissions require requested_model and actual_model")
        if route.get("endpoint_kind") not in {"official", "gateway"}:
            errors.append("Vanilla LLM endpoint_kind must be official or gateway")
    for candidate in candidates:
        for field in ("rtl_files", "header_files", "include_dirs", "readmem_files"):
            for value in candidate.get(field, []):
                try:
                    safe_relative(value)
                except ExperimentError as exc:
                    errors.append(f"{candidate.get('candidate_id')}/{field}: {exc}")
    return errors


NON_SCOREABLE_STOP_REASONS = {"provider_failure", "operator_abort"}


def submission_score_eligibility(submission: dict[str, Any]) -> tuple[bool, str | None]:
    """Separate a valid audit record from a run that is eligible for scoring."""
    stop_reason = str(submission.get("stop_reason") or "")
    if stop_reason in NON_SCOREABLE_STOP_REASONS:
        return False, f"non-scoreable infrastructure termination: {stop_reason}"
    return True, None


def bound_file(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": sha256_file(path)}


def validate_model_preflight(path: Path, model_routes: Path) -> None:
    payload = read_json(path)
    if payload.get("routes_sha256") != sha256_file(model_routes):
        raise ExperimentError("model preflight was produced for different route configuration")
    expected = VANILLA_METHOD_IDS
    results = payload.get("results") or []
    observed = {str(row.get("method_id")) for row in results}
    if observed != expected:
        raise ExperimentError(
            "model preflight must contain exactly the six Vanilla methods; "
            f"observed={sorted(observed)}"
        )
    failed = [str(row.get("method_id")) for row in results if row.get("status") != "ready"]
    if failed:
        raise ExperimentError("model routes are not ready: " + ", ".join(sorted(failed)))


def executable_version(command: list[str]) -> str | None:
    try:
        result = run(command)
    except OSError:
        return None
    text = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    return text.splitlines()[0] if text else None


def resolved_agent_env() -> dict[str, str]:
    env_script = REPO / "r2g-skills/signoff-loop/scripts/flow/_env.sh"
    command = [
        "bash",
        "-c",
        'source "$1" >/dev/null 2>&1; env -0',
        "r2g-exp1-env",
        str(env_script),
    ]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise ExperimentError(
            f"cannot resolve the Agent toolchain environment: "
            f"{result.stderr.decode(errors='replace').strip()}"
        )
    values: dict[str, str] = {}
    for item in result.stdout.split(b"\0"):
        if b"=" not in item:
            continue
        key, value = item.split(b"=", 1)
        values[key.decode(errors="replace")] = value.decode(errors="replace")
    for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE", "__PYVENV_LAUNCHER__"):
        values.pop(key, None)
    return values


def manifest_toolchain_env(manifest: dict[str, Any]) -> dict[str, str]:
    """Resolve tools, then enforce the exact paths frozen in the campaign."""
    env = resolved_agent_env()
    toolchain = manifest["toolchain"]
    orfs_root = Path(toolchain["orfs_root"]).resolve()
    # ORFS honours an inherited FLOW_HOME before deriving it from the Makefile.
    # Drop installation-relative paths from the ambient Agent environment so a
    # frozen campaign cannot combine one ORFS checkout with another checkout's
    # scripts, platforms, or design directory.
    for key in (
        "FLOW_HOME",
        "DESIGN_HOME",
        "PLATFORM_HOME",
        "SCRIPTS_DIR",
        "UTILS_DIR",
    ):
        env.pop(key, None)
    env["ORFS_ROOT"] = str(orfs_root)
    env["FLOW_HOME"] = str(orfs_root / "flow")
    if toolchain.get("pdk_root"):
        env["PDK_ROOT"] = str(toolchain["pdk_root"])
    return env


def toolchain_record(env: dict[str, str]) -> dict[str, Any]:
    orfs_root = Path(
        env.get("ORFS_ROOT", "/home/yangao/r2g_toolchain/OpenROAD-flow-scripts")
    ).resolve()
    pdk_root = Path(env.get("PDK_ROOT", "")).resolve() if env.get("PDK_ROOT") else None
    if not (orfs_root / ".git").exists():
        raise ExperimentError(
            "Experiment 1 requires a Git-traceable ORFS checkout: " + str(orfs_root)
        )
    return {
        "platform": "sky130hd",
        "orfs_root": str(orfs_root),
        "orfs_commit": git_text("rev-parse", "HEAD", cwd=orfs_root),
        "yosys_version": executable_version([env.get("YOSYS_EXE", "yosys"), "-V"]),
        "openroad_version": executable_version([env.get("OPENROAD_EXE", "openroad"), "-version"]),
        "pdk_root": str(pdk_root) if pdk_root else None,
        "clock_period_ns": 10.0,
        "abc_area": 0,
        "synth_memory_max_bits": 131072,
        "candidate_timeout_seconds": 3600,
        "minimum_mapped_cells": MINIMUM_MAPPED_CELLS,
        "maximum_mapped_cells_exclusive": MAXIMUM_MAPPED_CELLS_EXCLUSIVE,
    }


def campaign_paths(root: Path) -> dict[str, Path]:
    root = root.resolve()
    return {
        "root": root,
        "manifest": root / "execution_manifest.json",
        "submissions": root / "submissions",
        "evaluation": root / "evaluation",
        "reports": root / "reports",
    }


def init_campaign(args: argparse.Namespace) -> None:
    paths = campaign_paths(args.campaign_root)
    if paths["manifest"].exists():
        raise ExperimentError(f"campaign already exists: {paths['root']}")
    if paths["root"].exists() and any(paths["root"].iterdir()):
        raise ExperimentError(f"campaign root is not empty: {paths['root']}")
    env = resolved_agent_env()
    if args.orfs_root is not None:
        env["ORFS_ROOT"] = str(args.orfs_root.resolve())
    commit = git_text("rev-parse", "HEAD")
    status = git_text("status", "--porcelain")
    diff = git_text("diff", "--binary", "HEAD")
    task = read_json(TASK_SPEC)
    if args.allow_dirty_canary and not args.non_scoring_canary:
        raise ExperimentError(
            "--allow-dirty-canary requires --non-scoring-canary"
        )
    if status and not args.allow_dirty_canary:
        raise ExperimentError(
            "formal campaign requires a clean Agent worktree; "
            "use --allow-dirty-canary only for a non-paper canary"
        )
    if task.get("status") != "frozen" and not args.non_scoring_canary:
        raise ExperimentError(
            "Experiment 1 task spec is not frozen; use --allow-dirty-canary "
            "only for a non-paper canary"
        )
    for key in ("submissions", "evaluation", "reports"):
        paths[key].mkdir(parents=True, exist_ok=True)
    knowledge = REPO / "r2g-skills/signoff-loop/knowledge/knowledge.sqlite"
    batches = [
        {
            "method_id": method["method_id"],
            "batch_id": batch_id,
            "status": "pending",
            "submission_path": None,
            "submission_sha256": None,
            "evaluation_path": None,
            "evaluation_sha256": None,
        }
        for method in task["methods"]
        for batch_id in range(1, int(task["batch_policy"]["batches_per_method"]) + 1)
    ]
    model_routes = args.model_routes.resolve()
    model_preflight = args.model_preflight.resolve()
    benchmark_registry = args.benchmark_registry.resolve()
    catalog_path = benchmark_registry / "registry_catalog.json"
    if not catalog_path.is_file():
        raise ExperimentError(
            f"benchmark registry catalog is missing: {catalog_path}"
        )
    catalog = read_json(catalog_path)
    profile_id = str(catalog.get("active_profile") or "")
    profile_path = benchmark_registry / "profiles" / f"{profile_id}.json"
    if (
        not profile_id
        or not profile_path.is_file()
        or read_json(profile_path).get("ready") is not True
    ):
        raise ExperimentError("benchmark registry active profile is not audit-ready")
    validate_model_preflight(model_preflight, model_routes)
    implementation_files = {
        "campaign_controller_and_evaluator": Path(__file__).resolve(),
        "method_campaign_orchestrator": REPO / "tools/run_experiment1_method_campaign.py",
        "vanilla_method_runner": REPO / "tools/run_experiment1_vanilla_method.py",
        "r2g_method_runner": REPO / "tools/run_experiment1_r2g_method.py",
        "r2g_submission_builder": REPO / "tools/build_experiment1_r2g_submission.py",
        "model_route_preflight": REPO / "tools/preflight_experiment1_model_routes.py",
        "model_route_configuration": model_routes,
        "execution_manifest_schema": MANIFEST_SCHEMA,
    }
    missing_implementations = [
        str(path) for path in implementation_files.values() if not path.is_file()
    ]
    if missing_implementations:
        raise ExperimentError(
            "missing Experiment 1 implementation binding(s): "
            + ", ".join(missing_implementations)
        )
    manifest = {
        "schema_version": "1.1",
        "experiment_id": task["experiment_id"],
        "campaign_id": args.campaign_id or paths["root"].name,
        "campaign_mode": (
            "non_scoring_canary" if args.non_scoring_canary else "formal"
        ),
        "created_at": now_iso(),
        "protocol": bound_file(PROTOCOL),
        "task_spec": bound_file(TASK_SPEC),
        "submission_schema": bound_file(SUBMISSION_SCHEMA),
        "implementation_bindings": [
            {"role": role, **bound_file(path)}
            for role, path in implementation_files.items()
        ],
        "model_route_preflight": bound_file(model_preflight),
        "benchmark_registry": {
            "path": str(benchmark_registry),
            "sha256": sha256_tree(benchmark_registry),
            "active_profile": profile_id,
        },
        "agent_snapshot": {
            "repository": str(REPO),
            "commit": commit,
            "dirty": bool(status),
            "worktree_diff_sha256": sha256_text(diff) if diff else None,
            "knowledge_snapshot_sha256": sha256_file(knowledge) if knowledge.is_file() else None,
        },
        "toolchain": toolchain_record(env),
        "model_routes": [],
        "batches": batches,
        "method_reports": [
            {
                "method_id": method["method_id"],
                "status": "pending",
                "report_path": None,
                "report_sha256": None,
            }
            for method in task["methods"]
        ],
    }
    errors = validate_json(manifest, MANIFEST_SCHEMA)
    if errors:
        raise ExperimentError("generated manifest is invalid:\n" + "\n".join(errors))
    write_json_atomic(paths["manifest"], manifest)
    print(f"Initialized Experiment 1 campaign: {paths['root']}")
    if args.non_scoring_canary:
        print("WARNING: non-scoring canary; this campaign is not a paper result.")


def prior_candidate_keys(paths: dict[str, Path], method_id: str, batch_id: int) -> set[tuple[str, str, str]]:
    keys: set[tuple[str, str, str]] = set()
    for path in sorted(paths["submissions"].glob(f"{method_id}.batch*.json")):
        match = re.search(r"\.batch(\d+)\.json$", path.name)
        if not match or int(match.group(1)) >= batch_id:
            continue
        submission = read_json(path)
        keys.update(submission_key(item) for item in submission.get("candidates", []))
    return keys


def validate_submission(
    submission_path: Path,
    *,
    campaign_root: Path | None = None,
) -> tuple[dict[str, Any], list[str]]:
    submission = read_json(submission_path)
    errors = validate_json(submission, SUBMISSION_SCHEMA)
    if errors:
        return submission, errors
    prior: set[tuple[str, str, str]] = set()
    if campaign_root is not None:
        paths = campaign_paths(campaign_root)
        prior = prior_candidate_keys(
            paths,
            str(submission["method_id"]),
            int(submission["batch_id"]),
        )
    errors.extend(semantic_submission_errors(submission, prior_keys=prior))
    return submission, errors


def find_batch(manifest: dict[str, Any], method_id: str, batch_id: int) -> dict[str, Any]:
    for row in manifest["batches"]:
        if row["method_id"] == method_id and int(row["batch_id"]) == batch_id:
            return row
    raise ExperimentError(f"unknown campaign batch: {method_id}/batch{batch_id}")


def accept_submission(args: argparse.Namespace) -> None:
    paths = campaign_paths(args.campaign_root)
    manifest = read_json(paths["manifest"])
    submission, errors = validate_submission(args.submission, campaign_root=paths["root"])
    if errors:
        raise ExperimentError("submission rejected:\n" + "\n".join(errors))
    score_eligible, reason = submission_score_eligibility(submission)
    if not score_eligible:
        raise ExperimentError(
            "submission is a valid audit record but cannot be locked for scoring: "
            + str(reason)
        )
    method_id = submission["method_id"]
    batch_id = int(submission["batch_id"])
    batch = find_batch(manifest, method_id, batch_id)
    if batch["status"] != "pending":
        raise ExperimentError(f"batch is already locked with status={batch['status']}")
    destination = paths["submissions"] / f"{method_id}.batch{batch_id}.json"
    shutil.copyfile(args.submission, destination)
    batch.update(
        {
            "status": "submitted",
            "submission_path": str(destination),
            "submission_sha256": sha256_file(destination),
        }
    )
    route = {"method_id": method_id, "batch_id": batch_id, **submission["model_route"]}
    manifest["model_routes"].append(route)
    write_json_atomic(paths["manifest"], manifest)
    print(f"Accepted and digest-locked: {destination}")


def verify_bound_campaign(manifest: dict[str, Any]) -> None:
    for key in (
        "protocol",
        "task_spec",
        "submission_schema",
        "model_route_preflight",
    ):
        record = manifest[key]
        path = Path(record["path"])
        if not path.is_file() or sha256_file(path) != record["sha256"]:
            raise ExperimentError(f"campaign binding changed or disappeared: {key} -> {path}")
    for record in manifest.get("implementation_bindings", []):
        path = Path(record["path"])
        if not path.is_file() or sha256_file(path) != record["sha256"]:
            raise ExperimentError(
                "campaign implementation changed or disappeared: "
                f"{record['role']} -> {path}"
            )
    registry = manifest["benchmark_registry"]
    registry_path = Path(registry["path"])
    if sha256_tree(registry_path) != registry["sha256"]:
        raise ExperimentError(
            "campaign benchmark registry changed or disappeared: "
            + str(registry_path)
        )


def source_cache_key(candidate: dict[str, Any]) -> str:
    return hashlib.sha256(
        (
            normalized_repo_url(str(candidate["repo_url"]))
            + "|"
            + str(candidate["commit"]).lower()
        ).encode("utf-8")
    ).hexdigest()[:20]


def clone_candidate(candidate: dict[str, Any], destination: Path, log: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as stream:
        for attempt in range(1, 4):
            temporary = destination.with_name(destination.name + ".tmp")
            if temporary.exists():
                shutil.rmtree(temporary)
            stream.write(f"CLONE ATTEMPT {attempt}/3\n")
            stream.flush()
            result = run(
                [
                    "git", "clone", "--no-checkout", "--filter=blob:none",
                    candidate["repo_url"], str(temporary),
                ],
                stdout=stream,
                stderr=subprocess.STDOUT,
            )
            if result.returncode == 0:
                result = run(
                    ["git", "checkout", "--detach", candidate["commit"]],
                    cwd=temporary,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                )
            if result.returncode == 0:
                temporary.replace(destination)
                break
            if temporary.exists():
                shutil.rmtree(temporary)
            if attempt < 3:
                time.sleep(attempt)
        else:
            raise ExperimentError(f"git source retrieval failed with rc={result.returncode}")
    head = git_text("rev-parse", "HEAD", cwd=destination)
    if head.lower() != candidate["commit"].lower():
        raise ExperimentError(f"checked out {head}, expected {candidate['commit']}")
    return destination


def cached_candidate_source(
    candidate: dict[str, Any],
    cache_root: Path,
    log: Path,
) -> Path:
    destination = cache_root / source_cache_key(candidate)
    if destination.is_dir():
        head = git_text("rev-parse", "HEAD", cwd=destination)
        if head.lower() != candidate["commit"].lower():
            raise ExperimentError(
                f"source cache has {head}, expected {candidate['commit']}"
            )
        log.write_text(
            f"REUSED SOURCE CACHE: {destination}\nHEAD: {head}\n",
            encoding="utf-8",
        )
        return destination
    return clone_candidate(candidate, destination, log)


def verify_candidate_inputs(candidate: dict[str, Any], source: Path) -> dict[str, Any]:
    files = [
        *candidate["rtl_files"],
        *candidate.get("header_files", []),
        *candidate.get("readmem_files", []),
    ]
    source_root = source.resolve()
    missing: list[str] = []
    escaped: list[str] = []
    digests: list[dict[str, Any]] = []
    for value in files:
        relative = safe_relative(value)
        path = source / relative
        if not path.is_file():
            missing.append(value)
            continue
        resolved = path.resolve()
        if resolved != source_root and source_root not in resolved.parents:
            escaped.append(value)
            continue
        digests.append(
            {
                "path": value,
                "sha256": sha256_file(resolved),
                "size": resolved.stat().st_size,
            }
        )
    include_paths: list[Path] = []
    missing_dirs: list[str] = []
    for value in candidate.get("include_dirs", []):
        path = (source / safe_relative(value)).resolve()
        if not path.is_dir():
            missing_dirs.append(value)
        elif path != source_root and source_root not in path.parents:
            escaped.append(value)
        else:
            include_paths.append(path)

    declared_headers = set(candidate.get("header_files", []))
    declared_rtl = set(candidate["rtl_files"])
    declared_readmem = set(candidate.get("readmem_files", []))
    undeclared_headers: set[str] = set()
    missing_include_references: set[str] = set()
    undeclared_readmem: set[str] = set()
    missing_readmem_references: set[str] = set()
    dynamic_readmem: list[str] = []
    macros = {"YOSYS"}
    macros.update(value.split("=", 1)[0] for value in candidate.get("defines", []))
    scanning: set[Path] = set()

    def repo_relative(path: Path) -> str | None:
        try:
            return path.resolve().relative_to(source_root).as_posix()
        except ValueError:
            return None

    def resolve_reference(reference: str, consumer: Path) -> Path | None:
        candidates = [
            consumer.parent / reference,
            *(directory / reference for directory in include_paths),
            source_root / reference,
        ]
        for path in candidates:
            if not path.is_file():
                continue
            resolved = path.resolve()
            if resolved == source_root or source_root in resolved.parents:
                return resolved
        return None

    def scan_active_references(consumer: Path) -> None:
        if not consumer.is_file():
            return
        resolved_consumer = consumer.resolve()
        if resolved_consumer in scanning:
            return
        scanning.add(resolved_consumer)
        text = resolved_consumer.read_text(encoding="utf-8", errors="ignore")
        active = True
        conditionals: list[dict[str, bool]] = []
        active_lines: list[str] = []

        for line in text.splitlines(keepends=True):
            directive = PREPROCESSOR_DIRECTIVE_RE.match(line)
            if directive:
                command, name = directive.groups()
                if command in {"ifdef", "ifndef"}:
                    condition = bool(name in macros)
                    if command == "ifndef":
                        condition = not condition
                    conditionals.append(
                        {
                            "parent_active": active,
                            "branch_taken": condition,
                        }
                    )
                    active = active and condition
                elif command == "elsif" and conditionals:
                    frame = conditionals[-1]
                    condition = bool(name in macros)
                    active = (
                        frame["parent_active"]
                        and not frame["branch_taken"]
                        and condition
                    )
                    frame["branch_taken"] = frame["branch_taken"] or condition
                elif command == "else" and conditionals:
                    frame = conditionals[-1]
                    active = frame["parent_active"] and not frame["branch_taken"]
                    frame["branch_taken"] = True
                elif command == "endif" and conditionals:
                    frame = conditionals.pop()
                    active = frame["parent_active"]
                elif command == "define" and active and name:
                    macros.add(name)
                elif command == "undef" and active and name:
                    macros.discard(name)
                continue

            if not active:
                continue

            include = INCLUDE_RE.match(line)
            if include:
                reference = include.group(1)
                target = resolve_reference(reference, resolved_consumer)
                if target is None:
                    missing_include_references.add(
                        f"{repo_relative(resolved_consumer) or resolved_consumer}:"
                        f"{reference}"
                    )
                    continue
                relative = repo_relative(target)
                if relative is None:
                    escaped.append(str(target))
                    continue
                if relative not in declared_headers and relative not in declared_rtl:
                    undeclared_headers.add(relative)
                scan_active_references(target)
                continue

            active_lines.append(line)

        active_text = "".join(active_lines)
        for call in READMEM_CALL_RE.findall(active_text):
            literal = LITERAL_STRING_RE.match(call)
            if not literal:
                dynamic_readmem.append(
                    f"{repo_relative(resolved_consumer) or resolved_consumer}:{call[:80]}"
                )
                continue
            reference = literal.group(1)
            target = resolve_reference(reference, resolved_consumer)
            if target is None:
                missing_readmem_references.add(
                    f"{repo_relative(resolved_consumer) or resolved_consumer}:{reference}"
                )
                continue
            relative = repo_relative(target)
            if relative is None:
                escaped.append(str(target))
            elif relative not in declared_readmem:
                undeclared_readmem.add(relative)

        scanning.remove(resolved_consumer)

    for value in candidate["rtl_files"]:
        scan_active_references(source / safe_relative(value))

    evidence = candidate["license_evidence"]
    evidence_value = evidence["repository_path"]
    declared_spdx = evidence.get("spdx_id")
    observed_spdx: str | None = None
    evidence_path = source / safe_relative(evidence_value)
    license_location_ok = evidence_path.is_file()
    if license_location_ok:
        observed_spdx = infer_spdx_identifier(
            evidence_path.read_text(encoding="utf-8", errors="ignore")
        )
    license_ok = bool(
        declared_spdx
        and observed_spdx
        and str(declared_spdx).lower() == observed_spdx.lower()
    )
    digest_payload = json.dumps(digests, sort_keys=True, separators=(",", ":"))
    return {
        "files": digests,
        "closure_sha256": sha256_text(digest_payload),
        "missing_files": missing,
        "missing_include_dirs": missing_dirs,
        "escaped_paths": sorted(set(escaped)),
        "undeclared_headers": sorted(undeclared_headers),
        "missing_include_references": sorted(missing_include_references),
        "undeclared_readmem_files": sorted(undeclared_readmem),
        "missing_readmem_references": sorted(missing_readmem_references),
        "dynamic_readmem_references": sorted(dynamic_readmem),
        "license_declared": license_ok,
        "license_location_ok": license_location_ok,
        "license_spdx_declared": declared_spdx,
        "license_spdx_observed": observed_spdx,
    }


def infer_clock_candidates(candidate: dict[str, Any], source: Path) -> list[str]:
    text = "\n".join(
        (source / safe_relative(value)).read_text(encoding="utf-8", errors="ignore")
        for value in candidate["rtl_files"]
    )
    names: list[str] = []
    for match in re.finditer(r"(?i)\b(?:posedge|negedge)\s+([A-Za-z_][A-Za-z0-9_$]*)", text):
        value = match.group(1)
        if value not in names:
            names.append(value)
    for value in (
        "clk", "clock", "i_clk", "i_clock", "clock_i", "clk_i",
        "wb_clk_i", "wb_clk", "clock_in", "core_clk", "CK",
    ):
        if value not in names:
            names.append(value)
    return names


def write_synth_project(
    candidate: dict[str, Any],
    source: Path,
    project: Path,
) -> tuple[Path, str]:
    constraints = project / "constraints"
    constraints.mkdir(parents=True, exist_ok=True)
    candidates = infer_clock_candidates(candidate, source)
    sdc = constraints / "constraint.sdc"
    sdc.write_text(
        "set candidates [get_ports -quiet {" + " ".join(candidates) + "}]\n"
        "if {[llength $candidates] > 0} {\n"
        "  create_clock -name core_clk -period 10 [lindex $candidates 0]\n"
        "} else {\n"
        "  create_clock -name virtual_clk -period 10\n"
        "}\n",
        encoding="utf-8",
    )
    # Make variables split path lists on whitespace. Legal repository filenames
    # containing spaces therefore looked like several RTL files and failed before
    # Yosys. Stage stable, whitespace-free symlinks while keeping the original
    # source tree as the independently digested provenance authority.
    stage = project / "rtl_staged"
    stage.mkdir(parents=True, exist_ok=True)
    source_files = [
        (source / safe_relative(value)).resolve()
        for value in candidate["rtl_files"]
    ]
    staged_rtl: list[str] = []
    for index, original in enumerate(source_files):
        suffix = original.suffix if original.suffix else ".v"
        staged = stage / f"rtl_{index:04d}{suffix}"
        if staged.exists() or staged.is_symlink():
            staged.unlink()
        staged.symlink_to(original)
        staged_rtl.append(str(staged))

    include_sources = [
        (source / safe_relative(value)).resolve()
        for value in candidate.get("include_dirs", [])
    ]
    include_sources.extend(path.parent for path in source_files)
    staged_includes: list[str] = []
    seen_includes: set[Path] = set()
    include_stage = project / "include_staged"
    include_stage.mkdir(parents=True, exist_ok=True)
    for original in include_sources:
        if original in seen_includes:
            continue
        seen_includes.add(original)
        staged = include_stage / f"inc_{len(staged_includes):04d}"
        if staged.exists() or staged.is_symlink():
            staged.unlink()
        staged.symlink_to(original, target_is_directory=True)
        staged_includes.append(str(staged))
    lines = [
        f"export DESIGN_NAME = {candidate['top_module']}",
        "export PLATFORM = sky130hd",
        f"export VERILOG_FILES = {' '.join(staged_rtl)}",
        f"export VERILOG_INCLUDE_DIRS = {' '.join(staged_includes)}",
        f"export SDC_FILE = {sdc.resolve()}",
        "export ABC_AREA = 0",
        "export SYNTH_MEMORY_MAX_BITS = 131072",
        "export R2G_FLOW_SCOPE = synth_only",
    ]
    defines = [f"-D{value}" for value in candidate.get("defines", [])]
    if defines:
        lines.append("export VERILOG_DEFINES = " + " ".join(defines))
    parameters = candidate.get("top_parameters") or {}
    if parameters:
        rendered: list[str] = []
        for key, value in parameters.items():
            if isinstance(value, bool):
                atom = "1" if value else "0"
            else:
                atom = str(value).strip()
            if not re.fullmatch(
                r"(?:-?\d+(?:\.\d+)?|"
                r"\d+'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+|"
                r"[A-Za-z_][A-Za-z0-9_.$]*)",
                atom,
            ):
                raise ExperimentError(
                    f"unsupported top parameter value for {key}: {atom!r}"
                )
            rendered.extend((key, atom))
        lines.append("export VERILOG_TOP_PARAMS = " + " ".join(rendered))
    config = constraints / "config.mk"
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Include the evaluation workspace in the namespace. Different methods can
    # legitimately select the same design and run concurrently; a candidate-only
    # variant would make their clean/synth targets overwrite one another inside
    # the shared ORFS checkout.
    variant = "exp1_" + hashlib.sha256(
        (
            f"{candidate['repo_url']}|{candidate['commit']}|"
            f"{candidate['top_module']}|{project.resolve()}"
        ).encode()
    ).hexdigest()[:12]
    return config, variant


def parse_ports(netlist: str, direction: str) -> list[str]:
    names: list[str] = []
    for match in PORT_RE[direction].finditer(netlist):
        declaration = re.sub(r"\[[^\]]+\]", " ", match.group(1))
        declaration = re.sub(r"\b(?:wire|reg|logic|signed|unsigned)\b", " ", declaration)
        for value in declaration.split(","):
            name_match = re.search(r"([A-Za-z_$][A-Za-z0-9_$]*)\s*$", value.strip())
            if name_match:
                names.append(name_match.group(1))
    return list(dict.fromkeys(names))


def inspect_mapped_netlist(path: Path, flow_log: str) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    cell_matches = list(CELL_RE.finditer(text))
    inputs = parse_ports(text, "input")
    outputs = parse_ports(text, "output")
    instance_text = "\n".join(match.group(2) for match in cell_matches)
    driven_outputs = [
        name
        for name in outputs
        if re.search(rf"(?<![A-Za-z0-9_$]){re.escape(name)}(?![A-Za-z0-9_$])", instance_text)
    ]
    return {
        "mapped_cells": len(cell_matches),
        "input_ports": inputs,
        "output_ports": outputs,
        "cell_driven_outputs": driven_outputs,
        "has_functional_io": bool(inputs and outputs and driven_outputs),
        "unresolved_module_evidence": sorted(set(UNRESOLVED_RE.findall(flow_log))),
    }


def newest_mapped_netlist(
    flow_dir: Path,
    top: str,
    variant: str,
    started_ns: int,
) -> Path | None:
    result_dir = flow_dir / "results/sky130hd" / top / variant
    candidates = [
        path
        for pattern in ("*synth*.v", "*yosys*.v")
        for path in result_dir.glob(pattern)
        if path.is_file() and path.stat().st_size > 0 and path.stat().st_mtime_ns >= started_ns
    ]
    return max(candidates, key=lambda path: path.stat().st_mtime_ns) if candidates else None


def run_formal_synth(
    candidate: dict[str, Any],
    source: Path,
    project: Path,
    output: Path,
    env: dict[str, str],
    *,
    timeout_seconds: int = 3600,
) -> dict[str, Any]:
    if timeout_seconds < 1:
        raise ExperimentError("synthesis timeout must be positive")
    config, variant = write_synth_project(candidate, source, project)
    orfs_root = Path(env["ORFS_ROOT"]).resolve()
    flow_dir = orfs_root / "flow"
    log_path = output / "synth.log"
    output.mkdir(parents=True, exist_ok=True)
    common = [
        "make",
        "-C",
        str(flow_dir),
        f"DESIGN_CONFIG={config.resolve()}",
        f"FLOW_VARIANT={variant}",
    ]
    clean = run([*common, "clean_all"], env=env)
    started_ns = time.time_ns()
    started = time.monotonic()
    with log_path.open("w", encoding="utf-8") as stream:
        stream.write("CLEAN RETURN CODE: " + str(clean.returncode) + "\n")
        stream.flush()
        result = run(
            [
                "timeout", "--signal=TERM", "--kill-after=60", str(timeout_seconds),
                *common, "synth",
            ],
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
        )
    elapsed = round(time.monotonic() - started, 3)
    flow_log = log_path.read_text(encoding="utf-8", errors="ignore")
    mapped = newest_mapped_netlist(
        flow_dir,
        candidate["top_module"],
        variant,
        started_ns,
    )
    copied: Path | None = None
    inspection = {
        "mapped_cells": 0,
        "input_ports": [],
        "output_ports": [],
        "cell_driven_outputs": [],
        "has_functional_io": False,
        "unresolved_module_evidence": [],
    }
    if mapped is not None:
        copied = output / "mapped_netlist.v"
        shutil.copyfile(mapped, copied)
        inspection = inspect_mapped_netlist(copied, flow_log)
        mapped_text = copied.read_text(encoding="utf-8", errors="ignore")
        normalized_mapped = "\n".join(
            line.strip() for line in mapped_text.splitlines()
            if line.strip() and not line.lstrip().startswith("//")
        )
        inspection["mapped_netlist_sha256"] = sha256_file(copied)
        inspection["mapped_netlist_normalized_sha256"] = sha256_text(normalized_mapped)
    passed = (
        result.returncode == 0
        and copied is not None
        and copied.stat().st_size > 0
        and MINIMUM_MAPPED_CELLS <= inspection["mapped_cells"] < MAXIMUM_MAPPED_CELLS_EXCLUSIVE
        and inspection["has_functional_io"]
        and not inspection["unresolved_module_evidence"]
    )
    return {
        "returncode": result.returncode,
        "elapsed_seconds": elapsed,
        "timed_out": result.returncode in {124, 137},
        "config_path": str(config),
        "flow_variant": variant,
        "source_mapped_netlist": str(mapped) if mapped else None,
        "mapped_netlist": str(copied) if copied else None,
        "synth_log": str(log_path),
        **inspection,
        "synth_qualified": passed,
    }


CLOSURE_EVIDENCE_FIELDS = (
    "missing_files",
    "missing_include_dirs",
    "escaped_paths",
    "undeclared_headers",
    "missing_include_references",
    "undeclared_readmem_files",
    "missing_readmem_references",
    "dynamic_readmem_references",
)


def input_qualification_failure(inputs: dict[str, Any]) -> str | None:
    """Return the deterministic input-gate failure shared by precheck and scoring."""
    if any(inputs.get(key) for key in CLOSURE_EVIDENCE_FIELDS):
        return "compilation_closure_incomplete"
    if not inputs.get("license_declared") or not inputs.get("license_location_ok"):
        return "license_evidence_incomplete"
    return None


def synthesis_qualification_failure(synth: dict[str, Any]) -> str | None:
    """Return the deterministic synthesis-gate failure shared by all evaluators."""
    if synth.get("returncode") != 0:
        return "synth_timeout" if synth.get("timed_out") else "synth_failed"
    if not synth.get("mapped_netlist"):
        return "mapped_netlist_missing"
    if synth.get("unresolved_module_evidence"):
        return "unresolved_module"
    cells = int(synth.get("mapped_cells") or 0)
    if cells < MINIMUM_MAPPED_CELLS:
        return "trivial_design"
    if cells >= MAXIMUM_MAPPED_CELLS_EXCLUSIVE:
        return "oversize_design"
    if not synth.get("has_functional_io"):
        return "nonfunctional_shell_or_io"
    return None


def technical_input_failure(inputs: dict[str, Any]) -> str | None:
    """Input failures that make the RTL technically unusable, excluding licensing."""
    if any(inputs.get(key) for key in CLOSURE_EVIDENCE_FIELDS):
        return "compilation_closure_incomplete"
    return None


def license_qualification_failure(inputs: dict[str, Any]) -> str | None:
    if not inputs.get("license_declared") or not inputs.get("license_location_ok"):
        return "license_evidence_incomplete"
    return None


def evaluate_candidate(
    candidate: dict[str, Any],
    root: Path,
    source_cache: Path,
    env: dict[str, str],
    *,
    synth_timeout_seconds: int = 3600,
) -> dict[str, Any]:
    candidate_id = candidate["candidate_id"]
    output = root / candidate_id
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    result: dict[str, Any] = {
        "candidate_id": candidate_id,
        "key": list(submission_key(candidate)),
        "started_at": now_iso(),
        "qualified": False,
        "technical_qualified": False,
        "publishable_qualified": False,
    }
    try:
        source = cached_candidate_source(
            candidate,
            source_cache,
            output / "git.log",
        )
        inputs = verify_candidate_inputs(candidate, source)
        result["source_evidence"] = inputs
        technical_failure = technical_input_failure(inputs)
        if technical_failure:
            result["technical_failure_class"] = technical_failure
            result["publishable_failure_class"] = technical_failure
            result["failure_class"] = technical_failure
            return result
        synth = run_formal_synth(
            candidate,
            source,
            output / "project",
            output,
            env,
            timeout_seconds=synth_timeout_seconds,
        )
        result["synthesis"] = synth
        technical_failure = synthesis_qualification_failure(synth)
        result["technical_failure_class"] = technical_failure
        result["technical_qualified"] = technical_failure is None
        license_failure = license_qualification_failure(inputs)
        publishable_failure = technical_failure or license_failure
        result["publishable_failure_class"] = publishable_failure
        result["publishable_qualified"] = publishable_failure is None
        # Backward-compatible alias. Primary method summaries use the explicit fields.
        result["qualified"] = result["publishable_qualified"]
        result["failure_class"] = publishable_failure
        return result
    except (ExperimentError, OSError, subprocess.SubprocessError) as exc:
        result["failure_class"] = "evaluator_exception"
        result["error"] = str(exc)
        return result
    finally:
        result["ended_at"] = now_iso()
        result["evaluation_seconds"] = round(time.monotonic() - started, 3)
        write_json_atomic(output / "result.json", result)


def batch_summary(
    results: list[dict[str, Any]], *, target_candidates: int = 25
) -> dict[str, Any]:
    repositories = Counter(
        str(item["key"][0]).rstrip("/")
        for item in results
        if item.get("key")
    )
    total = len(results)
    shares = [count / total for count in repositories.values()] if total else []
    hhi = sum(value * value for value in shares)
    technical = [item for item in results if item.get("technical_qualified", item.get("qualified"))]
    qualified = [item for item in results if item.get("publishable_qualified", item.get("qualified"))]
    qualified_repositories = Counter(
        str(item["key"][0]).rstrip("/")
        for item in qualified
        if item.get("key")
    )
    qualified_total = sum(qualified_repositories.values())
    qualified_shares = (
        [count / qualified_total for count in qualified_repositories.values()]
        if qualified_total else []
    )
    qualified_hhi = sum(value * value for value in qualified_shares)
    qualified_effective_repositories = 1.0 / qualified_hhi if qualified_hhi else 0.0
    failure_classes = Counter(item.get("failure_class") or "qualified" for item in results)
    buckets = Counter()
    for item in qualified:
        cells = int((item.get("synthesis") or {}).get("mapped_cells", 0))
        if cells >= 10000:
            buckets["10000-99999"] += 1
        elif cells >= 1000:
            buckets["1000-9999"] += 1
        else:
            buckets["100-999"] += 1
    return {
        "submitted_candidates": total,
        "evaluated_candidates": total,
        "qualified_candidates": len(qualified),
        "technical_qualified_candidates": len(technical),
        "publishable_qualified_candidates": len(qualified),
        "target_candidates": target_candidates,
        "submission_completion_rate": (
            min(total, target_candidates) / target_candidates
            if target_candidates
            else 0.0
        ),
        "independent_qualification_rate": (
            min(len(qualified), target_candidates) / target_candidates
            if target_candidates
            else 0.0
        ),
        "technical_qualification_rate": (
            min(len(technical), target_candidates) / target_candidates
            if target_candidates else 0.0
        ),
        "publishable_qualification_rate": (
            min(len(qualified), target_candidates) / target_candidates
            if target_candidates else 0.0
        ),
        "qualification_rate": len(qualified) / total if total else 0.0,
        "failure_classes": dict(sorted(failure_classes.items())),
        "cell_buckets": dict(sorted(buckets.items())),
        "unique_repositories": len(repositories),
        "largest_repository_share": max(shares, default=0.0),
        "repository_hhi": hhi,
        "effective_repository_count": 1.0 / hhi if hhi else 0.0,
        "qualified_unique_repositories": len(qualified_repositories),
        "qualified_largest_repository_share": max(qualified_shares, default=0.0),
        "qualified_repository_hhi": qualified_hhi,
        "qualified_effective_repository_count": qualified_effective_repositories,
        "qualified_repository_cap_compliant": all(
            count <= 4 for count in qualified_repositories.values()
        ),
        # Bounded [0, 1] and resistant to padding with unqualified repositories:
        # only independently qualified candidates contribute.
        "diverse_qualified_yield": (
            min(qualified_effective_repositories, target_candidates) / target_candidates
            if target_candidates else 0.0
        ),
    }


def evaluate_batch(args: argparse.Namespace) -> None:
    paths = campaign_paths(args.campaign_root)
    manifest = read_json(paths["manifest"])
    verify_bound_campaign(manifest)
    batch = find_batch(manifest, args.method_id, args.batch_id)
    method_batches = [
        row for row in manifest["batches"] if row["method_id"] == args.method_id
    ]
    unlocked = [
        int(row["batch_id"]) for row in method_batches
        if row["status"] not in {"submitted", "evaluating", "complete"}
    ]
    if unlocked:
        raise ExperimentError(
            "all four method batches must be digest-locked before formal evaluation; "
            f"unlocked batches: {unlocked}"
        )
    if batch["status"] != "submitted":
        raise ExperimentError(f"batch must be submitted, found status={batch['status']}")
    submission_path = Path(batch["submission_path"])
    if sha256_file(submission_path) != batch["submission_sha256"]:
        raise ExperimentError("locked submission digest mismatch")
    submission, errors = validate_submission(submission_path, campaign_root=paths["root"])
    if errors:
        raise ExperimentError("locked submission no longer validates:\n" + "\n".join(errors))
    score_eligible, reason = submission_score_eligibility(submission)
    if not score_eligible:
        raise ExperimentError("locked submission is not score eligible: " + str(reason))
    evaluation_root = paths["evaluation"] / f"{args.method_id}.batch{args.batch_id}"
    if evaluation_root.exists():
        raise ExperimentError(f"formal evaluation already exists: {evaluation_root}")
    evaluation_root.mkdir(parents=True)
    batch["status"] = "evaluating"
    write_json_atomic(paths["manifest"], manifest)
    env = manifest_toolchain_env(manifest)
    env.update({"NUM_CORES": str(args.cores), "ORFS_MAX_CPUS": str(args.cores)})
    source_cache = evaluation_root / "_source_cache"
    source_cache.mkdir()
    results = []
    for candidate in submission["candidates"]:
        result = evaluate_candidate(candidate, evaluation_root, source_cache, env)
        write_json_atomic(evaluation_root / candidate["candidate_id"] / "result.json", result)
        results.append(result)
        if result.get("failure_class") == "evaluator_exception":
            failed_path = evaluation_root.with_name(
                evaluation_root.name
                + ".failed."
                + datetime.now().strftime("%Y%m%d_%H%M%S")
            )
            evaluation_root.replace(failed_path)
            batch["status"] = "submitted"
            write_json_atomic(paths["manifest"], manifest)
            raise ExperimentError(
                "evaluation aborted because evaluator infrastructure failed; "
                f"diagnostics archived at {failed_path}"
            )
    report = {
        "schema_version": "1.0",
        "experiment_id": submission["experiment_id"],
        "method_id": args.method_id,
        "batch_id": args.batch_id,
        "submission_sha256": batch["submission_sha256"],
        "evaluator_started_from_clean_outputs": True,
        "evaluator_profile": read_json(TASK_SPEC)["evaluator_profile"],
        "toolchain": manifest["toolchain"],
        "summary": batch_summary(
            results,
            target_candidates=int(
                read_json(TASK_SPEC)["batch_policy"]["target_candidates_per_batch"]
            ),
        ),
        "results": results,
    }
    report_path = paths["reports"] / f"{args.method_id}.batch{args.batch_id}.evaluation.json"
    write_json_atomic(report_path, report)
    batch.update(
        {
            "status": "complete",
            "evaluation_path": str(report_path),
            "evaluation_sha256": sha256_file(report_path),
        }
    )
    write_json_atomic(paths["manifest"], manifest)
    summary = report["summary"]
    print(
        f"{args.method_id}/batch{args.batch_id}: "
        f"{summary['qualified_candidates']}/{summary['evaluated_candidates']} qualified"
    )


def summarize_method(args: argparse.Namespace) -> None:
    """Create the primary 100-slot result after all four batches complete."""
    paths = campaign_paths(args.campaign_root)
    manifest = read_json(paths["manifest"])
    verify_bound_campaign(manifest)
    method_row = next(
        row for row in manifest["method_reports"] if row["method_id"] == args.method_id
    )
    if method_row["status"] != "pending":
        raise ExperimentError("method report is already finalized")
    batches = sorted(
        (row for row in manifest["batches"] if row["method_id"] == args.method_id),
        key=lambda row: int(row["batch_id"]),
    )
    if len(batches) != 4 or any(row["status"] != "complete" for row in batches):
        states = {int(row["batch_id"]): row["status"] for row in batches}
        raise ExperimentError(
            "method summary requires four completed formal evaluations; "
            f"states={states}"
        )

    batch_reports: list[dict[str, Any]] = []
    submissions: list[dict[str, Any]] = []
    for batch in batches:
        report_path = Path(str(batch["evaluation_path"]))
        submission_path = Path(str(batch["submission_path"]))
        if sha256_file(report_path) != batch["evaluation_sha256"]:
            raise ExperimentError(f"evaluation digest mismatch: {report_path}")
        if sha256_file(submission_path) != batch["submission_sha256"]:
            raise ExperimentError(f"submission digest mismatch: {submission_path}")
        batch_reports.append(read_json(report_path))
        submissions.append(read_json(submission_path))

    results = [item for report in batch_reports for item in report["results"]]
    submitted = [item for submission in submissions for item in submission["candidates"]]
    task = read_json(TASK_SPEC)
    target = int(task["method_aggregate_policy"]["target_candidates_per_method"])
    if target != 100:
        raise ExperimentError("method aggregate target changed after campaign freeze")

    # Exact evaluator-produced mapped-netlist equivalence is a conservative
    # duplicate-family gate. The first occurrence receives publication credit.
    seen_fingerprints: dict[str, str] = {}
    duplicate_ids: list[str] = []
    publishable = 0
    technical = 0
    qualified_repositories: Counter[str] = Counter()
    for result in results:
        if result.get("technical_qualified", result.get("qualified")):
            technical += 1
        if not result.get("publishable_qualified", result.get("qualified")):
            continue
        fingerprint = str(
            (result.get("synthesis") or {}).get("mapped_netlist_normalized_sha256")
            or (result.get("source_evidence") or {}).get("closure_sha256")
            or ""
        )
        if fingerprint and fingerprint in seen_fingerprints:
            duplicate_ids.append(str(result["candidate_id"]))
            continue
        if fingerprint:
            seen_fingerprints[fingerprint] = str(result["candidate_id"])
        publishable += 1
        qualified_repositories[normalized_repo_url(str(result["key"][0]))] += 1

    total_tokens = sum(
        int((submission.get("resource_usage") or {}).get("total_tokens") or 0)
        for submission in submissions
    )
    total_time = sum(
        float((submission.get("resource_usage") or {}).get("method_runtime_seconds") or 0)
        for submission in submissions
    )
    total_cost = sum(
        float((submission.get("resource_usage") or {}).get("cost_usd") or 0)
        for submission in submissions
    )
    repo_total = sum(qualified_repositories.values())
    repo_hhi = (
        sum((count / repo_total) ** 2 for count in qualified_repositories.values())
        if repo_total else 0.0
    )
    effective_repositories = 1.0 / repo_hhi if repo_hhi else 0.0
    report = {
        "schema_version": "1.0",
        "experiment_id": task["experiment_id"],
        "method_id": args.method_id,
        "fixed_denominator": target,
        "all_batches_locked_before_evaluation": True,
        "batch_report_sha256": [
            sha256_file(Path(str(row["evaluation_path"]))) for row in batches
        ],
        "summary": {
            "submission_completion": len(submitted),
            "submission_completion_rate": min(len(submitted), target) / target,
            "technical_qualification": technical,
            "technical_qualification_rate": min(technical, target) / target,
            "publishable_qualification": publishable,
            "publishable_qualification_rate": min(publishable, target) / target,
            "exact_design_duplicates_excluded": len(duplicate_ids),
            "duplicate_candidate_ids": duplicate_ids,
            "qualified_unique_repositories": len(qualified_repositories),
            "qualified_effective_repository_count": effective_repositories,
            "diverse_qualified_yield": min(effective_repositories, target) / target,
            "total_external_llm_tokens": total_tokens,
            "total_method_wall_time_seconds": round(total_time, 3),
            "total_api_cost_usd": round(total_cost, 6),
        },
        "batches": [report["summary"] for report in batch_reports],
    }
    report_path = paths["reports"] / f"{args.method_id}.method.evaluation.json"
    if report_path.exists():
        raise ExperimentError(f"method report already exists: {report_path}")
    write_json_atomic(report_path, report)
    method_row.update(
        {
            "status": "complete",
            "report_path": str(report_path),
            "report_sha256": sha256_file(report_path),
        }
    )
    write_json_atomic(paths["manifest"], manifest)
    print(
        f"{args.method_id}: publishable={publishable}/{target}, "
        f"technical={technical}/{target}, submitted={len(submitted)}/{target}"
    )


def lint_protocol() -> None:
    errors: list[str] = []
    task = read_json(TASK_SPEC)
    if task.get("experiment_id") != "r2g-exp1-rtl-acquisition-v1":
        errors.append("unexpected experiment_id")
    if {row.get("method_id") for row in task.get("methods", [])} != METHOD_IDS:
        errors.append("task spec must contain the seven frozen method IDs")
    if task.get("method_budget", {}).get("search_requests") != 120:
        errors.append("search request budget must be 120")
    profile = task.get("evaluator_profile", {})
    if profile.get("minimum_mapped_cells") != MINIMUM_MAPPED_CELLS:
        errors.append("minimum mapped cell threshold must be 100")
    if profile.get("maximum_mapped_cells_exclusive") != MAXIMUM_MAPPED_CELLS_EXCLUSIVE:
        errors.append("maximum mapped cell threshold must be exclusive 100000")
    batch_policy = task.get("batch_policy", {})
    if batch_policy.get("target_unique_repositories_per_batch") != 12:
        errors.append("target unique repositories per batch must be 12")
    if batch_policy.get("maximum_candidates_per_repository") != 4:
        errors.append("maximum candidates per repository must be 4")
    aggregate = task.get("method_aggregate_policy", {})
    if aggregate.get("target_candidates_per_method") != 100:
        errors.append("method aggregate target must be 100")
    if aggregate.get("all_batches_must_lock_before_formal_evaluation") is not True:
        errors.append("all method batches must lock before formal evaluation")
    budget = task.get("method_budget", {})
    if budget.get("vanilla_max_turns_per_batch") != 100:
        errors.append("Vanilla max turns per batch must be 100")
    if budget.get("vanilla_max_output_tokens_per_turn") != 4096:
        errors.append("Vanilla max output tokens per turn must be 4096")
    for schema in (SUBMISSION_SCHEMA, MANIFEST_SCHEMA):
        schema_errors = Draft202012Validator.check_schema(read_json(schema))
        if schema_errors:
            errors.append(f"invalid schema: {schema}")
    if errors:
        raise ExperimentError("\n".join(errors))
    print("Experiment 1 protocol artifacts are internally consistent.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("lint-protocol")

    init = subparsers.add_parser("init-campaign")
    init.add_argument("--campaign-root", type=Path, required=True)
    init.add_argument("--campaign-id")
    init.add_argument(
        "--model-routes",
        type=Path,
        default=EXPERIMENT_DIR / "experiment1_model_routes.json",
        help="Route configuration to bind into this campaign.",
    )
    init.add_argument("--model-preflight", type=Path, required=True)
    init.add_argument(
        "--benchmark-registry",
        type=Path,
        default=DEFAULT_BENCHMARK_REGISTRY,
        help="Immutable contamination registry bound into the campaign.",
    )
    init.add_argument(
        "--orfs-root",
        type=Path,
        help="Git-traceable ORFS checkout to freeze into the campaign manifest.",
    )
    init.add_argument(
        "--non-scoring-canary",
        action="store_true",
        help="Mark this campaign as diagnostic and permanently ineligible for paper scoring.",
    )
    init.add_argument(
        "--allow-dirty-canary",
        action="store_true",
        help="Allow initialization for diagnostics; the campaign is not a paper result.",
    )

    lint = subparsers.add_parser("lint-submission")
    lint.add_argument("--submission", type=Path, required=True)
    lint.add_argument("--campaign-root", type=Path)

    accept = subparsers.add_parser("accept-submission")
    accept.add_argument("--campaign-root", type=Path, required=True)
    accept.add_argument("--submission", type=Path, required=True)

    evaluate = subparsers.add_parser("evaluate-batch")
    evaluate.add_argument("--campaign-root", type=Path, required=True)
    evaluate.add_argument("--method-id", choices=sorted(METHOD_IDS), required=True)
    evaluate.add_argument("--batch-id", type=int, choices=range(1, 5), required=True)
    evaluate.add_argument("--cores", type=int, default=4)
    summarize = subparsers.add_parser("summarize-method")
    summarize.add_argument("--campaign-root", type=Path, required=True)
    summarize.add_argument("--method-id", choices=sorted(METHOD_IDS), required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "lint-protocol":
            lint_protocol()
        elif args.command == "init-campaign":
            init_campaign(args)
        elif args.command == "lint-submission":
            _, errors = validate_submission(args.submission, campaign_root=args.campaign_root)
            if errors:
                raise ExperimentError("\n".join(errors))
            print(f"Submission OK: {args.submission}")
        elif args.command == "accept-submission":
            accept_submission(args)
        elif args.command == "evaluate-batch":
            evaluate_batch(args)
        elif args.command == "summarize-method":
            summarize_method(args)
    except ExperimentError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
