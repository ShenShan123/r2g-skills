#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

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

from common.io_utils import now_iso


DEFAULT_REQUESTS = workspace_path("failures/llm_patch_requests.jsonl")
DEFAULT_RESULTS = workspace_path("failures/llm_patch_results.jsonl")
DEFAULT_MODEL = "gpt-5.2"
DEFAULT_BASE_URL = "https://api.openai.com/v1"


RESULT_SCHEMA = {
    "name": "llm_patch_result",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "request_id": {"type": "string"},
            "design": {"type": "string"},
            "decision": {"type": "string", "enum": ["diagnosis_only", "unified_diff_patch", "reject"]},
            "confidence": {"type": "number"},
            "summary": {"type": "string"},
            "patch_unified_diff": {"type": "string"},
            "notes": {"type": "string"},
        },
        "required": [
            "request_id",
            "design",
            "decision",
            "confidence",
            "summary",
            "patch_unified_diff",
            "notes",
        ],
        "additionalProperties": False,
    },
}


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def extract_output_text(payload: dict) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    for item in payload.get("output", []) or []:
        for content in item.get("content", []) or []:
            text = content.get("text")
            if isinstance(text, str):
                return text
    return ""


def call_openai(base_url: str, api_key: str, model: str, prompt: str) -> dict:
    body = {
        "model": model,
        "input": prompt,
        "text": {
            "format": {
                "type": "json_schema",
                "name": RESULT_SCHEMA["name"],
                "strict": RESULT_SCHEMA["strict"],
                "schema": RESULT_SCHEMA["schema"],
            }
        },
    }
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/responses",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Call OpenAI Responses API for LLM patch requests.")
    parser.add_argument("--requests-jsonl", type=Path, default=DEFAULT_REQUESTS)
    parser.add_argument("--results-jsonl", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--max-requests", type=int, default=10)
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", DEFAULT_MODEL))
    parser.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    # LLM patching is OPTIONAL and default OFF (r2g ingestion decision
    # 2026-07-09); the OpenAI path is the LAST-RESORT fallback behind the
    # local agent. Explicit opt-in required.
    if not args.dry_run and os.environ.get("R2G_ACQUIRE_ENABLE_LLM", "") != "1":
        raise SystemExit(
            "HINT: the OpenAI LLM patch path is disabled by default. "
            "Export R2G_ACQUIRE_ENABLE_LLM=1 (and OPENAI_API_KEY) to run it."
        )

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key and not args.dry_run:
        raise SystemExit("OPENAI_API_KEY is not set")

    requests = load_jsonl(args.requests_jsonl)
    done_ids = {row.get("request_id", "") for row in load_jsonl(args.results_jsonl)}
    pending = [row for row in requests if row.get("request_id") not in done_ids][: args.max_requests]

    args.results_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with args.results_jsonl.open("a", encoding="utf-8") as fh:
        for item in pending:
            request_id = item.get("request_id", "")
            design = item.get("design", "")
            if args.dry_run:
                result = {
                    "request_id": request_id,
                    "design": design,
                    "decision": "diagnosis_only",
                    "confidence": 0.0,
                    "summary": "dry-run placeholder",
                    "patch_unified_diff": "",
                    "notes": "dry-run; no API call executed",
                    "provider": "openai",
                    "model": args.model,
                    "created_at": now_iso(),
                }
            else:
                try:
                    payload = call_openai(args.base_url, api_key, args.model, item.get("prompt", ""))
                    text = extract_output_text(payload)
                    parsed = json.loads(text)
                    result = {
                        **parsed,
                        "provider": "openai",
                        "model": args.model,
                        "response_id": payload.get("id", ""),
                        "created_at": now_iso(),
                    }
                except urllib.error.HTTPError as exc:
                    error_text = exc.read().decode("utf-8", errors="ignore")
                    result = {
                        "request_id": request_id,
                        "design": design,
                        "decision": "reject",
                        "confidence": 0.0,
                        "summary": "API HTTP error",
                        "patch_unified_diff": "",
                        "notes": error_text[:4000],
                        "provider": "openai",
                        "model": args.model,
                        "created_at": now_iso(),
                    }
                except Exception as exc:
                    result = {
                        "request_id": request_id,
                        "design": design,
                        "decision": "reject",
                        "confidence": 0.0,
                        "summary": f"executor error: {type(exc).__name__}",
                        "patch_unified_diff": "",
                        "notes": str(exc),
                        "provider": "openai",
                        "model": args.model,
                        "created_at": now_iso(),
                    }
            fh.write(json.dumps(result, ensure_ascii=False) + "\n")
            fh.flush()
            print(request_id)


if __name__ == "__main__":
    main()
