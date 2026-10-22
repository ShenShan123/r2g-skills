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
from pathlib import Path

from . import tools
from .paths import PKG_DIR, r2g_home

MAX_STEPS = 12
MAX_TOOL_RESULT_CHARS = 12000


def system_prompt() -> str:
    return (PKG_DIR / "prompts" / "system.md").read_text()


class SessionStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or r2g_home() / "sessions.sqlite")
        with self._c() as c:
            c.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, created REAL, "
                      "updated REAL, title TEXT, messages TEXT)")

    def _c(self):
        c = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        return c

    def create(self) -> str:
        sid = uuid.uuid4().hex[:12]
        with self._c() as c:
            c.execute("INSERT INTO sessions VALUES (?,?,?,?,?)", (sid, time.time(), time.time(), "", "[]"))
        return sid

    def load(self, sid: str) -> list[dict]:
        with self._c() as c:
            r = c.execute("SELECT messages FROM sessions WHERE id=?", (sid,)).fetchone()
        return json.loads(r["messages"]) if r else []

    def save(self, sid: str, messages: list[dict], title: str | None = None) -> None:
        with self._c() as c:
            c.execute("UPDATE sessions SET messages=?, updated=?, title=COALESCE(NULLIF(title,''), ?) WHERE id=?",
                      (json.dumps(messages, ensure_ascii=False), time.time(), title or "", sid))

    def list(self, limit: int = 30) -> list[dict]:
        with self._c() as c:
            rs = c.execute("SELECT id, created, updated, title FROM sessions ORDER BY updated DESC LIMIT ?",
                           (limit,)).fetchall()
        return [dict(r) for r in rs]


def _clip(obj) -> str:
    s = json.dumps(obj, ensure_ascii=False, default=str)
    if len(s) > MAX_TOOL_RESULT_CHARS:
        s = s[:MAX_TOOL_RESULT_CHARS] + "...(truncated)"
    return s


def run_turn(adapter, sessions: SessionStore, sid: str, user_text: str):
    """Generator of UI events for one user turn."""
    messages = sessions.load(sid)
    messages.append({"role": "user", "content": user_text})
    title = user_text[:40]
    for step in range(MAX_STEPS):
        text, calls, err = "", [], None
        for ev in adapter.stream(messages, tools.specs(), system_prompt()):
            if ev["type"] == "text":
                text += ev["delta"]
                yield ev
            elif ev["type"] == "tool_call":
                calls.append(ev)
            elif ev["type"] == "error":
                err = ev["message"]
        if err:
            yield {"type": "error", "message": err}
            sessions.save(sid, messages, title)
            return
        messages.append({"role": "assistant", "content": text,
                         "tool_calls": [{"id": c["id"], "name": c["name"], "arguments": c["arguments"]}
                                        for c in calls]})
        if not calls:
            break
        for c in calls:
            yield {"type": "tool_call", "id": c["id"], "name": c["name"], "arguments": c["arguments"]}
            result = tools.call(c["name"], c["arguments"])
            yield {"type": "tool_result", "id": c["id"], "name": c["name"], "result": result}
            messages.append({"role": "tool", "tool_call_id": c["id"], "name": c["name"], "content": _clip(result)})
        sessions.save(sid, messages, title)
    else:
        yield {"type": "text", "delta": "\n\n（已达到单轮工具调用上限，先停在这里。）"}
    sessions.save(sid, messages, title)
    yield {"type": "done"}
