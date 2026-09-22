"""Frozen, two-arm ORFS seed acquisition for the Revision4 research Pilot.

This is *training acquisition*, not TEHM selection or an S1 effect estimate.
The only supported intervention is the preregistered constructed density
challenge. Every arm is executed once and independently audited, including
failed and inconclusive arms.
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping

from .research_epoch import verify_research_epoch
from .research_flow import (
    audit_flow_run, verify_flow_audit, verify_staged_flow_project,
)


SCHEMA = "tehm-r4-seed-pair-spec-v1"
EVENT_SCHEMA = "tehm-r4-seed-pair-event-v1"
SUMMARY_SCHEMA = "tehm-r4-seed-pair-summary-v1"
ARMS = ("control", "treatment")
EXPECTED_OVERRIDES = {"control": {"CORE_UTILIZATION": "95"},
                      "treatment": {"CORE_UTILIZATION": "40"}}


class ResearchSeedPairError(ValueError):
    """A seed-pair input, execution or receipt is not trustworthy."""


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ResearchSeedPairError(f"cannot read JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ResearchSeedPairError(f"JSON object required: {path}")
    return value


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _path(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise ResearchSeedPairError(f"{label} must be an absolute path")
    return Path(value).resolve()


def _project_identity(project: Path, expected_design: str,
                      expected_digest: str, arm: str) -> dict[str, Any]:
    checked = verify_staged_flow_project(project)
    receipt = _json(project / "stage-receipt.json")
    if (not checked.get("valid") or checked.get("design_id") != expected_design
            or checked.get("receipt_digest") != expected_digest
            or receipt.get("flow_executed") is not False):
        raise ResearchSeedPairError(f"staged {arm} project binding mismatch")
    if receipt.get("declared_overrides") != EXPECTED_OVERRIDES[arm]:
        raise ResearchSeedPairError(f"staged {arm} has an unregistered action")
    if receipt.get("logic_changes") != [] or receipt.get("stub_generated") is not False:
        raise ResearchSeedPairError("seed acquisition forbids RTL edits and stubs")
    return receipt


def verify_seed_pair_spec(spec: str | Path, *, allow_executed: bool = False) -> dict[str, Any]:
    """Validate all four frozen inputs and their disjoint source authority."""
    spec_path = Path(spec).expanduser().resolve()
    payload = _json(spec_path)
    if payload.get("schema") != SCHEMA or payload.get("profile") != "constructed_flow_feasibility_training":
        raise ResearchSeedPairError("seed-pair schema or profile mismatch")
    if payload.get("model_call_limit") != 0 or payload.get("retry_limit") != 0:
        raise ResearchSeedPairError("seed acquisition requires zero model calls and retries")
    if payload.get("stage_timeout_seconds") != 900 or payload.get("max_cpus") != 2:
        raise ResearchSeedPairError("seed resource budget differs from preregistration")
    epoch = _path(payload.get("epoch"), "epoch")
    checked_epoch = verify_research_epoch(epoch)
    if not checked_epoch.get("valid") or not checked_epoch.get("research_evaluation_ready"):
        raise ResearchSeedPairError("seed epoch is not evaluation-ready")
    toolchain = _json(epoch / "bindings/toolchain-manifest.json")
    orfs = _path((toolchain.get("orfs") or {}).get("root"), "ORFS root")
    flow_script = _path(payload.get("flow_script"), "flow script")
    if (not flow_script.is_file() or _file_digest(flow_script) != payload.get("flow_script_sha256")):
        raise ResearchSeedPairError("flow script drifted")
    if (toolchain.get("orfs") or {}).get("git_head") != payload.get("orfs_git_head"):
        raise ResearchSeedPairError("ORFS Git pin mismatch")
    if orfs != _path(payload.get("orfs_root"), "ORFS root"):
        raise ResearchSeedPairError("ORFS root differs from frozen toolchain")
    provenance_path = _path(payload.get("provenance_file"), "provenance file")
    if _file_digest(provenance_path) != payload.get("provenance_sha256"):
        raise ResearchSeedPairError("seed upstream provenance drifted")
    provenance = _json(provenance_path)
    provenance_entries = provenance.get("entries")
    if not isinstance(provenance_entries, list) or len(provenance_entries) != 2:
        raise ResearchSeedPairError("seed provenance needs exactly two source entries")
    provenance_by_design = {row.get("design_id"): row for row in provenance_entries
                            if isinstance(row, Mapping)}
    if len(provenance_by_design) != 2:
        raise ResearchSeedPairError("seed provenance has duplicate or invalid design IDs")
    development_groups = payload.get("pilot_development_source_groups")
    if (not isinstance(development_groups, list) or not development_groups
            or any(not isinstance(group, str) or not group for group in development_groups)):
        raise ResearchSeedPairError("Pilot development source groups are not declared")
    cases = payload.get("cases")
    if not isinstance(cases, list) or len(cases) != 2:
        raise ResearchSeedPairError("exactly two seed designs are required")
    groups: set[str] = set()
    designs: set[str] = set()
    projects: set[Path] = set()
    seen_source_paths: set[Path] = set()
    seen_source_hashes: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, Mapping):
            raise ResearchSeedPairError("seed case must be an object")
        design = case.get("design_id")
        group = case.get("source_group")
        if not isinstance(design, str) or not design or design in designs:
            raise ResearchSeedPairError("seed design IDs must be unique")
        if not isinstance(group, str) or not group or group in groups:
            raise ResearchSeedPairError("seed source groups must be distinct")
        designs.add(design)
        groups.add(group)
        if group in development_groups:
            raise ResearchSeedPairError("seed source group overlaps Pilot development")
        upstream = provenance_by_design.get(design)
        if (not isinstance(upstream, Mapping)
                or upstream.get("source_group_conservative") != group
                or upstream.get("exact_file_match") is not True):
            raise ResearchSeedPairError("seed design lacks matching exact-file provenance")
        arms = case.get("arms")
        if not isinstance(arms, Mapping) or set(arms) != set(ARMS):
            raise ResearchSeedPairError("each seed design requires both arms")
        receipts: dict[str, dict[str, Any]] = {}
        arm_paths: dict[str, Path] = {}
        for arm in ARMS:
            row = arms[arm]
            if not isinstance(row, Mapping):
                raise ResearchSeedPairError("seed arm must be an object")
            project = _path(row.get("project"), f"{design} {arm} project")
            if project in projects:
                raise ResearchSeedPairError("seed arms cannot share a project")
            projects.add(project)
            digest = row.get("stage_receipt_digest")
            receipts[arm] = _project_identity(project, design, digest, arm)
            arm_paths[arm] = project
            if not allow_executed and any((project / "backend").glob("RUN_*")):
                raise ResearchSeedPairError(f"seed arm has already executed: {project}")
        control, treatment = (receipts[arm] for arm in ARMS)
        invariants = ("design_id", "platform", "top_module", "source_files",
                      "source_bundle_digest", "config_template", "sdc_template",
                      "authority_checkout", "logic_changes", "stub_generated")
        for key in invariants:
            if control.get(key) != treatment.get(key):
                raise ResearchSeedPairError(f"seed pair differs at {key}: {design}")
        if control.get("platform") != "sky130hs":
            raise ResearchSeedPairError("seed pair is not on sky130hs")
        if control.get("sdc_template", {}).get("staged_sha256") != treatment.get(
                "sdc_template", {}).get("staged_sha256"):
            raise ResearchSeedPairError("seed arm clock constraints differ")
        pinned_files = upstream.get("files")
        if not isinstance(pinned_files, list) or not pinned_files:
            raise ResearchSeedPairError("seed provenance file list is empty")
        staged_sources = {Path(row["source_path"]).name: row["sha256"]
                          for row in control["source_files"]}
        if len(staged_sources) != len(control["source_files"]):
            raise ResearchSeedPairError("seed source filenames are ambiguous")
        matched: set[str] = set()
        for file in pinned_files:
            if not isinstance(file, Mapping):
                raise ResearchSeedPairError("seed provenance file is malformed")
            local = _path(file.get("local_file"), "upstream-matched local file")
            name = local.name
            pinned_sha = "sha256:" + str(file.get("sha256_upstream_and_local"))
            if local in seen_source_paths or pinned_sha in seen_source_hashes:
                raise ResearchSeedPairError("seed source files overlap across groups")
            if (name in matched or staged_sources.get(name) != pinned_sha
                    or not local.is_file() or _file_digest(local) != pinned_sha):
                raise ResearchSeedPairError("seed upstream/local/staged bytes diverged")
            matched.add(name)
            seen_source_paths.add(local)
            seen_source_hashes.add(pinned_sha)
        if matched != set(staged_sources):
            raise ResearchSeedPairError("seed source file closure differs from provenance")
        normalized.append({"design_id": design, "source_group": group,
                           "projects": {arm: str(arm_paths[arm]) for arm in ARMS},
                           "stage_receipt_digests": {
                               arm: receipts[arm]["receipt_digest"] for arm in ARMS}})
    return {"valid": True, "spec_digest": _digest(payload),
            "epoch_digest": checked_epoch["epoch_digest"],
            "cases": normalized, "flow_script": str(flow_script),
            "orfs_root": str(orfs), "toolchain": toolchain}


def _append_event(path: Path, event: dict[str, Any], previous: str | None,
                  sequence: int) -> str:
    record = {"schema": EVENT_SCHEMA, "sequence": sequence,
              "previous_digest": previous, **event}
    record["event_digest"] = _digest(record)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return record["event_digest"]


def _execute(flow_script: Path, project: Path, variant: str,
             environment: dict[str, str], log: Path, timeout: int) -> tuple[int, str | None]:
    def stop_group(pid: int, sig: signal.Signals) -> None:
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            pass

    with log.open("xb") as handle:
        process = subprocess.Popen(
            ["bash", str(flow_script), str(project), "sky130hs", variant],
            stdout=handle, stderr=subprocess.STDOUT, env=environment,
            start_new_session=True,
        )
        try:
            return process.wait(timeout=timeout), None
        except subprocess.TimeoutExpired:
            stop_group(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                stop_group(process.pid, signal.SIGKILL)
                process.wait()
            return 124, "driver_wallclock_timeout"
        except BaseException:
            stop_group(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                stop_group(process.pid, signal.SIGKILL)
                process.wait()
            raise


def run_seed_pair(*, spec: str | Path, output: str | Path) -> dict[str, Any]:
    """Execute both predeclared designs and both arms once, serially."""
    spec_path = Path(spec).expanduser().resolve()
    destination = Path(output).expanduser().resolve()
    if destination.exists():
        raise ResearchSeedPairError(f"refusing to overwrite seed pair: {destination}")
    checked = verify_seed_pair_spec(spec_path)
    payload = _json(spec_path)
    for case in checked["cases"]:
        for project in case["projects"].values():
            if destination == Path(project) or destination.is_relative_to(Path(project)):
                raise ResearchSeedPairError("receipt output cannot be inside an arm project")
    tools = checked["toolchain"]["tools"]
    # The runner accepts many ambient escape hatches (resume, fast routing,
    # alternate work roots and hook overrides). None belongs to this frozen
    # one-action comparison. Keep ordinary process plumbing but pin all
    # research/ORFS controls explicitly below.
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("R2G_", "ORFS_"))
        and key not in {
            "FROM_STAGE", "FLOW_VARIANT", "PLACE_FAST", "ROUTE_FAST",
            "ROUTE_FAST_SKIP_DRT", "ROUTE_FAST_DRT_ITERS", "NUM_CORES",
            "DESIGN_CONFIG", "PDK_ROOT", "OPENROAD_EXE", "YOSYS_EXE",
            "MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES",
        }
    }
    environment.update({
        "ORFS_ROOT": checked["orfs_root"],
        "PDK_ROOT": checked["toolchain"]["pdk"]["root"],
        "OPENROAD_EXE": tools["openroad"]["path"],
        "YOSYS_EXE": tools["yosys"]["path"],
        "ORFS_TIMEOUT": str(payload["stage_timeout_seconds"]),
        "ORFS_MAX_CPUS": str(payload["max_cpus"]),
        "NUM_CORES": str(payload["max_cpus"]),
        "ORFS_STAGES": "synth floorplan place cts route finish",
        "FROM_STAGE": "",
        "MAKEFLAGS": "", "MFLAGS": "", "MAKEOVERRIDES": "",
        "R2G_SYNTH_FRONTEND": "default",
    })
    destination.mkdir(parents=True)
    environment["R2G_JOURNAL_DB"] = str(destination / "journal.sqlite")
    ledger = destination / "attempt-events.jsonl"
    tail: str | None = None
    sequence = 0
    for case in checked["cases"]:
        for arm in ARMS:
            design = case["design_id"]
            project = Path(case["projects"][arm])
            if not verify_research_epoch(payload["epoch"]).get("valid"):
                raise ResearchSeedPairError("seed epoch drifted before an arm")
            if not verify_staged_flow_project(project).get("valid"):
                raise ResearchSeedPairError("staged arm drifted before execution")
            variant = f"r4_seed_b_{design}_{arm}_v1"
            attempt = destination / design / arm
            attempt.mkdir(parents=True)
            log = attempt / "runner.log"
            sequence += 1
            tail = _append_event(ledger, {
                "event": "STARTED", "design_id": design, "arm": arm,
                "spec_digest": checked["spec_digest"],
                "source_group": case["source_group"], "project": str(project),
                "stage_receipt_digest": case["stage_receipt_digests"][arm],
                "flow_variant": variant, "started_unix": time.time(),
            }, tail, sequence)
            before = set((project / "backend").glob("RUN_*"))
            started = time.monotonic()
            error: str | None = None
            audit: dict[str, Any] | None = None
            run_dir: Path | None = None
            status = 125
            try:
                status, error = _execute(Path(checked["flow_script"]), project,
                                         variant, environment, log,
                                         6 * (payload["stage_timeout_seconds"] + 30))
                new_runs = set((project / "backend").glob("RUN_*")) - before
                if len(new_runs) != 1:
                    raise ResearchSeedPairError(
                        f"expected one new raw flow run, observed {len(new_runs)}")
                run_dir = next(iter(new_runs))
                audit = audit_flow_run(
                    project=project, run_dir=run_dir, producer_epoch=payload["epoch"],
                    auditor_epoch=payload["epoch"], output=attempt / "audit")
            except Exception as exc:  # preserve all failed attempts without inventing a verdict
                error = f"{type(exc).__name__}: {exc}"
                if not log.is_file():
                    log.write_text(f"runner failed before opening log: {error}\n",
                                   encoding="utf-8")
            sequence += 1
            tail = _append_event(ledger, {
                "event": "TERMINAL", "design_id": design, "arm": arm,
                "flow_exit_code": status, "exception": error,
                "run_dir": str(run_dir) if run_dir is not None else None,
                "runner_log_sha256": _file_digest(log) if log.is_file() else None,
                "audit_path": str(attempt / "audit") if audit is not None else None,
                "audit_digest": audit["audit_digest"] if audit is not None else None,
                "oracle_verdict": audit["oracle_verdict"] if audit is not None else "UNKNOWN",
                "oracle_reason": audit["oracle_reason"] if audit is not None else None,
                "failure_layer": audit["failure_layer"] if audit is not None else None,
                "actual_cost": audit["actual_cost"] if audit is not None else None,
                "elapsed_seconds": time.monotonic() - started,
            }, tail, sequence)
    result = verify_seed_pair_run(destination, spec_path)
    result["event_tail_digest"] = tail
    (destination / "run-summary.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def verify_seed_pair_run(output: str | Path, spec: str | Path) -> dict[str, Any]:
    """Recompute the append-only chain and reverify every available raw audit."""
    root = Path(output).expanduser().resolve()
    spec_path = Path(spec).expanduser().resolve()
    payload = _json(spec_path)
    if payload.get("schema") != SCHEMA:
        raise ResearchSeedPairError("seed-pair specification changed schema")
    verified_spec = verify_seed_pair_spec(spec_path, allow_executed=True)
    if verified_spec["spec_digest"] != _digest(payload):
        raise ResearchSeedPairError("seed-pair specification digest drifted")
    case_index = {case["design_id"]: case for case in payload["cases"]}
    expected = [(case["design_id"], arm) for case in payload["cases"] for arm in ARMS]
    try:
        lines = (root / "attempt-events.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ResearchSeedPairError("seed-pair event ledger is missing") from exc
    if len(lines) != 2 * len(expected):
        raise ResearchSeedPairError("seed-pair ledger lacks a full four-arm denominator")
    tail: str | None = None
    outcomes: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        try:
            event = json.loads(line)
        except ValueError as exc:
            raise ResearchSeedPairError("seed-pair ledger is malformed") from exc
        if not isinstance(event, dict):
            raise ResearchSeedPairError("seed-pair event must be an object")
        digest = event.pop("event_digest", None)
        if (event.get("schema") != EVENT_SCHEMA or event.get("sequence") != index + 1
                or event.get("previous_digest") != tail or digest != _digest(event)):
            raise ResearchSeedPairError("seed-pair event chain is invalid")
        tail = digest
        design, arm = expected[index // 2]
        if event.get("design_id") != design or event.get("arm") != arm:
            raise ResearchSeedPairError("seed-pair event order or identity changed")
        if event.get("event") != ("STARTED" if index % 2 == 0 else "TERMINAL"):
            raise ResearchSeedPairError("seed-pair event sequence changed")
        if not index % 2 and event.get("spec_digest") != _digest(payload):
            raise ResearchSeedPairError("seed-pair specification drifted")
        if not index % 2:
            expected_arm = case_index[design]["arms"][arm]
            if (event.get("project") != expected_arm["project"]
                    or event.get("stage_receipt_digest") != expected_arm[
                        "stage_receipt_digest"]
                    or event.get("source_group") != case_index[design]["source_group"]):
                raise ResearchSeedPairError("seed-pair arm input binding drifted")
        if index % 2:
            log = root / design / arm / "runner.log"
            if not log.is_file() or event.get("runner_log_sha256") != _file_digest(log):
                raise ResearchSeedPairError("seed-pair runner log drifted")
            audit_path = event.get("audit_path")
            if audit_path is not None:
                if _path(audit_path, "audit") != root / design / arm / "audit":
                    raise ResearchSeedPairError("seed-pair audit path changed")
                audit = verify_flow_audit(audit_path)
                raw_audit = _json(Path(audit_path) / "flow-audit.json")
                expected_arm = case_index[design]["arms"][arm]
                if (audit["audit_digest"] != event.get("audit_digest")
                        or audit["design_id"] != design
                        or audit["oracle_verdict"] != event.get("oracle_verdict")
                        or audit["failure_layer"] != event.get("failure_layer")
                        or audit["oracle_reason"] != event.get("oracle_reason")
                        or audit["actual_cost"] != event.get("actual_cost")):
                    raise ResearchSeedPairError("seed-pair audit binding changed")
                if (raw_audit.get("project_path") != expected_arm["project"]
                        or raw_audit.get("stage_receipt_digest") != expected_arm[
                            "stage_receipt_digest"]
                        or raw_audit.get("run_dir") != event.get("run_dir")
                        or raw_audit.get("producer_epoch_digest") != verified_spec[
                            "epoch_digest"]
                        or raw_audit.get("auditor_epoch_digest") != verified_spec[
                            "epoch_digest"]):
                    raise ResearchSeedPairError("seed-pair raw audit targets a different arm or epoch")
            elif event.get("oracle_verdict") != "UNKNOWN":
                raise ResearchSeedPairError("unaudited arm cannot have a semantic verdict")
            elif event.get("actual_cost") is not None:
                raise ResearchSeedPairError("unaudited arm cannot claim audited cost")
            outcomes.append({"design_id": design, "arm": arm,
                             "oracle_verdict": event["oracle_verdict"],
                             "oracle_reason": event.get("oracle_reason"),
                             "failure_layer": event.get("failure_layer"),
                             "actual_cost": event.get("actual_cost"),
                             "audit_digest": event.get("audit_digest"),
                             "exception": event.get("exception")})
    qualified = []
    for index in range(0, len(outcomes), 2):
        control, treatment = outcomes[index:index + 2]
        qualified.append({"design_id": control["design_id"],
                          "positive_training_pair": bool(
                              control["oracle_verdict"] == "FAIL"
                              and control["failure_layer"] == "FLOW_TARGET_FAILURE"
                              and treatment["oracle_verdict"] == "PASS"
                              and control["audit_digest"] and treatment["audit_digest"]),
                          "control": control["oracle_verdict"],
                          "treatment": treatment["oracle_verdict"]})
    all_audited = all(row["audit_digest"] is not None for row in outcomes)
    audited_stage_calls = sum(
        int((row["actual_cost"] or {}).get("eda_stage_calls", 0))
        for row in outcomes if row["audit_digest"] is not None)
    audited_flow_calls = sum(
        int((row["actual_cost"] or {}).get("flow_driver_calls", 0))
        for row in outcomes if row["audit_digest"] is not None)
    return {"schema": SUMMARY_SCHEMA, "valid": all_audited,
            "all_registered_terminal": True, "all_arms_audited": all_audited,
            "spec_digest": _digest(payload),
            "event_tail_digest": tail, "outcomes": outcomes,
            "qualified_pairs": qualified,
            "two_source_group_positive_pairs": all(
                row["positive_training_pair"] for row in qualified),
            "memory_gate_status": "NOT_RUN", "m0_status": "NOT_BUILT",
            "actual_cost": {"registered_arm_attempts": len(outcomes),
                            "audited_flow_driver_calls": audited_flow_calls,
                            "audited_eda_stage_calls": audited_stage_calls,
                            "unaudited_arm_count": sum(
                                row["audit_digest"] is None for row in outcomes),
                            "model_calls": 0, "model_tokens": 0},
            "memory_update": "none",
            "claim_boundary": "Constructed training acquisition only; not natural failures, TEHM-selected S1 actions, functional repair or agent evidence."}


__all__ = ["ResearchSeedPairError", "verify_seed_pair_spec", "run_seed_pair",
           "verify_seed_pair_run"]
