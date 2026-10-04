#!/usr/bin/env python3
"""Run one provider-neutral Vanilla LLM method for Experiment 2 Pilot."""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from typing import Any
import urllib.error
import urllib.request


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.experiment2_signoff import (  # noqa: E402
    DEFAULT_COHORT,
    DEFAULT_TASK_SPEC,
    Experiment2Error,
    TARGET_FREQUENCY_MHZ,
    append_jsonl,
    copy_checkpoint,
    evaluate_checkpoint,
    fixture_by_id,
    latest_backend_run,
    list_bounded_project_files,
    load_cohort,
    now_iso,
    project_paths,
    public_fixture_id,
    read_bounded_project_file,
    read_json,
    run,
    run_strict_measurement,
    set_bounded_core_utilization,
    set_bounded_explicit_area,
    set_bounded_string_knob,
    sha256_file,
    sha256_tree,
    signoff_environment,
    target_period,
    verify_campaign_bindings,
    write_json_atomic,
)
from tools.preflight_experiment1_model_routes import endpoint_for, load_env_file  # noqa: E402
from tools.run_experiment1_vanilla_method import (  # noqa: E402
    NETWORK_RETRY_DELAYS,
    TRANSIENT_HTTP_CODES,
    TokenBudgetError,
    assistant_history_message,
    endpoint_kind_for_route,
    is_transient_network_error,
    provider_usage,
)


DEFAULT_ROUTES = REPO / "docs" / "experiments" / "signoff" / "experiment2_model_routes.json"
DEFAULT_ENV = Path.home() / ".config" / "r2g" / "experiment1_api.env"
METHODS = {"openai-vanilla", "qwen-vanilla", "deepseek-vanilla"}
EDITABLE_NUMERIC_KNOBS = {
    "CORE_UTILIZATION",
    "PLACE_DENSITY_LB_ADDON",
    "PLACE_DENSITY",
    "ROUTING_LAYER_ADJUSTMENT",
    "SETUP_SLACK_MARGIN",
    "HOLD_SLACK_MARGIN",
    "TNS_END_PERCENT",
    "CTS_CLUSTER_SIZE",
    "CTS_CLUSTER_DIAMETER",
    "CTS_BUF_DISTANCE",
    "GPL_TIMING_DRIVEN",
    "GPL_ROUTABILITY_DRIVEN",
    "SKIP_GATE_CLONING",
    "SKIP_LAST_GASP",
    "SKIP_CTS_REPAIR_TIMING",
}


def load_experiment2_route(path: Path, method_id: str) -> dict[str, Any]:
    document = read_json(path, {}) or {}
    for route in document.get("routes", []):
        if route.get("method_id") != method_id:
            continue
        if route.get("api_style") not in {"openai_chat", "openai_responses"}:
            raise Experiment2Error(f"unsupported Experiment 2 API style: {route.get('api_style')}")
        return route
    raise Experiment2Error(f"route not found: {method_id}")


def responses_tool_specs() -> list[dict[str, Any]]:
    result = []
    for item in tool_specs():
        function = item["function"]
        result.append(
            {
                "type": "function",
                "name": function["name"],
                "description": function.get("description", ""),
                "parameters": function["parameters"],
            }
        )
    return result


def responses_input(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    instructions = []
    items: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content") or ""
        if role == "system":
            instructions.append(str(content))
            continue
        if role in {"user", "assistant"} and content:
            items.append({"role": role, "content": str(content)})
        if role == "assistant":
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                arguments = function.get("arguments") or "{}"
                if not isinstance(arguments, str):
                    arguments = json.dumps(arguments, separators=(",", ":"))
                items.append(
                    {
                        "type": "function_call",
                        "call_id": str(call.get("id") or ""),
                        "name": str(function.get("name") or ""),
                        "arguments": arguments,
                    }
                )
        elif role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": str(message.get("tool_call_id") or ""),
                    "output": str(content),
                }
            )
    return "\n\n".join(instructions), items


def responses_payload(
    route: dict[str, Any], messages: list[dict[str, Any]], max_output_tokens: int
) -> dict[str, Any]:
    instructions, items = responses_input(messages)
    return {
        "model": route["model_id"],
        "instructions": instructions,
        "input": items,
        "tools": responses_tool_specs(),
        "tool_choice": route.get("tool_choice", "auto"),
        "max_output_tokens": max_output_tokens,
        "store": False,
    }


def chat_message_from_responses(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("status") != "completed":
        detail = data.get("error") or data.get("incomplete_details") or data.get("status")
        raise Experiment2Error(f"Responses request did not complete: {detail}")
    text_blocks = []
    calls = []
    for item in data.get("output") or []:
        if item.get("type") == "function_call":
            calls.append(
                {
                    "id": item.get("call_id") or item.get("id"),
                    "type": "function",
                    "function": {
                        "name": item.get("name"),
                        "arguments": item.get("arguments") or "{}",
                    },
                }
            )
        elif item.get("type") == "message":
            for block in item.get("content") or []:
                if block.get("type") in {"output_text", "text"} and block.get("text"):
                    text_blocks.append(str(block["text"]))
    result: dict[str, Any] = {"role": "assistant", "content": "\n".join(text_blocks)}
    if calls:
        result["tool_calls"] = calls
    return result


def tool_specs() -> list[dict[str, Any]]:
    definitions = [
        {
            "name": "inspect_state",
            "description": "Inspect the current frequency, config, attempt history, latest run, reports and remaining budget.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "read_project_file",
            "description": "Read a bounded frozen RTL, config, SDC, report, manifest, or backend log. Pass a project-relative path such as rtl/src/top.v, constraints/config.mk, or reports/drc.json; never pass an absolute path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer", "minimum": 1},
                    "line_count": {"type": "integer", "minimum": 1, "maximum": 400},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
        {
            "name": "list_project_files",
            "description": "List bounded project-relative file names under rtl, constraints, reports, or backend. Use this after a flow instead of guessing report or log paths. It returns names and sizes only, never file contents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prefix": {"type": "string", "description": "Project-relative file or directory prefix, for example backend or reports."},
                    "contains": {"type": "string", "description": "Optional case-insensitive substring filter such as flow.log or 3_2_place_iop.log."},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                "required": ["prefix"],
                "additionalProperties": False,
            },
        },
        {
            "name": "set_orfs_knob",
            "description": "Set one allowed numeric ORFS implementation knob. RTL, platform, footprint, clock identity and signoff rules are protected.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "enum": sorted(EDITABLE_NUMERIC_KNOBS)},
                    "value": {"type": "number"},
                },
                "required": ["name", "value"],
                "additionalProperties": False,
            },
        },
        {
            "name": "set_floorplan_area",
            "description": "Set bounded explicit die dimensions for a fixture whose registered pin-capacity action permits it; the core margin is frozen by the evaluator.",
            "parameters": {
                "type": "object",
                "properties": {
                    "die_width_um": {"type": "number"},
                    "die_height_um": {"type": "number"},
                },
                "required": ["die_width_um", "die_height_um"],
                "additionalProperties": False,
            },
        },
        {
            "name": "set_orfs_string_knob",
            "description": "Set one fixture-registered string-valued ORFS implementation knob. Values outside the frozen action policy are rejected.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["name", "value"],
                "additionalProperties": False,
            },
        },
        {
            "name": "run_orfs_flow",
            "description": "Run one complete ORFS physical implementation at the current protected target and config. This can consume substantial time.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "validate_checkpoint",
            "description": "Run strict DRC, LVS, route, setup/hold, antenna, RCX and provenance measurement on the latest flow. Returns raw gates and no repair advice.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "lock_checkpoint",
            "description": "Durably lock the current checkpoint if and only if it is strict-clean at the fixed 100 MHz target.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "submit_first_clean",
            "description": "Finish the campaign and submit the first locked strict-clean checkpoint, or explicitly submit no clean checkpoint.",
            "parameters": {
                "type": "object",
                "properties": {"note": {"type": "string", "maxLength": 1000}},
                "required": ["note"],
                "additionalProperties": False,
            },
        },
    ]
    return [{"type": "function", "function": item} for item in definitions]


class VanillaSignoffRun:
    def __init__(
        self,
        *,
        route: dict[str, Any],
        campaign_root: Path,
        method_id: str,
        fixture_id: str,
        token_budget: int,
        wall_time_budget: int,
        max_turns: int,
        max_output_tokens: int,
        flow_timeout: int,
        max_flow_attempts: int,
    ) -> None:
        if method_id not in METHODS:
            raise Experiment2Error(f"not a Vanilla Experiment 2 method: {method_id}")
        self.route = route
        self.campaign_root = campaign_root.resolve()
        self.method_id = method_id
        self.fixture_id = fixture_id
        manifest = verify_campaign_bindings(self.campaign_root)
        cohort = load_cohort(Path(manifest["cohort"]["path"]))
        self.fixture = fixture_by_id(cohort, fixture_id)
        task = read_json(Path(manifest["task_spec"]["path"]), {}) or {}
        self.protocol_version = str(task.get("protocol_version") or "legacy")
        self.baseline_first = self.protocol_version == "baseline_first_v2"
        self.public_id = public_fixture_id(self.fixture)
        self.paths = project_paths(self.campaign_root, method_id, fixture_id)
        self.project = self.paths["project"]
        self.runtime_skills = self.campaign_root / "runtime" / "r2g-skills"
        self.token_budget = token_budget
        self.wall_time_budget = wall_time_budget
        self.max_turns = max_turns
        self.max_output_tokens = max_output_tokens
        self.flow_timeout = flow_timeout
        self.max_flow_attempts = max_flow_attempts
        self.started_at = now_iso()
        self.started_monotonic = time.monotonic()
        self.usage = {"input": 0, "output": 0, "reasoning": 0, "total": 0}
        self.actual_model = ""
        self.provider_fingerprint = None
        self.final: dict[str, Any] | None = None
        self.events = self.paths["method"] / "vanilla_events.jsonl"
        self.baseline_path = self.paths["method"] / "baseline_observation.json"

    def event(self, kind: str, value: dict[str, Any]) -> None:
        append_jsonl(self.events, {"at": now_iso(), "kind": kind, **value})

    def remaining_seconds(self) -> float:
        return max(0.0, self.wall_time_budget - (time.monotonic() - self.started_monotonic))

    def flow_attempt_count(self) -> int:
        if not self.paths["attempts"].is_file():
            return 0
        return sum(
            '"event": "flow"' in line
            for line in self.paths["attempts"].read_text(encoding="utf-8").splitlines()
        )

    def baseline_observed(self) -> bool:
        value = read_json(self.baseline_path, {}) or {}
        return bool(
            value.get("status") == "observed"
            and value.get("validation_sha256")
            and value.get("role_consistent") is True
        )

    def require_baseline_before_mutation(self) -> None:
        if self.baseline_first and not self.baseline_observed():
            raise Experiment2Error(
                "baseline-first protocol: run the untouched ORFS baseline and validate it "
                "before changing any implementation knob"
            )

    def state(self) -> dict[str, Any]:
        config = (self.project / "constraints" / "config.mk").read_text(encoding="utf-8", errors="ignore")
        period = target_period(self.project)
        campaign_state = read_json(self.paths["method"] / "campaign_state.json", {}) or {}
        validations = []
        for path in sorted(self.paths["validations"].glob("validation_*.json"))[-3:]:
            data = read_json(path, {}) or {}
            validations.append({"path": path.name, "strict_clean": data.get("strict_clean"), "metrics": data.get("metrics"), "failures": data.get("failures")})
        attempts = []
        if self.paths["attempts"].is_file():
            for line in self.paths["attempts"].read_text(encoding="utf-8").splitlines()[-8:]:
                try:
                    attempts.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        flow_attempts = self.flow_attempt_count()
        latest_run = latest_backend_run(self.project)
        return {
            "fixture_id": self.public_id,
            "mapped_cells": self.fixture["mapped_cells"],
            "size_bin": self.fixture["size_bin"],
            "clock_port": self.fixture["clock_port"],
            "footprint_policy": self.fixture.get("footprint_policy") or {
                "mode": "fixed_area",
                "die_area": self.fixture["die_area"],
                "core_area": self.fixture["core_area"],
            },
            "readable_project_roots": ["rtl", "constraints", "reports", "backend"],
            "readable_project_files": ["metadata.json", "experiment2_input_manifest.json"],
            "path_rule": "read_project_file requires a project-relative path, never an absolute path",
            "latest_backend_run": str(latest_run.relative_to(self.project)) if latest_run else None,
            "path_discovery_rule": "Call list_project_files after a flow; do not guess backend report or log paths.",
            "target_period_ns": period,
            "target_frequency_mhz": 1000.0 / period if period else None,
            "config_mk": config,
            "recent_attempts": attempts,
            "recent_validations": validations,
            "best_checkpoint": campaign_state.get("best_checkpoint"),
            "baseline_observation": read_json(self.baseline_path, None),
            "provider_tokens_used": self.usage["total"],
            "provider_tokens_remaining": max(0, self.token_budget - self.usage["total"]),
            "orfs_flow_attempts_remaining": max(0, self.max_flow_attempts - flow_attempts),
            "wall_time_seconds_remaining": round(self.remaining_seconds(), 1),
        }

    def read_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return read_bounded_project_file(self.project, arguments)

    def list_files(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return list_bounded_project_files(self.project, arguments)

    def set_knob(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self.require_baseline_before_mutation()
        name = str(arguments["name"])
        value = float(arguments["value"])
        if name not in EDITABLE_NUMERIC_KNOBS or not math.isfinite(value):
            raise Experiment2Error("knob is not allowed or value is non-finite")
        fixture_allowed = (self.fixture.get("action_policy") or {}).get("allowed_numeric_knobs")
        if fixture_allowed is not None and name not in fixture_allowed:
            raise Experiment2Error("numeric knob is outside the fixture action policy")
        if name == "CORE_UTILIZATION":
            result = set_bounded_core_utilization(self.project, self.fixture, value)
            self.event("set_knob", {"name": name, "value": value})
            return {"name": name, "value": value, "bounds": result}
        config_path = self.project / "constraints" / "config.mk"
        text = config_path.read_text(encoding="utf-8")
        pattern = re.compile(rf"(?m)^\s*export\s+{re.escape(name)}\s*=.*$")
        line = f"export {name} = {value:g}"
        text = pattern.sub(line, text) if pattern.search(text) else text.rstrip() + "\n" + line + "\n"
        config_path.write_text(text, encoding="utf-8")
        self.event("set_knob", {"name": name, "value": value})
        return {"name": name, "value": value}

    def run_flow(self) -> dict[str, Any]:
        if self.remaining_seconds() <= 60:
            raise Experiment2Error("insufficient wall-time budget for another flow")
        attempt = self.flow_attempt_count() + 1
        if attempt > self.max_flow_attempts:
            raise Experiment2Error(
                f"ORFS flow-attempt limit reached ({self.max_flow_attempts}); submit the best checkpoint"
            )
        if self.baseline_first and attempt == 1:
            campaign_state = read_json(self.paths["method"] / "campaign_state.json", {}) or {}
            observed = sha256_file(self.project / "constraints" / "config.mk")
            if observed != campaign_state.get("initial_config_sha256"):
                raise Experiment2Error(
                    "baseline-first protocol: initial config changed before the mandatory baseline"
                )
        variant = f"exp2_{self.method_id.replace('-', '_')}_{self.public_id}_{attempt:02d}"
        env = signoff_environment(self.runtime_skills, self.campaign_root, self.project)
        timeout = max(60, min(self.flow_timeout, int(self.remaining_seconds())))
        env["ORFS_TIMEOUT"] = str(timeout)
        command = ["bash", str(self.runtime_skills / "signoff-loop" / "scripts" / "flow" / "run_orfs.sh"), str(self.project), "sky130hd", variant]
        started = time.monotonic()
        result = run(command, cwd=self.campaign_root, env=env, log=self.paths["logs"] / f"flow_{attempt:02d}.log", timeout=timeout + 180)
        period = target_period(self.project)
        latest_run = latest_backend_run(self.project)
        row = {"event": "flow", "at": now_iso(), "attempt": attempt, "flow_variant": variant, "period_ns": period, "frequency_mhz": 1000.0 / period if period else None, "returncode": result.returncode, "elapsed_seconds": round(time.monotonic() - started, 3), "latest_backend_run": str(latest_run.relative_to(self.project)) if latest_run else None}
        append_jsonl(self.paths["attempts"], row)
        self.event("flow", row)
        return row

    def validate(self) -> dict[str, Any]:
        if self.remaining_seconds() <= 60:
            raise Experiment2Error("insufficient wall-time budget for strict validation")
        if self.baseline_first and self.flow_attempt_count() < 1:
            raise Experiment2Error(
                "baseline-first protocol: run the untouched ORFS baseline before validation"
            )
        index = len(list(self.paths["validations"].glob("validation_*.json"))) + 1
        result = run_strict_measurement(self.project, self.fixture, self.runtime_skills, self.campaign_root, self.paths["logs"] / f"validation_{index:02d}")
        output = self.paths["validations"] / f"validation_{index:02d}.json"
        write_json_atomic(output, result)
        append_jsonl(self.paths["attempts"], {"event": "validate", "at": now_iso(), "result": str(output), "strict_clean": result["strict_clean"], "metrics": result["metrics"]})
        if self.baseline_first and not self.baseline_observed():
            expected_clean = self.fixture.get("role") == "clean_sentinel"
            record = {
                "schema_version": "1.0",
                "status": "observed",
                "public_id": self.public_id,
                "observed_at": now_iso(),
                "validation_path": output.name,
                "validation_sha256": sha256_file(output),
                "strict_clean": result.get("strict_clean"),
                "metrics": result.get("metrics"),
                "failures": result.get("failures"),
                "role_consistent": result.get("strict_clean") is expected_clean,
            }
            write_json_atomic(self.baseline_path, record)
            self.event("baseline_observed", record)
            if not record["role_consistent"]:
                raise Experiment2Error(
                    "observed baseline contradicts the preregistered cohort role; fixture is ineligible"
                )
        return result

    def lock(self) -> dict[str, Any]:
        result = evaluate_checkpoint(self.project, self.fixture)
        if not result["strict_clean"]:
            return {"locked": False, "reason": "current checkpoint is not strict-clean", "gates": result["gates"]}
        frequency = float(result["metrics"]["frequency_mhz"])
        destination = self.paths["checkpoints"] / f"{frequency:.6f}MHz" / "project"
        if not destination.exists():
            copy_checkpoint(self.project, destination)
        record = {"locked_at": now_iso(), "method_id": self.method_id, "fixture_id": self.public_id, "frequency_mhz": frequency, "project": str(destination), "tree_sha256": sha256_tree(destination)}
        write_json_atomic(destination.parent / "checkpoint.json", record)
        state_path = self.paths["method"] / "campaign_state.json"
        state = read_json(state_path, {}) or {}
        if not state.get("best_checkpoint"):
            state["best_checkpoint"] = record
        state["status"] = "checkpoint_locked"
        write_json_atomic(state_path, state)
        self.final = {
            "best_checkpoint": state["best_checkpoint"],
            "note": "automatic submission after first strict-clean checkpoint",
        }
        return {"locked": True, "submitted": True, **record}

    def execute_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "inspect_state":
            return self.state()
        if name == "read_project_file":
            return self.read_file(arguments)
        if name == "list_project_files":
            return self.list_files(arguments)
        if name == "set_orfs_knob":
            return self.set_knob(arguments)
        if name == "set_floorplan_area":
            self.require_baseline_before_mutation()
            result = set_bounded_explicit_area(
                self.project,
                self.fixture,
                float(arguments["die_width_um"]),
                float(arguments["die_height_um"]),
            )
            self.event("set_floorplan_area", result)
            return result
        if name == "set_orfs_string_knob":
            self.require_baseline_before_mutation()
            result = set_bounded_string_knob(
                self.project,
                self.fixture,
                str(arguments["name"]),
                str(arguments["value"]),
            )
            self.event("set_string_knob", result)
            return result
        if name == "run_orfs_flow":
            return self.run_flow()
        if name == "validate_checkpoint":
            return self.validate()
        if name == "lock_checkpoint":
            return self.lock()
        if name == "submit_first_clean":
            state = read_json(self.paths["method"] / "campaign_state.json", {}) or {}
            self.final = {"best_checkpoint": state.get("best_checkpoint"), "note": str(arguments["note"])}
            return {"submitted": True, **self.final}
        raise Experiment2Error(f"unknown tool: {name}")

    def api_turn(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        key = os.environ.get(str(self.route["api_key_env"]))
        if not key:
            raise Experiment2Error(f"missing credential: {self.route['api_key_env']}")
        api_style = self.route.get("api_style")
        if api_style == "openai_responses":
            payload = responses_payload(self.route, messages, self.max_output_tokens)
        else:
            payload = {"model": self.route["model_id"], "messages": messages, "tools": tool_specs(), "tool_choice": "auto", "stream": False}
            payload[str(self.route.get("max_output_field", "max_tokens"))] = self.max_output_tokens
        estimated_input = len(json.dumps(payload, ensure_ascii=True, separators=(",", ":"))) // 3
        estimated_input += int(self.route.get("request_overhead_reserve_tokens", 0))
        if self.usage["total"] + estimated_input + self.max_output_tokens > self.token_budget:
            raise TokenBudgetError("token budget would be exceeded by next turn")
        request = urllib.request.Request(
            endpoint_for(self.route), data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={"content-type": "application/json", "authorization": f"Bearer {key}", "user-agent": "r2g-experiment2-vanilla-runner/1.0"}, method="POST",
        )
        data = None
        for attempt, delay in enumerate(NETWORK_RETRY_DELAYS, 1):
            if delay:
                time.sleep(delay)
            try:
                with urllib.request.urlopen(request, timeout=300) as response:
                    data = json.loads(response.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace").replace(key, "[REDACTED]")
                if exc.code not in TRANSIENT_HTTP_CODES or attempt == len(NETWORK_RETRY_DELAYS):
                    raise Experiment2Error(f"provider HTTP {exc.code}: {body[:500]}") from exc
                self.event("network_retry", {"attempt": attempt, "http_code": exc.code})
            except Exception as exc:
                if not is_transient_network_error(exc) or attempt == len(NETWORK_RETRY_DELAYS):
                    raise Experiment2Error(f"provider connection failed: {exc}") from exc
                self.event("network_retry", {"attempt": attempt, "error": str(exc)[:300]})
        if data is None:
            raise Experiment2Error("provider returned no response")
        usage = provider_usage(data)
        for field in self.usage:
            self.usage[field] += usage[field]
        self.event(
            "token_usage",
            {
                "turn_usage": usage,
                "cumulative_usage": dict(self.usage),
                "provider_tokens_remaining": max(0, self.token_budget - self.usage["total"]),
            },
        )
        if self.usage["total"] > self.token_budget:
            raise TokenBudgetError("token budget exhausted")
        self.actual_model = str(data.get("model") or self.actual_model or self.route["model_id"])
        self.provider_fingerprint = data.get("system_fingerprint") or self.provider_fingerprint
        if api_style == "openai_responses":
            return chat_message_from_responses(data)
        choices = data.get("choices") or []
        if not choices:
            raise Experiment2Error("provider returned no choices")
        return choices[0].get("message") or {}

    def compact(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        assistant_indices = [i for i, item in enumerate(messages) if i >= 2 and item.get("role") == "assistant"]
        start = assistant_indices[-3] if len(assistant_indices) >= 3 else 2
        return [copy.deepcopy(messages[0]), copy.deepcopy(messages[1]), {"role": "user", "content": "Deterministic runner state: " + json.dumps(self.state(), ensure_ascii=True)}, *copy.deepcopy(messages[start:])]

    def run_agent(self) -> Path:
        system = (
            "You are a Vanilla LLM baseline in a controlled Sky130HD physical-design experiment. "
            "You have the same frozen RTL, footprint, EDA tools, low-level flow executor and public hard-gate validator as every method, but no R2G skills, memory, Recipe, diagnosis, ranking or knowledge database. "
            "The target is frozen at 100 MHz (10 ns). Choose allowed ORFS implementation knobs, run the flow, inspect raw logs/reports, and stop after preserving the first strict-clean checkpoint. "
            "Strict clean requires complete synthesis through finish, route=0, full-deck DRC=0, LVS clean, setup and hold WNS>=0 with TNS=0, antenna=0, RCX complete and same-run provenance. Missing, skipped, unknown or timeout fails. "
            "Do not modify RTL, top, platform, library, clock identity, signoff decks, or add false/multicycle/disabled timing checks. Obey the disclosed fixture action and footprint policies; every out-of-policy action is rejected. A dirty fast result is never better than a slower strict-clean result. "
            "Never modify the target frequency or clock period. Call validate_checkpoint before lock_checkpoint. Finish with submit_first_clean if no strict-clean checkpoint is found."
        )
        if self.baseline_first:
            system += (
                " The baseline-first protocol is mandatory: before changing any knob, run one "
                "complete ORFS flow with the untouched initial configuration and call "
                "validate_checkpoint. Only after that raw baseline evidence is recorded may you "
                "diagnose and apply a repair. The four-flow limit includes this baseline, leaving "
                "at most three repair flows."
            )
        user = (
            f"Design {self.public_id}: {self.fixture['mapped_cells']} mapped cells in the frozen Experiment 1 qualification; "
            f"primary clock {self.fixture['clock_port']}; footprint policy "
            f"{json.dumps(self.fixture.get('footprint_policy') or {'mode': 'fixed_area', 'die_area': self.fixture['die_area'], 'core_area': self.fixture['core_area']}, sort_keys=True)}. "
            f"Registered public action policy {json.dumps(self.fixture.get('action_policy') or {}, sort_keys=True)}. "
            f"Limits: {self.wall_time_budget} seconds, {self.token_budget} provider tokens, "
            f"at most {self.max_flow_attempts} complete ORFS flows, fixed {TARGET_FREQUENCY_MHZ:g} MHz "
            "target, four CPU cores and one ORFS flow at a time. Token use, ORFS calls and wall "
            "time are reported as efficiency outcomes. Stop immediately after the first verified "
            "strict-clean checkpoint."
        )
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        stop_reason = "max_turns"
        try:
            for turn in range(1, self.max_turns + 1):
                if self.remaining_seconds() <= 0:
                    stop_reason = "wall_time_limit"
                    break
                message = self.api_turn(self.compact(messages))
                calls = message.get("tool_calls") or []
                messages.append(assistant_history_message(message))
                self.event("model_turn", {"turn": turn, "tools": [call.get("function", {}).get("name") for call in calls]})
                if not calls:
                    messages.append({"role": "user", "content": "Continue with tools and finish by calling submit_first_clean."})
                    continue
                for call in calls:
                    name = str(call.get("function", {}).get("name"))
                    arguments: dict[str, Any] = {}
                    try:
                        raw = call.get("function", {}).get("arguments") or {}
                        arguments = raw if isinstance(raw, dict) else json.loads(raw)
                        output = self.execute_tool(name, arguments)
                        self.event("tool_call", {"tool": name, "arguments": arguments, "ok": True})
                    except Exception as exc:
                        output = {"error": type(exc).__name__, "message": str(exc)[:1000]}
                        self.event("tool_call", {"tool": name, "arguments": arguments, "ok": False, "error": str(exc)[:300]})
                    messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": json.dumps(output, ensure_ascii=True)[:16000]})
                if self.final is not None:
                    stop_reason = "submitted"
                    break
        except TokenBudgetError:
            stop_reason = "token_limit"
        except Experiment2Error as exc:
            stop_reason = "provider_failure"
            self.event("method_error", {"error": str(exc)[:1000]})
        if self.final is None:
            state = read_json(self.paths["method"] / "campaign_state.json", {}) or {}
            self.final = {"best_checkpoint": state.get("best_checkpoint"), "note": "automatic finalization at resource/turn limit"}
        result = {
            "schema_version": "1.0", "experiment_id": "r2g-exp2-orfs-signoff-pilot",
            "method_id": self.method_id, "fixture_id": self.fixture_id,
            "public_id": self.public_id, "protocol_version": self.protocol_version,
            "started_at": self.started_at, "ended_at": now_iso(), "stop_reason": stop_reason,
            "model_route": {"requested_model": self.route["model_id"], "actual_model": self.actual_model or self.route["model_id"], "api_provider": self.route["provider"], "endpoint_kind": endpoint_kind_for_route(self.route), "provider_fingerprint": self.provider_fingerprint},
            "resource_usage": {"wall_time_seconds": round(time.monotonic() - self.started_monotonic, 3), "input_tokens": self.usage["input"], "output_tokens": self.usage["output"], "reasoning_tokens": self.usage["reasoning"], "total_tokens": self.usage["total"], "human_interventions": 0},
            "strict_clean_before_independent_final_evaluation": bool(
                (self.final or {}).get("best_checkpoint")
            ),
            "submission": self.final,
        }
        output = self.paths["method"] / "vanilla_submission.json"
        write_json_atomic(output, result)
        return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--method", choices=sorted(METHODS), required=True)
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--routes", type=Path, default=DEFAULT_ROUTES)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--token-budget", type=int, default=300_000)
    parser.add_argument("--wall-time-seconds", type=int, default=14_400)
    parser.add_argument("--max-turns", type=int, default=50)
    parser.add_argument("--max-flow-attempts", type=int, default=4)
    parser.add_argument("--max-output-tokens", type=int, default=2048)
    parser.add_argument("--flow-timeout-seconds", type=int, default=7200)
    args = parser.parse_args()
    if args.env_file.is_file():
        load_env_file(args.env_file)
    try:
        manifest = verify_campaign_bindings(args.campaign_root)
        bound_routes = Path(manifest["model_routes"]["path"])
        if args.routes.resolve() != bound_routes.resolve():
            raise Experiment2Error(f"model routes differ from campaign binding: {args.routes}")
        route = load_experiment2_route(bound_routes, args.method)
        output = VanillaSignoffRun(
            route=route, campaign_root=args.campaign_root, method_id=args.method,
            fixture_id=args.fixture, token_budget=args.token_budget,
            wall_time_budget=args.wall_time_seconds, max_turns=args.max_turns,
            max_output_tokens=args.max_output_tokens, flow_timeout=args.flow_timeout_seconds,
            max_flow_attempts=args.max_flow_attempts,
        ).run_agent()
    except Experiment2Error as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
