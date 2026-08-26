#!/usr/bin/env python3
"""Run one provider-neutral Vanilla LLM RTL-acquisition method."""

from __future__ import annotations

import argparse
from collections import Counter
import copy
import hashlib
import http.client
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import ssl
import subprocess
import sys
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.preflight_experiment1_model_routes import (  # noqa: E402
    endpoint_for,
    load_env_file,
)
from tools.run_experiment1_rtl_acquisition import (  # noqa: E402
    CLOSURE_EVIDENCE_FIELDS,
    EXPERIMENT_DIR,
    ExperimentError,
    SUBMISSION_SCHEMA,
    TASK_SPEC,
    input_qualification_failure,
    now_iso,
    read_json,
    prior_candidate_keys,
    campaign_paths,
    manifest_toolchain_env,
    normalized_repo_url,
    run_formal_synth,
    semantic_submission_errors,
    submission_score_eligibility,
    synthesis_qualification_failure,
    validate_json,
    verify_candidate_inputs,
    verify_bound_campaign,
    write_json_atomic,
)


VANILLA_METHODS = {
    "openai-vanilla",
    "deepseek-vanilla",
    "qwen-vanilla",
    "glm-vanilla",
    "kimi-vanilla",
    "grok-vanilla",
}
READ_ONLY_COMMANDS = {
    "cat", "cut", "find", "grep", "head", "ls", "rg", "sed", "sort",
    "tail", "uniq", "wc",
}
READ_ONLY_GIT = {"status", "log", "show", "ls-tree", "grep", "rev-parse", "diff"}


class TokenBudgetError(ExperimentError):
    pass


NETWORK_RETRY_DELAYS = (0, 2, 5, 10, 20, 40, 60)
TRANSIENT_HTTP_CODES = {429, 500, 502, 503, 504}


def is_transient_network_error(exc: BaseException) -> bool:
    return isinstance(
        exc,
        (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
            socket.timeout,
            ssl.SSLError,
            http.client.IncompleteRead,
            http.client.RemoteDisconnected,
        ),
    )


def is_transient_git_failure(text: str) -> bool:
    value = text.lower()
    return any(
        marker in value
        for marker in (
            "tls connection",
            "ssl",
            "gnutls",
            "connection reset",
            "connection timed out",
            "could not resolve host",
            "remote end hung up",
            "unexpected disconnect",
            "early eof",
        )
    )


def submitted_stop_reason(candidate_count: int, target: int) -> str:
    return "target_reached" if candidate_count >= target else "submitted_early"


def assistant_history_message(message: dict[str, Any]) -> dict[str, Any]:
    calls = message.get("tool_calls") or []
    value: dict[str, Any] = {
        "role": "assistant",
        "content": message.get("content") or "",
    }
    if calls:
        value["tool_calls"] = calls
    return value


def anthropic_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": item["function"]["name"],
            "description": item["function"].get("description", ""),
            "input_schema": item["function"]["parameters"],
        }
        for item in tool_specs()
    ]


def anthropic_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Translate the runner's provider-neutral history into Anthropic blocks."""
    system_parts: list[str] = []
    converted: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role == "system":
            system_parts.append(str(message.get("content") or ""))
            continue
        if role == "assistant":
            blocks: list[dict[str, Any]] = []
            if message.get("content"):
                blocks.append({"type": "text", "text": str(message["content"])})
            for call in message.get("tool_calls") or []:
                raw = call.get("function", {}).get("arguments") or {}
                arguments = raw if isinstance(raw, dict) else json.loads(raw)
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": str(call.get("id") or ""),
                        "name": str(call.get("function", {}).get("name") or ""),
                        "input": arguments,
                    }
                )
            entry = {"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]}
        elif role == "tool":
            entry = {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": str(message.get("tool_call_id") or ""),
                        "content": str(message.get("content") or ""),
                    }
                ],
            }
        else:
            entry = {
                "role": "user",
                "content": [{"type": "text", "text": str(message.get("content") or "")}],
            }
        if converted and converted[-1]["role"] == entry["role"]:
            converted[-1]["content"].extend(entry["content"])
        else:
            converted.append(entry)
    return "\n\n".join(system_parts), converted


def anthropic_response_message(data: dict[str, Any]) -> dict[str, Any]:
    text_parts: list[str] = []
    calls: list[dict[str, Any]] = []
    for block in data.get("content") or []:
        if block.get("type") == "text":
            text_parts.append(str(block.get("text") or ""))
        elif block.get("type") == "tool_use":
            calls.append(
                {
                    "id": str(block.get("id") or ""),
                    "type": "function",
                    "function": {
                        "name": str(block.get("name") or ""),
                        "arguments": json.dumps(
                            block.get("input") or {}, ensure_ascii=True, separators=(",", ":")
                        ),
                    },
                }
            )
    return {"content": "\n".join(text_parts), "tool_calls": calls}


def responses_tools() -> list[dict[str, Any]]:
    """Translate the common Chat Completions tool schema to Responses tools."""
    return [
        {
            "type": "function",
            "name": item["function"]["name"],
            "description": item["function"].get("description", ""),
            "parameters": item["function"]["parameters"],
        }
        for item in tool_specs()
    ]


def responses_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate provider-neutral history to Responses input items."""
    converted: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role == "assistant":
            if message.get("content"):
                converted.append(
                    {"role": "assistant", "content": str(message["content"])}
                )
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                raw_arguments = function.get("arguments") or "{}"
                arguments = (
                    json.dumps(raw_arguments, ensure_ascii=True, separators=(",", ":"))
                    if isinstance(raw_arguments, dict)
                    else str(raw_arguments)
                )
                converted.append(
                    {
                        "type": "function_call",
                        "call_id": str(call.get("id") or ""),
                        "name": str(function.get("name") or ""),
                        "arguments": arguments,
                    }
                )
        elif role == "tool":
            converted.append(
                {
                    "type": "function_call_output",
                    "call_id": str(message.get("tool_call_id") or ""),
                    "output": str(message.get("content") or ""),
                }
            )
        else:
            converted.append(
                {"role": str(role or "user"), "content": str(message.get("content") or "")}
            )
    return converted


def responses_response_message(data: dict[str, Any]) -> dict[str, Any]:
    """Translate a Responses result to the runner's common assistant message."""
    text_parts: list[str] = []
    calls: list[dict[str, Any]] = []
    for item in data.get("output") or []:
        if item.get("type") == "function_call":
            calls.append(
                {
                    "id": str(item.get("call_id") or item.get("id") or ""),
                    "type": "function",
                    "function": {
                        "name": str(item.get("name") or ""),
                        "arguments": str(item.get("arguments") or "{}"),
                    },
                }
            )
        elif item.get("type") == "message":
            for block in item.get("content") or []:
                if block.get("type") in {"output_text", "text"}:
                    text_parts.append(str(block.get("text") or ""))
    return {"content": "\n".join(text_parts), "tool_calls": calls}


def inline_safe_paths(value: Any) -> Any:
    if isinstance(value, dict):
        if value == {"$ref": "#/$defs/safeRelativePath"}:
            return {
                "type": "string",
                "minLength": 1,
                "not": {
                    "pattern": r"(^/|(^|/)\.\.(/|$)|^[A-Za-z][A-Za-z0-9+.-]*://)"
                },
            }
        return {key: inline_safe_paths(item) for key, item in value.items()}
    if isinstance(value, list):
        return [inline_safe_paths(item) for item in value]
    return value


def tool_specs() -> list[dict[str, Any]]:
    submission_schema = read_json(SUBMISSION_SCHEMA)
    candidate = inline_safe_paths(copy.deepcopy(submission_schema["$defs"]["candidate"]))
    out_of_scope = inline_safe_paths(
        copy.deepcopy(submission_schema["$defs"]["outOfScope"])
    )
    definitions = [
        {
            "name": "search_repositories",
            "description": "Search public GitHub repositories. Every call consumes one search request.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1},
                    "page": {"type": "integer", "minimum": 1, "maximum": 10},
                    "per_page": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "clone_repository",
            "description": "Clone a public HTTPS Git repository without submodules and pin a commit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_url": {"type": "string", "pattern": "^https://"},
                    "commit": {"type": ["string", "null"]},
                },
                "required": ["repo_url"],
                "additionalProperties": False,
            },
        },
        {
            "name": "list_repository_files",
            "description": "List tracked files in a cloned repository, optionally filtered by substring.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_id": {"type": "string"},
                    "contains": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                },
                "required": ["repo_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "read_repository_file",
            "description": "Read a bounded UTF-8 text excerpt from one repository-relative file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_id": {"type": "string"},
                    "path": {"type": "string"},
                    "start_line": {"type": "integer", "minimum": 1},
                    "line_count": {"type": "integer", "minimum": 1, "maximum": 300},
                },
                "required": ["repo_id", "path"],
                "additionalProperties": False,
            },
        },
        {
            "name": "run_readonly_command",
            "description": (
                "Run one bounded read-only command inside a cloned repository. "
                "Allowed commands: cat, cut, find, grep, head, ls, rg, sed, sort, "
                "tail, uniq, wc, and read-only git subcommands."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_id": {"type": "string"},
                    "argv": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 30,
                        "items": {"type": "string"},
                    },
                },
                "required": ["repo_id", "argv"],
                "additionalProperties": False,
            },
        },
        {
            "name": "validate_candidate",
            "description": (
                "Run the public deterministic candidate precheck: schema and pinned-source "
                "identity, active compilation-input closure, repository-relative license "
                "evidence with matching canonical SPDX, then the frozen Sky130HD synth-only "
                "screen (nonempty mapped netlist, at least 100 mapped cells, functional input "
                "and output, and no unresolved modules). Only a full pass is checkpointed. "
                "The independent evaluator later reclones and reruns the same gates."
            ),
            "parameters": {
                "type": "object",
                "properties": {"repo_id": {"type": "string"}, "candidate": candidate},
                "required": ["repo_id", "candidate"],
                "additionalProperties": False,
            },
        },
        {
            "name": "submit_candidates",
            "description": "Submit the final ordered candidate list. Call only when finished.",
            "parameters": {
                "type": "object",
                "properties": {
                    "candidates": {
                        "type": "array",
                        "maxItems": 25,
                        "items": candidate,
                    },
                    "out_of_scope": {"type": "array", "items": out_of_scope},
                },
                "required": ["candidates", "out_of_scope"],
                "additionalProperties": False,
            },
        },
    ]
    return [
        {"type": "function", "function": definition}
        for definition in definitions
    ]


def provider_usage(data: dict[str, Any]) -> dict[str, int]:
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    details = usage.get("completion_tokens_details")
    if not isinstance(details, dict):
        details = usage.get("output_tokens_details")
    if not isinstance(details, dict):
        details = {}
    input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion_tokens = int(
        usage.get("completion_tokens") or usage.get("output_tokens") or 0
    )
    reasoning_tokens = int(details.get("reasoning_tokens") or 0)
    output_tokens = max(0, completion_tokens - reasoning_tokens)
    total_tokens = int(
        usage.get("total_tokens") or input_tokens + completion_tokens
    )
    return {
        "input": input_tokens,
        "output": output_tokens,
        "reasoning": reasoning_tokens,
        "total": total_tokens,
    }


def endpoint_kind_for_route(route: dict[str, Any]) -> str:
    return "official" if route.get("channel") == "official_api" else "gateway"


def normalized_checkout(value: Any) -> str:
    if value is None:
        return "HEAD"
    rendered = str(value).strip()
    if rendered.lower() in {"", "null", "none", "head"}:
        return "HEAD"
    return rendered


def campaign_toolchain_env(campaign_root: Path) -> dict[str, str]:
    manifest = read_json(campaign_root.resolve() / "execution_manifest.json")
    verify_bound_campaign(manifest)
    return manifest_toolchain_env(manifest)


class VanillaRun:
    def __init__(
        self,
        *,
        route: dict[str, Any],
        campaign_root: Path,
        batch_id: int,
        target: int,
        max_turns: int,
        max_output_tokens: int,
        run_kind: str = "smoke",
    ) -> None:
        self.route = route
        self.campaign_root = campaign_root.resolve()
        self.batch_id = batch_id
        self.target = target
        self.max_turns = max_turns
        self.max_output_tokens = max_output_tokens
        self.run_kind = run_kind
        task = read_json(TASK_SPEC)
        budget = task["method_budget"]
        self.token_budget = int(budget["vanilla_llm_total_tokens"])
        self.wall_time_budget = int(budget["wall_time_seconds"])
        self.search_budget = int(budget["search_requests"])
        self.method_id = str(route["method_id"])
        self.root = (
            self.campaign_root
            / "method_runs"
            / f"{self.method_id}.batch{batch_id}.{run_kind}"
        )
        if self.root.exists():
            raise ExperimentError(f"method workspace already exists: {self.root}")
        self.repos = self.root / "repositories"
        self.synth = self.root / "synth"
        self.logs = self.root / "logs"
        for path in (self.repos, self.synth, self.logs):
            path.mkdir(parents=True)
        self.repo_paths: dict[str, Path] = {}
        self.repo_meta: dict[str, dict[str, str]] = {}
        self.queries: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.clone_count = 0
        self.synth_count = 0
        self.last_search_monotonic: float | None = None
        self.validation_history: list[dict[str, Any]] = []
        self.qualified_candidates: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.prior_candidate_keys = prior_candidate_keys(
            campaign_paths(self.campaign_root), self.method_id, self.batch_id
        )
        self.usage = {"input": 0, "output": 0, "reasoning": 0, "total": 0}
        self.actual_model: str | None = None
        self.provider_fingerprint: str | None = None
        self.final: dict[str, Any] | None = None
        self.started_at = now_iso()
        self.started_monotonic = time.monotonic()
        self.env = campaign_toolchain_env(self.campaign_root)
        self.cpu_cores = int(budget["cpu_cores"])
        if int(budget["max_concurrent_synthesis"]) != 1:
            raise ExperimentError(
                "Vanilla runner supports exactly one concurrent synthesis per method"
            )
        self.env.update(
            {
                "NUM_CORES": str(self.cpu_cores),
                "ORFS_MAX_CPUS": str(self.cpu_cores),
            }
        )

    def event(self, kind: str, value: dict[str, Any]) -> None:
        record = {"timestamp": now_iso(), "kind": kind, **value}
        self.events.append(record)
        with (self.logs / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")

    def repository(self, repo_id: str) -> Path:
        try:
            return self.repo_paths[repo_id]
        except KeyError as exc:
            raise ExperimentError(f"unknown repo_id: {repo_id}") from exc

    def search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if len(self.queries) >= self.search_budget:
            raise ExperimentError("search request budget exhausted")
        query = str(arguments["query"])
        page = int(arguments.get("page", 1))
        per_page = min(int(arguments.get("per_page", 10)), 5)
        timestamp = now_iso()
        self.queries.append(
            {"query": query, "backend": "github", "page": page, "timestamp": timestamp}
        )
        params = urllib.parse.urlencode(
            {"q": query, "page": page, "per_page": per_page}
        )
        headers = {
            "accept": "application/vnd.github+json",
            "user-agent": "r2g-experiment1-vanilla-runner/1.0",
        }
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if token:
            headers["authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            "https://api.github.com/search/repositories?" + params,
            headers=headers,
        )
        if self.last_search_monotonic is not None:
            delay = 2.2 - (time.monotonic() - self.last_search_monotonic)
            if delay > 0:
                time.sleep(delay)
        data: dict[str, Any] | None = None
        last_error: BaseException | None = None
        for attempt, delay in enumerate(NETWORK_RETRY_DELAYS, start=1):
            if delay:
                time.sleep(delay)
            self.last_search_monotonic = time.monotonic()
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    data = json.loads(response.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                last_error = exc
                retryable = exc.code in {403, *TRANSIENT_HTTP_CODES}
                if not retryable or attempt == len(NETWORK_RETRY_DELAYS):
                    raise
                retry_after = exc.headers.get("Retry-After")
                reset_at = exc.headers.get("X-RateLimit-Reset")
                if retry_after:
                    wait_seconds = float(retry_after)
                elif reset_at:
                    wait_seconds = max(2.2, float(reset_at) - time.time() + 1)
                else:
                    wait_seconds = 30.0
                time.sleep(min(90.0, wait_seconds))
            except Exception as exc:
                last_error = exc
                if not is_transient_network_error(exc) or attempt == len(NETWORK_RETRY_DELAYS):
                    raise
        if data is None:
            raise ExperimentError(f"GitHub search failed without a response: {last_error}")
        return {
            "total_count": data.get("total_count", 0),
            "items": [
                {
                    "full_name": item.get("full_name"),
                    "html_url": item.get("html_url"),
                    "description": item.get("description"),
                    "default_branch": item.get("default_branch"),
                    "license_spdx": (item.get("license") or {}).get("spdx_id"),
                    "stars": item.get("stargazers_count"),
                    "updated_at": item.get("updated_at"),
                }
                for item in data.get("items", [])
            ],
        }

    def clone(self, arguments: dict[str, Any]) -> dict[str, Any]:
        repo_url = str(arguments["repo_url"]).rstrip("/")
        if not repo_url.startswith("https://"):
            raise ExperimentError("only HTTPS repositories are allowed")
        checkout = normalized_checkout(arguments.get("commit"))
        repo_id = hashlib.sha256(repo_url.lower().encode()).hexdigest()[:16]
        if repo_id in self.repo_paths:
            return {"repo_id": repo_id, **self.repo_meta[repo_id], "reused": True}
        destination = self.repos / repo_id
        temporary = destination.with_name(destination.name + ".tmp")
        for path in (destination, temporary):
            if path.exists():
                shutil.rmtree(path)
        try:
            command = [
                "git", "clone", "--filter=blob:none", "--no-checkout",
                repo_url, str(temporary),
            ]
            result = None
            for attempt, delay in enumerate(NETWORK_RETRY_DELAYS[:5], start=1):
                if delay:
                    time.sleep(delay)
                if temporary.exists():
                    shutil.rmtree(temporary)
                result = subprocess.run(command, text=True, capture_output=True, timeout=300)
                if result.returncode == 0:
                    break
                detail = result.stderr or result.stdout
                if not is_transient_git_failure(detail) or attempt == 5:
                    raise ExperimentError(f"git clone failed: {detail[-500:]}")
            if result is None or result.returncode:
                raise ExperimentError("git clone failed after bounded retries")
            result = subprocess.run(
                ["git", "checkout", "--detach", checkout],
                cwd=temporary,
                text=True,
                capture_output=True,
                timeout=180,
            )
            if result.returncode:
                raise ExperimentError(
                    f"git checkout failed: {(result.stderr or result.stdout)[-500:]}"
                )
            temporary.replace(destination)
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=destination,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        self.repo_paths[repo_id] = destination
        self.repo_meta[repo_id] = {"repo_url": repo_url, "commit": commit}
        self.clone_count += 1
        return {"repo_id": repo_id, "repo_url": repo_url, "commit": commit, "reused": False}

    def list_files(self, arguments: dict[str, Any]) -> dict[str, Any]:
        root = self.repository(str(arguments["repo_id"]))
        result = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", "HEAD"],
            cwd=root,
            text=True,
            capture_output=True,
            check=True,
        )
        contains = str(arguments.get("contains", "")).lower()
        limit = min(int(arguments.get("limit", 200)), 150)
        values = [
            line for line in result.stdout.splitlines()
            if not contains or contains in line.lower()
        ]
        return {"files": values[:limit], "matched": len(values), "truncated": len(values) > limit}

    def read_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        root = self.repository(str(arguments["repo_id"])).resolve()
        relative = Path(str(arguments["path"]))
        path = (root / relative).resolve()
        if root not in path.parents or not path.is_file():
            raise ExperimentError("file path is missing or escapes repository")
        start = int(arguments.get("start_line", 1))
        count = min(int(arguments.get("line_count", 200)), 120)
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        excerpt = [line[:500] for line in lines[start - 1 : start - 1 + count]]
        return {
            "path": relative.as_posix(),
            "start_line": start,
            "lines": excerpt,
            "total_lines": len(lines),
        }

    def readonly_command(self, arguments: dict[str, Any]) -> dict[str, Any]:
        root = self.repository(str(arguments["repo_id"]))
        argv = [str(item) for item in arguments["argv"]]
        executable = Path(argv[0]).name
        for value in argv[1:]:
            if value.startswith(("--git-dir", "--work-tree")):
                raise ExperimentError("repository escape option is not allowed")
            if value.startswith("-"):
                continue
            path_value = Path(value)
            if path_value.is_absolute() or ".." in path_value.parts:
                raise ExperimentError("absolute and parent paths are not allowed")
        if executable == "git":
            if (
                len(argv) < 2
                or argv[1] == "-C"
                or argv[1] not in READ_ONLY_GIT
            ):
                raise ExperimentError("git subcommand is not read-only or not allowed")
        elif executable not in READ_ONLY_COMMANDS:
            raise ExperimentError(f"command is not allowed: {executable}")
        result = subprocess.run(
            argv,
            cwd=root,
            text=True,
            capture_output=True,
            timeout=30,
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout[-12000:],
            "stderr": result.stderr[-4000:],
            "display": shlex.join(argv),
        }

    def validate_candidate(self, arguments: dict[str, Any]) -> dict[str, Any]:
        repo_id = str(arguments["repo_id"])
        source = self.repository(repo_id)
        candidate = arguments["candidate"]
        candidate_schema = inline_safe_paths(
            copy.deepcopy(read_json(SUBMISSION_SCHEMA)["$defs"]["candidate"])
        )
        errors = validate_json(candidate, candidate_schema)
        if errors:
            return {
                "accepted": False,
                "precheck_qualified": False,
                "failure_class": "candidate_schema_invalid",
                "schema_errors": errors[:20],
            }
        expected = self.repo_meta[repo_id]
        if normalized_repo_url(candidate["repo_url"]) != normalized_repo_url(
            expected["repo_url"]
        ):
            return {
                "accepted": False,
                "precheck_qualified": False,
                "failure_class": "pinned_source_identity_mismatch",
                "identity_field": "repo_url",
                "expected_repo_url": expected["repo_url"],
                "received_repo_url": candidate["repo_url"],
                "synthesis_run": False,
            }
        if candidate["commit"].lower() != expected["commit"].lower():
            raise ExperimentError("candidate commit does not match checked-out source")
        candidate_key = (
            str(candidate["repo_url"]).rstrip("/"),
            str(candidate["commit"]).lower(),
            str(candidate["top_module"]),
        )
        if candidate_key in self.prior_candidate_keys:
            return {
                "accepted": False,
                "precheck_qualified": False,
                "failure_class": "cross_batch_candidate_duplicate",
                "synthesis_run": False,
            }

        inputs = verify_candidate_inputs(candidate, source)
        failure = input_qualification_failure(inputs)
        input_summary = {
            key: inputs[key] for key in CLOSURE_EVIDENCE_FIELDS
        }
        input_summary.update(
            {
                "closure_sha256": inputs["closure_sha256"],
                "license_location_ok": inputs["license_location_ok"],
                "license_spdx_declared": inputs["license_spdx_declared"],
                "license_spdx_observed": inputs["license_spdx_observed"],
                "license_declared": inputs["license_declared"],
            }
        )
        if failure:
            self.validation_history.append(
                {
                    "repo_id": repo_id,
                    "candidate_id": candidate["candidate_id"],
                    "top_module": candidate["top_module"],
                    "precheck_qualified": False,
                    "failure_class": failure,
                }
            )
            return {
                "accepted": True,
                "precheck_qualified": False,
                "failure_class": failure,
                "input_evidence": input_summary,
                "synthesis_run": False,
            }

        self.synth_count += 1
        output = self.synth / f"{self.synth_count:03d}_{candidate['candidate_id']}"
        remaining_wall = int(
            self.wall_time_budget - (time.monotonic() - self.started_monotonic)
        )
        if remaining_wall < 1:
            return {
                "accepted": True,
                "precheck_qualified": False,
                "failure_class": "wall_time_limit",
                "input_evidence": input_summary,
                "synthesis_run": False,
            }
        result = run_formal_synth(
            candidate,
            source,
            output / "project",
            output,
            self.env,
            timeout_seconds=min(3600, remaining_wall),
        )
        failure = synthesis_qualification_failure(result)
        summary = {
            "returncode": result["returncode"],
            "elapsed_seconds": result["elapsed_seconds"],
            "timed_out": result["timed_out"],
            "flow_variant": result["flow_variant"],
            "mapped_netlist_present": bool(result["mapped_netlist"]),
            "mapped_cells": result["mapped_cells"],
            "has_functional_io": result["has_functional_io"],
            "unresolved_module_evidence": result["unresolved_module_evidence"],
            "synth_qualified": failure is None,
        }
        self.validation_history.append(
            {
                "repo_id": repo_id,
                "candidate_id": candidate["candidate_id"],
                "top_module": candidate["top_module"],
                "returncode": summary["returncode"],
                "precheck_qualified": failure is None,
                "failure_class": failure,
                "mapped_cells": summary["mapped_cells"],
            }
        )
        if failure is None:
            key = (
                normalized_repo_url(str(candidate["repo_url"])),
                str(candidate["commit"]).lower(),
                str(candidate["top_module"]),
            )
            self.qualified_candidates.setdefault(key, copy.deepcopy(candidate))
        return {
            "accepted": True,
            "precheck_qualified": failure is None,
            "failure_class": failure,
            "input_evidence": input_summary,
            "synthesis_run": True,
            "synthesis": summary,
        }

    def submit(self, arguments: dict[str, Any]) -> dict[str, Any]:
        candidates = []
        rejected = []
        repository_counts: Counter[str] = Counter()
        for candidate in list(arguments.get("candidates") or [])[: self.target]:
            key = (
                normalized_repo_url(str(candidate.get("repo_url", ""))),
                str(candidate.get("commit", "")).lower(),
                str(candidate.get("top_module", "")),
            )
            qualified = self.qualified_candidates.get(key)
            if qualified is None:
                rejected.append(str(candidate.get("candidate_id", "unknown")))
                continue
            repository = str(qualified.get("repo_url", "")).rstrip("/").removesuffix(".git").lower()
            if repository_counts[repository] >= 4:
                rejected.append(str(candidate.get("candidate_id", "unknown")) + ":repository_cap")
                continue
            if qualified not in candidates:
                candidates.append(copy.deepcopy(qualified))
                repository_counts[repository] += 1
        out_of_scope = list(arguments.get("out_of_scope") or [])
        out_of_scope_schema = read_json(SUBMISSION_SCHEMA)["$defs"]["outOfScope"]
        out_of_scope_errors = []
        for index, item in enumerate(out_of_scope):
            out_of_scope_errors.extend(
                f"out_of_scope/{index}: {error}"
                for error in validate_json(item, out_of_scope_schema)
            )
        if rejected or out_of_scope_errors:
            return {
                "accepted": False,
                "submitted": len(candidates),
                "rejected_unqualified": rejected,
                "schema_errors": out_of_scope_errors[:20],
                "target": self.target,
            }
        self.final = {
            "candidates": candidates,
            "out_of_scope": out_of_scope,
        }
        return {
            "accepted": True,
            "submitted": len(candidates),
            "rejected_unqualified": rejected,
            "target": self.target,
        }

    def execute_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        handlers = {
            "search_repositories": self.search,
            "clone_repository": self.clone,
            "list_repository_files": self.list_files,
            "read_repository_file": self.read_file,
            "run_readonly_command": self.readonly_command,
            "validate_candidate": self.validate_candidate,
            "submit_candidates": self.submit,
        }
        if name not in handlers:
            raise ExperimentError(f"unknown tool: {name}")
        return handlers[name](arguments)

    def api_turn(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        key = os.environ.get(str(self.route["api_key_env"]))
        if not key:
            raise ExperimentError(f"missing credential: {self.route['api_key_env']}")
        api_style = self.route.get("api_style")
        anthropic = api_style == "anthropic_messages"
        responses = api_style == "openai_responses"
        if anthropic:
            system, provider_messages = anthropic_messages(messages)
            payload: dict[str, Any] = {
                "model": self.route["model_id"],
                "system": system,
                "messages": provider_messages,
                "tools": anthropic_tools(),
                "tool_choice": {"type": "auto"},
                "max_tokens": self.max_output_tokens,
            }
        elif responses:
            payload = {
                "model": self.route["model_id"],
                "input": responses_input(messages),
                "tools": responses_tools(),
                "tool_choice": "auto",
                "max_output_tokens": self.max_output_tokens,
            }
        else:
            payload = {
                "model": self.route["model_id"],
                "messages": messages,
                "tools": tool_specs(),
                "tool_choice": "auto",
                "stream": False,
            }
            payload[str(self.route.get("max_output_field", "max_tokens"))] = (
                self.max_output_tokens
            )
        estimated_input = len(
            json.dumps(messages, ensure_ascii=True, separators=(",", ":"))
        ) // 3
        if (
            self.usage["total"] + estimated_input + self.max_output_tokens
            > self.token_budget
        ):
            raise TokenBudgetError("token budget would be exceeded by the next turn")
        headers = {
            "content-type": "application/json",
            "user-agent": "r2g-experiment1-vanilla-runner/1.0",
        }
        if anthropic:
            headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
        else:
            headers["authorization"] = f"Bearer {key}"
        request = urllib.request.Request(
            endpoint_for(self.route),
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        data: dict[str, Any] | None = None
        last_error: BaseException | None = None
        for attempt, delay in enumerate(NETWORK_RETRY_DELAYS, start=1):
            if delay:
                time.sleep(delay)
            try:
                with urllib.request.urlopen(request, timeout=300) as response:
                    data = json.loads(response.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                last_error = exc
                body = (
                    exc.read()
                    .decode("utf-8", errors="replace")
                    .replace(key, "[REDACTED]")
                )
                if exc.code not in TRANSIENT_HTTP_CODES or attempt == len(NETWORK_RETRY_DELAYS):
                    raise ExperimentError(
                        f"provider HTTP {exc.code}: {body[:500]}"
                    ) from exc
                self.event(
                    "network_retry",
                    {"component": "provider", "attempt": attempt, "http_code": exc.code},
                )
            except Exception as exc:
                last_error = exc
                if not is_transient_network_error(exc) or attempt == len(NETWORK_RETRY_DELAYS):
                    raise ExperimentError(f"provider connection failed: {exc}") from exc
                self.event(
                    "network_retry",
                    {"component": "provider", "attempt": attempt, "error": str(exc)[:300]},
                )
        if data is None:
            raise ExperimentError(f"provider failed without a response: {last_error}")
        usage = provider_usage(data)
        for key_name in self.usage:
            self.usage[key_name] += usage[key_name]
        self.actual_model = str(data.get("model") or self.actual_model or "")
        self.provider_fingerprint = data.get("system_fingerprint") or self.provider_fingerprint
        if self.usage["total"] > self.token_budget:
            raise ExperimentError("token budget exhausted")
        if anthropic:
            return anthropic_response_message(data)
        if responses:
            return responses_response_message(data)
        choices = data.get("choices") or []
        if not choices:
            raise ExperimentError("provider returned no choices")
        return choices[0].get("message") or {}

    def compact_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        assistant_indices = [
            index
            for index, message in enumerate(messages)
            if index >= 2 and message.get("role") == "assistant"
        ]
        start = assistant_indices[-4] if len(assistant_indices) >= 4 else 2
        repositories = [
            {"repo_id": repo_id, **metadata}
            for repo_id, metadata in self.repo_meta.items()
        ]
        elapsed_seconds = max(0.0, time.monotonic() - self.started_monotonic)
        token_used = int(self.usage["total"])
        token_fraction = token_used / self.token_budget if self.token_budget else 1.0
        if token_fraction >= 0.9:
            budget_phase = "critical"
        elif token_fraction >= 0.75:
            budget_phase = "low"
        elif token_fraction >= 0.5:
            budget_phase = "half_used"
        else:
            budget_phase = "normal"
        state = {
            "queries_used": len(self.queries),
            "search_budget": self.search_budget,
            "provider_tokens_used": token_used,
            "provider_token_budget": self.token_budget,
            "provider_tokens_remaining": max(0, self.token_budget - token_used),
            "provider_token_budget_fraction_used": round(token_fraction, 4),
            "wall_time_seconds_used": round(elapsed_seconds, 1),
            "wall_time_seconds_budget": self.wall_time_budget,
            "wall_time_seconds_remaining": max(
                0, round(self.wall_time_budget - elapsed_seconds, 1)
            ),
            "budget_phase": budget_phase,
            "repositories": repositories,
            "candidate_validation_history": self.validation_history,
            "qualified_candidate_count": len(self.qualified_candidates),
            "instruction": (
                "Only validate_candidate-qualified candidates receive submission credit. Passing "
                "candidates are already durably checkpointed, so validate candidates "
                "incrementally and call submit_candidates exactly once when finished."
            ),
        }
        return [
            copy.deepcopy(messages[0]),
            copy.deepcopy(messages[1]),
            {
                "role": "user",
                "content": "Deterministic runner state: "
                + json.dumps(state, ensure_ascii=True),
            },
            *copy.deepcopy(messages[start:]),
        ]

    def build_submission(self, stop_reason: str) -> dict[str, Any]:
        final = self.final or {"candidates": [], "out_of_scope": []}
        candidates = final["candidates"][: self.target]
        return {
            "schema_version": "1.0",
            "experiment_id": "r2g-exp1-rtl-acquisition-v1",
            "method_id": self.method_id,
            "batch_id": self.batch_id,
            "model_route": {
                "requested_model": self.route["model_id"],
                "actual_model": self.actual_model or self.route["model_id"],
                "api_provider": self.route["provider"],
                "endpoint_kind": endpoint_kind_for_route(self.route),
                "reasoning_effort": self.route.get("formal_reasoning_policy"),
                "provider_fingerprint": self.provider_fingerprint,
            },
            "started_at": self.started_at,
            "ended_at": now_iso(),
            "stop_reason": stop_reason,
            "resource_usage": {
                "method_runtime_seconds": round(time.monotonic() - self.started_monotonic, 3),
                "input_tokens": self.usage["input"],
                "output_tokens": self.usage["output"],
                "reasoning_tokens": self.usage["reasoning"],
                "total_tokens": self.usage["total"],
                "cost_usd": None,
                "search_requests": len(self.queries),
                "clone_count": self.clone_count,
                "self_synth_attempts": self.synth_count,
                "format_repairs": 0,
                "human_interventions": 0,
            },
            "queries": self.queries,
            "candidates": candidates,
            "out_of_scope": final["out_of_scope"],
        }

    def run(self) -> Path:
        task = read_json(TASK_SPEC)
        if self.run_kind == "smoke":
            completion_instruction = (
                "As soon as one candidate passes validate_candidate, submit it immediately "
                "in this smoke run."
            )
            run_instruction = (
                f"This is a diagnostic smoke run. Submit up to {self.target} candidate(s), "
                "then stop."
            )
        else:
            completion_instruction = (
                f"Continue until you can submit {self.target} qualified candidates or a "
                "frozen resource limit prevents further work. Missing slots count as failures."
            )
            run_instruction = (
                f"This is formal Batch {self.batch_id}. Submit up to {self.target} "
                "candidates, then stop."
            )
        system = (
            "You are a Vanilla LLM baseline for a controlled RTL-acquisition experiment. "
            "You have no R2G Agent code, memory, Recipe, or prior candidate list. Use only "
            "the supplied tools. Find public Verilog/SystemVerilog RTL, pin every Git commit, "
            "identify a real top module and complete compile-input closure, and verify a "
            "repository-relative license file at the pinned commit. license_evidence must use "
            "repository_path (never a remote URL) and a canonical SPDX identifier matching "
            "that file. validate_candidate publicly checks the candidate schema, pinned-source "
            "identity, active compilation-input closure, license path and canonical SPDX match, "
            "and the frozen Sky130HD synthesis gates. Only a full pass receives submission "
            "credit and is durably checkpointed. The independent evaluator later reclones the "
            "pinned commit and reruns the same gates; submitted candidates cannot be replaced "
            "after those results are visible. A main-track candidate must map to at least 100 "
            "and fewer than 100000 cells under the frozen precheck; smaller and larger designs "
            "do not occupy submission slots. The primary comparison reports valid unique "
            "submission rate, independent qualification rate, and diverse-qualified yield, "
            "which is effective repository count among independently qualified candidates "
            "divided by 25. Aim for at least 12 repositories and submit no more than four "
            "candidates from one repository. Missing slots fail; unqualified repositories "
            "cannot increase the diversity metric. Do not invent paths, commits, synthesis "
            "results, or licenses. "
            f"The exact limits are {self.token_budget} cumulative provider tokens, "
            f"{self.search_budget} repository searches, and {self.wall_time_budget} seconds "
            "of method wall time. Current usage and remaining budget are shown in every "
            "deterministic runner-state message. Manage the disclosed budget so inspected "
            "candidates are validated incrementally. "
            + completion_instruction
            + " Call submit_candidates exactly once."
        )
        user = (
            task["core_task_en"]
            + "\n"
            + run_instruction
            + " The twelve seed categories are: "
            + ", ".join(task["seed_categories"])
            + "."
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        # Reaching the deterministic turn budget is an ordinary method outcome.
        # Reserve operator_abort for a real cancellation outside this loop.
        stop_reason = "turn_limit"
        try:
            for turn in range(1, self.max_turns + 1):
                if (
                    time.monotonic() - self.started_monotonic
                    >= self.wall_time_budget
                ):
                    stop_reason = "wall_time_limit"
                    break
                message = self.api_turn(self.compact_messages(messages))
                calls = message.get("tool_calls") or []
                messages.append(assistant_history_message(message))
                self.event(
                    "model_turn",
                    {
                        "turn": turn,
                        "tool_names": [
                            call.get("function", {}).get("name") for call in calls
                        ],
                    },
                )
                if not calls:
                    messages.append(
                        {
                            "role": "user",
                            "content": "Continue using tools. Finish by calling submit_candidates.",
                        }
                    )
                    continue
                for call in calls:
                    name = str(call.get("function", {}).get("name"))
                    try:
                        raw_arguments = call.get("function", {}).get("arguments") or {}
                        arguments = (
                            raw_arguments
                            if isinstance(raw_arguments, dict)
                            else json.loads(raw_arguments)
                        )
                        result = self.execute_tool(name, arguments)
                        self.event("tool_call", {"tool": name, "ok": True})
                    except Exception as exc:
                        result = {"error": type(exc).__name__, "message": str(exc)[:1000]}
                        self.event(
                            "tool_call",
                            {"tool": name, "ok": False, "error": str(exc)[:300]},
                        )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id"),
                            "content": json.dumps(result, ensure_ascii=True)[:12000],
                        }
                    )
                if self.final is not None:
                    stop_reason = submitted_stop_reason(
                        len(self.final.get("candidates") or []), self.target
                    )
                    break
        except TokenBudgetError as exc:
            self.event("method_error", {"error": str(exc)[:1000]})
            stop_reason = "token_limit"
        except ExperimentError as exc:
            self.event("method_error", {"error": str(exc)[:1000]})
            stop_reason = "provider_failure"
        if self.final is None and self.qualified_candidates:
            self.final = {
                "candidates": list(self.qualified_candidates.values())[: self.target],
                "out_of_scope": [],
            }
            self.event(
                "automatic_finalization",
                {
                    "reason": stop_reason,
                    "submitted": len(self.final["candidates"]),
                },
            )
        submission = self.build_submission(stop_reason)
        output = self.root / "submission.json"
        write_json_atomic(output, submission)
        errors = [
            *validate_json(submission, SUBMISSION_SCHEMA),
            *semantic_submission_errors(
                submission, target_candidates=self.target
            ),
        ]
        score_eligible, eligibility_reason = submission_score_eligibility(submission)
        write_json_atomic(
            self.root / "method_result.json",
            {
                "submission": str(output),
                "schema_valid": not errors,
                "errors": errors,
                "submitted": len(submission["candidates"]),
                "target": self.target,
                "stop_reason": submission["stop_reason"],
                "score_eligible": not errors and score_eligible,
                "eligibility_reason": eligibility_reason,
            },
        )
        return output


def load_route(path: Path, method_id: str) -> dict[str, Any]:
    routes = read_json(path).get("routes", [])
    for route in routes:
        if route.get("method_id") == method_id:
            if route.get("api_style") not in {
                "openai_chat", "openai_responses", "anthropic_messages"
            }:
                raise ExperimentError("unsupported Vanilla runner API style")
            return route
    raise ExperimentError(f"route not found: {method_id}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--method-id", choices=sorted(VANILLA_METHODS), required=True
    )
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--batch-id", type=int, choices=range(1, 5), default=1)
    parser.add_argument("--target", type=int, choices=range(1, 26), default=1)
    parser.add_argument("--max-turns", type=int, default=30)
    parser.add_argument("--max-output-tokens", type=int, default=4096)
    parser.add_argument("--run-kind", choices=("smoke", "formal"), default="smoke")
    parser.add_argument(
        "--routes",
        type=Path,
        default=EXPERIMENT_DIR / "experiment1_model_routes.json",
    )
    parser.add_argument("--env-file", type=Path, required=True)
    args = parser.parse_args()
    if args.run_kind == "formal" and args.target != 25:
        parser.error("--run-kind formal requires --target 25")
    task = read_json(TASK_SPEC)
    if args.run_kind == "formal" and (
        args.max_turns
        != int(task["method_budget"]["vanilla_max_turns_per_batch"])
        or args.max_output_tokens
        != int(task["method_budget"]["vanilla_max_output_tokens_per_turn"])
    ):
        parser.error("formal turn/output budgets must match the frozen task spec")
    load_env_file(args.env_file)
    route = load_route(args.routes, args.method_id)
    runner = VanillaRun(
        route=route,
        campaign_root=args.campaign_root,
        batch_id=args.batch_id,
        target=args.target,
        max_turns=args.max_turns,
        max_output_tokens=args.max_output_tokens,
        run_kind=args.run_kind,
    )
    output = runner.run()
    result = read_json(runner.root / "method_result.json")
    print(
        f"{args.method_id}: submitted={result['submitted']}/{result['target']} "
        f"schema_valid={result['schema_valid']} output={output}"
    )
    if not result["schema_valid"]:
        return 2
    return 0 if result["score_eligible"] else 3


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ExperimentError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
