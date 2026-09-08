"""Narrow terminal-failure evidence, not learner admission or signoff PASS.

The producer must separately bind this contract before execution. Replaying
historical logs only diagnoses the failure; it cannot establish preregistration.
"""
from __future__ import annotations

import hashlib
import copy
import re
import json
from pathlib import Path

from tehm.adapters.r2g_evidence import parse_config_mk

from tehm.ids import stable_dumps

CONTRACT = {
    "version": "orfs-density-terminal-failure-v1",
    "scope": "flow_feasibility",
    "stage": "place",
    "error_codes": ["FLW-0024", "GPL-0301"],
    "required_successful_prefix": ["synth", "floorplan"],
}
FLOW_CONTRACT = {
    "version": "orfs-flow-feasibility-v2", "scope": "flow_feasibility",
    "failure_contract": copy.deepcopy(CONTRACT),
    "success_stages": ["synth", "floorplan", "place", "cts", "route", "finish"],
    "final_artifacts": ["6_final.def", "6_final.odb", "6_final.gds"],
    "signoff_claim": False,
}


def resolve_terminal_contract(version: str) -> dict:
    for contract in (CONTRACT, FLOW_CONTRACT):
        if version == contract["version"]:
            return copy.deepcopy(contract)
    raise ValueError("unsupported terminal failure contract")


def _digest(value):
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _registered_inputs(project: Path) -> list[dict]:
    config = project / "constraints/config.mk"
    values = parse_config_mk(config.read_text())
    names = [values.get("SDC_FILE", ""), *values.get("VERILOG_FILES", "").split()]
    if len(names) < 2 or any(not name or "$" in name or not Path(name).is_absolute()
                             for name in names):
        raise ValueError("terminal contract requires explicit absolute SDC and RTL inputs")
    # The R2G wrapper also requires/stages this local SDC even when SDC_FILE
    # names an external file. Bind both actual inputs before launching it.
    paths = sorted({config.absolute(), (project / "constraints/constraint.sdc").absolute(),
                    *(Path(name).absolute() for name in names)})
    return [{"path": str(path), "resolved_path": str(path.resolve()),
             "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in paths]


def register_terminal_contract(project: Path, *, contract_version: str,
                               toolchain: dict, command: list[str]) -> dict:
    """Runner-only registration for a fresh, non-resumed execution workspace.

    The exclusive file precedes invocation. It is an auditable producer record,
    not a cryptographic timestamp or permission to admit historical failures.
    """
    project = Path(project).resolve()
    contract = resolve_terminal_contract(contract_version)
    if any((project / "backend").glob("RUN_*")) or (project / "campaign-run-receipt.json").exists():
        raise ValueError("terminal registration requires a fresh project; no historical backfill")
    if toolchain.get("status") != "bound_internal" or toolchain.get("manifest_validation", {}).get("valid") is not True:
        raise ValueError("terminal registration requires a valid internal toolchain lock")
    registration = {"version": "orfs-terminal-preregistration-v1",
                    "project": str(project), "contract": contract,
                    "contract_digest": _digest(contract),
                    "toolchain_digest": _digest(toolchain), "command": list(command),
                    "inputs": _registered_inputs(project)}
    registration["registration_digest"] = _digest(registration)
    path = project / "terminal-preregistration.json"
    with path.open("x") as stream:
        stream.write(json.dumps(registration, indent=2, sort_keys=True) + "\n")
    return registration


def recheck_terminal_registration(project: Path, registration: dict) -> bool:
    """Recheck registration and source identities, without granting authority."""
    project = Path(project).resolve()
    try:
        stored = json.loads((project / "terminal-preregistration.json").read_text())
        unsigned = {k: v for k, v in registration.items() if k != "registration_digest"}
        contract = resolve_terminal_contract(registration.get("contract", {}).get("version"))
        return (stored == registration and registration.get("project") == str(project)
                and registration.get("registration_digest") == _digest(unsigned)
                and registration.get("contract") == contract
                and registration.get("contract_digest") == _digest(contract)
                and registration.get("inputs") == _registered_inputs(project))
    except (OSError, ValueError, TypeError):
        return False


def terminal_run_file_bindings(project: Path) -> list[dict]:
    """Bind one fresh run's raw inputs; ambiguity or missing files abstains."""
    project = Path(project).resolve()
    runs = list((project / "backend").glob("RUN_*"))
    if len(runs) != 1:
        return []
    records = []
    names = ["run-meta.json", "stage_log.jsonl", "flow.log"]
    try:
        meta = json.loads((runs[0] / "run-meta.json").read_text())
        if type(meta.get("make_status")) is int and meta["make_status"] == 0:
            names.extend("final/" + name for name in FLOW_CONTRACT["final_artifacts"])
    except (OSError, ValueError, AttributeError):
        return []
    for name in names:
        path = (runs[0] / name).resolve()
        if not path.is_relative_to(project / "backend") or not path.is_file() or not path.stat().st_size:
            return []
        records.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return records


def replay_terminal_failure(project: Path, *, registration_digest: str,
                            current_toolchain: dict, _allow_completion: bool = False) -> dict:
    """Verify raw evidence against an independently supplied registration pin.

    The caller obtains that pin from the trusted pre-execution producer, not
    from the post-execution receipt being audited. This verifies consistency,
    not a cryptographic timestamp. Learner authority is deliberately separate.
    """
    project = Path(project).resolve()
    registration = json.loads((project / "terminal-preregistration.json").read_text())
    execution = json.loads((project / "campaign-run-receipt.json").read_text())
    expected_contract = FLOW_CONTRACT if _allow_completion else CONTRACT
    if registration.get("contract") != expected_contract:
        raise ValueError("terminal consumer contract version mismatch")
    if (not registration_digest or registration.get("registration_digest") != registration_digest
            or not recheck_terminal_registration(project, registration)
            or execution.get("terminal_preregistration") != registration):
        raise ValueError("terminal registration binding mismatch")
    if (current_toolchain.get("status") != "bound_internal"
            or current_toolchain.get("manifest_validation", {}).get("valid") is not True
            or _digest(current_toolchain) != registration["toolchain_digest"]
            or execution.get("toolchain_binding") != current_toolchain):
        raise ValueError("terminal toolchain binding mismatch")
    if (execution.get("command") != registration["command"]
            or type(execution.get("attempt")) is not int or execution["attempt"] != 1
            or execution.get("completed") is not (execution.get("flow_rc") == 0)
            or execution.get("resume_from") is not None
            or execution.get("supervisor_timeout") is not False
            or type(execution.get("flow_rc")) is not int
            or execution["flow_rc"] not in ((0, 2) if _allow_completion else (2,))):
        raise ValueError("terminal execution is not the registered fresh failure")
    files = terminal_run_file_bindings(project)
    if not files or execution.get("terminal_run_files") != files:
        raise ValueError("terminal raw run evidence mismatch")
    paths = {Path(item["path"]).name: Path(item["path"]) for item in files}
    stage_path = paths["stage_log.jsonl"]
    if (execution.get("stage_log") != str(stage_path)
            or execution.get("stage_log_sha256") != files[1]["sha256"]):
        raise ValueError("terminal stage log binding mismatch")
    meta = json.loads(paths["run-meta.json"].read_text())
    if (meta.get("run_tag") != stage_path.parent.name or type(meta.get("make_status")) is not int
            or meta["make_status"] != execution["flow_rc"]):
        raise ValueError("terminal run identity mismatch")
    stages = [json.loads(line) for line in stage_path.read_text().splitlines() if line.strip()]
    log = paths["flow.log"].read_text()
    if execution["flow_rc"] == 0:
        valid = ([row.get("stage") for row in stages] == FLOW_CONTRACT["success_stages"]
                 and all(type(row.get("status")) is int and row["status"] == 0 for row in stages)
                 and not re.search(r"\[ERROR\s+[A-Z]+-\d+\]", log))
        result = {"contract": copy.deepcopy(FLOW_CONTRACT), "contract_digest": _digest(FLOW_CONTRACT),
                  "verdict": "PASS" if valid else "UNKNOWN", "run_tag": meta["run_tag"],
                  "reasons": [] if valid else ["incomplete_or_contradictory_flow_completion"],
                  "inputs_digest": _digest(files), "full_signoff_complete": False,
                  "learner_admission": False, "preregistration_verified": False,
                  "downstream_checks": {key: "UNKNOWN" for key in ("drc", "lvs", "final_timing")},
                  "receipt_digest": "pending"}
    else:
        result = evaluate_density_terminal_failure(meta, stages, log)
        if _allow_completion:
            result.update(contract=copy.deepcopy(FLOW_CONTRACT), contract_digest=_digest(FLOW_CONTRACT))
    result.pop("receipt_digest")
    result.update(registration_binding_verified=True, registration_digest=registration_digest,
                  execution_digest=_digest(execution))
    if terminal_run_file_bindings(project) != files or not recheck_terminal_registration(project, registration):
        raise ValueError("terminal inputs changed during replay")
    result["receipt_digest"] = _digest(result)
    return result


def replay_flow_feasibility(project: Path, *, registration_digest: str,
                            current_toolchain: dict) -> dict:
    """V2 paired measurement: completed flow or recognized density failure.

    PASS does not assert DRC/LVS, timing closure, PPA utility or learner admission.
    """
    return replay_terminal_failure(project, registration_digest=registration_digest,
                                   current_toolchain=current_toolchain, _allow_completion=True)


def replay_flow_feasibility_pair(before: Path, after: Path, *, before_pin: str,
                                 after_pin: str, current_toolchain: dict,
                                 config_edits: dict) -> dict:
    """Check a declared single density intervention, not grant causal authority.

    Equal outcome labels alone are insufficient. Both registrations, RTL/SDC
    contents, config context, and distinct executions must match this pairing.
    """
    before, after = Path(before).resolve(), Path(after).resolve()
    if before == after:
        raise ValueError("flow pair requires distinct projects")
    if set(config_edits) != {"CORE_UTILIZATION"}:
        raise ValueError("flow pair requires one declared CORE_UTILIZATION edit")
    receipts = [replay_flow_feasibility(project, registration_digest=pin,
                                       current_toolchain=current_toolchain)
                for project, pin in ((before, before_pin), (after, after_pin))]
    if receipts[0]["run_tag"] == receipts[1]["run_tag"]:
        raise ValueError("flow pair requires distinct run witnesses")
    configs = [parse_config_mk((p / "constraints/config.mk").read_text()) for p in (before, after)]
    normalized = []
    for project, config in zip((before, after), configs):
        if not config.get("DESIGN_NAME") or not config.get("PLATFORM"):
            raise ValueError("flow pair lacks design/platform identity")
        observed = dict(config)
        observed["VERILOG_FILES"] = [hashlib.sha256(Path(path).read_bytes()).hexdigest()
                                     for path in config["VERILOG_FILES"].split()]
        observed["SDC_FILE"] = hashlib.sha256(Path(config["SDC_FILE"]).read_bytes()).hexdigest()
        observed["wrapper_local_sdc_digest"] = hashlib.sha256(
            (project / "constraints/constraint.sdc").read_bytes()).hexdigest()
        normalized.append(observed)
    value = config_edits["CORE_UTILIZATION"]
    if type(value) not in (str, int, float) or not 0 < float(value) <= 100:
        raise ValueError("flow pair density edit is invalid")
    if (configs[1].get("CORE_UTILIZATION") != str(value)
            or configs[0].get("CORE_UTILIZATION") == str(value)):
        raise ValueError("flow pair does not execute its declared density edit")
    normalized[0]["CORE_UTILIZATION"] = str(value)
    if normalized[0] != normalized[1]:
        raise ValueError("flow pair has undeclared config, RTL or SDC differences")
    result = {"version": "orfs-flow-feasibility-pair-v1", "contract_digest": _digest(FLOW_CONTRACT),
              "before": receipts[0], "after": receipts[1], "config_edits": dict(config_edits),
              "controlled_measurement_valid": all(r["verdict"] in {"PASS", "FAIL"} for r in receipts),
              "matched_context_digest": _digest(normalized[0]),
              "learner_admission": False, "promotion_attempted": False}
    result["receipt_digest"] = _digest(result)
    return result


def replay_locked_flow_feasibility_pair(
        before: Path, after: Path, *, before_pin: str, after_pin: str,
        config_edits: dict, toolchain_manifest: str | Path,
        expected_manifest_digest: str) -> dict:
    """Replay using live tool probes, never a producer's saved valid flag.

    Registration and lock pins must come from the caller's frozen acquisition
    record, not be inferred from the execution being validated. This checks
    measurement integrity only; it does not certify pin chronology or grant
    learner authority. Original pair receipt identity is retained.
    """
    from tehm.orfs_toolchain import load_toolchain_manifest
    from tehm.orfs_toolchain_preflight import preflight_orfs_toolchain

    locked = load_toolchain_manifest(toolchain_manifest)
    if not expected_manifest_digest or locked["manifest_digest"] != expected_manifest_digest:
        raise ValueError("flow pair toolchain lock pin mismatch")
    manifest = {"orfs_root": locked["orfs"]["root"],
                "toolchain_manifest": str(Path(toolchain_manifest).resolve())}

    def check_tools():
        current = preflight_orfs_toolchain(manifest)
        validation = current.get("manifest_validation") or {}
        if (current.get("status") != "bound_internal" or validation.get("valid") is not True
                or validation.get("manifest_digest") != expected_manifest_digest):
            raise ValueError("flow pair live toolchain replay failed")
        return current

    tools = check_tools()
    receipt = replay_flow_feasibility_pair(
        before, after, before_pin=before_pin, after_pin=after_pin,
        current_toolchain=tools, config_edits=config_edits)
    if check_tools() != tools:
        raise ValueError("flow pair toolchain changed during replay")
    return receipt


def evaluate_density_terminal_failure(run_meta: dict, stages: list[dict],
                                      flow_log: str) -> dict:
    """Recognize a specific density failure from mutually consistent inputs.

    Unknown tool errors, timeouts, incomplete prefixes and contradictory later
    success remain UNKNOWN. No report's PASS or caller-provided reason is used.
    """
    reasons = []
    if not isinstance(run_meta, dict) or not isinstance(stages, list) or not isinstance(flow_log, str):
        raise ValueError("terminal failure requires run metadata, stage rows and log text")
    if any(not isinstance(row, dict) for row in stages):
        raise ValueError("terminal failure stage rows must be objects")
    if not isinstance(run_meta.get("run_tag"), str) or not run_meta["run_tag"].strip():
        reasons.append("missing_run_identity")
    if type(run_meta.get("make_status")) is not int or run_meta["make_status"] != 2:
        reasons.append("not_normal_make_failure")
    expected_stages = CONTRACT["required_successful_prefix"] + [CONTRACT["stage"]]
    if [row.get("stage") for row in stages] != expected_stages:
        reasons.append("incomplete_or_contradictory_stage_sequence")
    elif any(type(row.get("status")) is not int or row["status"] != status
             for row, status in zip(stages, (0, 0, 2))):
        reasons.append("stage_status_mismatch")
    codes = sorted(set(re.findall(r"\[ERROR\s+([A-Z]+-\d+)\]", flow_log)))
    if len(codes) != 1 or codes[0] not in CONTRACT["error_codes"]:
        reasons.append("unrecognized_or_mixed_errors")
    lower = flow_log.lower()
    if any(marker in lower for marker in (
            "timed out", "timeout", "segmentation fault", "permission denied",
            "read-only file system", "killed", "interrupted", "not found")):
        reasons.append("infrastructure_or_interruption_marker")
    if codes == ["FLW-0024"] and "Place density exceeds 1.0" not in flow_log:
        reasons.append("missing_density_diagnostic")
    if codes == ["GPL-0301"] and not re.search(r"Utilization.*exceeds.*100", flow_log):
        reasons.append("missing_utilization_diagnostic")
    receipt = {
        "contract": copy.deepcopy(CONTRACT), "contract_digest": _digest(CONTRACT),
        "run_tag": run_meta.get("run_tag"),
        "inputs_digest": _digest({"run_meta": run_meta, "stages": stages,
                                  "flow_log_sha256": hashlib.sha256(flow_log.encode()).hexdigest()}),
        "verdict": "UNKNOWN" if reasons else "FAIL", "reasons": sorted(reasons),
        "observed_error_codes": codes,
        "downstream_checks": {key: "UNKNOWN" if reasons else "NOT_EXECUTED"
                              for key in ("route", "drc", "lvs", "final_timing")},
        "full_signoff_complete": False, "learner_admission": False,
        "preregistration_verified": False,
    }
    receipt["receipt_digest"] = _digest(receipt)
    return receipt
