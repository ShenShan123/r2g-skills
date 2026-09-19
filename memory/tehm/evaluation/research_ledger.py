"""Append-only attempt ledger and independent scoped audit for Research RC1."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from tehm.evaluation.research_campaign import verify_prepared_campaign


EVENT_SCHEMA = "tehm-research-attempt-event-v1"
AUDIT_SCHEMA = "tehm-research-attempt-audit-v1"
SUMMARY_SCHEMA = "tehm-research-campaign-summary-v1"
AUDIT_ARTIFACT_SCHEMA = "tehm-research-audit-artifacts-v1"
EVENT_TYPES = {"ATTEMPT_STARTED", "TOOL_RESULT", "CHECK_RECORDED", "ATTEMPT_TERMINAL"}
EXECUTION_STATUSES = {"COMPLETED", "TIMEOUT", "TOOL_ERROR", "CANCELLED"}
CHECK_VERDICTS = {"PASS", "FAIL", "UNKNOWN"}
ORACLE_VERDICTS = {"PASS", "FAIL", "UNKNOWN"}
GENESIS = "sha256:" + "0" * 64


class ResearchLedgerError(ValueError):
    """Raised when an attempt ledger or audit violates the frozen protocol."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResearchLedgerError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ResearchLedgerError(f"{label} must be a JSON object")
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
    temporary.write_bytes(_canonical(value) + b"\n")
    temporary.replace(path)


def _identifier(value: object, label: str) -> str:
    if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise ResearchLedgerError(f"{label} is invalid")
    return value


def _prepared(root: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    checked = verify_prepared_campaign(root)
    if not checked.get("valid"):
        raise ResearchLedgerError("prepared campaign is invalid")
    campaign = _load_json(root / "prepared-campaign.json", "prepared campaign")
    contexts = {}
    for task_id in campaign["task_ids"]:
        context = _load_json(root / "task-contexts" / f"{task_id}.json", "task context")
        contexts[task_id] = context
    return campaign, contexts


def _artifact_ref(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ResearchLedgerError(f"{label} must be an object")
    path = value.get("path")
    digest = value.get("sha256")
    size = value.get("bytes")
    if type(path) is not str or not path:
        raise ResearchLedgerError(f"{label}.path is invalid")
    if type(digest) is not str or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ResearchLedgerError(f"{label}.sha256 is invalid")
    if type(size) is not int or size < 0:
        raise ResearchLedgerError(f"{label}.bytes is invalid")
    return {"path": path, "sha256": digest, "bytes": size}


def artifact_reference(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ResearchLedgerError(f"raw artifact is missing: {source}")
    return {"path": str(source), "sha256": _sha256_file(source), "bytes": source.stat().st_size}


def _validate_payload(event_type: str, payload: object, context: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ResearchLedgerError("attempt event payload must be an object")
    value = json.loads(_canonical(dict(payload)))
    if event_type == "ATTEMPT_STARTED":
        retry_of = value.get("retry_of")
        if retry_of is not None:
            _identifier(retry_of, "retry_of")
        return value
    if event_type == "TOOL_RESULT":
        _identifier(value.get("tool_name"), "tool_name")
        if value.get("call_kind") not in {"EDA", "MODEL", "OTHER"}:
            raise ResearchLedgerError("tool result call_kind is invalid")
        for field in ("command_digest", "result_digest", "toolchain_digest"):
            if (type(value.get(field)) is not str
                    or not re.fullmatch(r"sha256:[0-9a-f]{64}", value[field])):
                raise ResearchLedgerError(f"tool result {field} is invalid")
        if type(value.get("exit_code")) is not int:
            raise ResearchLedgerError("tool result exit_code is invalid")
        wallclock = value.get("wallclock_seconds")
        if isinstance(wallclock, bool) or not isinstance(wallclock, (int, float)) or wallclock < 0:
            raise ResearchLedgerError("tool result wallclock_seconds is invalid")
        refs = value.get("raw_artifacts")
        if not isinstance(refs, list):
            raise ResearchLedgerError("tool result raw_artifacts must be a list")
        value["raw_artifacts"] = [
            _artifact_ref(item, f"raw_artifacts[{index}]") for index, item in enumerate(refs)
        ]
        return value
    if event_type == "CHECK_RECORDED":
        check = _identifier(value.get("check_name"), "check_name")
        contract = context["contract"]
        allowed = set(contract["required_checks"]) | set(contract["optional_checks"])
        if check not in allowed:
            raise ResearchLedgerError(f"check is outside frozen task contract: {check}")
        if value.get("verdict") not in CHECK_VERDICTS:
            raise ResearchLedgerError("check verdict is invalid")
        if not isinstance(value.get("scope"), Mapping) or not value["scope"]:
            raise ResearchLedgerError("check scope must be non-empty")
        refs = value.get("raw_report_refs")
        if not isinstance(refs, list):
            raise ResearchLedgerError("check raw_report_refs must be a list")
        if value["verdict"] in {"PASS", "FAIL"} and not refs:
            raise ResearchLedgerError("PASS/FAIL check requires raw report evidence")
        value["raw_report_refs"] = [
            _artifact_ref(item, f"raw_report_refs[{index}]") for index, item in enumerate(refs)
        ]
        return value
    if event_type == "ATTEMPT_TERMINAL":
        status = value.get("execution_status")
        if status not in EXECUTION_STATUSES:
            raise ResearchLedgerError("terminal execution_status is invalid")
        exception = value.get("terminal_exception")
        if status == "COMPLETED" and exception is not None:
            raise ResearchLedgerError("COMPLETED attempt cannot contain a terminal exception")
        if status != "COMPLETED":
            if not isinstance(exception, Mapping):
                raise ResearchLedgerError("non-completed attempt requires terminal_exception")
            for field in ("class", "detail"):
                if type(exception.get(field)) is not str or not exception[field]:
                    raise ResearchLedgerError(f"terminal exception {field} is invalid")
        cost = value.get("actual_cost")
        if not isinstance(cost, Mapping):
            raise ResearchLedgerError("terminal actual_cost must be an object")
        for field in ("eda_calls", "model_calls", "model_tokens"):
            if type(cost.get(field)) is not int or cost[field] < 0:
                raise ResearchLedgerError(f"actual_cost.{field} is invalid")
        wallclock = cost.get("wallclock_seconds")
        if isinstance(wallclock, bool) or not isinstance(wallclock, (int, float)) or wallclock < 0:
            raise ResearchLedgerError("actual_cost.wallclock_seconds is invalid")
        if "producer_oracle_verdict" in value:
            raise ResearchLedgerError("producer cannot write an oracle verdict")
        return value
    raise ResearchLedgerError(f"unknown attempt event type: {event_type}")


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ResearchLedgerError(f"cannot read attempt ledger: {path}") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            raise ResearchLedgerError(f"blank attempt ledger line: {line_number}")
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ResearchLedgerError(f"invalid attempt ledger JSON: {line_number}") from exc
        if not isinstance(event, dict):
            raise ResearchLedgerError(f"attempt ledger line is not an object: {line_number}")
        events.append(event)
    return events


def _replay(events: list[dict[str, Any]], campaign: Mapping[str, Any],
            contexts: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    previous = GENESIS
    attempts: dict[str, dict[str, Any]] = {}
    retried_attempts: set[str] = set()
    task_policy_attempts: Counter[tuple[str, str]] = Counter()
    cost = Counter({
        "eda_calls": 0,
        "model_calls": 0,
        "model_tokens": 0,
        "wallclock_seconds": 0.0,
    })
    for index, event in enumerate(events, start=1):
        if event.get("schema") != EVENT_SCHEMA or event.get("sequence") != index:
            raise ResearchLedgerError(f"attempt ledger sequence/schema mismatch: {index}")
        if event.get("previous_event_digest") != previous:
            raise ResearchLedgerError(f"attempt ledger hash chain mismatch: {index}")
        claimed = event.get("event_digest")
        unsigned = dict(event)
        unsigned.pop("event_digest", None)
        if claimed != _digest(unsigned):
            raise ResearchLedgerError(f"attempt ledger event digest mismatch: {index}")
        if event.get("campaign_id") != campaign["campaign_id"]:
            raise ResearchLedgerError("attempt event campaign mismatch")
        if event.get("prepared_campaign_digest") != campaign["prepared_campaign_digest"]:
            raise ResearchLedgerError("attempt event prepared input mismatch")
        event_type = event.get("event_type")
        if event_type not in EVENT_TYPES:
            raise ResearchLedgerError("attempt event type is invalid")
        task_id = _identifier(event.get("task_id"), "task_id")
        if task_id not in contexts:
            raise ResearchLedgerError(f"attempt event task is not registered: {task_id}")
        policy = event.get("policy")
        if policy not in campaign["policies"]:
            raise ResearchLedgerError(f"attempt event policy is not registered: {policy}")
        attempt_id = _identifier(event.get("attempt_id"), "attempt_id")
        payload = _validate_payload(event_type, event.get("payload"), contexts[task_id])
        state = attempts.get(attempt_id)
        if event_type == "ATTEMPT_STARTED":
            if state is not None:
                raise ResearchLedgerError(f"attempt started twice: {attempt_id}")
            prior_for_key = [
                item for item in attempts.values()
                if item["task_id"] == task_id and item["policy"] == policy
            ]
            retry_of = payload.get("retry_of")
            if prior_for_key and retry_of is None:
                raise ResearchLedgerError("additional attempt must declare retry_of")
            if retry_of is not None:
                prior = attempts.get(retry_of)
                if prior is None or prior["task_id"] != task_id or prior["policy"] != policy:
                    raise ResearchLedgerError("retry_of does not bind the same task/policy")
                if prior["terminal"] is None:
                    raise ResearchLedgerError("retry_of attempt is not terminal")
                if retry_of in retried_attempts:
                    raise ResearchLedgerError("attempt cannot have multiple retry children")
                exception = prior["terminal"].get("terminal_exception") or {}
                if exception.get("class") not in set(campaign["budget"]["retryable_classes"]):
                    raise ResearchLedgerError("retry_of failure class is not retryable")
                retried_attempts.add(retry_of)
            state = {
                "attempt_id": attempt_id, "task_id": task_id, "policy": policy,
                "start_sequence": index, "checks": {}, "tools": [], "terminal": None,
            }
            attempts[attempt_id] = state
            task_policy_attempts[(task_id, policy)] += 1
        else:
            if state is None:
                raise ResearchLedgerError(f"attempt event precedes start: {attempt_id}")
            if state["task_id"] != task_id or state["policy"] != policy:
                raise ResearchLedgerError(f"attempt identity drifted: {attempt_id}")
            if state["terminal"] is not None:
                raise ResearchLedgerError(f"attempt event follows terminal: {attempt_id}")
            if event_type == "TOOL_RESULT":
                state["tools"].append({"sequence": index, **payload})
            elif event_type == "CHECK_RECORDED":
                name = payload["check_name"]
                if name in state["checks"]:
                    raise ResearchLedgerError(f"attempt check recorded twice: {attempt_id}:{name}")
                state["checks"][name] = {"sequence": index, **payload}
            elif event_type == "ATTEMPT_TERMINAL":
                observed_eda = sum(item["call_kind"] == "EDA" for item in state["tools"])
                observed_model = sum(item["call_kind"] == "MODEL" for item in state["tools"])
                if payload["actual_cost"]["eda_calls"] != observed_eda:
                    raise ResearchLedgerError("terminal EDA call count disagrees with tool receipts")
                if payload["actual_cost"]["model_calls"] != observed_model:
                    raise ResearchLedgerError("terminal model call count disagrees with tool receipts")
                state["terminal"] = {"sequence": index, **payload}
                actual = payload["actual_cost"]
                cost["eda_calls"] += actual["eda_calls"]
                cost["model_calls"] += actual["model_calls"]
                cost["model_tokens"] += actual["model_tokens"]
                cost["wallclock_seconds"] += actual["wallclock_seconds"]
        previous = claimed

    retry_limit = campaign["budget"]["retry_limit"]
    if any(count > retry_limit + 1 for count in task_policy_attempts.values()):
        raise ResearchLedgerError("attempt retry budget exceeded")
    for field in ("eda_calls", "model_calls", "model_tokens", "wallclock_seconds"):
        ceiling_field = {
            "eda_calls": "eda_call_limit", "model_calls": "model_call_limit",
            "model_tokens": "model_token_limit", "wallclock_seconds": "wallclock_limit_seconds",
        }[field]
        if cost[field] > campaign["budget"][ceiling_field]:
            raise ResearchLedgerError(f"campaign actual cost exceeds {ceiling_field}")
    return {
        "attempts": attempts,
        "tail_digest": previous,
        "event_count": len(events),
        "actual_cost": dict(cost),
    }


def verify_attempt_ledger(*, prepared: str | Path, ledger: str | Path) -> dict[str, Any]:
    prepared_root = Path(prepared).expanduser().resolve()
    ledger_path = Path(ledger).expanduser().resolve()
    campaign, contexts = _prepared(prepared_root)
    replay = _replay(_read_events(ledger_path), campaign, contexts)
    running = sum(state["terminal"] is None for state in replay["attempts"].values())
    terminal = len(replay["attempts"]) - running
    return {
        "valid": True,
        "campaign_id": campaign["campaign_id"],
        "prepared_campaign_digest": campaign["prepared_campaign_digest"],
        "event_count": replay["event_count"],
        "attempt_count": len(replay["attempts"]),
        "terminal_attempt_count": terminal,
        "running_attempt_count": running,
        "tail_digest": replay["tail_digest"],
        "actual_cost": replay["actual_cost"],
    }


def append_attempt_event(
    *, prepared: str | Path, ledger: str | Path, event_type: str,
    task_id: str, policy: str, attempt_id: str, payload: Mapping[str, Any],
    expected_tail_digest: str | None = None, timestamp: str | None = None,
) -> dict[str, Any]:
    """Atomically append one event after replaying the complete prior chain."""
    prepared_root = Path(prepared).expanduser().resolve()
    ledger_path = Path(ledger).expanduser().resolve()
    campaign, contexts = _prepared(prepared_root)
    task_id = _identifier(task_id, "task_id")
    attempt_id = _identifier(attempt_id, "attempt_id")
    if task_id not in contexts:
        raise ResearchLedgerError(f"task is not registered: {task_id}")
    if policy not in campaign["policies"]:
        raise ResearchLedgerError(f"policy is not registered: {policy}")
    if event_type not in EVENT_TYPES:
        raise ResearchLedgerError("event_type is invalid")
    checked_payload = _validate_payload(event_type, payload, contexts[task_id])
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = ledger_path.with_name(ledger_path.name + ".lock")
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        events = _read_events(ledger_path)
        replay = _replay(events, campaign, contexts)
        if expected_tail_digest is not None and expected_tail_digest != replay["tail_digest"]:
            raise ResearchLedgerError("attempt ledger tail changed concurrently")
        event = {
            "schema": EVENT_SCHEMA,
            "sequence": len(events) + 1,
            "previous_event_digest": replay["tail_digest"],
            "campaign_id": campaign["campaign_id"],
            "prepared_campaign_digest": campaign["prepared_campaign_digest"],
            "event_type": event_type,
            "task_id": task_id,
            "policy": policy,
            "attempt_id": attempt_id,
            "timestamp": timestamp or datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            "payload": checked_payload,
        }
        event["event_digest"] = _digest(event)
        prospective = [*events, event]
        _replay(prospective, campaign, contexts)
        line = _canonical(event) + b"\n"
        descriptor = os.open(ledger_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(descriptor, line)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return event


def _verify_raw_ref(reference: Mapping[str, Any]) -> None:
    checked = _artifact_ref(reference, "raw evidence reference")
    path = Path(checked["path"]).expanduser().resolve()
    if (not path.is_file() or path.stat().st_size != checked["bytes"]
            or _sha256_file(path) != checked["sha256"]):
        raise ResearchLedgerError(f"raw evidence drifted or is missing: {path}")


def _frontend_source_verdict(state: Mapping[str, Any],
                             context: Mapping[str, Any]) -> str:
    check = state["checks"].get("source_integrity")
    if check is None:
        return "UNKNOWN"
    report = None
    for reference in check["raw_report_refs"]:
        path = Path(reference["path"]).expanduser().resolve()
        if path.suffix != ".json":
            continue
        try:
            candidate = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (isinstance(candidate, dict) and
                candidate.get("schema") == "tehm-research-source-integrity-report-v1"):
            report = candidate
            break
    if report is None:
        return "UNKNOWN"
    compile_input = context.get("compile_input") or {}
    root = Path(str(compile_input.get("source_root") or "")).expanduser().resolve()
    relative_project = PurePosixPath(str(compile_input.get("project_path") or ""))
    if (relative_project.is_absolute() or ".." in relative_project.parts or
            not relative_project.parts):
        return "UNKNOWN"
    project = root / Path(*relative_project.parts)
    entries = report.get("entries")
    if not isinstance(entries, list) or not entries:
        return "UNKNOWN"
    identities = []
    for item in entries:
        if not isinstance(item, Mapping):
            return "UNKNOWN"
        relative = PurePosixPath(str(item.get("path") or ""))
        expected_digest = item.get("expected_sha256")
        expected_bytes = item.get("expected_bytes")
        if (relative.is_absolute() or ".." in relative.parts or not relative.parts or
                type(expected_digest) is not str or
                not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_digest) or
                type(expected_bytes) is not int or expected_bytes < 0):
            return "UNKNOWN"
        identities.append({
            "path": relative.as_posix(), "bytes": expected_bytes,
            "sha256": expected_digest,
        })
        source = project / Path(*relative.parts)
        if (not source.is_file() or source.stat().st_size != expected_bytes or
                _sha256_file(source) != expected_digest):
            return "FAIL"
    expected_bundle = compile_input.get("source_bundle_digest")
    if type(expected_bundle) is not str or _digest(identities) != expected_bundle:
        return "UNKNOWN"
    if report.get("expected_source_bundle_digest") != expected_bundle:
        return "UNKNOWN"
    return "PASS"


def _frontend_netlist_has_top(state: Mapping[str, Any], filename: str,
                              top: object) -> bool:
    if type(top) is not str or not top:
        return False
    for tool in state["tools"]:
        if tool.get("tool_name") != "yosys":
            continue
        for reference in tool["raw_artifacts"]:
            path = Path(reference["path"]).expanduser().resolve()
            if path.name != filename:
                continue
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return False
            modules = value.get("modules") if isinstance(value, Mapping) else None
            return isinstance(modules, Mapping) and top in modules
    return False


def _recompute_frontend_checks(state: Mapping[str, Any],
                               context: Mapping[str, Any]) -> dict[str, str]:
    """Derive S0 checks from source bytes and raw Yosys JSON, not producer PASS."""
    source = _frontend_source_verdict(state, context)
    top = (context.get("compile_input") or {}).get("top_module")
    elaborated = _frontend_netlist_has_top(state, "elaborated.json", top)
    synthesized = _frontend_netlist_has_top(state, "synthesized.json", top)
    terminal = state.get("terminal")
    execution_status = terminal.get("execution_status") if terminal else "RUNNING"
    yosys_tools = [tool for tool in state["tools"] if tool.get("tool_name") == "yosys"]
    exit_code = yosys_tools[-1].get("exit_code") if yosys_tools else None
    if elaborated:
        elaboration = "PASS"
    elif execution_status == "COMPLETED" and type(exit_code) is int and exit_code != 0:
        elaboration = "FAIL"
    else:
        elaboration = "UNKNOWN"
    if synthesized:
        synthesis = "PASS"
    elif (elaboration == "PASS" and execution_status == "COMPLETED" and
          type(exit_code) is int and exit_code != 0):
        synthesis = "FAIL"
    else:
        synthesis = "UNKNOWN"
    return {
        "source_integrity": source,
        "elaboration": elaboration,
        "synthesis": synthesis,
    }


def _attempt_audit(state: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
    terminal = state["terminal"]
    checks = state["checks"]
    for tool in state["tools"]:
        for reference in tool["raw_artifacts"]:
            _verify_raw_ref(reference)
    for check in checks.values():
        for reference in check["raw_report_refs"]:
            _verify_raw_ref(reference)
    required = list(context["contract"]["required_checks"])
    producer_projection = {
        name: (checks[name]["verdict"] if name in checks else "UNKNOWN")
        for name in required
    }
    recomputed = None
    if context["contract"].get("task_kind") == "frontend_synthesis_preflight":
        recomputed = _recompute_frontend_checks(state, context)
    required_projection = {
        name: (recomputed.get(name, "UNKNOWN") if recomputed is not None
               else producer_projection[name])
        for name in required
    }
    if terminal is None:
        execution_status = "RUNNING"
        oracle = "UNKNOWN"
        reason = "attempt_not_terminal"
        terminal_exception = None
        actual_cost = None
    else:
        execution_status = terminal["execution_status"]
        terminal_exception = terminal["terminal_exception"]
        actual_cost = terminal["actual_cost"]
        if any(value == "FAIL" for value in required_projection.values()):
            oracle = "FAIL"
            reason = "required_check_failed"
        elif execution_status != "COMPLETED":
            oracle = "UNKNOWN"
            reason = f"execution_{execution_status.lower()}"
        elif all(value == "PASS" for value in required_projection.values()):
            oracle = "PASS"
            reason = "all_required_checks_passed"
        else:
            oracle = "UNKNOWN"
            reason = "required_check_missing_or_unknown"
    return {
        "attempt_id": state["attempt_id"],
        "task_id": state["task_id"],
        "policy": state["policy"],
        "execution_status": execution_status,
        "oracle_verdict": oracle,
        "oracle_reason": reason,
        "required_checks": required_projection,
        "producer_required_checks": producer_projection,
        "audit_recomputed_checks": recomputed is not None,
        "check_disagreements": sorted(
            name for name in required
            if producer_projection[name] != required_projection[name]
        ),
        "optional_checks": {
            name: checks[name]["verdict"] for name in context["contract"]["optional_checks"]
            if name in checks
        },
        "tool_call_count": len(state["tools"]),
        "terminal_exception": terminal_exception,
        "actual_cost": actual_cost,
        "utility_result": "UNDETERMINED",
        "repair_metric_eligible": bool(context["contract"]["eligible_for_repair_metric"]),
    }


def _audit_artifacts(root: Path) -> dict[str, Any]:
    manifest_path = root / "artifact-manifest.json"
    files = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_file() and path != manifest_path:
            files.append({"path": path.relative_to(root).as_posix(),
                          "sha256": _sha256_file(path), "bytes": path.stat().st_size})
    payload = {"schema": AUDIT_ARTIFACT_SCHEMA, "files": files,
               "files_digest": _digest(files)}
    payload["manifest_digest"] = _digest(payload)
    return payload


def audit_attempt_ledger(*, prepared: str | Path, ledger: str | Path,
                         output: str | Path) -> dict[str, Any]:
    """Independently recompute scoped verdicts from raw append-only evidence."""
    prepared_root = Path(prepared).expanduser().resolve()
    ledger_path = Path(ledger).expanduser().resolve()
    destination = Path(output).expanduser().resolve()
    if destination.exists():
        raise ResearchLedgerError(f"refusing to overwrite audit: {destination}")
    campaign, contexts = _prepared(prepared_root)
    events = _read_events(ledger_path)
    replay = _replay(events, campaign, contexts)
    expected = [(task_id, policy) for task_id in campaign["task_ids"]
                for policy in campaign["policies"]]
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    audited_attempts = []
    for state in sorted(replay["attempts"].values(), key=lambda item: item["start_sequence"]):
        audit = _attempt_audit(state, contexts[state["task_id"]])
        audited_attempts.append(audit)
        by_key[(state["task_id"], state["policy"])].append(audit)
    cases = []
    for task_id, policy in expected:
        attempts = by_key.get((task_id, policy), [])
        terminal = [item for item in attempts if item["execution_status"] != "RUNNING"]
        effective = terminal[-1] if terminal else None
        cases.append({
            "task_id": task_id,
            "policy": policy,
            "registered": True,
            "attempt_ids": [item["attempt_id"] for item in attempts],
            "terminal_attempt_count": len(terminal),
            "effective_attempt_id": effective["attempt_id"] if effective else None,
            "execution_status": effective["execution_status"] if effective else "NOT_EXECUTED",
            "oracle_verdict": effective["oracle_verdict"] if effective else "UNKNOWN",
            "missing_reason": None if effective else "no_terminal_attempt",
        })
    verdict_counts = Counter(item["oracle_verdict"] for item in cases)
    execution_counts = Counter(item["execution_status"] for item in cases)
    all_terminal = all(item["effective_attempt_id"] is not None for item in cases)
    audit_payload = {
        "schema": AUDIT_SCHEMA,
        "campaign_id": campaign["campaign_id"],
        "prepared_campaign_digest": campaign["prepared_campaign_digest"],
        "ledger_path": str(ledger_path),
        "ledger_sha256": _sha256_file(ledger_path) if ledger_path.is_file() else None,
        "ledger_tail_digest": replay["tail_digest"],
        "event_count": len(events),
        "attempts": audited_attempts,
        "cases": cases,
        "full_denominator": len(expected),
        "all_registered_terminal": all_terminal,
        "producer_success_fields_trusted": False,
    }
    audit_payload["audit_digest"] = _digest(audit_payload)
    summary = {
        "schema": SUMMARY_SCHEMA,
        "campaign_id": campaign["campaign_id"],
        "prepared_campaign_digest": campaign["prepared_campaign_digest"],
        "audit_digest": audit_payload["audit_digest"],
        "registered_case_count": len(expected),
        "terminal_case_count": sum(item["effective_attempt_id"] is not None for item in cases),
        "oracle_verdict_counts": dict(sorted(verdict_counts.items())),
        "execution_status_counts": dict(sorted(execution_counts.items())),
        "actual_cost": replay["actual_cost"],
        "all_registered_terminal": all_terminal,
        "claim_boundary": "Independent scoped audit; UNKNOWN and infrastructure failures remain in the denominator.",
    }
    summary["summary_digest"] = _digest(summary)
    staging = destination.with_name(destination.name + f".tmp.{os.getpid()}")
    if staging.exists():
        raise ResearchLedgerError(f"audit staging exists: {staging}")
    staging.mkdir(parents=True)
    try:
        _write_json(staging / "audited-attempts.json", audit_payload)
        _write_json(staging / "summary.json", summary)
        _write_json(staging / "artifact-manifest.json", _audit_artifacts(staging))
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging.replace(destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {
        "valid": True,
        "campaign_id": campaign["campaign_id"],
        "audit_digest": audit_payload["audit_digest"],
        "summary_digest": summary["summary_digest"],
        "registered_case_count": summary["registered_case_count"],
        "terminal_case_count": summary["terminal_case_count"],
        "all_registered_terminal": all_terminal,
        "oracle_verdict_counts": summary["oracle_verdict_counts"],
        "execution_status_counts": summary["execution_status_counts"],
    }


def verify_research_audit(path: str | Path) -> dict[str, Any]:
    root = Path(path).expanduser().resolve()
    audit = _load_json(root / "audited-attempts.json", "attempt audit")
    summary = _load_json(root / "summary.json", "campaign summary")
    artifacts = _load_json(root / "artifact-manifest.json", "audit artifact manifest")
    if audit.get("schema") != AUDIT_SCHEMA or summary.get("schema") != SUMMARY_SCHEMA:
        raise ResearchLedgerError("audit or summary schema mismatch")
    unsigned_audit = dict(audit)
    audit_digest = unsigned_audit.pop("audit_digest", None)
    if audit_digest != _digest(unsigned_audit):
        raise ResearchLedgerError("attempt audit digest mismatch")
    unsigned_summary = dict(summary)
    summary_digest = unsigned_summary.pop("summary_digest", None)
    if summary_digest != _digest(unsigned_summary):
        raise ResearchLedgerError("campaign summary digest mismatch")
    if summary.get("audit_digest") != audit_digest:
        raise ResearchLedgerError("campaign summary is not bound to audit")
    if artifacts.get("schema") != AUDIT_ARTIFACT_SCHEMA:
        raise ResearchLedgerError("audit artifact schema mismatch")
    unsigned_artifacts = dict(artifacts)
    artifact_digest = unsigned_artifacts.pop("manifest_digest", None)
    if artifact_digest != _digest(unsigned_artifacts):
        raise ResearchLedgerError("audit artifact manifest digest mismatch")
    files = artifacts.get("files")
    if not isinstance(files, list) or artifacts.get("files_digest") != _digest(files):
        raise ResearchLedgerError("audit artifact files digest mismatch")
    for entry in files:
        if not isinstance(entry, Mapping):
            raise ResearchLedgerError("audit artifact entry is invalid")
        relative = PurePosixPath(str(entry.get("path") or ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise ResearchLedgerError("audit artifact path is unsafe")
        target = root / Path(*relative.parts)
        if (not target.is_file() or target.stat().st_size != entry.get("bytes")
                or _sha256_file(target) != entry.get("sha256")):
            raise ResearchLedgerError(f"audit artifact drifted: {relative}")
    verdict_total = sum((summary.get("oracle_verdict_counts") or {}).values())
    execution_total = sum((summary.get("execution_status_counts") or {}).values())
    if verdict_total != summary.get("registered_case_count") or execution_total != verdict_total:
        raise ResearchLedgerError("campaign summary denominator is inconsistent")
    return {
        "valid": True,
        "campaign_id": summary["campaign_id"],
        "audit_digest": audit_digest,
        "summary_digest": summary_digest,
        "artifact_manifest_digest": artifact_digest,
        "registered_case_count": summary["registered_case_count"],
        "terminal_case_count": summary["terminal_case_count"],
        "all_registered_terminal": summary["all_registered_terminal"],
        "oracle_verdict_counts": summary["oracle_verdict_counts"],
        "execution_status_counts": summary["execution_status_counts"],
        "actual_cost": summary["actual_cost"],
    }


__all__ = [
    "AUDIT_SCHEMA", "EVENT_SCHEMA", "ResearchLedgerError", "append_attempt_event",
    "artifact_reference", "audit_attempt_ledger", "verify_attempt_ledger",
    "verify_research_audit",
]
