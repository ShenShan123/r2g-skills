#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import json
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


DEFAULT_REQUESTS = workspace_path("failures/llm_patch_requests.json")
DEFAULT_RESULTS = workspace_path("failures/llm_patch_results.jsonl")
DEFAULT_OUT_JSON = workspace_path("failures/llm_patch_result_evaluation.json")
DEFAULT_OUT_MD = workspace_path("failures/llm_patch_result_evaluation.md")
DEFAULT_POLICY = skill_reference_path("llm_repair_policy.json")


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def validate_result(result: dict) -> tuple[str, list[str]]:
    required = ["request_id", "design", "decision", "confidence", "summary", "patch_unified_diff", "notes"]
    problems = [field for field in required if field not in result]
    if problems:
        return "invalid_schema", problems
    decision = str(result.get("decision") or "")
    if decision not in {"diagnosis_only", "unified_diff_patch", "reject"}:
        return "invalid_decision", [decision]
    if decision == "unified_diff_patch" and not str(result.get("patch_unified_diff") or "").strip():
        return "missing_patch", []
    return "valid", []


def split_source_paths(source_path: str) -> list[Path]:
    return [Path(part) for part in str(source_path or "").split(";") if part.strip()]


def count_logic_lines(text: str) -> int:
    count = 0
    in_block = False
    for raw_line in text.splitlines():
        line = raw_line
        if in_block:
            if "*/" in line:
                line = line.split("*/", 1)[1]
                in_block = False
            else:
                continue
        while "/*" in line:
            before, after = line.split("/*", 1)
            if "*/" in after:
                after = after.split("*/", 1)[1]
                line = before + after
            else:
                line = before
                in_block = True
                break
        line = line.split("//", 1)[0].strip()
        if line:
            count += 1
    return count


def diff_changed_logic_lines(diff_text: str) -> int:
    count = 0
    for line in diff_text.splitlines():
        if line.startswith(("+++", "---", "@@", "diff --git", "index ")):
            continue
        if not line.startswith(("+", "-")):
            continue
        stripped = line[1:].strip()
        if stripped and not stripped.startswith("//"):
            count += 1
    return count


def diff_target_paths(diff_text: str) -> list[str]:
    paths = []
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            paths.append(line[len("+++ b/"):].strip())
    if paths:
        return paths
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            match = re.match(r"diff --git a/(.+?) b/(.+)$", line)
            if match:
                paths.append(match.group(2).strip())
    return paths


def resolve_diff_target(relpath: str, source_paths: list[Path]) -> Path | None:
    relpath = relpath.strip()
    if not relpath:
        return None
    for source in source_paths:
        parents = [source.parent, *source.parents]
        for parent in parents:
            candidate = parent / relpath
            if candidate.exists():
                return candidate
    rel_name = Path(relpath).name
    for source in source_paths:
        if source.name == rel_name and source.exists():
            return source
        for parent in [source.parent, *source.parents]:
            matches = list(parent.rglob(rel_name))
            if matches:
                return matches[0]
    return None


def analyze_patch_minimality(request: dict, result: dict, policy: dict) -> tuple[dict, str | None]:
    diff_text = str(result.get("patch_unified_diff") or "")
    if not diff_text.strip():
        return {"available": False, "reason": "empty_patch"}, None
    source_paths = split_source_paths(request.get("source_path", ""))
    relpaths = diff_target_paths(diff_text)
    target = None
    for relpath in relpaths:
        target = resolve_diff_target(relpath, source_paths)
        if target:
            break
    changed_logic_lines = diff_changed_logic_lines(diff_text)
    analysis = {
        "available": True,
        "target_relpaths": relpaths,
        "resolved_target": str(target) if target else "",
        "changed_logic_lines": changed_logic_lines,
    }
    if not target:
        analysis["reason"] = "target_not_resolved"
        return analysis, None
    try:
        source_text = target.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        analysis["reason"] = "target_unreadable"
        return analysis, None
    source_logic_lines = count_logic_lines(source_text)
    analysis["source_logic_lines"] = source_logic_lines
    if source_logic_lines <= 0:
        analysis["reason"] = "source_logic_lines_zero"
        return analysis, None
    ratio = changed_logic_lines / max(source_logic_lines, 1)
    analysis["logic_change_ratio"] = ratio
    threshold = float(policy.get("max_logic_change_ratio", 0.2) or 0.2)
    failure_class = str(request.get("failure_class") or "")
    reject_classes = set(policy.get("reject_large_patch_failure_classes", []))
    warn_classes = set(policy.get("warn_large_patch_failure_classes", []))
    if ratio > threshold:
        if failure_class in reject_classes:
            return analysis, "reject_large_patch"
        if failure_class in warn_classes or not warn_classes:
            return analysis, "warn_large_patch"
    return analysis, None


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate LLM patch-result schema before any patch application.")
    parser.add_argument("--requests-json", type=Path, default=DEFAULT_REQUESTS)
    parser.add_argument("--results-jsonl", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--policy-json", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--out-json", type=Path, default=DEFAULT_OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_OUT_MD)
    args = parser.parse_args()

    requests = load_json(args.requests_json).get("requests") or []
    policy = load_json(args.policy_json)
    requests_by_id = {item.get("request_id", ""): item for item in requests if item.get("request_id")}
    results = load_jsonl(args.results_jsonl)

    evaluations = []
    for result in results:
        request_id = str(result.get("request_id") or "")
        request = requests_by_id.get(request_id, {})
        status, problems = validate_result(result)
        warnings: list[str] = []
        patch_analysis = None
        if status == "valid" and result.get("decision") == "unified_diff_patch" and request:
            patch_analysis, patch_status = analyze_patch_minimality(request, result, policy)
            if patch_status == "reject_large_patch":
                status = patch_status
                problems.append("logic_change_ratio_exceeded")
            elif patch_status == "warn_large_patch":
                warnings.append("logic_change_ratio_exceeded")
        evaluations.append(
            {
                "request_id": request_id,
                "design": result.get("design") or request.get("design", ""),
                "status": status,
                "problems": problems,
                "warnings": warnings,
                "decision": result.get("decision", ""),
                "confidence": result.get("confidence", None),
                "has_matching_request": bool(request),
                "patch_analysis": patch_analysis,
                "created_at": now_iso(),
            }
        )

    payload = {
        "generated_at": now_iso(),
        "request_count": len(requests),
        "result_count": len(results),
        "evaluation_count": len(evaluations),
        "evaluations": evaluations,
    }
    write_json(args.out_json, payload)
    with args.out_md.open("w", encoding="utf-8") as fh:
        fh.write("# LLM Patch Result Evaluation\n\n")
        fh.write(f"- generated_at: {payload['generated_at']}\n")
        fh.write(f"- request_count: {payload['request_count']}\n")
        fh.write(f"- result_count: {payload['result_count']}\n\n")
        fh.write("| request_id | design | status | decision | has_matching_request | warnings |\n")
        fh.write("|---|---|---|---|---|---|\n")
        for item in evaluations[:200]:
            fh.write(
                f"| {item['request_id']} | {item['design']} | {item['status']} | {item['decision']} | "
                f"{item['has_matching_request']} | {','.join(item['warnings'])} |\n"
            )
    print(args.out_json)


if __name__ == "__main__":
    main()
