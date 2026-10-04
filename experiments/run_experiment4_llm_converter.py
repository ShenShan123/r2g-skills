#!/usr/bin/env python3
"""Develop and freeze one Experiment 4 LLM-generated graph converter."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any
import urllib.error
import urllib.request


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_env_file(path: Path | None) -> None:
    if path is None:
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def route_by_key(path: Path, model_key: str) -> dict[str, Any]:
    matches = [row for row in read_json(path)["routes"] if row["model_key"] == model_key]
    if len(matches) != 1:
        raise ValueError(f"model route is not unique: {model_key}")
    return matches[0]


def endpoint_for(route: dict[str, Any]) -> str:
    override = os.environ.get(str(route.get("endpoint_env") or ""), "").strip()
    if override:
        if override.endswith("/chat/completions"):
            return override
        return override.rstrip("/") + ("/chat/completions" if override.rstrip("/").endswith("/v1") else "/v1/chat/completions")
    return str(route["endpoint"])


def token_count(usage: dict[str, Any], fallback: int) -> int:
    for key in ("total_tokens", "total_token_count"):
        if isinstance(usage.get(key), (int, float)):
            return int(usage[key])
    for left, right in (("prompt_tokens", "completion_tokens"), ("input_tokens", "output_tokens")):
        if isinstance(usage.get(left), (int, float)) and isinstance(usage.get(right), (int, float)):
            return int(usage[left]) + int(usage[right])
    return fallback


def assistant_text(message: dict[str, Any]) -> tuple[str, str]:
    """Normalize OpenAI-compatible string and typed-block message content."""
    content = message.get("content")
    if isinstance(content, str) and content:
        return content, "content_string"
    if isinstance(content, list):
        fragments: list[str] = []
        for block in content:
            if isinstance(block, str):
                fragments.append(block)
            elif isinstance(block, dict):
                for key in ("text", "content"):
                    value = block.get(key)
                    if isinstance(value, str):
                        fragments.append(value)
                        break
        if fragments:
            return "".join(fragments), "content_blocks"
    for key in ("output_text", "reasoning_content"):
        value = message.get(key)
        if isinstance(value, str) and value:
            return value, key
    return "", "empty_content_string" if isinstance(content, str) else "empty"


def call_model(
    route: dict[str, Any], system: str, user: str, max_tokens: int, retries: int
) -> tuple[str, dict[str, Any]]:
    api_key = os.environ.get(route["api_key_env"], "").strip()
    if not api_key:
        raise ValueError(f"missing API key: {route['api_key_env']}")
    body = {
        "model": route["model_id"],
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    request = urllib.request.Request(
        endpoint_for(route),
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                payload = json.loads(response.read().decode("utf-8"))
            choice = payload["choices"][0]
            content, content_source = assistant_text(choice["message"])
            usage = dict(payload.get("usage") or {})
            usage["response_metadata"] = {
                "content_source": content_source,
                "finish_reason": choice.get("finish_reason"),
            }
            return content, usage
        except (OSError, KeyError, IndexError, json.JSONDecodeError, urllib.error.HTTPError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(min(60, 10 * (2**attempt)))
    raise RuntimeError(f"provider call failed: {last_error}")


def parse_response(text: str) -> dict[str, str]:
    value = text.strip()
    outer_fence = re.fullmatch(r"```(?:text|plaintext|python)?\s*(.*?)\s*```", value, flags=re.S | re.I)
    if outer_fence:
        value = outer_fence.group(1).strip()
    envelope = re.fullmatch(
        r"\s*===SUMMARY_BEGIN===\s*(.*?)\s*===SUMMARY_END===\s*"
        r"===PYTHON_SOURCE_BEGIN===\s*\n?(.*?)\s*\n?===PYTHON_SOURCE_END===\s*",
        value,
        flags=re.S,
    )
    if envelope:
        summary, python_source = envelope.groups()
        if not python_source.strip():
            raise ValueError("python_source is empty")
        return {"summary": summary.strip(), "python_source": python_source}
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", value, flags=re.S | re.I)
    if fenced:
        value = fenced.group(1)
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        start, end = value.find("{"), value.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("model response contains no JSON object")
        payload = json.loads(value[start : end + 1])
    if set(payload) != {"summary", "python_source"}:
        raise ValueError("response must contain exactly summary and python_source")
    if not isinstance(payload["summary"], str) or not isinstance(payload["python_source"], str):
        raise ValueError("response fields must be strings")
    if not payload["python_source"].strip():
        raise ValueError("python_source is empty")
    return payload


def development_budget_stop_reason(
    max_output_tokens: int, minimum_output_tokens: int, call_count: int, call_limit: int
) -> str | None:
    if max_output_tokens < minimum_output_tokens:
        return "token_budget_exhausted"
    if call_count >= call_limit:
        return "api_call_budget_exhausted"
    return None


def public_contract(runtime_root: Path, exact_contract_path: Path | None = None) -> str:
    paths = [
        runtime_root / "r2g-skills/def-graph/scripts/r2g2/upstream_docs/B_VIEW_DATASET_STRUCTURE.md",
        runtime_root / "r2g-skills/def-graph/references/four-stage-dataset.md",
    ]
    sections = []
    for path in paths:
        sections.append(f"\n===== {path.name} =====\n{path.read_text(encoding='utf-8')}")
    if exact_contract_path is not None:
        sections.append(
            "\n===== PUBLIC_CONTRACT_V2.json =====\n"
            + exact_contract_path.read_text(encoding="utf-8")
        )
    return "".join(sections)


def anonymous_development_summary(cohort: dict[str, Any]) -> list[dict[str, Any]]:
    rows = sorted(cohort["splits"]["development"], key=lambda row: row["task_id"])
    return [
        {"case": f"dev-{index:02d}", "size_band": row["size_band"], "mapped_cells": row["mapped_cells"], "platform": row["platform"]}
        for index, row in enumerate(rows, 1)
    ]


def _bounded_diagnostics(case_root: Path, max_signatures: int, max_chars: int) -> list[dict[str, str]]:
    diagnostics: list[dict[str, str]] = []

    def add(source: str, category: str, detail: str) -> None:
        normalized = " ".join(detail.split())[:max_chars]
        item = {"source": source, "category": category, "detail": normalized}
        if normalized and item not in diagnostics and len(diagnostics) < max_signatures:
            diagnostics.append(item)

    for log_name in ("converter.log", "independent_validate.log", "structural_summary.log"):
        path = case_root / "logs" / log_name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")[-65536:]
        source = log_name.removesuffix(".log")
        for match in re.finditer(r"FileNotFoundError:.*?['\"]([^'\"]+)['\"]", text):
            missing = match.group(1)
            relative = missing.split("/generated/", 1)[-1] if "/generated/" in missing else Path(missing).name
            add(source, "MISSING_OUTPUT", relative)
        for match in re.finditer(r"AttributeError:.*?no attribute ['\"]([^'\"]+)['\"]", text):
            add(source, "MISSING_ATTRIBUTE", match.group(1))
        for exception, detail in re.findall(
            r"(?m)^(KeyError|ValueError|TypeError|RuntimeError|ModuleNotFoundError|ImportError):\s*(.+)$", text
        ):
            detail = re.sub(r"/(?:[^\s:'\"]+/)+[^\s:'\"]+", "<absolute-path>", detail)
            add(source, exception.upper(), detail)
    return diagnostics


def feedback_for(
    campaign_root: Path,
    method: str,
    cohort: dict[str, Any],
    max_signatures: int = 8,
    max_chars: int = 240,
) -> dict[str, Any]:
    rows = sorted(cohort["splits"]["development"], key=lambda row: row["task_id"])
    cases = []
    for index, row in enumerate(rows, 1):
        score_path = campaign_root / "methods" / method / row["task_id"] / "score.json"
        case_root = score_path.parent
        score = read_json(score_path) if score_path.is_file() else {}
        lint_path = case_root / "contract_lint.json"
        lint = read_json(lint_path) if lint_path.is_file() else {}
        run_state_path = case_root / "run_state.json"
        run_state = read_json(run_state_path) if run_state_path.is_file() else {}
        cases.append(
            {
                "case": f"dev-{index:02d}",
                "strict_pass": score.get("strict_pass", False),
                "validation_status": score.get("validation_status", "MISSING"),
                "statistics_status": score.get("statistics_status", "MISSING"),
                "contract_checks_passed": score.get("contract_checks_passed", 0),
                "contract_checks_total": score.get("contract_checks_total", 0),
                "static_contract_checks_passed": lint.get("checks_passed", 0),
                "static_contract_checks_total": lint.get("checks_total", 0),
                "static_contract_issue_count": lint.get("issue_count", 0),
                "static_contract_issues": (lint.get("issues") or [])[:max_signatures],
                "structural_issue_count": score.get("structural_issue_count", 0),
                "conversion_status": run_state.get("status", "MISSING"),
                "conversion_wall_seconds": score.get("conversion_wall_seconds"),
                "output_bytes": score.get("output_bytes", 0),
                "error_signatures": _bounded_diagnostics(case_root, max_signatures, max_chars),
            }
        )
    aggregate = {
        "strict_passes": sum(case["strict_pass"] is True for case in cases),
        "total_cases": len(cases),
        "validation_passes": sum(case["validation_status"] == "PASS" for case in cases),
        "statistics_passes": sum(case["statistics_status"] == "PASS" for case in cases),
        "contract_checks_passed": sum(int(case["contract_checks_passed"] or 0) for case in cases),
        "contract_checks_total": sum(int(case["contract_checks_total"] or 0) for case in cases),
        "static_contract_checks_passed": sum(int(case["static_contract_checks_passed"] or 0) for case in cases),
        "static_contract_checks_total": sum(int(case["static_contract_checks_total"] or 0) for case in cases),
        "structural_issue_count": sum(int(case["structural_issue_count"] or 0) for case in cases),
    }
    aggregate["all_strict_pass"] = aggregate["strict_passes"] == aggregate["total_cases"]
    return {"method": method, "aggregate": aggregate, "cases": cases}


def feedback_rank(feedback: dict[str, Any], round_index: int) -> tuple[int, int, int, int, int, int, int]:
    aggregate = feedback.get("aggregate") or {}
    return (
        int(aggregate.get("strict_passes", 0)),
        int(aggregate.get("static_contract_checks_passed", 0)),
        int(aggregate.get("contract_checks_passed", 0)),
        int(aggregate.get("statistics_passes", 0)),
        int(aggregate.get("validation_passes", 0)),
        -int(aggregate.get("structural_issue_count", 0)),
        -round_index,
    )


def run_referee(
    runner: Path,
    cohort: Path,
    campaign_root: Path,
    runtime_root: Path,
    python: Path,
    method: str,
    converter: Path,
) -> None:
    common = [
        str(python), str(runner), "--cohort", str(cohort), "--campaign-root", str(campaign_root),
        "--runtime-root", str(runtime_root), "--python", str(python),
    ]
    commands = [
        ("run", common + ["run", "--split", "development", "--method", method, "--converter", str(converter), "--workers", "1"]),
        ("evaluate", common + ["evaluate", "--split", "development", "--method", method, "--workers", "1"]),
    ]
    for phase, command in commands:
        result = subprocess.run(command, check=False)
        if result.returncode != 0 and phase == "run":
            # A broken converter is still evaluated and returned as feedback.
            continue
        if result.returncode != 0:
            raise RuntimeError(f"referee command failed: {command}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-key", choices=("gpt", "claude", "qwen"), required=True)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--routes", type=Path, required=True)
    parser.add_argument("--limits", type=Path, required=True)
    parser.add_argument("--system-prompt", type=Path, required=True)
    parser.add_argument("--public-contract", type=Path)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--retries", type=int, default=3)
    args = parser.parse_args()

    load_env_file(args.env_file)
    cohort = read_json(args.cohort)
    limits = read_json(args.limits)["converter_development"]
    route = route_by_key(args.routes, args.model_key)
    root = args.campaign_root.resolve() / "converter_development" / args.model_key
    root.mkdir(parents=True, exist_ok=True)
    ledger_path = root / "token_ledger.json"
    ledger = read_json(ledger_path) if ledger_path.is_file() else {
        "schema_version": "experiment4-token-ledger-1.0",
        "model_key": args.model_key,
        "budget": limits["total_tokens_per_model"],
        "consumed": 0,
        "calls": [],
    }
    system = args.system_prompt.read_text(encoding="utf-8")
    contract = public_contract(args.runtime_root, args.public_contract)
    development = anonymous_development_summary(cohort)
    previous_source = ""
    previous_feedback: dict[str, Any] | None = None
    successful_rounds: list[dict[str, Any]] = []
    stop_reason = "round_limit"

    for round_index in range(int(limits["rounds_per_model"])):
        round_root = root / f"round_{round_index}"
        response_path = round_root / "response.json"
        converter_path = round_root / "converter.py"
        failure_path = round_root / "call_failure.json"
        raw_path = round_root / "raw_response.txt"
        request_path = round_root / "request_receipt.json"
        provider_receipt_path = round_root / "provider_response_receipt.json"
        round_result_path = round_root / "round_result.json"
        if round_result_path.is_file() and converter_path.is_file():
            round_result = read_json(round_result_path)
            if round_result.get("converter_sha256") != sha256_file(converter_path):
                raise SystemExit(f"round {round_index} checkpoint converter digest mismatch")
            successful_rounds.append(round_result)
            previous_feedback = round_result["feedback"]
            previous_source = converter_path.read_text(encoding="utf-8")
            if previous_feedback.get("aggregate", {}).get("strict_passes") == int(
                limits.get("early_stop_strict_passes", len(development))
            ):
                stop_reason = "development_all_strict_pass"
                break
            continue
        if response_path.is_file() and converter_path.is_file():
            response = read_json(response_path)
        elif raw_path.is_file():
            if not any(int(call.get("round", -1)) == round_index for call in ledger["calls"]):
                if not provider_receipt_path.is_file():
                    raise SystemExit(f"round {round_index} has raw response without provider usage receipt")
                receipt = read_json(provider_receipt_path)
                ledger["consumed"] = int(ledger["consumed"]) + int(receipt["charged_tokens"])
                ledger["calls"].append(receipt["call_record"])
                write_json(ledger_path, ledger)
            try:
                response = parse_response(raw_path.read_text(encoding="utf-8"))
            except ValueError as exc:
                write_json(
                    failure_path,
                    {
                        "schema_version": "experiment4-consumed-call-failure-2.0.1",
                        "created_at": utc_now(),
                        "round": round_index,
                        "status": "malformed_response",
                        "error": str(exc),
                        "raw_response": str(raw_path),
                        "raw_response_sha256": sha256_file(raw_path),
                    },
                )
                previous_feedback = {
                    "response_parse_error": True,
                    "round": round_index,
                    "call_consumed": True,
                    "error": str(exc),
                    "instruction": "Return the complete delimited response envelope and full replacement source.",
                }
                previous_source = ""
                continue
            write_json(response_path, response)
            converter_path.write_text(response["python_source"], encoding="utf-8")
            if failure_path.is_file():
                failure = read_json(failure_path)
                failure["resolved_at"] = utc_now()
                failure["resolution"] = "reparsed_by_protocol_2.0.1_outer_fence_compatibility"
                write_json(failure_path, failure)
                for call in ledger["calls"]:
                    if int(call.get("round", -1)) == round_index:
                        call["original_status"] = call.get("status")
                        call["status"] = "completed_after_parser_compatibility"
                        call.pop("error", None)
                        call["response"] = str(response_path)
                        break
                write_json(ledger_path, ledger)
        elif failure_path.is_file():
            failure = read_json(failure_path)
            if failure.get("status") == "ambiguous_interrupted_request":
                previous_feedback = {
                    "provider_interruption": True,
                    "round": round_index,
                    "call_consumed": True,
                    "error": failure.get("error"),
                    "instruction": "Continue from the last valid converter source; do not repeat the interrupted attempt.",
                }
            else:
                previous_feedback = {
                    "response_parse_error": True,
                    "round": round_index,
                    "call_consumed": True,
                    "error": failure.get("error"),
                    "instruction": "Return the complete delimited response envelope and full replacement source.",
                }
                previous_source = ""
            continue
        else:
            if request_path.is_file():
                raise SystemExit(
                    f"round {round_index} has an ambiguous started request without a response; "
                    "manual provider reconciliation is required before retry"
                )
            user_payload = {
                "round": round_index,
                "development_cases": development,
                "public_contract": contract,
                "previous_source": previous_source or None,
                "previous_feedback": previous_feedback,
            }
            user = json.dumps(user_payload, ensure_ascii=False)
            prompt_reserve = (len(system.encode()) + len(user.encode()) + 2) // 3
            remaining_after_prompt = int(ledger["budget"]) - int(ledger["consumed"]) - prompt_reserve - 1024
            max_output_tokens = min(int(limits["max_output_tokens_per_call"]), remaining_after_prompt)
            budget_stop = development_budget_stop_reason(
                max_output_tokens,
                int(limits.get("minimum_output_tokens_per_call", 4096)),
                len(ledger["calls"]),
                int(limits["api_calls_per_model"]),
            )
            if budget_stop:
                stop_reason = budget_stop
                break
            round_root.mkdir(parents=True, exist_ok=True)
            write_json(
                request_path,
                {
                    "schema_version": "experiment4-request-receipt-1.1",
                    "created_at": utc_now(),
                    "round": round_index,
                    "model_key": args.model_key,
                    "model_id": route["model_id"],
                    "prompt_sha256": hashlib.sha256(user.encode("utf-8")).hexdigest(),
                    "estimated_prompt_tokens": prompt_reserve,
                    "max_output_tokens": max_output_tokens,
                },
            )
            raw, usage = call_model(route, system, user, max_output_tokens, args.retries)
            raw_path.write_text(raw, encoding="utf-8")
            charged = token_count(usage, prompt_reserve + max_output_tokens)
            ledger["consumed"] = int(ledger["consumed"]) + charged
            call_record = {
                "round": round_index,
                "at": utc_now(),
                "usage": usage,
                "charged_tokens": charged,
                "raw_response": str(raw_path),
                "raw_response_sha256": sha256_file(raw_path),
                "status": "provider_response_received",
            }
            write_json(
                provider_receipt_path,
                {
                    "schema_version": "experiment4-provider-response-receipt-1.1",
                    "created_at": utc_now(),
                    "charged_tokens": charged,
                    "call_record": call_record,
                },
            )
            ledger["calls"].append(call_record)
            write_json(ledger_path, ledger)
            try:
                response = parse_response(raw)
            except ValueError as exc:
                call_record["status"] = "malformed_response"
                call_record["error"] = str(exc)
                write_json(ledger_path, ledger)
                write_json(
                    failure_path,
                    {
                        "schema_version": "experiment4-consumed-call-failure-1.1",
                        "created_at": utc_now(),
                        "round": round_index,
                        "status": "malformed_response",
                        "error": str(exc),
                        "charged_tokens": charged,
                        "raw_response": str(raw_path),
                        "raw_response_sha256": sha256_file(raw_path),
                    },
                )
                previous_feedback = {
                    "response_parse_error": True,
                    "round": round_index,
                    "call_consumed": True,
                    "error": str(exc),
                    "instruction": "Return the complete delimited response envelope and full replacement source.",
                }
                previous_source = ""
                continue
            write_json(response_path, response)
            converter_path.write_text(response["python_source"], encoding="utf-8")
            call_record["status"] = "completed"
            call_record["response"] = str(response_path)
            write_json(ledger_path, ledger)

        source_bytes = converter_path.stat().st_size
        if source_bytes > int(limits["max_converter_source_bytes"]):
            previous_feedback = {
                "source_too_large": True,
                "source_bytes": source_bytes,
                "max_source_bytes": int(limits["max_converter_source_bytes"]),
            }
            previous_source = converter_path.read_text(encoding="utf-8")
            continue
        compile_result = subprocess.run(
            [str(args.python), "-m", "py_compile", str(converter_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        if compile_result.returncode != 0:
            previous_feedback = {"compile_error": True, "detail": (compile_result.stdout or "")[-2000:]}
        else:
            method = f"llm-{args.model_key}-round-{round_index}"
            run_referee(args.runner, args.cohort, args.campaign_root, args.runtime_root, args.python, method, converter_path)
            previous_feedback = feedback_for(
                args.campaign_root,
                method,
                cohort,
                int(limits.get("feedback_max_signatures_per_case", 8)),
                int(limits.get("feedback_max_signature_chars", 240)),
            )
            round_result = {
                "round": round_index,
                "converter": str(converter_path.resolve()),
                "converter_sha256": sha256_file(converter_path),
                "feedback": previous_feedback,
                "rank": list(feedback_rank(previous_feedback, round_index)),
            }
            write_json(round_root / "round_result.json", round_result)
            successful_rounds.append(round_result)
        previous_source = converter_path.read_text(encoding="utf-8")
        if previous_feedback.get("aggregate", {}).get("strict_passes") == int(
            limits.get("early_stop_strict_passes", len(development))
        ):
            stop_reason = "development_all_strict_pass"
            break

    if not successful_rounds:
        raise SystemExit("no syntactically valid converter round is available to freeze")
    selected = max(successful_rounds, key=lambda row: tuple(row["rank"]))
    frozen = root / "frozen_converter.py"
    selected_source = Path(selected["converter"])
    if frozen.exists() and sha256_file(frozen) != sha256_file(selected_source):
        raise SystemExit("existing frozen converter differs from selected development round")
    shutil.copy2(selected_source, frozen)
    manifest = {
        "schema_version": "experiment4-frozen-converter-1.1",
        "frozen_at": utc_now(),
        "model_key": args.model_key,
        "model_id": route["model_id"],
        "converter": str(frozen.resolve()),
        "converter_sha256": sha256_file(frozen),
        "configured_max_development_rounds": limits["rounds_per_model"],
        "development_rounds_completed": len(ledger["calls"]),
        "selected_round": selected["round"],
        "selected_round_rank": selected["rank"],
        "selected_round_aggregate": selected["feedback"]["aggregate"],
        "stop_reason": stop_reason,
        "consumed_tokens": ledger["consumed"],
        "hidden_test_exposed": False,
    }
    write_json(root / "frozen_converter_manifest.json", manifest)
    print(f"[experiment4] frozen {args.model_key}: {manifest['converter_sha256']}")


if __name__ == "__main__":
    main()
