#!/usr/bin/env python3
"""Call frozen Experiment 3 proposal models and build bounded round plans."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import time
from typing import Any
import urllib.error
import urllib.request


REPO = Path(__file__).resolve().parents[1]
BANK_PATH = REPO / "experiments/experiment3_candidate_bank.py"
SPEC = importlib.util.spec_from_file_location("experiment3_candidate_bank", BANK_PATH)
BANK = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(BANK)


class BudgetExhausted(RuntimeError):
    """The frozen token budget cannot safely admit another model call."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def usage_token_count(usage: dict[str, Any]) -> int | None:
    """Normalize token usage returned by OpenAI-compatible providers."""
    for key in ("total_tokens", "total_token_count"):
        value = usage.get(key)
        if isinstance(value, (int, float)) and value >= 0:
            return int(value)
    for input_key, output_key in (
        ("prompt_tokens", "completion_tokens"),
        ("input_tokens", "output_tokens"),
        ("input_token_count", "output_token_count"),
    ):
        input_tokens = usage.get(input_key)
        output_tokens = usage.get(output_key)
        if isinstance(input_tokens, (int, float)) and isinstance(output_tokens, (int, float)):
            return int(input_tokens) + int(output_tokens)
    return None


def conservative_request_tokens(system_prompt: str, user_prompt: str, max_output_tokens: int) -> int:
    """Estimate a conservative request budget without treating bytes as tokens.

    The experiment prompts are English JSON. Empirically, the strictest provider
    in the formal canary used about one token per 2.4 UTF-8 bytes. Reserving one
    token per two bytes keeps a safety margin while avoiding the previous 2-4x
    over-reservation that prevented later frozen rounds from being called.
    """
    prompt_bytes = len(system_prompt.encode("utf-8")) + len(user_prompt.encode("utf-8"))
    estimated_prompt_tokens = (prompt_bytes + 1) // 2
    return estimated_prompt_tokens + max_output_tokens


@contextmanager
def locked_ledger(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _load_ledger(path: Path, total_budget: int) -> dict[str, Any]:
    if path.is_file():
        ledger = read_json(path)
        if ledger.get("total_token_budget") != total_budget:
            raise ValueError(
                f"budget ledger is bound to {ledger.get('total_token_budget')} tokens, not {total_budget}"
            )
        return ledger
    return {
        "schema_version": "experiment3-token-budget-ledger-1.0",
        "created_at": now(),
        "updated_at": now(),
        "total_token_budget": total_budget,
        "consumed_tokens": 0,
        "reservations": {},
        "calls": {},
        "failures": [],
    }


def reserve_call(path: Path, call_id: str, total_budget: int, reserve_tokens: int) -> str:
    with locked_ledger(path):
        ledger = _load_ledger(path, total_budget)
        completed = ledger["calls"].get(call_id)
        if completed and completed.get("status") == "completed":
            return "completed"
        reservations = ledger["reservations"]
        if call_id in reservations:
            previous = reservations[call_id]
            same_host = previous.get("hostname") == socket.gethostname()
            pid_alive = same_host and Path(f"/proc/{previous.get('pid')}").exists()
            if pid_alive:
                raise RuntimeError(f"call already has an active budget reservation: {call_id}")
            ledger["failures"].append(
                {
                    "call_id": call_id,
                    "failed_at": now(),
                    "error_type": "orphaned_reservation_reclaimed",
                    "reserved_tokens_released": int(previous["reserved_tokens"]),
                }
            )
            del reservations[call_id]
        committed = int(ledger.get("consumed_tokens") or 0)
        reserved = sum(int(item["reserved_tokens"]) for item in reservations.values())
        if committed + reserved + reserve_tokens > total_budget:
            raise BudgetExhausted(
                "token budget would be exceeded: "
                f"committed={committed}, active_reserved={reserved}, "
                f"requested_reserve={reserve_tokens}, budget={total_budget}"
            )
        reservations[call_id] = {
            "reserved_at": now(),
            "reserved_tokens": reserve_tokens,
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
        }
        ledger["updated_at"] = now()
        write_json(path, ledger)
    return "reserved"


def complete_call(
    path: Path,
    call_id: str,
    total_budget: int,
    usage: dict[str, Any],
    *,
    output: Path,
    recovered_from_output: bool = False,
) -> dict[str, Any]:
    with locked_ledger(path):
        ledger = _load_ledger(path, total_budget)
        existing = ledger["calls"].get(call_id)
        if existing and existing.get("status") == "completed":
            return existing
        reservation = ledger["reservations"].pop(call_id, None)
        measured = usage_token_count(usage)
        if measured is None:
            if reservation is None:
                raise ValueError(f"cannot recover unmetered call without a reservation: {call_id}")
            charged = int(reservation["reserved_tokens"])
            accounting = "conservative_reservation"
        else:
            charged = measured
            accounting = "provider_usage"
        record = {
            "status": "completed",
            "completed_at": now(),
            "charged_tokens": charged,
            "accounting": accounting,
            "usage": usage,
            "output": str(output.resolve()),
            "recovered_from_output": recovered_from_output,
        }
        ledger["calls"][call_id] = record
        ledger["consumed_tokens"] = int(ledger.get("consumed_tokens") or 0) + charged
        ledger["budget_exceeded_after_provider_accounting"] = (
            ledger["consumed_tokens"] > total_budget
        )
        ledger["updated_at"] = now()
        write_json(path, ledger)
        return record


def fail_call(path: Path, call_id: str, total_budget: int, error: Exception) -> None:
    with locked_ledger(path):
        ledger = _load_ledger(path, total_budget)
        reservation = ledger["reservations"].pop(call_id, None)
        ledger["failures"].append(
            {
                "call_id": call_id,
                "failed_at": now(),
                "error_type": type(error).__name__,
                "reserved_tokens_released": int((reservation or {}).get("reserved_tokens") or 0),
            }
        )
        ledger["updated_at"] = now()
        write_json(path, ledger)


def extract_json(text: str) -> dict[str, Any]:
    value = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", value, flags=re.S | re.I)
    if fenced:
        value = fenced.group(1)
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        start = value.find("{")
        end = value.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("model response contains no JSON object")
        payload = json.loads(value[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("model response must be a JSON object")
    return payload


def route_by_key(routes_path: Path, model_key: str) -> dict[str, Any]:
    matches = [row for row in read_json(routes_path)["routes"] if row["model_key"] == model_key]
    if len(matches) != 1:
        raise ValueError(f"route not uniquely defined: {model_key}")
    return matches[0]


def endpoint_for(route: dict[str, Any]) -> str:
    override = os.environ.get(str(route.get("endpoint_env") or ""), "").strip()
    if not override:
        return str(route["endpoint"])
    if override.endswith(("/chat/completions", "/messages", "/responses")):
        return override
    base = override.rstrip("/")
    if route.get("api_style") == "anthropic_messages":
        return base + ("/messages" if base.endswith("/v1") else "/v1/messages")
    if route.get("api_style") == "openai_responses":
        return base + ("/responses" if base.endswith("/v1") else "/v1/responses")
    return base + ("/chat/completions" if base.endswith("/v1") else "/v1/chat/completions")


def call_model(
    route: dict[str, Any],
    system_prompt: str,
    user_prompt: str,
    *,
    max_output_tokens: int,
    retries: int,
) -> tuple[str, dict[str, Any]]:
    key = os.environ.get(route["api_key_env"])
    if not key:
        raise ValueError(f"missing credential environment variable: {route['api_key_env']}")
    endpoint = endpoint_for(route)
    body = {
        "model": route["model_id"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0,
        "max_tokens": max_output_tokens,
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                payload = json.loads(response.read().decode("utf-8"))
            content = payload["choices"][0]["message"]["content"]
            return str(content), payload.get("usage") or {}
        except (OSError, KeyError, IndexError, json.JSONDecodeError, urllib.error.HTTPError) as exc:
            last_error = exc
            if attempt == retries:
                break
            time.sleep(min(60, 10 * (2**attempt)))
    raise RuntimeError(f"model call failed after retries: {last_error}")


def normalize_proposals(
    payload: dict[str, Any],
    *,
    source_model: str,
    failure_domain: str,
    candidate_version: int,
    max_proposals: int = 2,
    policy_path: Path = BANK.DEFAULT_POLICY,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw = payload.get("proposals")
    if not isinstance(raw, list):
        raise ValueError("model JSON has no proposals array")
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for proposal in raw[:max_proposals]:
        if not isinstance(proposal, dict):
            rejected.append({"proposal": proposal, "errors": ["proposal is not an object"]})
            continue
        candidate = dict(proposal)
        applicability = candidate.get("applicability")
        if isinstance(applicability, dict):
            applicability = dict(applicability)
            if (
                "required_failure_signatures" not in applicability
                and isinstance(applicability.get("failure_patterns"), list)
            ):
                applicability["required_failure_signatures"] = applicability.pop(
                    "failure_patterns"
                )
            candidate["applicability"] = applicability
        edits = candidate.get("config_edits")
        if isinstance(edits, dict):
            candidate["config_edits"] = {
                str(key): str(value) for key, value in edits.items()
            }
        candidate["source_model"] = source_model
        candidate["failure_domain"] = failure_domain
        candidate["candidate_version"] = candidate_version
        candidate["action_policy_sha256"] = BANK.sha256_file(policy_path)
        try:
            accepted.append(BANK.freeze_candidate(candidate, policy_path))
        except (KeyError, TypeError, ValueError) as exc:
            rejected.append({"proposal": candidate, "errors": [str(exc)]})
    return accepted, rejected


def proposal_user_prompt(
    context: dict[str, Any],
    feedback: list[dict[str, Any]],
    round_index: int,
    failure_domain: str,
    phase: str = "a_propose",
    resource_limits: dict[str, Any] | None = None,
) -> str:
    if phase not in {"a_propose", "pure_llm"}:
        raise ValueError(f"unsupported proposal phase: {phase}")
    return json.dumps(
        {
            "phase": "A-propose" if phase == "a_propose" else "Pure LLM held-out repair",
            "round_index": round_index,
            "failure_domain": failure_domain,
            "baseline_evidence": context,
            "resource_limits": resource_limits or {"mode": "non_scoring_canary"},
            (
                "prior_A_propose_feedback"
                if phase == "a_propose"
                else "prior_same_model_same_task_feedback"
            ): feedback,
            "instruction": (
                "Propose at most two bounded candidates. Return JSON only."
                if phase == "a_propose"
                else "Propose at most one next bounded repair attempt. Return JSON only."
            ),
        },
        sort_keys=True,
    )


def model_visible_resource_limits(
    payload: dict[str, Any] | None,
    *,
    phase: str,
    round_index: int,
    max_output_tokens: int,
    require_frozen: bool,
) -> dict[str, Any]:
    if payload is None:
        if require_frozen:
            raise ValueError("formal model call requires --resource-limits")
        return {
            "mode": "non_scoring_canary",
            "current_call_max_output_tokens": max_output_tokens,
        }
    if require_frozen and payload.get("status") != "frozen":
        raise ValueError("formal model call requires status=frozen resource limits")
    if phase == "a_propose":
        limits = payload.get("a_propose") or {}
        expected_output = limits.get("max_output_tokens_per_call", max_output_tokens)
        if require_frozen and expected_output != max_output_tokens:
            raise ValueError("CLI max output does not match frozen A-propose resource limit")
        calls_per_domain = int(limits["api_calls_per_model_per_domain"])
        domain_count = len(payload.get("failure_domains") or ("drc_edge_pin", "setup_timing"))
        total_turns = int(
            limits.get("model_turns_per_model_total", calls_per_domain * domain_count)
        )
        if total_turns != calls_per_domain * domain_count:
            raise ValueError("A-propose turn limit must equal the total domain call budgets")
        return {
            "mode": "formal" if require_frozen else "prefreeze",
            "fixed_rounds": int(limits["rounds"]),
            "current_round_zero_based": round_index,
            "api_calls_per_model_per_domain": calls_per_domain,
            "api_calls_per_model_total": total_turns,
            "model_turns_per_model_total": total_turns,
            "total_token_budget_per_model_across_both_domains": int(
                limits.get("total_tokens_per_model")
                or limits.get("suggested_total_tokens_per_model")
            ),
            "raw_proposals_per_call": int(limits["raw_proposals_per_call"]),
            "current_call_max_output_tokens": int(expected_output),
        }
    limits = payload.get("pure_llm") or {}
    expected_output = limits.get("max_output_tokens_per_call", max_output_tokens)
    if require_frozen and expected_output != max_output_tokens:
        raise ValueError("CLI max output does not match frozen Pure-LLM resource limit")
    calls_per_task = int(limits["api_calls_per_task"])
    turns_per_task = int(limits.get("model_turns_per_model_per_task", calls_per_task))
    if turns_per_task != calls_per_task:
        raise ValueError("Pure-LLM turn limit must equal per-task call budget")
    return {
        "mode": "formal" if require_frozen else "prefreeze",
        "current_attempt_zero_based": round_index,
        "api_calls_per_model_per_task": calls_per_task,
        "model_turns_per_model_per_task": turns_per_task,
        "executed_attempts_per_model_per_task": int(limits["executed_attempts_per_task"]),
        "total_token_budget_per_model_per_task": int(
            limits.get("total_tokens_per_model_per_task")
            or limits.get("suggested_total_tokens_per_model_per_task")
        ),
        "raw_proposals_per_call": 1,
        "current_call_max_output_tokens": int(expected_output),
    }


def validate_model_context(context: dict[str, Any], phase: str, failure_domain: str) -> None:
    tasks = context.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("model context must contain at least one task")
    if any(task.get("failure_domain") != failure_domain for task in tasks):
        raise ValueError("model context contains a task from another failure domain")
    if context.get("repair_outcomes_included") is not False:
        raise ValueError("model context must explicitly exclude repair outcomes")
    if phase == "a_propose":
        if context.get("split") != "a_propose":
            raise ValueError("A-propose may only read the a_propose split")
    elif phase == "pure_llm":
        if context.get("split") != "b_heldout":
            raise ValueError("Pure LLM may only read the b_heldout split")
        if context.get("task_isolation") != "single_task_only" or len(tasks) != 1:
            raise ValueError("Pure LLM context must contain exactly one isolated held-out task")
    else:
        raise ValueError(f"unsupported proposal phase: {phase}")


def select_round_plan(
    proposals: list[dict[str, Any]],
    active_hashes: set[str],
    *,
    attempted_hashes: set[str] | None = None,
    execution_limit: int = 4,
    active_limit: int = 8,
) -> dict[str, Any]:
    attempted_hashes = attempted_hashes or set()
    unique: dict[str, dict[str, Any]] = {}
    sources: dict[str, set[str]] = {}
    for proposal in proposals:
        fingerprint = proposal["candidate_hash"]
        unique.setdefault(fingerprint, proposal)
        sources.setdefault(fingerprint, set()).add(proposal["source_model"])
    available_slots = max(0, active_limit - len(active_hashes))
    excluded = active_hashes | attempted_hashes
    new_hashes = sorted(fingerprint for fingerprint in unique if fingerprint not in excluded)
    by_model: dict[str, list[str]] = {}
    for fingerprint in new_hashes:
        for model in sorted(sources[fingerprint]):
            by_model.setdefault(model, []).append(fingerprint)
    selected: list[str] = []
    for model in sorted(by_model):
        candidate = next((item for item in by_model[model] if item not in selected), None)
        if candidate:
            selected.append(candidate)
    for fingerprint in new_hashes:
        if fingerprint not in selected:
            selected.append(fingerprint)
    selected = selected[: min(execution_limit, available_slots)]
    return {
        "schema_version": "experiment3-proposal-round-plan-1.0",
        "created_at": now(),
        "structural_unique_count": len(unique),
        "already_active_count": sum(item in active_hashes for item in unique),
        "already_attempted_count": sum(item in attempted_hashes for item in unique),
        "selected_count": len(selected),
        "selected": [
            {
                "candidate": unique[fingerprint],
                "proposal_sources": sorted(sources[fingerprint]),
            }
            for fingerprint in selected
        ],
    }


def command_propose(args: argparse.Namespace) -> None:
    route = route_by_key(args.routes, args.model)
    system_prompt = args.system_prompt.read_text(encoding="utf-8")
    context = read_json(args.context)
    feedback = read_json(args.feedback) if args.feedback else []
    validate_model_context(context, args.phase, args.failure_domain)
    if args.phase == "pure_llm" and args.max_proposals != 1:
        raise ValueError("Pure LLM permits exactly one proposal per attempt")
    resource_payload = read_json(args.resource_limits) if args.resource_limits else None
    visible_limits = model_visible_resource_limits(
        resource_payload,
        phase=args.phase,
        round_index=args.round,
        max_output_tokens=args.max_output_tokens,
        require_frozen=args.require_frozen_resources,
    )
    user_prompt = proposal_user_prompt(
        context,
        feedback,
        args.round,
        args.failure_domain,
        args.phase,
        visible_limits,
    )
    context_identity = args.context.stem if args.phase == "pure_llm" else args.failure_domain
    call_id = args.call_id or (
        f"{args.phase}:{args.model}:{context_identity}:round-{args.round}"
    )
    if bool(args.budget_ledger) != bool(args.total_token_budget):
        raise ValueError("--budget-ledger and --total-token-budget must be supplied together")
    if args.output.exists():
        if not args.resume:
            raise FileExistsError(f"refusing to overwrite proposal output: {args.output}")
        existing = read_json(args.output)
        identity = (
            existing.get("phase", "a_propose"),
            existing.get("model_key"),
            existing.get("failure_domain"),
            existing.get("round"),
        )
        expected = (args.phase, args.model, args.failure_domain, args.round)
        if identity != expected:
            raise ValueError(f"existing proposal output identity mismatch: {identity} != {expected}")
        if existing.get("status") == "budget_exhausted":
            return
        if args.budget_ledger:
            complete_call(
                args.budget_ledger,
                call_id,
                args.total_token_budget,
                existing.get("usage") or {},
                output=args.output,
                recovered_from_output=True,
            )
        return

    if args.budget_ledger:
        reserve_tokens = conservative_request_tokens(
            system_prompt, user_prompt, args.max_output_tokens
        )
        try:
            reservation_status = reserve_call(
                args.budget_ledger, call_id, args.total_token_budget, reserve_tokens
            )
        except BudgetExhausted as exc:
            write_json(
                args.output,
                {
                    "schema_version": "experiment3-model-proposals-1.0",
                    "created_at": now(),
                    "execution_hostname": socket.gethostname(),
                    "model_key": args.model,
                    "model_id": route["model_id"],
                    "resolved_endpoint": endpoint_for(route),
                    "phase": args.phase,
                    "failure_domain": args.failure_domain,
                    "round": args.round,
                    "call_id": call_id,
                    "status": "budget_exhausted",
                    "budget_reason": str(exc),
                    "usage": {},
                    "contract_valid": False,
                    "accepted": [],
                    "rejected": [],
                    "response_content_persisted": False,
                },
            )
            return
        if reservation_status == "completed":
            raise RuntimeError(f"budget ledger records completion but output is missing: {call_id}")
    try:
        content, usage = call_model(
            route,
            system_prompt,
            user_prompt,
            max_output_tokens=args.max_output_tokens,
            retries=args.retries,
        )
    except Exception as exc:
        if args.budget_ledger:
            fail_call(args.budget_ledger, call_id, args.total_token_budget, exc)
        raise
    artifact = {
        "schema_version": "experiment3-model-proposals-1.0",
        "created_at": now(),
        "execution_hostname": socket.gethostname(),
        "model_key": args.model,
        "model_id": route["model_id"],
        "resolved_endpoint": endpoint_for(route),
        "phase": args.phase,
        "failure_domain": args.failure_domain,
        "round": args.round,
        "call_id": call_id,
        "status": "completed",
        "usage": usage,
        "response_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "contract_valid": False,
        "accepted": [],
        "rejected": [],
        "response_content_persisted": False,
    }
    # Persist a receipt immediately after the provider returns. If the process dies
    # during validation, resume can account for the spent call without repeating it.
    write_json(args.output, artifact)
    try:
        payload = extract_json(content)
        accepted, rejected = normalize_proposals(
            payload,
            source_model=args.model,
            failure_domain=args.failure_domain,
            candidate_version=args.round + 1,
            max_proposals=args.max_proposals,
            policy_path=args.action_policy,
        )
        artifact["contract_valid"] = True
        artifact["accepted"] = accepted
        artifact["rejected"] = rejected
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        artifact["rejected"] = [
            {
                "proposal": None,
                "errors": [f"{type(exc).__name__}: {exc}"],
            }
        ]
    if args.budget_ledger:
        artifact["resource_accounting"] = complete_call(
            args.budget_ledger,
            call_id,
            args.total_token_budget,
            usage,
            output=args.output,
        )
    write_json(args.output, artifact)


def command_plan(args: argparse.Namespace) -> None:
    proposals: list[dict[str, Any]] = []
    for path in args.proposal_file:
        proposals.extend(read_json(path).get("accepted") or [])
    active = set(read_json(args.active_hashes).get("candidate_hashes") or []) if args.active_hashes else set()
    attempted = (
        set(read_json(args.attempted_hashes).get("candidate_hashes") or [])
        if args.attempted_hashes
        else set()
    )
    write_json(
        args.output,
        select_round_plan(
            proposals,
            active,
            attempted_hashes=attempted,
            execution_limit=args.execution_limit,
            active_limit=args.active_limit,
        ),
    )


def command_resolve_routes(args: argparse.Namespace) -> None:
    payload = read_json(args.routes)
    resolved = []
    missing_credentials = []
    for route in payload.get("routes") or []:
        credential_present = bool(os.environ.get(route["api_key_env"]))
        if not credential_present:
            missing_credentials.append(route["model_key"])
        resolved.append(
            {
                "model_key": route["model_key"],
                "display_model": route["display_model"],
                "model_id": route["model_id"],
                "provider": route["provider"],
                "api_style": route["api_style"],
                "resolved_endpoint": endpoint_for(route),
                "endpoint_env": route.get("endpoint_env"),
                "endpoint_override_present": bool(
                    os.environ.get(str(route.get("endpoint_env") or ""), "").strip()
                ),
                "api_key_env": route["api_key_env"],
                "credential_present": credential_present,
            }
        )
    result = {
        "schema_version": "experiment3-resolved-model-routes-1.0",
        "created_at": now(),
        "execution_hostname": socket.gethostname(),
        "source_routes": str(args.routes.resolve()),
        "source_routes_sha256": BANK.sha256_file(args.routes),
        "credentials_persisted": False,
        "resolved_routes": resolved,
    }
    write_json(args.output, result)
    if args.require_credentials and missing_credentials:
        raise ValueError(f"missing credentials for model routes: {missing_credentials}")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subparsers = result.add_subparsers(dest="command", required=True)
    propose = subparsers.add_parser("propose")
    propose.add_argument("--routes", type=Path, required=True)
    propose.add_argument("--system-prompt", type=Path, required=True)
    propose.add_argument("--model", choices=("gpt", "claude", "qwen"), required=True)
    propose.add_argument("--phase", choices=("a_propose", "pure_llm"), default="a_propose")
    propose.add_argument("--failure-domain", required=True)
    propose.add_argument("--action-policy", type=Path, default=BANK.DEFAULT_POLICY)
    propose.add_argument("--round", type=int, choices=(0, 1, 2), required=True)
    propose.add_argument("--context", type=Path, required=True)
    propose.add_argument("--feedback", type=Path)
    propose.add_argument("--max-output-tokens", type=int, default=2048)
    propose.add_argument("--max-proposals", type=int, choices=(1, 2), default=2)
    propose.add_argument("--retries", type=int, default=2)
    propose.add_argument("--budget-ledger", type=Path)
    propose.add_argument("--total-token-budget", type=int)
    propose.add_argument("--call-id")
    propose.add_argument("--resume", action="store_true")
    propose.add_argument("--resource-limits", type=Path)
    propose.add_argument("--require-frozen-resources", action="store_true")
    propose.add_argument("--output", type=Path, required=True)
    propose.set_defaults(func=command_propose)
    plan = subparsers.add_parser("build-round-plan")
    plan.add_argument("--proposal-file", type=Path, action="append", required=True)
    plan.add_argument("--active-hashes", type=Path)
    plan.add_argument("--attempted-hashes", type=Path)
    plan.add_argument("--execution-limit", type=int, default=4)
    plan.add_argument("--active-limit", type=int, default=8)
    plan.add_argument("--output", type=Path, required=True)
    plan.set_defaults(func=command_plan)
    resolved = subparsers.add_parser("resolve-routes")
    resolved.add_argument("--routes", type=Path, required=True)
    resolved.add_argument("--require-credentials", action="store_true")
    resolved.add_argument("--output", type=Path, required=True)
    resolved.set_defaults(func=command_resolve_routes)
    return result


def main() -> int:
    args = parser().parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
