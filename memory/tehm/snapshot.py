"""Task-level snapshot binding for a TEHM store (P1b, decision D3; protocol v1 section 8.2).

At task start, ``bind_snapshot`` binds ONE committed store version and gives the task a private,
immutable copy of it:

- **Atomic boundary.** The database is copied with the SQLite online-backup API in a single step, which
  yields exactly one committed state (WAL contents included; uncommitted or interrupted writes are not
  part of any committed state, so they never appear).
- **Identity.** ``snapshot.json`` records the commit watermark of that state (``tehm.db.read_watermark``),
  a ``snapshot_id`` (sha256 over the copied database bytes and the referenced artifact digests), the
  source, and the binding time.
- **Database and files agree.** Only the artifacts the snapshot's own rows reference are copied, each
  re-hashed against its content address; a missing or corrupt referenced artifact fails the binding.
  Artifacts written after the bound state are never copied.
- **Fixed view.** The task's runtime opens the copy read-only (``TEHM_DB`` / ``TEHM_ARTIFACTS_ROOT``); later
  writes, rebuilds and rule-status changes in the source never touch it. Two overlapping tasks each keep
  their own copy.

Multi-task scheduling and batch barriers belong to the growth experiment (P4), not here.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path

SNAPSHOT_FILE = "snapshot.json"


def _referenced_artifacts(conn: sqlite3.Connection) -> dict[str, str]:
    """{relative_path: "sha256:<hex>"} referenced by the snapshot's state rows."""
    out: dict[str, str] = {}
    for (raw,) in conn.execute("SELECT artifact_manifest_json FROM tehm_states"):
        try:
            manifest = json.loads(raw) if raw else {}
        except ValueError:
            manifest = {}
        for entry in (manifest or {}).values():
            if isinstance(entry, dict) and entry.get("relative_path") and entry.get("digest"):
                out[entry["relative_path"]] = entry["digest"]
    return out


def bind_snapshot(src_dir: Path | str, dst_dir: Path | str) -> dict:
    """Bind the source store's current committed version into ``dst_dir`` (which must not exist).
    Returns the snapshot record (also written to ``dst_dir/snapshot.json``)."""
    from tehm import db as tehm_db
    src_dir, dst_dir = Path(src_dir), Path(dst_dir)
    if dst_dir.exists():
        raise FileExistsError(f"snapshot destination exists: {dst_dir}")
    tmp = dst_dir.with_name(dst_dir.name + ".binding")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    try:
        # mode=ro WITHOUT immutable: the reader honours the WAL, so the latest committed state is seen. A sealed
        # store (read-only directory, journal_mode=DELETE) cannot host the -shm file a WAL reader needs; with NO
        # -wal file present the main file IS the whole committed state, so it is opened immutable instead.
        db = src_dir / "tehm.sqlite"
        sealed = not os.access(src_dir, os.W_OK) and not (src_dir / "tehm.sqlite-wal").exists()
        uri = "file:%s?mode=ro%s" % (db, "&immutable=1" if sealed else "")
        with sqlite3.connect(uri, uri=True) as src, sqlite3.connect(tmp / "tehm.sqlite") as dst:
            src.backup(dst)                                  # one step = one committed state
        with sqlite3.connect(tmp / "tehm.sqlite") as dst:
            dst.execute("PRAGMA journal_mode=DELETE")        # a single self-contained file
            watermark = tehm_db.read_watermark(dst)
            refs = _referenced_artifacts(dst)
        h = hashlib.sha256((tmp / "tehm.sqlite").read_bytes())
        for rel, digest in sorted(refs.items()):
            blob = src_dir / "artifacts" / rel
            if not blob.is_file():
                raise RuntimeError(f"snapshot inconsistent: referenced artifact missing: {rel}")
            data = blob.read_bytes()
            if "sha256:" + hashlib.sha256(data).hexdigest() != digest:
                raise RuntimeError(f"snapshot inconsistent: referenced artifact corrupt: {rel}")
            out = tmp / "artifacts" / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data)
            mf = src_dir / "artifacts" / "manifests" / (digest.split(":", 1)[1] + ".json")
            if mf.is_file():
                (tmp / "artifacts" / "manifests").mkdir(parents=True, exist_ok=True)
                shutil.copy2(mf, tmp / "artifacts" / "manifests" / mf.name)
            h.update(digest.encode())
        (tmp / "artifacts").mkdir(exist_ok=True)
        record = {"snapshot_id": "snap_" + h.hexdigest()[:24], "watermark": watermark, "sealed_source": sealed,
                  "source": str(src_dir), "artifacts": len(refs), "bound_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        (tmp / SNAPSHOT_FILE).write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
        tmp.rename(dst_dir)                                  # the snapshot appears complete or not at all
        return record
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def read_snapshot(store_dir: Path | str) -> dict | None:
    p = Path(store_dir) / SNAPSHOT_FILE
    return json.loads(p.read_text()) if p.is_file() else None


def seal_store(store_dir: Path | str) -> str:
    """Checkpoint a store into a single self-contained file (journal_mode=DELETE) and make it read-only.
    Returns the database sha256. A sealed store binds through the immutable path of ``bind_snapshot``."""
    store_dir = Path(store_dir)
    with sqlite3.connect(store_dir / "tehm.sqlite") as c:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        c.execute("PRAGMA journal_mode=DELETE")
    for p in sorted(store_dir.rglob("*"), reverse=True) + [store_dir]:
        p.chmod(p.stat().st_mode & ~0o222)
    return hashlib.sha256((store_dir / "tehm.sqlite").read_bytes()).hexdigest()
