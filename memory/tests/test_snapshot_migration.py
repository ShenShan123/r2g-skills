from __future__ import annotations

import json
import sqlite3

import pytest

from scripts.migrate_tehm_snapshot_v4 import migrate
from tehm import db


def _v3_source(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE tehm_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO tehm_meta VALUES ('schema_version', 'tehm-v3');
        CREATE TABLE tehm_states (
            state_id TEXT PRIMARY KEY, state_hash TEXT NOT NULL,
            artifact_manifest_json TEXT NOT NULL, created_at TEXT NOT NULL);
        INSERT INTO tehm_states VALUES ('s', 'h', '{}', 'now');
    """)
    conn.commit()
    conn.close()


def test_migration_is_output_only_and_preserves_existing_rows(tmp_path):
    source = tmp_path / "source.sqlite"
    output = tmp_path / "migrated.sqlite"
    report = tmp_path / "migration.json"
    _v3_source(source)
    source_bytes = source.read_bytes()

    result = migrate(source, output, report=report)

    assert result["status"] == "MIGRATED"
    assert result["source_schema_version"] == "tehm-v3"
    assert result["output_schema_version"] == "tehm-v4"
    assert result["canonical_rows_preserved"] is True
    assert result["source_unchanged"] is True
    assert result["replay_required"] is True
    assert source.read_bytes() == source_bytes
    assert json.loads(report.read_text())["output_schema_version"] == "tehm-v4"
    conn = db.connect_read_only(output)
    conn.close()


def test_migration_refuses_in_place_or_unapproved_overwrite(tmp_path):
    source = tmp_path / "source.sqlite"
    _v3_source(source)
    with pytest.raises(ValueError, match="different paths"):
        migrate(source, source)
    output = tmp_path / "out.sqlite"
    migrate(source, output)
    with pytest.raises(FileExistsError, match="overwrite"):
        migrate(source, output)
