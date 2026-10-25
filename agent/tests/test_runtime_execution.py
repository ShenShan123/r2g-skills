import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import threading
import time

import pytest

from r2g_agent.jobs import JobStore, RUNNING, CANCELLED, RECOVERY_REQUIRED
from r2g_agent import pipeline
from r2g_agent.pipeline import Pipeline, OWNER, BOOT_ID, RecoveryRequired
from r2g_agent.processes import group_members, proc_start, stop_orphan


def setup_job(tmp_path, *, owner=OWNER, protocol=True):
    store = JobStore()
    project = tmp_path / 'project'
    project.mkdir(exist_ok=True)
    jid = store.create_job('execution test', {}, [{'name': 'top'}])
    store.set_job(jid, RUNNING)
    detail = {'owner': owner, 'source': str(tmp_path / 'input'), 'top': 'top'}
    if protocol:
        detail['execution_protocol'] = 1
    store.set_design(jid, 'top', status=RUNNING, stage='synth', project=str(project), detail=detail)
    pipe = Pipeline(store, workers=1, ingest=False, min_free_gb=0)
    pipe.stop_grace = .15
    return store, pipe, jid, project


def wait_until(condition, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(.02)
    raise AssertionError('condition not reached before deadline')


def test_create_and_transition_events_rollback_with_state(tmp_path, monkeypatch):
    store = JobStore()
    original = store._event
    def fail(*a, **kw):
        raise RuntimeError('injected event failure')
    monkeypatch.setattr(store, '_event', fail)
    with pytest.raises(RuntimeError):
        store.create_job('broken', {}, [{'name': 'a'}])
    assert store.jobs() == []
    monkeypatch.setattr(store, '_event', original)
    jid = store.create_job('ok', {}, [{'name': 'a'}])
    monkeypatch.setattr(store, '_event', fail)
    with pytest.raises(RuntimeError):
        store.set_design(jid, 'a', status=RUNNING, event=('synth','start','starting',{}))
    assert store.design(jid, 'a')['status'] == 'pending'
    assert len(store.events(jid)) == 1


def test_old_schema_migrates_additively_with_data_preserved(tmp_path):
    path = tmp_path / 'jobs.sqlite'
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE jobs (id TEXT PRIMARY KEY,created REAL,updated REAL,status TEXT,title TEXT,spec TEXT,result TEXT)')
        c.execute("INSERT INTO jobs VALUES ('old',1,2,'done','old title','{}','{}')")
        c.commit()
        with sqlite3.connect(tmp_path/'backup.sqlite') as backup:
            c.backup(backup)
    store = JobStore(path)
    assert store.job('old')['title'] == 'old title'
    assert store.job('old')['cancel_requested_at'] is None
    assert not store.set_job('old', RUNNING)
    assert not store.claim_job('old', ('done',), RUNNING)
    with sqlite3.connect(tmp_path/'backup.sqlite') as c:
        assert c.execute('SELECT status FROM jobs').fetchone()[0] == 'done'


def test_cancel_is_idempotent_persistent_and_rejects_late_pass(tmp_path):
    store, pipe, jid, _ = setup_job(tmp_path)
    first = pipe.cancel(jid)
    second = Pipeline(JobStore(store.path), ingest=False).cancel(jid)
    assert first == second and first['status'] == 'cancelling'
    assert first['cancel_requested_at'] and first['cancelled_at'] is None
    assert not store.set_design(jid, 'top', status='passed', expected_owner=OWNER)
    assert [e['kind'] for e in store.events(jid)].count('cancel_requested') == 1
    assert pipe.cancel('missing') is None
    store.set_design(jid, 'top', status=CANCELLED, expected_owner=OWNER)
    pipe.finalize_if_done(jid)
    final = store.job(jid)
    assert final['status'] == CANCELLED and final['cancelled_at'] >= final['cancel_requested_at']
    assert not store.set_job(jid, 'done')
    assert not store.set_design(jid, 'top', status=RUNNING)


def test_stale_worker_cannot_change_status_events_or_execution(tmp_path):
    store, _, jid, _ = setup_job(tmp_path)
    run = store.begin_execution(jid, 'top', 'synth', OWNER, BOOT_ID, ['echo','ok'])
    assert store.claim_design(jid, 'top', OWNER, 'new-owner')
    assert not store.set_design(jid, 'top', status='passed', expected_owner=OWNER)
    assert not store.event(jid, 'top', 'synth', 'ok', 'late', expected_owner=OWNER)
    assert not store.update_execution(run, OWNER, 'exited', exit_code=0)
    assert store.execution(run)['status'] == 'pending'


@pytest.mark.parametrize('record', ['legacy_missing_handle','legacy_pgid','pending','wrong_boot','stop_unconfirmed','missing_record'])
def test_uncertain_execution_is_not_replayed(tmp_path, monkeypatch, record):
    dead = '999999999:1'
    store, pipe, jid, project = setup_job(tmp_path, owner=dead, protocol=False)
    if record == 'legacy_pgid':
        store.set_design(jid,'top',detail={'pgid':999999998})
    elif record != 'legacy_missing_handle':
        if record == 'missing_record':
            store.set_design(jid,'top',detail={'execution_id':'missing','execution_protocol':1})
        else:
            run = store.begin_execution(jid,'top','synth',dead,
                                        'another-boot' if record == 'wrong_boot' else BOOT_ID, ['test'])
            if record != 'pending':
                store.update_execution(run,dead,'running',pgid=123456,process_start='1')
    monkeypatch.setattr(pipeline,'stop_orphan',lambda *a,**kw: False)
    monkeypatch.setattr(pipe.pool,'submit',lambda *a,**kw: pytest.fail('must not replay'))
    pipe.recover(resume=True)
    assert store.design(jid,'top')['status'] == RECOVERY_REQUIRED
    assert store.job(jid)['status'] == RECOVERY_REQUIRED
    assert any(e['kind'] == 'recovery_required' for e in store.events(jid))
    assert project.exists()


def test_queued_cancel_survives_restart_without_launch(tmp_path, monkeypatch):
    store, pipe, jid, _ = setup_job(tmp_path, owner='999999999:1')
    store.set_design(jid,'top',status='pending',stage='queued')
    pipe.cancel(jid)
    restarted = Pipeline(JobStore(store.path),ingest=False)
    monkeypatch.setattr(restarted.pool,'submit',lambda *a,**kw: pytest.fail('cancelled queue must not launch'))
    restarted.recover(resume=True)
    assert store.design(jid,'top')['status'] == CANCELLED
    assert store.job(jid)['status'] == CANCELLED


def test_active_execution_prevents_finalization(tmp_path):
    store, pipe, jid, _ = setup_job(tmp_path)
    run = store.begin_execution(jid,'top','synth',OWNER,BOOT_ID,['test'])
    store.set_design(jid,'top',status='failed')
    assert not pipe.finalize_if_done(jid)
    store.update_execution(run,OWNER,'not_started')
    assert pipe.finalize_if_done(jid)


def test_cancel_reaps_term_resistant_parent_and_child(tmp_path, monkeypatch):
    store, pipe, jid, project = setup_job(tmp_path)
    ready = tmp_path/'ready.json'
    program = """import os,signal,time,json,sys
signal.signal(signal.SIGTERM,signal.SIG_IGN)
child=os.fork()
if child:
 open(sys.argv[1],'w').write(json.dumps([os.getpid(),child]))
while True: time.sleep(.1)
"""
    monkeypatch.setattr(pipe,'_run_design_inner',lambda *a: pipe._sh(
        jid,'top','synth',[sys.executable,'-c',program,str(ready)],project))
    worker=threading.Thread(target=pipe._run_design,args=(jid,'top',tmp_path/'src',None,project,'nangate45'))
    worker.start()
    try:
        wait_until(ready.exists)
        pids=json.loads(ready.read_text())
        # A separate runtime/connection requests cancellation, not the local set.
        Pipeline(JobStore(store.path),ingest=False).cancel(jid)
        worker.join(5)
        assert not worker.is_alive()
        assert group_members(pids[0]) == []
        assert store.design(jid,'top')['status'] == CANCELLED
        run=store.execution(store.design(jid,'top')['detail']['execution_id'])
        assert run['status'] == 'interrupted' and run['exit_code'] is not None
        assert pipe.finalize_if_done(jid) and store.job(jid)['status'] == CANCELLED
    finally:
        if ready.exists():
            try: os.killpg(json.loads(ready.read_text())[0],signal.SIGKILL)
            except ProcessLookupError: pass
        worker.join(5)


def test_timeout_confirms_process_exit(tmp_path):
    store, pipe, jid, project = setup_job(tmp_path)
    rc=pipe._sh(jid,'top','synth',[sys.executable,'-c',
        'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)'],project,timeout=.2)
    run=store.execution(store.design(jid,'top')['detail']['execution_id'])
    assert rc == 124 and run['exit_code'] == 124 and run['status'] == 'exited'
    assert group_members(run['pgid']) == []


def test_failed_spawn_is_recorded_as_not_started(tmp_path):
    store, pipe, jid, project = setup_job(tmp_path)
    with pytest.raises(FileNotFoundError):
        pipe._sh(jid,'top','synth',['/no/such/edacode-test-tool'],project)
    run=store.execution(store.design(jid,'top')['detail']['execution_id'])
    assert run['status'] == 'not_started' and run['pgid'] is None


def test_uncertain_stop_does_not_delete_working_outputs(tmp_path, monkeypatch):
    store, pipe, jid, project=setup_job(tmp_path)
    output=project/'still-in-use'; output.write_text('preserve')
    monkeypatch.setattr(pipe,'_run_design_inner',lambda *a: (_ for _ in ()).throw(RecoveryRequired('unknown')))
    monkeypatch.setattr(pipeline.housekeeping,'drop_orfs_copies',lambda *a: pytest.fail('must preserve outputs'))
    pipe._run_design(jid,'top',tmp_path/'src',None,project,'nangate45')
    assert output.read_text() == 'preserve'
    assert store.job(jid)['status'] == RECOVERY_REQUIRED


def test_recycled_process_start_does_not_signal_group(tmp_path):
    p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)',str(tmp_path)],start_new_session=True)
    try:
        assert not stop_orphan(p.pid,str(tmp_path),.1,expected_start='wrong')
        assert p.poll() is None
        assert stop_orphan(p.pid,str(tmp_path),.1,expected_start=proc_start(p.pid))
        p.wait(timeout=2)
    finally:
        if p.poll() is None: p.kill(); p.wait(timeout=2)


@pytest.mark.parametrize('window', ['before_start','after_start','after_output','before_commit'])
def test_real_worker_crash_windows(tmp_path, monkeypatch, window):
    store, parent, jid, project=setup_job(tmp_path,owner='unstarted')
    marker=tmp_path/'side_effect'
    handle=tmp_path/'test-only-child-pid'
    worker_script=r'''
import os,sys
from pathlib import Path
from r2g_agent import pipeline
from r2g_agent.jobs import JobStore
window,jid,project,marker,handle=sys.argv[1:]
store=JobStore()
store.set_design(jid,'top',detail={'owner':pipeline.OWNER})
runner=pipeline.Pipeline(store,ingest=False)
actual_popen=pipeline.subprocess.Popen
def spawn(*args,**kwargs):
    if window=='before_start': os._exit(91)
    process=actual_popen(*args,**kwargs)
    Path(handle).write_text(str(process.pid))
    if window=='after_start': os._exit(92)
    return process
pipeline.subprocess.Popen=spawn
actual_update=store.update_execution
def update(invocation,owner,status,**kwargs):
    if window=='before_commit' and status=='exited': os._exit(93)
    return actual_update(invocation,owner,status,**kwargs)
store.update_execution=update
program="import sys,time; open(sys.argv[1],'a').write('one\\n'); time.sleep("+('60' if window in ('after_start','after_output') else '0')+")"
runner._sh(jid,'top','synth',[sys.executable,'-c',program,marker],Path(project))
'''
    env={**os.environ,'PYTHONPATH':str(Path(pipeline.__file__).resolve().parents[1])}
    worker=subprocess.Popen([sys.executable,'-c',worker_script,window,jid,str(project),str(marker),str(handle)],
                            env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        if window=='after_output':
            def ready():
                ref=store.design(jid,'top')['detail'].get('execution_id')
                return marker.exists() and ref and store.execution(ref)['status']=='running'
            wait_until(ready)
            worker.kill()
        stdout,stderr=worker.communicate(timeout=10)
        assert worker.returncode != 0,(stdout,stderr)
        calls=[]
        monkeypatch.setattr(parent.pool,'submit',lambda fn,*args: calls.append(args))
        monkeypatch.setattr(parent,'_watch_job',lambda jid: None)
        parent.recover(resume=True)
        if window in ('before_start','after_start'):
            assert calls==[]
            assert store.job(jid)['status']==RECOVERY_REQUIRED
            ref=store.design(jid,'top')['detail']['execution_id']
            assert store.execution(ref)['pgid'] is None
        else:
            assert len(calls)==1
            assert store.design(jid,'top')['status']=='pending'
            assert marker.read_text()=='one\n'
            ref=store.design(jid,'top')['detail']['execution_id']
            run=store.execution(ref)
            assert run['status']=='interrupted' and group_members(run['pgid'])==[]
            parent.recover(resume=True)
            assert len(calls)==1  # no second adoption while the new owner is alive
    finally:
        if worker.poll() is None: worker.kill(); worker.wait(timeout=3)
        if handle.exists():
            try: os.killpg(int(handle.read_text()),signal.SIGKILL)
            except ProcessLookupError: pass


@pytest.mark.parametrize('leader_exits', [False, True])
def test_stop_finds_detached_child_after_setsid(tmp_path, leader_exits):
    """Actual EDA wrappers use setsid/timeout; the original pgid is insufficient."""
    import uuid
    from r2g_agent.processes import execution_members
    marker = uuid.uuid4().hex
    ready = tmp_path/'detached-ready'
    program = '''import os,signal,time,sys
child=os.fork()
if not child:
 os.setsid()
 signal.signal(signal.SIGTERM, signal.SIG_IGN)
 open(sys.argv[1],'w').write(str(os.getpid()))
 while True: time.sleep(.1)
if sys.argv[2]=='True': sys.exit(0)
while True: time.sleep(.1)
'''
    unrelated = subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'], start_new_session=True)
    parent = subprocess.Popen([sys.executable,'-c',program,str(ready),str(leader_exits)],
                              start_new_session=True,env={**os.environ,'EDACODE_EXECUTION_ID':marker})
    start=proc_start(parent.pid)
    try:
        wait_until(ready.exists)
        child=int(ready.read_text())
        assert os.getpgid(child) != parent.pid
        if leader_exits: parent.wait(timeout=3)
        assert stop_orphan(parent.pid,str(tmp_path),.15,expected_start=start,execution_id=marker)
        parent.wait(timeout=3)
        assert execution_members(marker, start) == []
        assert group_members(child) == []
        assert unrelated.poll() is None
    finally:
        if parent.poll() is None: parent.kill(); parent.wait(timeout=3)
        if ready.exists():
            try: os.kill(int(ready.read_text()),signal.SIGKILL)
            except ProcessLookupError: pass
        unrelated.kill(); unrelated.wait(timeout=3)


def test_exception_after_cancel_gets_confirmed_terminal_state(tmp_path, monkeypatch):
    store, pipe, jid, project = setup_job(tmp_path)
    def fail(*args):
        pipe.cancel(jid)
        raise ValueError('late ordinary failure')
    monkeypatch.setattr(pipe, '_run_design_inner', fail)
    pipe._run_design(jid,'top',tmp_path/'src',None,project,'nangate45')
    assert store.design(jid,'top')['status'] == CANCELLED
    assert pipe.finalize_if_done(jid)
    assert store.job(jid)['cancelled_at'] is not None


def test_terminal_design_cannot_start_another_stage(tmp_path):
    store, pipe, jid, project = setup_job(tmp_path)
    store.set_design(jid,'top',status='passed')
    assert store.begin_execution(jid,'top','late',OWNER,BOOT_ID,['should-not-start']) is None
    assert store.active_executions(jid) == []


@pytest.mark.parametrize('drc,lvs,rcx,reason,expected', [
    ('clean', 'clean', 'complete', None, False),
    ('fail', 'clean', 'complete', None, False),
    ('clean', 'fail', 'complete', 'netgen_fallback_mismatch', False),
    ('clean', 'clean', 'failed', None, True),
    ('clean', 'clean', 'missing', None, True),
    ('clean', 'failed', 'complete', 'layout_changed_under_signoff', True),
    ('clean', 'failed', 'complete', 'physical_rebuild_required', True),
    ('clean', 'crash', 'complete', None, True),
    ('clean', 'incomplete', 'complete', None, True),
    ('clean', 'timeout', 'complete', None, True),
    ('clean', 'stale', 'complete', None, True),
    ('unknown', 'clean', 'complete', None, True),
    ('timeout', 'clean', 'complete', None, True),
])
def test_signoff_distinguishes_infrastructure_failure_from_measured_mismatch(tmp_path, monkeypatch, drc, lvs, rcx, reason, expected):
    # This test isolates consumer classification. Native evidence and missing
    # evidence are exercised independently in test_validation / integration.
    from r2g_agent import validation
    checks = {key: {'verdict': 'FAIL' if status == 'fail' else 'PASS'}
              for key, status in [('drc', drc), ('lvs', lvs), ('rcx', rcx)]}
    monkeypatch.setattr(validation, 'review', lambda *a, **kw: {
        'verdict': 'FAIL' if any(c['verdict'] == 'FAIL' for c in checks.values()) else 'PASS',
        'checks': checks, 'three_layer': {}, 'claims': [], 'evidence_files': []})
    reports = tmp_path / 'reports'
    reports.mkdir()
    (reports / 'drc.json').write_text(json.dumps({'status': drc, 'total_violations': 1 if drc == 'fail' else 0}))
    (reports / 'lvs.json').write_text(json.dumps({'status': lvs, 'reason': reason,
                                               'mismatch_count': 1 if lvs == 'fail' else 0}))
    if rcx != 'missing':
        (reports / 'rcx.json').write_text(json.dumps({'status':rcx}))
    verdict = pipeline.signoff_verdict(tmp_path)
    assert bool(verdict.get('infrastructure_error')) is expected
