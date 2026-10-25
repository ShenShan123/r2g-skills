"""Bounded views of durable, session-scoped tool observations.

The index lives in sessions.sqlite; files are observations, never signoff evidence.
Only runtime-generated IDs are accepted, and every read checks the stored digest.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

MAX_INLINE_CHARS = 12000
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_READ_CHARS = 4000
_SECRET_KEY = re.compile(r"(?i)^(?:api[_-]?key|authorization|password|secret|access[_-]?token|refresh[_-]?token)$")
_SECRET_TEXT = re.compile(
    r"(?i)(\b(?:authorization\s*[:=]\s*(?:bearer\s+)?|bearer\s+|"
    r"(?:api[_-]?key|password|access[_-]?token|refresh[_-]?token)\s*[:=]\s*))"
    r"[\"']?[^\s,\"'}]+"
)


def redact(value):
    if isinstance(value, dict):
        return {str(k): "[REDACTED]" if _SECRET_KEY.match(str(k)) else redact(v)
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return _SECRET_TEXT.sub(lambda m: m[1] + "[REDACTED]", value)
    return value


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class ToolOutputError(Exception):
    """No trustworthy stored observation is available."""


@dataclass(frozen=True)
class ToolContext:
    session_id: str
    outputs: "ToolOutputStore"
    history: Callable[[str, int, int, str], dict] | None = None
    turn_id: str | None = None
    action_id: str | None = None
    job_id: str | None = None
    read_only: bool = False
    grant_id: str | None = None


class ToolOutputStore:
    def __init__(self, database: Path):
        self.database = Path(database)
        self.directory = self.database.parent / "tool-results"
        with self._connect() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS tool_outputs (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL, call_id TEXT NOT NULL,
                tool_name TEXT NOT NULL, sha256 TEXT NOT NULL, original_bytes INTEGER NOT NULL,
                stored_bytes INTEGER NOT NULL, redacted INTEGER NOT NULL, created REAL NOT NULL
            )""")
            c.execute("CREATE INDEX IF NOT EXISTS tool_outputs_session ON tool_outputs(session_id, id)")

    @contextmanager
    def _connect(self):
        c = sqlite3.connect(self.database, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def record(self, session_id: str, call_id: str, tool_name: str, result) -> tuple[object, dict]:
        raw = _json(result)
        original_bytes = len(raw.encode("utf-8"))
        if original_bytes > MAX_OUTPUT_BYTES:
            raise ToolOutputError("tool output exceeds the 8 MiB storage limit")
        # Normalize before redaction so custom objects cannot bypass string filtering.
        clean = redact(json.loads(raw))
        text = _json(clean)
        data = text.encode("utf-8")
        if len(data) > MAX_OUTPUT_BYTES:
            raise ToolOutputError("redacted tool output exceeds the storage limit")
        with self._connect() as c:
            if not c.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone():
                raise ToolOutputError("unknown session")
        artifact_id = uuid.uuid4().hex
        digest = hashlib.sha256(data).hexdigest()
        temporary = None
        target = self.directory / (artifact_id + ".json")
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self.directory.is_symlink():
                raise ToolOutputError("tool output directory must not be a symlink")
            fd, temporary = tempfile.mkstemp(dir=self.directory, prefix=".pending-")
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, target)
            temporary = None
            directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            with self._connect() as c:
                c.execute("INSERT INTO tool_outputs VALUES (?,?,?,?,?,?,?,?,?)",
                          (artifact_id, session_id, call_id, tool_name, digest,
                           original_bytes, len(data), int(raw != text), time.time()))
        except (OSError, sqlite3.Error) as e:
            target.unlink(missing_ok=True)
            raise ToolOutputError("could not persist tool output: " + type(e).__name__) from e
        finally:
            if temporary:
                Path(temporary).unlink(missing_ok=True)
        ref = {"schema_version": 1, "artifact_id": artifact_id, "sha256": digest,
               "original_bytes": original_bytes, "stored_bytes": len(data),
               "redacted": raw != text}
        if len(text) <= MAX_INLINE_CHARS:
            return clean, ref
        # Reserve space for JSON escaping, metadata and a useful tail, not just the head.
        keep = MAX_INLINE_CHARS // 4
        while True:
            preview = {"truncated": True, "output_ref": ref,
                       "head": text[:keep], "tail": text[-keep:],
                       "omitted_chars": len(text) - 2 * keep,
                       "read_with": "read_tool_output(artifact_id, offset, limit, query)"}
            # Task identity must survive clipping a large submission receipt.
            # Chat progress and the next request's runtime context use this ID.
            if isinstance(clean, dict) and isinstance(clean.get("job_id"), str) and re.fullmatch(
                    r"[a-zA-Z0-9_-]{1,100}", clean["job_id"]):
                preview["job_id"] = clean["job_id"]
            if isinstance(clean, dict) and "error" in clean:
                preview["error"] = str(clean["error"])[:256]
            if len(_json(preview)) <= MAX_INLINE_CHARS:
                return preview, ref
            keep //= 2

    def read(self, session_id: str, artifact_id: str, offset: int = 0,
             limit: int = MAX_READ_CHARS, query: str = "") -> dict:
        if not isinstance(artifact_id, str) or not re.fullmatch(r"[0-9a-f]{32}", artifact_id):
            raise ToolOutputError("invalid tool output ID")
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= MAX_READ_CHARS:
            raise ToolOutputError("offset must be nonnegative and limit must be between 1 and 4000")
        if not isinstance(query, str) or len(query) > 256:
            raise ToolOutputError("query must be a string of at most 256 characters")
        with self._connect() as c:
            row = c.execute("SELECT * FROM tool_outputs WHERE id=? AND session_id=?",
                            (artifact_id, session_id)).fetchone()
        if row is None:
            raise ToolOutputError("tool output not found in this session")
        try:
            if self.directory.is_symlink():
                raise ToolOutputError("tool output directory must not be a symlink")
            fd = os.open(self.directory / (artifact_id + ".json"),
                         os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as f:
                if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                    raise ToolOutputError("tool output is not a regular file")
                data = f.read(MAX_OUTPUT_BYTES + 1)
        except OSError as e:
            raise ToolOutputError("tool output file unavailable") from e
        if len(data) != row["stored_bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ToolOutputError("tool output integrity check failed")
        text = data.decode("utf-8")
        match = None
        if query:
            match = text.find(query, offset)
            if match >= 0:
                offset = match
        piece = "" if match == -1 else text[offset:offset + limit]
        end = offset + len(piece)
        return {"artifact_id": artifact_id, "sha256": row["sha256"], "text": piece,
                "offset": offset, "next_offset": end if end < len(text) and match != -1 else None,
                "total_chars": len(text), "stored_bytes": row["stored_bytes"],
                "redacted": bool(row["redacted"]), "match": match}
