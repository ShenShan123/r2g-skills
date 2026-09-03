"""Revision3 P16 schema/contract freeze tests."""
from __future__ import annotations

import json
import sqlite3

import pytest

from tehm.db import connect, ensure_schema
from tehm.schema_contract import (
    SchemaContractError, SchemaContractFreezeReceipt, freeze_schema_contract,
    replay_schema_contract,
)


def _db(tmp_path):
    path = tmp_path / "tehm.sqlite"
    conn = connect(path)
    ensure_schema(conn)
    conn.close()
    return path


def test_p16_freeze_replays_read_only_db_and_excludes_docs(tmp_path):
    db_path = _db(tmp_path)
    output = tmp_path / "schema-freeze.json"
    report = freeze_schema_contract(db_path=db_path, output=output)
    receipt = replay_schema_contract(output)
    assert isinstance(receipt, SchemaContractFreezeReceipt)
    assert receipt.db_schema_version == "tehm-v4"
    assert report["memory_docs_submitted"] is False
    assert "memory/docs" not in output.read_text()
    assert not (tmp_path / "tehm.sqlite-wal").exists()
    assert not (tmp_path / "tehm.sqlite-shm").exists()


def test_p16_freeze_without_db_is_replayable(tmp_path):
    output = tmp_path / "schema-only.json"
    report = freeze_schema_contract(output=output)
    receipt = replay_schema_contract(output)
    assert receipt.db_path is None
    assert report["schema_contract"]["observed_objects"] is None


def test_p16_replay_rejects_schema_drift(tmp_path):
    from tehm import config

    schema = tmp_path / "schema.sql"
    schema.write_text(config.SCHEMA_PATH.read_text())
    output = tmp_path / "freeze.json"
    freeze_schema_contract(schema_path=schema, output=output)
    schema.write_text(schema.read_text() + "\n-- drift\n")
    with pytest.raises(SchemaContractError, match="digest drifted"):
        replay_schema_contract(output, schema_path=schema)


def test_p16_replay_rejects_tampered_receipt(tmp_path):
    output = tmp_path / "freeze.json"
    freeze_schema_contract(output=output)
    payload = json.loads(output.read_text())
    payload["schema_contract"]["memory_docs_submitted"] = True
    output.write_text(json.dumps(payload))
    with pytest.raises(SchemaContractError):
        replay_schema_contract(output)


def test_p16_db_schema_mismatch_is_fail_closed(tmp_path):
    db_path = _db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute("DROP TABLE tehm_states")
    conn.commit()
    conn.close()
    with pytest.raises(SchemaContractError, match="schema differs"):
        freeze_schema_contract(db_path=db_path)
