"""Job store: jobs, per-design state and an append-only event stream (SQLite).

A job is one batch request ("these RTL folders, nangate45, output graph").
Each design in it moves through the pipeline stages; every transition and every
tool outcome is an event, which is what the UI's job board and the agent's
`job_status` tool read. Nothing here decides a verdict -- it only records what
the pipeline's deterministic checks reported.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .paths import r2g_home

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, created REAL, updated REAL, status TEXT,
  title TEXT, spec TEXT, result TEXT
);
CREATE TABLE IF NOT EXISTS designs (
  job_id TEXT, name TEXT, project TEXT, stage TEXT, status TEXT,
  detail TEXT, updated REAL, PRIMARY KEY (job_id, name)
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, design TEXT, ts REAL,
  stage TEXT, kind TEXT, message TEXT, data TEXT
);
CREATE INDEX IF NOT EXISTS ev_job ON events (job_id, id);
CREATE TABLE IF NOT EXISTS stage_runs (
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL, design TEXT NOT NULL, stage TEXT NOT NULL,
  owner TEXT NOT NULL, boot_id TEXT NOT NULL, status TEXT NOT NULL,
  command TEXT NOT NULL, pgid INTEGER, process_start TEXT,
  created REAL NOT NULL, started REAL, finished REAL, exit_code INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS stage_runs_active ON stage_runs(job_id, design)
  WHERE status IN ('pending', 'running', 'recovery_required');
CREATE TABLE IF NOT EXISTS runtime_schema (component TEXT PRIMARY KEY, version INTEGER NOT NULL);
"""

# design.status values
PENDING, RUNNING, PASSED, FAILED, CANCELLED = "pending", "running", "passed", "failed", "cancelled"
CANCELLING, RECOVERY_REQUIRED = "cancelling", "recovery_required"
TERMINAL_JOBS = ("done", FAILED, CANCELLED)
TERMINAL_DESIGNS = (PASSED, FAILED, CANCELLED)


class JobStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or r2g_home() / "jobs.sqlite")
        self._lock = threading.Lock()
        with self._conn() as c:
            # WAL is a property of the database FILE: set it once here. Setting it
            # on every connection needs an exclusive lock and, with several
            # pipeline threads writing on a loaded machine, raised "database is
            # locked" -- which killed the job watcher and left a finished job
            # "running" with no dataset (2026-10-22).
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)
            c.execute("BEGIN IMMEDIATE")
            columns = {r[1] for r in c.execute("PRAGMA table_info(jobs)")}
            for name in ("cancel_requested_at", "cancelled_at"):
                if name not in columns:
                    c.execute("ALTER TABLE jobs ADD COLUMN %s REAL" % name)
            c.execute("INSERT OR REPLACE INTO runtime_schema VALUES ('execution', 1)")

    @contextmanager
    def _conn(self):
        c = sqlite3.connect(self.path, timeout=60)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=60000")
        try:
            with c:
                yield c
        finally:
            c.close()

    # -- jobs -------------------------------------------------------------
    def create_job(self, title: str, spec: dict, designs: list[dict]) -> str:
        jid = time.strftime("%m%d%H%M") + "-" + uuid.uuid4().hex[:6]
        now = time.time()
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO jobs (id,created,updated,status,title,spec,result) VALUES (?,?,?,?,?,?,?)",
                      (jid, now, now, PENDING, title, json.dumps(spec), None))
            for d in designs:
                c.execute("INSERT INTO designs VALUES (?,?,?,?,?,?,?)",
                          (jid, d["name"], d.get("project", ""), "queued", PENDING,
                           json.dumps(d.get("detail", {})), now))
            self._event(c, jid, None, "job", "created", "任务已创建：%d 个设计" % len(designs))
        return jid

    def set_job(self, jid: str, status: str, result: dict | None = None, *, event=None) -> bool:
        with self._lock, self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT status,cancel_requested_at FROM jobs WHERE id=?", (jid,)).fetchone()
            if not row or row["status"] in TERMINAL_JOBS:
                return False
            if row["cancel_requested_at"]:
                if status in TERMINAL_JOBS:
                    status = CANCELLED
                elif status == RUNNING:
                    status = CANCELLING
            c.execute("UPDATE jobs SET status=?, updated=?, result=COALESCE(?, result) WHERE id=?",
                      (status, time.time(), json.dumps(result) if result is not None else None, jid))
            if status == CANCELLED:
                c.execute("UPDATE jobs SET cancelled_at=? WHERE id=?", (time.time(), jid))
            if event:
                self._event(c, jid, None, *event)
            return True

    def request_cancel(self, jid: str) -> dict | None:
        with self._lock, self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT status,cancel_requested_at,cancelled_at FROM jobs WHERE id=?", (jid,)).fetchone()
            if not row:
                return None
            if row["status"] not in TERMINAL_JOBS and not row["cancel_requested_at"]:
                now = time.time()
                # Keep finalizing/recovery_required: neither means confirmed termination.
                status = row["status"] if row["status"] in ("finalizing", RECOVERY_REQUIRED) else CANCELLING
                c.execute("UPDATE jobs SET cancel_requested_at=?,status=?,updated=? WHERE id=?",
                          (now, status, now, jid))
                self._event(c, jid, None, "job", "cancel_requested", "已请求取消，等待执行器确认停止")
            return dict(c.execute("SELECT status,cancel_requested_at,cancelled_at FROM jobs WHERE id=?", (jid,)).fetchone())

    def cancel_requested(self, jid: str) -> bool:
        with self._conn() as c:
            row = c.execute("SELECT cancel_requested_at FROM jobs WHERE id=?", (jid,)).fetchone()
            return bool(row and row[0])

    def claim_job(self, jid: str, frm: tuple = ("running", "pending", "cancelling"), to: str = "finalizing") -> bool:
        """Atomically move a job from one of `frm` to `to`. True only for the one
        caller that made the change -- so two processes never both close a job."""
        frm = tuple(s for s in frm if s not in TERMINAL_JOBS)
        if not frm:
            return False
        with self._lock, self._conn() as c:
            cur = c.execute("UPDATE jobs SET status=?, updated=? WHERE id=? AND status IN (%s)"
                            % ",".join("?" * len(frm)), (to, time.time(), jid, *frm))
            return cur.rowcount == 1

    def claim_design(self, jid: str, name: str, old_owner: str | None, new_owner: str) -> bool:
        """Adopt a design whose owner is `old_owner`; True only for the one caller
        that changed it (two servers on one R2G_HOME never both resume it)."""
        with self._lock, self._conn() as c:
            cur = c.execute("UPDATE designs SET detail=json_set(COALESCE(detail,'{}'), '$.owner', ?), updated=? "
                            "WHERE job_id=? AND name=? AND status IN ('pending','running','recovery_required') "
                            "AND json_extract(COALESCE(detail,'{}'), '$.owner') IS ?",
                            (new_owner, time.time(), jid, name, old_owner))
            return cur.rowcount == 1

    def job(self, jid: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
            if not r:
                return None
            ds = c.execute("SELECT * FROM designs WHERE job_id=? ORDER BY rowid", (jid,)).fetchall()
        out = _row(r)
        out["designs"] = [_row(d) for d in ds]
        return out

    def jobs(self, limit: int = 50) -> list[dict]:
        with self._conn() as c:
            rs = c.execute("SELECT * FROM jobs ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
            out = []
            for r in rs:
                j = _row(r)
                counts = c.execute("SELECT status, COUNT(*) n FROM designs WHERE job_id=? GROUP BY status",
                                   (r["id"],)).fetchall()
                j["counts"] = {x["status"]: x["n"] for x in counts}
                out.append(j)
        return out

    # -- designs ----------------------------------------------------------
    def set_design(self, jid: str, name: str, *, stage: str | None = None, status: str | None = None,
                   detail: dict | None = None, project: str | None = None,
                   expected_owner: str | None = None, event=None) -> bool:
        with self._lock, self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT detail,status FROM designs WHERE job_id=? AND name=?", (jid, name)).fetchone()
            if not row or row["status"] in TERMINAL_DESIGNS:
                return False
            merged = json.loads(row["detail"]) if row and row["detail"] else {}
            if expected_owner is not None and merged.get("owner") != expected_owner:
                return False
            job = c.execute("SELECT status,cancel_requested_at FROM jobs WHERE id=?", (jid,)).fetchone()
            if not job or job["status"] in TERMINAL_JOBS or (status == PASSED and job["cancel_requested_at"]):
                return False
            if detail:
                merged.update(detail)
            c.execute("""UPDATE designs SET stage=COALESCE(?, stage), status=COALESCE(?, status),
                         detail=?, project=COALESCE(?, project), updated=? WHERE job_id=? AND name=?""",
                      (stage, status, json.dumps(merged), project, time.time(), jid, name))
            if event:
                self._event(c, jid, name, *event)
            return True

    def design(self, jid: str, name: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM designs WHERE job_id=? AND name=?", (jid, name)).fetchone()
        return _row(r) if r else None

    # -- events -----------------------------------------------------------
    @staticmethod
    def _event(c, jid, design, stage, kind, message, data=None):
        c.execute("INSERT INTO events (job_id,design,ts,stage,kind,message,data) VALUES (?,?,?,?,?,?,?)",
                  (jid, design, time.time(), stage, kind, message, json.dumps(data or {})))
        c.execute("UPDATE jobs SET updated=? WHERE id=?", (time.time(), jid))

    def event(self, jid: str, design: str | None, stage: str, kind: str, message: str,
              data: dict | None = None, *, expected_owner: str | None = None) -> bool:
        with self._lock, self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            if expected_owner is not None:
                row = c.execute("SELECT json_extract(detail,'$.owner') FROM designs WHERE job_id=? AND name=?",
                                (jid, design)).fetchone()
                if not row or row[0] != expected_owner:
                    return False
            self._event(c, jid, design, stage, kind, message, data)
            return True

    def begin_execution(self, jid, name, stage, owner, boot_id, command) -> str | None:
        """Commit intent before spawning. Missing handles never prove non-execution."""
        with self._lock, self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT d.detail,d.status AS design_status,j.cancel_requested_at,j.status FROM designs d JOIN jobs j "
                            "ON j.id=d.job_id WHERE d.job_id=? AND d.name=?", (jid, name)).fetchone()
            if not row or json.loads(row["detail"] or '{}').get('owner') != owner \
                    or row['design_status'] in (*TERMINAL_DESIGNS, RECOVERY_REQUIRED) \
                    or row['cancel_requested_at'] or row['status'] in (*TERMINAL_JOBS, 'finalizing'):
                return None
            invocation = uuid.uuid4().hex
            c.execute("INSERT INTO stage_runs (id,job_id,design,stage,owner,boot_id,status,command,created) "
                      "VALUES (?,?,?,?,?,?,'pending',?,?)",
                      (invocation, jid, name, stage, owner, boot_id, json.dumps(command), time.time()))
            c.execute("UPDATE designs SET detail=json_set(detail,'$.execution_id',?) WHERE job_id=? AND name=?",
                      (invocation, jid, name))
            self._event(c, jid, name, stage, "execution_pending", "阶段执行已登记", {"execution_id": invocation})
            return invocation

    def execution(self, invocation: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM stage_runs WHERE id=?", (invocation,)).fetchone()
            return dict(row) if row else None

    def active_executions(self, jid: str) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM stage_runs WHERE job_id=? "
                                               "AND status IN ('pending','running','recovery_required')", (jid,))]

    def update_execution(self, invocation, owner, status, *, pgid=None, process_start=None, exit_code=None) -> bool:
        with self._lock, self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT s.* FROM stage_runs s JOIN designs d ON d.job_id=s.job_id AND d.name=s.design "
                            "WHERE s.id=? AND json_extract(d.detail,'$.owner')=?", (invocation, owner)).fetchone()
            if not row or row['status'] not in ('pending','running','recovery_required'):
                return False
            c.execute("UPDATE stage_runs SET status=?,pgid=COALESCE(?,pgid),process_start=COALESCE(?,process_start),"
                      "started=CASE WHEN ?='running' THEN ? ELSE started END,"
                      "finished=CASE WHEN ? IN ('exited','not_started','interrupted') THEN ? ELSE finished END,"
                      "exit_code=? WHERE id=?",
                      (status, pgid, process_start, status, time.time(), status, time.time(), exit_code, invocation))
            self._event(c, row['job_id'], row['design'], row['stage'], "execution_" + status,
                        "阶段执行状态：" + status, {"execution_id": invocation, "exit_code": exit_code})
            return True

    def events(self, jid: str, after: int = 0, limit: int = 500, tail: int = 0) -> list[dict]:
        """Events after id `after`; with `tail`, only the last `tail` of them (a
        2000-design job has tens of thousands, and a page wants the recent ones)."""
        with self._conn() as c:
            if tail:
                rs = c.execute("SELECT * FROM (SELECT * FROM events WHERE job_id=? AND id>? ORDER BY id DESC LIMIT ?) "
                               "ORDER BY id", (jid, after, tail)).fetchall()
            else:
                rs = c.execute("SELECT * FROM events WHERE job_id=? AND id>? ORDER BY id LIMIT ?",
                               (jid, after, limit)).fetchall()
        return [_row(r) for r in rs]


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in ("spec", "result", "detail", "data"):
        if k in d and isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except ValueError:
                pass
    return d
