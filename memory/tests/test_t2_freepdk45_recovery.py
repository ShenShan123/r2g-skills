import json
import ast
import fcntl
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
import t2_freepdk45_recovery as C


def fixture(tmp_path):
    p=tmp_path/'A__initial__NB__r1';(p/'backend/RUN_0').mkdir(parents=True)
    (p/'constraints').mkdir();(p/'reports').mkdir()
    (p/'constraints/config.mk').write_text('export CORE_UTILIZATION = 20\n')
    C.save(p/'manifest.json',{'initial_config_sha256':C.sha(p/'constraints/config.mk')})
    C.save(p/'episode.json',{'arm':'NB','status':'UNVERIFIED_OR_SAFETY_STOP',
        'error':'initial reference: floorplan not successfully measured',
        'accounting':{'passed':True,'llm_requests':0}})
    (p/'reports/resource_ledger.jsonl').write_text(json.dumps({'type':'flow_run','rc':2})+'\n')
    return p


def test_recovery_only_reuses_the_exact_pre_model_initial_failure(tmp_path):
    p=fixture(tmp_path);assert C.check_initial_checkpoint(p)['accounting']['llm_requests']==0
    for changes in ({'status':'ACCEPTED'},{'arm':'TM0'}, {'error':'other error'},
                    {'accounting':{'passed':True,'llm_requests':1}}):
        old=C.load(p/'episode.json');C.save(p/'episode.json',{**old,**changes})
        with pytest.raises(ValueError):C.check_initial_checkpoint(p)
        C.save(p/'episode.json',old)


def test_recovery_never_reuses_a_changed_config_or_multiple_runs(tmp_path):
    p=fixture(tmp_path);(p/'constraints/config.mk').write_text('changed')
    with pytest.raises(ValueError):C.check_initial_checkpoint(p)


def test_only_never_started_jobs_are_dispatched(tmp_path):
    root=tmp_path/'nb';(root/'episodes').mkdir(parents=True)
    jobs=[{'root':str(root),'task':a+'/initial','arm':'NB'} for a in 'ABC']
    p=root/'episodes/A__initial__NB__r1';p.mkdir();C.save(p/'episode.json',{'status':'ACCEPTED'})
    assert C.never_started(jobs)==jobs[1:]
    (root/'episodes/B__initial__NB__r1').mkdir()
    with pytest.raises(ValueError,match='partial'):C.never_started(jobs)


def test_unfinished_episode_cannot_be_silently_skipped(tmp_path):
    root=tmp_path/'nb';p=root/'episodes/A__initial__NB__r1';p.mkdir(parents=True)
    C.save(p/'episode.json',{'status':'UNVERIFIED_OR_SAFETY_STOP'})
    with pytest.raises(ValueError,match='unresolved'):C.never_started([{'root':str(root),'task':'A/initial','arm':'NB'}])


def test_integrated_worker_has_no_repeated_initial_flow_and_has_original_costs():
    root=Path('/data1/zhangdy/tehm-experiments/t2-freepdk45-main50-adamem-20261022-r3')
    text=(root/'nb/code/memory/scripts/t2a_batch.py').read_text()
    out=C.integrate_batch(text)
    compile(out,'integrated_worker','exec')
    assert 'if not resume_initial:\n            rc=H.run(' in out
    assert 'initial_continuation' in out
    assert "prior_record.get('total_worker_wall_s',0)" in out
    assert 'attempt_resume' in out
    with pytest.raises(ValueError):C.integrate_batch('wrong boundary')


def test_execute_real_integrated_checkpoint_branch_without_model_or_eda(tmp_path,monkeypatch):
    """Execute the actual amended episode function; stub only external boundaries."""
    source=Path('/data1/zhangdy/tehm-experiments/t2-freepdk45-main50-adamem-20261022-r3/nb/code/memory/scripts/t2a_batch.py')
    tree=ast.parse(C.integrate_batch(source.read_text()))
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='episode')
    root=tmp_path/'nb';project=root/'episodes/A__initial__NB__r1'
    project.parent.mkdir(parents=True);p=fixture(project.parent)
    # fixture produces exactly the registered A project.
    assert p==project
    e=C.load(p/'episode.json');e.update(started=1,total_worker_wall_s=56,harness_cpu_s=2,wall_s=55)
    C.save(p/'episode.json',e)
    manifest=C.load(p/'manifest.json');manifest.update(platform='nangate45',area_reference_policy='test')
    C.save(p/'manifest.json',manifest)
    original=(p/'reports/resource_ledger.jsonl').read_bytes();actions=[]
    class Harness:
        _EPISODE={}
        @staticmethod
        def base_env(_):return dict(os.environ)
        @staticmethod
        def run(cmd,*args,**kw):
            assert 'run_orfs.sh' not in str(cmd),'initial flow was repeated'
            actions.append('extract');return 0
        @staticmethod
        def final_verdict(*args):return {'accepted':False}
        @staticmethod
        def llm_key(*args):return 'fake-not-a-secret'
        @staticmethod
        def _ledger(*args):return 0,'{}'
    def event(*args):
        with (p/'reports/resource_ledger.jsonl').open('a') as f:f.write(json.dumps({'type':args[2]})+'\n')
    T=SimpleNamespace(configure_online=lambda _:None,event=event,online_policy=lambda:{},
        accounting_check=lambda *args:{'passed':True,'llm_requests':0,'repair_attempts':0},
        request_token_totals=lambda *args:{},TechnicalFailure=RuntimeError)
    AV=SimpleNamespace(terminal_verdict=lambda *args:{'accepted':True},
                       terminal_status=lambda *args:'ACCEPTED',terminal_reason=lambda *args:'accepted')
    def agent(*args,**kw):
        actions.append('agent');return {'stop_reason':'test','decisions':[]}
    monkeypatch.setitem(sys.modules,'t2_agent_runtime',SimpleNamespace(run=agent))
    monkeypatch.setitem(sys.modules,'t2_agent_memories',SimpleNamespace(make_memory=lambda *args:object()))
    # This import is local at the reference boundary; do not write native evidence in this mock.
    import t2_nb_firstlook as N
    monkeypatch.setattr(N,'resolve_area_reference',lambda _:manifest)
    env=dict(Path=Path,time=time,os=os,sys=sys,json=json,fcntl=fcntl,
             __file__=str(root/'code/memory/scripts/t2a_batch.py'),
             preflight=lambda *args:{'plan':[{'task':'A/initial','arm':'NB','repeat':1}],
                                     'tasks':[{'task':'A/initial','template':str(tmp_path/'template')}]},
             Q=SimpleNamespace(module=lambda *args:Harness,save=C.save,digest=lambda _: 'test'),
             T=T,AV=AV,mark_engineer_restore=lambda *args:None)
    exec(compile(ast.Module(body=[function],type_ignores=[]),'actual_episode_function','exec'),env)
    env['episode'](root,'A/initial','NB',1,resume_initial=True)
    out=C.load(p/'episode.json')
    assert out['status']=='ACCEPTED' and out['initial_continuation']['initial_flow_repeated'] is False
    assert out['total_worker_wall_s']>=56 and out['harness_cpu_s']>=2
    assert actions==['extract','agent']
    assert (p/'reports/resource_ledger.jsonl').read_bytes().startswith(original)
    assert len(list((p/'backend').glob('RUN_*')))==1
