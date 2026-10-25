"""Evidence-bound runtime priority, independent of experiment drivers."""
import copy
import json
import pytest

def test_priority_requires_matched_own_full_task_evidence_and_known_costs():
    from tehm.policy.priority import prioritize
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
    from tehm.policy.priority import prioritize
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






def test_missing_or_unknown_policy_fields_cannot_enable_a_candidate(tmp_path):
    from tehm.policy.priority import load_policy,SCHEMA
    path=tmp_path/'policy.json'
    policy={'schema':SCHEMA,'task_entry_fallback':True,'cost_priority':True,'priority_samples':[]}
    path.write_text(json.dumps(policy));assert load_policy(path)==policy
    for changes in [{'task_entry_fallback':'true'},{'unexpected':True},{'priority_samples':[{'split':'TEST'}]}]:
        path.write_text(json.dumps({**policy,**changes}))
        with pytest.raises(ValueError):load_policy(path)




def test_priority_does_not_cross_clock_area_or_allowed_action_contexts():
    from tehm.policy.priority import prioritize,quality_context
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
