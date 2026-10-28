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
            c.execute('CREATE TABLE IF NOT EXISTS session_deletions (id TEXT PRIMARY KEY)')
            c.execute('CREATE TABLE IF NOT EXISTS model_usage '
                      '(call_id TEXT PRIMARY KEY, session_id TEXT, created REAL, payload TEXT)')
            c.execute('CREATE INDEX IF NOT EXISTS model_usage_session ON model_usage(session_id)')

    @contextmanager
    def _c(self):
        c = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        try:
            yield c
        finally:
            c.close()

    def exists(self, sid: str) -> bool:
        with self._c() as c:
            return c.execute('SELECT 1 FROM sessions WHERE id=?', (sid,)).fetchone() is not None

    def is_deleting(self, sid: str) -> bool:
        with self._c() as c:
            return c.execute('SELECT 1 FROM session_deletions WHERE id=?', (sid,)).fetchone() is not None

    def mark_deleting(self, sid: str) -> None:
        with self._c() as c:
            c.execute('INSERT OR IGNORE INTO session_deletions VALUES (?)', (sid,))

    def erase(self, sid: str) -> None:
        with self._c() as c:
            c.execute('DELETE FROM sessions WHERE id=?', (sid,))
            c.execute('DELETE FROM model_usage WHERE session_id=?', (sid,))
        # Keep the tiny tombstone so an old stream cannot revive the session.

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

    def record_usage(self, sid: str, call_id: str, payload: dict) -> None:
        with self._c() as c:
            c.execute('INSERT OR REPLACE INTO model_usage SELECT ?, ?, ?, ? '
                      'WHERE EXISTS (SELECT 1 FROM sessions WHERE id=?)',
                      (call_id, sid, time.time(), json.dumps(payload), sid))

    def usage(self, sid: str) -> dict:
        with self._c() as c:
            rows = c.execute('SELECT call_id, created, payload FROM model_usage '
                             'WHERE session_id=? ORDER BY created, call_id', (sid,)).fetchall()
        calls = [{**json.loads(r['payload']), 'call_id': r['call_id'], 'created': r['created']} for r in rows]
        counters = ('input_tokens', 'output_tokens', 'total_tokens', 'cache_read_input_tokens',
                    'uncached_input_tokens', 'cache_creation_input_tokens', 'reasoning_tokens')
        complete = bool(calls) and all(c.get('usage_reported') and c.get('stream_complete') for c in calls)
        totals = {key: sum(c[key] for c in calls) if complete and all(c.get(key) is not None for c in calls)
                  else None for key in counters}
        observed = {key: sum(c[key] for c in calls if c.get(key) is not None)
                    if any(c.get(key) is not None for c in calls) else None for key in counters}
        return {'session_id': sid, 'model_calls': len(calls), 'totals': totals, 'calls': calls,
                'observed_totals': observed, 'usage_complete': complete,
                'complete_usage_calls': sum(bool(c.get('usage_reported') and c.get('stream_complete')
                                                and c.get('total_tokens') is not None) for c in calls)}


def _clip(obj) -> str:
    s = json.dumps(obj, ensure_ascii=False, default=str)
    if len(s) > MAX_TOOL_RESULT_CHARS:
        s = s[:MAX_TOOL_RESULT_CHARS] + "...(truncated)"
    return s


def run_turn(adapter, sessions: SessionStore, sid: str, user_text: str):
    from .lifecycle import resource_lock
    try:
        with resource_lock('session', sid, exclusive=True, blocking=False):
            if sessions.is_deleting(sid) or not sessions.exists(sid):
                yield {'type': 'error', 'message': '对话正在删除或已删除'}
                return
            yield from _run_turn(adapter, sessions, sid, user_text)
    except BlockingIOError:
        yield {'type': 'error', 'message': '该对话正在处理上一条消息'}


def _run_turn(adapter, sessions: SessionStore, sid: str, user_text: str):
    """Generator of UI events for one user turn."""
    messages = sessions.load(sid)
    messages.append({"role": "user", "content": user_text})
    title = user_text[:40]
    for step in range(MAX_STEPS):
        if sessions.is_deleting(sid):
            return
        text, thinking, calls, err = "", "", [], None
        call_id = uuid.uuid4().hex
        mc = getattr(adapter, 'mc', None)
        sessions.record_usage(sid, call_id, {
            'provider': getattr(mc, 'provider', None), 'model': getattr(mc, 'model', None),
            'protocol': getattr(mc, 'protocol', None), 'step': step + 1,
            'usage_reported': False, 'stream_complete': False,
        })
        for ev in adapter.stream(messages, tools.specs(), system_prompt()):
            if sessions.is_deleting(sid):
                return
            if ev["type"] == "text":
                text += ev["delta"]
                yield ev
            elif ev["type"] == "thinking":
                thinking += ev["delta"]
                yield ev
            elif ev["type"] == "tool_call":
                calls.append(ev)
            elif ev["type"] == "error":
                err = ev["message"]
            elif ev["type"] == "usage":
                ev = {**ev, "call_id": call_id, "step": step + 1}
                sessions.record_usage(sid, call_id, ev)
                yield ev
        if err:
            yield {"type": "error", "message": err}
            sessions.save(sid, messages, title)
            return
        messages.append({"role": "assistant", "content": text, "thinking": thinking,
                         "tool_calls": [{"id": c["id"], "name": c["name"], "arguments": c["arguments"]}
                                        for c in calls]})
        if not calls:
            break
        for c in calls:
            if sessions.is_deleting(sid):
                return
            yield {"type": "tool_call", "id": c["id"], "name": c["name"], "arguments": c["arguments"]}
            token = tools.CURRENT_SESSION.set(sid)
            try:
                result = tools.call(c["name"], c["arguments"])
            finally:
                tools.CURRENT_SESSION.reset(token)
            yield {"type": "tool_result", "id": c["id"], "name": c["name"], "result": result}
            messages.append({"role": "tool", "tool_call_id": c["id"], "name": c["name"], "content": _clip(result)})
        sessions.save(sid, messages, title)
    else:
        yield {"type": "text", "delta": "\n\n（已达到单轮工具调用上限，先停在这里。）"}
    sessions.save(sid, messages, title)
    yield {"type": "done"}
