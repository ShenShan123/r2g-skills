import copy
import importlib.util
import json
from pathlib import Path
import sys

import pytest

SCRIPTS=Path(__file__).parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))
import t2_freepdk45_main as M


def item(i,repo=None,fingerprint=None):
    return {'handoff_id':i,'repository_url':repo or 'https://github.com/author/'+i,
        'frontend_route':'verilog-native','structural_fingerprint':fingerprint or i}


def row(i):
    return {'design':i+'__top__repo','clock_kind':'real','period':'5','run_dir':i,
            'status':'fail','failure_class':'DRC','cells':'9999'}


def test_selection_is_reproducible_and_outcome_blind():
    inv=[item(str(i)) for i in range(30)];rr=[row(str(i)) for i in range(30)]
    expected=M.select_candidates(inv,rr,set(),10)[0]
    for r in rr:r.update(status='pass',failure_class='none',cells='1',area_um2='0',worst_slack='-99')
    assert M.select_candidates(list(reversed(inv)),list(reversed(rr)),set(),10)[0]==expected


def test_source_repo_exclusion_without_author_or_module_name_overexclusion():
    inv=[item('A','https://github.com/same-owner/a'),item('B','https://github.com/same-owner/a.git'),
         item('C','https://github.com/same-owner/c')]
    chosen,excluded=M.select_candidates(inv,[row(x) for x in 'ABC'],{'A'},1)
    assert chosen[0]['handoff_id']=='C'


def test_duplicate_structural_fingerprints_not_independent_designs():
    inv=[item('A',fingerprint='same'),item('B',fingerprint='same'),item('C')]
    chosen,_=M.select_candidates(inv,[row(x) for x in 'ABC'],set(),2)
    assert len({r['structural_fingerprint'] for r in chosen})==2


@pytest.mark.parametrize('bad',['clock','frontend','missing'])
def test_missing_input_eligibility_is_not_a_repair_failure(bad):
    inv=[item('A')];rr=[row('A')]
    if bad=='clock':rr[0]['clock_kind']='virtual'
    if bad=='frontend':inv[0]['frontend_route']='ghdl-08'
    if bad=='missing':rr=[]
    with pytest.raises(ValueError,match='Insufficient'):M.select_candidates(inv,rr,set(),1)


def test_reme_excluded_in_execution_and_paid_construction():
    assert M.ARMS==('NB','RET','RECIPE','EXPEL','AWM','AMEM','ADAMEM','REASONINGBANK','TM0')
    assert M.BUILDS==('EXPEL','AWM','AMEM','REASONINGBANK')
    assert len(M.ARMS)*50==450


def test_method_replacement_cannot_reselect_scientific_inputs(tmp_path,monkeypatch):
    monkeypatch.setattr(M,'SELECTION_BASE',tmp_path)
    M.save(tmp_path/'selection.json',{'selected':[{'handoff_id':'A'}]})
    M.save(tmp_path/'registration.json',{'files':{str(tmp_path/'selection.json'):M.sha(tmp_path/'selection.json')}})
    assert M.verify_same_roster([{'handoff_id':'A'}])['sha256']==M.sha(tmp_path/'selection.json')
    with pytest.raises(ValueError,match='retain the frozen'):M.verify_same_roster([{'handoff_id':'B'}])
    M.save(tmp_path/'selection.json',{'selected':[{'handoff_id':'B'}]})
    with pytest.raises(ValueError,match='receipt changed'):M.verify_same_roster([{'handoff_id':'B'}])


@pytest.mark.parametrize('value',[None,True,float('nan'),float('inf'),1e39,'1.0'])
def test_unknown_not_numeric_zero(value):
    assert M.finite(value) is None
    assert M.relative(value,1) is None


def test_zero_slack_is_valid_but_zero_reference_is_not():
    assert M.finite(0)==0
    assert M.relative(2,0) is None
    assert M.relative(3,2)==.5


def ppa_fixture(tmp_path):
    project=tmp_path/'episode';(project/'reports').mkdir(parents=True)
    run=project/'backend/RUN_0';run.mkdir(parents=True)
    M.save(project/'manifest.json',{'area':{k:{'ref':100} for k in ('stdcell','core','die')}})
    M.save(project/'reports/ppa.json',{'run_tag':'RUN_0','run_dir':str(run),
        'geometry':{'stdcell_area_um2':80,'core_area_um2':150,'die_area_um2':160},
        'summary':{'timing':{'setup_wns':0,'hold_wns':.1,'setup_tns':0},'power':{'total_power_w':.01}}})
    return project,{'status':'ACCEPTED','final':{'judged_run':'RUN_0'}}


def test_ppa_reports_cell_core_die_separately(tmp_path):
    project,e=ppa_fixture(tmp_path);got=M.ppa(project,e)
    assert got['status']=='MEASURED'
    assert got['initial_area_relative_change']=={'stdcell':-.19999999999999996,'core':.5,'die':.6000000000000001}
    assert got['values']['setup_slack_ns']==0
    assert got['report_sha256']==M.sha(project/'reports/ppa.json')


def test_stale_ppa_does_not_become_terminal_measurement(tmp_path):
    project,e=ppa_fixture(tmp_path);e['final']['judged_run']='RUN_1'
    assert M.ppa(project,e)['status']=='UNKNOWN'


def test_missing_power_and_unconstrained_slack_are_unknown(tmp_path):
    project,e=ppa_fixture(tmp_path);p=project/'reports/ppa.json';d=M.load(p)
    d['summary']['power']={};d['summary']['timing']['setup_wns']=1e39;M.save(p,d)
    got=M.ppa(project,e)
    assert got['status']=='MEASURED_PARTIAL'
    assert got['values']['setup_slack_ns'] is None and got['values']['total_power_w'] is None


def test_platform_appropriate_routing_layers_and_shared_contract():
    import t2a_task_manifests as TM
    a=TM.allowlist({'PLATFORM':'nangate45'});b=TM.allowlist({'PLATFORM':'sky130hd'})
    assert a['MAX_ROUTING_LAYER']['values']==['metal'+str(i) for i in range(2,11)]
    assert b['MAX_ROUTING_LAYER']['values']==['met1','met2','met3','met4','met5']
    assert set(a)==set(b)


def test_shared_raw_history_keeps_real_platform(tmp_path):
    nb=tmp_path/'nb';p=nb/'episodes/A__initial__NB__r1';(p/'reports').mkdir(parents=True)
    M.save(p/'manifest.json',{'platform':'nangate45','initial_config':{'PLATFORM':'nangate45'}})
    M.save(p/'episode.json',{'status':'NOT_ACCEPTED','started':1})
    (p/'reports/resource_ledger.jsonl').write_text('')
    units,_=M.F.raw_units(nb,{'A':{'task':'A/initial'}})
    assert units and all(u['identity']['platform']==u['lineage']['platform']=='nangate45' for u in units)


def test_worker_wrapper_changes_only_entrypoint(monkeypatch):
    captured=[];monkeypatch.setattr(M.subprocess,'Popen',lambda cmd,**kw:captured.append((cmd,kw)))
    cmd=['taskset','-c','0,1','python3','/p/t2a_batch.py','episode','--arm','TM0']
    M.worker_popen(cmd,env={'test':'yes'})
    assert cmd[4]=='/p/t2a_batch.py'
    assert captured[0][0][4]=='/p/t2_agent_request_resume.py'
    assert captured[0][0][5:]==cmd[5:]
    with pytest.raises(ValueError):M.worker_popen(['a']*6)


def test_provenance_failure_is_technical_but_measured_drc_failure_is_not():
    import t2a_runtime as T
    checks={k:'pass' for k in T.ACCEPTANCE_CHECKS}
    checks['A11_provenance']='fail'
    final={'accepted':False,'acceptance':{'accepted':False,'checks':checks,'unknown':[]},'verification_stop':1}
    assert T.terminal_status(final)=='UNVERIFIED_OR_SAFETY_STOP'
    checks['A11_provenance']='pass';checks['A2_drc_full_deck']='fail'
    assert T.terminal_status(final)=='NOT_ACCEPTED'


def test_delta_preserves_other_private_runtime_code_and_refuses_unknown_seams():
    batch="other code\n        record['initial']=initial\nend\n"
    runtime="prefix\n    checks=acceptance.get('checks') or {}\nsuffix\n"
    b,r=M.integrate_provenance_guards(batch,runtime)
    assert b.startswith('other code\n') and b.endswith('end\n')
    assert 'Technical signoff/provenance failure before repair' in b
    assert r.startswith('prefix\n') and r.endswith('suffix\n')
    assert "return 'UNVERIFIED_OR_SAFETY_STOP'" in r
    with pytest.raises(ValueError):M.integrate_provenance_guards('unexpected',runtime)
