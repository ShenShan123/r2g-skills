import shutil
import sqlite3

import pytest

from r2g_agent import knowledge

REAL_DB, REAL_HEUR = knowledge.shipped_store()
pytestmark = pytest.mark.skipif(not REAL_DB.is_file(), reason="no shipped knowledge store")


@pytest.fixture(autouse=True)
def legacy_backend(monkeypatch):
    monkeypatch.setenv("R2G_MEMORY_BACKEND", "legacy")


@pytest.fixture
def shipped(tmp_path, monkeypatch):
    """A private copy of the shipped store, so a test can change it."""
    d = tmp_path / "shipped"
    d.mkdir()
    shutil.copy2(REAL_DB, d / "knowledge.sqlite")
    shutil.copy2(REAL_HEUR, d / "heuristics.json")
    monkeypatch.setattr(knowledge, "shipped_store", lambda: (d / "knowledge.sqlite", d / "heuristics.json"))
    return d / "knowledge.sqlite"


def _runs(db):
    c = sqlite3.connect(db)
    try:
        return c.execute("select count(*) from runs").fetchone()[0]
    finally:
        c.close()


def test_env_never_points_at_the_shipped_store():
    e = knowledge.env()
    assert e["R2G_KNOWLEDGE_DB"] != str(REAL_DB) and e["R2G_HEURISTICS_PATH"] != str(REAL_HEUR)
    assert all(v.startswith(str(knowledge.store_dir())) for k, v in e.items() if k != "R2G_MEMORY_BACKEND")


def test_seed_then_up_to_date(shipped):
    assert knowledge.ensure()["action"] == "seeded"
    p = knowledge.paths()
    assert _runs(p["db"]) == _runs(shipped) and p["heuristics"].is_file()
    assert knowledge.ensure()["action"] == "up_to_date"


def test_shipped_update_is_merged_and_user_rows_survive(shipped):
    knowledge.ensure()
    db = knowledge.paths()["db"]
    c = sqlite3.connect(db)
    # a row only the user has (their own run) and a shipped row they lack
    rid = c.execute("select run_id from runs order by run_id limit 1").fetchone()[0]
    c.execute("delete from runs where run_id=?", (rid,))
    assert c.execute("select count(*) from runs where run_id=?", (rid,)).fetchone()[0] == 0
    c.execute("create temp table t as select * from runs where orfs_status='pass' limit 1")
    c.execute("update t set run_id='user-own-run', project_path='/u/p'")
    c.execute("insert into runs select * from t")
    c.commit()
    c.close()
    s = sqlite3.connect(shipped)
    s.execute("pragma user_version = 7")       # the shipped bytes change, as after a git pull
    s.commit()
    s.close()
    r = knowledge.ensure()
    assert r["action"] == "merged", r
    c = sqlite3.connect(db)
    have = {x[0] for x in c.execute("select run_id from runs")}
    c.close()
    assert rid in have and "user-own-run" in have
    assert knowledge.ensure()["action"] == "up_to_date"


def test_refused_merge_leaves_the_user_store_alone(shipped):
    knowledge.ensure()
    p = knowledge.paths()
    before, marker = _runs(p["db"]), p["marker"].read_text()
    shipped.write_bytes(b"not a database")
    r = knowledge.ensure()
    assert r["action"] == "merge_refused"
    assert _runs(p["db"]) == before and p["marker"].read_text() == marker
