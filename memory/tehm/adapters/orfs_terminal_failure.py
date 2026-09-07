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


def _digest(value):
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _registered_inputs(project: Path) -> list[dict]:
    config = project / "constraints/config.mk"
    values = parse_config_mk(config.read_text())
    names = [values.get("SDC_FILE", ""), *values.get("VERILOG_FILES", "").split()]
    if len(names) < 2 or any(not name or "$" in name or not Path(name).is_absolute()
                             for name in names):
        raise ValueError("terminal contract requires explicit absolute SDC and RTL inputs")
    paths = sorted({config.absolute(), *(Path(name).absolute() for name in names)})
    return [{"path": str(path), "resolved_path": str(path.resolve()),
             "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in paths]


def register_terminal_contract(project: Path, *, contract_version: str,
                               toolchain: dict, command: list[str]) -> dict:
    """Runner-only registration for a fresh, non-resumed execution workspace.

    The exclusive file precedes invocation. It is an auditable producer record,
    not a cryptographic timestamp or permission to admit historical failures.
    """
    project = Path(project).resolve()
    if contract_version != CONTRACT["version"]:
        raise ValueError("unsupported terminal failure contract")
    if any((project / "backend").glob("RUN_*")) or (project / "campaign-run-receipt.json").exists():
        raise ValueError("terminal registration requires a fresh project; no historical backfill")
    if toolchain.get("status") != "bound_internal" or toolchain.get("manifest_validation", {}).get("valid") is not True:
        raise ValueError("terminal registration requires a valid internal toolchain lock")
    registration = {"version": "orfs-terminal-preregistration-v1",
                    "project": str(project), "contract": copy.deepcopy(CONTRACT),
                    "contract_digest": _digest(CONTRACT),
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
        return (stored == registration and registration.get("project") == str(project)
                and registration.get("registration_digest") == _digest(unsigned)
                and registration.get("contract") == CONTRACT
                and registration.get("contract_digest") == _digest(CONTRACT)
                and registration.get("inputs") == _registered_inputs(project))
    except (OSError, ValueError, TypeError):
        return False


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
