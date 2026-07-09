#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path
import sys


import sys

_SKILL_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SKILL_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS_DIR))
from skill_env import (
    out_root_path,
    skill_reference_path,
    workspace_path,
)

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_ROOT = SCRIPT_DIR.parent
for path in (SCRIPTS_ROOT, SCRIPT_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from common.io_utils import load_json, load_rows, write_json


DEFAULT_INDEX = out_root_path("index.csv")
DEFAULT_STRATEGY = skill_reference_path("failure_strategy.json")
DEFAULT_DENY_POLICY = skill_reference_path("repair_deny_policy.json")
DEFAULT_SIGNATURES = workspace_path("failures/failure_signatures.json")
DEFAULT_FAMILIES = workspace_path("failures/failure_families.json")
DEFAULT_OUT_JSON = workspace_path("failures/failure_casebook.json")
DEFAULT_OUT_JSONL = workspace_path("failures/failure_casebook.jsonl")
DEFAULT_OUT_MD = workspace_path("failures/failure_casebook.md")

def classify_failure_family(notes: str) -> str:
    lower = (notes or "").lower()
    if "synth_memory_max_bits" in lower or "synthesized memory size" in lower:
        return "memory_limit"
    if "can't open include file" in lower or "cannot open include file" in lower:
        return "missing_include"
    if "module `" in lower and ("not part of the design" in lower or "not found" in lower):
        return "missing_module"
    if "unimplemented compiler directive" in lower or "undefined macro" in lower:
        return "undefined_macro"
    if "re-definition of module" in lower and "$abstract\\dff" in lower:
        return "helper_collision"
    if "syntax error" in lower or "parse error" in lower or "front-end" in lower or "frontend" in lower:
        return "parse_error"
    if "no graph nodes were created from mapped netlist" in lower:
        return "graph_empty"
    return "unknown"


def infer_stage(status: str) -> str:
    s = (status or "").lower()
    if "graph" in s:
        return "graph_convert"
    if "synth" in s:
        return "synth"
    return "finalize"


def infer_tool(notes: str) -> str:
    lower = (notes or "").lower()
    if "slang" in lower:
        return "slang"
    if "sv2v" in lower:
        return "sv2v"
    if "vhd2vl" in lower or "vhdl" in lower:
        return "vhd2vl"
    if "no graph nodes were created from mapped netlist" in lower:
        return "graph_builder"
    return "yosys"


def unresolved_modules(notes: str) -> list[str]:
    return sorted(set(re.findall(r"module `([^`]+)`", notes or "")))


def symptom_pattern(notes: str) -> str:
    lines = [line.strip() for line in (notes or "").splitlines() if line.strip()]
    return " | ".join(lines[-3:])[:400]


def evidence_features(row: dict[str, str], notes: str) -> dict:
    source_path = row.get("source_path", "")
    suffix = Path(source_path).suffix.lower()
    return {
        "source_group": row.get("source_group", ""),
        "source_path_suffix": suffix,
        "unresolved_modules": unresolved_modules(notes),
        "has_include_error": "include file" in (notes or "").lower(),
        "has_memory_limit_error": "synth_memory_max_bits" in (notes or "").lower() or "synthesized memory size" in (notes or "").lower(),
        "has_parse_error": "syntax error" in (notes or "").lower() or "parse error" in (notes or "").lower(),
        "top": row.get("top", ""),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build structured failure casebook from failed index rows.")
    parser.add_argument("--index-csv", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--strategy-json", type=Path, default=DEFAULT_STRATEGY)
    parser.add_argument("--deny-policy-json", type=Path, default=DEFAULT_DENY_POLICY)
    parser.add_argument("--signatures-json", type=Path, default=DEFAULT_SIGNATURES)
    parser.add_argument("--families-json", type=Path, default=DEFAULT_FAMILIES)
    parser.add_argument("--out-json", type=Path, default=DEFAULT_OUT_JSON)
    parser.add_argument("--out-jsonl", type=Path, default=DEFAULT_OUT_JSONL)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_OUT_MD)
    args = parser.parse_args()

    rows = [row for row in load_rows(args.index_csv) if row.get("status") and row.get("status") != "success"]
    strategy = load_json(args.strategy_json)
    deny_policy = load_json(args.deny_policy_json)
    signatures = load_json(args.signatures_json)
    families = load_json(args.families_json)

    records: list[dict] = []
    for row in rows:
        notes = row.get("notes", "") or ""
        failure_class = classify_failure_family(notes)
        matched_actions: list[str] = []
        for item in strategy.values():
            pattern = item.get("pattern", "")
            if pattern and re.search(pattern, notes, flags=re.IGNORECASE):
                matched_actions.extend(item.get("actions", []))
        matched_actions = list(dict.fromkeys(matched_actions))

        surface_signature = ""
        for key, entry in signatures.items():
            if entry.get("example_design") == row.get("design"):
                surface_signature = key
                break

        deny_entry = (deny_policy.get("deny_classes") or {}).get(failure_class, {})
        family_entry = (families or {}).get(failure_class, {})
        record = {
            "design": row.get("design", ""),
            "status": row.get("status", ""),
            "failure_stage": infer_stage(row.get("status", "")),
            "tool_name": infer_tool(notes),
            "surface_signature": surface_signature,
            "semantic_signature": f"{infer_stage(row.get('status', ''))}:{infer_tool(notes)}:{failure_class}",
            "failure_class": failure_class,
            "symptom_pattern": symptom_pattern(notes),
            "root_cause_hypothesis": deny_entry.get("reason", family_entry.get("description", failure_class)),
            "evidence_features": evidence_features(row, notes),
            "repair_action_candidates": matched_actions,
            "repair_preconditions": {
                "has_source_path": bool(row.get("source_path")),
                "has_expected_top": bool(row.get("top")),
            },
            "risk_level": "high" if failure_class in {"semantic_reset_issue", "ram_or_macro_dependency"} else ("medium" if failure_class in {"missing_module", "memory_limit", "parse_error"} else "low"),
            "fidelity_risk": "high" if failure_class in {"missing_module", "ram_or_macro_dependency", "semantic_reset_issue"} else ("medium" if failure_class in {"memory_limit", "parse_error"} else "low"),
            "post_repair_checks": [
                "read_verilog/hierarchy/check",
                "graph non-empty check",
                "graph stat sanity check",
                "publish eligibility gate",
            ],
            "publish_policy": {
                "repair_status": deny_entry.get("repair_status", "repair_ok_and_publishable"),
                "publish_status": deny_entry.get("publish_status", "publishable_if_checks_pass"),
            },
            "fallback_if_failed": "exclude_or_debug_pool" if deny_entry else "retry_next_best_action",
        }
        records.append(record)

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.out_json, {"records": records, "count": len(records)})
    with args.out_jsonl.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(__import__("json").dumps(record, ensure_ascii=False) + "\n")
    with args.out_md.open("w", encoding="utf-8") as fh:
        fh.write("# Failure Casebook\n\n")
        fh.write(f"- record_count: {len(records)}\n\n")
        fh.write("| design | stage | tool | class | actions | publish_status |\n")
        fh.write("|---|---|---|---|---|---|\n")
        for record in records[:200]:
            fh.write(
                f"| {record['design']} | {record['failure_stage']} | {record['tool_name']} | {record['failure_class']} | "
                f"{';'.join(record['repair_action_candidates'])} | {record['publish_policy']['publish_status']} |\n"
            )
    print(args.out_json)


if __name__ == "__main__":
    main()
