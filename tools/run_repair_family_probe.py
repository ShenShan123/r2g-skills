#!/usr/bin/env python3
"""Materialize and execute provenance-recorded repair-family development probes."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
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


def _git_output(repo: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def _normalized_repo_url(value: str) -> str:
    value = value.strip().removesuffix(".git").rstrip("/")
    ssh_match = re.fullmatch(r"git@([^:]+):(.+)", value)
    if ssh_match:
        return f"{ssh_match.group(1)}/{ssh_match.group(2)}".lower()
    return re.sub(r"^[a-z]+://", "", value, flags=re.I).lower()


def source_provenance_status(
    source: Path,
    rtl_files: list[Path],
    repo_url: str | None,
    commit: str | None,
) -> str:
    """Describe only provenance that can be verified from the source checkout.

    A caller-provided URL and commit are declarations, not evidence. Full binding
    requires a matching origin, an exact resolved commit, and every frozen source
    file to match its Git blob at that commit.
    """
    if not repo_url or not commit:
        return "snapshot_bytes_bound_repo_commit_missing"
    root_text = _git_output(source, "rev-parse", "--show-toplevel")
    if not root_text:
        return "snapshot_bytes_bound_repo_commit_unverified"
    root = Path(root_text).resolve()
    resolved = _git_output(root, "rev-parse", f"{commit}^{{commit}}")
    if not resolved or resolved.lower() != commit.lower():
        return "snapshot_bytes_bound_repo_commit_unverified"
    origin = _git_output(root, "remote", "get-url", "origin")
    if not origin or _normalized_repo_url(origin) != _normalized_repo_url(repo_url):
        return "commit_and_bytes_bound_repo_url_unverified"
    for path in rtl_files:
        try:
            relative = path.resolve().relative_to(root).as_posix()
        except ValueError:
            return "snapshot_bytes_bound_repo_commit_unverified"
        expected_blob = _git_output(root, "rev-parse", f"{resolved}:{relative}")
        actual_blob = _git_output(root, "hash-object", str(path.resolve()))
        if not expected_blob or expected_blob != actual_blob:
            return "snapshot_bytes_bound_repo_commit_unverified"
    return "repo_url_commit_and_bytes_bound"


def materialize(args: argparse.Namespace) -> None:
    source = args.source.resolve()
    project = args.project.resolve()
    if project.exists():
        raise ValueError(f"project already exists: {project}")
    if bool(args.source_repo_url) != bool(args.source_commit):
        raise ValueError("--source-repo-url and --source-commit must be provided together")
    compile_suffixes = {".v", ".sv"}
    dependency_suffixes = {".v", ".sv", ".vh", ".svh", ".mem", ".hex", ".dat"}
    dependency_values = list(getattr(args, "dependency_file", []) or [])
    if args.rtl_file:
        requested = [Path(value) for value in args.rtl_file]
        dependencies = [Path(value) for value in dependency_values]
        all_requested = requested + dependencies
        if any(path.is_absolute() or ".." in path.parts for path in all_requested):
            raise ValueError("source input paths must be relative to --source")
        if set(requested) & set(dependencies):
            raise ValueError("a source input cannot be both --rtl-file and --dependency-file")
        compile_files = sorted(source / path for path in requested)
        dependency_files = sorted(source / path for path in dependencies)
        rtl_files = compile_files + dependency_files
        missing = [path for path in rtl_files if not path.is_file()]
        if missing:
            raise ValueError(f"missing explicit RTL input: {missing[0]}")
        if any(path.suffix.lower() not in compile_suffixes for path in compile_files):
            raise ValueError("explicit compilation unit contains an unsupported HDL type")
        if any(path.suffix.lower() not in dependency_suffixes for path in dependency_files):
            raise ValueError("explicit dependency closure contains an unsupported file type")
        relative_files = [path.relative_to(source) for path in rtl_files]
        compile_relatives = {path.relative_to(source) for path in compile_files}
    else:
        if dependency_values:
            raise ValueError("--dependency-file requires at least one --rtl-file")
        rtl_source = source / "rtl"
        if not rtl_source.is_dir():
            raise ValueError(f"source snapshot has no rtl directory: {source}")
        rtl_files = sorted(
            path for path in rtl_source.rglob("*")
            if path.is_file() and path.suffix.lower() in dependency_suffixes
        )
        relative_files = [path.relative_to(rtl_source) for path in rtl_files]
        compile_relatives = {
            relative for path, relative in zip(rtl_files, relative_files)
            if path.suffix.lower() in compile_suffixes
        }
    if not rtl_files:
        raise ValueError(f"source snapshot has no RTL files: {source}")
    provenance_status = source_provenance_status(
        source, rtl_files, args.source_repo_url, args.source_commit
    )
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
        "CORE_UTILIZATION": "20",
        "PLACE_DENSITY_LB_ADDON": "0.20",
        # Keep probe controls on the same Sky130HD baseline as
        # mk_sky130_project.py. Otherwise ABC_AREA=1 can appear to be a repair
        # even though it is already part of the production materialization policy.
        "ABC_AREA": "1",
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
    copied_by_relative = {
        path.relative_to(project / "rtl"): path
        for path in copied
    }
    verilog = [
        str(copied_by_relative[relative])
        for relative in sorted(compile_relatives)
        if relative.suffix.lower() in {".v", ".sv"}
    ]
    compilation_units = [
        str(path.relative_to(project)) for path in map(Path, verilog)
    ]
    dependency_inputs = [
        str(path.relative_to(project))
        for relative, path in sorted(copied_by_relative.items())
        if relative not in compile_relatives
    ]
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
        "compilation_units": compilation_units,
        "dependency_inputs": dependency_inputs,
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
        "source_provenance_status": provenance_status,
        "files": file_records,
        "compilation_units": compilation_units,
        "dependency_inputs": dependency_inputs,
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
            "source_repo_url": args.source_repo_url,
            "source_commit": args.source_commit,
            "source_provenance_status": provenance_status,
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


def classify_flow_failures(flow_log: str) -> tuple[bool, bool, bool, list[str]]:
    missing_include = bool(
        re.search(r"Can't open include file|cannot open include file|missing explicit RTL input", flow_log, re.I)
    )
    # Re-reading the same compilation unit through an explicit file list and a
    # source-level include is a closure construction error, not an ORFS
    # execution failure. Treat it like a missing dependency so it cannot enter
    # the physical-repair or learner paths.
    module_redefinition = bool(
        re.search(r"(?:ERROR:\s*)?Re-definition of module\b", flow_log, re.I)
    )
    # IFP-0065 alone can describe a genuinely undersized, repairable floorplan.
    # Classify it as an input-qualification failure only when synthesis/OpenDB also
    # proves that elaboration produced no physical instances at all.  Otherwise the
    # recipe learner can wastefully treat an empty netlist as a floorplan challenge.
    zero_cell_netlist = bool(
        re.search(r"number instances in verilog is\s+0\b", flow_log, re.I)
        or re.search(r"Design area\s+0(?:\.0+)?\s+um\^2", flow_log, re.I)
    )
    # A bounded no-macro task cannot turn a large inferred memory into a
    # physical-design Recipe.  This is neither a malformed RTL closure nor an
    # unclassified ORFS crash: retain it as explicit capacity evidence and keep
    # it out of replay, learner, and promotion paths.
    capacity_infeasible = bool(
        re.search(r"synthesized memory size\s+\d+\s+exceeds", flow_log, re.I)
        or re.search(r"exceeds\s+synth_memory_max_bits", flow_log, re.I)
    )
    input_failure = missing_include or module_redefinition or zero_cell_netlist
    runtime_failure = bool(
        re.search(r"Stage\s+'(?:route|place|cts|synth)'\s+failed\s+\(exit code 124\)", flow_log)
        or re.search(r"timed out after\s+\d+s, exit code 124", flow_log, re.I)
    )
    signatures: list[str] = []
    if missing_include:
        signatures.append("SYNTH_MISSING_INCLUDE")
    if module_redefinition:
        signatures.append("SYNTH_MODULE_REDEFINITION")
    if zero_cell_netlist:
        signatures.append("SYNTH_ZERO_CELL_NETLIST")
    if capacity_infeasible:
        signatures.append("SYNTH_MEMORY_CAPACITY")
    if runtime_failure:
        timeout_stage = re.search(r"Stage\s+'([^']+)'\s+failed\s+\(exit code 124\)", flow_log)
        signatures.append(f"{(timeout_stage.group(1) if timeout_stage else 'FLOW').upper()}_TIMEOUT")
    return input_failure, runtime_failure, capacity_infeasible, signatures


def mapped_cell_count(flow_log: str) -> int | None:
    """Return the elaborated cell count reported at floorplan entry.

    ORFS emits this before placement mutates the netlist. It is the only count
    used for the fixed main-track admission limit; later placement counts include
    inserted buffers and must not change eligibility.
    """
    counts = [
        int(value.replace(",", ""))
        for value in re.findall(r"number instances in verilog is\s+([0-9,]+)\b", flow_log, re.I)
    ]
    return counts[0] if counts else None


def mapped_cell_count_out_of_bounds(
    cell_count: int | None, min_mapped_cells: int | None, max_mapped_cells: int | None
) -> bool:
    if cell_count is None:
        return False
    return bool(
        (min_mapped_cells is not None and cell_count < min_mapped_cells)
        or (max_mapped_cells is not None and cell_count > max_mapped_cells)
    )


def constraint_coverage(flow_log: str) -> dict[str, int | str | None]:
    """Read ORFS ``check_setup`` coverage warnings from a floorplan flow log.

    A finite WNS for one selected clock is not a timing-clean design when other
    sequential registers have no clock constraint. The candidate path therefore
    requires an explicit zero count for unclocked register/latch pins. An
    unconstrained endpoint caused only by absent top-level I/O delays is retained
    as scope metadata: this fixed task does not define an external I/O model.
    Missing observations fail closed rather than guessing that coverage is good.
    """
    patterns = {
        "unclocked_register_pins": r"There are\s+([0-9,]+)\s+unclocked register/latch pins\.",
        "unconstrained_endpoints": r"There are\s+([0-9,]+)\s+unconstrained endpoints\.",
        "input_ports_missing_delay": r"There are\s+([0-9,]+)\s+input ports missing set_input_delay\.",
        "output_ports_missing_delay": r"There are\s+([0-9,]+)\s+output ports missing set_output_delay\.",
    }
    # ORFS emits these lines only when the corresponding count is nonzero.
    # Absence is therefore evidence of zero *only after* the floorplan
    # check_setup block is present; without that block the observation remains
    # unknown and the probe must fail closed.
    check_setup_seen = re.search(r"\bcheck_setup\b", flow_log, re.I) is not None
    counts: dict[str, int | None] = {}
    for name, pattern in patterns.items():
        matches = re.findall(pattern, flow_log, re.I)
        counts[name] = (
            int(matches[-1].replace(",", "")) if matches else (0 if check_setup_seen else None)
        )
    if counts["unclocked_register_pins"] is None:
        status = "unknown"
    elif int(counts["unclocked_register_pins"] or 0) > 0:
        status = "incomplete"
    else:
        status = "complete"
    return {"status": status, **counts}


UNCONSTRAINED_TIMING_SENTINEL = 1.0e30


def has_evaluable_final_timing(metrics: dict[str, Any]) -> bool:
    """Require real setup and hold observations for the fixed-frequency task.

    OpenSTA emits values around 1e39 when no constrained timing path exists.
    A declared clock and zero unclocked-register warnings are not sufficient in
    that case: a pathless top cannot demonstrate closure at the target period.
    """
    for key in ("setup_wns_ns", "hold_wns_ns"):
        value = metrics.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        numeric = float(value)
        if not math.isfinite(numeric) or abs(numeric) >= UNCONSTRAINED_TIMING_SENTINEL:
            return False
    return True


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
    # Keep the wrapper's CPU affinity ceiling aligned with the requested OpenROAD
    # worker count.  A probe must not silently expand to every host core merely
    # because it is launched outside the normal campaign wrapper.
    env["ORFS_MAX_CPUS"] = str(args.cores)
    stages = getattr(args, "orfs_stages", None)
    if stages:
        env["ORFS_STAGES"] = stages
    cpu_set = getattr(args, "cpu_set", None)
    if cpu_set:
        if not re.fullmatch(r"\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*", cpu_set):
            raise ValueError(f"invalid --cpu-set: {cpu_set}")
        env["ORFS_CPU_SET"] = cpu_set
    return env


def classify_execution_failure(
    commands: list[dict[str, Any]], known_signatures: list[str]
) -> tuple[bool, bool, list[str]]:
    """Classify non-diagnostic ORFS exits so they cannot become repair evidence."""
    flow_command = next(
        (
            item
            for item in commands
            if any(str(part).endswith("run_orfs.sh") for part in item.get("command", []))
        ),
        None,
    )
    if not flow_command:
        return False, False, []
    returncode = flow_command.get("returncode")
    if returncode in (130, 137, 143):
        return True, False, ["FLOW_INTERRUPTED"]
    if isinstance(returncode, int) and returncode != 0 and not known_signatures:
        return False, True, ["FLOW_EXECUTION_FAILED"]
    return False, False, []


def incomplete_collected_run(run_dir: Path | None, collect_only: bool) -> bool:
    """Recognize an externally stopped run during an explicit offline collection.

    `run-meta.json` is written by run_orfs.sh only after it has emitted a final
    success/failure exit. A missing file is therefore conclusive only when the
    caller deliberately performs collection after execution has stopped; it is
    never used to inspect a live run.
    """
    return bool(collect_only and run_dir and not (run_dir / "run-meta.json").is_file())


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
    input_qualification_failure, runtime_budget_failure, capacity_infeasible, classified = (
        classify_flow_failures(flow_log)
    )
    signature.extend(classified)
    cell_count = mapped_cell_count(flow_log)
    min_mapped_cells = getattr(args, "min_mapped_cells", None)
    max_mapped_cells = getattr(args, "max_mapped_cells", None)
    scale_ineligible = mapped_cell_count_out_of_bounds(
        cell_count, min_mapped_cells, max_mapped_cells
    )
    if scale_ineligible:
        signature.append("SYNTH_CELL_COUNT_OUT_OF_RANGE")
    coverage = constraint_coverage(flow_log)
    constraint_coverage_incomplete = coverage["status"] != "complete"
    if constraint_coverage_incomplete:
        signature.append("CONSTRAINT_COVERAGE_INCOMPLETE")
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
    timing_evaluation_incomplete = not has_evaluable_final_timing(metrics)
    if timing_evaluation_incomplete:
        signature.append("TIMING_EVALUATION_INCOMPLETE")
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
    execution_interrupted, unclassified_execution_failure, execution_signatures = (
        classify_execution_failure(commands, signature)
    )
    if incomplete_collected_run(run_dir, args.collect_only):
        execution_interrupted = True
        execution_signatures.append("FLOW_INTERRUPTED")
    signature.extend(execution_signatures)
    signature = sorted(set(signature))
    gate = reports["signoff_gate"]
    strict_clean = (
        gate.get("status") in {"clean", "pass", "strict_clean"}
        and not constraint_coverage_incomplete
        and not timing_evaluation_incomplete
    )
    publication_strict_clean = reports["signoff_manifest"].get("strict_clean") is True
    result = {
        "schema_version": "repair-family-probe-result-1.3",
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
        "capacity_infeasible": capacity_infeasible,
        "scale_ineligible": scale_ineligible,
        "constraint_coverage_incomplete": constraint_coverage_incomplete,
        "timing_evaluation_incomplete": timing_evaluation_incomplete,
        "constraint_coverage": coverage,
        "mapped_cells": cell_count,
        "min_mapped_cells": min_mapped_cells,
        "max_mapped_cells": max_mapped_cells,
        "orfs_stages": getattr(args, "orfs_stages", None) or "synth floorplan place cts route finish",
        "runtime_budget_failure": runtime_budget_failure,
        "execution_interrupted": execution_interrupted,
        "unclassified_execution_failure": unclassified_execution_failure,
        "constraint_attestation": {
            "status": (
                "bound_and_covered"
                if not constraint_coverage_incomplete and not timing_evaluation_incomplete
                else "bound_but_incomplete"
            ),
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
    prepare.add_argument(
        "--dependency-file",
        action="append",
        default=[],
        help=("relative include/readmem dependency; repeat to freeze it without adding it "
              "as an independent Verilog compilation unit"),
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
    run.add_argument(
        "--cpu-set",
        help="explicit taskset-compatible CPU list for this ORFS flow (for example 32-35)",
    )
    run.add_argument("--timeout-seconds", type=int, default=7200)
    run.add_argument(
        "--orfs-stages",
        help="space-separated ORFS stages for bounded preflight execution",
    )
    run.add_argument(
        "--min-mapped-cells",
        type=int,
        help="exclude a task when floorplan reports fewer elaborated cells than this floor",
    )
    run.add_argument(
        "--max-mapped-cells",
        type=int,
        help="exclude a task when floorplan reports more elaborated cells than this cap",
    )
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
