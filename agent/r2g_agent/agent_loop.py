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
import threading
import weakref
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
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, timeout=30, isolation_level=None,
                                   check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        weakref.finalize(self, self._db.close)
        with self._c() as c:
            # Streaming token ledgers and read-only cost monitors run together.
            # A long reader must not block every streamed model write.
            c.execute('PRAGMA journal_mode=WAL')
            c.execute('PRAGMA synchronous=NORMAL')
            c.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, created REAL, "
                      "updated REAL, title TEXT, messages TEXT)")
            c.execute('CREATE TABLE IF NOT EXISTS session_deletions (id TEXT PRIMARY KEY)')
            c.execute('CREATE TABLE IF NOT EXISTS model_usage '
                      '(call_id TEXT PRIMARY KEY, session_id TEXT, created REAL, payload TEXT)')
            c.execute('CREATE INDEX IF NOT EXISTS model_usage_session ON model_usage(session_id)')

    @contextmanager
    def _c(self):
        with self._lock:
            yield self._db

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


def run_turn(adapter, sessions: SessionStore, sid: str, user_text: str, should_stop=lambda: False):
    from .lifecycle import resource_lock
    try:
        with resource_lock('session', sid, exclusive=True, blocking=False):
            if sessions.is_deleting(sid) or not sessions.exists(sid):
                yield {'type': 'error', 'message': '对话正在删除或已删除'}
                return
            yield from _run_turn(adapter, sessions, sid, user_text, should_stop)
    except BlockingIOError:
        yield {'type': 'error', 'message': '该对话正在处理上一条消息'}


STOPPED_NOTE = "（已停止）"


def _run_turn(adapter, sessions: SessionStore, sid: str, user_text: str, should_stop=lambda: False):
    """Generator of UI events for one user turn. `should_stop()` true ends the
    turn at the next event or tool boundary; what was said so far is kept."""
    messages = sessions.load(sid)
    messages.append({"role": "user", "content": user_text})
    title = user_text[:40]

    def stopped(text: str = "", thinking: str = ""):
        messages.append({"role": "assistant", "content": (text + "\n\n" if text else "") + STOPPED_NOTE,
                         "thinking": thinking, "tool_calls": []})
        sessions.save(sid, messages, title)
        return {"type": "stopped"}

    for step in range(MAX_STEPS):
        if sessions.is_deleting(sid):
            return
        if should_stop():
            yield stopped()
            yield {"type": "done"}
            return
        text, thinking, calls, err = "", "", [], None
        call_id = uuid.uuid4().hex
        mc = getattr(adapter, 'mc', None)
        sessions.record_usage(sid, call_id, {
            'provider': getattr(mc, 'provider', None), 'model': getattr(mc, 'model', None),
            'protocol': getattr(mc, 'protocol', None), 'step': step + 1,
            'usage_reported': False, 'stream_complete': False,
        })
        stream = adapter.stream(messages, tools.specs(), system_prompt())
        try:
            for ev in stream:
                if sessions.is_deleting(sid):
                    return
                if should_stop():
                    yield stopped(text, thinking)
                    yield {"type": "done"}
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
        finally:
            close = getattr(stream, "close", None)
            if close:
                close()           # a stopped turn also ends the model's HTTP stream
        if err:
            yield {"type": "error", "message": err}
            sessions.save(sid, messages, title)
            return
        messages.append({"role": "assistant", "content": text, "thinking": thinking,
                         "tool_calls": [{"id": c["id"], "name": c["name"], "arguments": c["arguments"]}
                                        for c in calls]})
        if not calls:
            break
        for i, c in enumerate(calls):
            if sessions.is_deleting(sid):
                return
            if should_stop():
                # every call the model made needs an answer, or the next turn's request is malformed
                for rest in calls[i:]:
                    messages.append({"role": "tool", "tool_call_id": rest["id"], "name": rest["name"],
                                     "content": _clip({"error": "stopped by the user before this tool ran"})})
                yield stopped()
                yield {"type": "done"}
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


class TurnRun:
    """One chat turn running on its own thread.

    The HTTP response only follows it: a browser that closes the tab, reloads
    or loses its connection mid-answer no longer abandons the turn's generator
    (which kept the conversation's lock held, so every later message read
    "该对话正在处理上一条消息" until the server restarted). The turn finishes,
    is saved, and the conversation is free again; `stop()` ends it early."""

    def __init__(self, events):
        self.events: list[dict] = []
        self.done = False
        self._stop = threading.Event()
        self._cond = threading.Condition()
        self._source = events
        self.started = time.time()

    def stop(self) -> None:
        self._stop.set()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def run(self) -> None:
        try:
            for ev in self._source(self._stop.is_set):
                with self._cond:
                    self.events.append(ev)
                    self._cond.notify_all()
        except Exception as e:                                   # noqa: BLE001
            with self._cond:
                self.events.append({"type": "error", "message": "内部错误：%s" % e})
        finally:
            with self._cond:
                self.done = True
                self._cond.notify_all()

    def follow(self, keepalive: float = 15.0):
        """Events from the start; None now and then while waiting (a keep-alive)."""
        i = 0
        while True:
            with self._cond:
                if i >= len(self.events) and not self.done:
                    self._cond.wait(keepalive)
                batch, finished = self.events[i:], self.done
            i += len(batch)
            if not batch and not finished:
                yield None
            yield from batch
            if finished and i >= len(self.events):
                return


class TurnRegistry:
    def __init__(self):
        self._runs: dict[str, TurnRun] = {}
        self._lock = threading.Lock()

    def start(self, adapter, sessions: SessionStore, sid: str, text: str) -> TurnRun | None:
        """None if this conversation is still answering the previous message."""
        with self._lock:
            cur = self._runs.get(sid)
            if cur and not cur.done:
                return None
            run = TurnRun(lambda should_stop: run_turn(adapter, sessions, sid, text, should_stop))
            old = time.time() - 3600
            for k in [k for k, r in self._runs.items() if r.done and r.started < old]:
                del self._runs[k]
            self._runs[sid] = run
        threading.Thread(target=run.run, daemon=True, name="r2g-chat-" + sid).start()
        return run

    def busy(self, sid: str) -> bool:
        run = self._runs.get(sid)
        return bool(run and not run.done)

    def stop(self, sid: str) -> bool:
        run = self._runs.get(sid)
        if run and not run.done:
            run.stop()
            return True
        return False
