"""Seven-arm runtime validation with metered fake transport, not paper results."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import t2a_runtime as T
from test_repair_turns import ep, H, OK, _Resp, _spent


def test_o1_private_client_admits_larger_payload_without_changing_legacy(ep,monkeypatch):
    project,calls,_=ep
    import t2a_qualify as Q
    private=Q.module('o1_private_client',Path(H.__file__))
    private.SKILL=H.SKILL
    assert H.LLM_MAX_INPUT_BYTES==12000
    policy=T.configure_online(private)
    assert policy==T.online_policy() and private.LLM_MAX_INPUT_BYTES==96000
    assert private.LLM_MAX_TOKENS==800 and private.LLM_OUTPUT_CEILING==2000
    content,usage=private.llm_call('unused',[{'role':'user','content':'x'*64000}],
                                 project/'large',ledger=project)
    assert content and usage['total_tokens']==70 and len(calls)==1
    content,usage=private.llm_call('unused',[{'role':'user','content':'x'*96000}],
                                 project/'too-large',ledger=project)
    assert content is None and usage['refused']=='input_ceiling' and len(calls)==1
    assert _spent(project,'repair_attempts')==1 and H.LLM_MAX_INPUT_BYTES==12000
    events=[json.loads(l) for l in (project/'reports/resource_ledger.jsonl').read_text().splitlines()]
    assert T.accounting_check(events,[])['passed']


def test_o1_complete_insights_are_loaded_into_all_three_metered_turns(ep,monkeypatch):
    project,_,_=ep
    monkeypatch.setattr(H,'LLM_MAX_INPUT_BYTES',12000)  # restore legacy after this opt-in
    T.configure_online(H)
    monkeypatch.setattr(H,'_task_failure',lambda p:('orfs_stage','route',1))
    monkeypatch.setattr(H,'observation',lambda p:{'orfs_status':'fail'})
    sent=[]
    def transport(req,timeout=0):
        sent.append(req.data)
        return _Resp(json.dumps({**OK,'choices':[{'message':{'content':'{}'}}]}).encode())
    monkeypatch.setattr(H.urllib.request,'urlopen',transport)
    entire_list='verified insight. '*3300
    class Memory:
        def context(self,query,sit,auxiliary=None,max_bytes=None):
            assert max_bytes==T.MEMORY_CONTEXT_BYTES==80000
            return entire_list,['source-unit']
    rows,_=T.strict_turns(H,project,'EXPEL',manifest(project),'unused',
                         SimpleNamespace(from_project=lambda *a:{}),Memory())
    assert len(rows)==len(sent)==3 and _spent(project,'repair_attempts')==3
    assert all(12000<len(body)<=96000 for body in sent)
    assert all(entire_list in json.loads(body)['messages'][-1]['content'] for body in sent)
    assert _spent(project,'llm_requests')==3


@pytest.mark.parametrize('size,passed',[(12000,True),(96000,True),(96001,False)])
def test_o1_accounting_uses_the_registered_online_byte_limit(size,passed):
    event={'type':'llm_request','model_reported':'deepseek-flash','request_bytes':size,
           'usage_reported':{'prompt_tokens':100,'completion_tokens':20}}
    assert T.accounting_check([event],[])['passed'] is passed


def manifest(project):
    p=project/'manifest.json'
    p.write_text(json.dumps({'task':'dev/check','initial_config':{'CORE_UTILIZATION':'60'},
      'allowlist':{'CORE_UTILIZATION':{'type':'number','min':10,'max':90}},'immutable':{}}))
    return {'R2G_TASK_MANIFEST':str(p)}


@pytest.mark.parametrize('arm',T.ARMS)
def test_all_seven_share_three_turns_and_auxiliary_has_no_candidate_privilege(ep,monkeypatch,arm):
    project,calls,_=ep
    monkeypatch.setattr(H,'_task_failure',lambda p:('orfs_stage','route',1))
    monkeypatch.setattr(H,'observation',lambda p:{'orfs_status':'fail'})
    sent=[]
    def fake(req,timeout=0):
        obj=json.loads(req.data);sent.append(obj)
        auxiliary='Only extract query keywords' in obj['messages'][0]['content']
        content='{"keywords":["route"]}' if auxiliary else '{}'
        return _Resp(json.dumps({**OK,'choices':[{'message':{'content':content}}]}).encode())
    monkeypatch.setattr(H.urllib.request,'urlopen',fake)
    class Memory:
        def context(self,query,sit,auxiliary=None,max_bytes=None):
            if arm=='AMEM':assert auxiliary(query)==['route']
            return 'retained frozen experience',['source-unit']
    memory=Memory() if arm in ('RET','EXPEL','AWM','AMEM') else None
    rows,last=T.strict_turns(H,project,arm,manifest(project),'k',SimpleNamespace(
        from_project=lambda *a:{}),memory)
    assert len(rows)==3 and last is None
    assert all(r['status']=='REJECTED_PROPOSAL' and not r['flow_launched'] for r in rows)
    assert _spent(project,'repair_attempts')==3
    assert _spent(project,'llm_requests')==(6 if arm=='AMEM' else 3)
    events=[json.loads(l) for l in (project/'reports/resource_ledger.jsonl').read_text().splitlines()]
    gate=T.accounting_check(events,[])
    assert gate['passed'] and not gate['zero_online_requests']


def test_restore_preserves_cost_and_rejected_evidence(tmp_path):
    (tmp_path/'constraints').mkdir();(tmp_path/'reports').mkdir();(tmp_path/'backend').mkdir()
    cfg=tmp_path/'constraints/config.mk';cfg.write_text('before')
    report=tmp_path/'reports/drc.json';report.write_text('before evidence')
    ledger=tmp_path/'reports/resource_ledger.jsonl';ledger.write_text('cost history')
    state=T.snapshot_state(tmp_path,1)
    cfg.write_text('after');report.write_text('rejected')
    (tmp_path/'reports/lvs.json').write_text('new report')
    T.restore(tmp_path,state)
    assert cfg.read_text()=='before' and report.read_text()=='before evidence'
    assert not (tmp_path/'reports/lvs.json').exists()
    assert (state/'rejected/reports/lvs.json').read_text()=='new report'
    assert ledger.read_text()=='cost history'


def test_formal_gate_rejects_missing_runs_open_holds_usage_and_fourth_turn():
    events=[{'type':'flow_run','run_dir':'x'}, {'type':'budget_hold','hold_id':'open'},
            {'type':'budget_settle','hold_id':'done','actual':{'repair_attempts':4}},
            {'type':'llm_request','model_reported':'deepseek-flash','request_bytes':1,'usage_reported':'UNKNOWN'}]
    gate=T.accounting_check(events,[])
    assert not gate['passed'] and len(gate['errors'])==4


@pytest.mark.parametrize('purpose',['repair','final_acceptance'])
def test_intermediate_verification_cannot_spend_final_reserve(ep,monkeypatch,purpose):
    project,_,_=ep
    seen=[]
    monkeypatch.setattr(H,'_budgeted',lambda:True)
    def ledger(p,command,*args,**kw):
        if command=='remaining':
            seen.append(args);return 0,'{"wall_s": 200}'
        return 0,''
    monkeypatch.setattr(H,'_ledger',ledger)
    def run(cmd,env,log,timeout):
        assert env['R2G_BUDGET_PURPOSE']==purpose
        assert env['R2G_BUDGET_COMPONENT']==('candidate_verification' if purpose=='repair' else 'final_acceptance')
        return 0
    monkeypatch.setattr(H,'run',run)
    assert H.final_signoff(project,{'R2G_VERIFICATION_PURPOSE':purpose},project/'log')==0
    assert seen==[('--purpose',purpose)]


def test_rejected_workspace_requires_full_charged_reflow_even_at_final(ep,monkeypatch):
    project,_,_=ep
    state=T.snapshot_state(project,1)
    T.restore(project,state,execution_changed=True)
    calls=[]
    monkeypatch.setattr(H,'FLOW',H.SKILL/'scripts/flow')
    monkeypatch.setattr(H,'_ledger',lambda p,*a,**k:(0,'{"wall_s": 500}' if a[0]=='remaining' else ''))
    monkeypatch.setattr(H,'run',lambda cmd,env,log,timeout:calls.append((cmd,env)) or 0)
    monkeypatch.setattr(H,'final_verdict',lambda *a:{'accepted':True,'final_layout':True,'envelope':{'status':'PASS'}})
    v=T.terminal_verdict(H,project,{})
    assert v['accepted'] and v['execution_recovery']=='VERIFIED'
    assert calls[0][1]['FROM_STAGE']==''  # full clean_all, not stage-cache resume
    assert calls[0][1]['R2G_BUDGET_COMPONENT']=='rollback_recovery'
    assert calls[0][1]['R2G_BUDGET_PURPOSE']=='final_acceptance'
    assert not (project/'reports/t2a-workspace-unsafe.json').exists()


def test_failed_restoration_never_uses_old_acceptance(ep,monkeypatch):
    project,_,_=ep
    T.restore(project,T.snapshot_state(project,1),execution_changed=True)
    monkeypatch.setattr(H,'_ledger',lambda p,*a,**k:(0,'{"wall_s": 500}' if a[0]=='remaining' else ''))
    monkeypatch.setattr(H,'run',lambda *a:75)
    monkeypatch.setattr(H,'final_verdict',lambda *a:pytest.fail('stale verdict must not be read'))
    assert T.terminal_verdict(H,project,{})['accepted'] is False
    assert (project/'reports/t2a-workspace-unsafe.json').exists()


def test_unverified_restoration_is_not_an_ordinary_not_accepted_episode():
    assert T.terminal_status({'accepted':False,'execution_recovery':'UNVERIFIED','recovery_rc':2}) == 'UNVERIFIED_OR_SAFETY_STOP'


def test_failed_recovery_is_not_implicitly_retried_by_terminal_verification(ep,monkeypatch):
    project,_,_=ep
    T.restore(project,T.snapshot_state(project,1),execution_changed=True)
    monkeypatch.setattr(H,'_ledger',lambda p,*a,**k:(0,'{"wall_s":500}' if a[0]=='remaining' else ''))
    calls=[]
    monkeypatch.setattr(H,'run',lambda *a:calls.append(a) or 2)
    monkeypatch.setattr(H,'final_verdict',lambda *a:pytest.fail('unverified restoration cannot read cached verdict'))
    assert T.recover_execution(H,project,{})['execution_recovery']=='UNVERIFIED'
    assert T.terminal_verdict(H,project,{})['execution_recovery']=='UNVERIFIED'
    assert len(calls)==1  # final verification must not repeat the same failed clean flow


@pytest.mark.parametrize('code', [124, -9, 137])
def test_safety_stopped_recovery_is_not_retried_at_terminal(ep, monkeypatch, code):
    project, _, _ = ep
    T.restore(project, T.snapshot_state(project, 1), execution_changed=True)
    monkeypatch.setattr(H, '_ledger', lambda p, *a, **k:
                        (0, '{"wall_s":500}' if a[0] == 'remaining' else ''))
    calls = []
    monkeypatch.setattr(H, 'run', lambda *a: calls.append(a) or code)
    monkeypatch.setattr(H, 'final_verdict', lambda *a: pytest.fail('unsafe state'))
    first = T.recover_execution(H, project, {})
    final = T.terminal_verdict(H, project, {})
    assert first['execution_recovery'] == final['execution_recovery'] == 'SAFETY_STOP'
    assert final['recovery_not_retried'] is True
    assert final['recovery_rc'] == code and len(calls) == 1
    assert T.terminal_status(final) == 'UNVERIFIED_OR_SAFETY_STOP'
    assert (project / 'reports/t2a-workspace-unsafe.json').exists()


def test_terminal_status_requires_all_eleven_checks_and_consistent_acceptance():
    checks={key:'pass' for key in ('A1_flow_complete','A2_drc_full_deck','A3_lvs','A4_route',
        'A5_antenna','A6_setup','A7_hold','A8_drv_limits','A9_task_clock','A10_rcx','A11_provenance')}
    final={'accepted':True,'acceptance':{'accepted':True,'checks':checks,'unknown':[]}}
    assert T.terminal_status(final)=='ACCEPTED'
    final['acceptance']['checks']={**checks,'A2_drc_full_deck':'fail'}
    assert T.terminal_status(final)=='UNVERIFIED_OR_SAFETY_STOP'
    final['accepted']=final['acceptance']['accepted']=False
    assert T.terminal_status(final)=='NOT_ACCEPTED'
    final['acceptance']['checks'].pop('A3_lvs')
    assert T.terminal_status(final)=='UNVERIFIED_OR_SAFETY_STOP'


def test_unlaunched_terminal_checks_never_accept_cached_reports(ep,monkeypatch):
    project,_,_=ep
    project=project.with_name('dev__u80__NB');(project/'constraints').mkdir(parents=True)
    (project/'constraints/config.mk').write_text('export CORE_UTILIZATION = 60\n')
    (project/'constraints/constraint.sdc').write_text('set clk_period 10\n')
    run=project/'backend/RUN_A';(run/'final').mkdir(parents=True);(run/'final/6_final.odb').write_text('odb')
    (project/'reports').mkdir()
    for name,data in {'acceptance':{'accepted':True,'checks':{},'unknown':[],'failed':[]},
                      'drc':{'status':'clean'},'lvs':{'status':'clean'},'rcx':{'status':'clean'},
                      'timing_check':{'tier':'clean'}}.items():
        (project/'reports'/(name+'.json')).write_text(json.dumps(data))
    monkeypatch.setattr(H,'run',lambda *a:0)
    monkeypatch.setattr(H,'final_signoff',lambda *a:'budget_exhausted')
    assert H.final_verdict(project,{},project/'log')['accepted'] is False


def test_flow_correspondence_requires_the_actual_directory_not_just_equal_counts():
    events=[{'type':'flow_run','run_dir':'wrong-run'},
            {'type':'budget_charge','resource':'flows','amount':1}]
    assert not T.accounting_check(events,['actual-run'])['passed']
    assert T.accounting_check(events,['wrong-run'])['passed']


@pytest.mark.parametrize('success_turn',[1,2,3])
@pytest.mark.parametrize('arm',['NB','AMEM'])
def test_strict_acceptance_stops_generation_and_keeps_helper_tokens(ep,monkeypatch,success_turn,arm):
    project,_,_=ep
    monkeypatch.setattr(H,'_task_failure',lambda p:('orfs_stage','route',1))
    monkeypatch.setattr(H,'observation',lambda p:{'orfs_status':'fail'})
    import result_vector as V
    monkeypatch.setattr(V,'capture',lambda *a:{})
    monkeypatch.setattr(V,'compare',lambda *a:{'unknowns':[],'regressions':[]})
    generations=[];requests=[]
    def transport(req,timeout=0):
        obj=json.loads(req.data);requests.append(obj)
        helper='Only extract query keywords' in obj['messages'][0]['content']
        if not helper:generations.append(obj)
        content='{"keywords":["route"]}' if helper else OK['choices'][0]['message']['content']
        return _Resp(json.dumps({**OK,'choices':[{'message':{'content':content}}]}).encode())
    monkeypatch.setattr(H.urllib.request,'urlopen',transport)
    monkeypatch.setattr(H,'apply_llm',lambda *a,**k:('route',{'applied':True}))
    monkeypatch.setattr(H,'run',lambda *a:0)  # no EDA; this tests orchestration, not acceptance correctness
    monkeypatch.setattr(H,'final_verdict',lambda *a:{'accepted':len(generations)==success_turn,
        'acceptance':{'unknown':[],'failed':[]},'envelope':{'status':'PASS'}})
    class Memory:
        def context(self,query,sit,auxiliary=None,max_bytes=None):
            assert auxiliary(query)==['route'];return 'frozen experience',['unit']
    rows,last=T.strict_turns(H,project,arm,manifest(project),'k',SimpleNamespace(from_project=lambda *a:{}),
                            Memory() if arm=='AMEM' else None)
    assert last['accepted'] and len(rows)==len(generations)==success_turn
    assert _spent(project,'repair_attempts')==success_turn
    assert len(requests)==success_turn*(2 if arm=='AMEM' else 1)
    events=[json.loads(l) for l in (project/'reports/resource_ledger.jsonl').read_text().splitlines()]
    summary=T.request_token_totals(events)
    assert summary['llm_requests']==len(requests)
    assert summary['total_tokens']==70*len(requests)  # helper usage is not free


def test_token_totals_include_all_requests_not_holds_or_cache_double_counting():
    events=[{'type':'llm_request','role':'repair','attempt':0,'status':200,
             'usage_reported':{'prompt_tokens':100,'completion_tokens':10,'prompt_cache_hit_tokens':80}},
            {'type':'llm_request','role':'auxiliary','attempt':0,'status':200,
             'usage_reported':{'prompt_tokens':5,'completion_tokens':2}},
            {'type':'llm_request','role':'repair','attempt':0,'status':503,
             'usage_reported':{'prompt_tokens':14,'completion_tokens':0}},
            {'type':'llm_request','role':'repair','attempt':1,'status':200,
             'usage_reported':{'prompt_tokens':120,'completion_tokens':9}},
            {'type':'budget_settle','actual':{'tokens_in':239,'tokens_out':21}}]
    data=T.request_token_totals(events)
    assert data['llm_requests']==data['requests_with_complete_usage']==4
    assert data['repair_requests']==3 and data['auxiliary_requests']==1
    assert data['input_tokens']==239 and data['output_tokens']==21 and data['total_tokens']==260
    assert data['measurement_complete'] and data['unknown_usage_requests']==0
    events.append({'type':'llm_request','role':'repair','status':-1,'usage_reported':'UNKNOWN'})
    data=T.request_token_totals(events)
    assert not data['measurement_complete'] and data['unknown_usage_requests']==1
    assert data['total_tokens']=='UNKNOWN' and data['known_input_tokens']==239 and data['known_output_tokens']==21
