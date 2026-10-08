"""The shipped, git-tracked knowledge store is evidence: no default path may write it.

Regression (CORRECTIONS #80, E6 provisioning 2026-09-22): with R2G_KNOWLEDGE_DB /
R2G_HEURISTICS_PATH unset every ingest wrote knowledge/knowledge.sqlite, and
fix_log_manager.manage() rewrote <store dir>/heuristics.json even when
R2G_HEURISTICS_PATH named another file. A user who ran one flow mutated shipped
evidence, and a campaign's own heuristics file never received what it learned.
"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

import fix_log_manager
import knowledge_db
import learn_heuristics


def _digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_default_connect_reads_but_never_writes_the_shipped_db(monkeypatch) -> None:
    monkeypatch.delenv("R2G_KNOWLEDGE_DB", raising=False)
    monkeypatch.delenv(knowledge_db.SHIPPED_WRITE_OPT_IN, raising=False)
    before = _digest(knowledge_db.DEFAULT_DB_PATH)
    conn = knowledge_db.connect()
    try:
        knowledge_db.ensure_schema(conn)          # readers call this; must not migrate
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] >= 0
        with pytest.raises(sqlite3.OperationalError, match=knowledge_db.SHIPPED_WRITE_OPT_IN):
            conn.execute("DELETE FROM runs WHERE 0")
    finally:
        conn.close()
    assert _digest(knowledge_db.DEFAULT_DB_PATH) == before


def test_learner_refuses_to_rewrite_shipped_heuristics(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(knowledge_db.SHIPPED_WRITE_OPT_IN, raising=False)
    before = _digest(knowledge_db.DEFAULT_HEURISTICS_PATH)
    with pytest.raises(PermissionError, match="shipped"):
        learn_heuristics.learn(tmp_path / "k.sqlite", knowledge_db.DEFAULT_HEURISTICS_PATH)
    assert _digest(knowledge_db.DEFAULT_HEURISTICS_PATH) == before


def test_opt_in_restores_write_access(monkeypatch) -> None:
    monkeypatch.setenv(knowledge_db.SHIPPED_WRITE_OPT_IN, "1")
    assert knowledge_db.shipped_write_refusal(knowledge_db.DEFAULT_DB_PATH) is None
    assert knowledge_db.shipped_write_refusal(knowledge_db.DEFAULT_HEURISTICS_PATH) is None


def test_manage_writes_where_R2G_HEURISTICS_PATH_points(tmp_path: Path, tmp_knowledge_dir: Path,
                                                        monkeypatch) -> None:
    db = tmp_knowledge_dir / "knowledge.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn)
    conn.close()
    target = tmp_path / "campaign" / "heuristics.v2.json"
    monkeypatch.setenv("R2G_HEURISTICS_PATH", str(target))
    monkeypatch.setenv("R2G_MINE_AUTORUN", "0")

    fix_log_manager.manage(db)

    assert target.is_file()                                  # the readers' file
    assert not (tmp_knowledge_dir / "heuristics.json").exists()  # not the db sibling
