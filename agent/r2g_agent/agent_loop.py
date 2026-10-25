"""The agent loop: model <-> tools, model-agnostic, streamed as events.

One turn: append the user message, call the model with the tool specs, run any
tool calls it makes, feed the results back, repeat until it answers without a
tool call (or the step budget runs out). Every step is yielded as an event the
UI renders live (text deltas, tool calls, tool results).

Sessions are persisted so a conversation survives a server restart.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from . import tools
from .model_context import ContextBudgetError, prepare_context
from .paths import PKG_DIR, r2g_home
from .tool_outputs import ToolContext, ToolOutputError, ToolOutputStore
from .turns import TurnStore, TurnConflict, TERMINAL

MAX_STEPS = 12


def system_prompt() -> str:
    return (PKG_DIR / "prompts" / "system.md").read_text()


class SessionStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or r2g_home() / "sessions.sqlite")
        with self._c() as c:
            c.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, created REAL, "
                      "updated REAL, title TEXT, messages TEXT)")
            c.execute("CREATE TABLE IF NOT EXISTS model_calls (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, "
                      "step INTEGER NOT NULL, status TEXT NOT NULL, started REAL NOT NULL, "
                      "finished REAL, metadata TEXT NOT NULL)")
            c.execute("CREATE INDEX IF NOT EXISTS model_calls_session ON model_calls(session_id, started)")
            c.execute("BEGIN IMMEDIATE")
            if "context" not in {r[1] for r in c.execute("PRAGMA table_info(model_calls)")}:
                c.execute("ALTER TABLE model_calls ADD COLUMN context TEXT NOT NULL DEFAULT '{}'")
            if "turn_id" not in {r[1] for r in c.execute("PRAGMA table_info(model_calls)")}:
                c.execute("ALTER TABLE model_calls ADD COLUMN turn_id TEXT")
        self.outputs = ToolOutputStore(self.path)
        self.turns = TurnStore(self.path)

    @contextmanager
    def _c(self):
        c = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def create(self) -> str:
        sid = uuid.uuid4().hex[:12]
        with self._c() as c:
            c.execute("INSERT INTO sessions VALUES (?,?,?,?,?)", (sid, time.time(), time.time(), "", "[]"))
        return sid

    def load(self, sid: str) -> list[dict]:
        with self._c() as c:
            r = c.execute("SELECT messages FROM sessions WHERE id=?", (sid,)).fetchone()
        # Historical stores may contain provider-private reasoning. Keep it out
        # of API responses and future model context without rewriting old rows.
        return [{k: v for k, v in m.items() if k != "thinking"}
                for m in json.loads(r["messages"])] if r else []

    def save(self, sid: str, messages: list[dict], title: str | None = None) -> None:
        with self._c() as c:
            c.execute("UPDATE sessions SET messages=?, updated=?, title=COALESCE(NULLIF(title,''), ?) WHERE id=?",
                      (json.dumps(messages, ensure_ascii=False), time.time(), title or "", sid))

    def list(self, limit: int = 30) -> list[dict]:
        with self._c() as c:
            rs = c.execute("SELECT id, created, updated, title FROM sessions ORDER BY updated DESC LIMIT ?",
                           (limit,)).fetchall()
        return [dict(r) for r in rs]

    def read_history(self, sid: str, offset: int = 0, limit: int = 4000, query: str = "") -> dict:
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 4000:
            raise ValueError("offset must be nonnegative and limit must be between 1 and 4000")
        if not isinstance(query, str) or len(query) > 256:
            raise ValueError("query must be a string of at most 256 characters")
        # JSON character offsets, including role and tool IDs, are stable for an
        # unchanged archive; the hash tells callers when the archive has grown.
        text = json.dumps(self.load(sid), ensure_ascii=False, sort_keys=True)
        match = text.find(query, offset) if query else None
        if match is not None and match >= 0:
            offset = match
        piece = "" if match == -1 else text[offset:offset + limit]
        end = offset + len(piece)
        return {"source": "sessions.sqlite", "sha256": hashlib.sha256(text.encode()).hexdigest(),
                "text": piece, "offset": offset, "next_offset": end if end < len(text) and match != -1 else None,
                "total_chars": len(text), "match": match}

    def begin_model_call(self, sid: str, step: int, metadata: dict, request_context: dict | None = None,
                         turn_id: str | None = None) -> str:
        cid = uuid.uuid4().hex
        with self._c() as c:
            c.execute("BEGIN IMMEDIATE")
            if turn_id:
                turn = c.execute("SELECT max_model_calls FROM turns WHERE id=? AND status='running'", (turn_id,)).fetchone()
                used = c.execute("SELECT COUNT(*) FROM model_calls WHERE turn_id=?", (turn_id,)).fetchone()[0]
                if not turn or used >= turn[0]:
                    raise TurnConflict("turn model-call budget exhausted")
            c.execute("INSERT INTO model_calls "
                      "(id,session_id,step,status,started,finished,metadata,context,turn_id) VALUES (?,?,?,?,?,?,?,?,?)",
                      (cid, sid, step, "pending", time.time(), None, json.dumps(metadata),
                       json.dumps(request_context or {}, ensure_ascii=False), turn_id))
        return cid

    def finish_model_call(self, cid: str, status: str, metadata: dict) -> None:
        with self._c() as c:
            c.execute("UPDATE model_calls SET status=?, finished=?, metadata=? WHERE id=? AND status='pending'",
                      (status, time.time(), json.dumps(metadata), cid))

    def model_calls(self, sid: str) -> list[dict]:
        with self._c() as c:
            rows = c.execute("SELECT * FROM model_calls WHERE session_id=? ORDER BY started", (sid,)).fetchall()
        return [dict(r, metadata=json.loads(r["metadata"]), context=json.loads(r["context"])) for r in rows]


def run_turn(adapter, sessions: SessionStore, sid: str, user_text: str, *, turn_id: str | None = None):
    """Run a durable turn; callers may replay its events without rerunning it."""
    with sessions._c() as conn:
        if not conn.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone():
            yield {"type": "error", "message": "unknown session"}
            return
    if turn_id is None:
        turn, _ = sessions.turns.create(sid, uuid.uuid4().hex, {"message": user_text},
                                        job_id=sessions.turns.latest_job(sid), max_model_calls=MAX_STEPS)
        turn_id = turn["id"]
    turn = sessions.turns.get(turn_id)
    if not turn or turn["session_id"] != sid:
        raise TurnConflict("turn does not belong to this session")
    if not sessions.turns.claim(turn_id):
        return
    try:
        for event in _run_turn(adapter, sessions, sid, turn):
            if event["type"] in ("done", "error"):
                status = "complete" if event["type"] == "done" else (
                    "recovery_required" if event.get("recovery_required") else "error")
                yield sessions.turns.finish(turn_id, status, event)
            else:
                yield sessions.turns.publish(turn_id, event)
    except Exception as exc:
        row = sessions.turns.get(turn_id)
        if row and row["status"] not in TERMINAL:
            yield sessions.turns.finish(turn_id, "recovery_required", {
                "type": "error", "message": "本轮执行中断，需要核对已执行操作（%s）。" % type(exc).__name__})
    finally:
        row = sessions.turns.get(turn_id)
        if row and row["status"] == "running":
            sessions.turns.finish(turn_id, "recovery_required", {
                "type": "error", "message": "本轮未确认结束；已保存操作记录，不会自动重放。"})


def _run_turn(adapter, sessions: SessionStore, sid: str, turn: dict):
    user_text = turn["payload"]["message"]
    tid = turn["id"]
    messages = sessions.load(sid)
    messages.append({"role": "user", "content": user_text, "turn_id": tid, "turn_kind": turn["kind"]})
    title = user_text[:40]
    sessions.save(sid, messages, title)
    context = ToolContext(sid, sessions.outputs, sessions.read_history, turn_id=tid,
                          job_id=turn["job_id"], read_only=turn["kind"] == "job_review",
                          grant_id=turn["payload"].get("grant_id"))
    for step in range(turn["max_model_calls"]):
        text, calls, err = "", [], None
        mc = getattr(adapter, "mc", None)
        context = replace(context, job_id=sessions.turns.get(tid)["job_id"])
        specs, system = tools.specs(session_scoped=True, context=context), system_prompt()
        if context.read_only:
            specs = [s for s in specs if s["name"] in tools.REVIEW_TOOLS]
            system += "\n本轮是后台任务的只读审查。只解释已测结果、读取证据或提出需授权的修复提案；不得启动、修改或取消任务。"
        try:
            model_messages, request_context = prepare_context(
                messages, tools.RT.store, mc, specs, system, state=sessions.turns.context_state(tid))
        except (ContextBudgetError, sqlite3.Error) as exc:
            yield {"type": "error", "message": str(exc) if isinstance(exc, ContextBudgetError)
                   else "无法读取最新任务上下文，本轮已停止；请稍后重试。"}
            return
        metadata = {"provider": getattr(mc, "provider", None), "model_requested": getattr(mc, "model", None),
                    "model_observed": None, "stream_complete": False, "usage": {"status": "unresolved"}}
        if context.grant_id:
            from . import repairs
            try:
                repairs.reserve_model_for_turn(sessions.path, context.grant_id, sid)
            except ValueError as exc:
                yield {"type": "error", "message": str(exc)}
                return
        cid = sessions.begin_model_call(sid, step, metadata, request_context, turn_id=tid)
        ended = False
        try:
            for ev in adapter.stream(model_messages, specs, system):
                if ev["type"] == "text":
                    text += ev["delta"]
                    yield ev
                elif ev["type"] == "tool_call":
                    calls.append(ev)
                elif ev["type"] in ("done", "error"):
                    ended = True
                    if ev.get("metadata"):
                        metadata = ev["metadata"]
                    if ev["type"] == "error":
                        err = ev["message"]
                    elif metadata.get("stream_complete") is not True:
                        err = "模型响应流未完整结束，已停止执行本轮工具请求。"
                # Private reasoning deltas are neither published nor retained.
        except Exception as exc:
            err = "调用模型出错：%s" % type(exc).__name__
        if not ended and not err:
            err = "模型响应未确认完成，已停止执行本轮工具请求。"
        sessions.finish_model_call(cid, "error" if err else "complete", metadata)
        if context.grant_id:
            repairs.record_model_usage(sessions.path, context.grant_id, cid, metadata)
        yield {"type": "model_response", "id": cid, "metadata": metadata}
        if err:
            yield {"type": "error", "message": err}
            sessions.save(sid, messages, title)
            return
        if len({c["id"] for c in calls}) != len(calls):
            yield {"type": "error", "message": "模型返回重复工具调用标识，本轮未执行这些操作。"}
            return
        messages.append({"role": "assistant", "content": text, "model_call_id": cid, "turn_id": tid,
                         "tool_calls": [{"id": c["id"], "name": c["name"], "arguments": c["arguments"]}
                                        for c in calls]})
        sessions.save(sid, messages, title)
        if not calls:
            break
        for index, c in enumerate(calls):
            action = sessions.turns.begin_action(tid, cid, c)
            if action["status"] != "accepted" or not sessions.turns.start_action(action["id"]):
                yield {"type": "error", "message": "重复或未确认的工具操作已停止，需先核对记录。", "recovery_required": True}
                return
            context = replace(context, action_id=action["id"], job_id=sessions.turns.get(tid)["job_id"])
            yield {"type": "tool_call", "id": c["id"], "action_id": action["id"],
                   "name": c["name"], "arguments": c["arguments"]}
            result = tools.call(c["name"], c["arguments"], context=context)
            try:
                view, ref = sessions.outputs.record(sid, c["id"], c["name"], result)
            except ToolOutputError as e:
                # The tool may already have had side effects. Do not call it again.
                failure = {"error": str(e), "error_code": "tool_output_not_recorded",
                           "execution_may_have_completed": True}
                messages.append({"role": "tool", "tool_call_id": c["id"], "name": c["name"],
                                 "content": json.dumps(failure), "turn_id": tid, "action_id": action["id"]})
                for pending in calls[index + 1:]:
                    messages.append({"role": "tool", "tool_call_id": pending["id"],
                                     "name": pending["name"], "turn_id": tid, "content": json.dumps({
                                         "error": "not executed: tool observation storage failed",
                                         "error_code": "not_executed"})})
                sessions.save(sid, messages, title)
                yield {"type": "tool_result", "id": c["id"], "name": c["name"], "result": failure}
                yield {"type": "error", "message": "工具结果未能保存，本轮已停止；请核对任务状态后再决定下一步。",
                       "recovery_required": True}
                return
            messages.append({"role": "tool", "tool_call_id": c["id"], "name": c["name"],
                             "content": json.dumps(view, ensure_ascii=False, default=str), "output_ref": ref,
                             "turn_id": tid, "action_id": action["id"]})
            job_id = None
            if c["name"] in ("start_job", "start_batch", "execute_rtl_repair") and isinstance(result, dict) and result.get("job_id") and not result.get("error"):
                job_id = result["job_id"]
            sessions.turns.complete_action(action["id"], view, ref, messages, title, job_id=job_id)
            yield {"type": "tool_result", "id": c["id"], "action_id": action["id"], "name": c["name"], "result": view,
                   "output_ref": ref}
        sessions.save(sid, messages, title)
    else:
        yield {"type": "text", "delta": "\n\n（已达到单轮工具调用上限，先停在这里。）"}
    sessions.save(sid, messages, title)
    yield {"type": "done"}
