import json
from pathlib import Path

import pytest

from r2g_agent.agent_loop import SessionStore, run_turn
from r2g_agent.deletion import DeletionManager, ORFS_ARTIFACT_KINDS, artifact_paths, legacy_created_jobs
from r2g_agent.jobs import JobStore
from r2g_agent.lifecycle import resource_lock
from r2g_agent.pipeline import Pipeline
from r2g_agent import housekeeping, tools


def setup(monkeypatch):
    store, sessions = JobStore(), SessionStore()
    pipeline = Pipeline(store, workers=1, ingest=False)
    monkeypatch.setattr(housekeeping, 'orfs_flow_dir', lambda: None)
    return store, sessions, pipeline, DeletionManager(store, sessions, pipeline)


def finished(store, home, *, source=None, sid=None):
    jid = store.create_job('test', {'sources': [{'path': str(source)}] if source else [], 'session_id': sid}, [{'name': 'd'}])
    project = home / 'workspace' / jid / ('d__' + jid)
    project.mkdir(parents=True)
    (project / 'generated.dat').write_bytes(b'output')
    store.set_design(jid, 'd', project=str(project), status='passed')
    store.set_job(jid, 'done')
    return jid, project


def test_removes_all_owned_outputs_preserves_input_other_job_and_symlink_target(monkeypatch, r2g_home, tmp_path):
    s, sessions, pipe, manager = setup(monkeypatch)
    source = tmp_path / 'original.v'; source.write_text('module original; endmodule')
    jid, project = finished(s, r2g_home, source=source)
    other, other_project = finished(s, r2g_home, source=source)
    (project / 'input-link').symlink_to(source)
    dataset = r2g_home / 'datasets'; dataset.mkdir()
    for suffix in ('.tar.gz', '.tar.tmp', '_partial.tar.gz', '_partial.tar.tmp'):
        (dataset / f'r2g_dataset_{jid}{suffix}').write_bytes(b'output')
    per = dataset / 'per_design' / jid; per.mkdir(parents=True); (per / 'd.tar.gz').write_bytes(b'output')
    flow = tmp_path / 'flow'; flow.mkdir()
    monkeypatch.setattr(housekeeping, 'orfs_flow_dir', lambda: flow)
    meta = project / 'backend/RUN_1/run-meta.json'; meta.parent.mkdir(parents=True)
    meta.write_text(json.dumps({'flow_variant': project.name, 'platform': 'nangate45', 'design_name': 'd',
                                'orfs_results': str(tmp_path / 'untrusted/results')}))
    orfs = flow / 'results/nangate45/d' / project.name; orfs.mkdir(parents=True); (orfs / 'final').write_text('x')
    manager.request('job', jid); manager.process_once()
    assert manager.request('job', jid)['status'] == 'done'  # idempotent retry
    assert not s.job(jid) and not s.events(jid) and not project.exists() and not orfs.exists()
    assert not list(dataset.glob(f'*{jid}*')) and not per.exists()
    assert source.exists() and other_project.exists() and s.job(other)
    pipe.pool.shutdown()


def test_confirmation_required_at_http_boundary(monkeypatch):
    from fastapi.testclient import TestClient
    from r2g_agent import server, config
    monkeypatch.setattr(housekeeping, 'orfs_flow_dir', lambda: None)
    server.STATE.clear(); server.init_runtime(config.Config(ingest=False))
    monkeypatch.setattr(server.STATE['deletions'], 'start', lambda: None)
    client = TestClient(server.app)
    jid = server.tools.RT.store.create_job('queued', {}, [{'name': 'd'}])
    for body in ({}, {'confirm': False}, {'confirm': 'true'}):
        assert client.request('DELETE', '/api/jobs/' + jid, json=body).status_code == 400
    assert server.tools.RT.store.design(jid, 'd')['status'] == 'pending'
    assert client.request('DELETE', '/api/jobs/' + jid, json={'confirm': True}).status_code == 202
    server.STATE['deletions'].process_once()
    assert client.get('/api/jobs/' + jid).status_code == 404
    assert client.get('/api/deletions/job/' + jid).json()['status'] == 'done'
    server.tools.RT.pipeline.pool.shutdown()


def test_waits_for_artifact_writer_before_removing_files(monkeypatch, r2g_home):
    s, sessions, pipe, manager = setup(monkeypatch)
    jid, project = finished(s, r2g_home)
    manager.request('job', jid)
    with resource_lock('job', jid):
        manager.process_once()
        assert s.job(jid) and project.exists()
    manager.process_once()
    assert not project.exists()
    pipe.pool.shutdown()


def test_session_cascade_deletes_created_jobs_not_queried_jobs(monkeypatch, r2g_home):
    s, sessions, pipe, manager = setup(monkeypatch)
    sid = sessions.create()
    owned, a = finished(s, r2g_home, sid=sid)
    legacy, b = finished(s, r2g_home)
    unrelated, c = finished(s, r2g_home)
    sessions.save(sid, [
        {'role': 'assistant', 'tool_calls': [{'id': 'a', 'name': 'start_job'}, {'id': 'b', 'name': 'job_status'}]},
        {'role': 'tool', 'tool_call_id': 'a', 'content': json.dumps({'job_id': legacy})},
        {'role': 'tool', 'tool_call_id': 'b', 'content': json.dumps({'job_id': unrelated})},
    ], 'test')
    manager.request('session', sid)
    # Simulate restart while deletion is pending.
    manager = DeletionManager(s, sessions, pipe)
    for _ in range(4): manager.process_once()
    assert not sessions.exists(sid) and sessions.is_deleting(sid)
    assert not s.job(owned) and not s.job(legacy) and s.job(unrelated)
    assert not a.exists() and not b.exists() and c.exists()
    pipe.pool.shutdown()


def test_rejects_symlink_parent_and_preserves_record_on_cleanup_error(monkeypatch, r2g_home, tmp_path):
    s, sessions, pipe, manager = setup(monkeypatch)
    jid, project = finished(s, r2g_home)
    external = tmp_path / 'external'; external.mkdir()
    (r2g_home / 'datasets').symlink_to(external, target_is_directory=True)
    manager.request('job', jid); manager.process_once()
    assert s.deletion('job', jid)['status'] == 'error'
    assert s.job(jid) and project.exists() and external.exists()
    pipe.pool.shutdown()


def test_persistent_process_uncertainty_prevents_deletion(monkeypatch, r2g_home):
    s, sessions, pipe, manager = setup(monkeypatch)
    jid, project = finished(s, r2g_home)
    s.set_design(jid, 'd', detail={'pgid': 123, 'execution_id': 'marker', 'process_start': '1'})
    monkeypatch.setattr('r2g_agent.deletion.execution_members', lambda *a: [123])
    monkeypatch.setattr('r2g_agent.deletion.stop_orphan', lambda *a, **k: False)
    manager.request('job', jid); manager.process_once()
    assert project.exists() and s.job(jid) and s.deletion('job', jid)['status'] == 'pending'
    pipe.pool.shutdown()


def test_deleted_session_does_not_call_model_or_tools(monkeypatch, fake_adapter):
    s, sessions, pipe, manager = setup(monkeypatch)
    sid = sessions.create(); manager.request('session', sid)
    adapter = fake_adapter([])
    list(run_turn(adapter, sessions, sid, 'run'))
    assert not adapter.seen
    pipe.pool.shutdown()


def test_generated_inputs_referenced_by_another_job_are_protected(monkeypatch, r2g_home):
    s, sessions, pipe, manager = setup(monkeypatch)
    jid, project = finished(s, r2g_home)
    finished(s, r2g_home, source=project / 'generated.dat')
    manager.request('job', jid); manager.process_once()
    assert s.deletion('job', jid)['status'] == 'error' and project.exists()
    pipe.pool.shutdown()


def test_session_deletion_orders_dependent_jobs_and_retries_children(monkeypatch, r2g_home, tmp_path):
    s, sessions, pipe, manager = setup(monkeypatch)
    sid = sessions.create()
    parent, project = finished(s, r2g_home, sid=sid)
    child, _ = finished(s, r2g_home, source=project / 'generated.dat', sid=sid)
    external = tmp_path / 'external'; external.mkdir()
    (r2g_home / 'datasets').symlink_to(external, target_is_directory=True)
    manager.request('session', sid)
    for _ in range(4): manager.process_once()
    assert s.deletion('session', sid)['status'] == 'error'
    assert s.job(parent) and s.job(child)
    (r2g_home / 'datasets').unlink()
    manager.request('session', sid)
    for _ in range(6): manager.process_once()
    assert s.deletion('session', sid)['status'] == 'done'
    assert not s.job(parent) and not s.job(child) and not sessions.exists(sid)
    pipe.pool.shutdown()


def test_deletes_interrupted_orfs_outputs_without_run_metadata(monkeypatch, r2g_home, tmp_path):
    s, sessions, pipe, manager = setup(monkeypatch)
    jid, project = finished(s, r2g_home)
    flow = tmp_path / 'flow'; flow.mkdir()
    monkeypatch.setattr(housekeeping, 'orfs_flow_dir', lambda: flow)
    owned, unrelated = [], []
    for kind in ORFS_ARTIFACT_KINDS:
        root = flow / kind / 'nangate45' / 'different_top'
        for name, destinations in ((project.name, owned), (project.name + '_other', unrelated)):
            variant = root / name; variant.mkdir(parents=True)
            (variant / 'generated.dat').write_text(kind)
            destinations.append(variant)
    manager.request('job', jid); manager.process_once()
    assert s.deletion('job', jid)['status'] == 'done' and not s.job(jid)
    assert all(not path.exists() for path in owned)
    assert all(path.exists() for path in unrelated)
    pipe.pool.shutdown()


def test_metadata_rejects_orfs_symlink_parent_before_deleting_anything(monkeypatch, r2g_home, tmp_path):
    s, sessions, pipe, manager = setup(monkeypatch)
    jid, project = finished(s, r2g_home)
    (project / 'metadata.json').write_text(json.dumps({'platform': 'nangate45', 'design_name': 'd'}))
    flow = tmp_path / 'flow'; (flow / 'designs/nangate45').mkdir(parents=True)
    external = tmp_path / 'external'; external.mkdir()
    (external / project.name).mkdir()
    (flow / 'designs/nangate45/d').symlink_to(external, target_is_directory=True)
    monkeypatch.setattr(housekeeping, 'orfs_flow_dir', lambda: flow)
    manager.request('job', jid); manager.process_once()
    assert s.deletion('job', jid)['status'] == 'error'
    assert project.exists() and (external / project.name).exists() and s.job(jid)
    pipe.pool.shutdown()


def test_orfs_variant_symlink_is_unlinked_without_removing_its_target(monkeypatch, r2g_home, tmp_path):
    s, sessions, pipe, manager = setup(monkeypatch)
    jid, project = finished(s, r2g_home)
    flow = tmp_path / 'flow'; root = flow / 'designs/nangate45/d'; root.mkdir(parents=True)
    external = tmp_path / 'original'; external.mkdir(); (external / 'keep.v').write_text('input')
    link = root / project.name; link.symlink_to(external, target_is_directory=True)
    monkeypatch.setattr(housekeeping, 'orfs_flow_dir', lambda: flow)
    manager.request('job', jid); manager.process_once()
    assert s.deletion('job', jid)['status'] == 'done' and not link.is_symlink()
    assert (external / 'keep.v').read_text() == 'input'
    pipe.pool.shutdown()


def test_cleanup_must_verify_absence_before_marking_done(monkeypatch, r2g_home):
    s, sessions, pipe, manager = setup(monkeypatch)
    jid, project = finished(s, r2g_home)
    monkeypatch.setattr('r2g_agent.deletion.shutil.rmtree', lambda *a, **k: None)
    manager.request('job', jid); manager.process_once()
    assert s.deletion('job', jid)['status'] == 'error'
    assert '仍存在任务产物' in s.deletion('job', jid)['error']
    assert project.exists() and s.job(jid)
    pipe.pool.shutdown()


def test_other_jobs_orfs_generated_inputs_prevent_deletion(monkeypatch, r2g_home, tmp_path):
    s, sessions, pipe, manager = setup(monkeypatch)
    jid, project = finished(s, r2g_home)
    flow = tmp_path / 'flow'; output = flow / 'results/nangate45/d' / project.name
    output.mkdir(parents=True); source = output / 'generated.v'; source.write_text('generated')
    finished(s, r2g_home, source=source)
    monkeypatch.setattr(housekeeping, 'orfs_flow_dir', lambda: flow)
    manager.request('job', jid); manager.process_once()
    assert s.deletion('job', jid)['status'] == 'error' and project.exists() and source.exists()
    pipe.pool.shutdown()


def test_completed_deletion_retry_does_not_blindly_remove_new_files(monkeypatch, r2g_home, tmp_path):
    s, sessions, pipe, manager = setup(monkeypatch)
    jid, project = finished(s, r2g_home)
    manager.request('job', jid); manager.process_once()
    flow = tmp_path / 'flow'; remnant = flow / 'designs/nangate45/d' / project.name
    remnant.mkdir(parents=True); (remnant / 'config.mk').write_text('review before cleanup')
    monkeypatch.setattr(housekeeping, 'orfs_flow_dir', lambda: flow)
    # Deleted rows no longer prove process identity or source relationships.
    # Discovery supports an explicit audited migration, not a blind API retry.
    assert remnant in artifact_paths({'id': jid})
    assert manager.request('job', jid)['status'] == 'done'
    manager.process_once()
    assert remnant.exists()
    pipe.pool.shutdown()
