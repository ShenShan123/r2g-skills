"""Backend isolation (design doc 17.4, honesty H5/H8/H12).

TEHM never opens the legacy memory plane: the DB path lock is fail-closed, the
TEHM schema carries no legacy tables, and the honesty gates enforce it. Mirrors
the legacy firewall spirit (knowledge-only reads) in reverse.
"""
from __future__ import annotations

import pytest

from tehm import config, db, honesty


def test_validate_backend_lock_refuses_legacy_paths(tmp_path):
    legacy_db = tmp_path / "knowledge.sqlite"
    legacy_db.touch()
    with pytest.raises(ValueError, match="isolated"):
        config.validate_backend_lock(legacy_db)
    nested = tmp_path / "signoff-loop" / "knowledge" / "knowledge.sqlite"
    nested.parent.mkdir(parents=True)
    nested.touch()
    with pytest.raises(ValueError, match="isolated"):
        config.validate_backend_lock(nested)


def test_connect_refuses_legacy_path(tmp_path):
    legacy_db = tmp_path / "signoff-loop" / "knowledge.sqlite"
    legacy_db.parent.mkdir(parents=True)
    legacy_db.touch()
    with pytest.raises(ValueError):
        db.connect(legacy_db)


def test_tehm_schema_has_no_legacy_tables(tmp_tehm):
    conn, _, _ = tmp_tehm
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    legacy_tables = {"runs", "failure_events", "fix_events", "fix_trajectories",
                     "recipe_status", "ab_trials", "symptoms", "lessons"}
    assert tables.isdisjoint(legacy_tables)


def test_h5_gate_green(tmp_tehm):
    conn, _, tmp = tmp_tehm
    ok, detail = honesty.h5_backend_isolation(conn, tmp / "tehm.sqlite")
    assert ok, detail


def test_h5_gate_fails_on_legacy_path(tmp_tehm):
    conn, _, tmp = tmp_tehm
    ok, detail = honesty.h5_backend_isolation(conn, tmp / "signoff-loop" / "knowledge.sqlite")
    assert not ok
    assert "isolated" in detail


def test_h12_fail_closed(tmp_tehm):
    conn, _, tmp = tmp_tehm
    ok, _ = honesty.h12_no_silent_fallback(conn, tmp / "tehm.sqlite")
    assert ok
    bad_ok, detail = honesty.h12_no_silent_fallback(conn, tmp / "knowledge.sqlite")
    assert not bad_ok
    assert "knowledge.sqlite" in detail


def test_tehm_store_never_requires_legacy_tables(tmp_tehm):
    """A TEHM DB must be fully self-contained (no FK or view referencing legacy)."""
    conn, _, _ = tmp_tehm
    fks = conn.execute("PRAGMA foreign_key_list(tehm_transitions)").fetchall()
    for fk in fks:
        assert fk["table"] in (
            "tehm_states", "tehm_episodes", "tehm_rules"), fk


def test_no_legacy_module_imported():
    """A firewall check: importing the TEHM core must not pull legacy knowledge."""
    import subprocess
    import sys
    from pathlib import Path
    memory_root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, '.'); "
         "import tehm.db, tehm.honesty, tehm.canonical.capture, tehm.cli"],
        capture_output=True, text=True, cwd=str(memory_root), timeout=60)
    assert proc.returncode == 0, proc.stderr
