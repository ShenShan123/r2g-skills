"""Offline AdaMEM algorithm/host tests. No provider or EDA calls."""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest

sys.path[:0]=[str(Path(__file__).parents[1]/'scripts'),str(Path(__file__).parents[1])]
import t2_adamem as M
import t2_agent_runtime as R
import t2_agent_dispatch as D
import t2_agent_memories as MEM
from test_repair_turns import ep, H, OK, _Resp, _spent


def unit(i,status='ACCEPTED',session='one'):
    return {'unit_id':str(i),'episode_trace_id':session,'episode_status':status,
        'identity':{'session':session,'platform':'nangate45'},
        'lineage':{'design':session,'platform':'nangate45','project':session},
        'capture_context':{'step_index':i},'text_view':'step-result-'+str(i),
        'raw_row':{'from_stage':'floorplan','strategy':'strategy'+str(i),
                   'situation':{'check':'route','before':i},'config_before':{'CORE_UTILIZATION':str(80-i)},
                   'config_delta':{'CORE_UTILIZATION':str(79-i)},'before':i}}


@pytest.mark.parametrize('status',['NOT_ACCEPTED','ACCOUNTING_FAILURE',None,'UNVERIFIED'])
def test_only_whole_verified_success_enters_long_term(status):
    u=unit(0,status);u['shared_label']={'admitted':True}
    entries,excluded=M.step_entries([u])
    assert entries==[] and excluded[0]['reason']=='not_verified_final_success'


def test_inconsistent_episode_status_is_not_success():
    assert M.step_entries([unit(0),unit(1,'NOT_ACCEPTED')])[0]==[]


def test_index_keys_are_pre_action_and_values_keep_successful_suffix():
    entries,_=M.step_entries([unit(1),unit(0)])
    assert len(entries)==2
    assert entries[0]['past_state']['prior_steps']==[]
    assert 'step-result-0' not in entries[0]['query']
    assert 'step-result-1' not in entries[0]['query']
    assert entries[0]['remaining_trajectory']==['step-result-0','step-result-1']
    assert entries[1]['past_state']['prior_steps']==['step-result-0']
    assert entries[1]['remaining_trajectory']==['step-result-1']
    assert M.step_entries([unit(1),unit(0)])==M.step_entries([unit(0),unit(1)])


def test_no_inferred_pre_state_or_summary_fake_action():
    a,b=unit(0),unit(1);a['raw_row']={'from_stage':None};b['raw_row']={'from_stage':'route'}
    entries,excluded=M.step_entries([a,b])
    assert entries==[] and excluded[0]['reason']=='missing_recorded_pre_state'


def test_global_fix_log_order_beats_reset_session_step_counters():
    a,b=unit(0),unit(1)
    a['identity']['session']='catalogue-session';b['identity']['session']='agent-session'
    a['capture_context']['step_index']=5;b['capture_context']['step_index']=0
    a['provenance']={'sources':[{'path':'/episode/reports/fix_log.jsonl','line':8}]}
    b['provenance']={'sources':[{'path':'/episode/reports/fix_log.jsonl','line':11}]}
    entries,_=M.step_entries([b,a])
    assert [e['source_unit_id'] for e in entries]==['0','1']
    assert entries[0]['past_state']['prior_steps']==[]


def test_unknown_global_order_never_fabricates_successful_suffix():
    a,b=unit(0),unit(1)
    a['identity']['session']='catalogue-session';b['identity']['session']='agent-session'
    entries,excluded=M.step_entries([a,b])
    assert not entries and excluded[0]['reason']=='ambiguous_trajectory_step_order'


def store(tmp_path,units=None):
    root=tmp_path/'fold';shared=root/'shared';shared.mkdir(parents=True)
    source=shared/'shared_units.jsonl'
    source.write_text(''.join(json.dumps(u)+'\n' for u in (units if units is not None else [unit(0)])))
    (shared/'manifest.json').write_text(json.dumps({'units_sha256':M.digest(source)}))
    M.build(root/'controls',shared)
    return root


def test_local_build_hash_bound_zero_paid_calls_no_overwrite(tmp_path):
    root=store(tmp_path);data=json.loads((root/'controls/ADAMEM.json').read_text())
    assert data['configuration']==M.CONFIG and data['model_requests']==data['tokens']==data['EDA_flows']==0
    assert data['entries'] and data['units_sha256']==M.digest(root/'shared/shared_units.jsonl')
    with pytest.raises(FileExistsError):M.build(root/'controls',root/'shared')


def test_build_rejects_modified_shared_input(tmp_path):
    root=store(tmp_path);(root/'shared/shared_units.jsonl').write_text('changed')
    with pytest.raises(ValueError,match='hash mismatch'):M.build(root/'new-controls',root/'shared')


class Gateway:
    def __init__(self,answers,limit=3,error_at=None):
        self.answers=list(answers);self.limit=limit;self.sent=[];self.error_at=error_at
    def remaining(self):return self.limit-len(self.sent)
    def call(self,messages,prefix,**kwargs):
        if self.remaining()<=0:raise D.OnlineAllowanceSpent()
        self.sent.append((copy.deepcopy(messages),prefix,kwargs))
        if self.error_at==len(self.sent):raise D.IntegrityStop('UNKNOWN usage')
        Path(str(prefix)+'.try0.request.json').write_text(json.dumps({'messages':messages}))
        return self.answers[len(self.sent)-1],{'prompt_tokens':10,'completion_tokens':5,'repair_turn_id':str(len(self.sent))}


def choice(need=True):
    return json.dumps({'operation':'tool','arguments':{'tool_id':'route_relief'},
                       'request_memory':need,'memory_reason':'current congestion'})


FINAL=json.dumps({'operation':'repair','arguments':{'set':{'CORE_UTILIZATION':'45'},'rerun_from':'floorplan'}})
MESSAGES=[{'role':'system','content':'Return ONE JSON object with operation and arguments.'},
          {'role':'user','content':'CURRENT_REPORT congestion'}]


def memory(tmp_path,monkeypatch,units=None):
    root=store(tmp_path,units);project=tmp_path/'episode';project.mkdir()
    calls=[];events=[]
    class Index:
        def __init__(self,model,texts):calls.append(('init',model,texts))
        def search(self,query,k):calls.append(('search',query,k));return [0]
        def close(self):calls.append(('close',))
    monkeypatch.setattr(M.A,'DenseIndex',Index)
    m=M.AdaMemory(None,project,root,{})
    m.event=lambda kind,data:events.append((kind,data))
    return m,calls,events


def test_no_request_uses_one_call_no_retrieval_no_strategy(tmp_path,monkeypatch):
    m,indices,events=memory(tmp_path,monkeypatch);g=Gateway([choice(False)])
    content,usage=m.decide(MESSAGES,g,m.project/'d1')
    assert len(g.sent)==1 and indices==[] and R.decode(content)[0]['operation']=='tool'
    assert m.agent_context({},g)==''
    m.consume(MESSAGES,m.project/'d1')
    assert any(k=='memory_consumption' for k,v in events)


def test_three_stage_current_state_strategy_then_refined_action(tmp_path,monkeypatch):
    m,indices,events=memory(tmp_path,monkeypatch)
    g=Gateway([choice(),'{"strategy":"STATE_SPECIFIC_STRATEGY"}',FINAL])
    original=copy.deepcopy(MESSAGES)
    content,usage=m.decide(MESSAGES,g,m.project/'d1');m.consume(MESSAGES,m.project/'d1')
    assert content==FINAL and usage['repair_turn_id']=='3' and g.remaining()==0
    assert [c[2]['purpose'] for c in g.sent]==['planning','memory_adaptation','planning']
    assert all(c[2]['candidate_generating'] for c in g.sent)
    assert indices[-1]==('search','CURRENT_REPORT congestion',1)
    assert 'remaining_trajectory' in str(g.sent[1][0])
    assert 'remaining_trajectory' not in str(g.sent[2][0])
    assert 'STATE_SPECIFIC_STRATEGY' in str(g.sent[2][0]) and MESSAGES==original
    assert len([v for k,v in events if k=='memory_consumption'])==3
    assert next(v for k,v in events if k=='adamem_strategy_used')['strategy_persisted'] is False


def test_transient_strategy_not_reused_next_step_and_query_updates(tmp_path,monkeypatch):
    m,indices,events=memory(tmp_path,monkeypatch)
    m.decide(MESSAGES,Gateway([choice(),'{"strategy":"OLD_STRATEGY"}',FINAL]),m.project/'d1')
    messages=[MESSAGES[0],{'role':'user','content':'NEW_REPORT timing'}]
    g=Gateway([choice(),'{"strategy":"NEW_STRATEGY"}',FINAL])
    m.decide(messages,g,m.project/'d2')
    assert 'OLD_STRATEGY' not in str(g.sent)
    assert indices[-1]==('search','NEW_REPORT timing',1)


@pytest.mark.parametrize('limit',[1,2])
def test_no_hidden_extra_calls_if_two_refresh_calls_cannot_fit(tmp_path,monkeypatch,limit):
    m,indices,events=memory(tmp_path,monkeypatch);g=Gateway([choice()],limit)
    content,_=m.decide(MESSAGES,g,m.project/'d')
    assert len(g.sent)==1 and not indices and R.decode(content)[0]
    assert events[-1][1]['reason']=='insufficient_online_requests'


def test_empty_history_falls_back_without_paid_synthesis(tmp_path,monkeypatch):
    m,indices,events=memory(tmp_path,monkeypatch,[]);g=Gateway([choice()])
    assert R.decode(m.decide(MESSAGES,g,m.project/'d')[0])[0]
    assert len(g.sent)==1 and not indices and events[-1][1]['reason']=='no_successful_trajectory'


@pytest.mark.parametrize('bad',['not json','{"strategy":""}','{"strategy":12}','{"strategy":"s","operation":"repair"}'])
def test_malformed_strategy_is_charged_not_retried(tmp_path,monkeypatch,bad):
    m,_,events=memory(tmp_path,monkeypatch);g=Gateway([choice(),bad])
    content,_=m.decide(MESSAGES,g,m.project/'d')
    assert R.decode(content)[0] and len(g.sent)==2 and events[-1][0]=='adamem_invalid_strategy'


@pytest.mark.parametrize('at',[1,2,3])
def test_unknown_never_swallowed_into_tentative_fallback(tmp_path,monkeypatch,at):
    m,_,_=memory(tmp_path,monkeypatch);g=Gateway([choice(),'{"strategy":"s"}',FINAL],error_at=at)
    with pytest.raises(D.IntegrityStop,match='UNKNOWN'):m.decide(MESSAGES,g,m.project/'d')
    assert len(g.sent)==at


def test_modified_memory_and_wrong_request_receipt_fail_closed(tmp_path,monkeypatch):
    m,_,_=memory(tmp_path,monkeypatch);g=Gateway([choice(False)])
    m.decide(MESSAGES,g,m.project/'d')
    (m.project/'d-tentative.try0.request.json').write_text('{"messages":[]}')
    with pytest.raises(ValueError,match='differs'):m.consume(MESSAGES,m.project/'d')
    m.path.write_text('changed')
    with pytest.raises(D.IntegrityStop,match='changed'):m.agent_context({},g)


def test_factory_and_registered_new_controller_not_legacy_reme(tmp_path,monkeypatch):
    root=store(tmp_path)
    assert isinstance(MEM.make_memory(None,tmp_path,'ADAMEM',root,{}),M.AdaMemory)
    import t2_freepdk45_main as F
    assert 'ADAMEM' in F.ARMS and 'REME' not in F.ARMS and len(F.ARMS)*50==450
    assert 'ADAMEM' not in F.BUILDS # no LLM offline induction


def test_upstream_pin_and_algorithm_sources_present():
    sources=M.upstream_sources()
    assert len(sources)==6 and all(p.is_file() for p,r in sources)
    code=next(p for p,r in sources if p.name=='gpt4o_alfworld.py').read_text()
    assert 'get_action_with_adamem_high' in code and 'strategy_prompt' in code


def test_shared_live_host_executes_only_refined_action(tmp_path,monkeypatch):
    from test_t2_agent_runtime import fake_runtime
    m,indices,events=memory(tmp_path,monkeypatch)
    h,env,_,_=fake_runtime(m.project,monkeypatch,[])
    g=Gateway([choice(),'{"strategy":"adapt to current reports"}',FINAL])
    monkeypatch.setattr(D,'OnlineGateway',lambda *a:g)
    executed=[]
    def execute(*args,**kwargs):
        executed.append(args[4])
        return {'status':'RETAINED','rolled_back':False,'verdict':{'accepted':True}}
    monkeypatch.setattr(R,'execute_candidate',execute)
    result=R.run(h,m.project,'ADAMEM',env,'unused',{'accepted':False},lambda p:None,m)
    assert result['last']['accepted'] and len(g.sent)==3
    assert len(executed)==1 and json.loads(executed[0])==json.loads(FINAL)['arguments']
    assert len([v for k,v in events if k=='memory_consumption'])==3


def test_native_three_stages_use_actual_ledger_not_an_extra_free_helper(ep,tmp_path,monkeypatch):
    import os
    project,transport_calls,_=ep
    root=store(tmp_path)
    m=M.AdaMemory(H,project,root,{})
    monkeypatch.setattr(M.A,'DenseIndex',lambda *a:SimpleNamespace(search=lambda *a:[0]))
    Path(os.environ['R2G_BUDGET_FILE']).write_text(json.dumps(D.episode_budget()))
    answers=[choice(),'{"strategy":"current state guidance"}',FINAL]
    def fake(req,timeout=0):
        i=len(transport_calls);transport_calls.append(1)
        return _Resp(json.dumps({**OK,'choices':[{'message':{'content':answers[i]}}]}).encode())
    monkeypatch.setattr(H.urllib.request,'urlopen',fake)
    gateway=D.OnlineGateway(H,project,'not-a-real-key')
    content,_=m.decide(MESSAGES,gateway,project/'actual');m.consume(MESSAGES,project/'actual')
    assert content==FINAL and len(transport_calls)==3 and gateway.remaining()==0
    assert _spent(project,'llm_requests')==3 and _spent(project,'tokens_in')==150
    assert _spent(project,'tokens_out')==60
    with pytest.raises(D.OnlineAllowanceSpent):
        gateway.call(MESSAGES,project/'fourth',purpose='planning',candidate_generating=True)
    assert len(transport_calls)==3
