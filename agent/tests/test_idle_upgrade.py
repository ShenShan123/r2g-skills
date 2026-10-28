import importlib.util
from pathlib import Path
import sqlite3

import pytest


spec = importlib.util.spec_from_file_location(
    'idle_upgrade', Path(__file__).resolve().parents[2] / 'experiments/agent_repro/upgrade_ui_when_idle.py')
upgrade = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upgrade)


@pytest.mark.parametrize('reason', ['database is locked', 'database is busy'])
def test_busy_database_is_unknown_not_idle_and_next_poll_recovers(monkeypatch, reason):
    states = []
    def locked(_):
        raise sqlite3.OperationalError(reason)
    monkeypatch.setattr(upgrade, 'active_jobs', locked)
    assert upgrade.observe_jobs(Path('/unused'), lambda state, **kw: states.append(state)) is None
    assert states == ['waiting_for_database']
    monkeypatch.setattr(upgrade, 'active_jobs', lambda _: [{'id': 'live', 'unfinished': 8}])
    assert upgrade.observe_jobs(Path('/unused'), lambda *a, **kw: None)[0]['unfinished'] == 8


def test_non_lock_database_errors_are_not_hidden(monkeypatch):
    def invalid(_):
        raise sqlite3.OperationalError('no such table: jobs')
    monkeypatch.setattr(upgrade, 'active_jobs', invalid)
    with pytest.raises(sqlite3.OperationalError):
        upgrade.observe_jobs(Path('/unused'), lambda *a, **kw: None)
