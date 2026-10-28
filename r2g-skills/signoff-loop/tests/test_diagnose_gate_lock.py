"""A transiently locked knowledge store must not fail the lifecycle gate closed.

nangate45 C03368 (2026-10-28): with ~20 designs ingesting into one store, the
gate's ensure_schema hit `database is locked`, the plan lost lifecycle_gate_ok
and the repair stopped `no_auto_strategy` on 1107 antenna violations that the
same diagnosis repairs once the lock clears.
"""
import sqlite3
import time

import pytest

import diagnose_signoff_fix as dsf
import knowledge_db


def test_gate_waits_out_a_transient_lock(monkeypatch, tmp_path):
    real = knowledge_db.connect
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise sqlite3.OperationalError("database is locked")
        return real(tmp_path / "k.sqlite")

    monkeypatch.setattr(knowledge_db, "connect", flaky)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    conn = dsf._gate_connection()
    assert calls["n"] == 3
    conn.close()


def test_gate_still_fails_closed_on_other_errors_and_after_deadline(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)

    def broken(*a, **k):
        raise sqlite3.OperationalError("unable to open database file")
    monkeypatch.setattr(knowledge_db, "connect", broken)
    with pytest.raises(sqlite3.OperationalError):
        dsf._gate_connection()

    def locked(*a, **k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(knowledge_db, "connect", locked)
    monkeypatch.setenv("R2G_GATE_LOCK_WAIT_S", "0")
    with pytest.raises(sqlite3.OperationalError):
        dsf._gate_connection()
