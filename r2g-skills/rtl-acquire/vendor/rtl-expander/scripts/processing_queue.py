#!/usr/bin/env python3
"""Mutable, rebuildable operational queue for revision-local RTL processing."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable


QUEUE_SCHEMA = "rtl_processing_queue_v1"
TERMINAL_REPOSITORY_STATES = {"NO_RTL", "NO_DESIGN", "SYNTH_VALID", "DESIGN_RECOVERED"}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


class ProcessingQueue:
    """Operational state only; authoritative processing facts live in the ledger."""

    def __init__(self, corpus: Path):
        self.corpus = corpus
        self.path = corpus / "state" / "corpus.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=60)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.executescript("""
        CREATE TABLE IF NOT EXISTS processing_queue(
          round_id TEXT NOT NULL,
          repository_revision_key TEXT NOT NULL,
          source_path TEXT NOT NULL,
          acquired_at TEXT,
          state TEXT NOT NULL,
          attempts INTEGER NOT NULL DEFAULT 0,
          claimed_by TEXT,
          claim_started_at TEXT,
          processing_started_at TEXT,
          terminal_at TEXT,
          terminal_state TEXT,
          run_key TEXT,
          artifact_path TEXT,
          artifact_sha256 TEXT,
          artifact_size INTEGER,
          error_detail TEXT,
          enqueued_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          PRIMARY KEY(round_id,repository_revision_key)
        );
        CREATE INDEX IF NOT EXISTS processing_queue_claim_idx
          ON processing_queue(round_id,state,enqueued_at,repository_revision_key);
        """)
        columns = {
            str(row[1]) for row in self.connection.execute("PRAGMA table_info(processing_queue)")
        }
        for name, declaration in (
            ("artifact_sha256", "TEXT"), ("artifact_size", "INTEGER"),
        ):
            if name not in columns:
                self.connection.execute(
                    f"ALTER TABLE processing_queue ADD COLUMN {name} {declaration}"
                )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "ProcessingQueue":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def enqueue(
        self, round_id: str, revision_key: str, source_path: str,
        acquired_at: str | None = None,
    ) -> bool:
        now = utc_now()
        with self.connection:
            changed = self.connection.execute(
                """INSERT OR IGNORE INTO processing_queue(
                     round_id,repository_revision_key,source_path,acquired_at,state,
                     enqueued_at,updated_at) VALUES(?,?,?,?,?,?,?)""",
                (round_id, revision_key, source_path, acquired_at, "QUEUED", now, now),
            ).rowcount
        return bool(changed)

    def reconcile_frontier(
        self, round_id: str, frontier_path: Path, excluded_revision_keys: set[str],
    ) -> list[dict[str, Any]]:
        frontier = sqlite3.connect(f"file:{frontier_path}?mode=ro", uri=True)
        frontier.row_factory = sqlite3.Row
        try:
            rows = frontier.execute(
                """SELECT repository_revision_key,source_path,acquired_at
                   FROM repository_revisions ORDER BY acquired_at,repository_revision_key"""
            ).fetchall()
        finally:
            frontier.close()
        inserted: list[dict[str, Any]] = []
        for row in rows:
            key = str(row["repository_revision_key"])
            if key in excluded_revision_keys:
                continue
            if self.enqueue(round_id, key, str(row["source_path"]), row["acquired_at"]):
                inserted.append(dict(row))
        return inserted

    def requeue_abandoned(self, round_id: str) -> int:
        with self.connection:
            return self.connection.execute(
                """UPDATE processing_queue SET state='RETRY',claimed_by=NULL,
                   claim_started_at=NULL,updated_at=?
                   WHERE round_id=? AND state='PROCESSING'""",
                (utc_now(), round_id),
            ).rowcount

    def claim(self, round_id: str, worker_id: str, max_attempts: int = 3) -> dict[str, Any] | None:
        now = utc_now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                """SELECT * FROM processing_queue
                   WHERE round_id=? AND state IN ('QUEUED','RETRY') AND attempts<?
                   ORDER BY enqueued_at,repository_revision_key LIMIT 1""",
                (round_id, max_attempts),
            ).fetchone()
            if row is None:
                self.connection.commit()
                return None
            changed = self.connection.execute(
                """UPDATE processing_queue SET state='PROCESSING',attempts=attempts+1,
                   claimed_by=?,claim_started_at=?,processing_started_at=COALESCE(processing_started_at,?),
                   updated_at=? WHERE round_id=? AND repository_revision_key=?
                   AND state IN ('QUEUED','RETRY')""",
                (worker_id, now, now, now, round_id, row["repository_revision_key"]),
            ).rowcount
            self.connection.commit()
            if not changed:
                return None
            return dict(self.connection.execute(
                "SELECT * FROM processing_queue WHERE round_id=? AND repository_revision_key=?",
                (round_id, row["repository_revision_key"]),
            ).fetchone())
        except Exception:
            self.connection.rollback()
            raise

    def finish(
        self, round_id: str, revision_key: str, *, terminal_state: str,
        run_key: str | None, artifact_path: str | None,
        artifact_sha256: str | None = None, artifact_size: int | None = None,
    ) -> dict[str, Any]:
        if terminal_state not in TERMINAL_REPOSITORY_STATES:
            raise ValueError(f"nonterminal repository state: {terminal_state}")
        artifact = Path(artifact_path) if artifact_path else None
        if artifact is not None and artifact.is_file() and artifact_sha256 is None:
            digest = hashlib.sha256()
            size = 0
            with artifact.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
                    size += len(chunk)
            artifact_sha256 = digest.hexdigest()
            artifact_size = size
        if artifact_sha256 is not None and not re.fullmatch(
            r"[0-9a-f]{64}", artifact_sha256.lower()
        ):
            raise ValueError("invalid staging artifact admission digest")
        if artifact_sha256 is not None and (artifact_size is None or artifact_size < 0):
            raise ValueError("staging artifact admission digest requires byte size")
        now = utc_now()
        with self.connection:
            self.connection.execute(
                """UPDATE processing_queue SET state='TERMINAL',terminal_at=?,terminal_state=?,
                   run_key=?,artifact_path=?,artifact_sha256=?,artifact_size=?,
                   error_detail=NULL,claimed_by=NULL,
                   claim_started_at=NULL,updated_at=?
                   WHERE round_id=? AND repository_revision_key=?""",
                (now, terminal_state, run_key, artifact_path, artifact_sha256,
                 artifact_size, now, round_id, revision_key),
            )
        return {
            "artifact_sha256": artifact_sha256,
            "artifact_size": artifact_size,
            "rehash_required": False if artifact_sha256 is not None else True,
        }

    def fail(self, round_id: str, revision_key: str, detail: str, max_attempts: int = 3) -> None:
        now = utc_now()
        with self.connection:
            row = self.connection.execute(
                "SELECT attempts FROM processing_queue WHERE round_id=? AND repository_revision_key=?",
                (round_id, revision_key),
            ).fetchone()
            state = "BLOCKED" if row and int(row[0]) >= max_attempts else "RETRY"
            self.connection.execute(
                """UPDATE processing_queue SET state=?,error_detail=?,claimed_by=NULL,
                   claim_started_at=NULL,updated_at=?
                   WHERE round_id=? AND repository_revision_key=?""",
                (state, detail[-4000:], now, round_id, revision_key),
            )

    def rows(self, round_id: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM processing_queue WHERE round_id=? ORDER BY repository_revision_key",
            (round_id,),
        )]

    def terminal_keys(self, round_id: str) -> set[str]:
        return {str(row[0]) for row in self.connection.execute(
            """SELECT repository_revision_key FROM processing_queue
               WHERE round_id=? AND state='TERMINAL'""",
            (round_id,),
        )}

    def counts(self, round_id: str) -> dict[str, int]:
        values = {str(row[0]): int(row[1]) for row in self.connection.execute(
            "SELECT state,COUNT(*) FROM processing_queue WHERE round_id=? GROUP BY state",
            (round_id,),
        )}
        return {"total": sum(values.values()), **values}

    def waiting_depth(self, round_id: str) -> int:
        return int(self.connection.execute(
            """SELECT COUNT(*) FROM processing_queue
               WHERE round_id=? AND state IN ('QUEUED','RETRY','PROCESSING')""",
            (round_id,),
        ).fetchone()[0])

    def timing_seconds(self, round_id: str) -> list[float]:
        values = []
        for row in self.connection.execute(
            """SELECT enqueued_at,terminal_at FROM processing_queue
               WHERE round_id=? AND state='TERMINAL' AND terminal_at IS NOT NULL""",
            (round_id,),
        ):
            start = dt.datetime.fromisoformat(str(row[0]))
            end = dt.datetime.fromisoformat(str(row[1]))
            values.append(max(0.0, (end - start).total_seconds()))
        return values

    def lifecycle_counts(self, round_id: str) -> dict[str, int]:
        """Return monotonic processing counters independent of the current queue state."""
        row = self.connection.execute(
            """SELECT COUNT(*) AS acquired,
                      SUM(CASE WHEN processing_started_at IS NOT NULL THEN 1 ELSE 0 END) AS started,
                      SUM(CASE WHEN terminal_at IS NOT NULL THEN 1 ELSE 0 END) AS terminal
               FROM processing_queue WHERE round_id=?""",
            (round_id,),
        ).fetchone()
        return {
            "acquired": int(row[0] or 0),
            "started": int(row[1] or 0),
            "terminal": int(row[2] or 0),
        }


def exact_terminal_set(queue: ProcessingQueue, round_id: str, cohort_keys: Iterable[str]) -> bool:
    return queue.terminal_keys(round_id) == set(cohort_keys)
