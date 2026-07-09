#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
import sys

import sys

_SKILL_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SKILL_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS_DIR))
from skill_env import (
    workspace_path,
)

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_ROOT = SCRIPT_DIR.parent
for path in (SCRIPTS_ROOT, SCRIPT_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from common.io_utils import load_json, now_iso, write_json


DEFAULT_REQUESTS = workspace_path("failures/llm_patch_requests.json")
DEFAULT_RESULTS = workspace_path("failures/llm_patch_results_local_agent.jsonl")
DEFAULT_EVAL = workspace_path("failures/llm_patch_result_evaluation_local_agent.json")
DEFAULT_DIAGNOSIS = workspace_path("failures/failure_diagnosis.json")
DEFAULT_OUT_JSON = workspace_path("failures/llm_patch_rule_candidates.json")
DEFAULT_OUT_MD = workspace_path("failures/llm_patch_rule_candidates.md")


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def patch_feature(diff_text: str, notes: str) -> tuple[str, str]:
    if re.search(r"^\-.*\bbreak\b", diff_text, flags=re.MULTILINE) and re.search(r"^\+.*found", diff_text, flags=re.MULTILINE):
        return (
            "replace_procedural_break_with_flag",
            r"ERROR:.*break|unsupported.*break",
        )
    if "`include" in diff_text:
        return (
            "rewrite_or_insert_include",
            r"ERROR:.*include|can't open include file",
        )
    if re.search(r"`define", diff_text) or "macro definition" in notes.lower():
        return (
            "sanitize_macro_definition",
            r"ERROR:.*macro definition|Invalid name for macro definition",
        )
    if "2nd expression of procedural for-loop is not constant" in notes:
        return (
            "rewrite_nonconstant_procedural_for_loop",
            r"ERROR:.*procedural for-loop.*not constant",
        )
    return (
        "frontend_patch_other",
        r"ERROR:.*frontend|ERROR:.*parse",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Mine successful LLM patches into reusable failure-rule candidates.")
    parser.add_argument("--requests-json", type=Path, default=DEFAULT_REQUESTS)
    parser.add_argument("--results-jsonl", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--evaluation-json", type=Path, default=DEFAULT_EVAL)
    parser.add_argument("--diagnosis-json", type=Path, default=DEFAULT_DIAGNOSIS)
    parser.add_argument("--out-json", type=Path, default=DEFAULT_OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_OUT_MD)
    args = parser.parse_args()

    requests = {row.get("request_id", ""): row for row in (load_json(args.requests_json).get("requests") or []) if row.get("request_id")}
    results = {row.get("request_id", ""): row for row in load_jsonl(args.results_jsonl) if row.get("request_id")}
    evaluations = {row.get("request_id", ""): row for row in (load_json(args.evaluation_json).get("evaluations") or []) if row.get("request_id")}
    diagnoses = {row.get("design", ""): row for row in (load_json(args.diagnosis_json).get("diagnoses") or []) if row.get("design")}

    candidates = []
    support = Counter()
    for request_id, evaluation in evaluations.items():
        if evaluation.get("status") != "valid":
            continue
        result = results.get(request_id, {})
        if result.get("decision") != "unified_diff_patch":
            continue
        request = requests.get(request_id, {})
        design = str(result.get("design") or request.get("design") or "")
        diagnosis = diagnoses.get(design, {})
        summary = str(result.get("summary") or "")
        notes = str(result.get("notes") or "")
        diff_text = str(result.get("patch_unified_diff") or "")
        feature, regex_hint = patch_feature(diff_text, notes)
        key = (
            str(request.get("failure_class") or ""),
            str(request.get("next_best_action") or ""),
            feature,
            regex_hint,
        )
        support[key] += 1
        candidates.append(
            {
                "request_id": request_id,
                "design": design,
                "failure_class": request.get("failure_class", ""),
                "failure_stage": request.get("failure_stage", ""),
                "likely_cause": diagnosis.get("likely_cause", ""),
                "next_best_action": request.get("next_best_action", ""),
                "patch_feature": feature,
                "symptom_regex_hint": regex_hint,
                "suggested_repair": summary,
                "evidence_excerpt": notes[:400],
                "support_count": 0,
                "created_at": now_iso(),
            }
        )

    for item in candidates:
        key = (
            str(item.get("failure_class") or ""),
            str(item.get("next_best_action") or ""),
            str(item.get("patch_feature") or ""),
            str(item.get("symptom_regex_hint") or ""),
        )
        item["support_count"] = support[key]

    payload = {
        "generated_at": now_iso(),
        "count": len(candidates),
        "candidates": candidates,
    }
    write_json(args.out_json, payload)
    with args.out_md.open("w", encoding="utf-8") as fh:
        fh.write("# LLM Patch Rule Candidates\n\n")
        fh.write(f"- generated_at: {payload['generated_at']}\n")
        fh.write(f"- count: {payload['count']}\n\n")
        fh.write("| design | class | next_best_action | patch_feature | support_count |\n")
        fh.write("|---|---|---|---|---|\n")
        for item in candidates[:200]:
            fh.write(
                f"| {item['design']} | {item['failure_class']} | {item['next_best_action']} | "
                f"{item['patch_feature']} | {item['support_count']} |\n"
            )
    print(args.out_json)


if __name__ == "__main__":
    main()
