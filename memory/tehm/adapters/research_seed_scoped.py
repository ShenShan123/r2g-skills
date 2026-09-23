"""Canonical parent acquisition from independently audited RC1 seed pairs.

This adapter consumes the Revision4 research runner's immutable staged inputs
and flow-audit receipts.  It does not backfill the legacy terminal-run schema,
grant production authority, or reinterpret a constructed density challenge as
a natural failure.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from tehm.adapters.orfs_scoped import _compare_persisted_flow_record
from tehm.adapters.r2g_evidence import parse_config_mk
from tehm.canonical.capture import ExecutionRecord
from tehm.evaluation.research_flow import (
    verify_flow_audit, verify_staged_flow_project,
)
from tehm.ids import stable_dumps


ACQUISITION_VERSION = "tehm-r4-rc1-seed-acquisition-v1"
SCOPED_VERSION = "orfs-rc1-seed-record-v1"
FLOW_CONTRACT = {
    "version": "tehm-research-fixed-flow-feasibility-v1",
    "scope": "flow_feasibility",
    "required_stages": ["synth", "floorplan", "place", "cts", "route", "finish"],
    "accepted_control_failure": {
        "oracle_verdict": "FAIL",
        "oracle_reason": "placement_density_infeasible",
        "failure_layer": "FLOW_TARGET_FAILURE",
    },
    "accepted_treatment": {
        "oracle_verdict": "PASS",
        "oracle_reason": "flow_completed",
        "failure_layer": "NONE",
    },
    "strict_signoff_claim": False,
    "functional_repair_claim": False,
}


class ResearchSeedScopedError(ValueError):
    """An RC1 parent acquisition cannot be independently reconstructed."""


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ResearchSeedScopedError(f"cannot read JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ResearchSeedScopedError(f"JSON object required: {path}")
    return value


def _path(value: Any, label: str, *, directory: bool = True) -> Path:
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise ResearchSeedScopedError(f"{label} must be an absolute path")
    path = Path(value).resolve()
    if directory and not path.is_dir():
        raise ResearchSeedScopedError(f"{label} is missing: {path}")
    return path


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _audit(path: Path, project: Path, expected: Mapping[str, str]) -> dict[str, Any]:
    checked = verify_flow_audit(path)
    raw = _json(path / "flow-audit.json")
    if (checked.get("valid") is not True
            or checked.get("audit_digest") != raw.get("audit_digest")
            or Path(str(raw.get("project_path") or "")).resolve() != project
            or raw.get("source_mutation") != "none"
            or raw.get("memory_update") != "none"
            or raw.get("production_authority") is not False
            or any(raw.get(key) != value for key, value in expected.items())):
        raise ResearchSeedScopedError("flow audit does not satisfy the seed arm contract")
    return raw


def _source_identity(receipt: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "design_id": receipt.get("design_id"),
        "platform": receipt.get("platform"),
        "top_module": receipt.get("top_module"),
        "source_files": receipt.get("source_files"),
        "source_bundle_digest": receipt.get("source_bundle_digest"),
        "sdc_sha256": (receipt.get("sdc_template") or {}).get("staged_sha256"),
        "logic_changes": receipt.get("logic_changes"),
        "stub_generated": receipt.get("stub_generated"),
    }


def verify_research_seed_acquisition(acquisition: Mapping[str, Any]) -> dict[str, Any]:
    """Recompute a positive RC1 pair from staged inputs and raw audits."""
    if not isinstance(acquisition, Mapping):
        raise ResearchSeedScopedError("research seed acquisition must be an object")
    required = {
        "version", "before_project", "after_project", "before_audit",
        "after_audit", "lineage_id", "source_group", "config_edits",
        "expected_toolchain_manifest_digest",
    }
    allowed = required | {"role"}
    if set(acquisition) not in (required, allowed):
        raise ResearchSeedScopedError("research seed acquisition fields are invalid")
    if acquisition.get("version") != ACQUISITION_VERSION:
        raise ResearchSeedScopedError("research seed acquisition version is invalid")
    lineage = acquisition.get("lineage_id")
    source_group = acquisition.get("source_group")
    if (not isinstance(lineage, str) or not lineage.strip()
            or not isinstance(source_group, str) or not source_group.strip()):
        raise ResearchSeedScopedError("research seed lineage and source group are required")
    role = acquisition.get("role", "treatment")
    if role not in {"control", "treatment"}:
        raise ResearchSeedScopedError("research seed role is invalid")
    if acquisition.get("config_edits") != {"CORE_UTILIZATION": "40"}:
        raise ResearchSeedScopedError("research seed action is not the frozen density relief")
    expected_toolchain = acquisition.get("expected_toolchain_manifest_digest")
    if not isinstance(expected_toolchain, str) or not expected_toolchain:
        raise ResearchSeedScopedError("research seed toolchain digest is required")

    before = _path(acquisition.get("before_project"), "before project")
    after = _path(acquisition.get("after_project"), "after project")
    before_audit = _path(acquisition.get("before_audit"), "before audit")
    after_audit = _path(acquisition.get("after_audit"), "after audit")
    if before == after or before_audit == after_audit:
        raise ResearchSeedScopedError("research seed arms must be distinct")
    checked_projects = [verify_staged_flow_project(project)
                        for project in (before, after)]
    receipts = [_json(project / "stage-receipt.json") for project in (before, after)]
    if any(check.get("valid") is not True for check in checked_projects):
        raise ResearchSeedScopedError("research seed staged project is invalid")
    if any(check.get("receipt_digest") != receipt.get("receipt_digest")
           for check, receipt in zip(checked_projects, receipts)):
        raise ResearchSeedScopedError("research seed stage receipt binding changed")
    if _source_identity(receipts[0]) != _source_identity(receipts[1]):
        raise ResearchSeedScopedError("research seed arms differ outside the action")
    if (receipts[0].get("platform") != "sky130hs"
            or receipts[0].get("declared_overrides") != {"CORE_UTILIZATION": "95"}
            or receipts[1].get("declared_overrides") != {"CORE_UTILIZATION": "40"}
            or receipts[0].get("logic_changes") != []
            or receipts[0].get("stub_generated") is not False):
        raise ResearchSeedScopedError("research seed staged action contract changed")

    raw_audits = [
        _audit(before_audit, before, FLOW_CONTRACT["accepted_control_failure"]),
        _audit(after_audit, after, FLOW_CONTRACT["accepted_treatment"]),
    ]
    if any(raw.get("design_id") != receipts[0].get("design_id")
           or raw.get("stage_receipt_digest") != receipt.get("receipt_digest")
           or raw.get("toolchain_manifest_digest") != expected_toolchain
           for raw, receipt in zip(raw_audits, receipts)):
        raise ResearchSeedScopedError("research seed audit input or toolchain binding changed")
    if raw_audits[0].get("run_tag") == raw_audits[1].get("run_tag"):
        raise ResearchSeedScopedError("research seed arms share a run witness")

    configs = [parse_config_mk((project / "constraints/config.mk").read_text())
               for project in (before, after)]
    normalized = []
    for project, config in zip((before, after), configs):
        observed = dict(config)
        observed["VERILOG_FILES"] = [
            _file_digest(Path(item).resolve()) for item in config["VERILOG_FILES"].split()]
        observed["SDC_FILE"] = _file_digest(Path(config["SDC_FILE"]).resolve())
        observed["wrapper_local_sdc"] = _file_digest(
            project / "constraints/constraint.sdc")
        normalized.append(observed)
    normalized[0]["CORE_UTILIZATION"] = "40"
    if normalized[0] != normalized[1]:
        raise ResearchSeedScopedError("research seed arms have undeclared input differences")

    contract_digest = _digest(FLOW_CONTRACT)
    sides = {}
    for label, raw in zip(("before", "after"), raw_audits):
        sides[label] = {
            "contract": copy.deepcopy(FLOW_CONTRACT),
            "contract_digest": contract_digest,
            "verdict": raw["oracle_verdict"],
            "oracle_reason": raw["oracle_reason"],
            "failure_layer": raw["failure_layer"],
            "run_tag": raw["run_tag"],
            "audit_digest": raw["audit_digest"],
            "stage_receipt_digest": raw["stage_receipt_digest"],
            "raw_artifacts_digest": _digest(raw["raw_artifacts"]),
            "downstream_checks": {
                "drc": "UNKNOWN", "lvs": "UNKNOWN", "final_timing": "UNKNOWN"},
        }
    pair = {
        "contract": copy.deepcopy(FLOW_CONTRACT),
        "contract_digest": contract_digest,
        "before": sides["before"], "after": sides["after"],
        "controlled_measurement_valid": True,
        "learner_admission": False,
        "signoff_claim": False,
    }
    pair["receipt_digest"] = _digest(pair)
    normalized_acquisition = dict(acquisition)
    normalized_acquisition["role"] = role
    return {
        "valid": True,
        "acquisition_digest": _digest(normalized_acquisition),
        "lineage_id": lineage.strip(), "source_group": source_group.strip(),
        "role": role, "design_id": receipts[0]["design_id"],
        "projects": {"before": str(before), "after": str(after)},
        "audit_paths": {"before": str(before_audit), "after": str(after_audit)},
        "audits": {"before": raw_audits[0], "after": raw_audits[1]},
        "configs": {"before": configs[0], "after": configs[1]},
        "pair_receipt": pair,
        "expected_toolchain_manifest_digest": expected_toolchain,
    }


def build_research_seed_record(acquisition: Mapping[str, Any]) -> ExecutionRecord:
    """Build one canonical control or treatment record from an RC1 pair."""
    checked = verify_research_seed_acquisition(acquisition)
    role = checked["role"]
    pair = checked["pair_receipt"]
    states = []
    refs_by_side: dict[str, list[str]] = {}
    for side in ("before", "after"):
        raw = checked["audits"][side]
        audit_path = Path(checked["audit_paths"][side])
        side_refs = [str(audit_path / "flow-audit.json"),
                     str(audit_path / "artifact-manifest.json")]
        side_refs.extend(str(item["path"]) for item in raw["raw_artifacts"])
        refs_by_side[side] = side_refs
        states.append({
            "config": checked["configs"][side],
            "reports": {"flow_feasibility": {
                "scope": FLOW_CONTRACT["scope"],
                "verdict": raw["oracle_verdict"],
                "oracle_reason": raw["oracle_reason"],
                "failure_layer": raw["failure_layer"],
                "provenance": {"run_tag": raw["run_tag"]},
            }},
            "artifacts": {"flow_feasibility": {
                "project": checked["projects"][side],
                "audit_path": checked["audit_paths"][side],
                "audit_digest": raw["audit_digest"],
                "stage_receipt_digest": raw["stage_receipt_digest"],
                "run_tag": raw["run_tag"],
            }},
        })
    original = "REMOVED"
    verdict = "PASS"
    action = {
        "domain": "flow.CONFIG_DELTA",
        "transformation_family": "DENSITY_RELIEF",
        "payload": {
            "config_edits": {"CORE_UTILIZATION": "40"},
            "recheck": FLOW_CONTRACT["scope"],
            "measurement_contract_digest": pair["contract_digest"],
        },
    }
    scoped = {
        "version": SCOPED_VERSION,
        "role": "after" if role == "treatment" else "before",
        "before_project": checked["projects"]["before"],
        "after_project": checked["projects"]["after"],
        "before_audit": checked["audit_paths"]["before"],
        "after_audit": checked["audit_paths"]["after"],
        "source_group": checked["source_group"],
        "expected_manifest_digest": checked["expected_toolchain_manifest_digest"],
        "acquisition_digest": checked["acquisition_digest"],
        "pair_receipt": pair,
    }
    refs = refs_by_side["before"] + refs_by_side["after"]
    if role == "control":
        states[1] = copy.deepcopy(states[0])
        refs = refs_by_side["before"]
        original, verdict = "PRESENT", "FAIL"
        action = {
            "domain": "flow.BASELINE_CONTROL",
            "transformation_family": "DENSITY_RELIEF",
            "payload": {
                "config_edits": {}, "control": True, "observation_only": True,
                "recheck": FLOW_CONTRACT["scope"],
                "measurement_contract_digest": pair["contract_digest"],
            },
        }
    identity = _digest({"scoped_execution": scoped,
                        "lineage_id": checked["lineage_id"]})
    record = ExecutionRecord(
        record_id="orfs-rc1-seed:" + identity.removeprefix("sha256:"),
        domain="flow.signoff", project_id=checked["lineage_id"],
        design_id=checked["design_id"], lineage_id=checked["lineage_id"],
        before=states[0], after=states[1], action=action,
        observation_delta={
            "original_failure": original,
            "failing_tests": {"before": 1, "after": 0 if verdict == "PASS" else 1},
            "created_regressions": [], "newly_observed_failures": [],
            "experiment_kind": "REPAIR" if role == "treatment" else "OBSERVATION",
            "utility_verdict": "UNKNOWN",
        },
        verification={
            "verdict": verdict, "oracle_type": "TARGET_TEST",
            "scope": FLOW_CONTRACT["scope"], "confidence_tier": "T",
            "oracle_complete": True, "obligation_coverage": 1.0,
            "evidence_refs": sorted(set(refs)), "scoped_execution": scoped,
        },
    )
    record.validate()
    return record


def replay_research_seed_record(record: ExecutionRecord) -> dict[str, Any]:
    """Rebuild a record from its frozen audit paths and reject substitutions."""
    record.validate()
    scoped = record.verification.get("scoped_execution")
    if not isinstance(scoped, Mapping) or scoped.get("version") != SCOPED_VERSION:
        raise ResearchSeedScopedError("unsupported RC1 scoped execution record")
    acquisition = {
        "version": ACQUISITION_VERSION,
        "before_project": scoped.get("before_project"),
        "after_project": scoped.get("after_project"),
        "before_audit": scoped.get("before_audit"),
        "after_audit": scoped.get("after_audit"),
        "lineage_id": record.lineage_id,
        "source_group": scoped.get("source_group"),
        "config_edits": {"CORE_UTILIZATION": "40"},
        "expected_toolchain_manifest_digest": scoped.get("expected_manifest_digest"),
        "role": "control" if scoped.get("role") == "before" else "treatment",
    }
    expected = build_research_seed_record(acquisition)
    if asdict(record) != asdict(expected):
        raise ResearchSeedScopedError("RC1 seed record differs from replayed acquisition")
    return expected.verification["scoped_execution"]["pair_receipt"]


def replay_persisted_research_seed(
    conn: sqlite3.Connection, transition_id: str, *, acquisition: Mapping[str, Any],
) -> dict[str, Any]:
    """Independently reconstruct and compare one persisted RC1 transition."""
    from tehm.causal.mechanism import load_transition_facts

    facts = load_transition_facts(conn, transition_id)
    scoped = facts.verifier.get("scoped_execution")
    if not isinstance(scoped, Mapping) or scoped.get("version") != SCOPED_VERSION:
        raise ResearchSeedScopedError("transition has no RC1 seed scoped execution")
    record = build_research_seed_record(acquisition)
    _compare_persisted_flow_record(conn, transition_id, record)
    return {
        "version": "orfs-rc1-seed-persisted-replay-v1",
        "transition_id": transition_id,
        "acquisition_digest": verify_research_seed_acquisition(
            acquisition)["acquisition_digest"],
        "persisted_binding_verified": True,
        "pair_receipt": record.verification["scoped_execution"]["pair_receipt"],
        "learner_admission": False,
        "promotion_attempted": False,
    }


__all__ = [
    "ACQUISITION_VERSION", "SCOPED_VERSION", "FLOW_CONTRACT",
    "ResearchSeedScopedError", "verify_research_seed_acquisition",
    "build_research_seed_record", "replay_research_seed_record",
    "replay_persisted_research_seed",
]
