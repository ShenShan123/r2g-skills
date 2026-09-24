"""Stage every S2 policy candidate in a distinct source-bound project.

This prepares the full Pilot-development denominator but executes no EDA.
The shared S1 controls remain feedback, not No Memory Agent outcomes.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .research_epoch import verify_research_epoch
from .research_flow import stage_flow_project, verify_staged_flow_project
from .research_inventory import bind_research_inventory_adapters, verify_research_inventory
from .research_s1_action import _pair_invariants
from .research_s1_control import _digest, _file_sha, _load
from .research_s2_proposal import POLICIES, verify_s2_proposals


PLAN_SCHEMA = "tehm-r4-s2-three-policy-stage-plan-v1"


class ResearchS2ActionError(ValueError):
    """An S2 candidate stage no longer matches the frozen policy proposal."""


def _inputs(proposals: Path, epoch: Path) -> tuple[dict[str, Any], dict[str, Any],
                                                   dict[str, Any], Path]:
    checked = verify_s2_proposals(proposals)
    source = _load(proposals / "proposals.json")
    if checked.get("proposal_digest") != source.get("proposal_digest"):
        raise ResearchS2ActionError("S2 proposals failed independent replay")
    frozen = verify_research_epoch(epoch)
    if not frozen.get("valid") or not frozen.get("research_evaluation_ready"):
        raise ResearchS2ActionError("S2 stage epoch is not evaluation ready")
    preflight = _load(Path(source["preflight_path"]) / "preflight.json")
    if frozen.get("memory_bundle_digest") != preflight.get("memory_bundle_digest"):
        raise ResearchS2ActionError("S2 M0 differs from scoped S1 preflight")
    epoch_root = Path(epoch)
    oracle = _load(epoch_root / "bindings/oracle-binding.json")
    indexed = {str(row.get("source_path")): row.get("sha256")
               for row in oracle.get("files", []) if isinstance(row, dict)}
    required = (Path(__file__).resolve(),
                Path(__file__).with_name("research_s2_proposal.py").resolve(),
                Path(__file__).with_name("research_flow.py").resolve())
    if any(indexed.get(str(path)) != "sha256:" + _file_sha(path)
           for path in required):
        raise ResearchS2ActionError("S2 stage source differs from frozen oracle")
    epoch_payload = _load(epoch_root / "research-epoch.json")
    if (epoch_payload["controller"]["sha256"] != source["controller_sha256"]
            or epoch_payload["budget"]["sha256"] != source["budget_sha256"]
            or source["controller_sha256"] != "sha256:" + _file_sha(
                Path(source["controller_path"]))
            or source["budget_sha256"] != "sha256:" + _file_sha(
                Path(source["budget_path"]))):
        raise ResearchS2ActionError("S2 controller or budget changed")
    binding = _load(Path(preflight["control_binding_path"]))
    control_inventory = Path(binding["control_inventory"])
    source_inventory = Path(_load(control_inventory / "bindings/source-inventory.json")["path"])
    return source, frozen, binding, source_inventory


def _expected_adapter(base: dict[str, Any], *, design: str, policy: str,
                      index: int, candidate: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    result["adapter_id"] = f"r4-s2-{design}-{policy}-{index}-v1"
    matches = [row for row in result["designs"] if row["design_id"] == design]
    if len(matches) != 1:
        raise ResearchS2ActionError("candidate design is absent from source adapter")
    row = matches[0]
    flow = row["flow_binding"]
    if flow.get("overrides") != {"CORE_UTILIZATION": "95"}:
        raise ResearchS2ActionError("S2 feedback project no longer uses u95")
    edits = candidate["config_edits"]
    if (not isinstance(edits, dict) or "CORE_UTILIZATION" not in edits
            or set(edits) - {"CORE_UTILIZATION", "PLACE_DENSITY_LB_ADDON", "ABC_AREA"}
            or any(type(value) is not str or not value for value in edits.values())):
        raise ResearchS2ActionError("candidate config edits exceed frozen scope")
    flow["overrides"] = dict(edits)
    flow["authority_note"] = (
        f"Pilot-development S2 {policy} candidate {index}; "
        f"proposal {candidate['candidate_digest']}; no RTL/SDC edit.")
    result["designs"] = [row]
    return result


def _candidate_records(source: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for task in source["rows"]:
        for policy in POLICIES:
            pool = task["pools"][policy]
            if (pool["candidate_limit"] != 3 or
                    len(pool["ordered_candidates"]) < 1 or
                    len(pool["ordered_candidates"]) > 3 or
                    pool["ordered_candidates"][-1]["source"] != "cold_start"):
                raise ResearchS2ActionError("S2 policy candidate budget drifted")
            for index, candidate in enumerate(pool["ordered_candidates"], start=1):
                rows.append({
                    "design_id": task["design_id"],
                    "source_group": task["source_group"],
                    "policy": policy,
                    "candidate_index": index,
                    "candidate_digest": candidate["candidate_digest"],
                    "candidate_source": candidate["source"],
                    "config_edits": candidate["config_edits"],
                    "control_project": task["control_project"],
                    "control_audit_digest": task["control_audit_digest"],
                })
    return rows


def prepare_s2_actions(*, proposals: str | Path, epoch: str | Path,
                       output: str | Path) -> dict[str, Any]:
    root = Path(output).expanduser().resolve()
    if root.exists():
        raise ResearchS2ActionError("refusing to overwrite S2 stage plan")
    proposal_root = Path(proposals).expanduser().resolve()
    epoch_root = Path(epoch).expanduser().resolve()
    source, frozen, binding, source_inventory = _inputs(proposal_root, epoch_root)
    preflight = Path(source["preflight_path"])
    base_adapter = _load(preflight.parent.parent / "preregistration/control-adapter-spec-v1.json")
    if len(binding["control_projects"]) != source["registered_designs"]:
        raise ResearchS2ActionError("S2 shared feedback denominator changed")
    root.mkdir(parents=True)
    staged = []
    for candidate in _candidate_records(source):
        design, policy, index = (candidate["design_id"], candidate["policy"],
                                 candidate["candidate_index"])
        relative = Path(design) / policy / f"candidate-{index}"
        adapter = _expected_adapter(base_adapter, design=design, policy=policy,
                                    index=index, candidate=candidate)
        adapter_path = root / "adapters" / relative.with_suffix(".json")
        adapter_path.parent.mkdir(parents=True, exist_ok=True)
        adapter_path.write_text(json.dumps(adapter, indent=2, sort_keys=True) + "\n",
                                encoding="utf-8")
        inventory = root / "inventory" / relative
        result = bind_research_inventory_adapters(
            inventory=source_inventory, adapter_spec=adapter_path,
            authority_root=Path(binding["orfs_root"]), output=inventory)
        if not result.get("valid") or not result.get("corpus_unchanged"):
            raise ResearchS2ActionError("S2 candidate inventory failed source binding")
        project = root / "projects" / relative
        stage_flow_project(inventory=inventory, design_id=design, output=project)
        original, selected = _pair_invariants(
            Path(candidate["control_project"]), project, candidate["config_edits"])
        staged.append({**candidate,
                       "adapter_path": str(adapter_path),
                       "adapter_sha256": "sha256:" + _file_sha(adapter_path),
                       "inventory_path": str(inventory),
                       "inventory_digest": result["inventory_digest"],
                       "project": str(project),
                       "control_stage_receipt_digest": original,
                       "candidate_stage_receipt_digest": selected})
    payload = {
        "schema": PLAN_SCHEMA,
        "proposals_path": str(proposal_root),
        "proposal_digest": source["proposal_digest"],
        "epoch_path": str(epoch_root), "epoch_digest": frozen["epoch_digest"],
        "memory_bundle_digest": frozen["memory_bundle_digest"],
        "controller_sha256": source["controller_sha256"],
        "budget_sha256": source["budget_sha256"],
        "registered_designs": source["registered_designs"],
        "registered_policy_tasks": source["registered_policy_tasks"],
        "candidate_limit": 3, "staged_candidates": staged,
        "authority": {"action_executed": False, "model_calls": 0,
                      "memory_update": "none", "production_authority": False},
        "claim_boundary": "Isolated S2 development candidate staging only; no Agent outcome or final-heldout claim.",
    }
    payload["plan_digest"] = _digest(payload)
    (root / "stage-plan.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"valid": True, "plan_digest": payload["plan_digest"],
            "registered_policy_tasks": payload["registered_policy_tasks"],
            "staged_candidates": len(staged), "output": str(root)}


def verify_s2_actions(plan: str | Path, *, allow_executed: bool = False) -> dict[str, Any]:
    root = Path(plan).expanduser().resolve()
    saved = _load(root / "stage-plan.json")
    if (saved.get("schema") != PLAN_SCHEMA or
            saved.get("plan_digest") != _digest({
                key: value for key, value in saved.items() if key != "plan_digest"})):
        raise ResearchS2ActionError("S2 stage plan digest or schema changed")
    source, frozen, binding, _ = _inputs(
        Path(saved["proposals_path"]), Path(saved["epoch_path"]))
    if (saved["proposal_digest"] != source["proposal_digest"]
            or saved["epoch_digest"] != frozen["epoch_digest"]
            or saved["memory_bundle_digest"] != frozen["memory_bundle_digest"]
            or saved["controller_sha256"] != source["controller_sha256"]
            or saved["budget_sha256"] != source["budget_sha256"]
            or saved["registered_designs"] != source["registered_designs"]
            or saved["registered_policy_tasks"] != source["registered_policy_tasks"]
            or saved["candidate_limit"] != 3):
        raise ResearchS2ActionError("S2 proposal, controller, budget, or epoch drifted")
    preflight = Path(source["preflight_path"])
    base_adapter = _load(preflight.parent.parent / "preregistration/control-adapter-spec-v1.json")
    expected = _candidate_records(source)
    if len(expected) != len(saved["staged_candidates"]):
        raise ResearchS2ActionError("S2 staged candidate denominator changed")
    projects = set()
    for candidate, actual in zip(expected, saved["staged_candidates"], strict=True):
        design, policy, index = (candidate["design_id"], candidate["policy"],
                                 candidate["candidate_index"])
        relative = Path(design) / policy / f"candidate-{index}"
        adapter_path = root / "adapters" / relative.with_suffix(".json")
        inventory = root / "inventory" / relative
        project = root / "projects" / relative
        if (actual["adapter_path"] != str(adapter_path)
                or actual["adapter_sha256"] != "sha256:" + _file_sha(adapter_path)
                or _load(adapter_path) != _expected_adapter(
                    base_adapter, design=design, policy=policy, index=index,
                    candidate=candidate)
                or actual["inventory_path"] != str(inventory)
                or actual["project"] != str(project)):
            raise ResearchS2ActionError("S2 candidate adapter or project path changed")
        checked_inventory = verify_research_inventory(inventory)
        if (not checked_inventory.get("valid")
                or actual["inventory_digest"] != checked_inventory["inventory_digest"]):
            raise ResearchS2ActionError("S2 candidate inventory drifted")
        if not verify_staged_flow_project(project).get("valid"):
            raise ResearchS2ActionError("S2 candidate project drifted")
        original, selected = _pair_invariants(
            Path(candidate["control_project"]), project, candidate["config_edits"])
        if actual != {**candidate,
                      "adapter_path": str(adapter_path),
                      "adapter_sha256": "sha256:" + _file_sha(adapter_path),
                      "inventory_path": str(inventory),
                      "inventory_digest": checked_inventory["inventory_digest"],
                      "project": str(project),
                      "control_stage_receipt_digest": original,
                      "candidate_stage_receipt_digest": selected}:
            raise ResearchS2ActionError("S2 candidate stage differs from proposal")
        if not allow_executed and any((project / "backend").glob("RUN_*")):
            raise ResearchS2ActionError("S2 candidate was executed before plan verification")
        if project in projects:
            raise ResearchS2ActionError("S2 policy arms share a staged workspace")
        projects.add(project)
    if len({(row["design_id"], row["policy"]) for row in expected}) != saved[
            "registered_policy_tasks"]:
        raise ResearchS2ActionError("S2 policy-task denominator is incomplete")
    return {"valid": True, "plan_digest": saved["plan_digest"],
            "registered_policy_tasks": saved["registered_policy_tasks"],
            "staged_candidates": len(expected), "output": str(root)}
