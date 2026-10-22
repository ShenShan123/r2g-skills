"""P1b-4: idempotent, atomic ingest; commit watermark; atomic artifact writes (protocol v1 section 8.2)."""
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tehm import db as tehm_db  # noqa: E402
from tehm.artifact_store import ArtifactStore  # noqa: E402
from tehm_backend import TehmMemoryBackend  # noqa: E402


def _project(tmp_path, rows):
    proj = tmp_path / "chacha"
    (proj / "constraints").mkdir(parents=True)
    (proj / "reports").mkdir()
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = chacha\nexport PLATFORM = sky130hs\nexport CORE_UTILIZATION = 17\n")
    (proj / "reports" / "fix_log.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return proj


def _row(i, verdict="no_improvement", after=5):
    return {"fix_session_id": "s", "iter": i, "check": "drc", "strategy": "cat_%d" % i, "violation_class": "li.3",
            "from_stage": "floorplan", "before": 5, "after": after, "verdict": verdict, "rolled_back": False,
            "config_delta": {"CORE_UTILIZATION": str(17 - i)}}


def _count(db):
    with sqlite3.connect(db) as c:
        return c.execute("SELECT count(*) FROM tehm_transitions").fetchone()[0]


def _wm(db):
    with sqlite3.connect(db) as c:
        return tehm_db.read_watermark(c)


def test_t11_reingesting_after_a_later_change_adds_no_transition(tmp_path):
    proj = _project(tmp_path, [_row(1), _row(2, verdict="cleared", after=0)])
    b = TehmMemoryBackend(db_path=tmp_path / "t.sqlite", artifact_root=tmp_path / "a")
    try:
        assert len(b.ingest_project(proj)) == 2 and _wm(tmp_path / "t.sqlite") == 1
        # a later on-disk change (another report, a config edit) changes every state digest
        (proj / "reports" / "lvs.json").write_text(json.dumps({"status": "clean"}))
        (proj / "constraints" / "config.mk").write_text(
            "export DESIGN_NAME = chacha\nexport PLATFORM = sky130hs\nexport CORE_UTILIZATION = 9\n")
        assert b.ingest_project(proj) == []
        assert _count(tmp_path / "t.sqlite") == 2 and _wm(tmp_path / "t.sqlite") == 1   # nothing new committed
    finally:
        b.close()


def test_an_interrupted_ingest_leaves_no_partial_evidence(tmp_path, monkeypatch):
    import tehm.adapters.r2g_evidence as ad
    proj = _project(tmp_path, [_row(1), _row(2)])
    real, calls = ad.capture, []

    def flaky(conn, store, record, **kw):
        calls.append(record.record_id)
        if len(calls) == 2:
            raise RuntimeError("interrupted")
        return real(conn, store, record, **kw)
    monkeypatch.setattr(ad, "capture", flaky)
    b = TehmMemoryBackend(db_path=tmp_path / "t.sqlite", artifact_root=tmp_path / "a")
    try:
        with pytest.raises(RuntimeError):
            b.ingest_project(proj)
        assert _count(tmp_path / "t.sqlite") == 0 and _wm(tmp_path / "t.sqlite") == 0  # first record rolled back
    finally:
        b.close()


def test_a_committed_rebuild_moves_the_watermark(tmp_path):
    proj = _project(tmp_path, [_row(1, verdict="cleared", after=0)])
    b = TehmMemoryBackend(db_path=tmp_path / "t.sqlite", artifact_root=tmp_path / "a")
    try:
        b.ingest_project(proj)
        w = _wm(tmp_path / "t.sqlite")
        b.rebuild()
        assert _wm(tmp_path / "t.sqlite") == w + 1
    finally:
        b.close()


def test_an_interrupted_artifact_write_leaves_nothing_at_the_final_path(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path / "a")

    def boom(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        store.put("graph", b"{}")
    blobs = [p for p in (tmp_path / "a").rglob("*") if p.is_file()]
    assert blobs == []                                    # neither the blob nor a temp file remains
