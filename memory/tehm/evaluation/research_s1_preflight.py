"""Read-only Revision4 S1 query, route, selector and binder preflight.

Scoped training witnesses cannot be trusted from a file-backed M0 directly.
This module verifies the exported bundle, copies its SQLite into isolated RAM,
and replays the frozen parent acquisitions under the existing learner gate.
The output is advisory; it never stages or executes a treatment arm.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from contracts import MemoryQuery, RepairContext
from tehm import db
from tehm.retrieval.asset_selector import select_knowledge_grounded_assets
from tehm.retrieval.memory_router import route_memory
from tehm.retrieval.query_planner import plan_query
from tehm.retrieval.structured_candidate import build_structured_candidate
from tehm.sync import verify_bundle
from tehm.verified_execution import scoped_learning_replay

from .research_epoch import verify_research_epoch
from .research_flow import verify_flow_audit
from .research_s1_control import (
    _digest, _file_sha, _load, verify_s1_control_binding, verify_s1_controls,
)


PREFLIGHT_SCHEMA = "tehm-r4-s1-scoped-candidate-preflight-v1"
_DENSITY_FAILURE = ("FAIL", "FLOW_TARGET_FAILURE", "place",
                    "placement_density_infeasible")


class ResearchS1PreflightError(ValueError):
    """An S1 target or scoped M0 replay cannot be trusted."""


def _frozen_code(epoch: Path) -> None:
    binding = _load(epoch / "bindings/oracle-binding.json")
    files = binding.get("files")
    if not isinstance(files, list):
        raise ResearchS1PreflightError("oracle binding is malformed")
    index = {str(item.get("source_path")): item.get("sha256") for item in files
             if isinstance(item, dict)}
    required = (Path(__file__).resolve(),
                Path(__file__).with_name("research_s1_control.py").resolve(),
                Path(__file__).parents[1] / "retrieval/query_planner.py",
                Path(__file__).parents[1] / "retrieval/memory_router.py",
                Path(__file__).parents[1] / "retrieval/asset_selector.py",
                Path(__file__).parents[1] / "retrieval/structured_candidate.py",
                Path(__file__).parents[1] / "verified_execution.py")
    for path in required:
        if index.get(str(path.resolve())) != "sha256:" + _file_sha(path):
            raise ResearchS1PreflightError(f"preflight code differs from frozen epoch: {path}")


def _observed_utilization(project: Path) -> str:
    config = (project / "constraints/config.mk").read_text(encoding="utf-8")
    values = re.findall(r"^export CORE_UTILIZATION\s*=\s*(\S+)\s*$", config, re.M)
    if len(values) != 1 or values[0] != "95":
        raise ResearchS1PreflightError("target config lacks one observed u95 control")
    return values[0]


def _report(*, binding: Path, controls: Path, epoch: Path) -> dict[str, Any]:
    checked_binding = verify_s1_control_binding(binding, allow_executed=True)
    checked_controls = verify_s1_controls(binding=binding, output=controls)
    if not checked_controls.get("valid") or not checked_controls.get("all_registered_terminal"):
        raise ResearchS1PreflightError("registered S1 controls are not fully audited")
    checked_epoch = verify_research_epoch(epoch)
    if not checked_epoch.get("valid") or not checked_epoch.get("research_evaluation_ready"):
        raise ResearchS1PreflightError("preflight epoch is not ready")
    _frozen_code(epoch)
    if (checked_epoch.get("memory_bundle_digest") !=
            verify_research_epoch(checked_binding["epoch"])["memory_bundle_digest"]):
        raise ResearchS1PreflightError("preflight M0 differs from control M0")
    frozen = _load(epoch / "research-epoch.json")
    bundle = Path(frozen["memory_snapshot"]["bundle_path"]).resolve()
    if not verify_bundle(bundle).get("ok"):
        raise ResearchS1PreflightError("frozen M0 bundle verification failed")
    database = bundle / "closed_loop/tehm.sqlite"
    before = "sha256:" + _file_sha(database)
    acquisitions = _load(bundle / "research/parent-acquisitions.json")
    m0_report = _load(bundle / "research/m0-build-report.json")
    if (acquisitions.get("schema") != "tehm-r4-research-seed-parent-acquisitions-v1"
            or acquisitions.get("digest") != m0_report.get("parent_acquisition_digest")
            or acquisitions.get("campaign_id") != m0_report.get("campaign_id")
            or not isinstance(acquisitions.get("acquisitions"), dict)):
        raise ResearchS1PreflightError("M0 parent acquisition binding is invalid")
    spec = _load(Path(_load(binding)["task_spec"]))
    if (spec.get("target_scope") != "flow_feasibility" or
            spec.get("measurement_contract_digest") !=
            "sha256:e5817134d2e2bc8e7f674854e199a31d73bd7091d8ccf7991733399c4d0e0c88"
            or spec.get("budget", {}).get("candidate_limit") != 3
            or spec.get("budget", {}).get("memory_advisor_limit") != 1):
        raise ResearchS1PreflightError("S1 target measurement contract differs from M0")
    source = db.connect_read_only(database)
    memory = sqlite3.connect(":memory:")
    memory.row_factory = sqlite3.Row
    try:
        source.backup(memory)
    finally:
        source.close()
    rows = []
    try:
        with scoped_learning_replay(
                memory, campaign_id=acquisitions["campaign_id"],
                acquisitions=acquisitions["acquisitions"],
                expected_digest=acquisitions["digest"]):
            for outcome, registered in zip(checked_controls["outcomes"],
                                           checked_binding["tasks"], strict=True):
                if outcome["design_id"] != registered["design_id"]:
                    raise ResearchS1PreflightError("S1 control denominator order drifted")
                audit_root = Path(outcome["audit_path"])
                checked_audit = verify_flow_audit(audit_root)
                audit = _load(audit_root / "flow-audit.json")
                project = Path(registered["project"])
                if (not checked_audit.get("valid") or
                        audit.get("project_path") != str(project) or
                        audit.get("stage_receipt_digest") != registered["receipt_digest"]):
                    raise ResearchS1PreflightError("control raw audit changed")
                signature = (checked_audit["oracle_verdict"],
                             checked_audit["failure_layer"],
                             checked_audit["terminal_stage"],
                             checked_audit["oracle_reason"])
                row: dict[str, Any] = {
                    "design_id": registered["design_id"],
                    "source_group": registered["source_group"],
                    "control_audit_path": str(audit_root),
                    "control_audit_digest": checked_audit["audit_digest"],
                    "control_verdict": checked_audit["oracle_verdict"],
                    "control_reason": checked_audit["oracle_reason"],
                    "target_failure": signature == _DENSITY_FAILURE,
                    "query": None, "routing": None, "selection": None,
                    "runtime_binding": None, "candidate": None,
                }
                if signature == _DENSITY_FAILURE:
                    observed = _observed_utilization(project)
                    context = RepairContext(
                        design_id=registered["design_id"], platform="sky130hs",
                        check="place", project_dir=project,
                        reports={"fixed_flow_audit": {
                            "audit_digest": checked_audit["audit_digest"],
                            "oracle_verdict": checked_audit["oracle_verdict"],
                            "oracle_reason": checked_audit["oracle_reason"],
                            "failure_layer": checked_audit["failure_layer"],
                            "terminal_stage": checked_audit["terminal_stage"],
                        }},
                        symptom_signature={"check": "place",
                                           "reason": checked_audit["oracle_reason"],
                                           "failure_layer": checked_audit["failure_layer"]},
                        mechanism_signature={"mechanism_family": "DENSITY_RELIEF",
                                             "transformation_family": "DENSITY_RELIEF"},
                    )
                    planned = plan_query(context)
                    query = MemoryQuery(
                        query_plan={**planned.query_plan,
                                    "target_scope": spec["target_scope"],
                                    "measurement_contract_digest":
                                    spec["measurement_contract_digest"],
                                    "flow_design_id": registered["design_id"],
                                    "flow_config": {"CORE_UTILIZATION": observed}},
                        dominant_dimensions=planned.dominant_dimensions,
                        context_ref=planned.context_ref)
                    routing = route_memory(
                        memory, query, no_memory_budget=2, memory_budget=1,
                        mode="shadow", persist_state=False, commit=False)
                    selection = select_knowledge_grounded_assets(
                        memory, query, routing=routing)
                    row["query"] = query.to_dict()
                    row["routing"] = {**routing.to_dict(),
                                      "decision_digest": routing.decision_digest}
                    row["selection"] = {**selection.receipt.to_dict(),
                                        "receipt_digest": selection.receipt.receipt_digest}
                    if selection.receipt.decision == "SELECT":
                        runtime_binding = selection.metadata.get("runtime_binding")
                        candidate = build_structured_candidate(
                            query, routing, selection, runtime_binding)
                        action = candidate.concrete_action
                        edits = (action.get("payload") or {}).get("config_edits")
                        if (action.get("domain") != spec["memory_arm"]["permitted_action_family"]
                                or not isinstance(edits, dict)
                                or set(edits) != {"CORE_UTILIZATION"}
                                or candidate.evaluation_only is not True):
                            raise ResearchS1PreflightError("selected action violates task contract")
                        row["runtime_binding"] = runtime_binding
                        row["candidate"] = candidate.to_dict()
                rows.append(row)
    finally:
        memory.close()
    after = "sha256:" + _file_sha(database)
    if before != after or any(database.with_name(database.name + suffix).exists()
                              for suffix in ("-wal", "-shm")):
        raise ResearchS1PreflightError("frozen M0 changed during scoped preflight")
    if not verify_research_epoch(epoch).get("valid"):
        raise ResearchS1PreflightError("preflight epoch drifted")
    payload = {
        "schema": PREFLIGHT_SCHEMA, "campaign_id": spec["campaign_id"],
        "task_spec_sha256": _load(binding)["task_spec_sha256"],
        "control_binding_path": str(binding), "controls_path": str(controls),
        "control_binding_digest": checked_binding["binding_digest"],
        "control_event_tail_digest": checked_controls["event_tail_digest"],
        "epoch_path": str(epoch), "epoch_digest": checked_epoch["epoch_digest"],
        "memory_bundle_digest": checked_epoch["memory_bundle_digest"],
        "memory_db_sha256_before": before, "memory_db_sha256_after": after,
        "denominator": {"registered_tasks": len(rows),
                        "target_failures": sum(row["target_failure"] for row in rows),
                        "route_selected": sum(row["routing"] is not None and
                                              row["routing"]["decision"] in {"APPLY", "CONSIDER"}
                                              for row in rows),
                        "asset_selected": sum(row["selection"] is not None and
                                              row["selection"]["decision"] == "SELECT"
                                              for row in rows),
                        "candidate_built": sum(row["candidate"] is not None for row in rows)},
        "rows": rows,
        "cost": {"eda_calls": 0, "model_calls": 0, "scoped_replay_passes": 1},
        "authority": {"shadow_only": True, "action_executed": False,
                      "memory_update": "none", "production_authority": False},
        "claim_boundary": "Scoped read-only S1 candidate preflight only; no selected treatment execution or action effect.",
    }
    payload["preflight_digest"] = _digest(payload)
    return payload


def audit_s1_candidate_preflight(*, binding: str | Path, controls: str | Path,
                                 epoch: str | Path, output: str | Path) -> dict[str, Any]:
    destination = Path(output).expanduser().resolve()
    if destination.exists():
        raise ResearchS1PreflightError("refusing to overwrite S1 preflight")
    payload = _report(binding=Path(binding).expanduser().resolve(),
                      controls=Path(controls).expanduser().resolve(),
                      epoch=Path(epoch).expanduser().resolve())
    destination.mkdir(parents=True)
    temporary = destination / f"preflight.json.tmp.{os.getpid()}"
    temporary.write_bytes(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode() + b"\n")
    temporary.replace(destination / "preflight.json")
    return {"valid": True, "preflight_digest": payload["preflight_digest"],
            "denominator": payload["denominator"], "output": str(destination)}


def verify_s1_candidate_preflight(preflight: str | Path) -> dict[str, Any]:
    root = Path(preflight).expanduser().resolve()
    saved = _load(root / "preflight.json")
    if saved.get("schema") != PREFLIGHT_SCHEMA:
        raise ResearchS1PreflightError("S1 preflight schema mismatch")
    expected = _digest({key: value for key, value in saved.items()
                        if key != "preflight_digest"})
    if saved.get("preflight_digest") != expected:
        raise ResearchS1PreflightError("S1 preflight digest mismatch")
    recomputed = _report(binding=Path(saved["control_binding_path"]),
                         controls=Path(saved["controls_path"]),
                         epoch=Path(saved["epoch_path"]))
    if recomputed != saved:
        raise ResearchS1PreflightError("S1 preflight replay differs from saved report")
    return {"valid": True, "preflight_digest": expected,
            "denominator": saved["denominator"], "output": str(root)}
