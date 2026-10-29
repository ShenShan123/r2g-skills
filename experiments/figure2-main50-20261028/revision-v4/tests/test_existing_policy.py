import copy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'code/memory/scripts'),str(ROOT/'code/memory')]
import figure2_policy as P


def witness(**changes):
    result={'unit_id':'fixture','shared_label':{'admitted':True,'outcome_class':'admitted_positive',
        'rolled_back':{'status':'KNOWN','value':False},'cost':{'flows_status':'KNOWN','flows':1}},
        'raw_row':{'acceptance_result':{'accepted':True,'acceptance':{'accepted':True},
        'envelope':{'status':'PASS'},'resources':{'open_holds':0,'unknown_occupancies':[],
        'flows_by_component':{'initial':1},'spent':{'flows':2,'tokens_in':50,'tokens_out':5,'llm_requests':1}}}}}
    result.update(changes)
    return result


def test_selector_permits_known_saving_without_making_a_model_call():
    decision=P.worth_local_trial([witness()])
    assert decision['use_memory'] is True
    assert decision['mean_local_step_flows']==1
    assert decision['mean_historical_R2G_online_tokens']==55


def test_no_counterfactual_or_no_witness_is_not_invented():
    assert P.worth_local_trial([])['use_memory'] is False
    assert 'not a causal' in P.worth_local_trial([])['authority']


@pytest.mark.parametrize('defect',['unknown_cost','rolled_back','unadmitted','not_accepted','unknown_usage','open_hold','nonfinite','zero_tokens','flow_penalty'])
def test_uncertain_or_non_saving_witness_defer_to_no_memory(defect):
    u=witness();lab=u['shared_label'];acc=u['raw_row']['acceptance_result'];r=acc['resources']
    if defect=='unknown_cost':lab['cost']['flows_status']='UNKNOWN'
    elif defect=='rolled_back':lab['rolled_back']['value']=True
    elif defect=='unadmitted':lab['admitted']=False
    elif defect=='not_accepted':acc['accepted']=False
    elif defect=='unknown_usage':r['unknown_occupancies']=[{'actual':'UNKNOWN'}]
    elif defect=='open_hold':r['open_holds']=1
    elif defect=='nonfinite':r['spent']['tokens_out']=float('inf')
    elif defect=='zero_tokens':r['spent'].update(tokens_in=0,tokens_out=0)
    elif defect=='flow_penalty':lab['cost']['flows']=3
    assert P.worth_local_trial([u])['use_memory'] is False


class Memory:
    def __init__(self):
        self.H=None;self.project=ROOT;self.experience_disabled=False;self.guidance_active=False
        self.count=0
    def native_action(self):self.count+=1;return {'id':'candidate'+str(self.count)}
    def agent_context(self,observation,gateway):return 'bounded original advice'


def test_fixed_tehm_only_three_local_proposals(monkeypatch):
    import t2a_runtime as T
    events=[];monkeypatch.setattr(T,'event',lambda *a:events.append(a))
    m=P.configure_memory(Memory(),'TEHM_FIXED')
    assert all(m.native_action() for _ in range(3))
    assert m.native_action() is None and m.count==3 and m.figure2_attempts==3
    assert m.agent_context({},None)=='bounded original advice'


def test_pure_recipe_cannot_enter_online_fallback(monkeypatch):
    import t2_agent_dispatch as D
    import t2a_runtime as T
    monkeypatch.setattr(T,'event',lambda *a:None)
    m=P.configure_memory(Memory(),'RECIPE')
    with pytest.raises(D.OnlineAllowanceSpent):m.agent_context({},None)
    assert m.figure2_stop=='no_applicable_recipe'


def test_selector_never_bypasses_absent_safe_candidate(monkeypatch):
    import t2a_runtime as T
    monkeypatch.setattr(T,'event',lambda *a:None)
    m=Memory();m.native_action=lambda:None
    m=P.configure_memory(m,'TEHM_SELECTOR')
    assert m.native_action() is None
    assert m.experience_disabled is False and m.figure2_attempts==0


def test_exact_roster_and_history_are_shared_across_variants():
    plan=json.loads((ROOT/'plan.json').read_text())
    assert len(plan['jobs'])+len(plan['carried_cells'])==150 and plan['reused_R2G']==50
    assert plan['new_online_requests_max']==3*sum(j['variant']!='RECIPE' for j in plan['jobs'])
    for design in {j['design'] for j in plan['jobs']}:
        group=[j for j in plan['jobs'] if j['design']==design]
        assert {j['variant'] for j in group}==set(P.VARIANTS)
        assert len({(Path(j['root'])/'shared/shared_units.jsonl').resolve() for j in group})==1
        assert len({(Path(j['root'])/'agent-store/tehm.sqlite').resolve() for j in group})==1
        assert all(not (Path(j['root'])/'episodes').exists() for j in group)


def test_new_runtime_difference_is_isolated_and_baseline_compatible():
    import experiment as E
    base=E.BASE
    ref=json.loads((ROOT/'baseline-reference.json').read_text())
    for rel,digest in ref['shared_executor_files'].items():
        assert E.sha(ROOT/'code'/rel)==digest==E.sha(base/'nb/code'/rel)
    assert len(ref['rows'])==50
    assert all(r['cost']['requests']<=3 for r in ref['rows'])
    assert sum(r['status']=='ACCEPTED' for r in ref['rows'])==25
    assert ref['retained_incomplete']==11


def test_own_and_same_ip_never_enter_history():
    plan=json.loads((ROOT/'plan.json').read_text())
    for job in [j for j in plan['jobs'] if j['variant']=='TEHM_FIXED']:
        root=Path(job['root']);exclude=json.loads((root/'exclusions.json').read_text())
        units=[json.loads(s) for s in (root/'shared/shared_units.jsonl').read_text().splitlines()]
        # A technically excluded original NB has no source unit to remove.
        # Its origin need not appear in the set of removed, present origins.
        assert job['design'] not in {u['source_handoff_id'] for u in units}
        assert not {u['source_handoff_id'] for u in units}&set(exclude['excluded_origins'])


def test_cost_includes_legacy_final_checkers_and_preserves_unknown(tmp_path):
    import experiment as E
    (tmp_path/'reports').mkdir()
    (tmp_path/'reports/resource_ledger.jsonl').write_text(json.dumps({'type':'tool_call','elapsed_s':5})+'\n')
    (tmp_path/'eval_arm.log').write_text('[strict-signoff] drc\n[strict-signoff] lvs\n[strict-signoff] rcx\n')
    row={'accounting':{'llm_requests':1,'flows':2},'token_usage':{
        'known_input_tokens':100,'known_output_tokens':20,'total_tokens':None,'unknown_usage_requests':1}}
    result=E.cost(row,tmp_path)
    assert result['tool_calls']==4 and result['checker_runs']==3
    assert result['known_tokens']==120 and result['actual_tokens'] is None
    assert result['unknown_requests']==1 and result['checker_wall_s_complete'] is False


@pytest.mark.parametrize('reply',[
    {'error':{'message':'Insufficient Balance'}},
    {'error':{'type':'insufficient_balance_error'}},
    {'error':{'message':'Invalid API Key'}},
    {'http_status':403},
    {'model':'unapproved-model','usage':{'total_tokens':100}},
])
def test_global_provider_faults_are_never_ordinary_skippable_transport(reply):
    from worker import provider_global_fault
    assert provider_global_fault(reply)


def test_local_unknown_transport_stays_local_and_reported_usage_not_changed():
    from worker import provider_global_fault
    reply={'error':{'message':'SSL EOF'},'usage':None}
    assert provider_global_fault(reply) is None
    assert reply['usage'] is None
