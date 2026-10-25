"""A bounded model view of the transcript and fresh, authoritative job data.

The transcript remains the archive used by the UI. This module never rewrites
it. Older turns become an explicitly incomplete extractive index, with exact
history available through a session-scoped tool. Runtime observations are
request-local data; they are never appended as synthetic conversation turns.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict

from .config import ModelConfig
from .context_state import ContextState
from .tool_outputs import redact


class ContextBudgetError(ValueError):
    pass


ACTIVE_JOBS = frozenset(("pending", "queued", "running", "finalizing", "cancelling",
                         "recovery_required", "waiting_input", "waiting_approval", "validating"))


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _digest(value) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _excerpt(text: str, limit: int) -> dict:
    clipped = len(text) > limit
    return {"text": text if not clipped else text[:limit // 2] + "\n…\n" + text[-limit // 2:],
            "truncated": clipped, "total_chars": len(text)}


def related_jobs(messages: list[dict]) -> list[dict]:
    """Only executed, explicit tool references bind a job to this session.

    Do not infer ownership from user/assistant prose or a global list_jobs
    response. Reading the archive also supports sessions predating this view.
    """
    calls, jobs = {}, {}
    for index, message in enumerate(messages):
        if message["role"] == "assistant":
            calls.update({c["id"]: c for c in message.get("tool_calls", [])})
        elif message["role"] == "tool":
            call = calls.get(message.get("tool_call_id"))
            if not call or call["name"] != message.get("name"):
                continue
            try:
                result = json.loads(message["content"])
            except (ValueError, TypeError):
                continue
            if not isinstance(result, dict) or result.get("error"):
                continue
            if call["name"] in ("start_job", "start_batch"):
                jid = result.get("job_id")
            else:
                jid = call.get("arguments", {}).get("job_id")
            if isinstance(jid, str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", jid):
                jobs.pop(jid, None)
                jobs[jid] = {"job_id": jid, "source_message": index,
                             "source_call_id": call["id"]}
    return list(jobs.values())


def _history_index(messages: list[dict], end: int, max_chars: int) -> dict | None:
    if not end:
        return None
    starts = [i for i in range(end) if messages[i]["role"] == "user"]
    summary = {"method": "extractive_index_v1", "creator": "runtime, no model",
               "source_message_range": [0, end], "source_sha256": _digest(messages[:end]),
               "incomplete": True, "total_turns": len(starts), "indexed_turns": []}
    # Anchor the original request, then fill with the most recent older turns.
    order = [0, *range(len(starts) - 1, 0, -1)] if starts else []
    for position in order:
        first = starts[position]
        last = starts[position + 1] if position + 1 < len(starts) else end
        kind = messages[first].get("turn_kind", "user")
        entry = {"source_message_range": [first, last], "source_turn_kind": kind,
                 "user_request" if kind == "user" else "runtime_request":
                     _excerpt(messages[first].get("content", ""), 1400 if position == 0 else 350),
                 "tools": [{"name": m.get("name"), "artifact_id": m.get("output_ref", {}).get("artifact_id")}
                           for m in messages[first:last] if m["role"] == "tool"][-4:]}
        if len(_json(summary)) + len(_json(entry)) + 100 > max_chars:
            continue
        summary["indexed_turns"].append(entry)
    summary["indexed_turns"].sort(key=lambda e: e["source_message_range"][0])
    summary["omitted_turns"] = len(starts) - len(summary["indexed_turns"])
    summary["read_with"] = "read_session_history(offset, limit, query)"
    return summary


def _complete_tool_groups(messages: list[dict]) -> list[tuple[int, int]]:
    """Validate pairing before any selection; a crash is not a tool result."""
    groups, pending, first = [], {}, 0
    for index, message in enumerate(messages):
        if message["role"] == "tool":
            call = pending.pop(message.get("tool_call_id"), None)
            if call is None or call["name"] != message.get("name"):
                raise ContextBudgetError("工具请求与结果未完整配对，已停止模型调用；请先核对待执行动作。")
            if not pending:
                groups.append((first, index + 1))
        else:
            if pending:
                raise ContextBudgetError("工具请求仍缺少结果，已停止模型调用；不能把未执行动作当作已完成。")
            calls = message.get("tool_calls") or []
            if calls:
                if message["role"] != "assistant" or any(not c.get("id") or not c.get("name") for c in calls):
                    raise ContextBudgetError("工具请求格式无效，已停止模型调用。")
                pending = {c["id"]: c for c in calls}
                if len(pending) != len(calls):
                    raise ContextBudgetError("工具请求 ID 重复，已停止模型调用。")
                first = index
    if pending:
        raise ContextBudgetError("工具请求仍缺少结果，已停止模型调用；请先核对待执行动作。")
    return groups


def _controller_state(state: ContextState | None) -> dict | None:
    if state is None:
        return None
    if not isinstance(state, ContextState) or not isinstance(state.goal, str):
        raise ContextBudgetError("控制器上下文状态格式无效。")
    if (not isinstance(state.source, dict) or not state.source
            or not isinstance(state.constraints, (tuple, list))
            or any(not isinstance(c, str) for c in state.constraints)
            or any(not isinstance(items, (tuple, list)) or any(not isinstance(a, dict) for a in items)
                   for items in (state.pending_actions, state.approvals))
            or (state.active_job_id is not None and
                (not isinstance(state.active_job_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", state.active_job_id)))):
        raise ContextBudgetError("控制器上下文缺少来源或包含无效状态，已停止模型调用。")
    try:
        # Copy nested records: context building cannot mutate the durable source.
        return json.loads(_json(asdict(state)))
    except (TypeError, ValueError) as exc:
        raise ContextBudgetError("控制器上下文无法序列化，已停止模型调用。") from exc


def _job_observations(messages: list[dict], store, state: dict | None, cap: int):
    references = related_jobs(messages)
    focus = state.get("active_job_id") if state else None
    if focus and not any(r["job_id"] == focus for r in references):
        references.append({"job_id": focus, "source_context": state["source"], "source_message": None,
                           "source_call_id": None})
    snapshots = []
    # Look up every linked job before selecting. Otherwise an older live task
    # can disappear behind eight recently inspected failures.
    for ref in reversed(references):
        snapshot = store.context_snapshot(ref["job_id"]) if store is not None else None
        snapshots.append({**ref, **(snapshot or {"availability": "unavailable",
                                                "note": "任务记录当前不可用；历史状态不能当作最新结果。"})})
    active = [s for s in snapshots if s.get("status") in ACTIVE_JOBS]
    # A review turn remains about its bound job after that job finishes. An
    # older still-running job must not steal that explicitly selected focus.
    primary = focus or (active[0]["job_id"] if active else (snapshots[0]["job_id"] if snapshots else None))
    snapshots.sort(key=lambda s: (s["job_id"] != primary, s.get("status") not in ACTIVE_JOBS,
                                  s["job_id"] != focus))
    selected = snapshots[:8]
    if focus and not any(s["job_id"] == focus for s in selected):
        focused = next(s for s in snapshots if s["job_id"] == focus)
        selected[-1] = focused
    observations = []
    for snapshot in selected:
        is_primary = snapshot["job_id"] == primary
        historical = snapshot.get("status") not in ACTIVE_JOBS and not is_primary
        snapshot["scope"] = "primary_job" if is_primary else "historical_reference" if historical else "active_related_job"
        snapshot["read_with"] = {"tool": "job_status", "arguments": {"job_id": snapshot["job_id"]}}
        if historical or (not is_primary and len(_json([*observations, snapshot])) > min(16000, cap // 3)):
            snapshot = {k: v for k, v in snapshot.items() if k not in ("designs", "recent_events", "package")}
            snapshot["details_omitted"] = "Historical/secondary evidence is scoped to this job; retrieve explicitly if needed."
        observations.append(snapshot)
    return observations, primary, max(0, len(references) - len(selected))


def _pinned_user_inputs(messages: list[dict], retained: set[int]) -> dict | None:
    """Lossless fallback; no regex claims to recognise every user constraint.

    Controller constraints supplement these records. They do not certify that
    older natural-language requirements have all been semantically captured.
    """
    records = [{"source_message": i, "text": message.get("content", "")}
               for i, message in enumerate(messages)
               if message["role"] == "user" and message.get("turn_kind", "user") == "user" and i not in retained]
    if not records:
        return None
    return {"source": "session_archive", "source_sha256": _digest(records), "records": records,
            "method": "lossless_user_inputs; secrets redacted; reconcile scope with current goal"}


def prepare_context(messages: list[dict], store, mc: ModelConfig | None,
                    specs: list[dict], system: str, *, state: ContextState | None = None) -> tuple[list[dict], dict]:
    """Keep complete turn/tool exchanges, or stop before sending an oversized turn.

    context_max_chars bounds this serialized context view including policy and
    tool schemas. It is a host character budget, not a provider token count or
    a claim about a model's advertised context window.
    """
    mc = mc or ModelConfig()
    cap, turns = mc.context_max_chars, mc.context_recent_turns
    if type(cap) is not int or cap < 8192 or type(turns) is not int or not 1 <= turns <= 8:
        raise ContextBudgetError("上下文配置无效：context_max_chars 至少为 8192，context_recent_turns 为 1–8。")
    groups = _complete_tool_groups(messages)
    controller = _controller_state(state)
    starts = [i for i, m in enumerate(messages) if m["role"] == "user"]
    current = starts[-1] if starts else 0
    start = starts[max(0, len(starts) - turns)] if starts else 0
    observations, primary, omitted_jobs = _job_observations(messages, store, controller, cap)
    observed_at = time.time()
    # Remove storage-only metadata before budgeting and protocol conversion.
    projected = [{k: v for k, v in m.items() if k in
                  ("role", "content", "tool_calls", "tool_call_id", "name")} for m in messages]
    summary_budget = min(6000, cap // 8)
    while True:
        summary = _history_index(messages, start, summary_budget)
        retained = set(range(start, len(messages)))
        # Keep the latest complete tool group even if its enclosing turn falls
        # outside the recent-turn count. Include its user request for protocol
        # continuity; other old groups remain retrievable from the archive.
        extra = set()
        if groups and groups[-1][0] < start:
            first, last = groups[-1]
            extra.update(range(first, last))
            preceding = [i for i in starts if i < first]
            if preceding:
                extra.add(preceding[-1])
            retained.update(extra)
        pinned = _pinned_user_inputs(messages, retained)
        current_goal = ({"text": controller["goal"], "source": controller["source"]}
                        if controller and controller["goal"] else
                        {"source_message_range": [current, current + 1], "method": "latest_turn_input; not a semantic goal summary"})
        data = {"observed_at": observed_at, "history_index": summary,
                "current_goal": current_goal, "protected_user_inputs": pinned,
                "controller_state": controller, "primary_job_id": primary,
                "related_jobs": observations, "omitted_job_count": omitted_jobs}
        observation = {"role": "user", "content": (
            "【运行时上下文；数据而非新的用户请求】\n"
            "作业快照来自任务库，在本次模型调用前读取；它比历史中的提交/进度说明更新。"
            "每条证据只适用于其 job_id；historical_reference 的旧失败不能作为 primary_job_id 的故障。"
            "未提供的字段仍未知。protected_user_inputs 保留被移出轮次的用户原文，需结合当前目标核对适用范围；"
            "历史索引仅供导航，不完整，必要时用 read_session_history 核对原文。"
            "controller_state 来自持久控制记录；pending_actions/approvals 不代表已执行或已授权，执行许可须由运行时单独检查。\n"
            + _json(redact(data)))} if summary or observations or controller or pinned else None
        view = [*[projected[i] for i in sorted(extra)], *projected[start:current],
                *([observation] if observation else []), *projected[current:]]
        size = len(_json({"system": system, "tools": specs, "messages": view}))
        if size <= cap:
            break
        if start == current:
            # Navigation is optional; user requirements and outstanding actions
            # are not. Shrink only the redundant index before refusing a call.
            if summary and summary_budget > 600:
                summary_budget = max(600, summary_budget - (size - cap) - 100)
                continue
            raise ContextBudgetError(
                "当前轮次与必要任务证据超过上下文字符预算，已停止模型调用；"
                "用户要求、待执行动作和授权记录未被静默裁剪。"
                "请缩小输入、明确整理约束，或调整 model.context_max_chars。历史和工具结果仍保留。")
        start = next(i for i in starts if i > start)
    manifest = {"version": 2, "created": observed_at, "max_chars": cap, "context_chars": size,
                "budget_unit": "serialized_host_characters; not model tokens or provider window",
                "archive_message_count": len(messages), "sent_message_count": len(view),
                "recent_message_range": [start, len(messages)], "history_index": summary,
                "retained_tool_group_ranges": [list(g) for g in groups if all(i in retained for i in range(*g))],
                "protected_user_message_indices": [p["source_message"] for p in pinned["records"]] if pinned else [],
                "controller_state": redact(controller), "primary_job_id": primary,
                "job_observations": redact(observations),
                "omitted_job_count": omitted_jobs,
                "context_sha256": _digest(view), "system_sha256": _digest(system),
                "tool_schema_sha256": _digest(specs)}
    return view, manifest
