"""DEV-only mechanism policy: honest costs and a pre-memory task entry."""
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import t2a_runtime as T
from test_repair_turns import H, ep, _spent, _turn


def test_task_entry_restores_control_state_but_never_costs_or_raw_runs(ep):
    project, _, _ = ep
    (project/'constraints/constraint.sdc').write_text('set clk_period 10\n')
    (project/'backend/RUN_INITIAL').mkdir(parents=True)
    report=project/'reports/route.json';report.write_text('{"status":"fail"}')
    ledger=project/'reports/resource_ledger.jsonl';ledger.write_text('cost before\n')
    fixlog=project/'reports/fix_log.jsonl';fixlog.write_text('attempt before\n')
    entry=T.task_entry_snapshot(project,{})
    (project/'constraints/config.mk').write_text('export CORE_UTILIZATION = 45\n')
    report.write_text('{"status":"clean"}')
    (project/'reports/antenna_nonconverged.json').write_text('memory branch control')
    ledger.write_text('cost before\ncost memory\n');fixlog.write_text('attempt before\nfailed memory\n')
    (project/'backend/RUN_REJECTED').mkdir()
    T.restore_task_entry(project,entry)
    assert (project/'constraints/config.mk').read_text()=='export CORE_UTILIZATION = 60\n'
    assert report.read_text()=='{"status":"fail"}'
    assert not (project/'reports/antenna_nonconverged.json').exists()
    assert (entry/'rejected/reports/antenna_nonconverged.json').read_text()=='memory branch control'
    assert ledger.read_text()=='cost before\ncost memory\n'
    assert fixlog.read_text()=='attempt before\nfailed memory\n'
    assert (project/'backend/RUN_REJECTED').exists()
    assert (project/'reports/t2a-workspace-unsafe.json').exists()


def test_task_entry_fallback_is_a_new_none_backend_process_and_keeps_spent_turns(ep,monkeypatch):
    project, _, _ = ep
    (project/'constraints/constraint.sdc').write_text('set clk_period 10\n')
    (project/'backend').mkdir()
    entry=T.task_entry_snapshot(project,{})
    _turn(project)
    (project/'constraints/config.mk').write_text('export CORE_UTILIZATION = 45\n')
    seen=[]
    def recover(*args,**kwargs):
        assert (project/'constraints/config.mk').read_text()=='export CORE_UTILIZATION = 60\n'
        if seen:return None  # a successful fresh recovery cleared the unsafe marker
        seen.append('charged recovery')
        return {'accepted':False,'execution_recovery':'PARTIAL_VERIFIED'}
    monkeypatch.setattr(T,'recover_execution',recover)
    def engineer(p,arm,env):
        assert arm=='NB' and env['R2G_MEMORY_BACKEND']=='none'
        assert env['R2G_ENGINEER_BRANCH']=='task_fallback'
        assert 'R2G_RECIPE_FILE' not in env and 'TEHM_DB' not in env
        seen.append('baseline policy')
        return {'accepted':True,'fix_rows':[]}
    monkeypatch.setattr(H,'run_engineer',engineer)
    got,env,meta=T.task_entry_fallback(H,project,{'R2G_MEMORY_BACKEND':'tehm',
        'TEHM_DB':'private','R2G_RECIPE_FILE':'recipe'},entry,lambda *a,**k:None)
    assert got['accepted'] and meta['executed']
    assert seen==['charged recovery','baseline policy']
    assert _spent(project,'repair_attempts')==1
    assert env['R2G_MEMORY_BACKEND']=='none'
    _turn(project);_turn(project)
    content,usage=_turn(project)
    assert content is None and usage=={'repair_allowance_exhausted':True}
    assert _spent(project,'repair_attempts')==3
    with pytest.raises(RuntimeError,match='already'):
        T.task_entry_fallback(H,project,env,entry,lambda *a,**k:None)


def test_task_entry_fallback_stops_on_unverified_recovery_without_running_catalogue(ep,monkeypatch):
    project, _, _=ep
    (project/'constraints/constraint.sdc').write_text('set clk_period 10\n')
    (project/'backend').mkdir()
    entry=T.task_entry_snapshot(project,{})
    monkeypatch.setattr(T,'recover_execution',lambda *a,**k:{'accepted':False,'execution_recovery':'UNVERIFIED'})
    monkeypatch.setattr(H,'run_engineer',lambda *a,**k:pytest.fail('unsafe catalogue launch'))
    got,_,_=T.task_entry_fallback(H,project,{},entry,lambda *a,**k:None)
    assert got['execution_recovery']=='UNVERIFIED'


def test_priority_requires_matched_own_full_task_evidence_and_known_costs():
    from t2a_mechanisms import prioritize
    candidates=[{'id':'tehm_x','rule_id':'tehm_rule:x'}, {'id':'tehm_y','rule_id':'tehm_rule:y'}]
    sit={'check':'orfs_stage','platform':'sky130hd','violation_class':'route'}
    row={'rule_id':'tehm_rule:x','situation':sit,'sample_id':'dev:1','split':'DEV',
         'memory_status':'ACCEPTED','baseline_status':'ACCEPTED','rolled_back':False,
         'costs':{'memory':{'flows':2,'tokens':0,'wall_s':100},
                  'baseline':{'flows':4,'tokens':0,'wall_s':200}}}
    front,deferred=prioritize(candidates,sit,[row])
    assert [s['id'] for s in front]==['tehm_x'] and [s['id'] for s in deferred]==['tehm_y']
    for change in [dict(rolled_back=True),dict(memory_status='NOT_ACCEPTED'),
                   dict(split='TEST'),dict(situation={**sit,'platform':'sky130hs'}),
                   dict(rule_id='other'),dict(memory_status='UNVERIFIED_OR_SAFETY_STOP')]:
        assert not prioritize(candidates,sit,[{**row,**change}])[0]
    unknown=copy.deepcopy(row);unknown['costs']['memory']['tokens']=None
    assert not prioritize(candidates,sit,[unknown])[0]
    no_savings=copy.deepcopy(row);no_savings['costs']['memory']=copy.deepcopy(row['costs']['baseline'])
    assert not prioritize(candidates,sit,[no_savings])[0]
    loss={**row,'sample_id':'dev:2','memory_status':'NOT_ACCEPTED'}
    assert not prioritize(candidates,sit,[row,loss])[0]


def test_priority_counts_duplicate_observations_once_and_never_borrows_another_rule():
    from t2a_mechanisms import prioritize
    candidate={'id':'tehm_x','rule_id':'tehm_rule:x'};sit={'check':'drc'}
    row={'rule_id':'tehm_rule:x','situation':sit,'sample_id':'dev:1','split':'DEV',
         'memory_status':'ACCEPTED','baseline_status':'NOT_ACCEPTED','rolled_back':False,
         'costs':{'memory':{'flows':3,'tokens':120,'wall_s':100},
                  'baseline':{'flows':2,'tokens':80,'wall_s':90}}}
    front,_=prioritize([candidate],sit,[row,row])
    assert front[0]['task_priority_evidence']['pairs']==1
    assert front[0]['task_priority_evidence']['gains']==1
    assert not prioritize([candidate],sit,[{**row,'rule_id':'other'}])[0]
    assert not prioritize([candidate],sit,[row,{**row,'memory_status':'NOT_ACCEPTED'}])[0]


def test_incomplete_and_test_cohorts_cannot_supply_priority_samples(tmp_path,monkeypatch):
    import t2a_batch as B
    from t2a_mechanisms import samples_from_dev
    def rejected(root,split):
        assert split=='DEV'
        raise RuntimeError('Not an audited DEV package')
    monkeypatch.setattr(B,'preflight',rejected)
    with pytest.raises(RuntimeError,match='DEV'):samples_from_dev(tmp_path)


def test_fallback_detection_does_not_treat_a_rolled_back_trial_as_retained():
    row={'memory_rule':'r','from_stage':'floorplan','rolled_back':False}
    assert T.retained_memory_action([row])
    assert not T.retained_memory_action([{**row,'rolled_back':True}])
    assert not T.retained_memory_action([{**row,'from_stage':None}])


def test_missing_or_unknown_policy_fields_cannot_enable_a_candidate(tmp_path):
    from t2a_mechanisms import load_policy,SCHEMA
    path=tmp_path/'policy.json'
    policy={'schema':SCHEMA,'task_entry_fallback':True,'cost_priority':True,'priority_samples':[]}
    path.write_text(json.dumps(policy));assert load_policy(path)==policy
    for changes in [{'task_entry_fallback':'true'},{'unexpected':True},{'priority_samples':[{'split':'TEST'}]}]:
        path.write_text(json.dumps({**policy,**changes}))
        with pytest.raises(ValueError):load_policy(path)


def test_dev_gate_needs_zero_losses_and_a_measured_gain_over_every_registered_pair():
    from t2a_mechanisms import paired_gate
    expected=[{'task':t,'arm':a,'repeat':1} for t in ['dev/a','dev/b'] for a in ['NB','TM0']]
    rows=[{**r,'split':'DEV','paper_data':False,'accounting_passed':True,'status':'ACCEPTED',
           'costs':{'flows':2 if r['arm']=='TM0' else 3,'tokens':100,'wall_s':20}} for r in expected]
    report=paired_gate(rows,expected)
    assert report['dev_release_gate_passed'] and report['cost_deltas_all_registered_pairs']['flows']==-2
    assert not report['formal_launch_allowed']
    for changed in [rows[:-1],rows+[rows[-1]],
                    [{**r,'status':'NOT_ACCEPTED'} if r['arm']=='TM0' else r for r in rows],
                    [{**r,'costs':{**r['costs'],'tokens':None}} if r['task']=='dev/b' else r for r in rows],
                    [{**r,'status':'UNVERIFIED_OR_SAFETY_STOP'} if r['task']=='dev/b' else r for r in rows],
                    [{**r,'split':'TEST'} for r in rows]]:
        assert not paired_gate(changed,expected)['dev_release_gate_passed']


def test_priority_does_not_cross_clock_area_or_allowed_action_contexts():
    from t2a_mechanisms import prioritize,quality_context
    man={'platform':'sky130hd','expected_clock_period_ns':10,
         'area':{'core':{'max_rel_increase':1}},'allowlist':{'CORE_UTILIZATION':{'min':10}}}
    scope=quality_context(man);candidate={'id':'m','rule_id':'r'};sit={'check':'route'}
    row={'rule_id':'r','situation':sit,'sample_id':'d1','split':'DEV','rolled_back':False,
         'quality_context':scope,'memory_status':'ACCEPTED','baseline_status':'NOT_ACCEPTED',
         'costs':{arm:{'flows':3,'tokens':0,'wall_s':30} for arm in ('memory','baseline')}}
    assert prioritize([candidate],sit,[row],scope)[0]
    for patch in [{'expected_clock_period_ns':5},{'area':{'core':{'max_rel_increase':0.5}}},
                  {'allowlist':{}},{'platform':'sky130hs'}]:
        assert not prioritize([candidate],sit,[row],quality_context({**man,**patch}))[0]
    with pytest.raises(ValueError):quality_context({**man,'expected_clock_period_ns':None})
