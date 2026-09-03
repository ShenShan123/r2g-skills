"""Writable TEHM campaign snapshot boundary tests."""
from __future__ import annotations

from pathlib import Path

from tehm import db


def test_checkpoint_and_close_removes_wal_sidecars(tmp_path: Path):
    path = tmp_path / "tehm.sqlite"
    conn = db.connect(path)
    db.ensure_schema(conn)
    conn.execute(
        "INSERT INTO tehm_meta(key, value) VALUES ('snapshot_test', 'ok')")

    db.checkpoint_and_close(conn)

    assert not Path(str(path) + "-wal").exists()
    assert not Path(str(path) + "-shm").exists()
    readonly = db.connect_read_only(path)
    try:
        assert readonly.execute(
            "SELECT value FROM tehm_meta WHERE key='snapshot_test'"
        ).fetchone()[0] == "ok"
    finally:
        readonly.close()
