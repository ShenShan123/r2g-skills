#!/usr/bin/env python3
"""Audit Experiment 4 before any scored converter development or evaluation."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any


SEMANTIC_GROUPS = ('alignment', 'causal', 'identity', 'label', 'mask', 'numeric', 'topology')
REQUIRED_LABEL_COVERAGE = (
    'net.routed_wirelength_um', 'net.ground_cap_pF',
    'pin.setup_slack_ns', 'pin.hold_slack_ns',
)


def now() -> str:
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


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def env_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if value.strip().strip("\"'"):
            keys.add(key.strip())
    return keys


def command_version(command: list[str]) -> str:
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
    return (result.stdout or "").splitlines()[0].strip()


def frozen_runtime_paths(root: Path) -> set[Path]:
    paths: set[Path] = set()
    for directory in (root / "runtime", root / "protocol"):
        for path in directory.rglob("*"):
            relative = path.relative_to(root)
            if not path.is_file():
                continue
            if "__pycache__" in relative.parts or ".pytest_cache" in relative.parts:
                continue
            if path.suffix in {".pyc", ".pyo"}:
                continue
            paths.add(relative)
    return paths


def verify_checksum_manifest(root: Path, manifest: Path) -> tuple[bool, dict[str, Any]]:
    expected = frozen_runtime_paths(root)
    recorded: dict[Path, str] = {}
    malformed: list[str] = []
    for line_number, raw in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            digest, relative_text = raw.split("  ", 1)
            relative = Path(relative_text)
        except ValueError:
            malformed.append(f"line {line_number}")
            continue
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            malformed.append(f"line {line_number}: invalid digest")
            continue
        if relative.is_absolute() or ".." in relative.parts:
            malformed.append(f"line {line_number}: unsafe path")
            continue
        recorded[relative] = digest

    missing = sorted(str(path) for path in expected - recorded.keys())
    extra = sorted(str(path) for path in recorded.keys() - expected)
    mismatched: list[str] = []
    for relative in sorted(expected & recorded.keys()):
        path = root / relative
        if sha256_file(path) != recorded[relative]:
            mismatched.append(str(relative))
    detail = {
        "path": str(manifest),
        "sha256": sha256_file(manifest),
        "expected_files": len(expected),
        "recorded_files": len(recorded),
        "malformed": malformed,
        "missing": missing,
        "extra": extra,
        "mismatched": mismatched,
    }
    return not (malformed or missing or extra or mismatched), detail


def confirmatory_cohort_errors(cohort: dict[str, Any], cohort_path: Path) -> list[str]:
    errors: list[str] = []
    policy = cohort.get('selection_policy') or {}
    source = cohort.get('source') or {}
    audit = cohort.get('independence_audit') or {}
    threshold = policy.get('near_duplicate_threshold')
    if cohort.get('status') != 'frozen_confirmatory':
        errors.append('cohort status is not frozen_confirmatory')
    if policy.get('uses_graph_outputs') is not False:
        errors.append('selection must not use graph outputs')
    if policy.get('source_group_disjoint_across_all_splits') is not True:
        errors.append('source groups are not declared disjoint')
    if policy.get('structural_near_duplicate_disjoint_across_all_splits') is not True:
        errors.append('structural near-duplicate groups are not declared disjoint')
    if not isinstance(threshold, (int, float)) or not 0 < threshold <= 0.5:
        errors.append('near-duplicate threshold must be within (0, 0.5]')
    maximum = audit.get('maximum_similarity')
    if not isinstance(maximum, (int, float)) or not isinstance(threshold, (int, float)) or maximum >= threshold:
        errors.append('selected cohort violates the structural similarity threshold')
    if audit.get('pair_count') != 465:
        errors.append('31 selected cases must have exactly 465 audited pairs')
    inventory_text = source.get('inventory')
    inventory = Path(inventory_text) if isinstance(inventory_text, str) else None
    if inventory is None or not inventory.is_file():
        errors.append('source inventory is missing')
    elif sha256_file(inventory) != source.get('inventory_sha256'):
        errors.append('source inventory hash changed')
    else:
        inventory_data = read_json(inventory)
        if inventory_data.get('schema_version') != 'experiment4-unseen-inventory-2.0':
            errors.append('source inventory lacks structural clone audit v2')
        accepted = {row.get('task_id') for row in inventory_data.get('candidates') or []
                    if not row.get('exclusion_reasons')}
        selected = {row.get('task_id') for rows in (cohort.get('splits') or {}).values() for row in rows}
        if not selected <= accepted:
            errors.append('selected cohort contains a task absent from accepted unseen inventory')
    if cohort_path.name == 'cohort.json' and source.get('inventory') == str(cohort_path):
        errors.append('cohort cannot cite itself as its inventory')
    return errors


def semantic_validation_errors(validation_root: Path, runtime_feature_script: Path) -> list[str]:
    errors: list[str] = []
    aggregate_path = validation_root / 'audit_v0_3_final/aggregate.json'
    plan_path = validation_root / 'validation_plan.json'
    geometry_path = validation_root / 'geometry_validation_report.json'
    for path in (aggregate_path, plan_path, geometry_path):
        if not path.is_file():
            errors.append(f'missing semantic validation evidence: {path.name}')
    if errors:
        return errors
    aggregate = read_json(aggregate_path)
    plan = read_json(plan_path)
    geometry = read_json(geometry_path)
    if aggregate.get('planned') != 24 or aggregate.get('finished') != 24:
        errors.append('semantic validation did not finish all 24 development-evidence cases')
    if aggregate.get('oracle_errors'):
        errors.append('semantic validation contains oracle errors')
    if aggregate.get('failures_and_unassessable'):
        errors.append('semantic validation contains failed or unassessable core checks')
    counts = (aggregate.get('group_counts') or {}).get('r2g-geometry-fix') or {}
    for group in SEMANTIC_GROUPS:
        if counts.get(group) != {'PASS': 24}:
            errors.append(f'core semantic group is not 24/24 PASS: {group}')
    coverage = (aggregate.get('label_coverage_route_stage') or {}).get('r2g-geometry-fix') or {}
    for name in REQUIRED_LABEL_COVERAGE:
        row = coverage.get(name) or {}
        if not row.get('report_expected') or row.get('missing') or row.get('incorrect') or row.get('unparsed_blocks'):
            errors.append(f'label oracle coverage is incomplete: {name}')
    if geometry.get('change_scope_counts') != {'PASS': 96}:
        errors.append('geometry change-scope validation is not 96/96 PASS')
    if not runtime_feature_script.is_file():
        errors.append('runtime feature script is missing')
    elif plan.get('feature_patch_sha256') != sha256_file(runtime_feature_script):
        errors.append('runtime feature script differs from the independently validated implementation')
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--api-env-file", type=Path, required=True)
    parser.add_argument("--route-canary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument('--phase', choices=('converter_development', 'hidden_evaluation'),
                        default='converter_development')
    parser.add_argument('--require-confirmatory-cohort', action='store_true')
    parser.add_argument('--semantic-validation-root', type=Path)
    args = parser.parse_args()

    repo = args.repo.resolve()
    root = args.campaign_root.resolve()
    cohort_path = root / "cohort.json"
    cohort = read_json(cohort_path)
    protocol = load_module("experiment4_protocol", root / "runtime/experiments/experiment4_protocol.py")
    runner = load_module("experiment4_runner", root / "runtime/experiments/run_experiment4_graph_conversion.py")
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, **detail: Any) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    try:
        protocol.validate_cohort(cohort, check_files=True)
        add(
            "cohort",
            True,
            sha256=sha256_file(cohort_path),
            counts={name: len(rows) for name, rows in cohort["splits"].items()},
        )
    except Exception as exc:
        add("cohort", False, error=repr(exc))

    if args.require_confirmatory_cohort:
        errors = confirmatory_cohort_errors(cohort, cohort_path)
        add('confirmatory_cohort_independence', not errors, errors=errors,
            maximum_structural_similarity=(cohort.get('independence_audit') or {}).get('maximum_similarity'))

    if args.semantic_validation_root:
        semantic_root = args.semantic_validation_root.resolve()
        feature_script = root / 'runtime/r2g-skills/def-graph/scripts/r2g2/02_extract_features.py'
        errors = semantic_validation_errors(semantic_root, feature_script)
        add('independent_semantic_validation', not errors, errors=errors,
            evidence_root=str(semantic_root),
            aggregate_sha256=sha256_file(semantic_root / 'audit_v0_3_final/aggregate.json')
            if (semantic_root / 'audit_v0_3_final/aggregate.json').is_file() else None)
    elif args.require_confirmatory_cohort:
        add('independent_semantic_validation', False, errors=['--semantic-validation-root is required'])

    for split, expected in (("canary", 1), ("development", 6)):
        failures: dict[str, list[str]] = {}
        for row in cohort["splits"][split]:
            state_path = root / "inputs" / row["task_id"] / "input_attestation.json"
            if not state_path.is_file():
                failures[row["task_id"]] = ["attestation missing"]
                continue
            errors = runner.input_attestation_errors(read_json(state_path), row)
            if errors:
                failures[row["task_id"]] = errors
        add(f"{split}_inputs", not failures, expected=expected, failures=failures)

    hidden_materialized = [
        row["task_id"]
        for row in cohort["splits"]["hidden_test"]
        if (root / "inputs" / row["task_id"] / "input_attestation.json").exists()
    ]
    add("hidden_inputs_unmaterialized", not hidden_materialized, materialized=hidden_materialized)

    if args.phase == 'hidden_evaluation':
        freeze_errors = runner.frozen_converter_gate_errors(root)
        add('frozen_converter_gate', not freeze_errors, errors=freeze_errors)

    canary_score = root / "methods/r2g-frozen-v3" / cohort["splits"]["canary"][0]["task_id"] / "score.json"
    score = read_json(canary_score) if canary_score.is_file() else {}
    add(
        "r2g_canary",
        score.get("strict_pass") is True
        and score.get("config_immutable") is True
        and score.get("contract_fraction") == 1
        and score.get("static_contract_fraction") == 1,
        score=score,
    )

    full_contract_path = root / "protocol/public_contract_v2.json"
    model_contract_path = root / "protocol/public_model_contract_v2.json"
    contract_failures: list[str] = []
    if not full_contract_path.is_file() or not model_contract_path.is_file():
        contract_failures.append("one or both v2 contract files are missing")
    else:
        full_contract = read_json(full_contract_path)
        model_contract = read_json(model_contract_path)
        if full_contract.get("schema_version") != "experiment4-public-contract-2.0":
            contract_failures.append("full contract schema version mismatch")
        if model_contract.get("schema_version") != "experiment4-public-model-contract-2.0":
            contract_failures.append("model contract schema version mismatch")
        model_text = model_contract_path.read_text(encoding="utf-8")
        sensitive = ["/home/", "exp1_", *[row["task_id"] for split in cohort["splits"].values() for row in split]]
        leaked = sorted(token for token in sensitive if token in model_text)
        if leaked:
            contract_failures.append(f"model contract leaks identities or paths: {leaked[:5]}")
    add(
        "public_contract_v2",
        not contract_failures,
        failures=contract_failures,
        full_sha256=sha256_file(full_contract_path) if full_contract_path.is_file() else None,
        model_sha256=sha256_file(model_contract_path) if model_contract_path.is_file() else None,
    )

    routes = read_json(root / "protocol/experiment4_model_routes.json")["routes"]
    if args.route_canary.is_file():
        canary = read_json(args.route_canary)
        passing = {row["model_key"]: row for row in canary.get("results") or [] if row.get("status") == "passed"}
        route_match = all(
            route["model_key"] in passing
            and passing[route["model_key"]].get("model_id") == route["model_id"]
            for route in routes
        )
        add(
            "model_route_canaries",
            canary.get("status") == "passed" and route_match,
            evidence=str(args.route_canary.resolve()),
            evidence_sha256=sha256_file(args.route_canary),
            models=sorted(passing),
        )
    else:
        add('model_route_canaries', False, evidence=str(args.route_canary.resolve()),
            error='fresh route canary evidence is missing')
    keys = env_keys(args.api_env_file)
    credentials = {route["model_key"]: route["api_key_env"] in keys for route in routes}
    add("api_credentials_present", all(credentials.values()), credentials_present=credentials)

    limits = read_json(root / "protocol/experiment4_resource_limits.json")
    openroad = Path(limits["toolchain"]["openroad_exe"])
    yosys = Path(limits["toolchain"]["yosys_exe"])
    orfs_root = Path(limits["toolchain"]["orfs_root"])
    toolchain_ok = openroad.is_file() and yosys.is_file() and orfs_root.is_dir()
    add(
        "toolchain",
        toolchain_ok,
        openroad=str(openroad),
        openroad_sha256=sha256_file(openroad) if openroad.is_file() else None,
        openroad_version=command_version([str(openroad), "-version"]) if openroad.is_file() else None,
        yosys=str(yosys),
        yosys_sha256=sha256_file(yosys) if yosys.is_file() else None,
        yosys_version=command_version([str(yosys), "-V"]) if yosys.is_file() else None,
        orfs_commit=command_version(["git", "-C", str(orfs_root), "rev-parse", "HEAD"])
        if orfs_root.is_dir()
        else None,
    )

    free_gib = shutil.disk_usage(root).free // (1024**3)
    minimum = int(limits["execution"]["minimum_free_disk_gib"])
    add("free_disk", free_gib >= minimum, free_gib=free_gib, minimum_gib=minimum)

    checksum_manifest = root / "runtime_and_protocol.SHA256SUMS"
    if checksum_manifest.is_file():
        checksum_ok, checksum_detail = verify_checksum_manifest(root, checksum_manifest)
        add("runtime_checksum_manifest", checksum_ok, **checksum_detail)
    else:
        add("runtime_checksum_manifest", False, path=str(checksum_manifest), error="missing")

    failed = [check["name"] for check in checks if not check["passed"]]
    payload = {
        "schema_version": "experiment4-prelaunch-audit-2.0",
        "created_at": now(),
        "campaign_root": str(root),
        "cohort_sha256": sha256_file(cohort_path),
        "repo": str(repo),
        "repo_commit": command_version(["git", "-C", str(repo), "rev-parse", "HEAD"]),
        "phase": args.phase,
        "status": (f"ready_for_{args.phase}" if not failed else "blocked"),
        "failed_checks": failed,
        "checks": checks,
        "next_boundary": ("freeze all three LLM converters before materializing hidden_test"
                          if args.phase == 'converter_development'
                          else 'materialize and evaluate hidden_test exactly once'),
    }
    write_json(args.output, payload)
    print(json.dumps({"status": payload["status"], "failed_checks": failed}, sort_keys=True))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
