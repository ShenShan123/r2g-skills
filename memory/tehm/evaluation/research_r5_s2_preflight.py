"""Read-only compatibility audit of the existing S2 entrypoints, not a launcher.

Inspects a bounded, explicit source closure. No target, provider, recommender,
database connection or imported repository module is executed. A successful
audit is NOT an Agent-ready verdict, authorization or empirical result.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path

FILES = (
    "memory/evaluation/research_rc1_controller_v1.json",
    "memory/evaluation/research_rc1_budget_v1.json",
    "memory/evaluation/research_r5_paper_protocol_v1.json",
    "memory/tehm/evaluation/research_s2_proposal.py",
    "memory/tehm/evaluation/research_s2_run.py",
    "memory/legacy_backend.py",
    "memory/none_backend.py",
    "memory/baselines/r2g_legacy/baseline_manifest.json",
    "r2g-skills/signoff-loop/knowledge/heuristics.json",
    "r2g-skills/signoff-loop/knowledge/schema.sql",
    "r2g-skills/signoff-loop/knowledge/knowledge.sqlite",
)
POLICIES = ["no_persistent_memory", "legacy_memory", "tehm"]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def inspect(root: Path) -> dict:
    root = root.resolve(strict=True)
    data, pins = {}, {}
    for name in FILES:
        path = root / name
        if path.resolve(strict=True) != path or path.is_symlink():
            raise ValueError("input symlink or path escape: " + name)
        data[name] = path.read_bytes()
        pins[name] = digest(data[name])
    # Sidecars cannot silently add state outside the frozen database bytes.
    database = root / FILES[-1]
    for suffix in ("-wal", "-shm"):
        sidecar = database.with_name(database.name + suffix)
        # SHM is the WAL index, not additional database frames. Never open SQL.
        if sidecar.is_symlink() or (sidecar.exists() and suffix == "-wal"
                                   and sidecar.stat().st_size):
            raise ValueError("nonempty Legacy WAL or linked database sidecar")
    load = lambda name: json.loads(data[name])
    controller, budget, paper = [load(name) for name in FILES[:3]]
    legacy = load(FILES[7])
    content = {key: value for key, value in legacy.items() if key != "manifest_digest"}
    if digest(json.dumps(content, indent=2, sort_keys=True).encode()) != legacy.get("manifest_digest"):
        raise ValueError("Legacy manifest digest mismatch")
    wanted = [legacy["heuristics_digest"], legacy["schema_digest"],
              legacy["knowledge_db_fingerprint"]["db_sha256"]]
    if [pins[name] for name in FILES[-3:]] != wanted:
        raise ValueError("Legacy bytes differ from frozen baseline")
    tree = ast.parse(data[FILES[3]])
    edits = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "_ALLOWED_EDITS"
                for target in node.targets):
            call = node.value
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                    and call.func.id == "frozenset" and len(call.args) == 1):
                edits = sorted(ast.literal_eval(call.args[0]))
    strings = {node.value for node in ast.walk(tree)
               if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    runner_tree = ast.parse(data[FILES[4]])
    runner_strings = {node.value for node in ast.walk(runner_tree)
                      if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    observed = {
        "controller_kind": controller.get("controller_type"),
        "policies": controller.get("policies"),
        "model_call_limit": budget.get("model_call_limit"),
        "model_token_limit": budget.get("model_token_limit"),
        "legacy_allowed_edits": edits,
        "proposal_has_flow_config_domain": "flow.CONFIG_DELTA" in strings,
        "runner_has_orfs_stage_sequence": "synth floorplan place cts route finish" in runner_strings,
        "paper_controller_kind": paper.get("controller_kind"),
        "paper_model_calls_authorized": paper.get("model_calls_authorized"),
        "paper_model_tokens_authorized": paper.get("model_tokens_authorized"),
    }
    expected = {
        "controller_kind": "deterministic_skill", "policies": POLICIES,
        "model_call_limit": 0, "model_token_limit": 0,
        "legacy_allowed_edits": ["ABC_AREA", "CORE_UTILIZATION", "PLACE_DENSITY_LB_ADDON"],
        "proposal_has_flow_config_domain": True,
        "runner_has_orfs_stage_sequence": True,
        "paper_controller_kind": "deterministic_controlled_action_not_agent",
        "paper_model_calls_authorized": 0, "paper_model_tokens_authorized": 0,
    }
    if observed != expected or any(type(observed[key]) is not int for key in (
            "model_call_limit", "model_token_limit", "paper_model_calls_authorized",
            "paper_model_tokens_authorized")):
        raise ValueError("reviewed S2 compatibility assumptions changed; re-review required")
    for key in ("production_authority", "online_memory_update", "final_test_learning"):
        if controller.get(key) is not False:
            raise ValueError("controller authority boundary changed")
    return {
        "schema": "tehm-r5-s2-compatibility-audit-v1",
        "role": "OFFLINE_SOURCE_COMPATIBILITY_NOT_AGENT_EXPERIMENT",
        "valid": True, "observed": observed, "inputs_sha256": pins,
        "legacy_snapshot_matches_historical_manifest": True,
        "legacy_rtl_retrieval_or_activation_validated": False,
        "existing_s2_directly_reusable_for_r5_rtl_agent": False,
        "agent_ready": False, "execution_authorized": False,
        "model_calls": 0, "model_tokens": 0, "eda_calls": 0,
        "recommender_calls": 0, "method_tasks": 0,
        "required_before_agent_execution": [
            "separately_frozen_rtl_controller_prompt_and_observation_contract",
            "shared_rtl_action_validation_and_private_evaluator_adapter",
            "legacy_public_context_mapping_and_exposure_review_without_algorithm_change",
            "three_policy_isolation_and_budget_enforcement_rehearsal",
            "explicit_provider_model_and_call_token_budget_authorization",
            "separate_registered_development_cohort_not_relabelled_final",
        ],
        "boundary": "Only the listed entrypoints were inspected. AST literals corroborate reviewed scope, not a whole-repository absence proof. Hash equality does not certify Legacy usefulness, independence or answer safety. No launch authority is issued even if all input checks pass.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = inspect(args.input_root)
    with args.output.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"valid": True, "agent_ready": False, "model_calls": 0,
                      "output_sha256": digest(args.output.read_bytes())}))


if __name__ == "__main__":
    main()
