"""Known infeasible initial states remain repairable, never signoff-accepted."""
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import t2a_runtime as T
from test_repair_turns import H, ep, _spent


def write(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(obj)+'\n')


def bound_run(project,name,manifest,code='FLW-0024',full=False):
    run=project/'backend'/name;run.mkdir(parents=True)
    stages=['synth','floorplan','place'] if full else ['place']
    (run/'stage_log.jsonl').write_text(''.join(json.dumps({'stage':s,'status':2 if s==stages[-1] else 0})+'\n' for s in stages))
    (run/'flow.log').write_text('[ERROR '+code+'] physical infeasibility\n')
    if full:write(run/'cleanup.json',{'from_stage':'','clean_all_rc':0,'status':'PASS'})
    write(run/'effective-config.json',{'status':'PASS','run_dir':str(run),
         'project_config_sha256':hashlib.sha256((project/'constraints/config.mk').read_bytes()).hexdigest(),
         'manifest_sha256':hashlib.sha256(Path(manifest).read_bytes()).hexdigest()})
    return run


def partial_task(project):
    sdc=project/'constraints/constraint.sdc';sdc.write_text('set clk_period 10\n')
    man=project/'manifest.json'
    write(man,{'initial_config':{'CORE_UTILIZATION':'60'},'require_execution_binding':True,
          'allowlist':{'CORE_UTILIZATION':{'type':'number','min':10,'max':90}},
          'immutable':{'files':{'constraints/constraint.sdc':'sha256:'+hashlib.sha256(sdc.read_bytes()).hexdigest()}}})
    run=bound_run(project,'RUN_00',man)
    write(project/'reports/ppa.json',{'run_dir':str(run),'orfs_status':'fail','orfs_fail_stage':'place'})
    return {'R2G_TASK_MANIFEST':str(man)}


def recover_stub(project,env,monkeypatch,code=2,error='DPL-0036',binding=True,flow_record=True):
    calls=[]
    def run(cmd,e,log,timeout):
        calls.append((cmd,e))
        if str(cmd[1]).endswith('run_orfs.sh'):
            new=bound_run(project,'RUN_01',env['R2G_TASK_MANIFEST'],error,full=True)
            if not binding:write(new/'effective-config.json',{'status':'FAIL'})
            if flow_record:
                T.event(H,project,'budget_charge',{'resource':'flows','amount':1,'component':'rollback_recovery'})
                T.event(H,project,'flow_run',{'run_dir':str(new),'rc':code,'component':'rollback_recovery','timeout':False,'budget_stop':False})
            return code
        if str(cmd[1]).endswith('extract_ppa.py'):
            write(project/'reports/ppa.json',{'run_dir':str(project/'backend/RUN_01'),'orfs_status':'fail','orfs_fail_stage':'place'})
        return 0
    monkeypatch.setattr(H,'run',run)
    monkeypatch.setattr(H,'final_verdict',lambda *a:pytest.fail('partial state must not read cached signoff'))
    return calls


def test_known_partial_restore_allows_remaining_turns_without_extra_opportunities(ep,monkeypatch):
    project,_,_=ep;env=partial_task(project)
    state=T.snapshot_state(project,0)
    write(project/'reports/acceptance.json',{'accepted':True})  # stale rejected evidence
    T.restore(project,state,execution_changed=True)
    write(project/'reports/acceptance.json',{'accepted':True})
    write(project/'reports/drc.json',{'status':'clean','run_tag':'rejected-stale-run'})
    calls=recover_stub(project,env,monkeypatch)
    result=T.recover_execution(H,project,env)
    assert result['execution_recovery']=='PARTIAL_VERIFIED' and result['accepted'] is False
    assert not (project/'reports/t2a-workspace-unsafe.json').exists()
    assert not (project/'reports/acceptance.json').exists()
    assert not (project/'reports/drc.json').exists()
    assert json.loads((project/'reports/t2a-restoration-displaced/RUN_01/acceptance.json').read_text())['accepted'] is True
    assert T.terminal_status(result)=='UNVERIFIED_OR_SAFETY_STOP'
    monkeypatch.setattr(H,'_task_failure',lambda p:('orfs_stage','place',1))
    monkeypatch.setattr(H,'observation',lambda p:{'orfs_status':'fail'})
    # Three empty candidates consume three turns; no flows and no extra recovery.
    from test_repair_turns import OK, _Resp
    monkeypatch.setattr(H.urllib.request,'urlopen',lambda *a,**k:_Resp(json.dumps({**OK,'choices':[{'message':{'content':'{}'}}]}).encode()))
    rows,_=T.strict_turns(H,project,'NB',env,'unused',SimpleNamespace(from_project=lambda *a:{}))
    assert len(rows)==3 and _spent(project,'repair_attempts')==3
    assert len([c for c,e in calls if str(c[1]).endswith('run_orfs.sh')])==1


@pytest.mark.parametrize('error,binding,flow_record',[
    ('GRT-0246',True,True),('GRT-0116',False,True),('DPL-0036',True,False)])
def test_partial_restore_cannot_bypass_unknown_install_binding_or_accounting_fault(ep,monkeypatch,error,binding,flow_record):
    project,_,_=ep;env=partial_task(project)
    T.restore(project,T.snapshot_state(project,0),execution_changed=True)
    recover_stub(project,env,monkeypatch,error=error,binding=binding,flow_record=flow_record)
    assert T.recover_execution(H,project,env)['execution_recovery']=='UNVERIFIED'
    assert (project/'reports/t2a-workspace-unsafe.json').exists()


@pytest.mark.parametrize('code',[75,76,-9,124,137])
def test_refusal_and_safety_codes_never_count_as_native_partial_restoration(ep,monkeypatch,code):
    project,_,_=ep;env=partial_task(project)
    T.restore(project,T.snapshot_state(project,0),execution_changed=True)
    recover_stub(project,env,monkeypatch,code=code)
    result=T.recover_execution(H,project,env)
    assert result['execution_recovery'] in ('SAFETY_STOP','SAFETY_OR_ACCOUNTING_STOP')
    assert 'failed_recovery' not in json.loads((project/'reports/t2a-workspace-unsafe.json').read_text())


def test_complete_baseline_cannot_be_downgraded_to_partial_verified(ep,monkeypatch):
    project,_,_=ep;env=partial_task(project)
    (project/'backend/RUN_00/final').mkdir();(project/'backend/RUN_00/final/6_final.odb').write_text('complete')
    T.restore(project,T.snapshot_state(project,0),execution_changed=True)
    recover_stub(project,env,monkeypatch)
    assert T.recover_execution(H,project,env)['execution_recovery']=='UNVERIFIED'


def test_sdc_change_invalidates_partial_baseline_proof(ep,monkeypatch):
    project,_,_=ep;env=partial_task(project)
    T.restore(project,T.snapshot_state(project,0),execution_changed=True)
    (project/'constraints/constraint.sdc').write_text('set clk_period 99\n')
    recover_stub(project,env,monkeypatch)
    assert T.recover_execution(H,project,env)['execution_recovery']=='UNVERIFIED'


def test_partial_continuation_at_turn_start_does_not_reset_the_three_turn_counter(ep,monkeypatch):
    project,_,respond=ep;env=partial_task(project)
    partial={'execution_recovery':'PARTIAL_VERIFIED','accepted':False,'final_layout':False}
    recovered=iter([partial,None,None])
    monkeypatch.setattr(T,'recover_execution',lambda *a,**k:next(recovered))
    monkeypatch.setattr(H,'_task_failure',lambda p:('orfs_stage','place',1))
    monkeypatch.setattr(H,'observation',lambda p:{'orfs_status':'fail'})
    from test_repair_turns import OK
    respond({**OK,'choices':[{'message':{'content':'{}'}}]})
    rows,_=T.strict_turns(H,project,'NB',env,'unused',SimpleNamespace(from_project=lambda *a:{}))
    assert [r['turn'] for r in rows]==[1,2,3] and _spent(project,'repair_attempts')==3


def test_old_baseline_evidence_cannot_be_modified_to_obtain_continuation(ep,monkeypatch):
    project,_,_=ep;env=partial_task(project)
    T.restore(project,T.snapshot_state(project,0),execution_changed=True)
    (project/'backend/RUN_00/flow.log').write_text('[ERROR GRT-0116] different failure\n')
    recover_stub(project,env,monkeypatch)
    assert T.recover_execution(H,project,env)['execution_recovery']=='UNVERIFIED'


def test_failed_clean_all_cannot_certify_a_fresh_partial_workspace(ep,monkeypatch):
    project,_,_=ep;env=partial_task(project)
    run=bound_run(project,'RUN_01',env['R2G_TASK_MANIFEST'],'DPL-0036',full=True)
    write(run/'cleanup.json',{'from_stage':'','clean_all_rc':2,'status':'FAIL'})
    assert T.partial_execution_proof(project,run,env,full_clean=True) is None


@pytest.mark.parametrize('code',[75,76,-9,124,137])
def test_candidate_safety_fault_stops_before_extract_or_cached_signoff(ep,monkeypatch,code):
    project,_,_=ep;env=partial_task(project)
    monkeypatch.setattr(H,'_task_failure',lambda p:('orfs_stage','place',1))
    monkeypatch.setattr(H,'observation',lambda p:{'orfs_status':'fail'})
    calls=[]
    monkeypatch.setattr(H,'run',lambda *a:calls.append(a) or code)
    monkeypatch.setattr(H,'final_verdict',lambda *a:pytest.fail('safety/accounting stop must not read acceptance'))
    rows,_=T.strict_turns(H,project,'NB',env,'unused',SimpleNamespace(from_project=lambda *a:{}))
    assert len(rows)==1 and rows[0]['status'] in ('SAFETY_STOP','SAFETY_OR_ACCOUNTING_STOP')
    assert _spent(project,'repair_attempts')==1 and len(calls)==1
    assert E_config(project)=='60'


def E_config(project):
    import t2a_envelope as E
    return E.effective_config((project/'constraints/config.mk').read_text())['CORE_UTILIZATION']
