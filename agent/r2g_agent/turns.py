"""Durable conversation turns, action intents, and replayable public events.

A dead worker's running turn is never replayed: either a model request or a
tool side effect may have happened. Queued turns have not started and may be
claimed after restart. SQLite is the authority across workers and SSE clients.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .processes import boot_id, proc_start, ProcessInspectionError
from .tool_outputs import redact

OWNER = f"{os.getpid()}:{proc_start(os.getpid())}:{boot_id()}"
TERMINAL = ("complete", "error", "recovery_required")


class TurnConflict(ValueError):
    pass


class SessionBusy(TurnConflict):
    pass


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def owner_alive(owner: str | None) -> bool:
    try:
        pid, start, boot = (owner or "").split(":")
        pid = int(pid)
    except ValueError:
        return True
    try:
        observed_start = proc_start(pid)
    except (FileNotFoundError, ProcessLookupError):
        return False
    except ProcessInspectionError as exc:
        # proc_start wraps unreadable /proc failures. ESRCH can race a read
        # just as ENOENT can, while EPERM never proves that a worker exited.
        return not isinstance(exc.__cause__, (FileNotFoundError, ProcessLookupError))
    except OSError:
        return True
    if observed_start is None or observed_start != start:
        return False
    try:
        return boot == boot_id()
    except OSError:
        return True  # Unreadable ownership never authorizes a second worker.


def _row(row):
    if row is None:
        return None
    result = dict(row)
    for key in ("payload", "arguments", "result", "output_ref"):
        if key in result and result[key] is not None:
            result[key] = json.loads(result[key])
    return result


class TurnStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        with self.connection() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS runtime_schema (component TEXT PRIMARY KEY, version INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS turns (
                  id TEXT PRIMARY KEY, session_id TEXT NOT NULL, request_key TEXT NOT NULL UNIQUE,
                  request_hash TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
                  job_id TEXT, status TEXT NOT NULL, owner TEXT, created REAL NOT NULL,
                  started REAL, finished REAL, max_model_calls INTEGER NOT NULL,
                  max_actions INTEGER NOT NULL, error TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS turns_active_session ON turns(session_id)
                  WHERE status='running';
                CREATE TABLE IF NOT EXISTS actions (
                  id TEXT PRIMARY KEY, session_id TEXT NOT NULL, turn_id TEXT NOT NULL,
                  model_call_id TEXT NOT NULL, call_id TEXT NOT NULL, tool_name TEXT NOT NULL,
                  request_hash TEXT NOT NULL, arguments TEXT NOT NULL, status TEXT NOT NULL,
                  created REAL NOT NULL, started REAL, finished REAL, result TEXT, output_ref TEXT,
                  UNIQUE(turn_id,model_call_id,call_id)
                );
                CREATE TABLE IF NOT EXISTS turn_events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, turn_id TEXT NOT NULL,
                  session_id TEXT NOT NULL, created REAL NOT NULL, payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS turn_events_turn ON turn_events(turn_id,id);
                CREATE TABLE IF NOT EXISTS job_links (
                  session_id TEXT NOT NULL, turn_id TEXT NOT NULL, action_id TEXT,
                  job_id TEXT NOT NULL, created REAL NOT NULL,
                  PRIMARY KEY(session_id,turn_id,job_id)
                );
            """)
            c.execute("INSERT OR IGNORE INTO runtime_schema VALUES ('conversation_turns', 1)")

    @contextmanager
    def connection(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def get(self, tid: str) -> dict | None:
        with self.connection() as c:
            return _row(c.execute("SELECT * FROM turns WHERE id=?", (tid,)).fetchone())

    def by_key(self, key: str) -> dict | None:
        with self.connection() as c:
            return _row(c.execute("SELECT * FROM turns WHERE request_key=?", (key,)).fetchone())

    def list(self, sid: str) -> list[dict]:
        with self.connection() as c:
            return [_row(r) for r in c.execute("SELECT * FROM turns WHERE session_id=? ORDER BY created,id", (sid,))]

    def create(self, sid: str, key: str, payload: dict, *, kind="user", job_id=None,
               max_model_calls=12, max_actions=24, allow_queue=False) -> tuple[dict, bool]:
        if not isinstance(key, str) or not 1 <= len(key) <= 200:
            raise ValueError("request_id must contain 1–200 characters")
        identity = digest({"session_id": sid, "kind": kind, "payload": payload, "job_id": job_id})
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            existing = c.execute("SELECT * FROM turns WHERE request_key=?", (key,)).fetchone()
            if existing:
                if existing["request_hash"] != identity:
                    raise TurnConflict("request_id was already used for another request")
                return _row(existing), False
            if not c.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone():
                raise ValueError("unknown session")
            if not allow_queue and c.execute(
                "SELECT 1 FROM turns WHERE session_id=? AND status IN ('queued','running')", (sid,)
            ).fetchone():
                raise SessionBusy("this session already has an active turn")
            tid = uuid.uuid4().hex
            c.execute("INSERT INTO turns VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (tid, sid, key, identity, kind, canonical(payload), job_id, "queued", None,
                       time.time(), None, None, max_model_calls, max_actions, None))
            if job_id:
                self._link_job(c, sid, tid, job_id)
            return _row(c.execute("SELECT * FROM turns WHERE id=?", (tid,)).fetchone()), True

    def claim(self, tid: str) -> bool:
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM turns WHERE id=?", (tid,)).fetchone()
            if not row or row["status"] != "queued":
                return False
            if c.execute("SELECT 1 FROM turns WHERE session_id=? AND status='running'", (row["session_id"],)).fetchone():
                return False
            first = c.execute("SELECT id FROM turns WHERE session_id=? AND status='queued' ORDER BY created,id LIMIT 1",
                              (row["session_id"],)).fetchone()
            if first[0] != tid:
                return False
            c.execute("UPDATE turns SET status='running',owner=?,started=? WHERE id=?", (OWNER, time.time(), tid))
            return True

    @staticmethod
    def _event(c, row, event: dict) -> dict:
        event = {**redact(event), "session_id": row["session_id"], "turn_id": row["id"]}
        cur = c.execute("INSERT INTO turn_events(turn_id,session_id,created,payload) VALUES (?,?,?,?)",
                        (row["id"], row["session_id"], time.time(), canonical(event)))
        return {**event, "event_id": cur.lastrowid}

    def publish(self, tid: str, event: dict) -> dict:
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM turns WHERE id=? AND status='running' AND owner=?", (tid, OWNER)).fetchone()
            if not row:
                raise TurnConflict("turn worker no longer owns this turn")
            return self._event(c, row, event)

    def finish(self, tid: str, status: str, event: dict) -> dict:
        if status not in TERMINAL:
            raise ValueError("invalid terminal turn status")
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM turns WHERE id=? AND status='running' AND owner=?", (tid, OWNER)).fetchone()
            if not row:
                raise TurnConflict("turn worker no longer owns this turn")
            c.execute("UPDATE turns SET status=?,finished=?,error=? WHERE id=?",
                      (status, time.time(), event.get("message"), tid))
            if status == "recovery_required":
                self._interrupt_actions(c, row)
                c.execute("UPDATE model_calls SET status='unresolved',finished=? WHERE turn_id=? AND status='pending'",
                          (time.time(), tid))
            return self._event(c, row, event)

    @staticmethod
    def _interrupt_actions(c, turn) -> None:
        c.execute("UPDATE actions SET status='recovery_required' WHERE turn_id=? AND status='executing'", (turn["id"],))
        c.execute("UPDATE actions SET status='not_executed',finished=? WHERE turn_id=? AND status='accepted'",
                  (time.time(), turn["id"]))
        row = c.execute("SELECT messages FROM sessions WHERE id=?", (turn["session_id"],)).fetchone()
        messages = json.loads(row[0])
        pending = {}
        for message in messages:
            if message.get("turn_id") != turn["id"]:
                continue
            if message["role"] == "assistant":
                pending.update({call["id"]: call for call in message.get("tool_calls", [])})
            elif message["role"] == "tool":
                pending.pop(message.get("tool_call_id"), None)
        actions = {r["call_id"]: r for r in c.execute("SELECT * FROM actions WHERE turn_id=? ORDER BY created,id", (turn["id"],))}
        missing = list(pending.values())
        for call in missing:
            action = actions.get(call["id"])
            uncertain = action is not None and action["status"] == "recovery_required"
            observation = {"error": "worker stopped; execution requires reconciliation" if uncertain
                           else "not executed before the worker stopped",
                           "error_code": "execution_unconfirmed" if uncertain else "not_executed",
                           "execution_may_have_completed": uncertain}
            messages.append({"role": "tool", "tool_call_id": call["id"], "name": call["name"],
                             "content": canonical(observation), "turn_id": turn["id"], "recovery_observation": True})
        if missing:
            c.execute("UPDATE sessions SET messages=?,updated=? WHERE id=?", (canonical(messages), time.time(), turn["session_id"]))

    def events(self, tid: str, after=0, limit=500) -> list[dict]:
        with self.connection() as c:
            return [{**json.loads(r["payload"]), "event_id": r["id"]} for r in c.execute(
                "SELECT id,payload FROM turn_events WHERE turn_id=? AND id>? ORDER BY id LIMIT ?", (tid, after, limit))]

    def queued(self) -> list[dict]:
        with self.connection() as c:
            return [_row(r) for r in c.execute("SELECT * FROM turns WHERE status='queued' ORDER BY created,id")]

    def recover(self) -> list[str]:
        with self.connection() as c:
            candidates = c.execute("SELECT * FROM turns WHERE status='running'").fetchall()
        stopped = []
        for row in candidates:
            if owner_alive(row["owner"]):
                continue
            with self.connection() as c:
                c.execute("BEGIN IMMEDIATE")
                changed = c.execute("UPDATE turns SET status='recovery_required',finished=?,error=? "
                                    "WHERE id=? AND status='running' AND owner=?",
                                    (time.time(), "worker stopped; model/tool effects require reconciliation", row["id"], row["owner"]))
                if changed.rowcount:
                    self._interrupt_actions(c, row)
                    c.execute("UPDATE model_calls SET status='unresolved',finished=? WHERE turn_id=? AND status='pending'",
                              (time.time(), row["id"]))
                    self._event(c, row, {"type": "error", "message": "执行进程已停止；已有模型请求和工具操作需核对，本轮不会自动重放。"})
                    stopped.append(row["id"])
        return stopped

    def begin_action(self, tid: str, model_call_id: str, call: dict) -> dict:
        fingerprint = digest({"name": call["name"], "arguments": call["arguments"]})
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            turn = c.execute("SELECT * FROM turns WHERE id=? AND status='running' AND owner=?", (tid, OWNER)).fetchone()
            if not turn:
                raise TurnConflict("turn is not owned by this worker")
            old = c.execute("SELECT * FROM actions WHERE turn_id=? AND model_call_id=? AND call_id=?",
                            (tid, model_call_id, call["id"])).fetchone()
            if old:
                if old["request_hash"] != fingerprint:
                    raise TurnConflict("tool call id was reused with different arguments")
                return _row(old)
            if c.execute("SELECT COUNT(*) FROM actions WHERE turn_id=?", (tid,)).fetchone()[0] >= turn["max_actions"]:
                raise TurnConflict("turn tool-action budget exhausted")
            aid = uuid.uuid4().hex
            c.execute("INSERT INTO actions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (aid, turn["session_id"], tid, model_call_id, call["id"], call["name"], fingerprint,
                       canonical(redact(call["arguments"])), "accepted", time.time(), None, None, None, None))
            return _row(c.execute("SELECT * FROM actions WHERE id=?", (aid,)).fetchone())

    def start_action(self, aid: str) -> bool:
        with self.connection() as c:
            changed = c.execute("UPDATE actions SET status='executing',started=? WHERE id=? AND status='accepted' "
                                "AND EXISTS (SELECT 1 FROM turns WHERE turns.id=actions.turn_id "
                                "AND turns.status='running' AND turns.owner=?)", (time.time(), aid, OWNER))
            return changed.rowcount == 1

    def complete_action(self, aid: str, result, output_ref: dict, messages: list[dict], title: str,
                        *, job_id: str | None = None) -> None:
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM actions WHERE id=? AND status='executing' "
                            "AND EXISTS (SELECT 1 FROM turns WHERE turns.id=actions.turn_id "
                            "AND turns.status='running' AND turns.owner=?)", (aid, OWNER)).fetchone()
            if not row:
                raise TurnConflict("action is not executing")
            c.execute("UPDATE actions SET status='complete',finished=?,result=?,output_ref=? WHERE id=?",
                      (time.time(), canonical(result), canonical(output_ref), aid))
            c.execute("UPDATE sessions SET messages=?,updated=?,title=COALESCE(NULLIF(title,''),?) WHERE id=?",
                      (canonical(messages), time.time(), title, row["session_id"]))
            if job_id:
                self._link_job(c, row["session_id"], row["turn_id"], job_id, aid)

    def actions(self, tid: str) -> list[dict]:
        with self.connection() as c:
            return [_row(r) for r in c.execute("SELECT * FROM actions WHERE turn_id=? ORDER BY created,id", (tid,))]

    def link_job(self, tid: str, job_id: str, action_id: str | None = None) -> None:
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT session_id FROM turns WHERE id=?", (tid,)).fetchone()
            if not row:
                raise ValueError("unknown turn")
            self._link_job(c, row[0], tid, job_id, action_id)

    @staticmethod
    def _link_job(c, sid: str, tid: str, job_id: str, action_id: str | None = None) -> None:
        c.execute("INSERT OR IGNORE INTO job_links VALUES (?,?,?,?,?)", (sid, tid, action_id, job_id, time.time()))
        c.execute("UPDATE turns SET job_id=? WHERE id=?", (job_id, tid))

    def latest_job(self, sid: str) -> str | None:
        with self.connection() as c:
            row = c.execute("SELECT job_id FROM job_links WHERE session_id=? ORDER BY created DESC LIMIT 1", (sid,)).fetchone()
            return row[0] if row else None

    def linked_jobs(self, sid: str) -> list[str]:
        with self.connection() as c:
            return [r[0] for r in c.execute("SELECT job_id FROM job_links WHERE session_id=? "
                                           "GROUP BY job_id ORDER BY MAX(created)", (sid,))]

    def context_state(self, tid: str):
        from .context_state import ContextState
        turn = self.get(tid)
        if not turn:
            raise ValueError("unknown turn")
        approvals = ()
        grant_id = turn["payload"].get("grant_id")
        if grant_id:
            from . import repairs
            approvals = (repairs.context_for_turn(self.path, grant_id, turn["session_id"]),)
        with self.connection() as c:
            pending = [_row(r) for r in c.execute("SELECT * FROM actions WHERE session_id=? "
                       "AND status IN ('accepted','executing','recovery_required') ORDER BY created,id", (turn["session_id"],))]
            parent = None if turn["kind"] == "user" else c.execute(
                "SELECT turns.id,turns.payload FROM turns JOIN job_links ON turns.id=job_links.turn_id "
                "WHERE turns.session_id=? AND job_links.job_id=? AND turns.kind='user' ORDER BY turns.created LIMIT 1",
                (turn["session_id"], turn["job_id"])).fetchone()
        return ContextState(
            goal=json.loads(parent["payload"])["message"] if parent else turn["payload"]["message"],
            constraints=("This turn is a read-only job review; proposals require later user authorization.",)
            if turn["kind"] == "job_review" else (),
            pending_actions=tuple({k: a[k] for k in ("id", "tool_name", "arguments", "status")}
                                  for a in pending),
            approvals=approvals, active_job_id=turn["job_id"],
            source={"store": "sessions.sqlite", "turn_id": tid, "turn_kind": turn["kind"],
                    "parent_turn_id": parent["id"] if parent else None, "schema_version": 1})
