#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
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

from common.io_utils import now_iso


DEFAULT_REQUESTS = workspace_path("failures/llm_patch_requests.jsonl")
DEFAULT_RESULTS = workspace_path("failures/llm_patch_results.jsonl")
DEFAULT_WORKDIR = Path.home()
DEFAULT_CODEX_HOME = Path.home() / ".codex_local_exec"
DEFAULT_SOURCE_CODEX_HOME = Path.home() / ".codex"


RESULT_SCHEMA = {
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


def build_agent_prompt(item: dict) -> str:
    return (
        "You are repairing a local RTL frontend failure.\n"
        "Return only a single JSON object that matches the provided schema.\n"
        "Be conservative. Preserve circuit semantics. Only propose parser/frontend repairs.\n"
        "If confidence is low, choose diagnosis_only.\n\n"
        f"request_id: {item['request_id']}\n"
        f"design: {item['design']}\n"
        f"failure_class: {item['failure_class']}\n"
        f"failure_stage: {item['failure_stage']}\n"
        f"next_best_action: {item['next_best_action']}\n"
        f"pool_recommendation: {item['pool_recommendation']}\n\n"
        f"Prompt:\n{item['prompt']}\n"
    )


def bootstrap_codex_home(target: Path, source: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for name in ["auth.json", "config.toml", "models_cache.json", "version.json", "installation_id"]:
        src = source / name
        dst = target / name
        if src.exists() and src.is_file():
            shutil.copy2(src, dst)


def main() -> None:
    parser = argparse.ArgumentParser(description="Use local Codex agent to turn LLM patch requests into patch results.")
    parser.add_argument("--requests-jsonl", type=Path, default=DEFAULT_REQUESTS)
    parser.add_argument("--results-jsonl", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    parser.add_argument("--codex-home", type=Path, default=DEFAULT_CODEX_HOME)
    parser.add_argument("--source-codex-home", type=Path, default=DEFAULT_SOURCE_CODEX_HOME)
    parser.add_argument("--max-requests", type=int, default=5)
    parser.add_argument("--model", default="gpt-5.4-mini")
    parser.add_argument("--request-timeout-sec", type=int, default=180)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    # LLM patching is OPTIONAL and default OFF (r2g ingestion decision
    # 2026-07-09) — deterministic repair is first-line; this executor only
    # runs when the operator explicitly opts in.
    if not args.dry_run and os.environ.get("R2G_ACQUIRE_ENABLE_LLM", "") != "1":
        raise SystemExit(
            "HINT: the local-agent LLM patch path is disabled by default. "
            "Export R2G_ACQUIRE_ENABLE_LLM=1 to run it (deterministic repair "
            "via repair/auto_fix_failures.py is the first-line path)."
        )

    requests = load_jsonl(args.requests_jsonl)
    done_ids = {row.get("request_id", "") for row in load_jsonl(args.results_jsonl)}
    pending = [row for row in requests if row.get("request_id") not in done_ids][: args.max_requests]

    args.results_jsonl.parent.mkdir(parents=True, exist_ok=True)
    bootstrap_codex_home(args.codex_home, args.source_codex_home)
    with args.results_jsonl.open("a", encoding="utf-8") as out_fh:
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
                    "notes": "dry-run; local agent not executed",
                    "provider": "local_codex_agent",
                    "model": args.model,
                    "created_at": now_iso(),
                }
            else:
                with tempfile.TemporaryDirectory(prefix="n45_llm_agent_") as tmpdir:
                    tmpdir_path = Path(tmpdir)
                    schema_path = tmpdir_path / "result_schema.json"
                    output_path = tmpdir_path / "last_message.txt"
                    schema_path.write_text(json.dumps(RESULT_SCHEMA, indent=2), encoding="utf-8")
                    prompt = build_agent_prompt(item)
                    cmd = [
                        "codex",
                        "exec",
                        "-",
                        "--skip-git-repo-check",
                        "--sandbox",
                        "workspace-write",
                        "--model",
                        args.model,
                        "--output-schema",
                        str(schema_path),
                        "--output-last-message",
                        str(output_path),
                        "-C",
                        str(args.workdir),
                    ]
                    try:
                        proc = subprocess.run(
                            cmd,
                            input=prompt,
                            text=True,
                            capture_output=True,
                            check=False,
                            timeout=args.request_timeout_sec,
                            env={
                                **os.environ,
                                "CODEX_HOME": str(args.codex_home),
                            },
                        )
                    except subprocess.TimeoutExpired as exc:
                        result = {
                            "request_id": request_id,
                            "design": design,
                            "decision": "reject",
                            "confidence": 0.0,
                            "summary": f"local agent timeout after {args.request_timeout_sec}s",
                            "patch_unified_diff": "",
                            "notes": ((exc.stderr or exc.stdout or "") if isinstance((exc.stderr or exc.stdout or ""), str) else "")[:4000],
                            "provider": "local_codex_agent",
                            "model": args.model,
                            "created_at": now_iso(),
                        }
                        out_fh.write(json.dumps(result, ensure_ascii=False) + "\n")
                        out_fh.flush()
                        print(request_id)
                        continue
                    if proc.returncode != 0:
                        result = {
                            "request_id": request_id,
                            "design": design,
                            "decision": "reject",
                            "confidence": 0.0,
                            "summary": f"local agent error: returncode={proc.returncode}",
                            "patch_unified_diff": "",
                            "notes": (proc.stderr or proc.stdout or "")[:4000],
                            "provider": "local_codex_agent",
                            "model": args.model,
                            "created_at": now_iso(),
                        }
                    else:
                        raw = output_path.read_text(encoding="utf-8").strip()
                        parsed = json.loads(raw)
                        result = {
                            **parsed,
                            "provider": "local_codex_agent",
                            "model": args.model,
                            "created_at": now_iso(),
                        }
            out_fh.write(json.dumps(result, ensure_ascii=False) + "\n")
            out_fh.flush()
            print(request_id)


if __name__ == "__main__":
    main()
