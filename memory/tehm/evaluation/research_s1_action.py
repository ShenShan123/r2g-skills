"""Prepare isolated S1 treatments strictly from verified TEHM candidate receipts.

This is a planning boundary only. The caller must execute and independently
audit each staged arm separately before making an action-effect claim.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .research_epoch import verify_research_epoch
from .research_flow import stage_flow_project, verify_staged_flow_project
from .research_inventory import (
    bind_research_inventory_adapters, verify_research_inventory,
)
from .research_s1_control import (
    _digest, _file_sha, _load, verify_s1_control_binding,
)
from .research_s1_preflight import verify_s1_candidate_preflight


PLAN_SCHEMA = "tehm-r4-s1-selected-treatment-plan-v1"


class ResearchS1ActionError(ValueError):
    """A proposed S1 treatment does not follow the frozen TEHM selection."""


def _inputs(preflight: Path, epoch: Path) -> tuple[dict[str, Any], dict[str, Any],
                                                   dict[str, Any], dict[str, Any]]:
    checked = verify_s1_candidate_preflight(preflight)
    saved = _load(preflight / "preflight.json")
    if not checked.get("valid") or checked.get("preflight_digest") != saved.get(
            "preflight_digest"):
        raise ResearchS1ActionError("S1 candidate preflight is not verified")
    checked_epoch = verify_research_epoch(epoch)
    if (not checked_epoch.get("valid") or
            not checked_epoch.get("research_evaluation_ready") or
            checked_epoch.get("memory_bundle_digest") != saved.get("memory_bundle_digest")):
        raise ResearchS1ActionError("action-planning epoch or M0 differs from preflight")
    oracle = _load(epoch / "bindings/oracle-binding.json")
    path = Path(__file__).resolve()
    indexed = {str(row.get("source_path")): row.get("sha256")
               for row in oracle.get("files", []) if isinstance(row, dict)}
    if indexed.get(str(path)) != "sha256:" + _file_sha(path):
        raise ResearchS1ActionError("action planner differs from frozen oracle")
    binding_path = Path(saved["control_binding_path"])
    binding = _load(binding_path)
    verified_binding = verify_s1_control_binding(binding_path, allow_executed=True)
    task = _load(Path(binding["task_spec"]))
    if (saved["control_binding_digest"] != verified_binding["binding_digest"] or
            saved["task_spec_sha256"] != binding["task_spec_sha256"] or
            len(saved.get("rows", [])) != len(verified_binding["tasks"])):
        raise ResearchS1ActionError("preflight task denominator changed")
    return saved, task, binding, checked_epoch


def _selected(row: dict[str, Any], task: dict[str, Any]) -> tuple[dict[str, str], str] | None:
    candidate = row.get("candidate")
    if candidate is None:
        return None
    if (not row.get("target_failure") or
            (row.get("routing") or {}).get("decision") not in {"APPLY", "CONSIDER"} or
            (row.get("selection") or {}).get("decision") != "SELECT" or
            not isinstance(candidate, dict) or candidate.get("evaluation_only") is not True):
        raise ResearchS1ActionError("candidate lacks route, selection, or target failure")
    action = candidate.get("concrete_action") or {}
    edits = (action.get("payload") or {}).get("config_edits")
    if (action.get("domain") != task["memory_arm"]["permitted_action_family"]
            or type(edits) is not dict or not edits
            or set(edits) - set(task["memory_arm"]["permitted_config_keys"])):
        raise ResearchS1ActionError("candidate action differs from preregistered key contract")
    if any(type(value) is not str or not value for value in edits.values()):
        raise ResearchS1ActionError("candidate edit value is invalid")
    digest = candidate.get("candidate_digest")
    if type(digest) is not str or not digest.startswith("sha256:"):
        raise ResearchS1ActionError("candidate content digest is missing")
    return edits, digest


def _expected_adapter(source: dict[str, Any], selected: list[dict[str, Any]]) -> dict:
    spec = copy.deepcopy(source)
    spec["adapter_id"] = "r4-s1-tehm-selected-treatment-v1"
    indexed = {row.get("design_id"): row for row in source.get("designs", [])
               if isinstance(row, dict)}
    designs = []
    for item in selected:
        design = item["design_id"]
        if design not in indexed:
            raise ResearchS1ActionError("selected design is absent from control adapter")
        row = copy.deepcopy(indexed[design])
        flow = row["flow_binding"]
        if flow.get("overrides") != {"CORE_UTILIZATION": "95"}:
            raise ResearchS1ActionError("control adapter no longer specifies u95")
        flow["overrides"] = dict(item["config_edits"])
        flow["authority_note"] = (
            "Pilot-development treatment derived only from verified TEHM candidate "
            + item["candidate_digest"] + "; no RTL or SDC edit.")
        designs.append(row)
    spec["designs"] = designs
    return spec


def _pair_invariants(control: Path, treatment: Path, edits: dict[str, str]) -> tuple[str, str]:
    original = _load(control / "stage-receipt.json")
    selected = _load(treatment / "stage-receipt.json")
    for key in ("design_id", "platform", "top_module", "source_files",
                "source_bundle_digest", "config_template", "sdc_template",
                "authority_checkout", "logic_changes", "stub_generated"):
        # Staged source paths differ by isolated workspace, but their content
        # identity and file order must agree.
        if key == "source_files":
            left = [(row["source_path"], row["sha256"], row["bytes"])
                    for row in original[key]]
            right = [(row["source_path"], row["sha256"], row["bytes"])
                     for row in selected[key]]
        else:
            left, right = original.get(key), selected.get(key)
        if left != right:
            raise ResearchS1ActionError(f"treatment differs from control at {key}")
    if (original.get("declared_overrides") != {"CORE_UTILIZATION": "95"}
            or selected.get("declared_overrides") != edits):
        raise ResearchS1ActionError("only binder-selected config edits are permitted")
    if not verify_staged_flow_project(control).get("valid") or not verify_staged_flow_project(
            treatment).get("valid"):
        raise ResearchS1ActionError("control or treatment staging drifted")
    return original["receipt_digest"], selected["receipt_digest"]


def prepare_s1_treatments(*, preflight: str | Path, epoch: str | Path,
                          output: str | Path) -> dict[str, Any]:
    """Materialize source-identical projects with only binder-selected edits."""
    root = Path(output).expanduser().resolve()
    if root.exists():
        raise ResearchS1ActionError("refusing to overwrite S1 treatment plan")
    preflight_path = Path(preflight).expanduser().resolve()
    epoch_path = Path(epoch).expanduser().resolve()
    saved, task, binding, checked_epoch = _inputs(preflight_path, epoch_path)
    control_rows = {row["design_id"]: row for row in binding["control_projects"]}
    selected = []
    rejected = []
    for row in saved["rows"]:
        value = _selected(row, task)
        if value is None:
            rejected.append({"design_id": row["design_id"],
                             "route_decision": (row.get("routing") or {}).get("decision"),
                             "selection_decision": (row.get("selection") or {}).get("decision")})
            continue
        edits, digest = value
        selected.append({"design_id": row["design_id"],
                         "source_group": row["source_group"],
                         "candidate_digest": digest,
                         "config_edits": edits,
                         "control_project": control_rows[row["design_id"]]["project"],
                         "control_audit_digest": row["control_audit_digest"]})
    if not selected:
        raise ResearchS1ActionError("no legal TEHM candidate to stage; retain controls")
    control_adapter = _load(preflight_path.parent.parent / "preregistration/control-adapter-spec-v1.json")
    expected = _expected_adapter(control_adapter, selected)
    control_inventory = Path(binding["control_inventory"])
    source_inventory = Path(_load(control_inventory / "bindings/source-inventory.json")["path"])
    root.mkdir(parents=True)
    adapter_path = root / "selected-adapter-spec.json"
    adapter_path.write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n",
                            encoding="utf-8")
    inventory_path = root / "inventory"
    adapter_result = bind_research_inventory_adapters(
        inventory=source_inventory, adapter_spec=adapter_path,
        authority_root=Path(binding["orfs_root"]), output=inventory_path)
    if not adapter_result.get("valid") or not adapter_result.get("corpus_unchanged"):
        raise ResearchS1ActionError("selected adapter failed source-bound verification")
    for item in selected:
        project = root / "projects" / item["design_id"]
        stage_flow_project(inventory=inventory_path,
                           design_id=item["design_id"], output=project)
        original_digest, treatment_digest = _pair_invariants(
            Path(item["control_project"]), project, item["config_edits"])
        item["control_stage_receipt_digest"] = original_digest
        item["treatment_project"] = str(project)
        item["treatment_stage_receipt_digest"] = treatment_digest
    payload = {
        "schema": PLAN_SCHEMA, "campaign_id": task["campaign_id"],
        "preflight_path": str(preflight_path),
        "preflight_digest": saved["preflight_digest"],
        "epoch_path": str(epoch_path), "epoch_digest": checked_epoch["epoch_digest"],
        "memory_bundle_digest": checked_epoch["memory_bundle_digest"],
        "task_spec_sha256": binding["task_spec_sha256"],
        "selected_adapter_spec_sha256": _file_sha(adapter_path),
        "selected_inventory_digest": adapter_result["inventory_digest"],
        "registered_tasks": len(saved["rows"]),
        "selected_treatments": selected, "no_reuse": rejected,
        "authority": {"candidate_source": "verified_TEHM_route_selector_binder",
                      "action_executed": False, "memory_update": "none",
                      "production_authority": False},
        "claim_boundary": "Isolated candidate-derived staging only; no treatment execution or S1 effect.",
    }
    payload["plan_digest"] = _digest(payload)
    (root / "treatment-plan.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"valid": True, "plan_digest": payload["plan_digest"],
            "registered_tasks": payload["registered_tasks"],
            "selected_treatments": len(selected), "output": str(root)}


def verify_s1_treatment_plan(plan: str | Path, *,
                             allow_executed: bool = False) -> dict[str, Any]:
    """Recheck preflight candidate digests and all source/SDC stage invariants."""
    root = Path(plan).expanduser().resolve()
    saved = _load(root / "treatment-plan.json")
    if saved.get("schema") != PLAN_SCHEMA or saved.get("plan_digest") != _digest({
            key: value for key, value in saved.items() if key != "plan_digest"}):
        raise ResearchS1ActionError("S1 treatment plan digest or schema mismatch")
    preflight = Path(saved["preflight_path"])
    epoch = Path(saved["epoch_path"])
    source, task, binding, checked_epoch = _inputs(preflight, epoch)
    if (source["preflight_digest"] != saved["preflight_digest"] or
            checked_epoch["epoch_digest"] != saved["epoch_digest"] or
            checked_epoch["memory_bundle_digest"] != saved["memory_bundle_digest"] or
            binding["task_spec_sha256"] != saved["task_spec_sha256"] or
            saved["registered_tasks"] != len(source["rows"])):
        raise ResearchS1ActionError("selected plan input binding drifted")
    expected_selected = []
    expected_rejected = []
    control_rows = {row["design_id"]: row for row in binding["control_projects"]}
    for row in source["rows"]:
        value = _selected(row, task)
        if value is None:
            expected_rejected.append({"design_id": row["design_id"],
                                      "route_decision": (row.get("routing") or {}).get("decision"),
                                      "selection_decision": (row.get("selection") or {}).get("decision")})
            continue
        edits, digest = value
        expected_selected.append({"design_id": row["design_id"],
                                  "source_group": row["source_group"],
                                  "candidate_digest": digest,
                                  "config_edits": edits,
                                  "control_project": control_rows[row["design_id"]]["project"],
                                  "control_audit_digest": row["control_audit_digest"]})
    if saved["no_reuse"] != expected_rejected or len(saved["selected_treatments"]) != len(
            expected_selected):
        raise ResearchS1ActionError("selected/rejected denominator changed")
    adapter_path = root / "selected-adapter-spec.json"
    control_adapter = _load(preflight.parent.parent / "preregistration/control-adapter-spec-v1.json")
    if (_load(adapter_path) != _expected_adapter(control_adapter, expected_selected)
            or saved["selected_adapter_spec_sha256"] != _file_sha(adapter_path)):
        raise ResearchS1ActionError("selected adapter differs from candidates")
    inventory = verify_research_inventory(root / "inventory")
    if (not inventory.get("valid") or
            inventory["inventory_digest"] != saved["selected_inventory_digest"]):
        raise ResearchS1ActionError("selected inventory drifted")
    for expected, actual in zip(expected_selected, saved["selected_treatments"], strict=True):
        project = root / "projects" / expected["design_id"]
        control = Path(expected["control_project"])
        original_digest, treatment_digest = _pair_invariants(
            control, project, expected["config_edits"])
        if (actual != {**expected,
                       "control_stage_receipt_digest": original_digest,
                       "treatment_project": str(project),
                       "treatment_stage_receipt_digest": treatment_digest}):
            raise ResearchS1ActionError("treatment project does not match candidate")
        if not allow_executed and any((project / "backend").glob("RUN_*")):
            raise ResearchS1ActionError("treatment project already executed")
    return {"valid": True, "plan_digest": saved["plan_digest"],
            "registered_tasks": saved["registered_tasks"],
            "selected_treatments": len(expected_selected), "output": str(root)}
