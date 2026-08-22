#!/usr/bin/env python3
"""Materialize and execute provenance-recorded repair-family development probes."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any


REPO = Path(__file__).resolve().parents[1]
SKILLS = REPO / "r2g-skills"
ORFS = Path.home() / "r2g_toolchain/OpenROAD-flow-scripts"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def input_integrity_failures(project: Path, manifest: dict[str, Any]) -> list[str]:
    """Recheck every frozen compilation/config input before trusting a probe run."""
    failures: list[str] = []
    file_records = manifest.get("files") or []
    for record in file_records:
        if not isinstance(record, dict) or not record.get("path"):
            failures.append("invalid frozen source-file record")
            continue
        path = project / record["path"]
        if not path.is_file():
            failures.append(f"missing frozen input: {record['path']}")
            continue
        if path.stat().st_size != record.get("size") or sha256_file(path) != record.get("sha256"):
            failures.append(f"mutated frozen input: {record['path']}")

    protected = manifest.get("protected_task") or {}
    source_digest = hashlib.sha256(
        json.dumps(file_records, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if source_digest != protected.get("source_digest"):
        failures.append("frozen source-closure digest mismatch")

    for artifact in manifest.get("config_artifacts") or []:
        if not isinstance(artifact, dict) or not artifact.get("path"):
            failures.append("invalid frozen config-artifact record")
            continue
        path = project / artifact["path"]
        if not path.is_file():
            failures.append(f"missing frozen config artifact: {artifact['path']}")
        elif path.stat().st_size != artifact.get("size") or sha256_file(path) != artifact.get(
            "sha256"
        ):
            failures.append(f"mutated frozen config artifact: {artifact['path']}")

    protected_digest = hashlib.sha256(
        json.dumps(protected, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if protected_digest != manifest.get("protected_task_digest"):
        failures.append("protected-task digest mismatch")

    sdc_path = project / "constraints/constraint.sdc"
    if not sdc_path.is_file():
        failures.append("missing frozen SDC: constraints/constraint.sdc")
    elif sha256_file(sdc_path) != protected.get("sdc_sha256"):
        failures.append("mutated frozen SDC: constraints/constraint.sdc")

    config_path = project / "constraints/config.mk"
    if not config_path.is_file():
        failures.append("missing frozen config: constraints/config.mk")
    elif sha256_file(config_path) != manifest.get("config_sha256"):
        failures.append("mutated frozen config: constraints/config.mk")
    return failures


def parse_edits(values: list[str]) -> dict[str, str]:
    result = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"expected KEY=VALUE: {value}")
        key, item = value.split("=", 1)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError(f"invalid config key: {key}")
        result[key] = item
    return result


def materialize(args: argparse.Namespace) -> None:
    source = args.source.resolve()
    project = args.project.resolve()
    if project.exists():
        raise ValueError(f"project already exists: {project}")
    if bool(args.source_repo_url) != bool(args.source_commit):
        raise ValueError("--source-repo-url and --source-commit must be provided together")
    allowed_suffixes = {".v", ".sv", ".vh", ".svh", ".mem", ".hex"}
    if args.rtl_file:
        requested = [Path(value) for value in args.rtl_file]
        if any(path.is_absolute() or ".." in path.parts for path in requested):
            raise ValueError("--rtl-file paths must be relative to --source")
        rtl_files = sorted(source / path for path in requested)
        missing = [path for path in rtl_files if not path.is_file()]
        if missing:
            raise ValueError(f"missing explicit RTL input: {missing[0]}")
        if any(path.suffix.lower() not in allowed_suffixes for path in rtl_files):
            raise ValueError("explicit RTL closure contains an unsupported file type")
        relative_files = [path.relative_to(source) for path in rtl_files]
    else:
        rtl_source = source / "rtl"
        if not rtl_source.is_dir():
            raise ValueError(f"source snapshot has no rtl directory: {source}")
        rtl_files = sorted(
            path for path in rtl_source.rglob("*")
            if path.is_file() and path.suffix.lower() in allowed_suffixes
        )
        relative_files = [path.relative_to(rtl_source) for path in rtl_files]
    if not rtl_files:
        raise ValueError(f"source snapshot has no RTL files: {source}")
    for name in ("rtl", "constraints", "backend", "reports", "drc", "lvs", "rcx", "input"):
        (project / name).mkdir(parents=True, exist_ok=True)
    for source_file, relative in zip(rtl_files, relative_files):
        destination = project / "rtl" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, destination)
    copied = sorted(path for path in (project / "rtl").rglob("*") if path.is_file())
    file_records = [
        {
            "path": str(path.relative_to(project)),
            "sha256": sha256_file(path),
            "size": path.stat().st_size,
        }
        for path in copied
    ]
    source_digest = hashlib.sha256(
        json.dumps(file_records, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    period_ns = 1000.0 / args.frequency_mhz
    sdc = (
        f"create_clock -name {args.clock_port} -period {period_ns:.9g} "
        f"[get_ports {{{args.clock_port}}}]\n"
        "set_clock_uncertainty 0.0 [get_clocks *]\n"
    )
    sdc_path = project / "constraints/constraint.sdc"
    sdc_path.write_text(sdc, encoding="utf-8")
    edits = parse_edits(args.set)
    defaults = {
        "CORE_UTILIZATION": "25",
        "PLACE_DENSITY_LB_ADDON": "0.20",
        "ABC_AREA": "0",
    }
    defaults.update(edits)
    config_artifacts = []
    if args.fastroute_tcl:
        source_tcl = args.fastroute_tcl.resolve()
        if not source_tcl.is_file():
            raise ValueError(f"missing FASTROUTE_TCL artifact: {source_tcl}")
        target_tcl = project / "constraints/fastroute.tcl"
        shutil.copy2(source_tcl, target_tcl)
        defaults["FASTROUTE_TCL"] = str(target_tcl)
        edits["FASTROUTE_TCL"] = str(target_tcl)
        config_artifacts.append(
            {
                "knob": "FASTROUTE_TCL",
                "path": str(target_tcl.relative_to(project)),
                "sha256": sha256_file(target_tcl),
                "size": target_tcl.stat().st_size,
            }
        )
    for key in args.unset:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError(f"invalid config key to unset: {key}")
        defaults.pop(key, None)
    verilog = [str(path) for path in copied if path.suffix.lower() in {".v", ".sv"}]
    # VERILOG_INCLUDE_DIRS must cover every directory that could resolve a bare
    # `` `include "file.v" ``. Some real designs (mor1kx, riscv_top, usb_device) keep
    # their defines in a `.v` file one directory above the including module, so
    # restricting to `.vh`/`.svh` silently breaks synth for them. Add ALL Verilog source
    # directories (extra `-I` dirs are harmless — yosys only consults them after the
    # including file's own directory misses).
    includes = sorted(
        {str(path.parent) for path in copied if path.suffix.lower() in {".v", ".sv", ".vh", ".svh"}}
    )
    lines = [
        f"export DESIGN_NAME = {args.top_module}",
        f"export PLATFORM = {args.platform}",
        "export VERILOG_FILES = " + " ".join(verilog),
        f"export SDC_FILE = {sdc_path}",
    ]
    if includes:
        lines.append("export VERILOG_INCLUDE_DIRS = " + " ".join(includes))
    lines.extend(f"export {key} = {value}" for key, value in sorted(defaults.items()))
    config_path = project / "constraints/config.mk"
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    protected = {
        "source_digest": source_digest,
        "source_repo_url": args.source_repo_url,
        "source_commit": args.source_commit,
        "top_module": args.top_module,
        "platform": args.platform,
        "clock_port": args.clock_port,
        "target_frequency_mhz": args.frequency_mhz,
        "sdc_sha256": sha256_file(sdc_path),
        "signoff_mode": "strict",
        "check_set": ["orfs", "route", "drc", "lvs", "setup", "hold", "antenna", "rcx"],
    }
    protected_digest = hashlib.sha256(
        json.dumps(protected, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    manifest = {
        "schema_version": "repair-family-probe-input-1.0",
        "created_at": now(),
        "family_id": args.family,
        "task_id": args.task_id,
        "variant": args.variant,
        "source_snapshot": str(source),
        "source_repo_url": args.source_repo_url,
        "source_commit": args.source_commit,
        "source_provenance_status": (
            "repo_commit_and_bytes_bound"
            if args.source_repo_url and args.source_commit
            else "snapshot_only_requires_repo_commit_reconstruction"
        ),
        "files": file_records,
        "protected_task": protected,
        "protected_task_digest": protected_digest,
        "config_edits": edits,
        "config_unsets": sorted(set(args.unset)),
        "config_artifacts": config_artifacts,
        "config_sha256": sha256_file(config_path),
    }
    write_json(project / "repair_family_probe_input.json", manifest)
    write_json(
        project / "metadata.json",
        {
            "design_name": args.top_module,
            "platform": args.platform,
            "source_kind": "historical_snapshot_probe",
            "source_digest": source_digest,
            "source_bytes_verified": True,
            "compile_inputs_verified": True,
            "rtl_readiness": "ready",
            "repair_family_task_id": args.task_id,
        },
    )
    print(project)


def run_logged(command: list[str], *, env: dict[str, str], cwd: Path, log: Path) -> dict[str, Any]:
    log.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with log.open("w", encoding="utf-8") as stream:
        result = subprocess.run(command, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT)
    return {
        "command": command,
        "returncode": result.returncode,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "log": str(log),
    }


def latest_run(project: Path) -> Path | None:
    runs = [path for path in (project / "backend").glob("RUN_*") if path.is_dir()]
    return max(runs, key=lambda path: path.stat().st_mtime) if runs else None


def metric(report: dict[str, Any], *keys: str) -> Any:
    value: Any = report
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def classify_flow_failures(flow_log: str) -> tuple[bool, bool, list[str]]:
    input_failure = bool(
        re.search(r"Can't open include file|cannot open include file|missing explicit RTL input", flow_log, re.I)
    )
    runtime_failure = bool(
        re.search(r"Stage\s+'(?:route|place|cts|synth)'\s+failed\s+\(exit code 124\)", flow_log)
        or re.search(r"timed out after\s+\d+s, exit code 124", flow_log, re.I)
    )
    signatures: list[str] = []
    if input_failure:
        signatures.append("SYNTH_MISSING_INCLUDE")
    if runtime_failure:
        timeout_stage = re.search(r"Stage\s+'([^']+)'\s+failed\s+\(exit code 124\)", flow_log)
        signatures.append(f"{(timeout_stage.group(1) if timeout_stage else 'FLOW').upper()}_TIMEOUT")
    return input_failure, runtime_failure, signatures


def final_artifact(run_dir: Path, name: str) -> Path:
    for subdir in ("final", "results", ""):
        candidate = run_dir / subdir / name if subdir else run_dir / name
        if candidate.is_file():
            return candidate
    return run_dir / "results" / name


def execution_environment(args: argparse.Namespace, state: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE", "__PYVENV_LAUNCHER__"):
        env.pop(key, None)
    env.update(
        {
            "ORFS_ROOT": str(ORFS),
            "ORFS_TIMEOUT": str(args.timeout_seconds),
            "NUM_CORES": str(args.cores),
            "R2G_SIGNOFF_LOOP_DIR": str(SKILLS / "signoff-loop"),
            "R2G_DEF_GRAPH_DIR": str(SKILLS / "def-graph"),
            "R2G_KNOWLEDGE_DB": str(state / "knowledge.sqlite"),
            "R2G_JOURNAL_DB": str(state / "journal.sqlite"),
            "R2G_HEURISTICS_PATH": str(state / "heuristics.json"),
            "R2G_STAGE_FRESHNESS": "content",
        }
    )
    # ORFS_MAX_CPUS is a CPU-index ceiling, not a per-flow worker count. Leaving it
    # unset lets independently launched probes use the host scheduler normally.
    env.pop("ORFS_MAX_CPUS", None)
    return env


def execute(args: argparse.Namespace) -> None:
    project = args.project.resolve()
    manifest = read_json(project / "repair_family_probe_input.json")
    if not isinstance(manifest, dict):
        raise ValueError(f"project has no probe manifest: {project}")
    integrity_failures = input_integrity_failures(project, manifest)
    if integrity_failures:
        raise ValueError("frozen probe input integrity failed: " + "; ".join(integrity_failures))
    platform = manifest["protected_task"]["platform"]
    state = project.parent / "runtime_state" / project.name
    state.mkdir(parents=True, exist_ok=True)
    env = execution_environment(args, state)
    logs = project.parent / "logs" / project.name
    previous_result = read_json(project / "repair_family_probe_result.json", {}) or {}
    commands = list(previous_result.get("commands") or []) if args.collect_only else []
    variant = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"repair_probe_{manifest['task_id']}_{manifest['variant']}")
    if not args.skip_orfs and not args.collect_only:
        commands.append(
            run_logged(
                ["bash", str(SKILLS / "signoff-loop/scripts/flow/run_orfs.sh"), str(project), platform, variant],
                env=env,
                cwd=project.parent,
                log=logs / "run_orfs.log",
            )
        )
    run_dir = latest_run(project)
    if (args.skip_orfs or args.collect_only) and run_dir is None:
        raise ValueError("--skip-orfs/--collect-only requires an existing backend run")
    if (
        not args.collect_only
        and run_dir
        and any(final_artifact(run_dir, name).is_file() for name in ("6_final.def", "6_final.odb"))
    ):
        commands.append(
            run_logged(
                [
                    "bash",
                    str(SKILLS / "signoff-loop/scripts/flow/fix_signoff.sh"),
                    str(project),
                    platform,
                    "--check",
                    "both",
                    "--max-iters",
                    "0",
                    "--variant",
                    variant,
                ],
                env=env,
                cwd=project.parent,
                log=logs / "strict_signoff.log",
            )
        )
        run_dir = latest_run(project) or run_dir
        final_def = final_artifact(run_dir, "6_final.def")
        gate_command = [
            "python3",
            str(SKILLS / "def-graph/scripts/flow/signoff_gate.py"),
            str(project),
            "--run-dir",
            str(run_dir),
            "--mode",
            "strict",
        ]
        if final_def.is_file():
            gate_command.extend(["--def", str(final_def)])
        commands.append(
            run_logged(gate_command, env=env, cwd=project.parent, log=logs / "signoff_gate.log")
        )

    reports = {
        name: read_json(project / "reports" / f"{name}.json", {}) or {}
        for name in ("signoff_gate", "signoff_manifest", "drc", "lvs", "route", "timing_check", "rcx", "ppa")
    }
    flow_log = ""
    if run_dir and (run_dir / "flow.log").is_file():
        flow_log = (run_dir / "flow.log").read_text(encoding="utf-8", errors="ignore")
    signature = sorted(set(re.findall(r"\[ERROR\s+([A-Z]+-\d+)\]", flow_log)))
    input_qualification_failure, runtime_budget_failure, classified = classify_flow_failures(flow_log)
    signature.extend(classified)
    drc = reports["drc"]
    timing = reports["timing_check"]
    ppa_timing = metric(reports["ppa"], "summary", "timing") or {}
    gate_checks = reports["signoff_gate"].get("checks", {})
    gate_antenna = gate_checks.get("antenna", {}) if isinstance(gate_checks, dict) else {}
    metrics = {
        "route_violations": first_present(
            reports["route"].get("total_violations"),
            reports["route"].get("violations"),
        ),
        "drc_violations": first_present(drc.get("total_violations"), drc.get("violations")),
        "antenna_violations": first_present(
            gate_antenna.get("violations"),
            drc.get("antenna_violations"),
        ),
        "lvs_mismatches": first_present(
            reports["lvs"].get("mismatch_count"),
            reports["lvs"].get("mismatches"),
        ),
        "setup_wns_ns": first_present(
            ppa_timing.get("setup_wns"),
            timing.get("setup_wns_ns"),
            timing.get("wns_ns"),
            timing.get("wns"),
        ),
        "hold_wns_ns": first_present(
            ppa_timing.get("hold_wns"),
            timing.get("hold_wns_ns"),
            timing.get("hold_wns"),
        ),
    }
    categories = drc.get("categories")
    if isinstance(categories, dict):
        for rule_class, detail in categories.items():
            count = detail.get("count") if isinstance(detail, dict) else detail
            if isinstance(count, (int, float)) and count > 0:
                signature.append(f"DRC:{rule_class}")
    if isinstance(metrics["route_violations"], (int, float)) and metrics["route_violations"] > 0:
        signature.append("ROUTE_VIOLATIONS")
    if isinstance(metrics["setup_wns_ns"], (int, float)) and metrics["setup_wns_ns"] < 0:
        signature.append("SETUP_TIMING")
    if isinstance(metrics["hold_wns_ns"], (int, float)) and metrics["hold_wns_ns"] < 0:
        signature.append("HOLD_TIMING")
    signature = sorted(set(signature))
    gate = reports["signoff_gate"]
    strict_clean = gate.get("status") in {"clean", "pass", "strict_clean"}
    publication_strict_clean = reports["signoff_manifest"].get("strict_clean") is True
    result = {
        "schema_version": "repair-family-probe-result-1.1",
        "completed_at": now(),
        "family_id": manifest["family_id"],
        "task_id": manifest["task_id"],
        "variant": manifest["variant"],
        "run_id": run_dir.name if run_dir else None,
        "run_dir": str(run_dir) if run_dir else None,
        "protected_task_digest": manifest["protected_task_digest"],
        "config_sha256": manifest["config_sha256"],
        "strict_clean": strict_clean,
        "strict_clean_scope": "fixed_target_physical_signoff",
        "publication_strict_clean": publication_strict_clean,
        "input_qualification_failure": input_qualification_failure,
        "runtime_budget_failure": runtime_budget_failure,
        "constraint_attestation": {
            "status": "bound",
            "mode": "fixed_registered_target",
            "target_frequency_mhz": manifest["protected_task"]["target_frequency_mhz"],
            "sdc_sha256": manifest["protected_task"]["sdc_sha256"],
            "config_sha256": manifest["config_sha256"],
        },
        "environment_failure": any(item["returncode"] in (126, 127) for item in commands),
        "normalized_failure_signature": signature,
        "metrics": metrics,
        "commands": commands,
        "report_status": {
            name: value.get("status") if isinstance(value, dict) else None
            for name, value in reports.items()
        },
    }
    write_json(project / "repair_family_probe_result.json", result)
    print(json.dumps(result, indent=2, sort_keys=True))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("materialize")
    prepare.add_argument("--source", type=Path, required=True)
    prepare.add_argument("--source-repo-url")
    prepare.add_argument("--source-commit")
    prepare.add_argument(
        "--rtl-file",
        action="append",
        default=[],
        help="relative compilation input; repeat to freeze an explicit source closure",
    )
    prepare.add_argument("--project", type=Path, required=True)
    prepare.add_argument("--family", required=True)
    prepare.add_argument("--task-id", required=True)
    prepare.add_argument("--variant", required=True)
    prepare.add_argument("--platform", required=True, choices=("sky130hd", "sky130hs", "nangate45"))
    prepare.add_argument("--top-module", required=True)
    prepare.add_argument("--clock-port", required=True)
    prepare.add_argument("--frequency-mhz", type=float, default=100.0)
    prepare.add_argument("--set", action="append", default=[])
    prepare.add_argument("--unset", action="append", default=[])
    prepare.add_argument("--fastroute-tcl", type=Path)
    prepare.set_defaults(func=materialize)
    run = sub.add_parser("execute")
    run.add_argument("--project", type=Path, required=True)
    run.add_argument("--cores", type=int, default=4)
    run.add_argument("--timeout-seconds", type=int, default=7200)
    run.add_argument("--skip-orfs", action="store_true", help="reuse the latest completed backend run")
    run.add_argument(
        "--collect-only",
        action="store_true",
        help="rebuild the probe result from existing run/reports without invoking EDA",
    )
    run.set_defaults(func=execute)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        args.func(args)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=os.sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
