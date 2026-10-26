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
"""

# BEGIN IMMEDIATE attempts, each after busy_timeout (60 s) ran out
_LOCK_RETRIES = 5

# design.status values
PENDING, RUNNING, PASSED, FAILED, CANCELLED = "pending", "running", "passed", "failed", "cancelled"


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

    @contextlib.contextmanager
    def _conn(self, write: bool = False):
        """One connection per call, CLOSED on exit (sqlite3's own context manager
        only commits, so every call used to leak a connection until GC).

        A write is one BEGIN IMMEDIATE transaction, and taking that lock is retried
        while the file stays locked past busy_timeout: a bookkeeping write that lost
        a lock race used to raise out of the pipeline and fail the DESIGN -- a
        control design that had just signed off clean read "内部错误：database is
        locked" (2026-10-24). Once IMMEDIATE holds the lock, set_design's
        read-modify-write cannot hit the snapshot-upgrade BUSY that busy_timeout
        never retries."""
        c = sqlite3.connect(self.path, timeout=60, isolation_level=None)
        c.row_factory = sqlite3.Row
        try:
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
        finally:
            c.close()

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

    def set_job(self, jid: str, status: str, result: dict | None = None) -> None:
        with self._lock, self._conn(write=True) as c:
            c.execute("UPDATE jobs SET status=?, updated=?, result=COALESCE(?, result) WHERE id=?",
                      (status, time.time(), json.dumps(result) if result is not None else None, jid))

    def claim_job(self, jid: str, frm: tuple = ("running", "pending"), to: str = "finalizing") -> bool:
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
                out.append(j)
        return out

    # -- designs ----------------------------------------------------------
    def set_design(self, jid: str, name: str, *, stage: str | None = None, status: str | None = None,
                   detail: dict | None = None, project: str | None = None) -> None:
        with self._lock, self._conn(write=True) as c:
            row = c.execute("SELECT detail FROM designs WHERE job_id=? AND name=?", (jid, name)).fetchone()
            merged = json.loads(row["detail"]) if row and row["detail"] else {}
            if detail:
                merged.update(detail)
            c.execute("""UPDATE designs SET stage=COALESCE(?, stage), status=COALESCE(?, status),
                         detail=?, project=COALESCE(?, project), updated=? WHERE job_id=? AND name=?""",
                      (stage, status, json.dumps(merged), project, time.time(), jid, name))

    def design(self, jid: str, name: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM designs WHERE job_id=? AND name=?", (jid, name)).fetchone()
        return _row(r) if r else None

    # -- events -----------------------------------------------------------
    def event(self, jid: str, design: str | None, stage: str, kind: str, message: str,
              data: dict | None = None) -> None:
        with self._lock, self._conn(write=True) as c:
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
