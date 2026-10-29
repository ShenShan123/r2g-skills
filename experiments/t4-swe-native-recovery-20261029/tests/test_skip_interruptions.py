import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'adapters'),str(ROOT/'tests')]
from test_continuation import fixture,save


def enable_policy(root):
    pointer=root/'inputs/active-registration.json'
    active=json.loads(pointer.read_text())
    path=root/active['path']
    reg=json.loads(path.read_text())
    auth=root/reg['continuation_authorization']
    content=json.loads(auth.read_text())
    content['interruption_policy']='skip_local_interruptions_no_retry'
    content['actual_unknown_usage']='UNKNOWN, never zero or an estimated actual value'
    save(auth,content)
    from admission import sha
    reg['interruption_policy']='skip_local_interruptions_no_retry'
    reg['files'][str(auth.relative_to(root))]=sha(auth)
    save(path,reg)
    save(pointer,{'path':str(path.relative_to(root)),'sha256':sha(path)})
    return reg


def test_unknown_default_remains_fail_closed(tmp_path):
    fixture(tmp_path)
    save(tmp_path/'hosts/swe-agent/tehm/episodes/task-1/provider/requests.jsonl',
         {'event':'request_ended','request_local_id':'new','usage':'UNKNOWN'})
    from admission import admission_blockers
    assert admission_blockers(tmp_path)


def test_authorized_unknown_is_reported_but_not_a_global_stop(tmp_path):
    fixture(tmp_path)
    enable_policy(tmp_path)
    path=tmp_path/'hosts/swe-agent/tehm/episodes/task-1/provider/requests.jsonl'
    save(path,{'event':'request_ended','request_local_id':'new','usage':'UNKNOWN'})
    before=path.read_bytes()
    from admission import admission_blockers,unresolved_unknowns
    assert admission_blockers(tmp_path)==[]
    assert unresolved_unknowns(tmp_path)[0]['request_local_id']=='new'
    assert path.read_bytes()==before


def test_global_identity_fault_still_blocks(tmp_path):
    fixture(tmp_path)
    enable_policy(tmp_path)
    save(tmp_path/'reports/TECHNICAL-PAUSE.json',{'reason':'unapproved response model'})
    from admission import admission_blockers
    assert admission_blockers(tmp_path)


def test_policy_authorization_cannot_be_changed_after_freeze(tmp_path):
    fixture(tmp_path)
    reg=enable_policy(tmp_path)
    (tmp_path/reg['continuation_authorization']).write_text('{}')
    from admission import admission_blockers
    with pytest.raises(ValueError,match='authorization hash'):
        admission_blockers(tmp_path)


def test_unknown_stops_affected_provider_not_other_lane(tmp_path,monkeypatch):
    fixture(tmp_path)
    enable_policy(tmp_path)
    import run_swe
    monkeypatch.setattr(run_swe,'ROOT',tmp_path)
    class Response:
        headers={}
        status=200
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def read(self):
            return json.dumps({'model':'deepseek-flash','id':'offline',
                'usage':{'prompt_tokens':2,'completion_tokens':1,'total_tokens':3}}).encode()
    bad=run_swe.BatchProvider(tmp_path/'hosts/swe-agent/native/episodes/task-1/provider','OFFLINE')
    good=run_swe.BatchProvider(tmp_path/'hosts/swe-agent/tehm/episodes/task-1/provider','OFFLINE')
    calls=[]
    def failed(*args,**kwargs):
        calls.append(1)
        raise OSError('offline transport fixture')
    with pytest.raises(run_swe.UnknownUsage):
        bad.request({'messages':[]},transport=failed)
    with pytest.raises(run_swe.UnknownUsage):
        bad.request({'messages':[]},transport=failed)
    assert len(calls)==1 # No retry inside an interrupted episode.
    assert good.request({'messages':[]},transport=lambda *a,**k:Response())['id']=='offline'
    assert run_swe.collect_usage(bad)['actual_total_tokens'] is None
    assert run_swe.collect_usage(good)['actual_total_tokens']==3


def test_authorized_second_pair_not_rerun_and_remaining_36_selected(tmp_path):
    tasks,reg,_,auth=fixture(tmp_path)
    for arm in ('native','tehm'):
        save(tmp_path/f'hosts/swe-agent/{arm}/episodes/task-1/result.json',
             {'status':'ACCOUNTING_FAILURE'})
    data=json.loads(auth.read_text())
    data['preserved_tasks']=['task-0','task-1']
    data['remaining_episodes']=[[t['instance_id'],a] for t in tasks[2:] for a in ('native','tehm')]
    save(auth,data)
    from batch import continuation_plan
    assert continuation_plan(tmp_path,reg,tasks)==tasks[2:]


def test_provider_credential_permission_fault_not_treated_as_transient(tmp_path,monkeypatch):
    import io
    import urllib.error
    fixture(tmp_path)
    enable_policy(tmp_path)
    import run_swe
    monkeypatch.setattr(run_swe,'ROOT',tmp_path)
    provider=run_swe.BatchProvider(tmp_path/'hosts/swe-agent/native/episodes/task-1/provider','OFFLINE')
    def forbidden(*a,**k):
        raise urllib.error.HTTPError('https://api.deepseek.com/chat/completions',401,
                                     'offline authorization fixture',{},io.BytesIO(b'fixture'))
    with pytest.raises(run_swe.UnknownUsage):
        provider.request({'messages':[]},transport=forbidden)
    from admission import admission_blockers
    assert (tmp_path/'reports/TECHNICAL-PAUSE.json').exists()
    assert admission_blockers(tmp_path)


def test_dispatch_continues_after_local_interrupt_and_skips_unqualified(tmp_path,monkeypatch):
    tasks,reg,_,auth=fixture(tmp_path)
    reg=enable_policy(tmp_path)
    for task in tasks[1:]:
        save(tmp_path/'qualification'/task['instance_id']/'qualification.json',
             {'status':'BLOCKED' if task['instance_id']=='task-2' else 'PASS','reason':'offline environment fixture'})
    # Real raw first-pair receipts must have task/arm keys, as actual records do.
    for arm in ('native','tehm'):
        save(tmp_path/f'hosts/swe-agent/{arm}/episodes/task-0/result.json',
             {'task':'task-0','arm':arm,'status':'ACCOUNTING_FAILURE'})
    launched=[]
    def popen(args,**kwargs):
        task=args[args.index('--task')+1];arm=args[args.index('--arm')+1]
        launched.append((task,arm))
        save(tmp_path/f'hosts/swe-agent/{arm}/episodes/{task}/result.json',
             {'task':task,'arm':arm,'status':'ACCOUNTING_FAILURE' if task=='task-1' else 'NOT_ACCEPTED'})
        return SimpleNamespace(pid=10,returncode=0,wait=lambda:0)
    import batch,run_swe
    monkeypatch.setattr(batch,'ROOT',tmp_path)
    monkeypatch.setattr(batch,'roster',lambda:tasks)
    monkeypatch.setattr(batch.subprocess,'Popen',popen)
    monkeypatch.setattr(run_swe,'check_freeze',lambda:reg)
    batch._run(True)
    assert ('task-1','native') in launched and ('task-19','tehm') in launched
    assert not any(t in {'task-0','task-2'} for t,_ in launched)
    assert len(launched)==36
    assert not (tmp_path/'hosts/swe-agent/native/episodes/task-2').exists()
    state=json.loads((tmp_path/'reports/batch-state.json').read_text())
    assert state['status']=='COMPLETE_WITH_INTERRUPTIONS'
    rows=batch.summary()
    assert [r['status'] for r in rows if r['task']=='task-2']==['QUALIFICATION_BLOCKED']*2
