#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import sys

_SKILL_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SKILL_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS_DIR))
from skill_env import (
    skill_reference_path,
    workspace_path,
)

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_ROOT = SCRIPT_DIR.parent
for path in (SCRIPTS_ROOT, SCRIPT_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from common.io_utils import load_json, now_iso, write_json


DEFAULT_CASEBOOK = workspace_path("failures/failure_casebook.json")
DEFAULT_REPAIR_LOG = workspace_path("failures/repair_action_log.json")
DEFAULT_STRATEGY = skill_reference_path("failure_strategy.json")
DEFAULT_OUT_JSON = workspace_path("failures/failure_diagnosis.json")
DEFAULT_OUT_JSONL = workspace_path("failures/failure_diagnosis.jsonl")
DEFAULT_OUT_MD = workspace_path("failures/failure_diagnosis.md")


def best_next_action(candidates: list[str], attempted: list[str], strategy: dict) -> str:
    attempted_set = set(attempted or [])
    best_action = ""
    best_reward = float("-inf")
    for item in strategy.values():
        for action_item in item.get("action_stats") or []:
            action = str(action_item.get("action") or "")
            if not action or action not in candidates or action in attempted_set:
                continue
            reward = float(action_item.get("avg_reward", 0.0) or 0.0)
            if reward > best_reward:
                best_reward = reward
                best_action = action
    if best_action:
        return best_action
    for action in candidates:
        if action and action not in attempted_set:
            return action
    return ""


def summarize_evidence(record: dict) -> dict:
    evidence = record.get("evidence_features") or {}
    return {
        "top": evidence.get("top", ""),
        "source_group": evidence.get("source_group", ""),
        "source_path_suffix": evidence.get("source_path_suffix", ""),
        "unresolved_modules": evidence.get("unresolved_modules", []),
        "has_include_error": bool(evidence.get("has_include_error")),
        "has_memory_limit_error": bool(evidence.get("has_memory_limit_error")),
        "has_parse_error": bool(evidence.get("has_parse_error")),
    }


def infer_default_next_action(record: dict, attempted: list[str], strategy: dict) -> str:
    design = str(record.get("design") or "").lower()
    failure_class = str(record.get("failure_class") or "")
    symptom = str(record.get("symptom_pattern") or "").lower()
    evidence = record.get("evidence_features") or {}
    suffix = str(evidence.get("source_path_suffix") or "").lower()
    unresolved = list(evidence.get("unresolved_modules") or [])
    is_vtr_regression = "/vtr-verilog-to-routing-min/odin_ii/regression_test/benchmark/verilog/" in symptom
    is_vtr_regression_design = design.startswith("vtr_verilog_to_routing_min_odin_ii_regression_test_benchmark_verilog_")

    if failure_class == "missing_include":
        return "resolve_include"
    if failure_class == "missing_module":
        return "stub_module" if unresolved else "retry_next_best_action"
    if failure_class == "memory_limit":
        return "set_mem_limit:131072"
    if failure_class in {"undefined_macro", "helper_collision", "graph_empty"}:
        return "exclude"
    if failure_class == "parse_error":
        if is_vtr_regression_design or (is_vtr_regression and any(token in symptom for token in ("/keywords/", "/preprocessor/", "/syntax/"))):
            return "exclude"
        if "re-definition of module `$abstract" in symptom:
            return "recover_original_bundle_and_sanitize_simtasks"
        if ">>%<<" in symptom or "%%" in symptom:
            return "template_materialization"
        if "invalid name for macro definition" in symptom or "macro definition" in symptom:
            return "sanitize_macro_definition"
        if (
            "unrecognized format character" in symptom
            or ("system task" in symptom and "$display" in symptom)
            or "can't resolve task name `$" in symptom
        ):
            return "sanitize_simulation_system_tasks"
        if "does not have a port named" in symptom:
            return "exclude"
        if (
            ("procedural for-loop" in symptom and "not constant" in symptom)
            or "2nd expression of procedural for-loop is not constant" in symptom
            or "2nd expression of procedura" in symptom
        ):
            return "rewrite_nonconstant_procedural_loop"
        if suffix in {".sv", ".svh"} or "systemverilog" in symptom:
            return "sv2v"
        if "unexpected" in symptom and "syntax error" in symptom:
            return "unexpected_token_frontend_patch"
        if "unexpected" in symptom:
            return "unexpected_token_frontend_patch"
        if "syntax error" in symptom or "parse error" in symptom:
            return "legacy_syntax_frontend_patch"
        if "ast frontend in derive mode" in symptom or "analyzing design hierarchy" in symptom:
            return "hierarchy_derive_frontend_patch"
        if any(token in symptom for token in ("vhdl", "entity", "architecture", "library ieee")):
            return "vhd2vl"
        return "frontend_manual_patch_candidate"

    if failure_class == "unknown":
        if "multiple edge sensitive events found for this sign" in symptom:
            return "exclude"
        if "is connected to constants" in symptom:
            return "exclude"
        if "multiple conflicting drivers" in symptom:
            return "exclude"
        if "found 1 problems in 'check -assert'" in symptom:
            return "exclude"
        if "replacing memory" in symptom or "list of registers" in symptom:
            return "memory_lowering_review"
        return "retry_metadata_audit"

    candidates = list(record.get("repair_action_candidates") or [])
    next_action = best_next_action(candidates, attempted, strategy)
    if next_action:
        return next_action
    return str(record.get("fallback_if_failed") or "manual_or_exclude")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build compact diagnosis records from structured failure casebook + repair log.")
    parser.add_argument("--casebook-json", type=Path, default=DEFAULT_CASEBOOK)
    parser.add_argument("--repair-log-json", type=Path, default=DEFAULT_REPAIR_LOG)
    parser.add_argument("--strategy-json", type=Path, default=DEFAULT_STRATEGY)
    parser.add_argument("--out-json", type=Path, default=DEFAULT_OUT_JSON)
    parser.add_argument("--out-jsonl", type=Path, default=DEFAULT_OUT_JSONL)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_OUT_MD)
    args = parser.parse_args()

    casebook = load_json(args.casebook_json)
    records = casebook.get("records") or []
    repair_log = load_json(args.repair_log_json)
    strategy = load_json(args.strategy_json)

    diagnoses: list[dict] = []
    for record in records:
        design = str(record.get("design") or "")
        repair_entry = (repair_log.get(design) or {}) if isinstance(repair_log, dict) else {}
        attempted = list(repair_entry.get("last_actions") or [])
        next_action = infer_default_next_action(record, attempted, strategy)
        diagnosis = {
            "design": design,
            "failure_stage": record.get("failure_stage", ""),
            "failure_class": record.get("failure_class", ""),
            "tool_name": record.get("tool_name", ""),
            "symptom": record.get("symptom_pattern", ""),
            "likely_cause": record.get("root_cause_hypothesis", ""),
            "attempted_repair": attempted,
            "evidence": summarize_evidence(record),
            "next_best_action": next_action,
            "publish_status": (record.get("publish_policy") or {}).get("publish_status", ""),
            "repair_status": (record.get("publish_policy") or {}).get("repair_status", ""),
            "created_at": now_iso(),
        }
        diagnoses.append(diagnosis)

    payload = {
        "generated_at": now_iso(),
        "count": len(diagnoses),
        "diagnoses": diagnoses,
    }
    write_json(args.out_json, payload)
    args.out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with args.out_jsonl.open("w", encoding="utf-8") as fh:
        for diagnosis in diagnoses:
            fh.write(__import__("json").dumps(diagnosis, ensure_ascii=False) + "\n")
    with args.out_md.open("w", encoding="utf-8") as fh:
        fh.write("# Failure Diagnosis\n\n")
        fh.write(f"- generated_at: {payload['generated_at']}\n")
        fh.write(f"- count: {payload['count']}\n\n")
        fh.write("| design | class | symptom | attempted_repair | next_best_action |\n")
        fh.write("|---|---|---|---|---|\n")
        for diagnosis in diagnoses[:200]:
            attempted_text = ";".join(diagnosis["attempted_repair"]) if diagnosis["attempted_repair"] else "none"
            fh.write(
                f"| {diagnosis['design']} | {diagnosis['failure_class']} | "
                f"{diagnosis['symptom'][:80].replace('|', '/')} | {attempted_text} | {diagnosis['next_best_action']} |\n"
            )
    print(args.out_json)


if __name__ == "__main__":
    main()
