"""Shared baseline/memory fixes, exercised through actual host and ledger boundaries."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest

sys.path[:0]=[str(Path(__file__).parents[1]/'scripts'),str(Path(__file__).parents[1])]
import t2_agent_runtime as R
import t2_agent_verdict as V
import t2_agent_tools as TO
import t2_agent_memories as M
import t2_agent_dispatch as D
import t2a_runtime as T
from test_repair_turns import H,ep
from test_t2a_partial_restoration import partial_task,write


@pytest.mark.parametrize('arm',D.ARMS)
def test_all_arms_accept_only_known_non_executable_transport_metadata(arm):
    response={'operation':'repair','arguments':{'set':{'CORE_UTILIZATION':'12'},'rerun_from':'floorplan'},'type':'json_object'}
    decision,error=R.decode(json.dumps(response))
    assert error is None and set(decision)=={'operation','arguments'}
    for extra in ({'type':'shell'},{'command':'touch something'},{'operation_override':'shell'}):
        assert R.decode(json.dumps({**response,**extra}))[0] is None


def native_fixture(ep):
    p,_,_=ep;env=partial_task(p);run=p/'backend/RUN_00'
    T.event(H,p,'budget_charge',{'resource':'flows','amount':1,'component':'initial'})
    T.event(H,p,'flow_run',{'run_dir':str(run),'rc':2,'component':'initial','timeout':False,'budget_stop':False})
    return p,env


def test_proven_native_failure_is_measured_without_passing_unmeasured_signoff(ep):
    p,env=native_fixture(ep)
    verdict=V.attach_failure(p,env,{'accepted':False,'final_layout':False,'envelope':{'status':'UNVERIFIED'}})
    assert V.terminal_status(verdict)=='NOT_ACCEPTED'
    receipt=verdict['native_failure_receipt']
    assert receipt['success_condition_failed']=='A1_flow_complete'
    assert 'A6_setup' in receipt['unmeasured_checks'] and 'acceptance' not in verdict
    assert V.terminal_reason(verdict)=='measured_native_infeasibility'
    assert T.terminal_status(verdict)=='UNVERIFIED_OR_SAFETY_STOP' # historical classifier unchanged


@pytest.mark.parametrize('fault',['unsafe','config_changed','timeout','unknown_error','missing_charge',
                                 'missing_usage','duplicate_outcome','completed_layout'])
def test_native_failure_receipt_cannot_mask_technical_fault(ep,fault):
    p,env=native_fixture(ep);ledger=p/'reports/resource_ledger.jsonl'
    if fault=='unsafe':write(p/'reports/t2a-workspace-unsafe.json',{})
    elif fault=='config_changed':(p/'constraints/config.mk').write_text('export CORE_UTILIZATION = 59\n')
    elif fault=='unknown_error':(p/'backend/RUN_00/flow.log').write_text('[ERROR GRT-0246] installation failure\n')
    elif fault=='missing_charge':ledger.write_text('\n'.join(l for l in ledger.read_text().splitlines() if json.loads(l)['type']!='budget_charge')+'\n')
    elif fault=='completed_layout':
        (p/'backend/RUN_00/final').mkdir();(p/'backend/RUN_00/final/6_final.odb').write_text('final')
    elif fault=='timeout':
        ev=[json.loads(l) for l in ledger.read_text().splitlines() if l]
        for x in ev:
            if x['type']=='flow_run':x['timeout']=True
        ledger.write_text(''.join(json.dumps(x)+'\n' for x in ev))
    elif fault=='missing_usage':T.event(H,p,'llm_request',{'usage_reported':'UNKNOWN'})
    elif fault=='duplicate_outcome':T.event(H,p,'flow_run',{'run_dir':str(p/'backend/RUN_00'),'rc':2})
    assert V.native_failure(p,env) is None


def test_restore_failure_is_not_relabelled_as_native_failure(ep):
    p,env=native_fixture(ep)
    result=V.attach_failure(p,env,{'accepted':False,'execution_recovery':'UNVERIFIED'})
    assert 'native_failure_receipt' not in result


def test_tool_discovery_does_not_execute_and_selected_action_is_config_bound(tmp_path,monkeypatch):
    (tmp_path/'constraints').mkdir();(tmp_path/'constraints/config.mk').write_text('export CORE_UTILIZATION = 20\n')
    calls=[];events=[]
    h=SimpleNamespace(SKILL=Path(__file__).parents[2]/'r2g-skills/signoff-loop',_task_failure=lambda p:('drc','m3.2',18))
    tools=TO.PublicTools(h,tmp_path,{'R2G_MEMORY_BACKEND':'tehm','TEHM_DB':'private','R2G_RECIPE_FILE':'private'})
    c={'id':'density_relief','rationale':'routing spacing relief','auto_apply':True,
       'config_edits':{'CORE_UTILIZATION':'12'},'recheck':'drc','rerun_from':'floorplan'}
    def query(check,apply=None):
        calls.append((check,apply))
        return {'strategies':[c]} if apply is None else {'status':'applied','applied':apply,'config_edits':c['config_edits']}
    monkeypatch.setattr(tools,'_query',query);monkeypatch.setattr(TO.T,'event',lambda *a:events.append(a[2]))
    offered=tools.discover();assert calls==[('drc',None)] and offered[0]['set']=={'CORE_UTILIZATION':'12'}
    assert tools.env['R2G_MEMORY_BACKEND']=='none' and 'TEHM_DB' not in tools.env
    assert tools.apply(offered[0]['tool_id'])[0]=='floorplan'
    with pytest.raises(D.IntegrityStop):tools.apply(offered[0]['tool_id'])
    tools.used.clear();(tmp_path/'constraints/config.mk').write_text('export CORE_UTILIZATION = 30\n')
    with pytest.raises(D.IntegrityStop,match='configuration changed'):tools.apply(offered[0]['tool_id'])


@pytest.mark.parametrize('arm',D.ARMS)
def test_every_arm_can_select_single_public_tool_through_same_executor(tmp_path,monkeypatch,arm):
    from test_t2_agent_runtime import fake_runtime
    h,env,calls,events=fake_runtime(tmp_path,monkeypatch,[json.dumps({'operation':'tool','arguments':{'tool_id':'density@1'}})])
    applied=[]
    public=SimpleNamespace(offered={'density@1':{}},discover=lambda:[{'tool_id':'density@1','set':{'CORE_UTILIZATION':'12'}}],
                           apply=lambda ident:applied.append(ident) or ('floorplan',{}))
    monkeypatch.setattr(R,'PublicTools',lambda *a:public)
    def execute(*a,native_apply=None,component=None):
        assert component=='catalogue';native_apply()
        return {'status':'RETAINED','rolled_back':False,'verdict':{'accepted':True}}
    monkeypatch.setattr(R,'execute_candidate',execute)
    memory=None if arm=='NB' else SimpleNamespace(agent_context=lambda *a:'bounded experience')
    result=R.run(h,tmp_path,arm,env,'unused',{'accepted':False},lambda p:None,memory)
    assert len(calls)==1 and applied==['density@1'] and result['last']['accepted']
    assert 'available_deterministic_tools' in calls[0][-1]['content']
    assert any(k=='repair_route' and v['choice']=='deterministic_tool' for k,v in events)


@pytest.mark.parametrize('route,allowed',[('NO_SKILL',True),('CONSIDER',True),('INAPPLICABLE',False),('ABSTAIN',False)])
def test_native_rule_and_knowledge_router_select_advice_not_execution(route,allowed,monkeypatch):
    m=object.__new__(M.Memory);m.arm='TM0';m.used=[];m.native_failures=0;m.route_state=None
    m.experience_disabled=False;m.guidance_active=False
    m.H=SimpleNamespace(observation=lambda p:{});m.project=None;m.current_situation=lambda:{}
    def route_knowledge(*a):m.route_state={'knowledge_route':route};return '',[]
    m.route_knowledge=route_knowledge
    m.diagnose=lambda:{'strategies':[{'id':'verified-own-evidence-rule','source':'tehm_rule'}]}
    monkeypatch.setattr(M.T,'event',lambda *a:None)
    assert m.native_action() is None
    text,ids=m.adaptive_context('query',{})
    assert bool(text) is allowed
    assert bool(ids) is allowed


def test_metadata_compatibility_preserves_three_request_limit_and_execution_feedback(tmp_path,monkeypatch):
    from test_t2_agent_runtime import fake_runtime
    response=json.dumps({'type':'json_object','operation':'repair','arguments':{'set':{'CORE_UTILIZATION':'12'},'rerun_from':'floorplan'}})
    h,env,calls,events=fake_runtime(tmp_path,monkeypatch,[response])
    monkeypatch.setattr(R,'execute_candidate',lambda *a:{'status':'FAILED','rolled_back':True})
    result=R.run(h,tmp_path,'NB',env,'unused',{'accepted':False},lambda p:None)
    assert len(calls)==3 and len(result['decisions'])==3
    assert all(d['status']=='FAILED' for d in result['decisions'])


def test_common_journal_preserves_actual_action_ownership_and_rollback(ep):
    import t2_agent_journal as J
    p,env=native_fixture(ep)
    entry=J.begin(H,p,1,'catalogue','{"operation":"tool"}')
    post=p/'backend/RUN_01';post.mkdir()
    write(post/'r2g_action.json',{'action_id':entry['action_id']})
    (p/'constraints/config.mk').write_text('export CORE_UTILIZATION = 12\n')
    result={'flow_launched':True,'flow_rc':0,'status':'REGRESSION','rolled_back':True,
            'applied':{'applied':'density_relief'},'verdict':{'accepted':False}}
    J.finish(p,entry,result,'floorplan','catalogue')
    row=json.loads((p/'reports/fix_log.jsonl').read_text())
    assert row['pre_run_tag']=='RUN_00' and row['post_run_tag']=='RUN_01'
    assert row['action_id']==entry['action_id'] and row['rolled_back'] is True
    assert row['config_delta']=={'CORE_UTILIZATION':'12'} and row['strategy']=='density_relief'
    assert row['evidence_admission']=='NOT_ASSERTED' and result['journal_ref']['action_id']==entry['action_id']


def test_prompt_does_not_prescribe_design_specific_fixes_or_static_rule_names():
    h=SimpleNamespace(executable_knobs=lambda m:['CORE_UTILIZATION'])
    prompt=R.instructions(h,'sky130hd',{})
    for text in ('density_relief','route_relief','C02786','keccak','sqrt','"12"'):
        assert text not in prompt
    assert 'currently offered descriptions and parameters' in prompt


@pytest.mark.parametrize('ident,value',[('store_rule_alpha','17'),('unseen_rule_beta','31')])
def test_native_selection_and_apply_consume_store_values_not_a_repair_template(monkeypatch,ident,value):
    m=object.__new__(M.Memory);m.arm='RECIPE';m.used=[];m.native_failures=0;m.route_state=None
    m.H=None;m.project=None
    c={'id':ident,'source':'recipe','config_edits':{'CORE_UTILIZATION':value},'rerun_from':'floorplan'}
    def diagnose(apply=None):
        return {'strategies':[c]} if apply is None else {'status':'applied','applied':ident,'config_edits':c['config_edits']}
    m.diagnose=diagnose
    monkeypatch.setattr(M.T,'event',lambda *a:None)
    selected=m.native_action();assert selected['id']==ident
    assert m.apply_native(selected)[1]['config_edits']['CORE_UTILIZATION']==value


@pytest.mark.parametrize('arm',D.ARMS)
def test_real_request_serializer_metering_and_consumption_for_all_arms(ep,monkeypatch,arm):
    import os
    from test_repair_turns import OK,_Resp,_spent
    from test_t2a_runtime import manifest
    from t2_tehm_agent_memory import verify_request,digest
    p,calls,_=ep;env=manifest(p);(p/'backend').mkdir()
    Path(os.environ['R2G_BUDGET_FILE']).write_text(json.dumps(D.episode_budget()))
    monkeypatch.setattr(T,'recover_execution',lambda *a:None)
    monkeypatch.setattr(H,'observation',lambda p:{'drc':18})
    monkeypatch.setattr(R,'PublicTools',lambda *a:SimpleNamespace(discover=lambda:[],offered={}))
    monkeypatch.setattr(R,'execute_candidate',lambda *a,**kw:{'status':'FAILED','rolled_back':True})
    def transport(request,timeout=0):
        data=json.loads(request.data);calls.append(data)
        content='{"keywords":["routing"]}' if data['messages'][0]['content']=='keywords only' else json.dumps({
            'type':'json_object','operation':'repair','arguments':{'set':{'CORE_UTILIZATION':'40'},'rerun_from':'floorplan'}})
        return _Resp(json.dumps({**OK,'choices':[{'message':{'content':content}}]}).encode())
    monkeypatch.setattr(H.urllib.request,'urlopen',transport)
    receipts=[]
    class Context:
        def __init__(self):self.queries=0
        def agent_context(self,obs,gateway):
            if arm=='AMEM':
                self.queries+=1
                gateway.call([{'role':'system','content':'keywords only'}],p/f'helper-{self.queries}',purpose='query')
            return 'Selected source-bound historical advice.'
        def consume(self,messages,prefix):
            receipts.append(verify_request(p,{'request_prefix':prefix.name,'messages_sha256':digest(messages)}))
    memory=None if arm=='NB' else Context()
    out=R.run(H,p,arm,env,'unused',{'accepted':False},lambda p:None,memory)
    assert out['stop_reason']=='online_allowance_spent' and len(calls)==3
    assert _spent(p,'llm_requests')==3 and _spent(p,'tokens_in')==150
    assert len(out['decisions'])==(1 if arm=='AMEM' else 3)
    assert len(receipts)==(0 if arm=='NB' else 1 if arm=='AMEM' else 3)
    assert all(r['serialized_request_present'] for r in receipts)
