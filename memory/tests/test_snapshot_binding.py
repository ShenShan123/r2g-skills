"""P1b-5 / D3: task-level snapshot binding (protocol v1 section 8.2). T18-T20 on a real ingested store."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tehm import db as tehm_db  # noqa: E402
from tehm.snapshot import bind_snapshot, read_snapshot  # noqa: E402
from tehm_backend import TehmMemoryBackend  # noqa: E402


def _row(i, after):
    return {"fix_session_id": "s", "iter": i, "check": "drc", "strategy": "cat_%d" % i, "violation_class": "li.3",
            "from_stage": "floorplan", "before": 5, "after": after, "verdict": "cleared" if after == 0 else "applied",
            "rolled_back": False, "config_delta": {"CORE_UTILIZATION": str(17 - i)}}


@pytest.fixture
def master(tmp_path):
    proj = tmp_path / "chacha"
    (proj / "constraints").mkdir(parents=True)
    (proj / "reports").mkdir()
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = chacha\nexport PLATFORM = sky130hs\nexport CORE_UTILIZATION = 17\n")
    store = tmp_path / "master"
    b = TehmMemoryBackend(db_path=store / "tehm.sqlite", artifact_root=store / "artifacts")

    def ingest(*rows):
        with open(proj / "reports" / "fix_log.jsonl", "a") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        return b.ingest_project(proj)
    yield store, ingest
    b.close()


def _n(store):
    with sqlite3.connect("file:%s?mode=ro" % (store / "tehm.sqlite"), uri=True) as c:
        return c.execute("SELECT count(*) FROM tehm_transitions").fetchone()[0], tehm_db.read_watermark(c)


def test_t18_commits_before_start_are_visible_later_and_uncommitted_ones_are_not(master, tmp_path):
    store, ingest = master
    ingest(_row(1, 2))
    s1 = bind_snapshot(store, tmp_path / "task1")
    ingest(_row(2, 0))                                               # committed after the task started
    with sqlite3.connect(store / "tehm.sqlite") as w:               # an uncommitted write in progress
        w.execute("BEGIN IMMEDIATE")
        w.execute("INSERT OR REPLACE INTO tehm_meta(key, value) VALUES ('uncommitted_probe', '1')")
        s2 = bind_snapshot(store, tmp_path / "task2")
        w.rollback()
    assert _n(tmp_path / "task1") == (1, s1["watermark"]) and s1["watermark"] == 1
    assert _n(tmp_path / "task2") == (2, s2["watermark"]) and s2["watermark"] == 2
    with sqlite3.connect(tmp_path / "task2" / "tehm.sqlite") as c:
        assert c.execute("SELECT value FROM tehm_meta WHERE key='uncommitted_probe'").fetchone() is None


def test_t19_two_overlapping_tasks_keep_their_own_fixed_views(master, tmp_path):
    store, ingest = master
    ingest(_row(1, 2))
    a = bind_snapshot(store, tmp_path / "a")
    ingest(_row(2, 0))
    b = bind_snapshot(store, tmp_path / "b")
    ingest(_row(3, 0))                                               # a third commit changes neither view
    assert (_n(tmp_path / "a"), _n(tmp_path / "b"), _n(store)[0]) == ((1, 1), (2, 2), 3)
    assert a["snapshot_id"] != b["snapshot_id"] and read_snapshot(tmp_path / "a") == a


def test_t20_database_and_artifacts_come_from_the_same_commit(master, tmp_path):
    store, ingest = master
    ingest(_row(1, 2))
    s = bind_snapshot(store, tmp_path / "t")
    ingest(_row(2, 0))
    snap_blobs = {p.name for p in (tmp_path / "t" / "artifacts" / "sha256").rglob("*") if p.is_file()}
    master_blobs = {p.name for p in (store / "artifacts" / "sha256").rglob("*") if p.is_file()}
    with sqlite3.connect(tmp_path / "t" / "tehm.sqlite") as c:
        refs = {json.loads(v)[k]["digest"].split(":")[1] for (v,) in c.execute(
            "SELECT artifact_manifest_json FROM tehm_states") for k in json.loads(v)}
    assert refs == snap_blobs and s["artifacts"] == len(refs)      # exactly the referenced blobs
    assert master_blobs - snap_blobs                                # later blobs never copied


def test_a_corrupt_referenced_artifact_fails_the_binding_and_leaves_nothing(master, tmp_path):
    store, ingest = master
    ingest(_row(1, 2))
    blob = next(p for p in (store / "artifacts" / "sha256").rglob("*") if p.is_file())
    blob.write_bytes(b"corrupt")
    with pytest.raises(RuntimeError, match="corrupt"):
        bind_snapshot(store, tmp_path / "t")
    assert not (tmp_path / "t").exists() and not (tmp_path / "t.binding").exists()




def test_a_sealed_read_only_store_binds_and_a_wal_store_is_never_read_immutably(master, tmp_path):
    import os
    from tehm.snapshot import seal_store
    store, ingest = master
    ingest(_row(1, 2))
    sealed = tmp_path / "sealed"
    import shutil
    shutil.copytree(store, sealed)
    seal_store(sealed)
    try:
        r = bind_snapshot(sealed, tmp_path / "task")
        assert r["sealed_source"] and r["watermark"] == 1 and _n(tmp_path / "task") == (1, 1)
    finally:
        for p in [sealed] + list(sealed.rglob("*")):
            p.chmod(p.stat().st_mode | 0o200)
    # a WAL store whose directory is read-only is NOT opened immutable (its WAL may hold committed state)
    wal = tmp_path / "wal"
    shutil.copytree(store, wal)
    with sqlite3.connect(wal / "tehm.sqlite") as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("INSERT OR REPLACE INTO tehm_meta(key, value) VALUES ('probe', '1')")
    (wal / "tehm.sqlite-wal").touch()
    os.chmod(wal, 0o555)
    try:
        try:
            r = bind_snapshot(wal, tmp_path / "task2")
        except sqlite3.OperationalError:
            assert not (tmp_path / "task2").exists()         # refused cleanly: acceptable
        else:                                                # bound: never immutably, the WAL's commit is visible
            assert r["sealed_source"] is False
            with sqlite3.connect(tmp_path / "task2" / "tehm.sqlite") as c:
                assert c.execute("SELECT value FROM tehm_meta WHERE key='probe'").fetchone() == ("1",)
    finally:
        os.chmod(wal, 0o755)
