#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import math
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


DEFAULT_CASES = workspace_path("failures/llm_repair_cases.json")
DEFAULT_POLICY = skill_reference_path("llm_repair_policy.json")
DEFAULT_OUT_JSON = workspace_path("failures/llm_patch_requests.json")
DEFAULT_OUT_JSONL = workspace_path("failures/llm_patch_requests.jsonl")
DEFAULT_OUT_MD = workspace_path("failures/llm_patch_requests.md")


def make_request_id(design: str, prompt: str) -> str:
    digest = hashlib.md5(f"{design}\n{prompt}".encode("utf-8")).hexdigest()[:12]
    return f"{design}:{digest}"


def estimate_tokens(text: str, chars_per_token: int) -> int:
    chars_per_token = max(chars_per_token, 1)
    return int(math.ceil(len(text) / chars_per_token))


def main() -> None:
    parser = argparse.ArgumentParser(description="Turn LLM repair cases into API-ready patch requests.")
    parser.add_argument("--cases-json", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--policy-json", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--out-json", type=Path, default=DEFAULT_OUT_JSON)
    parser.add_argument("--out-jsonl", type=Path, default=DEFAULT_OUT_JSONL)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_OUT_MD)
    args = parser.parse_args()

    payload = load_json(args.cases_json)
    policy = load_json(args.policy_json)
    cases = payload.get("cases") or []

    requests = []
    skipped = []
    max_tokens = int(policy.get("max_tokens_per_design", 12000) or 12000)
    chars_per_token = int(policy.get("approx_chars_per_token", 4) or 4)
    skip_over_budget = bool(policy.get("skip_if_estimated_tokens_exceed_budget", True))
    for case in cases:
        prompt = case.get("prompt", "")
        design = case.get("design", "")
        estimated_tokens = estimate_tokens(prompt, chars_per_token)
        if skip_over_budget and estimated_tokens > max_tokens:
            skipped.append(
                {
                    "design": design,
                    "reason": "estimated_token_budget_exceeded",
                    "estimated_prompt_tokens": estimated_tokens,
                    "max_tokens_per_design": max_tokens,
                }
            )
            continue
        prompt = (
            f"{prompt}\n\n"
            "Additional patch constraints:\n"
            "- Keep the patch minimal and localized.\n"
            "- Do not rewrite unrelated logic or whole files.\n"
            "- If a likely fix requires changing more than about 20% of the target file's logic lines, "
            "prefer diagnosis_only unless the failure clearly requires a broader frontend rewrite.\n"
            f"- Stay within an estimated per-design prompt budget of about {max_tokens} tokens.\n"
        )
        request = {
            "request_id": make_request_id(design, prompt),
            "design": design,
            "source_path": case.get("source_path", ""),
            "failure_class": case.get("failure_class", ""),
            "failure_stage": case.get("failure_stage", ""),
            "next_best_action": case.get("next_best_action", ""),
            "pool_recommendation": case.get("pool_recommendation", ""),
            "llm_priority_score": case.get("llm_priority_score", 0.0),
            "api_contract": {
                "mode": "diagnose_then_patch",
                "expected_response_type": "json",
                "allowed_outputs": ["diagnosis_only", "unified_diff_patch"],
                "required_fields": [
                    "request_id",
                    "design",
                    "decision",
                    "confidence",
                    "summary",
                    "patch_unified_diff",
                    "notes",
                ],
            },
            "post_repair_checks": case.get("post_repair_checks", []),
            "prompt": prompt,
            "created_at": now_iso(),
            "budget_hint": {
                "estimated_prompt_tokens": estimated_tokens,
                "max_tokens_per_design": max_tokens,
                "approx_chars_per_token": chars_per_token,
            },
            "policy_hint": {
                "max_cases_per_run": policy.get("max_cases_per_run"),
                "allowed_failure_classes": policy.get("allowed_failure_classes", []),
            },
        }
        requests.append(request)

    out_payload = {
        "generated_at": now_iso(),
        "count": len(requests),
        "skipped_count": len(skipped),
        "requests": requests,
        "skipped": skipped,
    }
    write_json(args.out_json, out_payload)
    args.out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with args.out_jsonl.open("w", encoding="utf-8") as fh:
        for request in requests:
            fh.write(__import__("json").dumps(request, ensure_ascii=False) + "\n")
    with args.out_md.open("w", encoding="utf-8") as fh:
        fh.write("# LLM Patch Requests\n\n")
        fh.write(f"- generated_at: {out_payload['generated_at']}\n")
        fh.write(f"- count: {out_payload['count']}\n\n")
        fh.write(f"- skipped_count: {out_payload['skipped_count']}\n\n")
        fh.write("| request_id | design | class | next_best_action | pool | est_tokens |\n")
        fh.write("|---|---|---|---|---|---|\n")
        for request in requests[:100]:
            fh.write(
                f"| {request['request_id']} | {request['design']} | {request['failure_class']} | "
                f"{request['next_best_action']} | {request['pool_recommendation']} | "
                f"{request['budget_hint']['estimated_prompt_tokens']} |\n"
            )
        if skipped:
            fh.write("\n## Skipped For Budget\n\n")
            fh.write("| design | reason | estimated_prompt_tokens | max_tokens_per_design |\n")
            fh.write("|---|---|---|---|\n")
            for item in skipped[:100]:
                fh.write(
                    f"| {item['design']} | {item['reason']} | {item['estimated_prompt_tokens']} | "
                    f"{item['max_tokens_per_design']} |\n"
                )
    print(args.out_json)


if __name__ == "__main__":
    main()
