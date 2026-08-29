#!/usr/bin/env python3
"""Migrate a TEHM SQLite snapshot into an isolated, read-only v4 handoff.

The source database is never opened writable.  A SQLite backup is made into a
new output path, the repository's forward-only migration chain is applied to
that copy, and canonical rows that existed in the source are compared before
and after migration.  The output is a new evidence input: source bundle and
replay digests must be recomputed after migration; this command never mutates
the source, canonical runtime, or authority state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

MEMORY_ROOT = Path(__file__).resolve().parents[1]
if str(MEMORY_ROOT) not in sys.path:
    sys.path.insert(0, str(MEMORY_ROOT))

from tehm import config, db as tehm_db  # noqa: E402
from tehm.ids import stable_dumps  # noqa: E402


VERSION = "tehm-snapshot-migration-v1"
CANONICAL_TABLES = (
    "tehm_states", "tehm_transitions", "tehm_episodes", "tehm_episode_steps",
    "tehm_dataset_membership", "tehm_views", "tehm_rules", "tehm_rule_sources",
    "tehm_activations", "tehm_rule_status", "tehm_edges", "tehm_trials",
    "tehm_physical_effects",
)
SUPPORTED_SOURCE_VERSIONS = {"tehm-v1", "tehm-v2", "tehm-v3", "tehm-v4"}


def migrate(source: Path, output: Path, *, report: Path | None = None,
            overwrite: bool = False) -> dict:
    """Create a migrated output copy and return its content-bound report."""
    source = Path(source).resolve()
    output = Path(output).resolve()
    if source == output:
        raise ValueError("source and output must be different paths")
    config.validate_backend_lock(source)
    config.validate_backend_lock(output)
    if not source.is_file():
        raise FileNotFoundError(f"TEHM source snapshot not found: {source}")
    if output.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite migration output: {output}")
    report = Path(report).resolve() if report is not None else Path(
        str(output) + ".migration.json")
    if report in (source, output):
        raise ValueError("migration report must be a distinct path")
    if report.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite migration report: {report}")
    output.parent.mkdir(parents=True, exist_ok=True)

    source_identity_before = _file_identity(source)
    source_conn = _open_read_only(source)
    try:
        source_version = _schema_version(source_conn)
        if source_version not in SUPPORTED_SOURCE_VERSIONS:
            raise ValueError(f"unsupported TEHM source schema: {source_version!r}")
        source_tables = _table_names(source_conn)
        existing_tables = [table for table in CANONICAL_TABLES
                           if table in source_tables]
        source_counts = _counts(source_conn, existing_tables)
        source_digests = _table_digests(source_conn, existing_tables)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{output.name}.", dir=str(output.parent))
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            # Backup reads the source snapshot without writing source WAL/SHM.
            destination = sqlite3.connect(str(temporary))
            try:
                source_conn.backup(destination)
            finally:
                destination.close()
        finally:
            source_conn.close()

    except Exception:
        try:
            source_conn.close()
        except Exception:  # pragma: no cover - cleanup only
            pass
        raise

    try:
        migrated_conn = tehm_db.connect(temporary)
        try:
            pre_migration_version = _schema_version(migrated_conn)
            tehm_db.ensure_schema(migrated_conn)
            post_migration_version = _schema_version(migrated_conn)
            post_tables = _table_names(migrated_conn)
            post_counts = _counts(migrated_conn, existing_tables)
            post_digests = _table_digests(migrated_conn, existing_tables)
            all_preserved = all(
                source_counts[table] == post_counts[table] and
                source_digests[table] == post_digests[table]
                for table in existing_tables)
            if not all_preserved:
                raise RuntimeError("canonical rows changed during schema migration")
            new_tables = sorted(set(post_tables) - set(source_tables))
        finally:
            migrated_conn.close()

        # The migration connection may have created zero-length WAL sidecars.
        # Remove only sidecars belonging to this disposable temporary path so
        # the handoff is a single portable SQLite file.
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(temporary) + suffix)
            if sidecar.exists():
                sidecar.unlink()
        if output.exists() and overwrite:
            output.unlink()
        output.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary, output)
    except Exception:
        _cleanup_sqlite_family(temporary)
        raise

    source_identity_after = _file_identity(source)
    if source_identity_before != source_identity_after:
        raise RuntimeError("source snapshot changed while migration was running")
    migrated_conn = tehm_db.connect_read_only(output)
    try:
        verified_version = _schema_version(migrated_conn)
        if verified_version != "tehm-v4":
            raise RuntimeError(f"migrated output schema is {verified_version!r}")
    finally:
        migrated_conn.close()

    output_identity = _file_identity(output)
    result = {
        "version": VERSION,
        "status": ("MIGRATED" if source_version != "tehm-v4"
                   else "ALREADY_CURRENT"),
        "source": str(source),
        "output": str(output),
        "source_schema_version": source_version,
        "pre_migration_schema_version": pre_migration_version,
        "output_schema_version": "tehm-v4",
        "source_identity": source_identity_before,
        "output_identity": output_identity,
        "source_unchanged": source_identity_before == source_identity_after,
        "canonical_tables_checked": existing_tables,
        "canonical_counts_before": source_counts,
        "canonical_counts_after": post_counts,
        "canonical_digests_before": source_digests,
        "canonical_digests_after": post_digests,
        "canonical_rows_preserved": all_preserved,
        "new_v4_tables": new_tables,
        "database_unchanged": False,
        "canonical_memory_mutation": "none; output copy only",
        "promotion_eligible": False,
        "replay_required": True,
        "note": ("Migration changes the output schema/file digest. Rebuild the "
                 "source freeze and replay evidence before using this handoff."),
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False,
                                 indent=2, sort_keys=True) + "\n")
    return result


def _open_read_only(path: Path) -> sqlite3.Connection:
    encoded = str(path).replace("?", "%3F")
    conn = sqlite3.connect(f"file:{encoded}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _schema_version(conn: sqlite3.Connection) -> str | None:
    if "tehm_meta" not in _table_names(conn):
        return None
    row = conn.execute(
        "SELECT value FROM tehm_meta WHERE key='schema_version'").fetchone()
    return row["value"] if row else None


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {row["name"] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def _counts(conn: sqlite3.Connection, tables: list[str]) -> dict[str, int]:
    return {table: int(conn.execute(
        f'SELECT COUNT(*) AS n FROM "{table}"').fetchone()["n"])
            for table in tables}


def _table_digests(conn: sqlite3.Connection, tables: list[str]) -> dict[str, str]:
    digests = {}
    for table in tables:
        columns = [row["name"] for row in conn.execute(
            f'PRAGMA table_info("{table}")')]
        rows = [dict(row) for row in conn.execute(f'SELECT * FROM "{table}"')]
        rows.sort(key=stable_dumps)
        payload = {"columns": columns, "rows": rows}
        digests[table] = hashlib.sha256(stable_dumps(payload).encode()).hexdigest()
    return digests


def _file_identity(path: Path) -> dict:
    result = {"path": str(path), "sha256": _sha256(path),
              "size": path.stat().st_size}
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(path) + suffix)
        if sidecar.is_file():
            result[suffix.lstrip("-")] = {
                "sha256": _sha256(sidecar), "size": sidecar.stat().st_size}
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cleanup_sqlite_family(path: Path) -> None:
    for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--report", type=Path, default=None)
    ap.add_argument("--overwrite", action="store_true",
                    help="explicitly replace an existing output/report")
    args = ap.parse_args(argv)
    result = migrate(args.source, args.output, report=args.report,
                     overwrite=args.overwrite)
    print(json.dumps({key: result[key] for key in (
        "status", "source_schema_version", "output_schema_version",
        "canonical_rows_preserved", "source_unchanged", "replay_required")},
                     ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
