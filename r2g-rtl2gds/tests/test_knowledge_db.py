"""Tests for knowledge_db module: schema bootstrap and family inference."""
from __future__ import annotations

import knowledge_db


def test_ensure_schema_creates_tables(tmp_knowledge_dir):
    db_path = tmp_knowledge_dir / "runs.sqlite"
    conn = knowledge_db.connect(db_path)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {r[0] for r in rows}
    assert {"runs", "failure_events", "config_lineage"}.issubset(names)
    conn.close()


def test_ensure_schema_is_idempotent(tmp_knowledge_dir):
    db_path = tmp_knowledge_dir / "runs.sqlite"
    conn = knowledge_db.connect(db_path)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    conn.close()


def test_infer_family_direct_mapping(tmp_knowledge_dir):
    families = knowledge_db.load_families(tmp_knowledge_dir / "families.json")
    assert knowledge_db.infer_family("aes128_core", families) == "aes_xcrypt"
    assert knowledge_db.infer_family("RocketTile", families) == "tinyRocket"


def test_infer_family_pattern_fallback(tmp_knowledge_dir):
    families = knowledge_db.load_families(tmp_knowledge_dir / "families.json")
    assert knowledge_db.infer_family("aes_new_variant", families) == "aes_xcrypt"
    assert knowledge_db.infer_family("bp_something", families) == "bp_multi_top"


def test_infer_family_unknown_returns_first_token(tmp_knowledge_dir):
    families = knowledge_db.load_families(tmp_knowledge_dir / "families.json")
    assert knowledge_db.infer_family("foobar_top", families) == "foobar"
