"""Frozen campaign preparation for TEHM Research Runtime RC1.

Preparation binds a campaign specification to one verified ResearchEpoch, one
verified read-only design inventory, a controller/budget, and a task-contract
registry.  It does not route memory or execute EDA.  Invalid tasks fail the
whole preparation; no cohort member is silently filtered.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from tehm.evaluation.research_epoch import verify_research_epoch
from tehm.evaluation.research_inventory import verify_research_inventory


CAMPAIGN_SPEC_SCHEMA = "tehm-research-campaign-spec-v1"
PREPARED_CAMPAIGN_SCHEMA = "tehm-research-prepared-campaign-v1"
TASK_CONTEXT_SCHEMA = "tehm-research-task-context-v1"
TASK_CONTRACT_SCHEMA = "tehm-research-task-contract-registry-v1"
ARTIFACT_MANIFEST_SCHEMA = "tehm-research-prepared-artifacts-v1"

PROFILES = {"frontend_preflight", "controlled_action_pilot", "agent_memory_static"}
POLICIES = {"no_persistent_memory", "legacy_memory", "tehm"}
ROLES = {"official_control", "external_design", "development_only"}
SUCCESS_RULES = {"ALL_REQUIRED_PASS"}
UTILITY_RULES = {"UNDETERMINED", "PAIRED_SCOPED_DELTA"}
GOLD_KEYS = {"fix", "gold_patch", "repaired_rtl", "heldout_answer", "target_patch"}
BUDGET_FIELDS = (
    "candidate_limit", "eda_call_limit", "model_call_limit", "model_token_limit",
    "wallclock_limit_seconds", "retry_limit",
)


class ResearchCampaignError(ValueError):
    """Raised when campaign inputs cannot be frozen or replayed."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
    temporary.write_bytes(_canonical(value) + b"\n")
    temporary.replace(path)


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResearchCampaignError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ResearchCampaignError(f"{label} must be a JSON object")
    return value


def _contains_forbidden(value: object) -> str | None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in GOLD_KEYS:
                return f"gold_field:{key}"
            found = _contains_forbidden(item)
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for item in value:
            found = _contains_forbidden(item)
            if found:
                return found
    elif value == "REQUIRED":
        return "unresolved_REQUIRED_placeholder"
    return None


def _identifier(value: object, label: str) -> str:
    if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise ResearchCampaignError(f"{label} is invalid")
    return value


def _strings(value: object, label: str, *, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        raise ResearchCampaignError(f"{label} must be a{' non-empty' if nonempty else ''} list")
    if any(type(item) is not str or not item for item in value):
        raise ResearchCampaignError(f"{label} must contain non-empty strings")
    if len(value) != len(set(value)):
        raise ResearchCampaignError(f"{label} must not contain duplicates")
    return list(value)


def _require_flags(payload: object, required: Mapping[str, bool], label: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ResearchCampaignError(f"{label} must be an object")
    result = dict(payload)
    for key, expected in required.items():
        if result.get(key) is not expected:
            raise ResearchCampaignError(f"{label}.{key} must be {str(expected).lower()}")
    return result


def _registry(path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    payload = _load_json(path, "task contract registry")
    if payload.get("schema") != TASK_CONTRACT_SCHEMA:
        raise ResearchCampaignError("task contract registry schema mismatch")
    contracts = payload.get("contracts")
    if not isinstance(contracts, Mapping) or not contracts:
        raise ResearchCampaignError("task contract registry is empty")
    checked: dict[str, dict[str, Any]] = {}
    for contract_id, raw in contracts.items():
        _identifier(contract_id, "contract_id")
        if not isinstance(raw, Mapping):
            raise ResearchCampaignError(f"task contract {contract_id} must be an object")
        contract = dict(raw)
        _strings(contract.get("required_checks"), f"{contract_id}.required_checks", nonempty=True)
        _strings(contract.get("optional_checks"), f"{contract_id}.optional_checks")
        if contract.get("success_rule") not in SUCCESS_RULES:
            raise ResearchCampaignError(f"task contract {contract_id} success_rule is invalid")
        if contract.get("utility_rule") not in UTILITY_RULES:
            raise ResearchCampaignError(f"task contract {contract_id} utility_rule is invalid")
        if contract.get("missing_report_verdict") != "UNKNOWN":
            raise ResearchCampaignError(f"task contract {contract_id} must fail missing reports to UNKNOWN")
        if contract.get("infrastructure_error_verdict") != "UNKNOWN":
            raise ResearchCampaignError(f"task contract {contract_id} must separate infrastructure errors")
        for field in ("eligible_for_repair_metric", "eligible_for_evolution"):
            if type(contract.get(field)) is not bool:
                raise ResearchCampaignError(f"task contract {contract_id}.{field} must be boolean")
        checked[contract_id] = contract
    return payload, checked


def _budget(spec: object, ceiling: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(spec, Mapping):
        raise ResearchCampaignError("campaign budget must be an object")
    result = dict(spec)
    for field in BUDGET_FIELDS:
        value = result.get(field)
        maximum = ceiling.get(field)
        if type(value) is not int or value < 0:
            raise ResearchCampaignError(f"campaign budget.{field} is invalid")
        if type(maximum) is not int or value > maximum:
            raise ResearchCampaignError(f"campaign budget.{field} exceeds frozen epoch")
    if result["candidate_limit"] < 1:
        raise ResearchCampaignError("campaign candidate_limit must be positive")
    retryable = _strings(result.get("retryable_classes", []), "budget.retryable_classes")
    ceiling_retryable = set(ceiling.get("retryable_classes") or [])
    if not set(retryable) <= ceiling_retryable:
        raise ResearchCampaignError("campaign retryable classes exceed frozen epoch")
    return result


def _copy_binding(source: Path, destination: Path, root: Path) -> dict[str, Any]:
    if not source.is_file():
        raise ResearchCampaignError(f"binding source is missing: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return {
        "source_path": str(source),
        "frozen_path": destination.relative_to(root).as_posix(),
        "sha256": _sha256_file(destination),
        "bytes": destination.stat().st_size,
    }


def _artifact_manifest(root: Path) -> dict[str, Any]:
    manifest_path = root / "artifact-manifest.json"
    files = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_file() and path != manifest_path:
            files.append({
                "path": path.relative_to(root).as_posix(),
                "sha256": _sha256_file(path),
                "bytes": path.stat().st_size,
            })
    payload = {
        "schema": ARTIFACT_MANIFEST_SCHEMA,
        "files": files,
        "files_digest": _digest(files),
    }
    payload["manifest_digest"] = _digest(payload)
    return payload


def _task_context(task: Mapping[str, Any], design: Mapping[str, Any],
                  contract_id: str, contract: Mapping[str, Any],
                  campaign_id: str, budget: Mapping[str, Any],
                  epoch: Mapping[str, Any], inventory: Mapping[str, Any]) -> dict[str, Any]:
    task_id = _identifier(task.get("task_id"), "task_id")
    role = task.get("role")
    if role not in ROLES:
        raise ResearchCampaignError(f"task {task_id} role is invalid")
    target_scope = task.get("target_scope")
    if not isinstance(target_scope, Mapping) or not target_scope:
        raise ResearchCampaignError(f"task {task_id} target_scope must be non-empty")
    allowed_actions = _strings(task.get("allowed_actions"), f"task {task_id} allowed_actions",
                               nonempty=True)
    if contract.get("action_mode") == "NO_ACTION" and allowed_actions != ["NO_ACTION"]:
        raise ResearchCampaignError(f"task {task_id} preflight must allow only NO_ACTION")
    readiness = design.get("readiness") or {}
    if readiness.get("status") == "UNSUPPORTED_CURRENT_PROFILE":
        raise ResearchCampaignError(f"task {task_id} design is unsupported")
    if contract.get("task_kind") == "rtl_repair" and (
            design.get("verification") or {}).get("functional_repair_oracle") != "verified":
        raise ResearchCampaignError(
            f"task {task_id} requires a verified functional repair oracle")
    compilation = design.get("compilation") or {}
    if not compilation.get("top_module") or not compilation.get("ordered_filelist"):
        raise ResearchCampaignError(f"task {task_id} compilation input is incomplete")
    context = {
        "schema": TASK_CONTEXT_SCHEMA,
        "campaign_id": campaign_id,
        "task_id": task_id,
        "case_id": task_id,
        "design_id": design["design_id"],
        "role": role,
        "contract_id": contract_id,
        "contract": dict(contract),
        "target_scope": dict(target_scope),
        "allowed_actions": allowed_actions,
        "observations": dict(task.get("observations") or {}),
        "compile_input": {
            "source_root": design["source_root"],
            "project_path": design["project_path"],
            "top_module": compilation["top_module"],
            "top_authority": compilation["top_authority"],
            "ordered_filelist": list(compilation["ordered_filelist"]),
            "filelist_authority": compilation["filelist_authority"],
            "include_dirs": list(compilation.get("include_dirs") or []),
            "defines": dict(compilation.get("defines") or {}),
            "top_parameters": dict(compilation.get("top_parameters") or {}),
            "source_bundle_digest": design["source_bundle_digest"],
            "design_manifest_digest": design["manifest_digest"],
        },
        "clock_and_constraints": dict(design.get("clock_and_constraints") or {}),
        "verification_scope": dict(design.get("verification") or {}),
        "budget": dict(budget),
        "frozen_refs": {
            "epoch_id": epoch["epoch_id"],
            "epoch_digest": epoch["epoch_digest"],
            "inventory_digest": inventory["inventory_digest"],
        },
        "authority": {
            "research_only": True,
            "source_read_only": True,
            "online_memory_update": False,
            "production_import": False,
            "future_execution_result_present": False,
        },
    }
    forbidden = _contains_forbidden(context)
    if forbidden:
        raise ResearchCampaignError(f"task {task_id} contains forbidden content: {forbidden}")
    context["task_context_digest"] = _digest(context)
    return context


def prepare_research_campaign(
    *, campaign_spec: str | Path, epoch: str | Path, inventory: str | Path,
    task_contracts: str | Path, output: str | Path,
) -> dict[str, Any]:
    """Freeze a complete task denominator without executing or filtering it."""
    spec_path = Path(campaign_spec).expanduser().resolve()
    epoch_root = Path(epoch).expanduser().resolve()
    inventory_root = Path(inventory).expanduser().resolve()
    contracts_path = Path(task_contracts).expanduser().resolve()
    destination = Path(output).expanduser().resolve()
    if destination.exists():
        raise ResearchCampaignError(f"refusing to overwrite prepared campaign: {destination}")
    spec = _load_json(spec_path, "campaign spec")
    forbidden = _contains_forbidden(spec)
    if forbidden:
        raise ResearchCampaignError(f"campaign spec contains forbidden content: {forbidden}")
    if spec.get("schema") != CAMPAIGN_SPEC_SCHEMA:
        raise ResearchCampaignError("campaign spec schema mismatch")
    campaign_id = _identifier(spec.get("campaign_id"), "campaign_id")
    if spec.get("profile") not in PROFILES:
        raise ResearchCampaignError("campaign profile is invalid")
    if spec.get("research_only") is not True:
        raise ResearchCampaignError("campaign must be research_only")

    checked_epoch = verify_research_epoch(epoch_root)
    if not checked_epoch.get("valid") or not checked_epoch.get("research_evaluation_ready"):
        raise ResearchCampaignError("research epoch is not evaluation-ready")
    checked_inventory = verify_research_inventory(inventory_root)
    if not checked_inventory.get("valid") or not checked_inventory.get("corpus_unchanged"):
        raise ResearchCampaignError("research inventory is invalid")
    epoch_payload = _load_json(epoch_root / "research-epoch.json", "research epoch")
    inventory_payload = _load_json(inventory_root / "inventory.json", "research inventory")
    controller = _load_json(epoch_root / "bindings/controller.json", "frozen controller")
    epoch_budget = _load_json(epoch_root / "bindings/budget.json", "frozen budget")
    registry_payload, contracts = _registry(contracts_path)

    policies = _strings(spec.get("policies"), "campaign policies", nonempty=True)
    if not set(policies) <= POLICIES:
        raise ResearchCampaignError("campaign policies contain an unknown policy")
    controller_policies = set(controller.get("policies") or [])
    if not set(policies) <= controller_policies:
        raise ResearchCampaignError("campaign policies exceed the frozen controller")
    budget = _budget(spec.get("budget"), epoch_budget)
    _require_flags(spec.get("memory"), {
        "online_update": False, "final_test_learning": False,
        "production_import": False,
    }, "memory")
    _require_flags(spec.get("execution"), {
        "original_corpus_read_only": True, "isolated_arm_workspaces": True,
        "overwrite_existing_attempt": False, "retain_failed_attempts": True,
        "audit_raw_reports": True,
    }, "execution")
    _require_flags(spec.get("reporting"), {
        "include_all_registered_tasks": True,
        "separate_unknown_and_failure": True,
        "separate_task_and_strict_signoff": True,
        "actual_cost_accounting": True,
    }, "reporting")

    tasks = spec.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ResearchCampaignError("campaign tasks must be a non-empty list")
    if any(not isinstance(task, Mapping) for task in tasks):
        raise ResearchCampaignError("campaign task must be an object")
    task_ids = [_identifier(task.get("task_id"), "task_id") for task in tasks]
    if len(task_ids) != len(set(task_ids)):
        raise ResearchCampaignError("campaign task IDs must be unique")

    designs: dict[str, dict[str, Any]] = {}
    for item in inventory_payload.get("designs") or []:
        manifest_path = inventory_root / str(item.get("manifest_path") or "")
        design = _load_json(manifest_path, "design manifest")
        designs[design["design_id"]] = design

    contexts: list[dict[str, Any]] = []
    for task in tasks:
        design_id = _identifier(task.get("design_id"), "design_id")
        design = designs.get(design_id)
        if design is None:
            raise ResearchCampaignError(f"campaign design is not in inventory: {design_id}")
        contract_id = _identifier(task.get("contract_id"), "contract_id")
        contract = contracts.get(contract_id)
        if contract is None:
            raise ResearchCampaignError(f"unknown task contract: {contract_id}")
        contexts.append(_task_context(
            task, design, contract_id, contract, campaign_id, budget,
            epoch_payload, inventory_payload,
        ))
    if spec["profile"] == "controlled_action_pilot":
        roles = Counter(context["role"] for context in contexts)
        if roles["official_control"] < 1 or roles["external_design"] < 1:
            raise ResearchCampaignError(
                "controlled_action_pilot requires official and external designs")

    staging = destination.with_name(destination.name + f".tmp.{os.getpid()}")
    if staging.exists():
        raise ResearchCampaignError(f"prepared campaign staging exists: {staging}")
    staging.mkdir(parents=True)
    try:
        bindings = {
            "campaign_spec": _copy_binding(spec_path, staging / "bindings/campaign-spec.json", staging),
            "task_contracts": _copy_binding(contracts_path, staging / "bindings/task-contracts.json", staging),
            "research_epoch": _copy_binding(epoch_root / "research-epoch.json",
                                             staging / "bindings/research-epoch.json", staging),
            "research_inventory": _copy_binding(inventory_root / "inventory.json",
                                                 staging / "bindings/research-inventory.json", staging),
        }
        for context in contexts:
            _write_json(staging / "task-contexts" / f"{context['task_id']}.json", context)
        with (staging / "task-roster.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=(
                "task_id", "design_id", "role", "contract_id", "task_context_digest"
            ))
            writer.writeheader()
            for context in contexts:
                writer.writerow({key: context[key] for key in writer.fieldnames})

        prepared = {
            "schema": PREPARED_CAMPAIGN_SCHEMA,
            "campaign_id": campaign_id,
            "status": "FROZEN_INPUTS",
            "profile": spec["profile"],
            "research_only": True,
            "epoch": {
                "path": str(epoch_root),
                "epoch_id": epoch_payload["epoch_id"],
                "epoch_digest": epoch_payload["epoch_digest"],
            },
            "inventory": {
                "path": str(inventory_root),
                "inventory_digest": inventory_payload["inventory_digest"],
            },
            "bindings": bindings,
            "controller": {
                "controller_id": controller["controller_id"],
                "content_digest": _digest(controller),
            },
            "budget": budget,
            "policies": policies,
            "task_contract_registry_id": registry_payload["registry_id"],
            "task_contract_registry_digest": _digest(registry_payload),
            "task_count": len(contexts),
            "task_ids": [context["task_id"] for context in contexts],
            "task_contexts_digest": _digest([
                {"task_id": context["task_id"],
                 "digest": context["task_context_digest"]}
                for context in contexts
            ]),
            "denominator": {
                "registered": len(tasks),
                "prepared": len(contexts),
                "silently_filtered": 0,
            },
            "authority": {
                "production_authority": False,
                "online_memory_update": False,
                "final_test_learning": False,
                "execution_started": False,
            },
        }
        prepared["prepared_campaign_digest"] = _digest(prepared)
        _write_json(staging / "prepared-campaign.json", prepared)
        _write_json(staging / "artifact-manifest.json", _artifact_manifest(staging))
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging.replace(destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return verify_prepared_campaign(destination)


def verify_prepared_campaign(path: str | Path) -> dict[str, Any]:
    root = Path(path).expanduser().resolve()
    prepared = _load_json(root / "prepared-campaign.json", "prepared campaign")
    if prepared.get("schema") != PREPARED_CAMPAIGN_SCHEMA:
        raise ResearchCampaignError("prepared campaign schema mismatch")
    unsigned = dict(prepared)
    claimed = unsigned.pop("prepared_campaign_digest", None)
    if claimed != _digest(unsigned):
        raise ResearchCampaignError("prepared campaign digest mismatch")
    artifacts = _load_json(root / "artifact-manifest.json", "artifact manifest")
    if artifacts.get("schema") != ARTIFACT_MANIFEST_SCHEMA:
        raise ResearchCampaignError("prepared artifact schema mismatch")
    unsigned_artifacts = dict(artifacts)
    artifact_digest = unsigned_artifacts.pop("manifest_digest", None)
    if artifact_digest != _digest(unsigned_artifacts):
        raise ResearchCampaignError("prepared artifact manifest digest mismatch")
    files = artifacts.get("files")
    if not isinstance(files, list) or artifacts.get("files_digest") != _digest(files):
        raise ResearchCampaignError("prepared artifact files digest mismatch")
    for entry in files:
        if not isinstance(entry, Mapping):
            raise ResearchCampaignError("prepared artifact entry is invalid")
        relative = PurePosixPath(str(entry.get("path") or ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise ResearchCampaignError("prepared artifact path is unsafe")
        target = root / Path(*relative.parts)
        if (not target.is_file() or target.stat().st_size != entry.get("bytes")
                or _sha256_file(target) != entry.get("sha256")):
            raise ResearchCampaignError(f"prepared artifact drifted: {relative}")
    checked_epoch = verify_research_epoch(prepared["epoch"]["path"])
    if checked_epoch["epoch_digest"] != prepared["epoch"]["epoch_digest"]:
        raise ResearchCampaignError("prepared epoch binding drifted")
    checked_inventory = verify_research_inventory(prepared["inventory"]["path"])
    if checked_inventory["inventory_digest"] != prepared["inventory"]["inventory_digest"]:
        raise ResearchCampaignError("prepared inventory binding drifted")
    task_ids = prepared.get("task_ids")
    if not isinstance(task_ids, list) or len(task_ids) != prepared.get("task_count"):
        raise ResearchCampaignError("prepared task denominator is malformed")
    contexts = []
    for task_id in task_ids:
        context = _load_json(root / "task-contexts" / f"{task_id}.json", "task context")
        if context.get("schema") != TASK_CONTEXT_SCHEMA:
            raise ResearchCampaignError("task context schema mismatch")
        unsigned_context = dict(context)
        context_digest = unsigned_context.pop("task_context_digest", None)
        if context_digest != _digest(unsigned_context):
            raise ResearchCampaignError("task context digest mismatch")
        contexts.append({"task_id": task_id, "digest": context_digest})
    if _digest(contexts) != prepared.get("task_contexts_digest"):
        raise ResearchCampaignError("prepared task context set drifted")
    denominator = prepared.get("denominator") or {}
    if (denominator.get("registered") != denominator.get("prepared")
            or denominator.get("silently_filtered") != 0):
        raise ResearchCampaignError("prepared campaign filtered its denominator")
    return {
        "valid": True,
        "campaign_id": prepared["campaign_id"],
        "status": prepared["status"],
        "prepared_campaign_digest": claimed,
        "artifact_manifest_digest": artifact_digest,
        "epoch_digest": prepared["epoch"]["epoch_digest"],
        "inventory_digest": prepared["inventory"]["inventory_digest"],
        "task_count": prepared["task_count"],
        "silently_filtered": 0,
        "execution_started": False,
    }


__all__ = [
    "ARTIFACT_MANIFEST_SCHEMA", "CAMPAIGN_SPEC_SCHEMA", "PREPARED_CAMPAIGN_SCHEMA",
    "ResearchCampaignError", "TASK_CONTEXT_SCHEMA", "TASK_CONTRACT_SCHEMA",
    "prepare_research_campaign", "verify_prepared_campaign",
]
