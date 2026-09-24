"""Run a bounded, identical deterministic controller for three S2 policies.

The shared S1 controls supply verified task feedback, not an Agent outcome.
Each policy executes its own isolated candidate sequence: memory advisor first
when distinct, then the same cold-start fallback.  PASS and UNKNOWN stop the
sequence; a known FAIL may advance to the next candidate.  No model calls,
retries, online learning, or production authority are permitted.
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
from .research_s1_control import _digest, _file_sha, _load
from .research_s2_action import verify_s2_actions
from .research_s2_proposal import POLICIES
from .research_seed_pair import _execute


EVENT_SCHEMA = "tehm-r4-s2-agent-event-v1"
SUMMARY_SCHEMA = "tehm-r4-s2-deterministic-agent-summary-v1"
_VERDICTS = {"PASS", "FAIL", "UNKNOWN"}


class ResearchS2RunError(ValueError):
    """The three-policy S2 development execution cannot be trusted."""


def _event(path: Path, previous: str | None, sequence: int,
           payload: dict[str, Any]) -> str:
    record = {"schema": EVENT_SCHEMA, "sequence": sequence,
              "previous_digest": previous, **payload}
    record["event_digest"] = _digest(record)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return record["event_digest"]


def _epoch(plan: dict[str, Any], epoch_path: Path) -> dict[str, Any]:
    checked = verify_research_epoch(epoch_path)
    original = verify_research_epoch(plan["epoch_path"])
    if (not checked.get("valid") or not checked.get("research_evaluation_ready")
            or not original.get("valid")
            or checked.get("memory_bundle_digest") != plan["memory_bundle_digest"]
            or checked.get("toolchain_manifest_digest") !=
            original.get("toolchain_manifest_digest")):
        raise ResearchS2RunError("S2 execution epoch differs from staged plan")
    binding = _load(epoch_path / "bindings/oracle-binding.json")
    indexed = {str(row.get("source_path")): row.get("sha256")
               for row in binding.get("files", []) if isinstance(row, dict)}
    required = (Path(__file__).resolve(),
                Path(__file__).with_name("research_s2_action.py").resolve(),
                Path(__file__).with_name("research_s2_proposal.py").resolve(),
                Path(__file__).with_name("research_flow.py").resolve())
    if any(indexed.get(str(path)) != "sha256:" + _file_sha(path)
           for path in required):
        raise ResearchS2RunError("S2 runner code differs from frozen oracle")
    epoch_data = _load(epoch_path / "research-epoch.json")
    if (epoch_data["controller"]["sha256"] != plan["controller_sha256"]
            or epoch_data["budget"]["sha256"] != plan["budget_sha256"]):
        raise ResearchS2RunError("S2 controller or budget differs from plan")
    return checked


def _environment(plan: dict[str, Any], epoch_path: Path
                 ) -> tuple[Path, dict[str, str], int, dict[str, Any]]:
    proposals = _load(Path(plan["proposals_path"]) / "proposals.json")
    preflight = _load(Path(proposals["preflight_path"]) / "preflight.json")
    binding = _load(Path(preflight["control_binding_path"]))
    toolchain = _load(epoch_path / "bindings/toolchain-manifest.json")
    budget = _load(epoch_path / "bindings/budget.json")
    if (binding["orfs_root"] != toolchain["orfs"]["root"]
            or binding["orfs_git_head"] != toolchain["orfs"]["git_head"]
            or binding["stage_timeout_seconds"] != 900
            or binding["max_cpus"] != 2
            or binding["retry_limit"] != 0
            or binding["model_call_limit"] != 0
            or budget["candidate_limit"] != 3
            or budget["eda_call_limit"] < 2
            or budget["model_call_limit"] != 0
            or budget["model_token_limit"] != 0
            or budget["wallclock_limit_seconds"] <= 0):
        raise ResearchS2RunError("S2 toolchain or actual budget differs from controls")
    flow_script = Path(binding["flow_script"])
    if "sha256:" + _file_sha(flow_script) != binding["flow_script_sha256"]:
        raise ResearchS2RunError("S2 flow runner bytes differ from controls")
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("R2G_", "ORFS_"))
                   and key not in {"FROM_STAGE", "FLOW_VARIANT", "PLACE_FAST",
                                   "ROUTE_FAST", "ROUTE_FAST_SKIP_DRT",
                                   "ROUTE_FAST_DRT_ITERS", "NUM_CORES", "DESIGN_CONFIG",
                                   "PDK_ROOT", "OPENROAD_EXE", "YOSYS_EXE", "MAKEFLAGS",
                                   "MFLAGS", "MAKEOVERRIDES"}}
    tools = toolchain["tools"]
    environment.update({"ORFS_ROOT": binding["orfs_root"],
                        "PDK_ROOT": toolchain["pdk"]["root"],
                        "OPENROAD_EXE": tools["openroad"]["path"],
                        "YOSYS_EXE": tools["yosys"]["path"],
                        "ORFS_TIMEOUT": "900", "ORFS_MAX_CPUS": "2",
                        "NUM_CORES": "2", "ORFS_STAGES":
                        "synth floorplan place cts route finish",
                        "FROM_STAGE": "", "MAKEFLAGS": "", "MFLAGS": "",
                        "MAKEOVERRIDES": "", "R2G_SYNTH_FRONTEND": "default",
                        "R2G_MEMORY_BACKEND": "none"})
    return flow_script, environment, 6 * (900 + 30), budget


def _groups(plan: dict[str, Any]) -> list[tuple[str, str, list[dict[str, Any]]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in plan["staged_candidates"]:
        grouped.setdefault((item["design_id"], item["policy"]), []).append(item)
    ordered = []
    for design in dict.fromkeys(row["design_id"] for row in plan["staged_candidates"]):
        for policy in POLICIES:
            candidates = grouped.get((design, policy))
            if (not candidates or len(candidates) > plan["candidate_limit"]
                    or [row["candidate_index"] for row in candidates] !=
                    list(range(1, len(candidates) + 1))
                    or candidates[-1]["candidate_source"] != "cold_start"):
                raise ResearchS2RunError("S2 policy candidate pool is incomplete")
            ordered.append((design, policy, candidates))
    if len(ordered) != plan["registered_policy_tasks"]:
        raise ResearchS2RunError("S2 policy-task denominator is incomplete")
    return ordered


def run_s2_pilot(*, plan: str | Path, epoch: str | Path,
                 output: str | Path) -> dict[str, Any]:
    """Execute every policy task once with hard candidate, EDA and time limits."""
    root = Path(output).expanduser().resolve()
    if root.exists():
        raise ResearchS2RunError("refusing to overwrite S2 execution")
    plan_root = Path(plan).expanduser().resolve()
    checked_plan = verify_s2_actions(plan_root)
    saved = _load(plan_root / "stage-plan.json")
    if checked_plan.get("plan_digest") != saved["plan_digest"]:
        raise ResearchS2RunError("S2 stage plan verification changed")
    if any(root == Path(row["project"]) or root.is_relative_to(Path(row["project"]))
           for row in saved["staged_candidates"]):
        raise ResearchS2RunError("execution output cannot be inside candidate project")
    epoch_path = Path(epoch).expanduser().resolve()
    checked_epoch = _epoch(saved, epoch_path)
    flow_script, environment, timeout, budget = _environment(saved, epoch_path)
    root.mkdir(parents=True)
    execution = {
        "schema": "tehm-r4-s2-execution-binding-v1",
        "plan_path": str(plan_root), "plan_digest": saved["plan_digest"],
        "epoch_path": str(epoch_path), "epoch_digest": checked_epoch["epoch_digest"],
        "memory_bundle_digest": checked_epoch["memory_bundle_digest"],
        "runner_sha256": "sha256:" + _file_sha(Path(__file__)),
        "candidate_limit": 3, "eda_call_limit": budget["eda_call_limit"],
        "wallclock_limit_seconds": budget["wallclock_limit_seconds"],
        "model_call_limit": 0, "retry_limit": 0,
    }
    execution["binding_digest"] = _digest(execution)
    (root / "execution-binding.json").write_text(
        json.dumps(execution, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    ledger = root / "attempt-events.jsonl"
    sequence, tail = 0, None

    def append(payload: dict[str, Any]) -> None:
        nonlocal sequence, tail
        sequence += 1
        tail = _event(ledger, tail, sequence, payload)

    for design, policy, candidates in _groups(saved):
        policy_start = time.monotonic()
        append({"event": "POLICY_STARTED", "design_id": design, "policy": policy,
                "plan_digest": saved["plan_digest"],
                "execution_binding_digest": execution["binding_digest"],
                "control_audit_digest": candidates[0]["control_audit_digest"],
                "candidate_digests": [row["candidate_digest"] for row in candidates]})
        verdict = "UNKNOWN"
        executed = 0
        stop_reason = "candidates_exhausted"
        for item in candidates:
            remaining = budget["wallclock_limit_seconds"] - (time.monotonic() - policy_start)
            if (remaining <= 0 or executed >= budget["candidate_limit"]
                    or executed >= budget["eda_call_limit"]):
                stop_reason = "budget_exhausted"
                break
            project = Path(item["project"])
            if not verify_staged_flow_project(project).get("valid"):
                raise ResearchS2RunError("S2 candidate project drifted before execution")
            attempt = root / design / policy / f"candidate-{item['candidate_index']}"
            attempt.mkdir(parents=True)
            log = attempt / "runner.log"
            variant = ("r4_s2_" + re.sub(r"[^A-Za-z0-9_]", "_",
                       design + "_" + policy) + f"_c{item['candidate_index']}_v1")
            append({"event": "ATTEMPT_STARTED", "design_id": design,
                    "policy": policy, "candidate_index": item["candidate_index"],
                    "candidate_digest": item["candidate_digest"],
                    "candidate_source": item["candidate_source"],
                    "project": str(project),
                    "stage_receipt_digest": item["candidate_stage_receipt_digest"],
                    "flow_variant": variant})
            before = set((project / "backend").glob("RUN_*"))
            started = time.monotonic()
            status, error, audit, run_dir = 125, None, None, None
            try:
                attempt_environment = dict(environment)
                attempt_environment["R2G_JOURNAL_DB"] = str(
                    attempt / "journal.sqlite")
                status, error = _execute(
                    flow_script, project, variant, attempt_environment, log,
                    min(timeout, max(1, int(remaining))))
                runs = set((project / "backend").glob("RUN_*")) - before
                if len(runs) != 1:
                    raise ResearchS2RunError(
                        f"expected one raw S2 run, observed {len(runs)}")
                run_dir = next(iter(runs))
                audit = audit_flow_run(
                    project=project, run_dir=run_dir,
                    producer_epoch=epoch_path, auditor_epoch=epoch_path,
                    output=attempt / "audit")
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                if not log.is_file():
                    log.write_text(f"runner failed before opening log: {error}\n",
                                   encoding="utf-8")
            verdict = audit["oracle_verdict"] if audit is not None else "UNKNOWN"
            executed += 1
            append({"event": "ATTEMPT_TERMINAL", "design_id": design,
                    "policy": policy, "candidate_index": item["candidate_index"],
                    "flow_exit_code": status, "exception": error,
                    "run_dir": str(run_dir) if run_dir is not None else None,
                    "runner_log_sha256": "sha256:" + _file_sha(log),
                    "audit_path": str(attempt / "audit") if audit is not None else None,
                    "audit_digest": audit["audit_digest"] if audit is not None else None,
                    "oracle_verdict": verdict,
                    "oracle_reason": audit["oracle_reason"] if audit is not None else None,
                    "failure_layer": audit["failure_layer"] if audit is not None else None,
                    "actual_cost": audit["actual_cost"] if audit is not None else None,
                    "elapsed_seconds": time.monotonic() - started})
            if verdict in {"PASS", "UNKNOWN"}:
                stop_reason = "success" if verdict == "PASS" else "unknown"
                break
        append({"event": "POLICY_TERMINAL", "design_id": design, "policy": policy,
                "oracle_verdict": verdict, "executed_candidates": executed,
                "stop_reason": stop_reason,
                "elapsed_seconds": time.monotonic() - policy_start})
    result = verify_s2_pilot(plan=plan_root, output=root)
    result["event_tail_digest"] = tail
    (root / "run-summary.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def _events(path: Path) -> tuple[list[dict[str, Any]], str | None]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ResearchS2RunError("S2 event ledger is missing") from exc
    events, tail = [], None
    for index, line in enumerate(lines, start=1):
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise ResearchS2RunError("S2 event JSON is invalid") from exc
        if not isinstance(record, dict):
            raise ResearchS2RunError("S2 event must be an object")
        digest = record.pop("event_digest", None)
        if (record.get("schema") != EVENT_SCHEMA or
                record.get("sequence") != index or
                record.get("previous_digest") != tail or
                digest != _digest(record)):
            raise ResearchS2RunError("S2 event hash chain is invalid")
        record["event_digest"] = digest
        events.append(record)
        tail = digest
    return events, tail


def verify_s2_pilot(*, plan: str | Path, output: str | Path) -> dict[str, Any]:
    """Replay the complete six-task policy denominator and every raw audit."""
    plan_root = Path(plan).expanduser().resolve()
    checked = verify_s2_actions(plan_root, allow_executed=True)
    saved = _load(plan_root / "stage-plan.json")
    if checked.get("plan_digest") != saved["plan_digest"]:
        raise ResearchS2RunError("S2 stage plan changed after execution")
    root = Path(output).expanduser().resolve()
    execution = _load(root / "execution-binding.json")
    if (execution.get("schema") != "tehm-r4-s2-execution-binding-v1"
            or execution.get("binding_digest") != _digest({
                key: value for key, value in execution.items()
                if key != "binding_digest"})
            or execution.get("plan_path") != str(plan_root)
            or execution.get("plan_digest") != saved["plan_digest"]
            or execution.get("runner_sha256") != "sha256:" + _file_sha(Path(__file__))
            or execution.get("candidate_limit") != 3
            or execution.get("model_call_limit") != 0
            or execution.get("retry_limit") != 0):
        raise ResearchS2RunError("S2 execution binding changed")
    epoch_path = Path(execution["epoch_path"])
    frozen = _epoch(saved, epoch_path)
    if (execution["epoch_digest"] != frozen["epoch_digest"]
            or execution["memory_bundle_digest"] != frozen["memory_bundle_digest"]):
        raise ResearchS2RunError("S2 execution epoch drifted")
    _, _, _, budget = _environment(saved, epoch_path)
    if (execution["eda_call_limit"] != budget["eda_call_limit"]
            or execution["wallclock_limit_seconds"] !=
            budget["wallclock_limit_seconds"]):
        raise ResearchS2RunError("S2 execution budget drifted")
    events, tail = _events(root / "attempt-events.jsonl")
    position, outcomes = 0, []

    def take(kind: str, design: str, policy: str) -> dict[str, Any]:
        nonlocal position
        if position >= len(events):
            raise ResearchS2RunError("S2 event ledger is incomplete")
        value = events[position]
        position += 1
        if (value.get("event") != kind or value.get("design_id") != design
                or value.get("policy") != policy):
            raise ResearchS2RunError("S2 event order or identity changed")
        return value

    for design, policy, candidates in _groups(saved):
        started = take("POLICY_STARTED", design, policy)
        if (started.get("plan_digest") != saved["plan_digest"]
                or started.get("execution_binding_digest") !=
                execution["binding_digest"]
                or started.get("control_audit_digest") !=
                candidates[0]["control_audit_digest"]
                or started.get("candidate_digests") !=
                [row["candidate_digest"] for row in candidates]):
            raise ResearchS2RunError("S2 policy start binding changed")
        attempts = []
        for item in candidates:
            if position >= len(events) or events[position].get("event") != "ATTEMPT_STARTED":
                break
            event = take("ATTEMPT_STARTED", design, policy)
            if (event.get("candidate_index") != item["candidate_index"]
                    or event.get("candidate_digest") != item["candidate_digest"]
                    or event.get("candidate_source") != item["candidate_source"]
                    or event.get("project") != item["project"]
                    or event.get("stage_receipt_digest") !=
                    item["candidate_stage_receipt_digest"]):
                raise ResearchS2RunError("S2 attempted candidate differs from plan")
            terminal = take("ATTEMPT_TERMINAL", design, policy)
            if terminal.get("candidate_index") != item["candidate_index"]:
                raise ResearchS2RunError("S2 attempt terminal candidate changed")
            attempt_root = root / design / policy / f"candidate-{item['candidate_index']}"
            log = attempt_root / "runner.log"
            if (not log.is_file() or terminal.get("runner_log_sha256") !=
                    "sha256:" + _file_sha(log)):
                raise ResearchS2RunError("S2 raw runner log changed")
            audit_path = terminal.get("audit_path")
            if audit_path is not None:
                if Path(audit_path).resolve() != attempt_root / "audit":
                    raise ResearchS2RunError("S2 audit path changed")
                audit = verify_flow_audit(audit_path)
                raw = _load(Path(audit_path) / "flow-audit.json")
                if (not audit.get("valid")
                        or audit["audit_digest"] != terminal.get("audit_digest")
                        or audit["oracle_verdict"] != terminal.get("oracle_verdict")
                        or audit["oracle_reason"] != terminal.get("oracle_reason")
                        or audit["failure_layer"] != terminal.get("failure_layer")
                        or audit["actual_cost"] != terminal.get("actual_cost")
                        or raw["project_path"] != item["project"]
                        or raw["stage_receipt_digest"] !=
                        item["candidate_stage_receipt_digest"]
                        or raw["run_dir"] != terminal.get("run_dir")
                        or raw["producer_epoch_digest"] != frozen["epoch_digest"]
                        or raw["auditor_epoch_digest"] != frozen["epoch_digest"]
                        or raw["memory_update"] != "none"
                        or raw["source_mutation"] != "none"
                        or raw["production_authority"] is not False
                        or audit["actual_cost"]["model_calls"] != 0
                        or len(list((Path(item["project"]) / "backend").glob(
                            "RUN_*"))) != 1):
                    raise ResearchS2RunError("S2 raw audit differs from ledger")
            elif (terminal.get("oracle_verdict") != "UNKNOWN"
                  or terminal.get("actual_cost") is not None
                  or terminal.get("audit_digest") is not None):
                raise ResearchS2RunError("unaudited S2 attempt claimed a verdict")
            if terminal.get("oracle_verdict") not in _VERDICTS:
                raise ResearchS2RunError("S2 attempt verdict is invalid")
            attempts.append({
                "candidate_index": item["candidate_index"],
                "candidate_source": item["candidate_source"],
                "candidate_digest": item["candidate_digest"],
                "oracle_verdict": terminal["oracle_verdict"],
                "oracle_reason": terminal.get("oracle_reason"),
                "failure_layer": terminal.get("failure_layer"),
                "audit_digest": terminal.get("audit_digest"),
                "audit_path": audit_path,
                "actual_cost": terminal.get("actual_cost"),
                "exception": terminal.get("exception"),
            })
            if terminal["oracle_verdict"] in {"PASS", "UNKNOWN"}:
                break
        finished = take("POLICY_TERMINAL", design, policy)
        verdict = attempts[-1]["oracle_verdict"] if attempts else "UNKNOWN"
        expected_stop = (
            "success" if verdict == "PASS" else
            "unknown" if verdict == "UNKNOWN" and attempts else
            "candidates_exhausted" if len(attempts) == len(candidates) else
            "budget_exhausted")
        if (finished.get("oracle_verdict") != verdict
                or finished.get("executed_candidates") != len(attempts)
                or finished.get("stop_reason") != expected_stop
                or len(attempts) > execution["candidate_limit"]
                or len(attempts) > execution["eda_call_limit"]
                or (expected_stop == "budget_exhausted"
                    and len(attempts) < min(execution["candidate_limit"],
                                            execution["eda_call_limit"])
                    and finished.get("elapsed_seconds", 0) <
                    execution["wallclock_limit_seconds"])):
            raise ResearchS2RunError("S2 policy terminal violates controller or budget")
        outcomes.append({
            "design_id": design, "policy": policy,
            "control_audit_digest": candidates[0]["control_audit_digest"],
            "oracle_verdict": verdict, "attempts": attempts,
            "executed_memory_candidate": any(
                row["candidate_source"] in {"legacy_memory", "tehm"}
                for row in attempts),
        })
    if position != len(events) or len(outcomes) != saved["registered_policy_tasks"]:
        raise ResearchS2RunError("S2 event ledger has extra or missing policy tasks")
    success = {policy: sum(row["policy"] == policy and row["oracle_verdict"] == "PASS"
                           for row in outcomes) for policy in POLICIES}
    memory_use = {policy: sum(row["policy"] == policy and
                              row["executed_memory_candidate"] for row in outcomes)
                  for policy in POLICIES}
    costs = {policy: {
        "flow_driver_calls": sum(len(row["attempts"]) for row in outcomes
                                 if row["policy"] == policy),
        "eda_stage_calls": sum(
            (attempt["actual_cost"] or {}).get("eda_stage_calls", 0)
            for row in outcomes if row["policy"] == policy
            for attempt in row["attempts"]),
        "stage_wallclock_seconds": sum(
            (attempt["actual_cost"] or {}).get("stage_wallclock_seconds", 0)
            for row in outcomes if row["policy"] == policy
            for attempt in row["attempts"]),
        "model_calls": 0, "model_tokens": 0,
    } for policy in POLICIES}
    return {
        "schema": SUMMARY_SCHEMA, "valid": True,
        "all_registered_terminal": True,
        "plan_digest": saved["plan_digest"],
        "execution_binding_digest": execution["binding_digest"],
        "execution_epoch_digest": frozen["epoch_digest"],
        "event_tail_digest": tail,
        "registered_designs": saved["registered_designs"],
        "registered_policy_tasks": saved["registered_policy_tasks"],
        "outcomes": outcomes, "success_by_policy": success,
        "memory_use_by_policy": memory_use, "actual_cost_by_policy": costs,
        "model_calls": 0, "memory_update": "none",
        "controller_type": "deterministic_skill",
        "claim_boundary": "Pilot-development deterministic controller comparison with historical Legacy exact-design exposure. Shared S1 controls are feedback, not No Memory Agent outcomes. No LLM-agent, strict signoff, natural prevalence, or final-heldout claim.",
    }
