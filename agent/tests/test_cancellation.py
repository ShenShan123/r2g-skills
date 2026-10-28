import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

import pytest

from r2g_agent import housekeeping
from r2g_agent.jobs import JobStore
from r2g_agent.pipeline import Cancelled, OWNER, Pipeline, StopUnconfirmed


def job(store, statuses):
    jid = store.create_job('cancellation test', {}, [{'name': str(i)} for i in range(len(statuses))])
    store.set_job(jid, 'running')
    for i, status in enumerate(statuses):
        store.set_design(jid, str(i), status=status, detail={'owner': OWNER})
    return jid


def until(check, seconds=8):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.05)
    assert check()


def test_cancel_all_queued_without_waiting_for_executor_and_preserve_results(monkeypatch):
    store = JobStore()
    jid = job(store, ['pending'] * 100 + ['passed', 'failed'])
    monkeypatch.setattr('r2g_agent.package.package_job', lambda *a: Path('/tmp/kept.zip'))
    pipe = Pipeline(store, ingest=False)
    try:
        first = pipe.cancel(jid)
        assert first == {'status': 'cancelled', 'cancel_requested': True}
        assert pipe.cancel(jid) == first
        states = [d['status'] for d in store.job(jid)['designs']]
        assert states == ['cancelled'] * 100 + ['passed', 'failed']
        assert len([e for e in store.events(jid) if e['kind'] == 'cancel']) == 1
        assert store.set_design(jid, '0', stage='synth', status='running') is False
        assert store.design(jid, '0')['status'] == 'cancelled'
        store.set_job(jid, 'running')
        assert store.job(jid)['status'] == 'cancelled'
        fresh = JobStore(store.path)
        assert fresh.cancel_requested(jid)
        assert Pipeline(fresh, ingest=False).recover(resume=True) == []
    finally:
        pipe.pool.shutdown()


def test_cancel_does_not_change_a_completed_job():
    store = JobStore()
    jid = job(store, ['passed'])
    store.set_job(jid, 'done', {'package': '/tmp/finished.zip'})
    before = store.job(jid)
    assert store.request_cancel(jid) == {'status': 'done', 'cancel_requested': False}
    assert store.job(jid) == before
    assert store.request_cancel('missing') is None


def test_cancel_before_a_free_slot_never_starts_work():
    def stop():
        raise Cancelled()
    with pytest.raises(Cancelled):
        with housekeeping.design_slot(1, should_stop=stop):
            pytest.fail('cancelled design obtained a free slot')
    assert housekeeping.slots_in_use(1) == 0


def test_cross_process_cancel_stops_term_resistant_detached_children(tmp_path):
    store = JobStore()
    jid = job(store, ['running'])
    project = tmp_path/'project'
    project.mkdir()
    ready = project/'child.pid'
    # A checker may start a new session; cancelling only the original group
    # leaves it running. Ignore TERM to exercise the bounded KILL escalation.
    child = "import os,signal,time; from pathlib import Path; signal.signal(signal.SIGTERM,signal.SIG_IGN); Path(%r).write_text(str(os.getpid())); time.sleep(60)" % str(ready)
    parent = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',%r],start_new_session=True); time.sleep(60)" % child
    # This test starts only tiny Python processes, not a physical EDA run.
    # Do not wait for 50 GB on the machine hosting pytest's temporary folder.
    pipe = Pipeline(store, ingest=False, min_free_gb=0)
    pipe.stop_grace = 0.2
    def execute(*a):
        pipe._stage(jid, '0', 'flow')
        pipe._sh(jid, '0', 'flow', [sys.executable, '-c', parent], project)
    pipe._run_design_inner = execute
    future = pipe.pool.submit(pipe._run_design, jid, '0', project, None, project, 'nangate45')
    try:
        until(ready.exists)
        # A distinct interpreter writes intent; neither a Python set nor the
        # cancelling service's process handles can make this test pass.
        code = "from pathlib import Path; from r2g_agent.jobs import JobStore; JobStore(Path(%r)).request_cancel(%r)" % (str(store.path), jid)
        subprocess.run([sys.executable, '-c', code], check=True,
                       env={**os.environ, 'PYTHONPATH': os.pathsep.join(sys.path)})
        future.result(timeout=12)
        assert store.design(jid, '0')['status'] == 'cancelled'
        assert pipe.finalize_if_done(jid)
        assert store.job(jid)['status'] == 'cancelled'
        fields = Path('/proc/%s/stat' % ready.read_text())
        assert not fields.exists() or fields.read_text().rsplit(')', 1)[1].split()[0] == 'Z'
    finally:
        if ready.exists():
            try: os.kill(int(ready.read_text()), signal.SIGKILL)
            except ProcessLookupError: pass
        pipe.pool.shutdown(wait=True)


def test_unconfirmed_stop_keeps_running_state_and_artifacts(tmp_path, monkeypatch):
    store = JobStore()
    jid = job(store, ['running'])
    pipe = Pipeline(store, ingest=False, min_free_gb=0)
    def blocked(*a):
        store.request_cancel(jid)
        raise StopUnconfirmed('still alive')
    monkeypatch.setattr(pipe, '_run_design_inner', blocked)
    monkeypatch.setattr(housekeeping, 'drop_orfs_copies', lambda p: pytest.fail('deleted live files'))
    try:
        pipe._run_design(jid, '0', tmp_path, None, tmp_path, 'nangate45')
        assert store.design(jid, '0')['status'] == 'running'
        assert not pipe.finalize_if_done(jid)
        assert store.job(jid)['status'] == 'cancelling'
    finally:
        pipe.pool.shutdown()


def test_dual_clean_is_separate_from_dataset_status():
    store = JobStore()
    jid = job(store, ['passed', 'failed', 'failed', 'pending'])
    for name in ['0', '1']:
        store.set_design(jid, name, detail={'signoff': {'dual_clean': True}})
    row = store.jobs()[0]
    assert row['dual_clean'] == 2
    assert row['counts'] == {'passed': 1, 'failed': 2, 'pending': 1}
