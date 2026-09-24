"""Audited control acquisition for Revision4 S1 Pilot-development tasks.

This executes only the preregistered, unmodified high-density control arms.
It does not select a memory asset, construct a treatment, or estimate an S1
effect. Every registered attempt and exception is retained.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from .research_epoch import verify_research_epoch
from .research_flow import audit_flow_run, verify_flow_audit, verify_staged_flow_project
from .research_inventory import verify_research_inventory
from .research_seed_pair import _execute


BINDING_SCHEMA = "tehm-r4-s1-control-execution-binding-v1"
TASK_SCHEMA = "tehm-r4-s1-controlled-action-task-spec-v1"
EVENT_SCHEMA = "tehm-r4-s1-control-event-v1"


class ResearchS1ControlError(ValueError):
    """The S1 control acquisition cannot be verified safely."""


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ResearchS1ControlError(f"cannot read JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ResearchS1ControlError(f"JSON object required: {path}")
    return value


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _absolute(value: object, label: str) -> Path:
    if type(value) is not str or not value or not Path(value).is_absolute():
        raise ResearchS1ControlError(f"{label} must be an absolute path")
    return Path(value).resolve()


def verify_s1_control_binding(binding: str | Path, *,
                              allow_executed: bool = False) -> dict[str, Any]:
    """Verify frozen task, two distinct lineages, source, project and budget."""
    binding_path = Path(binding).expanduser().resolve()
    payload = _load(binding_path)
    if payload.get("schema") != BINDING_SCHEMA:
        raise ResearchS1ControlError("S1 control binding schema mismatch")
    task_path = _absolute(payload.get("task_spec"), "task spec")
    if _file_sha(task_path) != payload.get("task_spec_sha256"):
        raise ResearchS1ControlError("task spec digest drifted")
    task = _load(task_path)
    if (task.get("schema") != TASK_SCHEMA or task.get("split") != "pilot_development"
            or task.get("task_kind") != "constructed_flow_feasibility_challenge"
            or task.get("campaign_id") != payload.get("campaign_id")):
        raise ResearchS1ControlError("S1 task definition differs from control binding")
    if (task.get("control", {}).get("config_overrides") !=
            {"CORE_UTILIZATION": "95"} or
            task.get("control", {}).get("action") != "NO_MEMORY_ACTION" or
            task.get("budget", {}).get("model_call_limit") != 0 or
            task.get("budget", {}).get("retry_limit") != 0 or
            task.get("budget", {}).get("control_flow_attempts_per_task") != 1):
        raise ResearchS1ControlError("S1 control action or budget drifted")
    if (payload.get("model_call_limit") != 0 or payload.get("retry_limit") != 0
            or payload.get("stage_timeout_seconds") != 900
            or payload.get("max_cpus") != 2):
        raise ResearchS1ControlError("control execution budget drifted")
    provenance_ref = task.get("source_provenance") or {}
    provenance_path = _absolute(provenance_ref.get("path"), "provenance")
    if _file_sha(provenance_path) != provenance_ref.get("sha256"):
        raise ResearchS1ControlError("source provenance drifted")
    provenance = _load(provenance_path)
    source_index = {row.get("design_id"): row
                    for row in provenance.get("entries", []) if isinstance(row, dict)}
    if len(source_index) != len(provenance.get("entries", [])):
        raise ResearchS1ControlError("provenance has duplicate design IDs")
    epoch = _absolute(payload.get("producer_epoch"), "producer epoch")
    if epoch != _absolute(payload.get("auditor_epoch"), "auditor epoch"):
        raise ResearchS1ControlError("control producer/auditor epoch mismatch")
    epoch_result = verify_research_epoch(epoch)
    source_epoch = task.get("source_epoch") or {}
    if (not epoch_result.get("valid") or not epoch_result.get("research_evaluation_ready")
            or str(epoch) != source_epoch.get("path")
            or epoch_result.get("epoch_digest") != source_epoch.get("epoch_digest")
            or epoch_result.get("memory_bundle_digest") !=
            source_epoch.get("memory_bundle_digest")):
        raise ResearchS1ControlError("S1 source epoch or M0 binding drifted")
    toolchain = _load(epoch / "bindings/toolchain-manifest.json")
    orfs = _absolute(payload.get("orfs_root"), "ORFS root")
    if (orfs != _absolute((toolchain.get("orfs") or {}).get("root"),
                          "frozen ORFS root") or
            payload.get("orfs_git_head") != (toolchain.get("orfs") or {}).get("git_head")):
        raise ResearchS1ControlError("ORFS checkout differs from frozen toolchain")
    flow_script = _absolute(payload.get("flow_script"), "flow script")
    if (not flow_script.is_file() or
            "sha256:" + _file_sha(flow_script) != payload.get("flow_script_sha256")):
        raise ResearchS1ControlError("flow runner bytes drifted")
    adapter = binding_path.parent / "control-adapter-spec-v1.json"
    if _file_sha(adapter) != payload.get("control_adapter_spec_sha256"):
        raise ResearchS1ControlError("control adapter preregistration drifted")
    inventory = _absolute(payload.get("control_inventory"), "control inventory")
    checked_inventory = verify_research_inventory(inventory)
    if (not checked_inventory.get("valid") or
            checked_inventory.get("inventory_digest") != payload.get("control_inventory_digest")
            or _file_sha(inventory / "adapter-spec.json") != _file_sha(adapter)):
        raise ResearchS1ControlError("control inventory drifted")
    tasks = task.get("tasks")
    projects = payload.get("control_projects")
    if (not isinstance(tasks, list) or not isinstance(projects, list)
            or len(tasks) != 2 or len(projects) != 2):
        raise ResearchS1ControlError("S1 control must retain two registered tasks")
    groups = {row.get("source_group") for row in tasks if isinstance(row, dict)}
    if (groups != {"github-owner:alexforencich", "github-owner:avakar"}
            or groups & {"github-owner:ultraembedded", "github-owner:freecores"}):
        raise ResearchS1ControlError("pilot task lineages overlap or drifted")
    normalized = []
    for item, row in zip(tasks, projects, strict=True):
        if not isinstance(item, dict) or not isinstance(row, dict):
            raise ResearchS1ControlError("task or control project is malformed")
        design = item.get("design_id")
        if design != row.get("design_id"):
            raise ResearchS1ControlError("control project order differs from task order")
        manifest_path = _absolute(item.get("source_manifest"), "source manifest")
        if _file_sha(manifest_path) != item.get("source_manifest_sha256"):
            raise ResearchS1ControlError("source manifest bytes drifted")
        source = source_index.get(design)
        if (not isinstance(source, dict) or source.get("exact_file_match") is not True
                or source.get("source_group_conservative") != item.get("source_group")):
            raise ResearchS1ControlError("task source lineage is unverified")
        local = _absolute(source.get("local_file"), "matched RTL")
        if _file_sha(local) != source.get("sha256_upstream_and_local"):
            raise ResearchS1ControlError("original RTL differs from upstream match")
        project = _absolute(row.get("project"), "staged control")
        checked = verify_staged_flow_project(project)
        receipt = _load(project / "stage-receipt.json")
        if (not checked.get("valid") or checked.get("design_id") != design
                or checked.get("receipt_digest") != row.get("stage_receipt_digest")
                or receipt.get("declared_overrides") != {"CORE_UTILIZATION": "95"}
                or receipt.get("inventory_digest") != checked_inventory["inventory_digest"]
                or receipt.get("logic_changes") != []
                or receipt.get("stub_generated") is not False):
            raise ResearchS1ControlError("staged control changed or has an unregistered edit")
        if not allow_executed and any((project / "backend").glob("RUN_*")):
            raise ResearchS1ControlError("control project already executed")
        variant = row.get("flow_variant")
        if type(variant) is not str or not re.fullmatch(r"[A-Za-z0-9_]+", variant):
            raise ResearchS1ControlError("flow variant is invalid")
        normalized.append({"design_id": design, "source_group": item["source_group"],
                           "project": str(project), "receipt_digest": checked["receipt_digest"],
                           "variant": variant})
    if len({row["variant"] for row in normalized}) != 2:
        raise ResearchS1ControlError("control flow variants must differ")
    return {"valid": True, "binding_digest": _digest(payload),
            "task_spec_sha256": payload["task_spec_sha256"],
            "epoch_digest": epoch_result["epoch_digest"], "epoch": str(epoch),
            "toolchain": toolchain, "flow_script": str(flow_script),
            "orfs_root": str(orfs), "tasks": normalized}


def _event(path: Path, prior: str | None, sequence: int, data: dict[str, Any]) -> str:
    record = {"schema": EVENT_SCHEMA, "sequence": sequence,
              "previous_digest": prior, **data}
    record["event_digest"] = _digest(record)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return record["event_digest"]


def run_s1_controls(*, binding: str | Path, output: str | Path) -> dict[str, Any]:
    """Run both frozen controls once, serially, preserving every terminal state."""
    destination = Path(output).expanduser().resolve()
    if destination.exists():
        raise ResearchS1ControlError("refusing to overwrite S1 control acquisition")
    checked = verify_s1_control_binding(binding)
    payload = _load(Path(binding).expanduser().resolve())
    if any(destination == Path(row["project"]) or
           destination.is_relative_to(Path(row["project"]))
           for row in checked["tasks"]):
        raise ResearchS1ControlError("control output cannot be inside a staged project")
    tools = checked["toolchain"]["tools"]
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("R2G_", "ORFS_"))
                   and key not in {"FROM_STAGE", "FLOW_VARIANT", "PLACE_FAST",
                                   "ROUTE_FAST", "ROUTE_FAST_SKIP_DRT",
                                   "ROUTE_FAST_DRT_ITERS", "NUM_CORES", "DESIGN_CONFIG",
                                   "PDK_ROOT", "OPENROAD_EXE", "YOSYS_EXE", "MAKEFLAGS",
                                   "MFLAGS", "MAKEOVERRIDES"}}
    environment.update({"ORFS_ROOT": checked["orfs_root"],
                        "PDK_ROOT": checked["toolchain"]["pdk"]["root"],
                        "OPENROAD_EXE": tools["openroad"]["path"],
                        "YOSYS_EXE": tools["yosys"]["path"],
                        "ORFS_TIMEOUT": str(payload["stage_timeout_seconds"]),
                        "ORFS_MAX_CPUS": str(payload["max_cpus"]),
                        "NUM_CORES": str(payload["max_cpus"]),
                        "ORFS_STAGES": "synth floorplan place cts route finish",
                        "FROM_STAGE": "", "MAKEFLAGS": "", "MFLAGS": "",
                        "MAKEOVERRIDES": "", "R2G_SYNTH_FRONTEND": "default"})
    destination.mkdir(parents=True)
    environment["R2G_JOURNAL_DB"] = str(destination / "journal.sqlite")
    ledger = destination / "attempt-events.jsonl"
    tail: str | None = None
    sequence = 0
    for task in checked["tasks"]:
        project = Path(task["project"])
        if (not verify_research_epoch(checked["epoch"]).get("valid")
                or not verify_staged_flow_project(project).get("valid")):
            raise ResearchS1ControlError("epoch or staged control drifted before execution")
        attempt = destination / task["design_id"]
        attempt.mkdir(parents=True)
        log = attempt / "runner.log"
        sequence += 1
        tail = _event(ledger, tail, sequence, {
            "event": "STARTED", "design_id": task["design_id"],
            "source_group": task["source_group"], "project": task["project"],
            "stage_receipt_digest": task["receipt_digest"],
            "flow_variant": task["variant"],
            "binding_digest": checked["binding_digest"], "started_unix": time.time(),
        })
        before = set((project / "backend").glob("RUN_*"))
        started = time.monotonic()
        run_dir = None
        audit = None
        error = None
        status = 125
        try:
            status, error = _execute(Path(checked["flow_script"]), project,
                                     task["variant"], environment, log,
                                     6 * (payload["stage_timeout_seconds"] + 30))
            new_runs = set((project / "backend").glob("RUN_*")) - before
            if len(new_runs) != 1:
                raise ResearchS1ControlError(
                    f"expected one raw control run, observed {len(new_runs)}")
            run_dir = next(iter(new_runs))
            audit = audit_flow_run(
                project=project, run_dir=run_dir,
                producer_epoch=checked["epoch"], auditor_epoch=checked["epoch"],
                output=attempt / "audit")
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            if not log.is_file():
                log.write_text(f"runner failed before opening log: {error}\n",
                               encoding="utf-8")
        sequence += 1
        tail = _event(ledger, tail, sequence, {
            "event": "TERMINAL", "design_id": task["design_id"],
            "flow_exit_code": status, "exception": error,
            "run_dir": str(run_dir) if run_dir is not None else None,
            "runner_log_sha256": "sha256:" + _file_sha(log),
            "audit_path": str(attempt / "audit") if audit is not None else None,
            "audit_digest": audit["audit_digest"] if audit is not None else None,
            "oracle_verdict": audit["oracle_verdict"] if audit is not None else "UNKNOWN",
            "oracle_reason": audit["oracle_reason"] if audit is not None else None,
            "failure_layer": audit["failure_layer"] if audit is not None else None,
            "actual_cost": audit["actual_cost"] if audit is not None else None,
            "elapsed_seconds": time.monotonic() - started,
        })
    result = verify_s1_controls(binding=binding, output=destination)
    result["event_tail_digest"] = tail
    (destination / "run-summary.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def verify_s1_controls(*, binding: str | Path, output: str | Path) -> dict[str, Any]:
    """Recompute the full denominator, event chain and available raw audits."""
    checked = verify_s1_control_binding(binding, allow_executed=True)
    root = Path(output).expanduser().resolve()
    try:
        lines = (root / "attempt-events.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ResearchS1ControlError("control attempt ledger is missing") from exc
    if len(lines) != 2 * len(checked["tasks"]):
        raise ResearchS1ControlError("control ledger lacks the registered denominator")
    tail = None
    outcomes = []
    for index, line in enumerate(lines):
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise ResearchS1ControlError("control event is malformed") from exc
        if not isinstance(record, dict):
            raise ResearchS1ControlError("control event must be an object")
        digest = record.pop("event_digest", None)
        if (record.get("schema") != EVENT_SCHEMA or
                record.get("sequence") != index + 1 or
                record.get("previous_digest") != tail or digest != _digest(record)):
            raise ResearchS1ControlError("control event chain is invalid")
        tail = digest
        task = checked["tasks"][index // 2]
        if (record.get("design_id") != task["design_id"] or
                record.get("event") != ("STARTED" if index % 2 == 0 else "TERMINAL")):
            raise ResearchS1ControlError("control event order changed")
        if index % 2 == 0:
            if (record.get("project") != task["project"] or
                    record.get("stage_receipt_digest") != task["receipt_digest"] or
                    record.get("flow_variant") != task["variant"] or
                    record.get("source_group") != task["source_group"] or
                    record.get("binding_digest") != checked["binding_digest"]):
                raise ResearchS1ControlError("control STARTED binding drifted")
            continue
        log = root / task["design_id"] / "runner.log"
        if not log.is_file() or record.get("runner_log_sha256") != "sha256:" + _file_sha(log):
            raise ResearchS1ControlError("control runner log drifted")
        audit_path = record.get("audit_path")
        if audit_path is not None:
            if _absolute(audit_path, "control audit") != root / task["design_id"] / "audit":
                raise ResearchS1ControlError("control audit path changed")
            audit = verify_flow_audit(audit_path)
            raw = _load(Path(audit_path) / "flow-audit.json")
            if (audit.get("audit_digest") != record.get("audit_digest") or
                    audit.get("design_id") != task["design_id"] or
                    audit.get("oracle_verdict") != record.get("oracle_verdict") or
                    audit.get("oracle_reason") != record.get("oracle_reason") or
                    audit.get("failure_layer") != record.get("failure_layer") or
                    audit.get("actual_cost") != record.get("actual_cost") or
                    raw.get("project_path") != task["project"] or
                    raw.get("stage_receipt_digest") != task["receipt_digest"] or
                    raw.get("run_dir") != record.get("run_dir") or
                    raw.get("producer_epoch_digest") != checked["epoch_digest"] or
                    raw.get("auditor_epoch_digest") != checked["epoch_digest"]):
                raise ResearchS1ControlError("control raw audit differs from ledger")
        elif record.get("oracle_verdict") != "UNKNOWN" or record.get("actual_cost") is not None:
            raise ResearchS1ControlError("unaudited control claimed a semantic verdict")
        outcomes.append({"design_id": task["design_id"],
                         "source_group": task["source_group"],
                         "oracle_verdict": record["oracle_verdict"],
                         "oracle_reason": record.get("oracle_reason"),
                         "failure_layer": record.get("failure_layer"),
                         "audit_digest": record.get("audit_digest"),
                         "audit_path": audit_path,
                         "exception": record.get("exception"),
                         "actual_cost": record.get("actual_cost")})
    return {"schema": "tehm-r4-s1-control-acquisition-summary-v1",
            "valid": all(row["audit_digest"] is not None for row in outcomes),
            "all_registered_terminal": True, "registered_tasks": len(outcomes),
            "binding_digest": checked["binding_digest"],
            "event_tail_digest": tail, "outcomes": outcomes,
            "memory_action_executed": False, "model_calls": 0,
            "claim_boundary": "Constructed S1 controls only; no selected memory action or paired effect."}
