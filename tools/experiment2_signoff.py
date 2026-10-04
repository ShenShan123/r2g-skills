#!/usr/bin/env python3
"""Shared preparation and strict-evaluation primitives for Experiment 2.

This module is evaluator infrastructure, not an Agent skill.  It deliberately
returns measurements and hard-gate verdicts, never repair advice.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any


REPO = Path(__file__).resolve().parents[1]
EXPERIMENT_DIR = REPO / "docs" / "experiments" / "signoff"
DEFAULT_COHORT = EXPERIMENT_DIR / "experiment2_pilot_cohort.json"
DEFAULT_TASK_SPEC = EXPERIMENT_DIR / "experiment2_task_spec.json"
METHOD_IDS = ("openai-vanilla", "qwen-vanilla", "deepseek-vanilla", "full-r2g")
TARGET_FREQUENCY_MHZ = 100.0
TARGET_PERIOD_NS = 10.0
HEX40 = re.compile(r"^[0-9a-f]{40}$")
FORBIDDEN_SDC = re.compile(
    r"(?im)^\s*(set_false_path|set_multicycle_path|set_disable_timing|"
    r"remove_clock|reset_path|set_case_analysis)\b"
)
PERIOD_RE = re.compile(r"(?m)^\s*set\s+clk_period\s+([0-9.eE+-]+)\s*$")
CLOCK_PORT_RE = re.compile(r"(?m)^\s*set\s+clk_port_name\s+([^\s;]+)\s*$")
ASSIGN_RE = re.compile(r"(?m)^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*(?:\?|:)?=\s*(.*?)\s*$")


class Experiment2Error(RuntimeError):
    pass


def require_fixed_target_frequency(value: float) -> float:
    if not math.isfinite(value) or abs(value - TARGET_FREQUENCY_MHZ) > 1e-6:
        raise Experiment2Error(
            f"Experiment 2 target is frozen at {TARGET_FREQUENCY_MHZ:g} MHz"
        )
    return TARGET_FREQUENCY_MHZ


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


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


def verify_campaign_bindings(campaign_root: Path) -> dict[str, Any]:
    manifest = read_json(campaign_root.resolve() / "experiment2_campaign.json")
    if not isinstance(manifest, dict):
        raise Experiment2Error(f"campaign is not prepared: {campaign_root}")
    records = {
        "cohort": manifest.get("cohort"),
        "task spec": manifest.get("task_spec"),
        "model routes": manifest.get("model_routes"),
        "knowledge seed": (manifest.get("knowledge_policy") or {}).get("full_r2g_seed"),
        "heuristics seed": (manifest.get("knowledge_policy") or {}).get("full_r2g_heuristics_seed"),
        **(manifest.get("implementations") or {}),
    }
    for label, record in records.items():
        if not isinstance(record, dict) or not record.get("path") or not record.get("sha256"):
            raise Experiment2Error(f"campaign binding is missing: {label}")
        path = Path(record["path"])
        if not path.is_file() or sha256_file(path) != record["sha256"]:
            raise Experiment2Error(f"campaign binding changed or disappeared: {label} -> {path}")
    return manifest


def sha256_tree(root: Path, *, excluded: set[str] | None = None) -> str:
    excluded = excluded or set()
    rows: list[tuple[str, str]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        rel = path.relative_to(root).as_posix()
        if rel not in excluded:
            rows.append((rel, sha256_file(path)))
    payload = json.dumps(rows, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    log: Path | None = None,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    if log is None:
        return subprocess.run(
            command, cwd=cwd, env=env, text=True, capture_output=True,
            check=False, timeout=timeout,
        )
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as stream:
        stream.write("COMMAND: " + json.dumps(command) + "\n")
        stream.flush()
        return subprocess.run(
            command, cwd=cwd, env=env, text=True, stdout=stream,
            stderr=subprocess.STDOUT, check=False, timeout=timeout,
        )


def git_text(*args: str, cwd: Path) -> str:
    result = run(["git", *args], cwd=cwd)
    if result.returncode:
        raise Experiment2Error((result.stderr or result.stdout or "git failed").strip())
    return result.stdout.strip()


def load_cohort(path: Path = DEFAULT_COHORT) -> dict[str, Any]:
    cohort = read_json(path)
    if not isinstance(cohort, dict):
        raise Experiment2Error(f"cannot read cohort: {path}")
    errors = lint_cohort(cohort, base_dir=path.resolve().parent)
    if errors:
        raise Experiment2Error("invalid cohort:\n- " + "\n- ".join(errors))
    return cohort


def fixture_by_id(cohort: dict[str, Any], fixture_id: str) -> dict[str, Any]:
    for fixture in cohort["fixtures"]:
        if fixture["id"] == fixture_id:
            return fixture
    raise Experiment2Error(f"unknown fixture: {fixture_id}")


def public_fixture_id(fixture: dict[str, Any]) -> str:
    """Return the blinded identifier exposed to an evaluated method."""
    return str(fixture.get("public_id") or fixture["id"])


def _resolve_bound_path(value: str, base_dir: Path | None) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    candidates = [REPO / path]
    if base_dir is not None:
        candidates.append(base_dir / path)
    return next((item.resolve() for item in candidates if item.is_file()), candidates[0].resolve())


def _lint_admission_evidence(
    fixture: dict[str, Any],
    *,
    base_dir: Path | None,
) -> list[str]:
    errors: list[str] = []
    label = str(fixture.get("id") or "<unknown>")
    role = fixture.get("role")
    evidence = fixture.get("admission_evidence") or {}
    if fixture.get("strict_clean_scope") != "fixed_target_physical_signoff":
        errors.append(f"{label}: strict_clean_scope must be fixed_target_physical_signoff")
    if role not in {"repair_needed", "clean_sentinel"}:
        errors.append(f"{label}: role must be repair_needed or clean_sentinel")
        return errors
    if role == "repair_needed" and not fixture.get("family_id"):
        errors.append(f"{label}: repair-needed fixture lacks family_id")
    family_evidence = evidence.get("family_evidence")
    if role == "repair_needed" and family_evidence:
        path = _resolve_bound_path(str(family_evidence.get("path") or ""), base_dir)
        document = read_json(path)
        if not path.is_file() or sha256_file(path) != family_evidence.get("sha256"):
            errors.append(f"{label}: family evidence is missing or changed")
        elif document.get("family_id") != fixture.get("family_id") or (document.get("admission") or {}).get("status") != "pass":
            errors.append(f"{label}: family evidence does not bind an admitted matching family")
    records = evidence.get("baseline_runs") or []
    expected = 2 if role == "repair_needed" else 1
    if len(records) != expected:
        errors.append(f"{label}: {role} requires exactly {expected} bound baseline run(s)")
    for index, record in enumerate(records, 1):
        path_value = record.get("path") if isinstance(record, dict) else None
        digest = record.get("sha256") if isinstance(record, dict) else None
        if not path_value or not re.fullmatch(r"[0-9a-f]{64}", str(digest or "")):
            errors.append(f"{label}: baseline run {index} lacks path/digest binding")
            continue
        path = _resolve_bound_path(str(path_value), base_dir)
        document = read_json(path)
        if not path.is_file() or sha256_file(path) != digest:
            errors.append(f"{label}: baseline run {index} evidence is missing or changed")
        elif role == "repair_needed" and document.get("strict_clean") is not False:
            errors.append(f"{label}: repair baseline run {index} is not a strict-clean failure")
        elif role == "clean_sentinel" and document.get("strict_clean") is not True:
            errors.append(f"{label}: sentinel baseline is not strict-clean")
    witness = evidence.get("witness")
    if role == "repair_needed":
        if not isinstance(witness, dict) or not witness.get("path") or not re.fullmatch(
            r"[0-9a-f]{64}", str(witness.get("sha256") or "")
        ):
            errors.append(f"{label}: repair-needed fixture lacks a bound clean witness")
        else:
            path = _resolve_bound_path(str(witness["path"]), base_dir)
            document = read_json(path)
            if not path.is_file() or sha256_file(path) != witness["sha256"]:
                errors.append(f"{label}: clean witness evidence is missing or changed")
            elif document.get("strict_clean") is not True:
                errors.append(f"{label}: witness is not strict-clean")
    return errors


def lint_cohort(cohort: dict[str, Any], *, base_dir: Path | None = None) -> list[str]:
    errors: list[str] = []
    fixtures = cohort.get("fixtures") or []
    ids = [item.get("id") for item in fixtures]
    cohort_kind = cohort.get("cohort_kind")
    retrospective_screen = cohort_kind == "retrospective_repair_screen"
    benchmark_screen = cohort_kind == "benchmark_challenge_screen"
    family_smoke = cohort_kind == "family_contract_smoke"
    family_balanced = cohort_kind == "family_balanced_pilot"
    expected_count = 4 if family_smoke else (6 if retrospective_screen else 8)
    if len(fixtures) != expected_count:
        errors.append(f"cohort requires {expected_count} fixtures, found {len(fixtures)}")
    if len(ids) != len(set(ids)):
        errors.append("fixture IDs are not unique")
    public_ids = [item.get("public_id") for item in fixtures]
    if any(public_ids):
        if not all(isinstance(item, str) and re.fullmatch(r"task_[0-9]{3}", item) for item in public_ids):
            errors.append("blinded public IDs must all use task_NNN")
        elif len(public_ids) != len(set(public_ids)):
            errors.append("blinded public IDs are not unique")
    if not retrospective_screen and not benchmark_screen and not family_smoke and not family_balanced:
        expected_bins = {"small": 2, "medium": 3, "large": 3}
        actual_bins = {name: sum(item.get("size_bin") == name for item in fixtures) for name in expected_bins}
        if actual_bins != expected_bins:
            errors.append(f"size bins are {actual_bins}, expected {expected_bins}")
    repo_urls = [item.get("candidate", {}).get("repo_url") for item in fixtures]
    if not benchmark_screen and len(repo_urls) != len(set(repo_urls)):
        errors.append("Pilot fixtures must come from distinct repositories")
    if family_smoke:
        roles = {name: sum(item.get("role") == name for item in fixtures) for name in ("repair_needed", "clean_sentinel")}
        if roles != {"repair_needed": 2, "clean_sentinel": 2}:
            errors.append(f"family contract smoke roles are {roles}, expected two repair and two sentinel")
    if family_balanced:
        roles = {name: sum(item.get("role") == name for item in fixtures) for name in ("repair_needed", "clean_sentinel")}
        families = {
            name: sum(item.get("family_id") == name for item in fixtures)
            for name in ("footprint_congestion", "pin_perimeter", "rule_specific_drc")
        }
        if roles != {"repair_needed": 6, "clean_sentinel": 2}:
            errors.append(f"family-balanced roles are {roles}, expected six repair and two sentinel")
        if families != {"footprint_congestion": 2, "pin_perimeter": 2, "rule_specific_drc": 2}:
            errors.append(f"family-balanced counts are {families}, expected two per registered family")
    for fixture in fixtures:
        candidate = fixture.get("candidate") or {}
        if not HEX40.fullmatch(str(candidate.get("commit") or "")):
            errors.append(f"{fixture.get('id')}: commit is not a pinned SHA-1")
        cells = fixture.get("mapped_cells")
        size_bin = fixture.get("size_bin")
        if not isinstance(cells, int) or cells < 100:
            errors.append(f"{fixture.get('id')}: invalid mapped-cell count")
        elif size_bin == "small" and not 100 <= cells <= 999:
            errors.append(f"{fixture.get('id')}: outside small bin")
        elif size_bin == "medium" and not 1000 <= cells <= 9999:
            errors.append(f"{fixture.get('id')}: outside medium bin")
        elif size_bin == "large" and cells < 10000:
            errors.append(f"{fixture.get('id')}: outside large bin")
        if not fixture.get("clock_port"):
            errors.append(f"{fixture.get('id')}: missing clock port")
        footprint = fixture.get("footprint_policy") or {"mode": "fixed_area"}
        if footprint.get("mode") in {"bounded_core_utilization", "bounded_explicit_area"}:
            values = [footprint.get(name) for name in ("initial", "minimum", "maximum")]
            if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in values):
                errors.append(f"{fixture.get('id')}: invalid bounded utilization policy")
            elif not 1 <= values[1] <= values[0] <= values[2] <= 100:
                errors.append(f"{fixture.get('id')}: utilization bounds do not contain initial value")
            if footprint.get("mode") == "bounded_explicit_area":
                area = (fixture.get("action_policy") or {}).get("explicit_area") or {}
                bounds = [area.get(name) for name in ("minimum_die_perimeter_um", "maximum_die_perimeter_um", "core_margin_um")]
                if not all(isinstance(value, (int, float)) and math.isfinite(value) and value > 0 for value in bounds):
                    errors.append(f"{fixture.get('id')}: invalid explicit-area action bounds")
                elif bounds[0] > bounds[1]:
                    errors.append(f"{fixture.get('id')}: explicit-area perimeter bounds are reversed")
        elif footprint.get("mode") == "fixed_area":
            for area_name in ("die_area", "core_area"):
                area = fixture.get(area_name)
                if not isinstance(area, list) or len(area) != 4 or not all(isinstance(v, (int, float)) for v in area):
                    errors.append(f"{fixture.get('id')}: invalid {area_name}")
        else:
            errors.append(f"{fixture.get('id')}: unknown footprint policy")
        closure = (fixture.get("qualification") or {}).get("closure_sha256")
        if not re.fullmatch(r"[0-9a-f]{64}", str(closure or "")):
            errors.append(f"{fixture.get('id')}: invalid qualification closure digest")
        if family_smoke or family_balanced:
            errors.extend(_lint_admission_evidence(fixture, base_dir=base_dir))
    repeats = set(cohort.get("repeatability_fixture_ids") or [])
    expected_repeats = expected_count if retrospective_screen or benchmark_screen else 2
    if not repeats <= set(ids) or len(repeats) != expected_repeats:
        errors.append(f"exactly {expected_repeats} valid repeatability fixtures are required")
    return errors


def project_paths(campaign_root: Path, method_id: str, fixture_id: str) -> dict[str, Path]:
    root = campaign_root.resolve()
    return {
        "campaign": root,
        "project": root / "methods" / method_id / fixture_id / "project",
        "method": root / "methods" / method_id / fixture_id,
        "source": root / "sources" / fixture_id,
        "logs": root / "methods" / method_id / fixture_id / "logs",
        "attempts": root / "methods" / method_id / fixture_id / "attempts.jsonl",
        "validations": root / "methods" / method_id / fixture_id / "validations",
        "checkpoints": root / "checkpoints" / method_id / fixture_id,
    }


def _format_area(area: list[float]) -> str:
    return " ".join(f"{float(value):g}" for value in area)


def render_sdc(fixture: dict[str, Any], period_ns: float) -> str:
    if not math.isfinite(period_ns) or period_ns <= 0:
        raise Experiment2Error("clock period must be finite and positive")
    return f"""current_design {fixture['candidate']['top_module']}

set clk_name core_clock
set clk_port_name {fixture['clock_port']}
set clk_period {period_ns:.9g}
set clk_io_pct 0.2

set clk_port [get_ports $clk_port_name]
create_clock -name $clk_name -period $clk_period $clk_port
set_clock_uncertainty 0.1 [get_clocks $clk_name]
set non_clock_inputs [all_inputs -no_clocks]
set_input_delay [expr $clk_period * $clk_io_pct] -clock $clk_name $non_clock_inputs
set_output_delay [expr $clk_period * $clk_io_pct] -clock $clk_name [all_outputs]
"""


def render_config(project: Path, fixture: dict[str, Any]) -> str:
    candidate = fixture["candidate"]
    rtl = [project / "rtl" / value for value in candidate["rtl_files"]]
    includes = [project / "rtl" / value for value in candidate.get("include_dirs", [])]
    lines = [
        f"export DESIGN_NAME = {candidate['top_module']}",
        "export PLATFORM = sky130hd",
        "export VERILOG_FILES = " + " ".join(str(path) for path in rtl),
        f"export SDC_FILE = {project / 'constraints' / 'constraint.sdc'}",
        "export PLACE_DENSITY_LB_ADDON = 0.20",
        "export ABC_AREA = 1",
    ]
    footprint = fixture.get("footprint_policy") or {"mode": "fixed_area"}
    if footprint.get("mode") in {"bounded_core_utilization", "bounded_explicit_area"}:
        lines.append(f"export CORE_UTILIZATION = {float(footprint['initial']):g}")
    else:
        lines.extend(
            [
                f"export DIE_AREA = {_format_area(fixture['die_area'])}",
                f"export CORE_AREA = {_format_area(fixture['core_area'])}",
            ]
        )
    if includes:
        lines.append("export VERILOG_INCLUDE_DIRS = " + " ".join(str(path) for path in includes))
    if candidate.get("defines"):
        lines.append("export VERILOG_DEFINES = " + " ".join(candidate["defines"]))
    overrides = fixture.get("initial_config_edits") or {}
    for name, value in sorted(overrides.items()):
        pattern = re.compile(rf"^export\s+{re.escape(str(name))}\s*=")
        line = f"export {name} = {value}"
        for index, existing in enumerate(lines):
            if pattern.match(existing):
                lines[index] = line
                break
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


def materialize_project(source: Path, project: Path, fixture: dict[str, Any]) -> None:
    if project.exists():
        raise Experiment2Error(f"project already exists: {project}")
    for name in ("rtl", "constraints", "backend", "reports", "drc", "lvs", "rcx"):
        (project / name).mkdir(parents=True, exist_ok=True)
    candidate = fixture["candidate"]
    closure_paths = [
        *candidate["rtl_files"], *candidate.get("header_files", []),
        *candidate.get("readmem_files", []),
    ]
    digests = []
    for relative in closure_paths:
        src = source / relative
        dst = project / "rtl" / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        digests.append({"path": relative, "sha256": sha256_file(dst), "size": dst.stat().st_size})
    (project / "constraints" / "config.mk").write_text(render_config(project, fixture), encoding="utf-8")
    (project / "constraints" / "constraint.sdc").write_text(
        render_sdc(fixture, TARGET_PERIOD_NS), encoding="utf-8"
    )
    source_digest = hashlib.sha256(
        json.dumps(digests, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    input_manifest = {
        "schema_version": "1.0",
        "fixture_id": public_fixture_id(fixture),
        "repo_url": candidate["repo_url"],
        "commit": candidate["commit"],
        "top_module": candidate["top_module"],
        "clock_port": fixture["clock_port"],
        "footprint_policy": fixture.get("footprint_policy") or {
            "mode": "fixed_area",
            "die_area": fixture["die_area"],
            "core_area": fixture["core_area"],
        },
        "files": digests,
        "source_digest": source_digest,
        "qualification_closure_sha256": fixture["qualification"]["closure_sha256"],
    }
    write_json_atomic(project / "experiment2_input_manifest.json", input_manifest)
    write_json_atomic(
        project / "metadata.json",
        {
            "design_name": candidate["top_module"],
            "platform": "sky130hd",
            "source_kind": "frozen_experiment_fixture",
            "source_commit": candidate["commit"],
            "source_digest": source_digest,
            "source_bytes_verified": True,
            "compile_inputs_verified": True,
            "collateral_verified": True,
            "rtl_readiness": "ready",
            "compile_manifest_digest": sha256_file(project / "experiment2_input_manifest.json"),
            "experiment2_development_seen": True,
            "experiment2_strict_clean_scope": "fixed_target_physical_signoff",
        },
    )


def set_target_frequency(project: Path, fixture: dict[str, Any], frequency_mhz: float) -> dict[str, float]:
    frequency_mhz = require_fixed_target_frequency(frequency_mhz)
    period_ns = TARGET_PERIOD_NS
    (project / "constraints" / "constraint.sdc").write_text(
        render_sdc(fixture, period_ns), encoding="utf-8"
    )
    return {"frequency_mhz": frequency_mhz, "period_ns": period_ns}


def set_bounded_core_utilization(
    project: Path,
    fixture: dict[str, Any],
    value: float,
) -> dict[str, float]:
    """Apply the cohort-registered footprint action without changing task identity."""
    footprint = fixture.get("footprint_policy") or {"mode": "fixed_area"}
    action_policy = fixture.get("action_policy") or {}
    allowed_numeric = action_policy.get("allowed_numeric_knobs")
    if allowed_numeric is not None and "CORE_UTILIZATION" not in allowed_numeric:
        raise Experiment2Error("CORE_UTILIZATION is outside the fixture action policy")
    if footprint.get("mode") != "bounded_core_utilization":
        raise Experiment2Error("CORE_UTILIZATION is frozen for this fixture")
    try:
        utilization = float(value)
        minimum = float(footprint["minimum"])
        maximum = float(footprint["maximum"])
    except (KeyError, TypeError, ValueError) as exc:
        raise Experiment2Error("invalid bounded CORE_UTILIZATION policy") from exc
    if not all(math.isfinite(item) for item in (utilization, minimum, maximum)):
        raise Experiment2Error("CORE_UTILIZATION and its bounds must be finite")
    if not minimum <= utilization <= maximum:
        raise Experiment2Error(
            f"CORE_UTILIZATION must remain within [{minimum:g}, {maximum:g}]"
        )
    config_path = project / "constraints" / "config.mk"
    try:
        config = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise Experiment2Error(f"cannot read project config: {config_path}") from exc
    pattern = re.compile(r"(?m)^\s*(?:export\s+)?CORE_UTILIZATION\s*(?:\?|:)?=.*$")
    line = f"export CORE_UTILIZATION = {utilization:g}"
    updated = pattern.sub(line, config) if pattern.search(config) else config.rstrip() + "\n" + line + "\n"
    config_path.write_text(updated, encoding="utf-8")
    return {
        "core_utilization": utilization,
        "minimum": minimum,
        "maximum": maximum,
    }


def set_bounded_explicit_area(
    project: Path,
    fixture: dict[str, Any],
    die_width_um: float,
    die_height_um: float,
) -> dict[str, float]:
    """Switch a registered pin-capacity task to bounded explicit floorplan sizing."""
    if (fixture.get("footprint_policy") or {}).get("mode") != "bounded_explicit_area":
        raise Experiment2Error("explicit area is frozen for this fixture")
    policy = (fixture.get("action_policy") or {}).get("explicit_area") or {}
    values = [float(die_width_um), float(die_height_um)]
    if not all(math.isfinite(value) and value > 0 for value in values):
        raise Experiment2Error("die dimensions must be finite and positive")
    perimeter = 2 * sum(values)
    minimum = float(policy["minimum_die_perimeter_um"])
    maximum = float(policy["maximum_die_perimeter_um"])
    margin = float(policy["core_margin_um"])
    if not minimum <= perimeter <= maximum:
        raise Experiment2Error(f"die perimeter must remain within [{minimum:g}, {maximum:g}] um")
    if min(values) <= 2 * margin:
        raise Experiment2Error("die dimensions do not leave the registered core margin")
    config_path = project / "constraints" / "config.mk"
    config = config_path.read_text(encoding="utf-8")
    config = re.sub(r"(?m)^\s*(?:export\s+)?CORE_UTILIZATION\s*(?:\?|:)?=.*\n?", "", config)
    updates = {
        "DIE_AREA": f"0 0 {values[0]:g} {values[1]:g}",
        "CORE_AREA": f"{margin:g} {margin:g} {values[0] - margin:g} {values[1] - margin:g}",
    }
    for name, value in updates.items():
        pattern = re.compile(rf"(?m)^\s*(?:export\s+)?{name}\s*(?:\?|:)?=.*$")
        line = f"export {name} = {value}"
        config = pattern.sub(line, config) if pattern.search(config) else config.rstrip() + "\n" + line + "\n"
    config_path.write_text(config, encoding="utf-8")
    return {"die_width_um": values[0], "die_height_um": values[1], "die_perimeter_um": perimeter, "core_margin_um": margin}


def set_bounded_string_knob(
    project: Path,
    fixture: dict[str, Any],
    name: str,
    value: str,
) -> dict[str, str]:
    allowed = ((fixture.get("action_policy") or {}).get("allowed_string_knobs") or {}).get(name) or []
    if value not in allowed:
        raise Experiment2Error("string knob or value is outside the fixture action policy")
    config_path = project / "constraints" / "config.mk"
    config = config_path.read_text(encoding="utf-8")
    pattern = re.compile(rf"(?m)^\s*(?:export\s+)?{re.escape(name)}\s*(?:\?|:)?=.*$")
    line = f"export {name} = {value}"
    config = pattern.sub(line, config) if pattern.search(config) else config.rstrip() + "\n" + line + "\n"
    config_path.write_text(config, encoding="utf-8")
    return {"name": name, "value": value}


def _parse_config(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    return {match.group(1): match.group(2).strip() for match in ASSIGN_RE.finditer(text)}


def _float_list(value: str | None) -> list[float] | None:
    try:
        result = [float(item) for item in str(value).split()]
    except (TypeError, ValueError):
        return None
    return result if all(math.isfinite(item) for item in result) else None


def protected_input_failures(project: Path, fixture: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    manifest = read_json(project / "experiment2_input_manifest.json", {}) or {}
    candidate = fixture["candidate"]
    if manifest.get("fixture_id") != public_fixture_id(fixture) or manifest.get("commit") != candidate["commit"]:
        failures.append("input manifest identity differs from frozen fixture")
    for entry in manifest.get("files") or []:
        path = project / "rtl" / str(entry.get("path") or "")
        if not path.is_file() or sha256_file(path) != entry.get("sha256"):
            failures.append(f"source byte mismatch: {entry.get('path')}")
    config = _parse_config(project / "constraints" / "config.mk")
    if config.get("DESIGN_NAME") != candidate["top_module"]:
        failures.append("DESIGN_NAME/top module changed")
    if config.get("PLATFORM") != "sky130hd":
        failures.append("platform changed")
    footprint = fixture.get("footprint_policy") or {"mode": "fixed_area"}
    if footprint.get("mode") == "bounded_core_utilization":
        try:
            utilization = float(config.get("CORE_UTILIZATION", "nan"))
        except ValueError:
            utilization = float("nan")
        minimum = float(footprint["minimum"])
        maximum = float(footprint["maximum"])
        if not math.isfinite(utilization) or not minimum <= utilization <= maximum:
            failures.append(
                f"CORE_UTILIZATION outside frozen bounds [{minimum:g}, {maximum:g}]"
            )
        if config.get("DIE_AREA") is not None or config.get("CORE_AREA") is not None:
            failures.append("explicit DIE_AREA/CORE_AREA forbidden in bounded-utilization mode")
    elif footprint.get("mode") == "bounded_explicit_area":
        utilization = _finite_number(config, "CORE_UTILIZATION")
        die = _float_list(config.get("DIE_AREA"))
        core = _float_list(config.get("CORE_AREA"))
        if utilization is not None:
            if abs(utilization - float(footprint["initial"])) > 1e-9 or die is not None or core is not None:
                failures.append("pin baseline footprint changed outside the registered action")
        else:
            policy = (fixture.get("action_policy") or {}).get("explicit_area") or {}
            if die is None or core is None or len(die) != 4 or len(core) != 4:
                failures.append("bounded explicit DIE_AREA/CORE_AREA is incomplete")
            else:
                width, height = die[2] - die[0], die[3] - die[1]
                perimeter = 2 * (width + height)
                minimum = float(policy.get("minimum_die_perimeter_um", "nan"))
                maximum = float(policy.get("maximum_die_perimeter_um", "nan"))
                margin = float(policy.get("core_margin_um", "nan"))
                if die[:2] != [0.0, 0.0] or not minimum <= perimeter <= maximum:
                    failures.append("explicit die area is outside registered perimeter bounds")
                expected_core = [margin, margin, width - margin, height - margin]
                if any(abs(a - b) > 1e-6 for a, b in zip(core, expected_core)):
                    failures.append("explicit core area does not preserve the registered margin")
    else:
        if _float_list(config.get("DIE_AREA")) != [float(v) for v in fixture["die_area"]]:
            failures.append("DIE_AREA changed")
        if _float_list(config.get("CORE_AREA")) != [float(v) for v in fixture["core_area"]]:
            failures.append("CORE_AREA changed")
    for name, allowed in ((fixture.get("action_policy") or {}).get("allowed_string_knobs") or {}).items():
        if config.get(name) is not None and config.get(name) not in allowed:
            failures.append(f"{name} is outside the registered action policy")
    sdc_path = project / "constraints" / "constraint.sdc"
    try:
        sdc = sdc_path.read_text(encoding="utf-8")
    except OSError:
        return failures + ["constraint.sdc missing"]
    if FORBIDDEN_SDC.search(sdc):
        failures.append("forbidden timing exception or check-disabling command")
    ports = CLOCK_PORT_RE.findall(sdc)
    periods = PERIOD_RE.findall(sdc)
    if ports != [fixture["clock_port"]]:
        failures.append("clock identity changed or ambiguous")
    if len(periods) != 1:
        failures.append("exactly one finite target period is required")
    else:
        try:
            period = float(periods[0])
            if not math.isfinite(period) or period <= 0:
                failures.append("target period is non-finite or non-positive")
            elif abs(period - TARGET_PERIOD_NS) > 1e-9:
                failures.append(
                    f"target period changed; Experiment 2 requires {TARGET_PERIOD_NS:g} ns"
                )
        except ValueError:
            failures.append("target period is invalid")
    if len(re.findall(r"(?m)^\s*create_clock\b", sdc)) != 1:
        failures.append("exactly one protected primary clock is required")
    return failures


def target_period(project: Path) -> float | None:
    try:
        match = PERIOD_RE.search((project / "constraints" / "constraint.sdc").read_text(encoding="utf-8"))
        value = float(match.group(1)) if match else None
    except (OSError, ValueError):
        return None
    return value if value is not None and math.isfinite(value) and value > 0 else None


def resource_intervention(project: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    """Measure task-easing footprint changes without folding them into clean/fail."""
    footprint = fixture.get("footprint_policy") or {"mode": "fixed_area"}
    config = _parse_config(project / "constraints" / "config.mk")
    mode = footprint.get("mode")
    result: dict[str, Any] = {
        "objective_track": fixture.get("objective_track") or "fixed_goal",
        "footprint_mode": mode,
    }
    if mode == "bounded_core_utilization":
        initial = float(footprint["initial"])
        current = _finite_number(config, "CORE_UTILIZATION")
        result.update({"initial_core_utilization_pct": initial, "final_core_utilization_pct": current})
        result["estimated_core_area_multiplier"] = (
            initial / current if current is not None and current > 0 else None
        )
    elif mode == "bounded_explicit_area":
        die = _float_list(config.get("DIE_AREA"))
        policy = (fixture.get("action_policy") or {}).get("explicit_area") or {}
        minimum = _finite_number(policy, "minimum_die_perimeter_um")
        perimeter = None
        area = None
        if die is not None and len(die) == 4:
            width, height = die[2] - die[0], die[3] - die[1]
            perimeter = 2 * (width + height)
            area = width * height
        result.update({
            "minimum_registered_die_perimeter_um": minimum,
            "final_die_perimeter_um": perimeter,
            "final_die_area_um2": area,
            "perimeter_over_minimum_ratio": (
                perimeter / minimum - 1
                if perimeter is not None and minimum is not None and minimum > 0
                else None
            ),
        })
    else:
        result["footprint_changed"] = bool(protected_input_failures(project, fixture))
    return result


def read_bounded_project_file(project: Path, arguments: dict[str, Any]) -> dict[str, Any]:
    relative = Path(str(arguments["path"]))
    if relative.is_absolute() or ".." in relative.parts:
        raise Experiment2Error("path must be project-relative")
    path = (project / relative).resolve()
    if project.resolve() not in path.parents:
        raise Experiment2Error("path escapes project")
    allowed = {"rtl", "constraints", "reports", "backend", "metadata.json", "experiment2_input_manifest.json"}
    if not relative.parts or relative.parts[0] not in allowed:
        raise Experiment2Error("only frozen RTL, constraints, reports, backend logs and manifests are readable")
    if not path.is_file():
        raise Experiment2Error(f"file not found: {relative}")
    start = int(arguments.get("start_line") or 1)
    count = int(arguments.get("line_count") or 200)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return {"path": str(relative), "start_line": start, "lines": lines[start - 1 : start - 1 + count]}


def list_bounded_project_files(project: Path, arguments: dict[str, Any]) -> dict[str, Any]:
    """List names under an allowed project prefix without exposing file contents."""
    relative = Path(str(arguments["prefix"]))
    if relative.is_absolute() or ".." in relative.parts:
        raise Experiment2Error("prefix must be project-relative")
    allowed = {"rtl", "constraints", "reports", "backend", "metadata.json", "experiment2_input_manifest.json"}
    if not relative.parts or relative.parts[0] not in allowed:
        raise Experiment2Error("only frozen RTL, constraints, reports, backend logs and manifests are listable")
    project_root = project.resolve()
    root = (project / relative).resolve()
    if project_root not in root.parents:
        raise Experiment2Error("prefix escapes project")
    if not root.exists():
        raise Experiment2Error(f"prefix not found: {relative}")
    limit = int(arguments.get("max_results") or 100)
    if not 1 <= limit <= 200:
        raise Experiment2Error("max_results must be between 1 and 200")
    contains = str(arguments.get("contains") or "").strip().lower()
    candidates = [root] if root.is_file() else sorted(root.rglob("*"))
    files: list[dict[str, Any]] = []
    matched = 0
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if project_root not in resolved.parents or not resolved.is_file():
            continue
        project_relative = str(resolved.relative_to(project_root))
        if contains and contains not in project_relative.lower():
            continue
        matched += 1
        if len(files) < limit:
            files.append({"path": project_relative, "size_bytes": resolved.stat().st_size})
    return {
        "prefix": str(relative),
        "contains": contains or None,
        "files": files,
        "matched_count": matched,
        "truncated": matched > limit,
    }


def latest_backend_run(project: Path) -> Path | None:
    marker = project / "backend" / ".r2g_signoff_run"
    if marker.is_file():
        name = marker.read_text(encoding="utf-8", errors="ignore").strip()
        candidate = project / "backend" / name
        if name.startswith("RUN_") and candidate.is_dir():
            return candidate
    runs = [path for path in (project / "backend").glob("RUN_*") if path.is_dir()]
    return max(runs, key=lambda path: path.stat().st_mtime_ns) if runs else None


def final_artifact(run_dir: Path, name: str) -> Path | None:
    for folder in ("final", "results"):
        path = run_dir / folder / name
        if path.is_file() and path.stat().st_size:
            return path
    return None


def _finite_number(mapping: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        try:
            value = float(mapping.get(key))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            return value
    return None


def evaluate_checkpoint(project: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    failures = protected_input_failures(project, fixture)
    period = target_period(project)
    run_dir = latest_backend_run(project)
    reports = project / "reports"
    drc = read_json(reports / "drc.json", {}) or {}
    lvs = read_json(reports / "lvs.json", {}) or {}
    route = read_json(reports / "route.json", {}) or {}
    timing = read_json(reports / "timing_check.json", {}) or {}
    rcx = read_json(reports / "rcx.json", {}) or {}
    gate = read_json(reports / "signoff_gate.json", {}) or {}
    checks = gate.get("checks") if isinstance(gate.get("checks"), dict) else {}
    config = _parse_config(project / "constraints" / "config.mk")
    metrics = {
        "frequency_mhz": 1000.0 / period if period else None,
        "period_ns": period,
        "drc_violations": _finite_number(drc, "total_violations", "violations", "violation_count"),
        "lvs_mismatches": _finite_number(lvs, "mismatch_count", "total_mismatches", "violations"),
        "route_violations": _finite_number(route, "total_violations", "violations", "violation_count"),
        "setup_wns_ns": _finite_number(timing, "wns_ns", "wns"),
        "setup_tns_ns": _finite_number(timing, "tns_ns", "tns"),
        "hold_wns_ns": _finite_number(timing, "hold_wns"),
        "hold_tns_ns": _finite_number(timing, "hold_tns"),
        "antenna_violations": _finite_number(checks.get("antenna") or {}, "violations"),
        "core_utilization_target_pct": _finite_number(config, "CORE_UTILIZATION"),
    }
    gate_results: dict[str, dict[str, Any]] = {}

    def record(name: str, passed: bool, detail: str) -> None:
        gate_results[name] = {"status": "pass" if passed else "fail", "detail": detail}
        if not passed:
            failures.append(f"{name}: {detail}")

    record("protected_inputs", not protected_input_failures(project, fixture), "frozen RTL, top, platform, bounded footprint policy, clock and SDC policy")
    record("flow", bool(run_dir) and (checks.get("orfs") or {}).get("status") == "complete", f"run={run_dir} orfs={(checks.get('orfs') or {}).get('status')!r}")
    record("route", route.get("status") == "clean" and metrics["route_violations"] == 0, f"status={route.get('status')!r} violations={metrics['route_violations']!r}")
    record("drc", drc.get("status") == "clean" and metrics["drc_violations"] == 0, f"status={drc.get('status')!r} violations={metrics['drc_violations']!r}")
    record("lvs", lvs.get("status") == "clean" and metrics["lvs_mismatches"] == 0, f"status={lvs.get('status')!r} mismatches={metrics['lvs_mismatches']!r}")
    timing_ok = (
        timing.get("tier") == "clean"
        and metrics["setup_wns_ns"] is not None and metrics["setup_wns_ns"] >= 0
        and metrics["setup_tns_ns"] == 0
        and metrics["hold_wns_ns"] is not None and metrics["hold_wns_ns"] >= 0
        and metrics["hold_tns_ns"] == 0
    )
    record("timing", timing_ok, f"tier={timing.get('tier')!r} setup=({metrics['setup_wns_ns']!r},{metrics['setup_tns_ns']!r}) hold=({metrics['hold_wns_ns']!r},{metrics['hold_tns_ns']!r})")
    antenna = checks.get("antenna") or {}
    record("antenna", antenna.get("status") == "clean" and metrics["antenna_violations"] == 0, f"status={antenna.get('status')!r} violations={metrics['antenna_violations']!r}")
    record("rcx", rcx.get("status") in {"complete", "clean", "ok", "pass"}, f"status={rcx.get('status')!r}")
    task_provenance = (checks.get("task_provenance") or {}).get("status")
    task_provenance_ok = task_provenance == "bound" or (
        fixture.get("strict_clean_scope") == "fixed_target_physical_signoff"
        and task_provenance == "not_applicable"
    )
    provenance_ok = gate.get("status") == "pass" and all(
        (checks.get(name) or {}).get("status") == expected
        for name, expected in (("binding", "bound"), ("artifact_digest", "bound"))
    ) and task_provenance_ok and (
        (checks.get("report_binding") or {}).get("status") not in {"foreign", "unknown"}
    )
    record("provenance", provenance_ok, f"gate={gate.get('status')!r} binding={(checks.get('binding') or {}).get('status')!r} report_binding={(checks.get('report_binding') or {}).get('status')!r} artifact_digest={(checks.get('artifact_digest') or {}).get('status')!r} task={(checks.get('task_provenance') or {}).get('status')!r}")
    evidence = {}
    if run_dir:
        for name in ("6_final.def", "6_final.gds", "6_final.odb", "6_final.v", "6_final.spef"):
            path = final_artifact(run_dir, name)
            evidence[name] = {"path": str(path) if path else None, "sha256": sha256_file(path) if path else None}
    return {
        "schema_version": "1.1",
        "evaluated_at": now_iso(),
        "fixture_id": fixture["id"],
        "role": fixture.get("role", "unclassified"),
        "family_id": fixture.get("family_id"),
        "strict_clean_scope": "fixed_target_physical_signoff",
        "constraint_attestation": {
            "status": "bound" if not protected_input_failures(project, fixture) else "invalid",
            "mode": "fixed_registered_target",
            "target_frequency_mhz": TARGET_FREQUENCY_MHZ,
            "period_ns": TARGET_PERIOD_NS,
            "sdc_sha256": sha256_file(project / "constraints" / "constraint.sdc")
            if (project / "constraints" / "constraint.sdc").is_file() else None,
            "config_sha256": sha256_file(project / "constraints" / "config.mk")
            if (project / "constraints" / "config.mk").is_file() else None,
        },
        "project": str(project.resolve()),
        "run_dir": str(run_dir) if run_dir else None,
        "strict_clean": all(item["status"] == "pass" for item in gate_results.values()),
        "gates": gate_results,
        "metrics": metrics,
        "failures": sorted(set(failures)),
        "evidence": evidence,
    }


def method_runtime_state(project: Path) -> Path:
    """Return the isolated mutable state directory for one method/design run."""
    return project.resolve().parent / "runtime_state"


def signoff_environment(
    runtime_skills: Path,
    campaign_root: Path,
    project: Path,
) -> dict[str, str]:
    env = os.environ.copy()
    for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE", "__PYVENV_LAUNCHER__"):
        env.pop(key, None)
    state = method_runtime_state(project)
    state.mkdir(parents=True, exist_ok=True)
    env.update(
        {
            "ORFS_ROOT": str(Path.home() / "r2g_toolchain" / "OpenROAD-flow-scripts"),
            "NUM_CORES": "4",
            "R2G_SIGNOFF_LOOP_DIR": str(runtime_skills / "signoff-loop"),
            "R2G_DEF_GRAPH_DIR": str(runtime_skills / "def-graph"),
            "R2G_KNOWLEDGE_DB": str(state / "knowledge.sqlite"),
            "R2G_JOURNAL_DB": str(state / "journal.sqlite"),
            "R2G_HEURISTICS_PATH": str(state / "heuristics.json"),
            "R2G_STAGE_FRESHNESS": "content",
        }
    )
    return env


def run_strict_measurement(
    project: Path,
    fixture: dict[str, Any],
    runtime_skills: Path,
    campaign_root: Path,
    log_dir: Path,
) -> dict[str, Any]:
    pre_failures = protected_input_failures(project, fixture)
    if pre_failures:
        return evaluate_checkpoint(project, fixture)
    period = target_period(project)
    if period is None:
        return evaluate_checkpoint(project, fixture)
    run_dir = latest_backend_run(project)
    if run_dir is None:
        return evaluate_checkpoint(project, fixture)
    env = signoff_environment(runtime_skills, campaign_root, project)
    flow_variant = ""
    run_meta = read_json(run_dir / "run-meta.json", {}) or {}
    if isinstance(run_meta.get("flow_variant"), str):
        flow_variant = run_meta["flow_variant"]
    fix = runtime_skills / "signoff-loop" / "scripts" / "flow" / "fix_signoff.sh"
    command = ["bash", str(fix), str(project), "sky130hd", "--check", "both", "--max-iters", "0"]
    if flow_variant:
        command.extend(["--variant", flow_variant])
    run(command, cwd=campaign_root, env=env, log=log_dir / "strict_signoff.log")
    run_dir = latest_backend_run(project) or run_dir
    final_def = final_artifact(run_dir, "6_final.def")
    gate = runtime_skills / "def-graph" / "scripts" / "flow" / "signoff_gate.py"
    gate_command = [
        "python3", str(gate), str(project), "--run-dir", str(run_dir),
        "--mode", "strict",
    ]
    if final_def:
        gate_command.extend(["--def", str(final_def)])
    run(gate_command, cwd=campaign_root, env=env, log=log_dir / "signoff_gate.log")
    return evaluate_checkpoint(project, fixture)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, sort_keys=True) + "\n")


def copy_checkpoint(project: Path, destination: Path) -> None:
    if destination.exists():
        raise Experiment2Error(f"checkpoint already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = run(["cp", "-a", "--reflink=auto", str(project), str(destination)])
    if result.returncode:
        raise Experiment2Error((result.stderr or result.stdout or "checkpoint copy failed").strip())


def import_experiment1_verifier():
    path = REPO / "tools" / "run_experiment1_rtl_acquisition.py"
    spec = importlib.util.spec_from_file_location("r2g_experiment1_verifier", path)
    if spec is None or spec.loader is None:
        raise Experiment2Error("cannot import Experiment 1 verifier")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
