"""Adaptive TEHM advice, evidence-based withdrawal and unchanged online allowance."""
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import pytest

sys.path[:0]=[str(Path(__file__).parents[1]/'scripts'),str(Path(__file__).parents[1])]
import t2_agent_runtime as R
import t2_agent_memories as M
from test_t2_agent_runtime import fake_runtime
from test_repair_turns import H,ep


def memory(monkeypatch,candidates=()):
    m=object.__new__(M.Memory)
    m.arm='TM0';m.used=[];m.experience_disabled=False;m.guidance_active=False
    m.fallback_reason=None;m.H=None;m.project=None;m.native_failures=0
    def route(*a):
        m.route_state={'knowledge_route':'NO_SKILL'}
        return 'Memory: no applicable validated knowledge.',[]
    m.route_knowledge=route
    m.diagnose=lambda:{'strategies':list(candidates)}
    monkeypatch.setattr(M.T,'event',lambda *a:None)
    return m


@pytest.mark.parametrize('value',['17','31'])
def test_procedural_advice_uses_actual_selected_values_and_does_not_promote(monkeypatch,value):
    candidate={'id':'db-rule-'+value,'source':'tehm_rule',
               'config_edits':{'CORE_UTILIZATION':value},'rerun_from':'floorplan'}
    m=memory(monkeypatch,[{'id':'public-first','source':'catalogue'},candidate])
    text,ids=m.adaptive_context('query',{})
    assert ids==[candidate['id']] and m.guidance_active
    assert m.native_action() is None
    data=json.loads(text)
    assert data['selected_evidence'][0]['reference_action_edits']==candidate['config_edits']
    assert data['automatic_causal_promotion'] is False
    assert 'NOT validated causal' in text and 'Adapt parameters' in text


def test_no_matching_experience_means_empty_context_not_generic_advice(monkeypatch):
    m=memory(monkeypatch,[{'id':'public','source':'catalogue'}])
    assert m.adaptive_context('query',{})==('',[])
    assert not m.guidance_active and not m.experience_disabled


def test_unknown_failure_signature_is_not_a_match_to_all_history(monkeypatch):
    m=memory(monkeypatch)
    m.diagnose=lambda:{'strategies':[{'id':'candidate','source':'tehm_rule'}],
        'tehm_v2':{'state':{'signature':None},'decisions':[{'id':'candidate','evidence':{'support':99}}]}}
    assert m.adaptive_context('query',{})==('',[])


def test_experience_context_bounded_and_does_not_render_unused_history(monkeypatch):
    candidates=[{'id':str(i),'source':'tehm_rule','config_edits':{'CORE_UTILIZATION':str(10+i)}} for i in range(30)]
    m=memory(monkeypatch,candidates)
    text,ids=m.adaptive_context('query',{})
    assert ids==['0','1'] and len(text.encode())<=16000
    assert len(json.loads(text)['selected_evidence'])==2


def test_typed_historical_observations_reach_agent_even_without_induced_rule(tmp_path,monkeypatch):
    import hashlib
    from tehm_v2 import policy as P
    (tmp_path/'constraints').mkdir()
    (tmp_path/'constraints/config.mk').write_text('export CORE_UTILIZATION = 20\n')
    candidate={'id':'public-tool','config_edits':{'CORE_UTILIZATION':'12'},'rerun_from':'floorplan'}
    def entry(ident,sig,family,outcome):
        return {'unit_id':ident,'signature':sig,'signature_type':'drc','ip_family':family,
            'source_group':family,'task':ident,'knob':'CORE_UTILIZATION','direction':'down','outcome':outcome}
    index={'entries':[entry('success','drc:m3.2','independent','admitted_positive'),
                      entry('failed-trial','drc:m3.2','another-source','no_change'),
                      entry('wrong-signature','drc:li.3','independent','admitted_positive'),
                      entry('own-design','drc:m3.2','target-family','admitted_positive')]}
    raw=json.dumps(index).encode();(tmp_path/'index.json').write_bytes(raw)
    policy={'index':str(tmp_path/'index.json'),'index_sha256':hashlib.sha256(raw).hexdigest(),
            'task_ip_family':'target-family'}
    (tmp_path/'policy.json').write_text(json.dumps(policy))
    profile=P.evidence_summary({**candidate,'_moves':P.moves(candidate['config_edits'],{'CORE_UTILIZATION':'20'})},
                              index,'drc:m3.2',{'target-family'})
    summary={k:profile[k] for k in ('support','harm','partial','ineffective','unconfirmed','groups','harm_groups')}
    m=memory(monkeypatch);m.project=tmp_path;m.env={'R2G_TEHM_V2_POLICY':str(tmp_path/'policy.json')}
    m.diagnose=lambda:{'strategies':[candidate],'tehm_v2':{
        'state':{'signature':'drc:m3.2'},'decisions':[{'id':candidate['id'],'evidence':summary}]}}
    text,ids=m.adaptive_context('query',{})
    item=json.loads(text)['selected_evidence'][0]
    assert ids==['public-tool'] and item['kind']=='action_matched_historical_observations'
    assert item['evidence_reference']['selected_unit_ids']==['failed-trial','success']
    assert item['evidence']['support']==1 and item['evidence']['ineffective']==1
    assert item['candidate_source']=='public_catalogue' and 'NOT validated causal' in text
    (tmp_path/'index.json').write_text('{}')
    with pytest.raises(M.IntegrityStop,match='index changed'):m.adaptive_context('query',{})


@pytest.mark.parametrize('row',[
    {'status':'REGRESSION','execution':{'rolled_back':True}},
    {'status':'FAILED_NATIVE','execution':{'rolled_back':True}},
    {'status':'UNVERIFIED','execution':{}},
    {'status':'REJECTED_DECISION'},
    {'status':'RETAINED','execution':{'rolled_back':False,'target_progress':{'improved':False}}},
    {'status':'RETAINED','execution':{'rolled_back':False,'target_progress':{'improved':None}}},
    {'status':'CONSTRAINT_REJECTION','execution':{'rolled_back':True}},
    {'status':'DUPLICATE_NOT_EXECUTED','execution':{'rolled_back':True}},
])
def test_ineffective_or_unverified_advice_switches_off_without_new_call(monkeypatch,row):
    m=memory(monkeypatch);m.guidance_active=True
    m.observe_decision({'sequence':1,**row})
    assert m.experience_disabled
    m.route_knowledge=lambda *a:pytest.fail('do not reintroduce cross-task memory after withdrawal')
    assert m.adaptive_context('new query',{})==('',[])
    assert m.fallback_reason


def test_verified_progress_may_keep_advice_without_claiming_success(monkeypatch):
    m=memory(monkeypatch);m.guidance_active=True
    m.observe_decision({'sequence':1,'status':'RETAINED','execution':{
        'rolled_back':False,'verdict':{'accepted':False},'target_progress':{'improved':True}}})
    assert not m.experience_disabled and m.guidance_active


def test_no_experience_decision_is_not_falsely_attributed_to_memory(monkeypatch):
    m=memory(monkeypatch)
    m.observe_decision({'sequence':1,'status':'FAILED','execution':{'rolled_back':True}})
    assert not m.experience_disabled


@pytest.mark.parametrize('fresh,x,y,expected',[(True,18,7,True),(True,18,18,False),
    (True,18,20,False),(False,18,7,None),(True,None,0,None),(True,18,1e39,None)])
def test_progress_requires_bound_real_measurement(fresh,x,y,expected):
    pre={'signals':{'drc':{'fresh':fresh,'total_violations':x}}}
    post={'signals':{'drc':{'fresh':True,'total_violations':y}}}
    assert R.target_progress(pre,post,'drc')['improved'] is expected


def test_unconstrained_slack_is_never_improvement():
    pre={'signals':{'timing':{'fresh':True,'tier':'unconstrained','wns_ns':-1}}}
    post={'signals':{'timing':{'fresh':True,'tier':'clean','wns_ns':0}}}
    assert R.target_progress(pre,post,'timing')['improved'] is None


def test_agent_can_adapt_advice_then_continue_independently_with_remaining_calls(tmp_path,monkeypatch):
    # Historical 17 is not an executable prescription: the model selects 23.
    response=json.dumps({'operation':'repair','arguments':{'set':{'CORE_UTILIZATION':'23'},'rerun_from':'floorplan'}})
    h,env,calls,events=fake_runtime(tmp_path,monkeypatch,[response])
    m=memory(monkeypatch,[{'id':'learned','source':'tehm_rule','config_edits':{'CORE_UTILIZATION':'17'}}])
    m.agent_context=lambda *a:m.adaptive_context('query',{})[0]
    m.consume=lambda *a:None
    applied=[]
    def execute(*args,**kwargs):
        assert 'native_apply' not in kwargs # no hidden literal rule application
        applied.append(json.loads(args[4]))
        return {'status':'REGRESSION','rolled_back':True,'comparison':{'regressions':['worse']}}
    monkeypatch.setattr(R,'execute_candidate',execute)
    out=R.run(h,tmp_path,'TM0',env,'unused',{'accepted':False},lambda p:None,m)
    assert len(calls)==3 and len(applied)==3 and out['stop_reason']=='online_allowance_spent'
    assert all(p['set']['CORE_UTILIZATION']=='23' for p in applied)
    assert 'Selected historical memory' in calls[0][-1]['content']
    assert 'Selected historical memory' not in calls[1][-1]['content']
    assert 'Selected historical memory' not in calls[2][-1]['content']
    assert 'no_experience' in calls[1][-1]['content']
    assert any(msg['role']=='assistant' for msg in calls[1])
    assert any('REGRESSION' in msg['content'] for msg in calls[1] if msg['role']=='user')


def test_real_serialized_requests_meter_adaptation_and_no_experience_fallback(ep,monkeypatch):
    import os
    import t2a_runtime as T
    import t2_agent_dispatch as D
    from test_t2a_runtime import manifest
    from test_repair_turns import OK,_Resp,_spent
    p,calls,_=ep;env=manifest(p);(p/'backend').mkdir();(p/'shared').mkdir()
    (p/'shared/manifest.json').write_text(json.dumps({'units_sha256':'eligible-source-digest'}))
    Path(os.environ['R2G_BUDGET_FILE']).write_text(json.dumps(D.episode_budget()))
    monkeypatch.setattr(T,'recover_execution',lambda *a:None)
    monkeypatch.setattr(H,'observation',lambda p:{'drc':18})
    monkeypatch.setattr(R,'PublicTools',lambda *a:SimpleNamespace(discover=lambda:[],offered={}))
    monkeypatch.setattr(R,'execute_candidate',lambda *a,**kw:{'status':'REGRESSION','rolled_back':True})
    def transport(request,timeout=0):
        data=json.loads(request.data);calls.append(data)
        content=json.dumps({'operation':'repair','arguments':{
            'set':{'CORE_UTILIZATION':'23'},'rerun_from':'floorplan'}})
        return _Resp(json.dumps({**OK,'choices':[{'message':{'content':content}}]}).encode())
    monkeypatch.setattr(H.urllib.request,'urlopen',transport)
    m=object.__new__(M.Memory)
    m.H=H;m.project=p;m.root=p;m.arm='TM0';m.helpers=0;m.used=[];m.native_failures=0
    m.receipt=None;m.experience_disabled=False;m.guidance_active=False;m.fallback_reason=None
    m.inner=SimpleNamespace(last_receipt={'decision':{'decision':'NO_SKILL'}})
    m.current_situation=lambda:{}
    def route(*a):
        m.route_state={'knowledge_route':'NO_SKILL'}
        return '',[]
    m.route_knowledge=route
    m.diagnose=lambda:{'strategies':[{'id':'learned-17','source':'tehm_rule',
                                    'config_edits':{'CORE_UTILIZATION':'17'}}]}
    out=R.run(H,p,'TM0',env,'unused',{'accepted':False},lambda p:None,m)
    assert len(calls)==3 and _spent(p,'llm_requests')==3 and _spent(p,'tokens_in')==150
    assert len(out['decisions'])==3
    assert 'learned-17' in calls[0]['messages'][-1]['content']
    assert all('Selected historical memory' not in c['messages'][-1]['content'] for c in calls[1:])
    events=[json.loads(line) for line in (p/'reports/resource_ledger.jsonl').read_text().splitlines()]
    receipts=[e for e in events if e['type']=='memory_agent_context']
    assert [e['mode'] for e in receipts]==['experience_assisted','no_experience','no_experience']
    assert sum(e['type']=='memory_fallback' for e in events)==1
    assert sum(e['type']=='memory_consumption' for e in events)==3
