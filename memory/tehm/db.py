"""TEHM store connection + schema management.

Follows the legacy ``knowledge_db`` discipline (WAL, busy_timeout, foreign_keys)
but opens ONLY the TEHM store. ``ensure_schema`` is idempotent and version-gated:
the DB's recorded ``schema_version`` must match ``DB_SCHEMA_VERSION`` or pending
migrations are applied (``migrations.py``).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tehm import config, migrations


def connect(db_path: Path) -> sqlite3.Connection:
    """Open the TEHM store (WAL + busy_timeout + foreign_keys). Fail-closed."""
    db_path = Path(db_path)
    config.validate_backend_lock(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def connect_read_only(db_path: Path) -> sqlite3.Connection:
    """Open an existing frozen TEHM snapshot without any filesystem writes."""
    db_path = Path(db_path)
    config.validate_backend_lock(db_path)
    if not db_path.is_file():
        raise FileNotFoundError(f"read-only TEHM snapshot not found: {db_path}")
    # ``immutable=1`` is important for evidence freezes: SQLite must not
    # create ``-wal``/``-shm`` sidecars while auditing a supposedly read-only
    # bundle, because those sidecars would invalidate its deterministic file
    # membership and could be mistaken for mutation.
    conn = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    recorded = _meta_get(conn, "schema_version", None) if _table_exists(
        conn, "tehm_meta") else None
    expected = f"tehm-v{config.DB_SCHEMA_VERSION}"
    if recorded != expected:
        conn.close()
        raise RuntimeError(
            f"read-only TEHM schema mismatch: DB is {recorded!r}, code wants {expected}")
    return conn


def ensure_schema(conn: sqlite3.Connection, schema_path: Path | None = None) -> None:
    """Create (if absent) and migrate the TEHM schema to the pinned version.

    Idempotent. Applies ``schema.sql`` on first open, then any pending entries
    from the migration registry (``migrations.py``). Records the applied schema
    version in ``tehm_meta``.
    """
    schema_path = schema_path or config.SCHEMA_PATH
    # A fresh DB has no tehm_meta yet; create the whole schema, else read the
    # recorded version and migrate forward.
    if not _table_exists(conn, "tehm_meta"):
        conn.executescript(schema_path.read_text())
        conn.execute("INSERT OR REPLACE INTO tehm_meta(key, value) VALUES ('schema_version', ?)",
                     (f"tehm-v{config.DB_SCHEMA_VERSION}",))
        conn.commit()
        recorded = f"tehm-v{config.DB_SCHEMA_VERSION}"
    else:
        recorded = _meta_get(conn, "schema_version", None)

    pending = migrations.pending_migrations(recorded)
    for entry in pending:
        entry.apply(conn)
        conn.execute("UPDATE tehm_meta SET value=? WHERE key='schema_version'",
                     (entry.to_version,))
        conn.commit()

    recorded_after = _meta_get(conn, "schema_version", None)
    if recorded_after != f"tehm-v{config.DB_SCHEMA_VERSION}":
        raise RuntimeError(
            f"TEHM schema mismatch: DB is {recorded_after!r}, code wants "
            f"tehm-v{config.DB_SCHEMA_VERSION}"
        )


def count_rows(conn: sqlite3.Connection, table: str) -> int:
    row = conn.execute(f'SELECT COUNT(*) AS n FROM "{table}"').fetchone()
    return int(row["n"]) if row else 0


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _meta_get(conn: sqlite3.Connection, key: str, default: str | None) -> str | None:
    row = conn.execute("SELECT value FROM tehm_meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def now_local() -> str:
    """The single canonical timestamp stamp for TEHM (mirrors knowledge_db)."""
    import datetime

    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(text: str | None, default: dict | None = None) -> dict:
    if not text:
        return default if default is not None else {}
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return default if default is not None else {}
