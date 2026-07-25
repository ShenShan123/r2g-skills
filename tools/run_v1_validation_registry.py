#!/usr/bin/env python3
"""Validate, expand, and execute the R2G V1 validation registry.

The registry uses the JSON subset of YAML 1.2, so this runner needs only the Python
standard library. Formal execution is fail-closed: an unfrozen protocol, unbound
fixture, or pending evaluator cannot produce a pass verdict — such subcases are
recorded as `not_scheduled`, never silently skipped and never fabricated into a
pass or a system-under-test failure.

`gates` executes the machine binding of the protocol's Section 2 gate conditions
(`gate_conditions` in the registry): `suite`/`builtin`/`command` conditions run
fail-closed with shared suites deduplicated, while `formal`/`operator` conditions
are reported as deferred with counts — never silently skipped. An executable pass
is a prerequisite for, never a substitute for, the frozen formal campaign.

The script is location-independent: the repo root is discovered by walking up to
the nearest `.git`, and the registry defaults to the sibling
`v1_validation_registry.yaml`. The canonical home for both is `tools/` (repo-level
operator tooling, beside `verify_graph_dataset.py` and `check_db_integrity.py`);
the protocol document stays in `docs/superpowers/plans/`, and run evidence lands
in the gitignored `docs/superpowers/plans/validation-reports/` regardless of where
the script lives (GC-OPS-02).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import itertools
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Iterable


def _find_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    raise SystemExit(f"cannot locate repo root (.git) above {start}")


HERE = Path(__file__).resolve().parent
REPO = _find_repo_root(HERE)
DEFAULT_REGISTRY = HERE / "v1_validation_registry.yaml"
# Evidence stays at the spec-documented, gitignored location even though the
# runner lives in tools/ — GC-OPS-02 guards exactly this path.
REPORTS_DIR = REPO / "docs" / "superpowers" / "plans" / "validation-reports"
REQ_RE = re.compile(r"^\*\*((?:ENV|ACQ|FLOW|DATA|AGENT|OPS)-\d{3}):", re.MULTILINE)
VAL_RE = re.compile(r"^\*\*(VAL-(?:ENV|ACQ|FLOW|DATA|AGENT|OPS)-\d{3}):", re.MULTILINE)
GC_TAGS = r"(?:ENV|ACQ|SYNTH|R2F|CON|SIG|LRN|F2G|GRA|PUB|OPS)"
GC_RE = re.compile(rf"^\*\*(GC-{GC_TAGS}-\d{{2}}):", re.MULTILINE)
GC_ID_RE = re.compile(rf"GC-{GC_TAGS}-\d{{2}}")
SPEC_VERSION_RE = re.compile(r"^- Version: \*\*([0-9]+\.[0-9]+)\*\*", re.MULTILINE)
TARGET_PLATFORMS = ["nangate45", "sky130hd", "sky130hs"]
OUTPUT_TAIL_CHARS = 20000
DEFAULT_SUITE_TIMEOUT_S = 1800
DEFAULT_CONDITION_TIMEOUT_S = 300
GATE_CONDITION_KINDS = {"suite", "builtin", "command", "formal", "operator"}
EXECUTABLE_KINDS = {"suite", "builtin", "command"}
SKILLS = ("eda-install", "signoff-loop", "def-graph", "rtl-acquire")


class RegistryError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_registry(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryError(f"cannot load registry {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RegistryError("registry root must be an object")
    return value


def _duplicates(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def _check_dependency_cycles(cases: dict[str, dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    state: dict[str, int] = {}

    def visit(case_id: str, chain: list[str]) -> None:
        marker = state.get(case_id, 0)
        if marker == 2:
            return
        if marker == 1:
            start = chain.index(case_id) if case_id in chain else 0
            errors.append("dependency cycle: " + " -> ".join(chain[start:] + [case_id]))
            return
        state[case_id] = 1
        for dependency in cases[case_id].get("depends_on", []):
            if dependency in cases:
                visit(dependency, chain + [case_id])
        state[case_id] = 2

    for case_id in cases:
        visit(case_id, [])
    return errors


def _gate_covers(gate_requirements: list[str], requirement: str) -> bool:
    for entry in gate_requirements:
        if entry == requirement:
            return True
        if entry.endswith("-*") and requirement.startswith(entry[:-1]):
            return True
    return False


def _builtin_env_sh_parity(context: dict[str, Any]) -> tuple[bool, str]:
    """GC-ENV-02: the four <skill>/scripts/flow/_env.sh copies must be byte-identical."""
    repo: Path = context["repo"]
    digests: dict[str, str] = {}
    for skill in SKILLS:
        path = repo / "r2g-skills" / skill / "scripts" / "flow" / "_env.sh"
        if not path.is_file():
            return False, f"missing {path}"
        digests[skill] = sha256(path)
    return len(set(digests.values())) == 1, json.dumps(digests, indent=2)


def _builtin_skills_symlinked(context: dict[str, Any]) -> tuple[bool, str]:
    """GC-ENV-03: deployed skills must be symlinks into the canonical tree (2026-06-08 defect)."""
    repo: Path = context["repo"]
    canonical = (repo / "r2g-skills").resolve()
    problems: list[str] = []
    targets: dict[str, str] = {}
    for skill in SKILLS:
        link = repo / ".claude" / "skills" / skill
        if not link.is_symlink():
            problems.append(f"{link} is not a symlink (stale copy or missing deploy)")
            continue
        resolved = link.resolve()
        targets[skill] = str(resolved)
        if canonical not in resolved.parents:
            problems.append(f"{link} resolves outside the canonical tree: {resolved}")
    return (not problems), json.dumps({"targets": targets, "problems": problems}, indent=2)


def _builtin_policies_parse(context: dict[str, Any]) -> tuple[bool, str]:
    """GC-ACQ-01: every rtl-acquire policy JSON must parse as a non-empty document."""
    repo: Path = context["repo"]
    policy_dir = repo / "r2g-skills" / "rtl-acquire" / "references"
    files = sorted(policy_dir.glob("*.json"))
    if not files:
        return False, f"no policy JSON files under {policy_dir}"
    results: dict[str, str] = {}
    ok = True
    for path in files:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            results[path.name] = f"error: {exc}"
            ok = False
            continue
        if isinstance(value, (dict, list)) and value:
            results[path.name] = "ok"
        else:
            results[path.name] = "empty"
            ok = False
    return ok, json.dumps(results, indent=2)


def _builtin_reports_gitignored(context: dict[str, Any]) -> tuple[bool, str]:
    """GC-OPS-02: evidence output must be gitignored (probe a path INSIDE the dir —
    the dir-only pattern does not match the bare, possibly nonexistent dir path)."""
    repo: Path = context["repo"]
    probe_path = "docs/superpowers/plans/validation-reports/gate-conditions.json"
    probe = subprocess.run(
        ["git", "check-ignore", "-q", probe_path],
        cwd=repo, text=True, capture_output=True, check=False,
    )
    return probe.returncode == 0, f"git check-ignore {probe_path} -> rc={probe.returncode}"


def _builtin_registry_self_lint(context: dict[str, Any]) -> tuple[bool, str]:
    """GC-OPS-01: full traceability lint over the loaded registry."""
    warnings: list[str] = []
    errors = lint_registry(context["registry_path"], context["registry"], warnings)
    return (not errors), json.dumps({"errors": errors, "warnings": warnings}, indent=2)


BUILTIN_CHECKS = {
    "env_sh_parity": _builtin_env_sh_parity,
    "skills_symlinked": _builtin_skills_symlinked,
    "policies_parse": _builtin_policies_parse,
    "reports_gitignored": _builtin_reports_gitignored,
    "registry_self_lint": _builtin_registry_self_lint,
}


def lint_registry(
    registry_path: Path, registry: dict[str, Any], warnings: list[str] | None = None
) -> list[str]:
    errors: list[str] = []
    warnings = warnings if warnings is not None else []
    protocol = registry.get("protocol") or {}
    spec_path = REPO / str(protocol.get("source", ""))
    if not spec_path.is_file():
        errors.append(f"protocol source missing: {spec_path}")
        spec_requirements: set[str] = set()
        spec_validations: set[str] = set()
    else:
        spec_text = spec_path.read_text(encoding="utf-8")
        spec_requirements = set(REQ_RE.findall(spec_text))
        spec_validations = set(VAL_RE.findall(spec_text))
        expected_digest = str(protocol.get("sha256", ""))
        actual_digest = sha256(spec_path)
        if expected_digest != actual_digest:
            errors.append(
                f"protocol digest mismatch: registry={expected_digest} actual={actual_digest}"
            )
        version_match = SPEC_VERSION_RE.search(spec_text)
        if not version_match:
            errors.append("protocol source has no parseable '- Version: **X.Y**' line")
        elif version_match.group(1) != str(protocol.get("version", "")):
            errors.append(
                f"protocol version mismatch: registry={protocol.get('version')!r} "
                f"spec={version_match.group(1)!r}"
            )

    cases_list = registry.get("cases")
    if not isinstance(cases_list, list):
        return errors + ["cases must be a list"]
    case_ids = [str(case.get("id", "")) for case in cases_list if isinstance(case, dict)]
    requirement_ids = [
        str(case.get("requirement", "")) for case in cases_list if isinstance(case, dict)
    ]
    for duplicate in _duplicates(case_ids):
        errors.append(f"duplicate case id: {duplicate}")
    for duplicate in _duplicates(requirement_ids):
        errors.append(f"duplicate requirement mapping: {duplicate}")

    expected_count = int(
        (registry.get("formal_execution_policy") or {}).get("mandatory_case_count", -1)
    )
    if len(cases_list) != expected_count:
        errors.append(f"case count {len(cases_list)} does not equal expected {expected_count}")
    if set(case_ids) != spec_validations:
        errors.append(
            "registry/spec VAL mismatch: missing="
            f"{sorted(spec_validations - set(case_ids))} extra={sorted(set(case_ids) - spec_validations)}"
        )
    if set(requirement_ids) != spec_requirements:
        errors.append(
            "registry/spec REQ mismatch: missing="
            f"{sorted(spec_requirements - set(requirement_ids))} "
            f"extra={sorted(set(requirement_ids) - spec_requirements)}"
        )
    phase_order = list(registry.get("phase_order", []))
    gates = registry.get("gates") or {}
    fixtures = registry.get("fixtures") or {}
    for case in cases_list:
        if not isinstance(case, dict):
            errors.append("case entry is not an object")
            continue
        case_id = str(case.get("id", "<missing-id>"))
        requirement = str(case.get("requirement", ""))
        if case_id != f"VAL-{requirement}":
            errors.append(f"{case_id}: expected mapping VAL-{requirement}")
        gate = case.get("gate")
        if gate not in gates:
            errors.append(f"{case_id}: unknown gate {gate!r}")
        elif not _gate_covers(list(gates[gate].get("requirements", [])), requirement):
            errors.append(f"{case_id}: gate {gate} does not cover requirement {requirement}")
        if case.get("phase") not in phase_order:
            errors.append(f"{case_id}: unknown phase {case.get('phase')!r}")
        if not case.get("methods"):
            errors.append(f"{case_id}: methods missing")
        if not isinstance(case.get("depends_on"), list):
            errors.append(f"{case_id}: depends_on must be a list")
        if not isinstance(case.get("matrix"), dict) or not case.get("matrix"):
            errors.append(f"{case_id}: non-empty matrix required")
        evaluator = case.get("evaluator") or {}
        if evaluator.get("status") not in {"pending", "ready", "disabled"}:
            errors.append(f"{case_id}: invalid evaluator status")
        if not evaluator.get("handler"):
            errors.append(f"{case_id}: evaluator handler missing")
        has_platform_axis = "platform" in (case.get("matrix") or {})
        for fixture in case.get("fixtures", []):
            if fixture not in fixtures:
                errors.append(f"{case_id}: unknown fixture {fixture}")
            elif fixtures[fixture].get("parameterized_by_platform") and not has_platform_axis:
                warnings.append(
                    f"{case_id}: platform-parameterized fixture {fixture} "
                    "used without a platform axis"
                )
        for axis, values in (case.get("matrix") or {}).items():
            if values == "@target_platforms":
                continue
            if not isinstance(values, list) or not values:
                errors.append(f"{case_id}: matrix axis {axis} must be a non-empty list")

    cases = {case["id"]: case for case in cases_list if isinstance(case, dict) and case.get("id")}
    for case_id, case in cases.items():
        case_phase = case.get("phase")
        for dependency in case.get("depends_on", []):
            if dependency not in cases:
                errors.append(f"{case_id}: unknown dependency {dependency}")
                continue
            if dependency == case_id:
                errors.append(f"{case_id}: self dependency")
            dep_phase = cases[dependency].get("phase")
            if (
                case_phase in phase_order
                and dep_phase in phase_order
                and phase_order.index(dep_phase) > phase_order.index(case_phase)
            ):
                errors.append(
                    f"{case_id}: depends on {dependency} from later phase {dep_phase}"
                )
    errors.extend(_check_dependency_cycles(cases))

    for suite in registry.get("diagnostic_suites", []):
        if not suite.get("id") or not isinstance(suite.get("command"), list):
            errors.append("diagnostic suite requires id and command list")
        if suite.get("official_val_verdict") is not False:
            errors.append(f"{suite.get('id')}: diagnostic suite must not award VAL verdicts")
        timeout_s = suite.get("timeout_s", DEFAULT_SUITE_TIMEOUT_S)
        if not isinstance(timeout_s, int) or timeout_s <= 0:
            errors.append(f"{suite.get('id')}: timeout_s must be a positive integer")

    # Gate conditions: the machine binding of the spec's Section 2 GC-* conditions.
    suite_ids = {str(suite.get("id")) for suite in registry.get("diagnostic_suites", [])}
    spec_conditions = set(GC_RE.findall(spec_text)) if spec_path.is_file() else set()
    gate_conditions = registry.get("gate_conditions")
    if not isinstance(gate_conditions, dict):
        errors.append("gate_conditions must be an object keyed by gate")
    else:
        condition_ids: list[str] = []
        formal_case_refs: dict[str, set[str]] = {}
        for gate_name in gates:
            if not gate_conditions.get(gate_name):
                errors.append(f"gate {gate_name} has no gate_conditions entry")
        for gate_name, conditions in gate_conditions.items():
            if gate_name not in gates:
                errors.append(f"gate_conditions references unknown gate {gate_name}")
                continue
            if not isinstance(conditions, list):
                errors.append(f"gate_conditions[{gate_name}] must be a list")
                continue
            gate_requirements = list(gates[gate_name].get("requirements", []))
            for condition in conditions:
                if not isinstance(condition, dict):
                    errors.append(f"gate_conditions[{gate_name}] entry is not an object")
                    continue
                condition_id = str(condition.get("id", "<missing-id>"))
                condition_ids.append(condition_id)
                if not GC_ID_RE.fullmatch(condition_id):
                    errors.append(f"{condition_id}: invalid gate-condition id")
                if not condition.get("title"):
                    errors.append(f"{condition_id}: title missing")
                if not condition.get("evidence"):
                    errors.append(f"{condition_id}: evidence missing")
                requirements = condition.get("requirements")
                if not isinstance(requirements, list) or not requirements:
                    errors.append(f"{condition_id}: non-empty requirements list required")
                else:
                    for requirement in requirements:
                        if not _gate_covers(gate_requirements, str(requirement)):
                            errors.append(
                                f"{condition_id}: requirement {requirement} "
                                f"not covered by {gate_name}"
                            )
                kind = condition.get("kind")
                if kind == "suite":
                    if condition.get("suite") not in suite_ids:
                        errors.append(f"{condition_id}: unknown suite {condition.get('suite')!r}")
                elif kind == "builtin":
                    if condition.get("builtin") not in BUILTIN_CHECKS:
                        errors.append(
                            f"{condition_id}: unknown builtin {condition.get('builtin')!r}"
                        )
                elif kind == "command":
                    command = condition.get("command")
                    if not isinstance(command, list) or not command:
                        errors.append(f"{condition_id}: command must be a non-empty list")
                    timeout_s = condition.get("timeout_s", DEFAULT_CONDITION_TIMEOUT_S)
                    if not isinstance(timeout_s, int) or timeout_s <= 0:
                        errors.append(f"{condition_id}: timeout_s must be a positive integer")
                elif kind == "formal":
                    refs = condition.get("cases")
                    if not isinstance(refs, list) or not refs:
                        errors.append(f"{condition_id}: formal condition requires a cases list")
                    else:
                        for ref in refs:
                            target = cases.get(str(ref))
                            if target is None:
                                errors.append(f"{condition_id}: unknown formal case {ref}")
                            elif target.get("gate") != gate_name:
                                errors.append(
                                    f"{condition_id}: case {ref} belongs to gate "
                                    f"{target.get('gate')}, not {gate_name}"
                                )
                        formal_case_refs.setdefault(gate_name, set()).update(
                            str(ref) for ref in refs
                        )
                elif kind == "operator":
                    if not condition.get("procedure"):
                        errors.append(f"{condition_id}: operator condition requires a procedure")
                else:
                    errors.append(f"{condition_id}: invalid kind {kind!r}")
        for duplicate in _duplicates(condition_ids):
            errors.append(f"duplicate gate-condition id: {duplicate}")
        if spec_path.is_file() and set(condition_ids) != spec_conditions:
            errors.append(
                "registry/spec GC mismatch: missing="
                f"{sorted(spec_conditions - set(condition_ids))} "
                f"extra={sorted(set(condition_ids) - spec_conditions)}"
            )
        # Formal coverage: every VAL case is wired into a formal condition of its own
        # gate, so no formal scope can silently drop out of the gate map.
        for case_id, case in cases.items():
            if case_id not in formal_case_refs.get(str(case.get("gate")), set()):
                errors.append(
                    f"{case_id}: not referenced by any formal gate condition "
                    f"of {case.get('gate')}"
                )
    return errors


def _axis_values(registry: dict[str, Any], raw: Any) -> list[Any]:
    if raw == "@target_platforms":
        return list(registry.get("target_platforms", []))
    return list(raw)


def expand_case(registry: dict[str, Any], case: dict[str, Any]) -> list[dict[str, Any]]:
    axes = list(case["matrix"])
    values = [_axis_values(registry, case["matrix"][axis]) for axis in axes]
    expanded: list[dict[str, Any]] = []
    for combination in itertools.product(*values):
        parameters = dict(zip(axes, combination))
        suffix = ".".join(
            f"{axis}={re.sub(r'[^A-Za-z0-9_.-]+', '_', str(value))}"
            for axis, value in parameters.items()
        )
        expanded.append(
            {
                "test_id": case["id"],
                "subcase_id": f"{case['id']}.{suffix}",
                "requirement_id": case["requirement"],
                "gate": case["gate"],
                "phase": case["phase"],
                "parameters": parameters,
                "fixtures": case.get("fixtures", []),
                "depends_on": case.get("depends_on", []),
                "evaluator": case["evaluator"],
            }
        )
    return expanded


def selected_cases(registry: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    cases = registry["cases"]
    if getattr(args, "case", None):
        wanted = set(args.case)
        cases = [case for case in cases if case["id"] in wanted]
        missing = wanted - {case["id"] for case in cases}
        if missing:
            raise RegistryError(f"unknown case(s): {sorted(missing)}")
    if getattr(args, "gate", None):
        cases = [case for case in cases if case["gate"] in set(args.gate)]
    if getattr(args, "phase", None):
        cases = [case for case in cases if case["phase"] in set(args.phase)]
    return cases


def readiness_reasons(registry: dict[str, Any], case: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    policy = registry["formal_execution_policy"]
    if policy.get("require_frozen_protocol") and registry["protocol"].get("status") != "Frozen":
        reasons.append("protocol_not_frozen")
    if policy.get("require_ready_evaluator") and case["evaluator"].get("status") != "ready":
        reasons.append("evaluator_not_ready")
    if policy.get("require_bound_fixtures"):
        for fixture in case.get("fixtures", []):
            if registry["fixtures"][fixture].get("binding_status") != "bound":
                reasons.append(f"fixture_unbound:{fixture}")
    return sorted(set(reasons))


def command_lint(args: argparse.Namespace) -> int:
    registry_path = args.registry.resolve()
    registry = load_registry(registry_path)
    warnings: list[str] = []
    errors = lint_registry(registry_path, registry, warnings)
    for warning in warnings:
        print(f"WARN  {warning}")
    if errors:
        for error in errors:
            print(f"ERROR {error}")
        print(f"registry lint: FAIL ({len(errors)} error(s), {len(warnings)} warning(s))")
        return 1
    expanded = sum(len(expand_case(registry, case)) for case in registry["cases"])
    conditions = sum(len(v) for v in (registry.get("gate_conditions") or {}).values())
    print(
        f"registry lint: PASS cases={len(registry['cases'])} "
        f"expanded_subcases={expanded} gate_conditions={conditions} "
        f"protocol={registry['protocol']['version']} warnings={len(warnings)}"
    )
    return 0


def command_list(args: argparse.Namespace) -> int:
    registry = load_registry(args.registry.resolve())
    for case in selected_cases(registry, args):
        reasons = readiness_reasons(registry, case)
        readiness = "ready" if not reasons else "blocked"
        print(
            f"{case['id']:<15} {case['requirement']:<10} {case['gate']:<20} "
            f"{case['phase']:<22} {readiness:<7} {case['title']}"
        )
    return 0


def command_plan(args: argparse.Namespace) -> int:
    registry = load_registry(args.registry.resolve())
    records: list[dict[str, Any]] = []
    for case in selected_cases(registry, args):
        reasons = readiness_reasons(registry, case)
        expanded = expand_case(registry, case)
        if args.platform:
            expanded = [
                item for item in expanded
                if item["parameters"].get("platform") in (None, args.platform)
            ]
        records.append(
            {
                "test_id": case["id"],
                "requirement_id": case["requirement"],
                "gate": case["gate"],
                "phase": case["phase"],
                "expanded_subcases": len(expanded),
                "formal_readiness": "ready" if not reasons else "blocked",
                "blocking_reasons": reasons,
                "subcases": expanded if args.details else [],
            }
        )
    if args.json:
        print(json.dumps(records, indent=2, ensure_ascii=True))
    else:
        total = 0
        ready = 0
        for record in records:
            total += record["expanded_subcases"]
            if record["formal_readiness"] == "ready":
                ready += record["expanded_subcases"]
            reasons = ",".join(record["blocking_reasons"]) or "none"
            print(
                f"{record['test_id']:<15} subcases={record['expanded_subcases']:<4} "
                f"{record['formal_readiness']:<7} reasons={reasons}"
            )
        print(f"plan summary: cases={len(records)} subcases={total} ready={ready} blocked={total-ready}")
    return 0


def _load_handler(reference: str):
    module_name, separator, function_name = reference.partition(":")
    if not separator:
        raise RegistryError(f"invalid handler reference: {reference}")
    module = importlib.import_module(module_name)
    return getattr(module, function_name)


def resolve_validation_python(explicit: str | None = None) -> str:
    candidates = [
        explicit,
        os.environ.get("R2G_VALIDATION_PYTHON"),
        os.environ.get("R2G_GRAPH_PYTHON"),
        "/proj/workarea/user5/pyenvs/rtl2graph/bin/python",
        str(Path.home() / ".conda" / "envs" / "gnn_env" / "bin" / "python"),
        sys.executable,
        shutil.which("python3"),
    ]
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate:
            continue
        path = str(Path(candidate).expanduser())
        if path in seen or not os.access(path, os.X_OK):
            continue
        seen.add(path)
        probe = subprocess.run(
            [path, "-c", "import pytest, torch, torch_geometric, pandas"],
            text=True, capture_output=True, check=False,
            env={key: value for key, value in os.environ.items() if key != "PYTHONHOME"},
        )
        if probe.returncode == 0:
            return path
    raise RegistryError(
        "no validation Python with pytest, torch, torch_geometric, and pandas; "
        "pass --python PATH or set R2G_VALIDATION_PYTHON / R2G_GRAPH_PYTHON"
    )


def _tail(text: str) -> dict[str, Any]:
    truncated = len(text) > OUTPUT_TAIL_CHARS
    return {
        "truncated": truncated,
        "text": text[-OUTPUT_TAIL_CHARS:] if truncated else text,
    }


def command_run(args: argparse.Namespace) -> int:
    registry = load_registry(args.registry.resolve())
    errors = lint_registry(args.registry.resolve(), registry)
    if errors:
        raise RegistryError("registry lint failed before run: " + "; ".join(errors))
    evidence_dir = args.evidence_dir.resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    any_incomplete = False
    for case in selected_cases(registry, args):
        reasons = readiness_reasons(registry, case)
        for subcase in expand_case(registry, case):
            if args.platform and subcase["parameters"].get("platform") not in (None, args.platform):
                continue
            record = dict(subcase)
            record["started_at_epoch"] = time.time()
            if reasons:
                record.update(
                    execution_status="not_scheduled",
                    verdict=None,
                    blocking_reasons=reasons,
                )
                any_incomplete = True
            else:
                try:
                    handler = _load_handler(case["evaluator"]["handler"])
                    outcome = handler(
                        repo=REPO,
                        registry=registry,
                        case=case,
                        subcase=subcase,
                        evidence_dir=evidence_dir,
                    )
                    if not isinstance(outcome, dict):
                        raise TypeError("evaluator must return a record object")
                    record.update(outcome)
                except Exception as exc:  # evaluator failures are not Agent failures
                    record.update(
                        execution_status="harness_error",
                        verdict=None,
                        failure_domain="evaluator",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    any_incomplete = True
            record["finished_at_epoch"] = time.time()
            results.append(record)
    status_counts: dict[str, int] = {}
    verdict_counts: dict[str, int] = {}
    for item in results:
        status = str(item.get("execution_status"))
        status_counts[status] = status_counts.get(status, 0) + 1
        verdict = item.get("verdict")
        if verdict is not None:
            verdict_counts[str(verdict)] = verdict_counts.get(str(verdict), 0) + 1
    report = {
        "registry_id": registry["registry_id"],
        "protocol": registry["protocol"],
        "repository_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True,
            capture_output=True, check=False,
        ).stdout.strip(),
        "platform_filter": args.platform,
        "formal": True,
        "status_counts": status_counts,
        "verdict_counts": verdict_counts,
        "results": results,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"formal run records={len(results)} output={args.out}")
    print(f"execution_status counts: {json.dumps(status_counts, sort_keys=True)}")
    print(f"verdict counts: {json.dumps(verdict_counts, sort_keys=True)}")
    if any_incomplete:
        print("formal run: INCOMPLETE (fail-closed; see execution_status and blocking_reasons)")
        return 2
    return 0 if all(item.get("verdict") == "pass" for item in results) else 1


def command_gates(args: argparse.Namespace) -> int:
    """Execute every executable gate condition fail-closed; report deferred ones.

    `suite` conditions shared across gates run once (deduplicated); `formal` and
    `operator` conditions are never silently skipped — they are reported as
    deferred with counts. Executable readiness is a prerequisite for, not a
    substitute for, the frozen formal campaign.
    """
    registry_path = args.registry.resolve()
    registry = load_registry(registry_path)
    errors = lint_registry(registry_path, registry)
    if errors:
        raise RegistryError("registry lint failed before gates: " + "; ".join(errors))
    gate_conditions: dict[str, Any] = registry["gate_conditions"]
    selected = list(gate_conditions)
    if args.gate:
        unknown = set(args.gate) - set(selected)
        if unknown:
            raise RegistryError(f"unknown gate(s): {sorted(unknown)}")
        selected = [gate for gate in selected if gate in set(args.gate)]
    suites = {suite["id"]: suite for suite in registry.get("diagnostic_suites", [])}
    needs_python = any(
        condition.get("kind") == "suite"
        or (
            condition.get("kind") == "command"
            and any("{python}" in str(part) for part in condition.get("command", []))
        )
        for gate in selected
        for condition in gate_conditions[gate]
    )
    python = "{python}"
    if not args.dry_run and needs_python:
        python = resolve_validation_python(args.python)
    replacements = {"python": python, "repo": str(REPO)}
    context = {"repo": REPO, "registry_path": registry_path, "registry": registry}
    env = os.environ.copy()
    env.pop("PYTHONHOME", None)
    suite_results: dict[str, dict[str, Any]] = {}

    def execute(command: list[str], timeout_s: int) -> dict[str, Any]:
        started = time.time()
        try:
            process = subprocess.run(
                command, cwd=REPO, text=True, capture_output=True,
                env=env, check=False, timeout=timeout_s,
            )
            return {
                "execution_status": "completed",
                "returncode": process.returncode,
                "ok": process.returncode == 0,
                "elapsed_s": round(time.time() - started, 3),
                "command": command,
                "stdout": _tail(process.stdout),
                "stderr": _tail(process.stderr),
            }
        except subprocess.TimeoutExpired as exc:
            return {
                "execution_status": "timeout",
                "returncode": None,
                "ok": False,
                "elapsed_s": round(time.time() - started, 3),
                "command": command,
                "stdout": _tail(exc.stdout if isinstance(exc.stdout, str) else ""),
                "stderr": _tail(exc.stderr if isinstance(exc.stderr, str) else ""),
            }
        except OSError as exc:
            # An unresolvable/unexecutable command (missing tool binary, bad
            # interpreter, permission denied) is a HARNESS failure, not a
            # system-under-test failure — but it must still fail CLOSED and, above
            # all, must NOT abort the sweep. Letting FileNotFoundError propagate
            # killed command_gates before it wrote the evidence report, so a single
            # absent tool left every downstream condition unexecuted AND left a
            # stale gate-conditions.json on disk claiming the previous verdict
            # (breaking GC-OPS-03 evidence completeness). `harness_error` is already
            # in formal_execution_policy.execution_statuses for exactly this case;
            # the builtin path has always used it, the command path never did.
            return {
                "execution_status": "harness_error",
                "returncode": None,
                "ok": False,
                "elapsed_s": round(time.time() - started, 3),
                "command": command,
                "error": f"{type(exc).__name__}: {exc}",
                "stdout": _tail(""),
                "stderr": _tail(str(exc)),
            }

    records: list[dict[str, Any]] = []
    gate_summaries: dict[str, dict[str, Any]] = {}
    any_failed = False
    for gate in selected:
        executable_total = executable_passed = 0
        deferred_formal_conditions = deferred_formal_cases = deferred_operator = 0
        gate_failed = False
        for condition in gate_conditions[gate]:
            kind = condition["kind"]
            record: dict[str, Any] = {
                "id": condition["id"],
                "gate": gate,
                "kind": kind,
                "title": condition.get("title"),
                "requirements": condition.get("requirements", []),
            }
            if kind in EXECUTABLE_KINDS:
                executable_total += 1
                if args.dry_run:
                    record.update(execution_status="not_scheduled", ok=None)
                    if kind == "suite":
                        record["suite"] = condition["suite"]
                    elif kind == "builtin":
                        record["builtin"] = condition["builtin"]
                    else:
                        record["command"] = [
                            str(part).format(**replacements) for part in condition["command"]
                        ]
                    records.append(record)
                    continue
                if kind == "suite":
                    suite_id = condition["suite"]
                    if suite_id not in suite_results:
                        suite = suites[suite_id]
                        command = [
                            str(part).format(**replacements) for part in suite["command"]
                        ]
                        print(f"[suite] {suite_id}: {' '.join(command)}", flush=True)
                        outcome = execute(
                            command, int(suite.get("timeout_s", DEFAULT_SUITE_TIMEOUT_S))
                        )
                        suite_results[suite_id] = outcome
                        state = "PASS" if outcome["ok"] else "FAIL"
                        print(
                            f"[suite] {suite_id}: {state} ({outcome['elapsed_s']}s)",
                            flush=True,
                        )
                    outcome = suite_results[suite_id]
                    record.update(
                        suite=suite_id,
                        execution_status=outcome["execution_status"],
                        returncode=outcome["returncode"],
                        ok=outcome["ok"],
                        elapsed_s=outcome["elapsed_s"],
                    )
                elif kind == "command":
                    command = [
                        str(part).format(**replacements) for part in condition["command"]
                    ]
                    print(f"[cmd  ] {condition['id']}: {' '.join(command)}", flush=True)
                    record.update(
                        execute(
                            command,
                            int(condition.get("timeout_s", DEFAULT_CONDITION_TIMEOUT_S)),
                        )
                    )
                else:  # builtin
                    started = time.time()
                    record["builtin"] = condition["builtin"]
                    try:
                        ok, detail = BUILTIN_CHECKS[condition["builtin"]](context)
                        record.update(
                            execution_status="completed",
                            ok=ok,
                            detail=_tail(detail),
                            elapsed_s=round(time.time() - started, 3),
                        )
                    except Exception as exc:  # probe failures are not Agent failures
                        record.update(
                            execution_status="harness_error",
                            ok=False,
                            error=f"{type(exc).__name__}: {exc}",
                            elapsed_s=round(time.time() - started, 3),
                        )
                if record.get("ok"):
                    executable_passed += 1
                else:
                    any_failed = True
                    gate_failed = True
                print(f"{condition['id']:<12} {'PASS' if record.get('ok') else 'FAIL'}", flush=True)
            elif kind == "formal":
                deferred_formal_conditions += 1
                deferred_formal_cases += len(condition.get("cases", []))
                record.update(
                    execution_status="deferred_formal",
                    ok=None,
                    cases=condition.get("cases", []),
                )
            else:  # operator
                deferred_operator += 1
                record.update(
                    execution_status="deferred_operator",
                    ok=None,
                    procedure=condition.get("procedure"),
                )
            records.append(record)
        if args.dry_run:
            verdict = "dry_run"
        elif gate_failed:
            verdict = "fail"
        elif executable_total:
            verdict = "executable_pass"
        else:
            verdict = "deferred_only"
        gate_summaries[gate] = {
            "executable_total": executable_total,
            "executable_passed": executable_passed,
            "deferred_formal_conditions": deferred_formal_conditions,
            "deferred_formal_cases": deferred_formal_cases,
            "deferred_operator_conditions": deferred_operator,
            "verdict": verdict,
        }
    report = {
        "registry_id": registry["registry_id"],
        "protocol": registry["protocol"],
        "repository_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True,
            capture_output=True, check=False,
        ).stdout.strip(),
        "formal": False,
        "dry_run": bool(args.dry_run),
        "gate_summaries": gate_summaries,
        "suite_results": suite_results,
        "conditions": records,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    for gate in selected:
        summary = gate_summaries[gate]
        print(
            f"{gate:<16} executable {summary['executable_passed']}/{summary['executable_total']}"
            f"  deferred_formal={summary['deferred_formal_conditions']}"
            f"({summary['deferred_formal_cases']} cases)"
            f" operator={summary['deferred_operator_conditions']}"
            f" -> {summary['verdict']}"
        )
    print(f"gate-conditions report: {args.out}")
    if args.dry_run:
        print("gate conditions: DRY RUN (nothing executed)")
        return 0
    if any_failed:
        print("gate conditions: FAIL (fail-closed; fix the implementation, not the probe)")
        return 1
    print(
        "gate conditions: EXECUTABLE PASS — deferred formal/operator conditions remain; "
        "certification still requires the frozen formal campaign"
    )
    return 0


def command_diagnostics(args: argparse.Namespace) -> int:
    registry = load_registry(args.registry.resolve())
    suites = registry.get("diagnostic_suites", [])
    if args.suite:
        wanted = set(args.suite)
        suites = [suite for suite in suites if suite["id"] in wanted]
        missing = wanted - {suite["id"] for suite in suites}
        if missing:
            raise RegistryError(f"unknown diagnostic suite(s): {sorted(missing)}")
    python = resolve_validation_python(args.python)
    evidence_dir = args.evidence_dir.resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    failed = False
    for suite in suites:
        replacements = {
            "python": python,
            "repo": str(REPO),
            "evidence_dir": str(evidence_dir),
        }
        command = [part.format(**replacements) for part in suite["command"]]
        timeout_s = int(suite.get("timeout_s", DEFAULT_SUITE_TIMEOUT_S))
        print(f"{suite['id']}: {' '.join(command)}")
        if args.dry_run:
            records.append({"id": suite["id"], "execution_status": "not_scheduled", "command": command})
            continue
        env = os.environ.copy()
        env.pop("PYTHONHOME", None)
        started = time.time()
        try:
            process = subprocess.run(
                command, cwd=REPO, text=True, capture_output=True,
                env=env, check=False, timeout=timeout_s,
            )
            execution_status = "completed"
            returncode: int | None = process.returncode
            stdout, stderr = process.stdout, process.stderr
        except subprocess.TimeoutExpired as exc:
            execution_status = "timeout"
            returncode = None
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        except OSError as exc:
            # Same fail-closed contract as command_gates.execute: an unresolvable
            # suite interpreter/binary is a harness failure that must be RECORDED,
            # never allowed to abort the remaining suites or skip the report.
            execution_status = "harness_error"
            returncode = None
            stdout = ""
            stderr = f"{type(exc).__name__}: {exc}"
        record = {
            "id": suite["id"],
            "official_val_verdict": False,
            "execution_status": execution_status,
            "returncode": returncode,
            "timeout_s": timeout_s,
            "elapsed_s": round(time.time() - started, 3),
            "command": command,
            "stdout": _tail(stdout),
            "stderr": _tail(stderr),
        }
        records.append(record)
        state = (
            "PASS" if returncode == 0
            else {"timeout": "TIMEOUT", "harness_error": "HARNESS-ERROR"}.get(
                execution_status, "FAIL")
        )
        print(f"{suite['id']}: {state} ({record['elapsed_s']}s)")
        failed = failed or returncode != 0
    report = {
        "registry_id": registry["registry_id"],
        "protocol_version": registry["protocol"]["version"],
        "validation_python": python,
        "formal": False,
        "records": records,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"diagnostic report: {args.out}")
    return 1 if failed else 0


def add_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--case", action="append", help="select a VAL-* case; repeatable")
    parser.add_argument("--gate", action="append", help="select a gate; repeatable")
    parser.add_argument("--phase", action="append", help="select a phase; repeatable")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    subparsers = parser.add_subparsers(dest="command", required=True)

    lint = subparsers.add_parser("lint", help="validate registry and protocol traceability")
    lint.set_defaults(func=command_lint)

    listing = subparsers.add_parser("list", help="list formal cases and readiness")
    add_filters(listing)
    listing.set_defaults(func=command_list)

    plan = subparsers.add_parser("plan", help="expand case matrices without executing evaluators")
    add_filters(plan)
    plan.add_argument("--platform", choices=TARGET_PLATFORMS)
    plan.add_argument("--details", action="store_true", help="include every expanded subcase")
    plan.add_argument("--json", action="store_true")
    plan.set_defaults(func=command_plan)

    gates_cmd = subparsers.add_parser(
        "gates", help="execute executable gate conditions fail-closed; report deferred ones"
    )
    gates_cmd.add_argument("--gate", action="append", help="select a gate; repeatable")
    gates_cmd.add_argument(
        "--python", help="Python with pytest, torch, torch_geometric, and pandas"
    )
    gates_cmd.add_argument("--dry-run", action="store_true")
    gates_cmd.add_argument("--out", type=Path, default=REPORTS_DIR / "gate-conditions.json")
    gates_cmd.set_defaults(func=command_gates)

    run = subparsers.add_parser("run", help="run ready formal evaluators; fail closed otherwise")
    add_filters(run)
    run.add_argument("--platform", choices=TARGET_PLATFORMS)
    run.add_argument("--evidence-dir", type=Path, default=REPORTS_DIR / "validation-evidence")
    run.add_argument("--out", type=Path, default=REPORTS_DIR / "validation-report.json")
    run.set_defaults(func=command_run)

    diagnostics = subparsers.add_parser(
        "diagnostics", help="run non-scoring component/probe suites"
    )
    diagnostics.add_argument("--suite", action="append", help="select DIAG-* suite; repeatable")
    diagnostics.add_argument(
        "--python", help="Python with pytest, torch, torch_geometric, and pandas"
    )
    diagnostics.add_argument("--dry-run", action="store_true")
    diagnostics.add_argument(
        "--evidence-dir", type=Path,
        default=REPORTS_DIR / "evidence" / "registry-diagnostics",
    )
    diagnostics.add_argument(
        "--out", type=Path,
        default=REPORTS_DIR / "registry-diagnostics.json",
    )
    diagnostics.set_defaults(func=command_diagnostics)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except RegistryError as exc:
        print(f"registry error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
