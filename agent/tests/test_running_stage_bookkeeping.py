import sqlite3
import pytest

from r2g_agent.jobs import JobStore
from r2g_agent.pipeline import Pipeline, Cancelled


def test_database_busy_does_not_interrupt_healthy_child(monkeypatch):
    pipe = Pipeline(JobStore(), ingest=False)
    try:
        monkeypatch.setattr(pipe, '_check_cancel', lambda j: (_ for _ in ()).throw(sqlite3.OperationalError('database is locked')))
        pipe._check_cancel_while_running('job')
        pipe._cancel.add('job')
        with pytest.raises(Cancelled):
            pipe._check_cancel_while_running('job')
    finally:
        pipe.pool.shutdown()
