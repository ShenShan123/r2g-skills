"""Job store: jobs, per-design state and an append-only event stream (SQLite).

A job is one batch request ("these RTL folders, nangate45, output graph").
Each design in it moves through the pipeline stages; every transition and every
tool outcome is an event, which is what the UI's job board and the agent's
`job_status` tool read. Nothing here decides a verdict -- it only records what
the pipeline's deterministic checks reported.
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
import time
import uuid
import weakref
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
CREATE TABLE IF NOT EXISTS job_cancellations (
  job_id TEXT PRIMARY KEY, requested REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS deletions (
  kind TEXT, target TEXT, status TEXT, requested REAL, updated REAL,
  error TEXT, PRIMARY KEY(kind, target)
);
"""

# BEGIN IMMEDIATE attempts, each after busy_timeout (60 s) ran out
_LOCK_RETRIES = 5

# design.status values
PENDING, RUNNING, PASSED, FAILED, CANCELLED = "pending", "running", "passed", "failed", "cancelled"
CANCELLING = "cancelling"


class JobStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or r2g_home() / "jobs.sqlite")
        self._lock = threading.RLock()
        # Keep WAL open for the lifetime of the store. Closing the last
        # connection after every read causes repeated exclusive checkpoint /
        # recovery races under concurrent HTTP and pipeline traffic.
        self._db = sqlite3.connect(self.path, timeout=60, isolation_level=None,
                                   check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        weakref.finalize(self, self._db.close)
        with self._conn() as c:
            # WAL is a property of the database FILE: set it once here. Setting it
            # on every connection needs an exclusive lock and, with several
            # pipeline threads writing on a loaded machine, raised "database is
            # locked" -- which killed the job watcher and left a finished job
            # "running" with no dataset (2026-10-22).
            c.execute("PRAGMA journal_mode=WAL")
            # WAL commits stay atomic; sync at checkpoints rather than forcing
            # a disk flush for every progress event on a shared HDD. This has
            # the same power-loss boundary as the application's JSON receipts.
            c.execute("PRAGMA synchronous=NORMAL")
            c.executescript(SCHEMA)

    @contextlib.contextmanager
    def _conn(self, write: bool = False):
        """Serialize access to one durable, persistent connection per store.

        This avoids last-connection WAL checkpoints racing new readers on slow
        disks, and prevents threads from interleaving one write transaction.

        A write is one BEGIN IMMEDIATE transaction, and taking that lock is retried
        while the file stays locked past busy_timeout: a bookkeeping write that lost
        a lock race used to raise out of the pipeline and fail the DESIGN -- a
        control design that had just signed off clean read "内部错误：database is
        locked" (2026-10-24). Once IMMEDIATE holds the lock, set_design's
        read-modify-write cannot hit the snapshot-upgrade BUSY that busy_timeout
        never retries."""
        with self._lock:
            c = self._db
            c.execute("PRAGMA busy_timeout=60000")
            if write:
                for attempt in range(_LOCK_RETRIES):
                    try:
                        c.execute("BEGIN IMMEDIATE")
                        break
                    except sqlite3.OperationalError as ex:
                        if "locked" not in str(ex) or attempt == _LOCK_RETRIES - 1:
                            raise
                        time.sleep(2 + 3 * attempt)
                try:
                    yield c
                except BaseException:
                    c.execute("ROLLBACK")
                    raise
                c.execute("COMMIT")
            else:
                yield c

    # -- jobs -------------------------------------------------------------
    def create_job(self, title: str, spec: dict, designs: list[dict]) -> str:
        jid = time.strftime("%m%d%H%M") + "-" + uuid.uuid4().hex[:6]
        now = time.time()
        with self._lock, self._conn(write=True) as c:
            c.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?)",
                      (jid, now, now, PENDING, title, json.dumps(spec), None))
            for d in designs:
                c.execute("INSERT INTO designs VALUES (?,?,?,?,?,?,?)",
                          (jid, d["name"], d.get("project", ""), "queued", PENDING,
                           json.dumps(d.get("detail", {})), now))
        self.event(jid, None, "job", "created", "任务已创建：%d 个设计" % len(designs))
        return jid

    def deletion(self, kind: str, target: str) -> dict | None:
        with self._conn() as c:
            row = c.execute('SELECT * FROM deletions WHERE kind=? AND target=?', (kind, target)).fetchone()
        return dict(row) if row else None

    def request_deletion(self, kind: str, target: str) -> dict:
        now = time.time()
        with self._conn(write=True) as c:
            c.execute("INSERT INTO deletions VALUES (?,?,'pending',?,?,'') "
                      "ON CONFLICT(kind,target) DO UPDATE SET status=CASE WHEN status='done' THEN status ELSE 'pending' END,error='',updated=excluded.updated",
                      (kind, target, now, now))
        return self.deletion(kind, target)

    def set_deletion(self, kind: str, target: str, status: str, error: str = '') -> None:
        with self._conn(write=True) as c:
            c.execute('UPDATE deletions SET status=?,error=?,updated=? WHERE kind=? AND target=?',
                      (status, error, time.time(), kind, target))

    def pending_deletions(self) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM deletions WHERE status='pending' ORDER BY requested")]

    def owned_jobs(self, sid: str) -> list[str]:
        with self._conn() as c:
            return [r[0] for r in c.execute("SELECT id FROM jobs WHERE json_extract(spec,'$.session_id')=?", (sid,))]

    def erase_job(self, jid: str) -> None:
        """Called only after exclusive artifact lock and successful disk cleanup."""
        with self._conn(write=True) as c:
            for table in ('events', 'designs', 'job_cancellations'):
                c.execute(f'DELETE FROM {table} WHERE job_id=?', (jid,))
            c.execute('DELETE FROM jobs WHERE id=?', (jid,))

    def set_job(self, jid: str, status: str, result: dict | None = None) -> None:
        with self._lock, self._conn(write=True) as c:
            if c.execute("SELECT 1 FROM job_cancellations WHERE job_id=?", (jid,)).fetchone():
                row = c.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()
                if row and row["status"] == CANCELLED:
                    return
                status = CANCELLED if status in ("done", FAILED, CANCELLED) else CANCELLING
            c.execute("UPDATE jobs SET status=?, updated=?, result=COALESCE(?, result) WHERE id=?",
                      (status, time.time(), json.dumps(result) if result is not None else None, jid))

    def request_cancel(self, jid: str) -> dict | None:
        """Persist intent and remove queued designs immediately, even with a full pool.

        Running designs remain running until their executor confirms termination.
        This ports EDACode 13665730's persistent intent without changing the legacy
        jobs table shape while existing experiment runtimes are still using it.
        """
        with self._lock, self._conn(write=True) as c:
            row = c.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()
            if not row:
                return None
            if row["status"] not in ("done", FAILED, CANCELLED):
                now = time.time()
                first = c.execute("INSERT OR IGNORE INTO job_cancellations VALUES (?,?)", (jid, now)).rowcount
                c.execute("UPDATE jobs SET status=?,updated=? WHERE id=? AND status != 'finalizing'",
                          (CANCELLING, now, jid))
                c.execute("UPDATE designs SET status=?,updated=? WHERE job_id=? AND status=?",
                          (CANCELLED, now, jid, PENDING))
                if first:
                    c.execute("INSERT INTO events (job_id,design,ts,stage,kind,message,data) VALUES (?,NULL,?,'job','cancel',?,?)",
                              (jid, now, "已请求停止：排队设计已取消，运行中的设计正在退出", "{}"))
            status = c.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()[0]
            requested = c.execute("SELECT requested FROM job_cancellations WHERE job_id=?", (jid,)).fetchone()
            return {"status": status, "cancel_requested": bool(requested)}

    def cancel_requested(self, jid: str) -> bool:
        with self._conn() as c:
            return c.execute("SELECT 1 FROM job_cancellations WHERE job_id=?", (jid,)).fetchone() is not None

    def claim_job(self, jid: str, frm: tuple = ("running", "pending", "cancelling"), to: str = "finalizing") -> bool:
        """Atomically move a job from one of `frm` to `to`. True only for the one
        caller that made the change -- so two processes never both close a job."""
        with self._lock, self._conn(write=True) as c:
            cur = c.execute("UPDATE jobs SET status=?, updated=? WHERE id=? AND status IN (%s)"
                            % ",".join("?" * len(frm)), (to, time.time(), jid, *frm))
            return cur.rowcount == 1

    def claim_design(self, jid: str, name: str, old_owner: str | None, new_owner: str) -> bool:
        """Adopt a design whose owner is `old_owner`; True only for the one caller
        that changed it (two servers on one R2G_HOME never both resume it)."""
        with self._lock, self._conn(write=True) as c:
            cur = c.execute("UPDATE designs SET detail=json_set(COALESCE(detail,'{}'), '$.owner', ?), updated=? "
                            "WHERE job_id=? AND name=? AND json_extract(COALESCE(detail,'{}'), '$.owner') IS ?",
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
                j["dual_clean"] = c.execute(
                    "SELECT COUNT(*) FROM designs WHERE job_id=? AND json_extract(detail,'$.signoff.dual_clean')=1",
                    (r["id"],)).fetchone()[0]
                out.append(j)
        return out

    # -- designs ----------------------------------------------------------
    def set_design(self, jid: str, name: str, *, stage: str | None = None, status: str | None = None,
                   detail: dict | None = None, project: str | None = None) -> bool:
        with self._lock, self._conn(write=True) as c:
            row = c.execute("SELECT detail,status FROM designs WHERE job_id=? AND name=?", (jid, name)).fetchone()
            if row and row["status"] == CANCELLED:
                return False       # a late worker must never resurrect a cancelled queue entry
            if status not in (None, CANCELLED) and c.execute(
                    "SELECT 1 FROM job_cancellations WHERE job_id=?", (jid,)).fetchone():
                return False
            merged = json.loads(row["detail"]) if row and row["detail"] else {}
            if detail:
                merged.update(detail)
            c.execute("""UPDATE designs SET stage=COALESCE(?, stage), status=COALESCE(?, status),
                         detail=?, project=COALESCE(?, project), updated=? WHERE job_id=? AND name=?""",
                      (stage, status, json.dumps(merged), project, time.time(), jid, name))
            return True

    def design(self, jid: str, name: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM designs WHERE job_id=? AND name=?", (jid, name)).fetchone()
        return _row(r) if r else None

    # -- events -----------------------------------------------------------
    def event(self, jid: str, design: str | None, stage: str, kind: str, message: str,
              data: dict | None = None) -> None:
        with self._lock, self._conn(write=True) as c:
            if not c.execute('SELECT 1 FROM jobs WHERE id=?', (jid,)).fetchone():
                return
            c.execute("INSERT INTO events (job_id, design, ts, stage, kind, message, data) VALUES (?,?,?,?,?,?,?)",
                      (jid, design, time.time(), stage, kind, message, json.dumps(data or {})))
            c.execute("UPDATE jobs SET updated=? WHERE id=?", (time.time(), jid))

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
