"""The agent loop: model <-> tools, model-agnostic, streamed as events.

One turn: append the user message, call the model with the tool specs, run any
tool calls it makes, feed the results back, repeat until it answers without a
tool call (or the step budget runs out). Every step is yielded as an event the
UI renders live (text deltas, tool calls, tool results).

Sessions are persisted so a conversation survives a server restart.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import tools
from .paths import PKG_DIR, r2g_home
from .tool_outputs import ToolContext, ToolOutputError, ToolOutputStore

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
        self.outputs = ToolOutputStore(self.path)

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

    def begin_model_call(self, sid: str, step: int, metadata: dict) -> str:
        cid = uuid.uuid4().hex
        with self._c() as c:
            c.execute("INSERT INTO model_calls VALUES (?,?,?,?,?,?,?)",
                      (cid, sid, step, "pending", time.time(), None, json.dumps(metadata)))
        return cid

    def finish_model_call(self, cid: str, status: str, metadata: dict) -> None:
        with self._c() as c:
            c.execute("UPDATE model_calls SET status=?, finished=?, metadata=? WHERE id=? AND status='pending'",
                      (status, time.time(), json.dumps(metadata), cid))

    def model_calls(self, sid: str) -> list[dict]:
        with self._c() as c:
            rows = c.execute("SELECT * FROM model_calls WHERE session_id=? ORDER BY started", (sid,)).fetchall()
        return [dict(r, metadata=json.loads(r["metadata"])) for r in rows]


def run_turn(adapter, sessions: SessionStore, sid: str, user_text: str):
    """Generator of UI events for one user turn."""
    with sessions._c() as conn:
        if not conn.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone():
            yield {"type": "error", "message": "unknown session"}
            return
    messages = sessions.load(sid)
    messages.append({"role": "user", "content": user_text})
    title = user_text[:40]
    sessions.save(sid, messages, title)
    context = ToolContext(sid, sessions.outputs)
    for step in range(MAX_STEPS):
        text, calls, err = "", [], None
        mc = getattr(adapter, "mc", None)
        metadata = {"provider": getattr(mc, "provider", None), "model_requested": getattr(mc, "model", None),
                    "model_observed": None, "stream_complete": False, "usage": {"status": "unresolved"}}
        cid = sessions.begin_model_call(sid, step, metadata)
        ended = False
        try:
            for ev in adapter.stream(messages, tools.specs(session_scoped=True), system_prompt()):
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
        yield {"type": "model_response", "id": cid, "metadata": metadata}
        if err:
            yield {"type": "error", "message": err}
            sessions.save(sid, messages, title)
            return
        messages.append({"role": "assistant", "content": text, "model_call_id": cid,
                         "tool_calls": [{"id": c["id"], "name": c["name"], "arguments": c["arguments"]}
                                        for c in calls]})
        if not calls:
            break
        for index, c in enumerate(calls):
            yield {"type": "tool_call", "id": c["id"], "name": c["name"], "arguments": c["arguments"]}
            result = tools.call(c["name"], c["arguments"], context=context)
            try:
                view, ref = sessions.outputs.record(sid, c["id"], c["name"], result)
            except ToolOutputError as e:
                # The tool may already have had side effects. Do not call it again.
                failure = {"error": str(e), "error_code": "tool_output_not_recorded",
                           "execution_may_have_completed": True}
                messages.append({"role": "tool", "tool_call_id": c["id"], "name": c["name"],
                                 "content": json.dumps(failure)})
                for pending in calls[index + 1:]:
                    messages.append({"role": "tool", "tool_call_id": pending["id"],
                                     "name": pending["name"], "content": json.dumps({
                                         "error": "not executed: tool observation storage failed",
                                         "error_code": "not_executed"})})
                sessions.save(sid, messages, title)
                yield {"type": "tool_result", "id": c["id"], "name": c["name"], "result": failure}
                yield {"type": "error", "message": "工具结果未能保存，本轮已停止；请核对任务状态后再决定下一步。"}
                return
            messages.append({"role": "tool", "tool_call_id": c["id"], "name": c["name"],
                             "content": json.dumps(view, ensure_ascii=False, default=str), "output_ref": ref})
            sessions.save(sid, messages, title)
            yield {"type": "tool_result", "id": c["id"], "name": c["name"], "result": view,
                   "output_ref": ref}
        sessions.save(sid, messages, title)
    else:
        yield {"type": "text", "delta": "\n\n（已达到单轮工具调用上限，先停在这里。）"}
    sessions.save(sid, messages, title)
    yield {"type": "done"}
