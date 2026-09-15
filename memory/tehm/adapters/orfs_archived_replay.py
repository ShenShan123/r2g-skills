"""Replay archived ORFS bytes while probing a relocated toolchain separately.

Historical absolute paths remain logical identity keys in ``OriginReadPlan``.
No historical path is resolved or opened.  The current toolchain is freshly
probed from its own manifest, but this module deliberately does not infer that
it generated the historical artifacts.  The receipt grants no canonical,
learner, lifecycle, or production authority.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import sqlite3

from tehm.adapters.orfs_terminal_failure import (
    FLOW_CONTRACT, _digest, evaluate_density_terminal_failure,
)
from tehm.adapters.r2g_evidence import parse_config_mk
from tehm.origin_bundle import OriginBundleError, OriginReadPlan
from tehm.orfs_toolchain import load_toolchain_manifest
from tehm.orfs_toolchain_preflight import preflight_orfs_toolchain


def _project(value: str) -> str:
    if type(value) is not str:
        raise ValueError("archived project must be a lexical absolute path")
    path = PurePosixPath(value)
    if (not value.startswith("/") or value == "/" or "\x00" in value or str(path) != value
            or any(part in {"", ".", ".."} for part in value.split("/")[1:])):
        raise ValueError("archived project must be an unambiguous lexical absolute path")
    return value


def _join(project: str, relative: str) -> str:
    return str(PurePosixPath(project) / relative)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _strict_json(data: bytes):
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate archived JSON key")
            result[key] = value
        return result

    def nonfinite(value):
        raise ValueError("non-finite archived JSON constant")

    return json.loads(data, object_pairs_hook=object_pairs, parse_constant=nonfinite)


def _object(plan: OriginReadPlan, path: str, name: str) -> dict:
    value = plan.read_json(path)
    if type(value) is not dict:
        raise ValueError(f"archived {name} must be an object")
    return value


def _verify_registration_inputs(plan: OriginReadPlan, rows: object) -> list[dict]:
    if type(rows) is not list or len(rows) < 3:
        raise ValueError("archived registration inputs are incomplete")
    seen = set()
    for row in rows:
        if type(row) is not dict or set(row) != {"path", "resolved_path", "sha256"}:
            raise ValueError("archived registration input binding is malformed")
        path = row["path"]
        if type(path) is not str or path in seen:
            raise ValueError("archived registration input path is ambiguous")
        _project(path)
        _project(row["resolved_path"])
        seen.add(path)
        if _sha(plan.read_bytes(path)) != row["sha256"]:
            raise ValueError("archived registration input digest mismatch")
    return rows


def _verify_run_files(plan: OriginReadPlan, project: str, rows: object,
                      *, completed: bool) -> dict[str, tuple[str, bytes]]:
    if type(rows) is not list:
        raise ValueError("archived terminal run files are missing")
    expected_names = {"run-meta.json", "stage_log.jsonl", "flow.log"}
    if completed:
        expected_names |= set(FLOW_CONTRACT["final_artifacts"])
    if len(rows) != len(expected_names):
        raise ValueError("archived terminal run file inventory mismatch")
    files = {}
    run_roots = set()
    backend = PurePosixPath(project) / "backend"
    for row in rows:
        if type(row) is not dict or set(row) != {"path", "sha256"}:
            raise ValueError("archived terminal run binding is malformed")
        path = row["path"]
        logical = PurePosixPath(path) if type(path) is str else PurePosixPath(".")
        if (not str(logical).startswith("/") or str(logical) != path
                or not logical.is_relative_to(backend)):
            raise ValueError("archived terminal run path escapes project")
        relative = logical.relative_to(backend)
        if len(relative.parts) < 2 or not relative.parts[0].startswith("RUN_"):
            raise ValueError("archived terminal run identity is invalid")
        run_roots.add(relative.parts[0])
        name = logical.name
        if name in files or name not in expected_names:
            raise ValueError("archived terminal run file name is ambiguous")
        if name in FLOW_CONTRACT["final_artifacts"] and relative.parts[-2] != "final":
            raise ValueError("archived final artifact path is invalid")
        expected_parts = 3 if name in FLOW_CONTRACT["final_artifacts"] else 2
        if len(relative.parts) != expected_parts:
            raise ValueError("archived terminal run path layout is invalid")
        data = plan.read_bytes(path)
        if not data or _sha(data) != row["sha256"]:
            raise ValueError("archived terminal run file digest mismatch")
        files[name] = (path, data)
    if len(run_roots) != 1 or set(files) != expected_names:
        raise ValueError("archived terminal run file inventory mismatch")
    return files


def _archived_flow_receipt(plan: OriginReadPlan, project: str, *,
                           registration_digest: str,
                           historical_toolchain_digest: str) -> dict:
    registration = _object(
        plan, _join(project, "terminal-preregistration.json"), "registration",
    )
    unsigned = {k: v for k, v in registration.items() if k != "registration_digest"}
    if (not registration_digest
            or registration.get("version") != "orfs-terminal-preregistration-v1"
            or registration.get("registration_digest") != registration_digest
            or registration_digest != _digest(unsigned)
            or registration.get("project") != project
            or registration.get("contract") != FLOW_CONTRACT
            or registration.get("contract_digest") != _digest(FLOW_CONTRACT)
            or registration.get("toolchain_digest") != historical_toolchain_digest):
        raise ValueError("archived terminal registration binding mismatch")
    _verify_registration_inputs(plan, registration.get("inputs"))

    execution = _object(
        plan, _join(project, "campaign-run-receipt.json"), "execution receipt",
    )
    historical_tools = execution.get("toolchain_binding")
    flow_rc = execution.get("flow_rc")
    if (execution.get("terminal_preregistration") != registration
            or type(historical_tools) is not dict
            or _digest(historical_tools) != historical_toolchain_digest
            or execution.get("command") != registration.get("command")
            or type(execution.get("attempt")) is not int
            or execution["attempt"] != 1
            or execution.get("completed") is not (flow_rc == 0)
            or execution.get("resume_from") is not None
            or execution.get("supervisor_timeout") is not False
            or type(flow_rc) is not int or flow_rc not in (0, 2)):
        raise ValueError("archived terminal execution binding mismatch")
    files = _verify_run_files(
        plan, project, execution.get("terminal_run_files"), completed=flow_rc == 0,
    )
    stage_path, stage_data = files["stage_log.jsonl"]
    if (execution.get("stage_log") != stage_path
            or execution.get("stage_log_sha256") != _sha(stage_data)):
        raise ValueError("archived terminal stage binding mismatch")
    meta = _strict_json(files["run-meta.json"][1])
    stages = [_strict_json(line) for line in stage_data.splitlines() if line.strip()]
    log = plan.read_text(files["flow.log"][0])
    if (type(meta) is not dict or meta.get("run_tag") != PurePosixPath(stage_path).parent.name
            or type(meta.get("make_status")) is not int or meta["make_status"] != flow_rc
            or any(type(row) is not dict for row in stages)):
        raise ValueError("archived terminal raw run identity mismatch")
    if flow_rc == 0:
        valid = (
            [row.get("stage") for row in stages] == FLOW_CONTRACT["success_stages"]
            and all(type(row.get("status")) is int and row["status"] == 0 for row in stages)
            and "[ERROR " not in log
        )
        result = {
            "contract": json.loads(json.dumps(FLOW_CONTRACT)),
            "contract_digest": _digest(FLOW_CONTRACT),
            "verdict": "PASS" if valid else "UNKNOWN",
            "run_tag": meta["run_tag"],
            "reasons": [] if valid else ["incomplete_or_contradictory_flow_completion"],
            "inputs_digest": _digest(execution["terminal_run_files"]),
            "full_signoff_complete": False,
            "learner_admission": False,
            "preregistration_verified": False,
            "downstream_checks": {
                key: "UNKNOWN" for key in ("drc", "lvs", "final_timing")
            },
        }
    else:
        result = evaluate_density_terminal_failure(meta, stages, log)
        result.update(
            contract=json.loads(json.dumps(FLOW_CONTRACT)),
            contract_digest=_digest(FLOW_CONTRACT),
        )
    result.pop("receipt_digest", None)
    result.update(
        registration_binding_verified=True,
        registration_digest=registration_digest,
        execution_digest=_digest(execution),
    )
    result["receipt_digest"] = _digest(result)
    return result


def rebuild_archived_flow_feasibility_record(
        plan: OriginReadPlan, *, acquisition: dict,
        current_toolchain_manifest: str | Path,
        expected_current_manifest_digest: str):
    """Rebuild original canonical record content from consumer-pinned origins.

    Archived and current receipts are separate.  No relocated physical path or
    new probe receipt is inserted into the original canonical identity.
    """
    from tehm.adapters.orfs_scoped import _record_from_replayed_flow_states

    required = {"before", "after", "lineage_id", "before_pin", "after_pin",
                "config_edits", "toolchain_manifest", "expected_manifest_digest"}
    if type(acquisition) is not dict or set(acquisition) not in (required, required | {"role"}):
        raise ValueError("archived reconstruction requires an explicit frozen acquisition")
    acquisition = dict(acquisition)
    before, after = _project(acquisition["before"]), _project(acquisition["after"])
    historical_manifest_path = _project(acquisition["toolchain_manifest"])
    lineage, role = acquisition["lineage_id"], acquisition.get("role", "treatment")
    if type(lineage) is not str or not lineage.strip() or role not in {"treatment", "control"}:
        raise ValueError("archived reconstruction requires lineage and role")
    historical_lock = load_toolchain_manifest(plan.read_json(historical_manifest_path))
    if historical_lock["manifest_digest"] != acquisition["expected_manifest_digest"]:
        raise ValueError("archived historical manifest pin mismatch")
    before_registration = _object(
        plan, _join(before, "terminal-preregistration.json"), "registration",
    )
    if acquisition["before_pin"] != _digest({
            k: v for k, v in before_registration.items() if k != "registration_digest"}):
        raise ValueError("archived reconstruction registration pin mismatch")
    historical_toolchain_digest = before_registration["toolchain_digest"]
    for project in (before, after):
        execution = _object(plan, _join(project, "campaign-run-receipt.json"), "execution")
        tools = execution.get("toolchain_binding") or {}
        validation = tools.get("manifest_validation") or {}
        if (validation.get("manifest_digest") != acquisition["expected_manifest_digest"]
                or tools.get("toolchain_manifest") != historical_manifest_path
                or tools.get("orfs_root") != historical_lock["orfs"]["root"]):
            raise ValueError("archived historical lock/execution association mismatch")
        for name in ("openroad", "yosys"):
            expected = historical_lock["tools"][name]
            actual = (tools.get("tools") or {}).get(name) or {}
            for field in ("path", "source", "sha256", "version", "capabilities"):
                if actual.get(field) != expected.get(field):
                    raise ValueError("archived historical executable lock association mismatch")
    replay = replay_archived_flow_feasibility_pair(
        plan, before, after, before_pin=acquisition["before_pin"],
        after_pin=acquisition["after_pin"], config_edits=acquisition["config_edits"],
        historical_toolchain_digest=historical_toolchain_digest,
        current_toolchain_manifest=current_toolchain_manifest,
        expected_current_manifest_digest=expected_current_manifest_digest,
    )
    pair = replay["historical_pair_receipt"]
    states, refs = [], []
    for side, project in (("before", before), ("after", after)):
        registration = _object(plan, _join(project, "terminal-preregistration.json"), "registration")
        execution = _object(plan, _join(project, "campaign-run-receipt.json"), "execution")
        files = execution["terminal_run_files"]
        refs.extend(row["path"] for row in files)
        states.append({
            "config": parse_config_mk(plan.read_text(_join(project, "constraints/config.mk"))),
            "reports": {"flow_feasibility": {
                "scope": FLOW_CONTRACT["scope"], "verdict": pair[side]["verdict"],
                "downstream_checks": pair[side]["downstream_checks"],
            }},
            "artifacts": {"flow_feasibility": {
                "project": project, "run_tag": pair[side]["run_tag"],
                "registration": registration, "run_files": files,
                "receipt_digest": pair[side]["receipt_digest"],
            }},
        })
    kwargs = {key: acquisition[key] for key in (
        "before_pin", "after_pin", "config_edits", "expected_manifest_digest",
    )}
    kwargs["config_edits"] = dict(kwargs["config_edits"])
    kwargs["toolchain_manifest"] = historical_manifest_path
    record = _record_from_replayed_flow_states(
        before, after, lineage_id=lineage, kwargs=kwargs, pair=pair,
        states=states, refs=refs, role=role,
    )
    if plan.verify() != replay["origin_read_plan"]:
        raise OriginBundleError("archived bytes changed during canonical reconstruction")
    replay = dict(replay)
    replay["canonical_record_reconstructed"] = True
    replay["receipt_digest"] = _digest({k: v for k, v in replay.items() if k != "receipt_digest"})
    return record, replay


def replay_persisted_archived_flow_feasibility(
        conn: sqlite3.Connection, transition_id: str, plan: OriginReadPlan, *,
        acquisition: dict, current_toolchain_manifest: str | Path,
        expected_current_manifest_digest: str) -> dict:
    """Read-only historical canonical reconstruction; never learner admission."""
    from tehm.adapters.orfs_scoped import _compare_persisted_flow_record
    from tehm.causal.mechanism import load_transition_facts

    changes_before = conn.total_changes
    facts = load_transition_facts(conn, transition_id)
    if not facts.verifier.get("scoped_execution"):
        raise ValueError("archived canonical replay requires a scoped transition")
    record, replay = rebuild_archived_flow_feasibility_record(
        plan, acquisition=acquisition, current_toolchain_manifest=current_toolchain_manifest,
        expected_current_manifest_digest=expected_current_manifest_digest,
    )
    _compare_persisted_flow_record(conn, transition_id, record)
    if conn.total_changes != changes_before:
        raise ValueError("archived canonical replay mutated the source database")
    result = {
        "version": "orfs-persisted-archived-scoped-replay-v1",
        "transition_id": transition_id, "acquisition_digest": _digest(acquisition),
        "archived_replay_receipt": replay, "persisted_binding_verified": True,
        "canonical_replayed": True, "canonical_memory_mutation": "none",
        "current_toolchain_probed": True, "toolchain_equivalence_proven": False,
        "historical_execution_reexecuted": False, "learner_admission": False,
        "promotion_attempted": False, "production_authority": False,
    }
    result["receipt_digest"] = _digest(result)
    return result


def _live_toolchain(toolchain_manifest: str | Path,
                    expected_manifest_digest: str) -> tuple[dict, dict]:
    locked = load_toolchain_manifest(toolchain_manifest)
    if (type(expected_manifest_digest) is not str
            or locked["manifest_digest"] != expected_manifest_digest):
        raise ValueError("relocated toolchain manifest pin mismatch")
    reference = {
        "orfs_root": locked["orfs"]["root"],
        "toolchain_manifest": str(Path(toolchain_manifest).resolve()),
    }
    current = preflight_orfs_toolchain(reference)
    validation = current.get("manifest_validation") or {}
    if (current.get("status") != "bound_internal" or validation.get("valid") is not True
            or validation.get("manifest_digest") != expected_manifest_digest):
        raise ValueError("relocated toolchain live preflight failed")
    return locked, current


def replay_archived_flow_feasibility_pair(
        plan: OriginReadPlan, before_project: str, after_project: str, *,
        before_pin: str, after_pin: str, config_edits: dict,
        historical_toolchain_digest: str,
        current_toolchain_manifest: str | Path,
        expected_current_manifest_digest: str) -> dict:
    """Derive a paired historical receipt without opening original paths.

    A fresh current-toolchain preflight is mandatory but deliberately separate
    from the archived execution identity.  Semantic/binary equivalence between
    those two generations is not inferred by this function.
    """
    if not isinstance(plan, OriginReadPlan):
        raise ValueError("archived replay requires an OriginReadPlan")
    before, after = _project(before_project), _project(after_project)
    if before == after or set(config_edits) != {"CORE_UTILIZATION"}:
        raise ValueError("archived flow pair requires distinct projects and one density edit")
    value = config_edits["CORE_UTILIZATION"]
    if type(value) not in (str, int, float) or not 0 < float(value) <= 100:
        raise ValueError("archived flow pair density edit is invalid")
    origin_before = plan.verify()
    locked, current = _live_toolchain(
        current_toolchain_manifest, expected_current_manifest_digest,
    )
    receipts = [
        _archived_flow_receipt(
            plan, project, registration_digest=pin,
            historical_toolchain_digest=historical_toolchain_digest,
        )
        for project, pin in ((before, before_pin), (after, after_pin))
    ]
    if receipts[0]["run_tag"] == receipts[1]["run_tag"]:
        raise ValueError("archived flow pair requires distinct run witnesses")
    configs = [
        parse_config_mk(plan.read_text(_join(project, "constraints/config.mk")))
        for project in (before, after)
    ]
    normalized = []
    for project, config in zip((before, after), configs, strict=True):
        if not config.get("DESIGN_NAME") or not config.get("PLATFORM"):
            raise ValueError("archived flow pair lacks design/platform identity")
        observed = dict(config)
        observed["VERILOG_FILES"] = [
            _sha(plan.read_bytes(path)) for path in config["VERILOG_FILES"].split()
        ]
        observed["SDC_FILE"] = _sha(plan.read_bytes(config["SDC_FILE"]))
        observed["wrapper_local_sdc_digest"] = _sha(
            plan.read_bytes(_join(project, "constraints/constraint.sdc"))
        )
        normalized.append(observed)
    if (configs[1].get("CORE_UTILIZATION") != str(value)
            or configs[0].get("CORE_UTILIZATION") == str(value)):
        raise ValueError("archived flow pair does not execute its declared density edit")
    normalized[0]["CORE_UTILIZATION"] = str(value)
    if normalized[0] != normalized[1]:
        raise ValueError("archived flow pair has undeclared config, RTL or SDC differences")
    if plan.verify() != origin_before:
        raise OriginBundleError("origin read plan changed during archived replay")
    if _live_toolchain(current_toolchain_manifest, expected_current_manifest_digest) != (locked, current):
        raise ValueError("relocated toolchain changed during archived replay")
    historical_pair = {
        "version": "orfs-flow-feasibility-pair-v1",
        "contract_digest": _digest(FLOW_CONTRACT),
        "before": receipts[0], "after": receipts[1],
        "config_edits": dict(config_edits),
        "controlled_measurement_valid": all(
            receipt["verdict"] in {"PASS", "FAIL"} for receipt in receipts
        ),
        "matched_context_digest": _digest(normalized[0]),
        "learner_admission": False, "promotion_attempted": False,
    }
    historical_pair["receipt_digest"] = _digest(historical_pair)
    result = {
        "version": "orfs-archived-flow-feasibility-pair-v1",
        "contract_digest": _digest(FLOW_CONTRACT),
        "before": receipts[0],
        "after": receipts[1],
        "config_edits": dict(config_edits),
        "controlled_measurement_valid": all(
            receipt["verdict"] in {"PASS", "FAIL"} for receipt in receipts
        ),
        "matched_context_digest": _digest(normalized[0]),
        "historical_pair_receipt": historical_pair,
        "historical_receipt_identity_preserved": True,
        "historical_run_directory_inventory_verified": False,
        "origin_read_plan": origin_before,
        "current_toolchain_manifest_digest": locked["manifest_digest"],
        "current_toolchain_preflight_digest": _digest(current),
        "current_toolchain_probed": True,
        "historical_toolchain_digest": historical_toolchain_digest,
        "historical_execution_reexecuted": False,
        "toolchain_equivalence_proven": False,
        "canonical_replayed": False,
        "learner_admission": False,
        "promotion_attempted": False,
        "production_authority": False,
    }
    result["receipt_digest"] = _digest(result)
    return result
