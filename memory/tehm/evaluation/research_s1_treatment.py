"""Execute and independently audit one frozen TEHM-selected S1 action per task.

Controls, abstentions and UNKNOWN outcomes remain in the preregistered cohort.
This is a constructed action-effect comparison, never an Agent baseline.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from .research_epoch import verify_research_epoch
from .research_flow import audit_flow_run, verify_flow_audit, verify_staged_flow_project
from .research_s1_action import verify_s1_treatment_plan
from .research_s1_control import _digest, _file_sha, _load
from .research_seed_pair import _execute


EVENT_SCHEMA = "tehm-r4-s1-treatment-event-v1"
SUMMARY_SCHEMA = "tehm-r4-s1-controlled-action-summary-v1"


class ResearchS1TreatmentError(ValueError):
    """The selected treatment execution cannot be trusted."""


def _append_event(path: Path, previous: str | None, sequence: int,
                  data: dict[str, Any]) -> str:
    record = {"schema": EVENT_SCHEMA, "sequence": sequence,
              "previous_digest": previous, **data}
    record["event_digest"] = _digest(record)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return record["event_digest"]


def _execution_epoch(plan: dict[str, Any], epoch_path: Path) -> dict[str, Any]:
    checked = verify_research_epoch(epoch_path)
    previous = verify_research_epoch(plan["epoch_path"])
    if (not checked.get("valid") or not checked.get("research_evaluation_ready")
            or not previous.get("valid")
            or checked.get("memory_bundle_digest") != plan["memory_bundle_digest"]
            or checked.get("toolchain_manifest_digest") !=
            previous.get("toolchain_manifest_digest")):
        raise ResearchS1TreatmentError("execution epoch differs from frozen action plan")
    oracle = _load(epoch_path / "bindings/oracle-binding.json")
    indexed = {str(row.get("source_path")): row.get("sha256")
               for row in oracle.get("files", []) if isinstance(row, dict)}
    module = Path(__file__).resolve()
    if indexed.get(str(module)) != "sha256:" + _file_sha(module):
        raise ResearchS1TreatmentError("treatment runner differs from frozen oracle")
    return checked


def _environment(plan: dict[str, Any], epoch_path: Path) -> tuple[Path, dict[str, str], int]:
    preflight = _load(Path(plan["preflight_path"]) / "preflight.json")
    control_binding = _load(Path(preflight["control_binding_path"]))
    _execution_epoch(plan, epoch_path)
    toolchain = _load(epoch_path / "bindings/toolchain-manifest.json")
    if (control_binding.get("orfs_root") != (toolchain.get("orfs") or {}).get("root")
            or control_binding.get("orfs_git_head") !=
            (toolchain.get("orfs") or {}).get("git_head")):
        raise ResearchS1TreatmentError("treatment toolchain differs from controls")
    flow_script = Path(control_binding["flow_script"])
    if "sha256:" + _file_sha(flow_script) != control_binding["flow_script_sha256"]:
        raise ResearchS1TreatmentError("flow runner bytes differ from controls")
    if (control_binding.get("stage_timeout_seconds") != 900 or
            control_binding.get("max_cpus") != 2 or
            control_binding.get("model_call_limit") != 0 or
            control_binding.get("retry_limit") != 0):
        raise ResearchS1TreatmentError("treatment budget differs from controls")
    tools = toolchain["tools"]
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("R2G_", "ORFS_"))
                   and key not in {"FROM_STAGE", "FLOW_VARIANT", "PLACE_FAST",
                                   "ROUTE_FAST", "ROUTE_FAST_SKIP_DRT",
                                   "ROUTE_FAST_DRT_ITERS", "NUM_CORES", "DESIGN_CONFIG",
                                   "PDK_ROOT", "OPENROAD_EXE", "YOSYS_EXE", "MAKEFLAGS",
                                   "MFLAGS", "MAKEOVERRIDES"}}
    environment.update({"ORFS_ROOT": control_binding["orfs_root"],
                        "PDK_ROOT": toolchain["pdk"]["root"],
                        "OPENROAD_EXE": tools["openroad"]["path"],
                        "YOSYS_EXE": tools["yosys"]["path"],
                        "ORFS_TIMEOUT": "900", "ORFS_MAX_CPUS": "2",
                        "NUM_CORES": "2", "ORFS_STAGES":
                        "synth floorplan place cts route finish",
                        "FROM_STAGE": "", "MAKEFLAGS": "", "MFLAGS": "",
                        "MAKEOVERRIDES": "", "R2G_SYNTH_FRONTEND": "default"})
    return flow_script, environment, 6 * (900 + 30)


def run_s1_treatments(*, plan: str | Path, epoch: str | Path,
                      output: str | Path) -> dict[str, Any]:
    """Execute each selected treatment once; never synthesize a missing arm."""
    root = Path(output).expanduser().resolve()
    if root.exists():
        raise ResearchS1TreatmentError("refusing to overwrite S1 treatment execution")
    checked = verify_s1_treatment_plan(plan)
    plan_root = Path(plan).expanduser().resolve()
    saved = _load(plan_root / "treatment-plan.json")
    if not checked.get("valid") or checked["plan_digest"] != saved["plan_digest"]:
        raise ResearchS1TreatmentError("selected treatment plan is invalid")
    if any(root == Path(row["treatment_project"]) or root.is_relative_to(
            Path(row["treatment_project"])) for row in saved["selected_treatments"]):
        raise ResearchS1TreatmentError("execution output cannot be inside a treatment project")
    epoch_path = Path(epoch).expanduser().resolve()
    checked_epoch = _execution_epoch(saved, epoch_path)
    flow_script, environment, timeout = _environment(saved, epoch_path)
    root.mkdir(parents=True)
    execution_binding = {
        "schema": "tehm-r4-s1-treatment-execution-binding-v1",
        "plan_path": str(plan_root), "plan_digest": saved["plan_digest"],
        "epoch_path": str(epoch_path),
        "epoch_digest": checked_epoch["epoch_digest"],
        "memory_bundle_digest": checked_epoch["memory_bundle_digest"],
        "runner_sha256": "sha256:" + _file_sha(Path(__file__)),
    }
    execution_binding["binding_digest"] = _digest(execution_binding)
    (root / "execution-binding.json").write_text(
        json.dumps(execution_binding, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    environment["R2G_JOURNAL_DB"] = str(root / "journal.sqlite")
    ledger = root / "attempt-events.jsonl"
    tail = None
    sequence = 0
    for item in saved["selected_treatments"]:
        project = Path(item["treatment_project"])
        if (not verify_research_epoch(epoch_path).get("valid") or
                not verify_staged_flow_project(project).get("valid")):
            raise ResearchS1TreatmentError("epoch or staged treatment drifted")
        design = item["design_id"]
        variant = "r4_s1_selected_" + re.sub(r"[^A-Za-z0-9_]", "_", design) + "_v1"
        attempt = root / design
        attempt.mkdir(parents=True)
        log = attempt / "runner.log"
        sequence += 1
        tail = _append_event(ledger, tail, sequence, {
            "event": "STARTED", "design_id": design,
            "source_group": item["source_group"],
            "plan_digest": saved["plan_digest"],
            "execution_binding_digest": execution_binding["binding_digest"],
            "candidate_digest": item["candidate_digest"],
            "project": str(project),
            "stage_receipt_digest": item["treatment_stage_receipt_digest"],
            "flow_variant": variant, "started_unix": time.time(),
        })
        before = set((project / "backend").glob("RUN_*"))
        started = time.monotonic()
        status = 125
        error = None
        audit = None
        run_dir = None
        try:
            status, error = _execute(flow_script, project, variant,
                                     environment, log, timeout)
            new_runs = set((project / "backend").glob("RUN_*")) - before
            if len(new_runs) != 1:
                raise ResearchS1TreatmentError(
                    f"expected one raw treatment run, observed {len(new_runs)}")
            run_dir = next(iter(new_runs))
            audit = audit_flow_run(
                project=project, run_dir=run_dir,
                producer_epoch=epoch_path,
                auditor_epoch=epoch_path, output=attempt / "audit")
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            if not log.is_file():
                log.write_text(f"runner failed before opening log: {error}\n",
                               encoding="utf-8")
        sequence += 1
        tail = _append_event(ledger, tail, sequence, {
            "event": "TERMINAL", "design_id": design,
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
    result = verify_s1_treatments(plan=plan_root, output=root)
    result["event_tail_digest"] = tail
    (root / "run-summary.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def verify_s1_treatments(*, plan: str | Path, output: str | Path) -> dict[str, Any]:
    """Recompute raw treatment audits and the complete paired denominator."""
    plan_root = Path(plan).expanduser().resolve()
    checked_plan = verify_s1_treatment_plan(plan_root, allow_executed=True)
    saved = _load(plan_root / "treatment-plan.json")
    if not checked_plan.get("valid") or checked_plan["plan_digest"] != saved["plan_digest"]:
        raise ResearchS1TreatmentError("selected plan changed after execution")
    root = Path(output).expanduser().resolve()
    execution_binding = _load(root / "execution-binding.json")
    if (execution_binding.get("schema") != "tehm-r4-s1-treatment-execution-binding-v1"
            or execution_binding.get("binding_digest") != _digest({
                key: value for key, value in execution_binding.items()
                if key != "binding_digest"})
            or execution_binding.get("plan_path") != str(plan_root)
            or execution_binding.get("plan_digest") != saved["plan_digest"]):
        raise ResearchS1TreatmentError("treatment execution binding changed")
    epoch_path = Path(execution_binding["epoch_path"])
    checked_epoch = _execution_epoch(saved, epoch_path)
    if (execution_binding.get("epoch_digest") != checked_epoch["epoch_digest"]
            or execution_binding.get("memory_bundle_digest") !=
            checked_epoch["memory_bundle_digest"]
            or execution_binding.get("runner_sha256") !=
            "sha256:" + _file_sha(Path(__file__))):
        raise ResearchS1TreatmentError("treatment runner or execution epoch drifted")
    preflight = _load(Path(saved["preflight_path"]) / "preflight.json")
    control_index = {row["design_id"]: row for row in preflight["rows"]}
    try:
        lines = (root / "attempt-events.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ResearchS1TreatmentError("treatment event ledger is missing") from exc
    selected = saved["selected_treatments"]
    if len(lines) != 2 * len(selected):
        raise ResearchS1TreatmentError("treatment ledger lacks selected denominator")
    tail = None
    outcomes = []
    for index, line in enumerate(lines):
        try:
            event = json.loads(line)
        except ValueError as exc:
            raise ResearchS1TreatmentError("treatment event JSON is malformed") from exc
        if not isinstance(event, dict):
            raise ResearchS1TreatmentError("treatment event must be an object")
        digest = event.pop("event_digest", None)
        if (event.get("schema") != EVENT_SCHEMA or
                event.get("sequence") != index + 1 or
                event.get("previous_digest") != tail or
                digest != _digest(event)):
            raise ResearchS1TreatmentError("treatment event hash chain is invalid")
        tail = digest
        item = selected[index // 2]
        if (event.get("design_id") != item["design_id"] or
                event.get("event") != ("STARTED" if index % 2 == 0 else "TERMINAL")):
            raise ResearchS1TreatmentError("treatment event identity or order changed")
        if index % 2 == 0:
            if (event.get("plan_digest") != saved["plan_digest"] or
                    event.get("execution_binding_digest") !=
                    execution_binding["binding_digest"] or
                    event.get("candidate_digest") != item["candidate_digest"] or
                    event.get("source_group") != item["source_group"] or
                    event.get("project") != item["treatment_project"] or
                    event.get("stage_receipt_digest") !=
                    item["treatment_stage_receipt_digest"]):
                raise ResearchS1TreatmentError("treatment STARTED binding changed")
            continue
        log = root / item["design_id"] / "runner.log"
        if not log.is_file() or event.get("runner_log_sha256") != "sha256:" + _file_sha(log):
            raise ResearchS1TreatmentError("treatment runner log changed")
        audit_path = event.get("audit_path")
        if audit_path is not None:
            if Path(audit_path).resolve() != root / item["design_id"] / "audit":
                raise ResearchS1TreatmentError("treatment audit path changed")
            audit = verify_flow_audit(audit_path)
            raw = _load(Path(audit_path) / "flow-audit.json")
            if (audit.get("audit_digest") != event.get("audit_digest") or
                    audit.get("design_id") != item["design_id"] or
                    audit.get("oracle_verdict") != event.get("oracle_verdict") or
                    audit.get("oracle_reason") != event.get("oracle_reason") or
                    audit.get("failure_layer") != event.get("failure_layer") or
                    audit.get("actual_cost") != event.get("actual_cost") or
                    raw.get("project_path") != item["treatment_project"] or
                    raw.get("stage_receipt_digest") !=
                    item["treatment_stage_receipt_digest"] or
                    raw.get("run_dir") != event.get("run_dir") or
                    raw.get("producer_epoch_digest") != checked_epoch["epoch_digest"] or
                    raw.get("auditor_epoch_digest") != checked_epoch["epoch_digest"]):
                raise ResearchS1TreatmentError("treatment raw audit differs from ledger")
        elif event.get("oracle_verdict") != "UNKNOWN" or event.get("actual_cost") is not None:
            raise ResearchS1TreatmentError("unaudited treatment claimed a semantic verdict")
        control = control_index.get(item["design_id"])
        if (not isinstance(control, dict) or control.get("target_failure") is not True
                or control.get("control_audit_digest") != item["control_audit_digest"]
                or control.get("control_verdict") != "FAIL"):
            raise ResearchS1TreatmentError("paired control audit is not a verified target failure")
        outcomes.append({"design_id": item["design_id"],
                         "source_group": item["source_group"],
                         "candidate_digest": item["candidate_digest"],
                         "control_audit_digest": item["control_audit_digest"],
                         "control_verdict": control["control_verdict"],
                         "treatment_verdict": event["oracle_verdict"],
                         "treatment_reason": event.get("oracle_reason"),
                         "treatment_failure_layer": event.get("failure_layer"),
                         "treatment_audit_digest": event.get("audit_digest"),
                         "treatment_audit_path": audit_path,
                         "exception": event.get("exception"),
                         "actual_cost": event.get("actual_cost"),
                         "effect": ("BENEFIT" if event["oracle_verdict"] == "PASS"
                                    else "UNKNOWN" if event["oracle_verdict"] == "UNKNOWN"
                                    else "NO_SUCCESS"),
                         })
    rows = outcomes + [{"design_id": row["design_id"], "effect": "NO_REUSE",
                        "treatment_verdict": None} for row in saved["no_reuse"]]
    if len(rows) != saved["registered_tasks"]:
        raise ResearchS1TreatmentError("paired denominator excludes a registered task")
    return {"schema": SUMMARY_SCHEMA,
            "valid": all(row["treatment_audit_digest"] is not None for row in outcomes),
            "all_registered_terminal": True,
            "plan_digest": saved["plan_digest"],
            "execution_binding_digest": execution_binding["binding_digest"],
            "execution_epoch_digest": checked_epoch["epoch_digest"],
            "event_tail_digest": tail,
            "registered_tasks": saved["registered_tasks"],
            "selected_treatments": len(selected),
            "outcomes": outcomes, "no_reuse": saved["no_reuse"],
            "benefit_count": sum(row["effect"] == "BENEFIT" for row in outcomes),
            "unknown_count": sum(row["effect"] == "UNKNOWN" for row in outcomes),
            "model_calls": 0, "memory_update": "none",
            "claim_boundary": "Constructed two-arm S1 action effect only; no natural prevalence, strict signoff, or Agent comparison."}
