"""Deterministic counterexamples for paper bookkeeping, not empirical repairs."""
from __future__ import annotations
import copy
import json
from pathlib import Path
from .research_r5_paper_protocol import ProtocolError, VIEWS, screen_source, summarize, validate_plan

def run_checks() -> dict:
    plan=json.loads((Path(__file__).resolve().parents[2]/'evaluation/research_r5_paper_protocol_v1.json').read_text())
    cases={}
    def check(name, fn):
        try: cases[name]=bool(fn())
        except Exception as error: cases[name]=False
    def rejected(fn):
        try: fn()
        except ProtocolError: return True
        return False
    def changed_plan(key,value):
        changed=copy.deepcopy(plan); changed[key]=value; return changed
    check('methods_valid_but_final_not_ready',lambda:validate_plan(plan)['manifest_consistent'] and not validate_plan(plan)['final_test_ready'])
    for key,value in [('unknown_counts_as_success',True),('drop_abstentions',True),('online_target_learning',True),
                      ('model_calls_authorized',1),('model_tokens_authorized',1),('model_calls_authorized',False),
                      ('candidate_budget',2),('candidate_budget',True),('native_subtests_are_tasks',True),
                      ('final_tasks',[{'task_id':'old'}]),('effect_target',0.5),('power_claim',0.8),
                      ('positive_effect_required_to_report',True),('next_scope_screen_cap',20),
                      ('frozen_method_software','0'*40),('frozen_memory_report_digest','sha256:'+'0'*64)]:
        check('reject_plan_'+key+'_'+repr(value),lambda k=key,v=value:rejected(lambda:validate_plan(changed_plan(k,v))))
    for role in ('QUALIFICATION','DEV','TRAIN','PILOT_TRANSFER','FINAL_TEST','HISTORICAL'):
        check('observed_'+role,lambda r=role:screen_source(plan,{'repository':'new/repo','observed_roles':[r]})['disposition']=='REJECT_OBSERVED_SCOPE')
    check('case_url_alias_quarantined',lambda:'prior_repository_requires_scope_level_exposure_review' in screen_source(plan,{'repository':'https://github.com/DrewBabel/eth-datapath.git'})['reasons'])
    check('new_owner_not_independence',lambda:not screen_source(plan,{'repository':'new/repo'})['final_test_ready'])
    check('fork_relation_retained',lambda:'known_related_source_requires_lineage_review' in screen_source(plan,{'repository':'new/fork','related_repositories':['ZipCPU/wb2axip']})['reasons'])
    check('string_roles_rejected',lambda:rejected(lambda:screen_source(plan,{'repository':'new/repo','observed_roles':'TRAIN'})))
    contract='sha256:'+'a'*64
    manifest={'role':'PILOT_TRANSFER','views':list(VIEWS),'shared_contract_digest':contract,'candidate_budget':1,
              'tasks':[{'task_id':'task1','role':'PILOT_TRANSFER','source_group':'group1','required_oracles':['target','preservation','native']}]}
    rows=[{'task_id':'task1','view':v,'shared_contract_digest':contract,'candidate_budget':1,'candidates_attempted':1,
           'verdicts':{'target':'PASS' if v=='m-plus' else 'FAIL','preservation':'PASS','native':'PASS' if v=='m-plus' else 'FAIL'},
           'source_changed':v=='m-plus','oracle_wall_seconds':1.0} for v in VIEWS]
    check('paired_fixture_difference',lambda:summarize(manifest,rows)['paired_group_delta_mplus_minus']['m-minus']['group1']==1)
    check('missing_arm_not_dropped',lambda:summarize(manifest,rows[1:])['per_view']['m-minus']['registered_tasks']==1 and summarize(manifest,rows[1:])['per_view']['m-minus']['unknown_tasks']==1)
    check('duplicates_rejected',lambda:rejected(lambda:summarize(manifest,rows+[rows[0]])))
    check('empty_denominator_rejected',lambda:rejected(lambda:summarize({**manifest,'tasks':[]},[])))
    def altered(key,value,index=1):
        value_rows=copy.deepcopy(rows); value_rows[index][key]=value; return value_rows
    for key,value in [('shared_contract_digest','sha256:'+'b'*64),('candidate_budget',2),('candidates_attempted',2),
                      ('candidates_attempted',True),('source_changed','false'),('oracle_wall_seconds',float('nan')),
                      ('task_id','healthy_control'),('verdicts',{'target':'PASS','preservation':'PASS'})]:
        check('reject_row_'+key+'_'+repr(value),lambda k=key,v=value:rejected(lambda:summarize(manifest,altered(k,v))))
    check('unknown_is_not_success',lambda:summarize(manifest,altered('verdicts',{'target':'UNKNOWN','preservation':'PASS','native':'PASS'}))['per_view']['m-plus']['successes']==0)
    check('harm_not_target_only_success',lambda:summarize(manifest,altered('verdicts',{'target':'PASS','preservation':'FAIL','native':'PASS'}))['per_view']['m-plus']['harmful_changed_tasks']==1)
    check('missing_cost_not_zero_cost_claim',lambda:not summarize(manifest,altered('oracle_wall_seconds',None))['per_view']['m-plus']['oracle_cost_complete'])
    check('no_authorship_or_evolution_grant',lambda:not summarize(manifest,rows)['source_independence_granted'] and not summarize(manifest,rows)['online_evolution_established'])
    macro=copy.deepcopy(manifest)
    macro['tasks']=[{**manifest['tasks'][0],'task_id':f'task{i}','source_group':'g1' if i<3 else 'g2'} for i in (1,2,3)]
    macro_rows=[{**r,'task_id':t['task_id'],'verdicts':{'target':'PASS' if t['source_group']=='g1' else 'FAIL','preservation':'PASS','native':'PASS'}} for t in macro['tasks'] for r in rows]
    check('macro_group_not_micro_task_rate',lambda:summarize(macro,macro_rows)['per_view']['m-plus']['macro_source_group_rate']==0.5 and summarize(macro,macro_rows)['per_view']['m-plus']['VerifiedRepair@B']==2/3)
    return {'valid':all(cases.values()),'case_count':len(cases),'failures':[k for k,v in cases.items() if not v],
            'role':'SYNTHETIC_BOOKKEEPING_CONFORMANCE_NOT_EXPERIMENT', 'model_calls':0}

if __name__=='__main__':
    result=run_checks(); print(json.dumps(result,indent=2,sort_keys=True)); raise SystemExit(0 if result['valid'] else 1)
