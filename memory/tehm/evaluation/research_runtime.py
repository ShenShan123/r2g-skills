"""Bounded execution facade for TEHM Research Runtime RC1.

The first supported profile is the Revision-4 S0 frontend/synthesis preflight.
It executes the frozen full denominator serially, in isolated copied
workspaces, and records every task in the append-only research ledger.  It
does not query or update memory and grants no flow, repair, or production
authority.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from tehm.evaluation.research_campaign import verify_prepared_campaign
from tehm.evaluation.research_inventory import verify_research_inventory
from tehm.evaluation.research_ledger import (
    append_attempt_event,
    artifact_reference,
    verify_attempt_ledger,
)


RUNTIME_VERSION = "tehm-research-runtime-rc1-v1"
SOURCE_REPORT_SCHEMA = "tehm-research-source-integrity-report-v1"


class ResearchRuntimeError(ValueError):
    """Raised when frozen inputs cannot enter the bounded research runtime."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResearchRuntimeError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ResearchRuntimeError(f"{label} must be a JSON object")
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
    temporary.write_bytes(_canonical(value) + b"\n")
    temporary.replace(path)


def _safe_relative(value: object, label: str) -> PurePosixPath:
    if type(value) is not str or not value:
        raise ResearchRuntimeError(f"{label} is invalid")
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ResearchRuntimeError(f"{label} is unsafe")
    return relative


def _normalise_digest(value: object, label: str) -> str:
    if type(value) is not str:
        raise ResearchRuntimeError(f"{label} is invalid")
    digest = value if value.startswith("sha256:") else "sha256:" + value
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ResearchRuntimeError(f"{label} is invalid")
    return digest


def _verify_runtime_source(epoch_root: Path) -> dict[str, Any]:
    """Bind the executing RC1 implementation to the frozen epoch bytes."""
    epoch = _load_json(epoch_root / "research-epoch.json", "research epoch")
    source = _load_json(
        epoch_root / "source/source-manifest.json", "source manifest"
    )
    repo = Path(__file__).resolve().parents[3]
    try:
        head = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise ResearchRuntimeError("cannot verify executing Git HEAD") from exc
    frozen_head = (epoch.get("source") or {}).get("git_head")
    if head != frozen_head or source.get("git_head") != frozen_head:
        raise ResearchRuntimeError("executing Git HEAD differs from frozen epoch")
    by_path = {
        item.get("path"): item for item in source.get("entries") or []
        if isinstance(item, Mapping) and item.get("kind") == "file"
    }
    critical = (
        "memory/scripts/research_pilot.py",
        "memory/tehm/evaluation/research_campaign.py",
        "memory/tehm/evaluation/research_inventory.py",
        "memory/tehm/evaluation/research_ledger.py",
        "memory/tehm/evaluation/research_runtime.py",
    )
    checked = []
    for relative in critical:
        frozen = by_path.get(relative)
        path = repo / relative
        if (not isinstance(frozen, Mapping) or not path.is_file() or
                path.stat().st_size != frozen.get("bytes") or
                _sha256_file(path) != frozen.get("sha256")):
            raise ResearchRuntimeError(
                f"executing runtime source differs from frozen epoch: {relative}"
            )
        checked.append(relative)
    return {"git_head": head, "critical_paths": checked}


def _source_report(manifest: Mapping[str, Any], project: Path) -> dict[str, Any]:
    entries = manifest.get("source_files")
    if not isinstance(entries, list) or not entries:
        raise ResearchRuntimeError("design manifest source_files are missing")
    checked: list[dict[str, Any]] = []
    missing: list[str] = []
    drifted: list[str] = []
    for index, raw in enumerate(entries):
        if not isinstance(raw, Mapping):
            raise ResearchRuntimeError(f"design source_files[{index}] is invalid")
        relative = _safe_relative(raw.get("path"), f"source_files[{index}].path")
        expected_digest = _normalise_digest(
            raw.get("sha256"), f"source_files[{index}].sha256"
        )
        expected_bytes = raw.get("bytes")
        if type(expected_bytes) is not int or expected_bytes < 0:
            raise ResearchRuntimeError(f"source_files[{index}].bytes is invalid")
        path = project / Path(*relative.parts)
        if not path.is_file():
            missing.append(relative.as_posix())
            actual_digest = None
            actual_bytes = None
        else:
            actual_digest = _sha256_file(path)
            actual_bytes = path.stat().st_size
            if actual_digest != expected_digest or actual_bytes != expected_bytes:
                drifted.append(relative.as_posix())
        checked.append({
            "path": relative.as_posix(),
            "expected_sha256": expected_digest,
            "expected_bytes": expected_bytes,
            "actual_sha256": actual_digest,
            "actual_bytes": actual_bytes,
        })
    identity_entries = [
        {"path": item["path"], "bytes": item["expected_bytes"],
         "sha256": item["expected_sha256"]}
        for item in checked
    ]
    expected_bundle = _normalise_digest(
        manifest.get("source_bundle_digest"), "source_bundle_digest"
    )
    computed_bundle = _digest(identity_entries)
    if computed_bundle != expected_bundle:
        drifted.append("source_bundle_manifest_identity")
    return {
        "schema": SOURCE_REPORT_SCHEMA,
        "runtime_version": RUNTIME_VERSION,
        "design_id": manifest.get("design_id"),
        "project_path": str(project),
        "expected_source_bundle_digest": expected_bundle,
        "computed_source_bundle_digest": computed_bundle,
        "entries": checked,
        "missing_files": sorted(set(missing)),
        "drifted_files": sorted(set(drifted)),
        "unchanged": not missing and not drifted,
    }


def _yosys_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _yosys_option(value: str, label: str) -> str:
    if (not value or any(character.isspace() for character in value) or
            any(character in value for character in ('"', "'", "\\"))):
        raise ResearchRuntimeError(f"{label} cannot be represented safely in Yosys")
    return value


def _yosys_atom(value: str, label: str) -> str:
    if not value or not re.fullmatch(r"[-+A-Za-z0-9_.$']+", value):
        raise ResearchRuntimeError(f"{label} cannot be represented safely in Yosys")
    return value


def _yosys_script(context: Mapping[str, Any], workspace: Path,
                  raw: Path) -> str:
    compile_input = context["compile_input"]
    filelist = compile_input["ordered_filelist"]
    if not isinstance(filelist, list) or not filelist:
        raise ResearchRuntimeError("frozen ordered_filelist is missing")
    files = []
    for index, value in enumerate(filelist):
        relative = _safe_relative(value, f"ordered_filelist[{index}]")
        path = workspace / Path(*relative.parts)
        if not path.is_file():
            raise ResearchRuntimeError(f"compile source is missing: {relative}")
        files.append(path)
    flags = ["-sv"]
    for value in compile_input.get("include_dirs") or []:
        relative = _safe_relative(value, "include_dir")
        flags.append(_yosys_option(
            "-I" + str(workspace / Path(*relative.parts)), "include_dir"
        ))
    defines = compile_input.get("defines") or {}
    if not isinstance(defines, Mapping):
        raise ResearchRuntimeError("frozen defines are invalid")
    for key, value in sorted(defines.items()):
        if type(key) is not str or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ResearchRuntimeError("frozen define name is invalid")
        option = f"-D{key}" if value in (None, "") else f"-D{key}={value}"
        flags.append(_yosys_option(option, "define"))
    top = compile_input.get("top_module")
    if type(top) is not str or not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", top):
        raise ResearchRuntimeError("frozen top module is invalid")
    parameters = compile_input.get("top_parameters") or {}
    if not isinstance(parameters, Mapping):
        raise ResearchRuntimeError("frozen top parameters are invalid")
    top_atom = _yosys_atom(top, "top module")
    hierarchy = ["hierarchy", "-check", "-top", top_atom]
    for key, value in sorted(parameters.items()):
        if type(key) is not str or not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", key):
            raise ResearchRuntimeError("frozen top parameter name is invalid")
        hierarchy.extend([
            "-chparam", _yosys_atom(key, "top parameter name"),
            _yosys_atom(str(value), "top parameter value"),
        ])
    read = "read_verilog " + " ".join(
        [*flags, *(_yosys_quote(str(path)) for path in files)]
    )
    return "\n".join([
        read,
        " ".join(hierarchy),
        "proc",
        f"write_json {_yosys_quote(str(raw / 'elaborated.json'))}",
        "synth -top " + top_atom,
        "stat",
        f"write_json {_yosys_quote(str(raw / 'synthesized.json'))}",
        "",
    ])


def _netlist_has_top(path: Path, top: str) -> bool:
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    modules = payload.get("modules") if isinstance(payload, Mapping) else None
    return isinstance(modules, Mapping) and top in modules


def _event(prepared: Path, ledger: Path, event_type: str, *, task_id: str,
           policy: str, attempt_id: str, payload: Mapping[str, Any]) -> None:
    append_attempt_event(
        prepared=prepared, ledger=ledger, event_type=event_type,
        task_id=task_id, policy=policy, attempt_id=attempt_id,
        payload=payload,
    )


def _check_event(prepared: Path, ledger: Path, *, task_id: str, policy: str,
                 attempt_id: str, name: str, verdict: str,
                 scope: Mapping[str, Any], refs: Sequence[Path]) -> None:
    _event(prepared, ledger, "CHECK_RECORDED", task_id=task_id,
           policy=policy, attempt_id=attempt_id, payload={
               "check_name": name,
               "verdict": verdict,
               "scope": dict(scope),
               "raw_report_refs": [artifact_reference(path) for path in refs],
           })


def _terminal(prepared: Path, ledger: Path, *, task_id: str, policy: str,
              attempt_id: str, status: str, exception: Mapping[str, str] | None,
              eda_calls: int, wallclock: float) -> None:
    _event(prepared, ledger, "ATTEMPT_TERMINAL", task_id=task_id,
           policy=policy, attempt_id=attempt_id, payload={
               "execution_status": status,
               "terminal_exception": dict(exception) if exception else None,
               "actual_cost": {
                   "eda_calls": eda_calls,
                   "model_calls": 0,
                   "model_tokens": 0,
                   "wallclock_seconds": round(max(0.0, wallclock), 6),
               },
           })


def _design_manifest(campaign: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
    inventory_root = Path(campaign["inventory"]["path"]).expanduser().resolve()
    inventory = _load_json(inventory_root / "inventory.json", "research inventory")
    index = {
        item.get("design_id"): item for item in inventory.get("designs") or []
        if isinstance(item, Mapping)
    }
    item = index.get(context["design_id"])
    if not isinstance(item, Mapping):
        raise ResearchRuntimeError("task design is absent from frozen inventory")
    relative = _safe_relative(item.get("manifest_path"), "design manifest path")
    manifest = _load_json(
        inventory_root / Path(*relative.parts), "design manifest"
    )
    if manifest.get("manifest_digest") != context["compile_input"]["design_manifest_digest"]:
        raise ResearchRuntimeError("task design manifest digest drifted")
    return manifest


def _run_frontend_task(*, prepared: Path, ledger: Path, output: Path,
                       campaign: Mapping[str, Any], context: Mapping[str, Any],
                       yosys: Path, toolchain_digest: str, timeout: float) -> None:
    task_id = context["task_id"]
    policy = "no_persistent_memory"
    attempt_id = "attempt-" + hashlib.sha256(
        f"{campaign['prepared_campaign_digest']}:{task_id}:{policy}:1".encode()
    ).hexdigest()[:24]
    attempt_root = output / "attempts" / task_id / policy / attempt_id
    raw = attempt_root / "raw"
    workspace = attempt_root / "workspace"
    raw.mkdir(parents=True)
    started = time.monotonic()
    _event(prepared, ledger, "ATTEMPT_STARTED", task_id=task_id,
           policy=policy, attempt_id=attempt_id, payload={
               "runtime_version": RUNTIME_VERSION,
               "controller_type": "deterministic_skill",
               "memory_consulted": False,
               "online_memory_update": False,
           })
    eda_calls = 0
    source_recorded = False
    recorded_checks: set[str] = set()
    try:
        manifest = _design_manifest(campaign, context)
        compile_input = context["compile_input"]
        project = (Path(compile_input["source_root"]).expanduser().resolve()
                   / Path(*_safe_relative(compile_input["project_path"],
                                          "project_path").parts))
        before = _source_report(manifest, project)
        _write_json(raw / "source-integrity-before.json", before)
        if not before["unchanged"]:
            _check_event(prepared, ledger, task_id=task_id, policy=policy,
                         attempt_id=attempt_id, name="source_integrity",
                         verdict="FAIL", scope=context["target_scope"],
                         refs=[raw / "source-integrity-before.json"])
            source_recorded = True
            recorded_checks.add("source_integrity")
            for name in ("elaboration", "synthesis"):
                _check_event(prepared, ledger, task_id=task_id, policy=policy,
                             attempt_id=attempt_id, name=name, verdict="UNKNOWN",
                             scope=context["target_scope"], refs=[])
                recorded_checks.add(name)
            _terminal(
                prepared, ledger, task_id=task_id, policy=policy,
                attempt_id=attempt_id, status="TOOL_ERROR",
                exception={"class": "source_drift",
                           "detail": "frozen source identity does not match inventory"},
                eda_calls=0, wallclock=time.monotonic() - started,
            )
            return
        shutil.copytree(project, workspace, symlinks=True)
        script = _yosys_script(context, workspace, raw)
        script_path = raw / "preflight.ys"
        script_path.write_text(script, encoding="utf-8")
        command = [str(yosys), "-s", str(script_path)]
        log_path = raw / "yosys.log"
        exit_code = 127
        terminal_status = "COMPLETED"
        terminal_exception = None
        tool_started = time.monotonic()
        try:
            with log_path.open("wb") as log:
                completed = subprocess.run(
                    command, cwd=workspace, stdout=log,
                    stderr=subprocess.STDOUT, timeout=max(0.001, timeout),
                    check=False,
                )
            exit_code = completed.returncode
        except subprocess.TimeoutExpired:
            exit_code = 124
            terminal_status = "TIMEOUT"
            terminal_exception = {
                "class": "wallclock_timeout",
                "detail": "bounded frontend preflight exceeded its remaining campaign budget",
            }
        eda_calls = 1
        tool_wallclock = time.monotonic() - tool_started
        if not log_path.exists():
            log_path.write_text("tool produced no captured output\n", encoding="utf-8")
        raw_artifacts = [script_path, log_path]
        raw_artifacts.extend(
            path for path in (raw / "elaborated.json", raw / "synthesized.json")
            if path.is_file()
        )
        result_payload = {
            "exit_code": exit_code,
            "artifacts": [
                {"name": path.name, "sha256": _sha256_file(path),
                 "bytes": path.stat().st_size}
                for path in raw_artifacts
            ],
        }
        _event(prepared, ledger, "TOOL_RESULT", task_id=task_id,
               policy=policy, attempt_id=attempt_id, payload={
                   "tool_name": "yosys",
                   "call_kind": "EDA",
                   "command_digest": _digest({
                       "argv": command,
                       "script_sha256": _sha256_file(script_path),
                   }),
                   "result_digest": _digest(result_payload),
                   "toolchain_digest": toolchain_digest,
                   "exit_code": exit_code,
                   "wallclock_seconds": round(tool_wallclock, 6),
                   "raw_artifacts": [artifact_reference(path) for path in raw_artifacts],
               })

        after = _source_report(manifest, project)
        source_report = {
            **after,
            "before_source_bundle_digest": before["computed_source_bundle_digest"],
            "before_unchanged": before["unchanged"],
        }
        _write_json(raw / "source-integrity.json", source_report)
        source_verdict = "PASS" if after["unchanged"] else "FAIL"
        _check_event(prepared, ledger, task_id=task_id, policy=policy,
                     attempt_id=attempt_id, name="source_integrity",
                     verdict=source_verdict, scope=context["target_scope"],
                     refs=[raw / "source-integrity.json"])
        source_recorded = True
        recorded_checks.add("source_integrity")

        top = compile_input["top_module"]
        elaborated = _netlist_has_top(raw / "elaborated.json", top)
        synthesized = _netlist_has_top(raw / "synthesized.json", top)
        if elaborated:
            elaboration_verdict = "PASS"
        elif terminal_status == "COMPLETED" and exit_code != 0:
            elaboration_verdict = "FAIL"
        else:
            elaboration_verdict = "UNKNOWN"
        if synthesized:
            synthesis_verdict = "PASS"
        elif elaborated and terminal_status == "COMPLETED" and exit_code != 0:
            synthesis_verdict = "FAIL"
        else:
            synthesis_verdict = "UNKNOWN"
        _check_event(
            prepared, ledger, task_id=task_id, policy=policy,
            attempt_id=attempt_id, name="elaboration",
            verdict=elaboration_verdict, scope=context["target_scope"],
            refs=[raw / "elaborated.json"] if elaborated else [log_path],
        )
        recorded_checks.add("elaboration")
        _check_event(
            prepared, ledger, task_id=task_id, policy=policy,
            attempt_id=attempt_id, name="synthesis",
            verdict=synthesis_verdict, scope=context["target_scope"],
            refs=[raw / "synthesized.json"] if synthesized else [log_path],
        )
        recorded_checks.add("synthesis")
        if source_verdict == "FAIL":
            terminal_status = "TOOL_ERROR"
            terminal_exception = {
                "class": "source_drift",
                "detail": "original source changed during isolated execution",
            }
        elif terminal_status == "COMPLETED" and exit_code == 0 and not synthesized:
            terminal_status = "TOOL_ERROR"
            terminal_exception = {
                "class": "missing_tool_output",
                "detail": "Yosys exited zero without a synthesized JSON netlist",
            }
        _terminal(
            prepared, ledger, task_id=task_id, policy=policy,
            attempt_id=attempt_id, status=terminal_status,
            exception=terminal_exception, eda_calls=eda_calls,
            wallclock=time.monotonic() - started,
        )
    except Exception as exc:
        fallback = raw / "runtime-exception.json"
        _write_json(fallback, {
            "schema": "tehm-research-runtime-exception-v1",
            "exception_class": type(exc).__name__,
            "detail": str(exc),
        })
        if not source_recorded:
            _check_event(prepared, ledger, task_id=task_id, policy=policy,
                         attempt_id=attempt_id, name="source_integrity",
                         verdict="UNKNOWN", scope=context["target_scope"],
                         refs=[fallback])
            recorded_checks.add("source_integrity")
        for name in ("elaboration", "synthesis"):
            if name not in recorded_checks:
                _check_event(prepared, ledger, task_id=task_id, policy=policy,
                             attempt_id=attempt_id, name=name,
                             verdict="UNKNOWN", scope=context["target_scope"],
                             refs=[fallback])
        _terminal(
            prepared, ledger, task_id=task_id, policy=policy,
            attempt_id=attempt_id, status="TOOL_ERROR",
            exception={"class": "runtime_error", "detail": str(exc)},
            eda_calls=eda_calls, wallclock=time.monotonic() - started,
        )


def run_research_campaign(*, prepared: str | Path, output: str | Path) -> dict[str, Any]:
    """Run the complete frozen S0 denominator and return ledger verification."""
    prepared_root = Path(prepared).expanduser().resolve()
    destination = Path(output).expanduser().resolve()
    if destination.exists():
        raise ResearchRuntimeError(f"refusing to overwrite research run: {destination}")
    checked = verify_prepared_campaign(prepared_root)
    if not checked.get("valid"):
        raise ResearchRuntimeError("prepared campaign is invalid")
    campaign = _load_json(prepared_root / "prepared-campaign.json", "prepared campaign")
    if campaign.get("profile") != "frontend_preflight":
        raise ResearchRuntimeError(
            "RC1 run currently supports only the frontend_preflight profile"
        )
    if campaign.get("policies") != ["no_persistent_memory"]:
        raise ResearchRuntimeError(
            "frontend preflight requires exactly the no_persistent_memory policy"
        )
    if campaign["budget"].get("model_call_limit") != 0:
        raise ResearchRuntimeError("deterministic frontend preflight requires zero model budget")
    inventory = verify_research_inventory(campaign["inventory"]["path"])
    if not inventory.get("valid") or not inventory.get("corpus_unchanged"):
        raise ResearchRuntimeError("frozen research inventory or corpus has drifted")
    epoch_root = Path(campaign["epoch"]["path"]).expanduser().resolve()
    source_binding = _verify_runtime_source(epoch_root)
    corpus_root = Path(str(inventory["corpus_root"])).expanduser().resolve()
    if destination == corpus_root or destination.is_relative_to(corpus_root):
        raise ResearchRuntimeError("research run output must be outside the source corpus")
    toolchain = _load_json(
        epoch_root / "bindings/toolchain-manifest.json", "toolchain manifest"
    )
    tool = (toolchain.get("tools") or {}).get("yosys") or {}
    yosys = Path(str(tool.get("path") or "")).expanduser().resolve()
    if not yosys.is_file() or not os.access(yosys, os.X_OK):
        raise ResearchRuntimeError("frozen Yosys executable is unavailable")
    if _sha256_file(yosys) != _normalise_digest(tool.get("sha256"), "yosys sha256"):
        raise ResearchRuntimeError("frozen Yosys executable digest drifted")
    toolchain_digest = _normalise_digest(
        toolchain.get("manifest_digest"), "toolchain manifest_digest"
    )
    destination.mkdir(parents=True)
    ledger = destination / "attempt-ledger.jsonl"
    campaign_started = time.monotonic()
    for task_id in campaign["task_ids"]:
        context = _load_json(
            prepared_root / "task-contexts" / f"{task_id}.json", "task context"
        )
        remaining = (float(campaign["budget"]["wallclock_limit_seconds"])
                     - (time.monotonic() - campaign_started))
        _run_frontend_task(
            prepared=prepared_root, ledger=ledger, output=destination,
            campaign=campaign, context=context, yosys=yosys,
            toolchain_digest=toolchain_digest, timeout=max(0.001, remaining),
        )
    result = verify_attempt_ledger(prepared=prepared_root, ledger=ledger)
    result.update({
        "runtime_version": RUNTIME_VERSION,
        "output": str(destination),
        "all_registered_terminal": (
            result["terminal_attempt_count"] ==
            len(campaign["task_ids"]) * len(campaign["policies"])
        ),
        "source_mutation": "none",
        "memory_update": "none",
        "production_authority": False,
        "runtime_source_binding": source_binding,
    })
    _write_json(destination / "run-summary.json", result)
    return result


__all__ = [
    "RUNTIME_VERSION", "ResearchRuntimeError", "run_research_campaign",
]
