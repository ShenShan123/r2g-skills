"""Offline counterexamples: never call the provider or an EDA executable."""
import copy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'code/memory/scripts'), str(ROOT/'code/memory')]
import figure2_policy as F
import t2_tehm_execution_policy as P
import t2a_runtime as T


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    yaml = tmp_path/'variables.yaml'
    yaml.write_text('SKIP_ANTENNA_REPAIR:\n  default: 0\nMAX_REPAIR_ANTENNAS_ITER_DRT:\n  default: 5\n')
    sit = {'platform':'nangate45', 'check':'drc', 'die_mode':'auto', 'util_band':'13-30',
           'timeout':False, 'violation_class':'METAL5_ANTENNA'}
    action = {'id':'safe', 'auto_apply':True, 'config_edits':{
        'SKIP_ANTENNA_REPAIR':'1', 'MAX_REPAIR_ANTENNAS_ITER_DRT':'10'},
        'recheck':'drc', 'rerun_from':'route'}
    units=[]; entries=[]
    for i in range(2):
        uid='fixture-'+str(i)
        units.append({'unit_id':uid, 'source_eligible':True,
            'shared_label':{'signature':'drc:METAL5_ANTENNA', 'admitted':True,
                'outcome_class':'admitted_positive', 'binding':{'pre':'KNOWN','post':'KNOWN'},
                'rolled_back':{'status':'KNOWN','value':False}},
            'raw_row':{'situation':dict(sit), 'from_stage':'route', 'config_delta':action['config_edits'],
                'config_before':{}, 'pre_run_tag':'pre', 'post_run_tag':'post', 'rolled_back':False,
                'acceptance_result':{'accepted':True, 'acceptance':{'accepted':True,'failed':[],'unknown':[]},
                                     'envelope':{'status':'PASS'}}}})
        for knob, after in action['config_edits'].items():
            entries.append({'unit_id':uid, 'source_group':'group'+str(i), 'ip_family':'family'+str(i),
                'task':str(i), 'signature':'drc:METAL5_ANTENNA', 'signature_type':'drc',
                'knob':knob, 'direction':'set', 'outcome':'admitted_positive'})
    receipt=tmp_path/'effective-config.json'
    receipt.write_text(json.dumps({'status':'PASS','errors':[], 'effective':{
        'SKIP_ANTENNA_REPAIR':{'origin':'file','value':'0'},
        'MAX_REPAIR_ANTENNAS_ITER_DRT':{'origin':'file','value':'5'}}}))
    execution={'path':str(receipt), 'sha256':hashlib.sha256(receipt.read_bytes()).hexdigest()}
    registry={'schema':'figure2-effective-defaults-v1', 'variables_file':str(yaml),
        'variables_sha256':hashlib.sha256(yaml.read_bytes()).hexdigest(),
        'defaults':{'SKIP_ANTENNA_REPAIR':0, 'MAX_REPAIR_ANTENNAS_ITER_DRT':5},
        'historical_units':{u['unit_id']:{'unit_sha256':digest(u),'pre_execution':dict(execution)} for u in units}}
    monkeypatch.setattr(P, 'precondition_registry', lambda:registry, raising=False)
    plan={'strategies':[action], 'tehm_v2':{'state':{'signature':'drc:METAL5_ANTENNA',
        'failures':['drc:METAL5_ANTENNA']}, 'decisions':[{'id':'safe','decision':'kept'}]}}
    execution['values']=json.loads(receipt.read_text())['effective']
    return SimpleNamespace(plan=plan, sit=sit, units=units, index={'entries':entries}, registry=registry,
                           yaml=yaml, execution=execution, receipt=receipt)


def choose(e, cfg=None):
    return P.choose(plan=e.plan, situation=e.sit, cfg={} if cfg is None else cfg,
        units=e.units, index=e.index, exclude_families=set(), used=[], effective_execution=e.execution)


def test_verified_tool_defaults_resolve_missing_current_and_historical_values(evidence):
    d=choose(evidence)
    assert d['choice']=='local_experience', d
    assert len(d['witness_ids'])==2
    assert d['preconditions']['SKIP_ANTENNA_REPAIR']['value']==0
    assert d['preconditions']['MAX_REPAIR_ANTENNAS_ITER_DRT']['value']==5


def test_explicit_none_must_not_fall_back_to_default(evidence):
    assert choose(evidence, {'SKIP_ANTENNA_REPAIR':None})['reason']=='unknown_action_precondition'


def test_explicit_config_wins_over_default(evidence):
    d=choose(evidence, {'SKIP_ANTENNA_REPAIR':'1', 'MAX_REPAIR_ANTENNAS_ITER_DRT':'10'})
    assert d['reason']=='no_effect'


def test_unbound_historical_version_cannot_borrow_current_defaults(evidence):
    evidence.registry['historical_units']={}
    assert choose(evidence)['candidate'] is None


def test_changed_historical_unit_cannot_borrow_defaults(evidence):
    evidence.units[0]['raw_row']['config_before']['unrelated']='changed'
    assert choose(evidence)['candidate'] is None


def test_unknown_signature_is_not_rewritten_to_current_rule(evidence):
    for u in evidence.units:u['shared_label']['signature']='drc:None'
    evidence.registry['historical_units']={u['unit_id']:{'unit_sha256':digest(u)} for u in evidence.units}
    assert choose(evidence)['candidate'] is None


def test_no_current_execution_receipt_is_unknown_even_with_known_version(evidence):
    evidence.execution=None
    assert choose(evidence)['reason']=='unknown_action_precondition'


def test_environment_override_is_not_silently_replaced_by_default(evidence):
    evidence.execution['values']['SKIP_ANTENNA_REPAIR']={'origin':'environment','value':'1'}
    d=choose(evidence)
    assert d['preconditions']['SKIP_ANTENNA_REPAIR']['value']==1
    assert d['candidate'] is None   # historical pre-state was 0, not 1


def test_old_version_without_pre_execution_receipt_is_unknown(evidence):
    for proof in evidence.registry['historical_units'].values():proof.pop('pre_execution')
    assert choose(evidence)['candidate'] is None


def test_tampered_historical_execution_receipt_is_an_integrity_error(evidence):
    evidence.receipt.write_text('{}')
    with pytest.raises(ValueError,match='receipt changed'):choose(evidence)


@pytest.mark.parametrize('bad',[None, '', '$(OTHER_KNOB)', float('nan'), float('inf'), True])
def test_unresolved_or_nonfinite_current_values_are_not_known(evidence,bad):
    assert choose(evidence,{'SKIP_ANTENNA_REPAIR':bad})['reason']=='unknown_action_precondition'


def test_tampered_tool_declarations_fail_load(evidence,monkeypatch,tmp_path):
    import t2_tehm_effective_state as S
    registry=tmp_path/'registry.json';registry.write_text(json.dumps(evidence.registry))
    monkeypatch.setattr(S,'REGISTRY',registry)
    assert S.load_registry()['defaults']['SKIP_ANTENNA_REPAIR']==0
    evidence.yaml.write_text('SKIP_ANTENNA_REPAIR:\n  default: 1\n')
    with pytest.raises(ValueError,match='declaration changed'):S.load_registry()


@pytest.mark.parametrize('defect',['none','stale_config','outside_run','failed_receipt','missing_receipt'])
def test_current_receipt_is_owned_fresh_and_passed(tmp_path,defect):
    from t2_tehm_effective_state import current_execution
    project=tmp_path/'project';run=project/'backend/RUN_owned';run.mkdir(parents=True)
    cfg=project/'config.mk';cfg.write_text('export CORE_UTILIZATION = 20\n')
    path=run/'effective-config.json'
    path.write_text(json.dumps({'status':'PASS','errors':[],
        'project_config_sha256':hashlib.sha256(cfg.read_bytes()).hexdigest(),
        'effective':{'SKIP_ANTENNA_REPAIR':{'origin':'file','value':'0'}}}))
    pointer=project/'backend/.r2g_signoff_run';pointer.write_text(json.dumps({'run_dir':str(run)}))
    if defect=='stale_config':cfg.write_text('changed')
    elif defect=='outside_run':pointer.write_text(json.dumps({'run_dir':str(tmp_path)}))
    elif defect=='failed_receipt':path.write_text(json.dumps({'status':'FAIL'}))
    elif defect=='missing_receipt':path.unlink()
    result=current_execution(project,cfg)
    assert (result is not None)==(defect=='none')


class FakeMemory:
    def __init__(self, action=None):
        self.H=None; self.project=ROOT; self.experience_disabled=False; self.guidance_active=False
        self.action=action; self.last_native_decision={'witness_ids':[]}
    def native_action(self):return self.action
    def agent_context(self, observation, gateway):
        return '' if self.experience_disabled else 'bounded historical advice'


@pytest.mark.parametrize('variant', ['TEHM_FIXED','TEHM_SELECTOR'])
def test_no_executable_action_does_not_disable_advice(monkeypatch,variant):
    monkeypatch.setattr(T,'event',lambda *args:None)
    m=F.configure_memory(FakeMemory(),variant)
    assert m.native_action() is None
    assert m.experience_disabled is False
    assert m.agent_context({},None)=='bounded historical advice'


def test_cost_rejection_preserves_the_same_advice_fallback(monkeypatch):
    monkeypatch.setattr(T,'event',lambda *args:None)
    monkeypatch.setattr(P,'load_evidence',lambda *args:([],None,None,None))
    m=FakeMemory({'id':'safe'}); m.root=ROOT;m.env={}
    m=F.configure_memory(m,'TEHM_SELECTOR')
    assert m.native_action() is None
    assert m.experience_disabled is False
    assert m.agent_context({},None)=='bounded historical advice'


def test_native_risk_withdrawal_is_not_reenabled(monkeypatch):
    monkeypatch.setattr(T,'event',lambda *args:None)
    m=FakeMemory(); m.experience_disabled=True
    m=F.configure_memory(m,'TEHM_SELECTOR')
    assert m.native_action() is None
    assert m.experience_disabled is True
    assert m.agent_context({},None)==''


@pytest.mark.parametrize('defect',['not_admitted','rollback_unknown','rolled_back','pre_unbound',
    'post_unbound','not_accepted','unknown_acceptance','bad_envelope','one_source',
    'harmful_evidence','multiple_failures','different_pre_state'])
def test_default_resolution_does_not_bypass_any_admission_gate(evidence,defect):
    e=evidence
    for u in e.units:
        lab=u['shared_label'];raw=u['raw_row'];acc=raw['acceptance_result']
        if defect=='not_admitted':lab['admitted']=False
        elif defect=='rollback_unknown':lab['rolled_back']['status']='UNKNOWN'
        elif defect=='rolled_back':raw['rolled_back']=True;lab['rolled_back']['value']=True
        elif defect=='pre_unbound':lab['binding']['pre']='UNKNOWN'
        elif defect=='post_unbound':lab['binding']['post']='UNKNOWN'
        elif defect=='not_accepted':acc['accepted']=False
        elif defect=='unknown_acceptance':acc['acceptance']['unknown']=['setup']
        elif defect=='bad_envelope':acc['envelope']['status']='UNVERIFIED'
        elif defect=='different_pre_state':raw['config_before']['SKIP_ANTENNA_REPAIR']='1'
        e.registry['historical_units'][u['unit_id']]['unit_sha256']=digest(u)
    if defect=='one_source':
        for row in e.index['entries']:row['source_group']='same_source'
    elif defect=='harmful_evidence':e.index['entries'][0]['outcome']='harm'
    elif defect=='multiple_failures':e.plan['tehm_v2']['state']['failures'].append('setup')
    assert choose(e)['candidate'] is None


def test_fixed_and_selector_keep_identical_bounded_advice_on_cost_rejection(monkeypatch):
    events=[]
    monkeypatch.setattr(T,'event',lambda *a:events.append(a[-1]))
    monkeypatch.setattr(P,'load_evidence',lambda *a:([],None,None,None))
    fixed=FakeMemory({'id':'safe'});selector=FakeMemory({'id':'safe'})
    for m in (fixed,selector):m.root=ROOT;m.env={}
    fixed=F.configure_memory(fixed,'TEHM_FIXED');selector=F.configure_memory(selector,'TEHM_SELECTOR')
    assert fixed.native_action()=={'id':'safe'}
    assert selector.native_action() is None
    assert fixed.agent_context({},None)==selector.agent_context({},None)
    assert fixed.figure2_attempts==1 and selector.figure2_attempts==0
    assert any(e.get('selector_scope')=='local_execution_only' for e in events)


@pytest.mark.parametrize('variant',['TEHM_FIXED','RECIPE'])
def test_three_local_proposals_remains_a_hard_ceiling(monkeypatch,variant):
    monkeypatch.setattr(T,'event',lambda *args:None)
    m=F.configure_memory(FakeMemory({'id':'safe'}),variant)
    assert all(m.native_action() for _ in range(3))
    assert m.native_action() is None
    assert m.figure2_attempts==3
