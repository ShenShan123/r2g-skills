import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import t2_agent_runtime as R
import t2_agent_dispatch as D


@pytest.mark.parametrize('payload', ['{}','null','[]','{"operation":"shell","arguments":{}}',
    '{"operation":"catalogue","arguments":{"hidden":"edit"}}'])
def test_invalid_tool_decisions_are_rejected(payload):
    assert R.decode(payload)[0] is None


def test_agent_is_offered_tools_without_old_cannot_run_tools_prompt():
    h=SimpleNamespace(executable_knobs=lambda m:['CORE_UTILIZATION','PLACE_DENSITY'])
    text=R.instructions(h,'sky130hd',{})
    assert 'You cannot run tools' not in text
    assert 'catalogue' in text and 'repair' in text and 'THREE total' in text


def fake_runtime(tmp_path,monkeypatch,responses):
    (tmp_path/'backend').mkdir()
    manifest=tmp_path/'manifest.json';manifest.write_text('{}')
    calls=[];events=[]
    class Gateway:
        def __init__(self,*a):self.sent=0
        def remaining(self):return 3-self.sent
        def call(self,messages,prefix,**kwargs):
            if self.remaining()==0:raise D.OnlineAllowanceSpent()
            self.sent+=1;calls.append(messages)
            return responses[min(self.sent-1,len(responses)-1)],{'repair_turn_id':str(self.sent)}
    monkeypatch.setattr(D,'OnlineGateway',Gateway)
    monkeypatch.setattr(R.T,'recover_execution',lambda *a:None)
    monkeypatch.setattr(R.T,'public_task_contract',lambda m:{})
    monkeypatch.setattr(R.T,'raise_on_technical_failure',lambda *a:None)
    monkeypatch.setattr(R.T,'event',lambda h,p,k,v:events.append((k,v)))
    monkeypatch.setattr(R,'PublicTools',lambda *a:SimpleNamespace(discover=lambda:[],offered={}))
    monkeypatch.setattr(R.AV,'attach_failure',lambda p,e,v:v)
    monkeypatch.setitem(sys.modules,'result_vector',SimpleNamespace(capture=lambda p:{}))
    h=SimpleNamespace(CFG={'platform':'sky130hd'},SKILL=tmp_path,_task_failure=lambda p:('drc','m3.2',5),observation=lambda p:{'failure':'DRC'},
        executable_knobs=lambda m:['CORE_UTILIZATION'],
        run_engineer=lambda *args:pytest.fail('unselected public repair must not run'))
    return h,{'R2G_TASK_MANIFEST':str(manifest)},calls,events


def test_first_repair_can_be_llm_selected_without_running_catalogue(tmp_path,monkeypatch):
    decision=json.dumps({'operation':'repair','arguments':{'set':{'CORE_UTILIZATION':'45'},'rerun_from':'floorplan'}})
    h,env,calls,events=fake_runtime(tmp_path,monkeypatch,[decision])
    monkeypatch.setattr(R,'execute_candidate',lambda *a:{'status':'RETAINED','rolled_back':False,
                                                        'verdict':{'accepted':True}})
    result=R.run(h,tmp_path,'NB',env,'unused',{'accepted':False},lambda p:None)
    assert len(calls)==1 and result['last']['accepted']
    assert [v['tool'] for k,v in events if k=='agent_tool_selected']==['repair']


def test_catalogue_runs_only_when_the_agent_requests_it(tmp_path,monkeypatch):
    h,env,calls,events=fake_runtime(tmp_path,monkeypatch,['{"operation":"catalogue","arguments":{}}'])
    selected=[]
    def catalogue(project,arm,tool_env):
        selected.append(arm)
        assert tool_env['R2G_MEMORY_BACKEND']=='none'
        return {'accepted':True}
    h.run_engineer=catalogue
    result=R.run(h,tmp_path,'NB',env,'unused',{'accepted':False},lambda p:None)
    assert len(calls)==1 and selected==['NB'] and result['last']['accepted']


def test_invalid_decisions_consume_the_three_opportunities_without_flows(tmp_path,monkeypatch):
    h,env,calls,events=fake_runtime(tmp_path,monkeypatch,['invalid'])
    monkeypatch.setattr(R,'execute_candidate',lambda *a:pytest.fail('no candidate'))
    result=R.run(h,tmp_path,'NB',env,'unused',{'accepted':False},lambda p:None)
    assert len(calls)==3 and result['stop_reason']=='online_allowance_spent'
    assert all(d['status']=='REJECTED_DECISION' for d in result['decisions'])


def test_auxiliary_memory_requests_share_the_host_allowance(tmp_path,monkeypatch):
    h,env,calls,events=fake_runtime(tmp_path,monkeypatch,['invalid'])
    class Memory:
        def agent_context(self,observation,gateway):
            gateway.call([],tmp_path/'aux',purpose='query')
            return 'memory text'
    result=R.run(h,tmp_path,'AMEM',env,'unused',{'accepted':False},lambda p:None,Memory())
    assert len(calls)==3 and len(result['decisions'])==1
    assert result['stop_reason']=='online_allowance_spent'


def test_missing_native_adapter_cannot_be_silently_substituted(tmp_path,monkeypatch):
    h,env,_,_=fake_runtime(tmp_path,monkeypatch,['invalid'])
    with pytest.raises(D.IntegrityStop):
        R.run(h,tmp_path,'REME',env,'unused',{'accepted':False},lambda p:None)


@pytest.mark.parametrize('regression,unknown,should_restore',[(False,False,False),(True,False,True),(False,True,True)])
def test_real_candidate_boundary_keeps_regression_and_unknown_rollback(tmp_path,monkeypatch,
        regression,unknown,should_restore):
    (tmp_path/'constraints').mkdir();(tmp_path/'constraints/config.mk').write_text('')
    restores=[];flows=[]
    monkeypatch.setitem(sys.modules,'result_vector',SimpleNamespace(
        capture=lambda p:{},compare=lambda *a:{'unknowns':['missing'] if unknown else [],
                                              'regressions':['worse'] if regression else []}))
    monkeypatch.setattr(R.T,'snapshot_state',lambda *a,**kw:tmp_path/'snapshot')
    monkeypatch.setattr(R.J,'begin',lambda *a:{'action_id':'fixture#1'})
    monkeypatch.setattr(R.J,'finish',lambda *a:None)
    monkeypatch.setattr(R.T,'candidate_execution_identity',lambda *a:None)
    monkeypatch.setattr(R.T,'restore',lambda *a,**kw:restores.append(kw))
    monkeypatch.setattr(R.E,'check_config',lambda *a,**kw:{'ok':True})
    h=SimpleNamespace(SKILL=tmp_path,FLOW=tmp_path,CFG={'platform':'sky130hd'},
        _task_failure=lambda p:('drc','m3.2',5),apply_llm=lambda *a,**kw:('place',{}),
        run=lambda cmd,*args:flows.append(cmd) or 0,
        final_verdict=lambda *a:{'accepted':False,'acceptance':{},'envelope':{'status':'PASS'}})
    row=R.execute_candidate(h,tmp_path,{}, {},'{}',{'repair_turn_id':'1'},1,{})
    assert row['flow_launched'] and len(flows)==2
    assert bool(restores)==should_restore and row['rolled_back']==should_restore
    if should_restore:assert restores==[{'execution_changed':True}]


def test_frozen_worker_integration_replaces_only_deterministic_first_branch():
    from t2_agent_nb_package import integrate_batch
    root=Path(__file__).resolve().parents[2]
    text=integrate_batch((root/'memory/scripts/t2a_batch.py').read_text())
    compile(text,'frozen-agent-batch','exec')
    segment=text[text.index("        if arm != 'NB':raise"):text.index("        final=AV.terminal_verdict")]
    assert 'run_agent(' in segment and 'H.run_engineer(' not in segment
    assert 'T.strict_turns(' not in segment and "['llm_requests']>3" in text
