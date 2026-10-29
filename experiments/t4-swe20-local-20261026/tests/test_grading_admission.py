import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
PREP=ROOT.parent/'t4-swe-agent-adaptation-20261026'
os.environ['LITELLM_LOCAL_MODEL_COST_MAP']='True'
sys.path[:0]=[str(ROOT/'adapters'),str(PREP/'sources/SWE-agent')]


def task():
    return {'repo':'psf/requests','version':'2.27','FAIL_TO_PASS':'["test_new"]',
            'PASS_TO_PASS':'["test_existing"]'}


def test_grade_requires_both_target_and_regression():
    from official import parse_and_grade
    assert parse_and_grade(task(),'PASSED test_new\nPASSED test_existing\n')['status']=='PASS'
    assert parse_and_grade(task(),'FAILED test_new\nPASSED test_existing\n')['status']=='FAIL'
    assert parse_and_grade(task(),'PASSED test_new\nFAILED test_existing\n')['status']=='FAIL'


def test_missing_or_unrun_cases_never_pass():
    from official import parse_and_grade
    for text in ['','PASSED test_new\n','all good exit 0','PASSED unrelated\n']:
        assert parse_and_grade(task(),text)['status']=='UNKNOWN'


def test_unknown_provider_usage_is_not_zero(tmp_path):
    from run_swe import collect_usage
    from bridge import Journal
    class Provider:
        journal=Journal(tmp_path/'events.jsonl')
    p=Provider()
    p.journal.append('request_started',request_local_id='1')
    p.journal.append('request_ended',request_local_id='1',usage='UNKNOWN')
    v=collect_usage(p)
    assert v['requests']==1 and v['unknown_requests']==1
    assert v['actual_total_tokens'] is None and v['known_tokens']==0


def test_batch_unknown_pause_refuses_before_transport(tmp_path,monkeypatch):
    import run_swe
    monkeypatch.setattr(run_swe,'ROOT',tmp_path)
    (tmp_path/'reports').mkdir()
    (tmp_path/'reports/UNKNOWN-PAUSE.json').write_text('{}')
    provider=run_swe.BatchProvider(tmp_path/'provider','OFFLINE-FAKE-NOT-A-KEY')
    import pytest
    with pytest.raises(run_swe.UnknownUsage):
        provider.request({'messages':[]},transport=lambda *a,**k:(_ for _ in ()).throw(AssertionError('must not send')))
    rows=[json.loads(s) for s in provider.journal.path.read_text().splitlines()]
    assert all(r['event']!='request_started' for r in rows)


def test_portable_recipe_never_accepts_terms_or_uses_default_conda_channel():
    from provision import local_dependency_recipe
    original=["cat <<'EOF_59812759871' > /root/environment.yml\n"
              'name: testbed\ndependencies:\n  - pip:\n      - pytest==8.3.3\nEOF_59812759871']
    commands=local_dependency_recipe({'python':'3.9'},original)
    text='\n'.join(commands)
    assert 'tos accept' not in text and 'repo.anaconda.com' not in text
    assert 'pytest==8.3.3' in text and 'python3.9' in text


def test_task_start_snapshot_excludes_later_writes(tmp_path):
    from bridge import FullMemory
    memory=FullMemory('swe-agent',root=tmp_path)
    episode=memory.arm_root/'episodes/task';episode.mkdir()
    try:
        conn,_=memory.backend._open()
        conn.execute('CREATE TABLE unit_fixture_marker (n INTEGER)')
        conn.commit()
        receipt=memory.bind_task_snapshot(episode)
        conn.execute('INSERT INTO unit_fixture_marker VALUES (1)');conn.commit()
        read,_=memory.retrieval_backend._open()
        assert read.execute('SELECT COUNT(*) FROM unit_fixture_marker').fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM unit_fixture_marker').fetchone()[0]==1
        assert receipt['snapshot_id']
        from bridge import State
        assert memory.consult(State('fixture','fixture','software','python','issue_tests',
                                    {'tests':{'status':'UNKNOWN'}},{},{}))['mode']=='no_experience'
    finally:
        memory.close()


def test_changed_source_does_not_keep_stale_baseline_failure(tmp_path):
    from bridge import FullMemory
    from host import SoftwareState
    m=FullMemory('swe-agent',arm='native',root=tmp_path)
    value=['']
    state=SoftwareState(m,{'instance_id':'fixture','repo':'fixture','base_commit':'a'},lambda:value[0])
    state.check={'status':'FAIL','scope':'qualified original baseline'}
    try:
        assert state().reports['tests']['status']=='FAIL'
        value[0]='fixture-only diff'
        assert state().reports['tests']['status']=='UNKNOWN'
    finally:
        m.close()


def test_native_patch_is_not_decoded_from_pty_display():
    from run_swe import native_submission
    from types import SimpleNamespace
    raw='diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+pattern="\\r\\n"\n'
    agent=SimpleNamespace(info={'submission':raw})
    assert native_submission(agent)==raw
    assert native_submission(agent).endswith('\n')
    assert '\\r\\n' in native_submission(agent)


def test_missing_submission_cannot_become_an_empty_success():
    from run_swe import native_submission
    from types import SimpleNamespace
    import pytest
    with pytest.raises(ValueError):
        native_submission(SimpleNamespace(info={'submission':None}))
