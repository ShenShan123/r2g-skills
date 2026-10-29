"""Figure 2's only TEHM delta: a local, evidence-labelled cost selector.

This is a forecast from held-out historical R2G costs, not a measured causal
counterfactual. Safety/admission/typed matching/rollback are unchanged.
"""
import json
import math
from pathlib import Path

VARIANTS=('RECIPE','TEHM_FIXED','TEHM_SELECTOR')


def number(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>=0


def worth_local_trial(witnesses):
    result={'policy':'figure2-heldout-observed-cost-v1','use_memory':False,
            'authority':'cost forecast only; not a causal effect or guarantee',
            'reason':'insufficient_verified_cost_evidence','witnesses':[]}
    if not witnesses:return result
    for unit in witnesses:
        lab=unit.get('shared_label') or {};raw=unit.get('raw_row') or {}
        acceptance=raw.get('acceptance_result') or {}
        resources=acceptance.get('resources') or {}
        spent=resources.get('budget_spent') or resources.get('spent') or {}
        initial=(resources.get('flows_by_component') or {}).get('initial')
        step=lab.get('cost') or {}
        rb=lab.get('rolled_back') or {}
        valid=(lab.get('admitted') is True and lab.get('outcome_class')=='admitted_positive'
               and rb.get('status')=='KNOWN' and rb.get('value') is False
               and acceptance.get('accepted') is True
               and (acceptance.get('acceptance') or {}).get('accepted') is True
               and (acceptance.get('envelope') or {}).get('status')=='PASS'
               and step.get('flows_status')=='KNOWN'
               and resources.get('open_holds')==0 and not resources.get('unknown_occupancies'))
        values=[step.get('flows'),spent.get('flows'),initial,spent.get('tokens_in'),spent.get('tokens_out'),spent.get('llm_requests')]
        if not valid or not all(number(v) for v in values):return result
        remaining=spent['flows']-initial
        if remaining<step['flows']:return result
        result['witnesses'].append({'unit_id':unit['unit_id'],'local_step_flows':step['flows'],
            'historical_R2G_post_initial_flows':remaining,
            'historical_R2G_online_tokens':spent['tokens_in']+spent['tokens_out'],
            'historical_R2G_requests':spent['llm_requests']})
    n=len(result['witnesses'])
    result['mean_local_step_flows']=sum(w['local_step_flows'] for w in result['witnesses'])/n
    result['mean_historical_R2G_post_initial_flows']=sum(w['historical_R2G_post_initial_flows'] for w in result['witnesses'])/n
    result['mean_historical_R2G_online_tokens']=sum(w['historical_R2G_online_tokens'] for w in result['witnesses'])/n
    result['use_memory']=(result['mean_historical_R2G_online_tokens']>0 and
                         result['mean_local_step_flows']<=result['mean_historical_R2G_post_initial_flows'])
    result['reason']='forecast_avoids_online_tokens_without_extra_flows' if result['use_memory'] else 'no_observed_cost_headroom'
    return result


def configure_memory(memory,variant):
    import t2_agent_dispatch as D
    import t2a_runtime as T
    if variant not in VARIANTS:raise ValueError('unknown Figure 2 variant')
    original_action=memory.native_action
    original_context=memory.agent_context
    memory.figure2_attempts=0
    memory.figure2_stop=None

    def native_action():
        if memory.figure2_attempts>=3:
            memory.figure2_stop='local_memory_action_allowance_spent'
            return None
        action=original_action()
        if variant=='TEHM_SELECTOR' and not memory.experience_disabled and action is not None:
            decision=getattr(memory,'last_native_decision',{})
            witnesses=[]
            if action is not None:
                from t2_tehm_execution_policy import load_evidence
                units,_,_,_=load_evidence(memory.root,memory.env)
                wanted=set(decision.get('witness_ids',[]))
                witnesses=[u for u in units if u['unit_id'] in wanted]
                if {u['unit_id'] for u in witnesses}!=wanted:
                    raise D.IntegrityStop('selector witness binding incomplete')
            choice=worth_local_trial(witnesses)
            choice.update(candidate=action['id'], variant=variant,model_requests=0,
                          safety_gates_unchanged=True,selector_scope='local_execution_only',
                          advice_channel_unchanged=True)
            T.event(memory.H,memory.project,'figure2_selector',choice)
            if not choice['use_memory']:
                return None
        if action is not None:
            memory.figure2_attempts+=1
            T.event(memory.H,memory.project,'figure2_local_attempt',{
                'variant':variant,'attempt':memory.figure2_attempts,'candidate':action['id'],
                'model_requests':0,'count_includes_rejected_or_noop_proposals':True})
        return action

    def context(observation,gateway):
        if variant=='RECIPE':
            memory.figure2_stop=memory.figure2_stop or 'no_applicable_recipe'
            raise D.OnlineAllowanceSpent('pure RECIPE never calls an online model')
        return original_context(observation,gateway)

    memory.native_action=native_action
    memory.agent_context=context
    return memory
