#!/usr/bin/env python3
"""Verify Experiment 1 model routes without persisting secrets or model text."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request
from typing import Any


PROBE_TOOL = {
    "name": "submit_probe",
    "description": "Submit the API route preflight result.",
    "input_schema": {
        "type": "object",
        "properties": {"value": {"type": "string", "enum": ["ok"]}},
        "required": ["value"],
        "additionalProperties": False,
    },
}
PLACEHOLDERS = {"", "xxx", "test", "placeholder", "sk-xxx"}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def is_usable_secret(value: str | None) -> bool:
    if not value:
        return False
    lowered = value.strip().lower()
    return (
        lowered not in PLACEHOLDERS
        and "placeholder" not in lowered
        and "xxx" not in lowered
    )


def config_digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_env_file(path: pathlib.Path) -> None:
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise ValueError(
            f"credential file must not be group/world accessible: {path} mode={mode:o}"
        )
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            raise ValueError(f"invalid credential file line {line_number}")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {"'", '"'}
        ):
            value = value[1:-1]
        if not key or not key.replace("_", "").isalnum():
            raise ValueError(f"invalid environment name on line {line_number}")
        if not is_usable_secret(os.environ.get(key)):
            os.environ[key] = value


def endpoint_for(route: dict[str, Any]) -> str:
    override = os.environ.get(str(route.get("endpoint_env", "")), "").strip()
    if not override:
        return str(route["endpoint"])
    if override.endswith(("/chat/completions", "/messages", "/responses")):
        return override
    base = override.rstrip("/")
    if route["api_style"] == "anthropic_messages":
        return base + "/v1/messages"
    if route["api_style"] == "openai_responses":
        return base + ("/responses" if base.endswith("/v1") else "/v1/responses")
    return base + "/chat/completions"


def openai_payload(route: dict[str, Any]) -> dict[str, Any]:
    function = {
        "name": PROBE_TOOL["name"],
        "description": PROBE_TOOL["description"],
        "parameters": PROBE_TOOL["input_schema"],
    }
    payload: dict[str, Any] = {
        "model": route["model_id"],
        "messages": [
            {
                "role": "system",
                "content": "This is an API preflight. Follow the tool instruction exactly.",
            },
            {
                "role": "user",
                "content": (
                    "Call submit_probe exactly once with value set to ok. "
                    "Do not answer in text."
                ),
            },
        ],
        "tools": [{"type": "function", "function": function}],
        "tool_choice": route.get("tool_choice", "auto"),
        "stream": False,
    }
    payload[str(route.get("max_output_field", "max_tokens"))] = int(
        route.get("max_output_tokens", 256)
    )
    return payload


def responses_payload(route: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": route["model_id"],
        "input": (
            "Call submit_probe exactly once with value set to ok. "
            "Do not answer in text."
        ),
        "tools": [
            {
                "type": "function",
                "name": PROBE_TOOL["name"],
                "description": PROBE_TOOL["description"],
                "parameters": PROBE_TOOL["input_schema"],
                "strict": True,
            }
        ],
        "tool_choice": "required",
        "max_output_tokens": int(route.get("max_output_tokens", 256)),
    }


def anthropic_payload(route: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": route["model_id"],
        "max_tokens": int(route.get("max_output_tokens", 256)),
        "system": "This is an API preflight. Follow the tool instruction exactly.",
        "messages": [
            {
                "role": "user",
                "content": (
                    "Call submit_probe exactly once with value set to ok. "
                    "Do not answer in text."
                ),
            }
        ],
        "tools": [PROBE_TOOL],
        "tool_choice": {"type": "tool", "name": PROBE_TOOL["name"]},
    }


def request_for(route: dict[str, Any], api_key: str) -> urllib.request.Request:
    if route["api_style"] == "anthropic_messages":
        payload = anthropic_payload(route)
        headers = {
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
    elif route["api_style"] == "openai_chat":
        payload = openai_payload(route)
        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {api_key}",
        }
    elif route["api_style"] == "openai_responses":
        payload = responses_payload(route)
        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {api_key}",
        }
    else:
        raise ValueError(f"unsupported api_style: {route['api_style']}")
    headers["user-agent"] = "r2g-experiment1-api-preflight/1.0"
    return urllib.request.Request(
        endpoint_for(route),
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers=headers,
        method="POST",
    )


def parse_json_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    return {}


def numeric_usage(usage: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in usage.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }


def parse_openai_response(
    data: dict[str, Any],
) -> tuple[bool, str | None, dict[str, Any]]:
    choices = data.get("choices") or []
    message = choices[0].get("message", {}) if choices else {}
    calls = message.get("tool_calls") or []
    matched = False
    for call in calls:
        function = call.get("function", {})
        if function.get("name") != PROBE_TOOL["name"]:
            continue
        try:
            arguments = parse_json_arguments(function.get("arguments"))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        matched = arguments.get("value") == "ok"
        if matched:
            break
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return matched, data.get("model"), numeric_usage(usage)


def parse_responses_response(
    data: dict[str, Any],
) -> tuple[bool, str | None, dict[str, Any]]:
    matched = False
    for item in data.get("output") or []:
        if item.get("type") != "function_call" or item.get("name") != PROBE_TOOL["name"]:
            continue
        try:
            arguments = parse_json_arguments(item.get("arguments"))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        matched = arguments.get("value") == "ok"
        if matched:
            break
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return matched, data.get("model"), numeric_usage(usage)


def parse_anthropic_response(
    data: dict[str, Any],
) -> tuple[bool, str | None, dict[str, Any]]:
    matched = False
    for block in data.get("content") or []:
        if (
            block.get("type") != "tool_use"
            or block.get("name") != PROBE_TOOL["name"]
        ):
            continue
        arguments = parse_json_arguments(block.get("input"))
        matched = arguments.get("value") == "ok"
        if matched:
            break
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return matched, data.get("model"), numeric_usage(usage)


def sanitized_error_body(raw: bytes, secret: str) -> dict[str, Any]:
    text = raw.decode("utf-8", errors="replace").replace(secret, "[REDACTED]")
    try:
        body = json.loads(text)
    except json.JSONDecodeError:
        return {"error_type": "non_json_error", "message": text[:300]}
    error = body.get("error", body) if isinstance(body, dict) else {}
    if not isinstance(error, dict):
        return {"error_type": "unknown_error"}
    return {
        "error_type": str(error.get("type") or error.get("code") or "api_error")[
            :120
        ],
        "error_code": str(error.get("code") or "")[:120],
        "message": str(error.get("message") or error.get("error") or "")[:300],
    }


def probe_route(route: dict[str, Any], timeout: int) -> dict[str, Any]:
    started = utc_now()
    key_env = str(route["api_key_env"])
    api_key = os.environ.get(key_env)
    base = {
        "method_id": route["method_id"],
        "provider": route["provider"],
        "channel": route["channel"],
        "requested_model": route["model_id"],
        "api_style": route["api_style"],
        "credential_env": key_env,
        "started_at": started,
    }
    if not is_usable_secret(api_key):
        return {
            **base,
            "status": "blocked_missing_credential",
            "tool_canary_passed": False,
            "usage_present": False,
            "finished_at": utc_now(),
        }

    assert api_key is not None
    try:
        request = request_for(route, api_key)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {
            **base,
            "status": "api_error",
            "http_status": exc.code,
            **sanitized_error_body(exc.read(), api_key),
            "tool_canary_passed": False,
            "usage_present": False,
            "finished_at": utc_now(),
        }
    except (OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        return {
            **base,
            "status": "transport_or_protocol_error",
            "error_type": type(exc).__name__,
            "message": str(exc).replace(api_key, "[REDACTED]")[:300],
            "tool_canary_passed": False,
            "usage_present": False,
            "finished_at": utc_now(),
        }

    if route["api_style"] == "anthropic_messages":
        passed, actual_model, usage = parse_anthropic_response(data)
    elif route["api_style"] == "openai_responses":
        passed, actual_model, usage = parse_responses_response(data)
    else:
        passed, actual_model, usage = parse_openai_response(data)
    model_matches = actual_model == route["model_id"]
    if not model_matches:
        status = "model_identity_mismatch"
    else:
        status = "ready" if passed and usage else "invalid_canary_response"
    return {
        **base,
        "status": status,
        "actual_model": actual_model,
        "actual_model_matches_requested": model_matches,
        "tool_canary_passed": passed,
        "usage_present": bool(usage),
        "usage": usage,
        "finished_at": utc_now(),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--routes",
        type=pathlib.Path,
        default=pathlib.Path(
            "docs/experiments/rtl-acquisition/experiment1_model_routes.json"
        ),
    )
    parser.add_argument("--output", type=pathlib.Path)
    parser.add_argument(
        "--env-file",
        type=pathlib.Path,
        help="Optional mode-600 credential file outside the repository.",
    )
    parser.add_argument("--method", action="append", default=[])
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument(
        "--allow-blocked",
        action="store_true",
        help="Return success even when one or more selected routes are not ready.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.env_file:
        try:
            load_env_file(args.env_file)
        except (OSError, ValueError) as exc:
            print(f"cannot load credential file: {exc}", file=sys.stderr)
            return 2
    config = json.loads(args.routes.read_text(encoding="utf-8"))
    selected = set(args.method)
    routes = [
        route
        for route in config.get("routes", [])
        if not selected or route.get("method_id") in selected
    ]
    known = {route.get("method_id") for route in config.get("routes", [])}
    unknown = selected - known
    if unknown:
        print(f"unknown method(s): {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2
    if not routes:
        print("no routes selected", file=sys.stderr)
        return 2

    results = [probe_route(route, args.timeout) for route in routes]
    report = {
        "schema_version": "1.0",
        "generated_at": utc_now(),
        "routes_path": str(args.routes.resolve()),
        "routes_sha256": config_digest(args.routes),
        "summary": {
            "selected": len(results),
            "ready": sum(item["status"] == "ready" for item in results),
            "blocked_or_failed": sum(item["status"] != "ready" for item in results),
        },
        "results": results,
    }
    rendered = json.dumps(report, indent=2, ensure_ascii=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(
        f"API routes ready: {report['summary']['ready']}/{report['summary']['selected']}"
    )
    for item in results:
        print(
            f"{item['method_id']}: {item['status']} "
            f"(credential={item['credential_env']})"
        )
    if report["summary"]["blocked_or_failed"] and not args.allow_blocked:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
