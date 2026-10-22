"""Explicit, frozen DEV mechanism policies; no outcome or promotion authority."""
import copy
import json
import math
from pathlib import Path

SCHEMA = 't2a-mechanisms-r4-v1'


def load_policy(path):
    data=json.loads(Path(path).read_text())
    if (set(data)!={'schema','task_entry_fallback','cost_priority','priority_samples'}
            or data['schema']!=SCHEMA or type(data['task_entry_fallback']) is not bool
            or type(data['cost_priority']) is not bool or not isinstance(data['priority_samples'],list)):
        raise ValueError('Invalid frozen mechanism policy')
    if any(s.get('split') not in ('DEV','SOURCE') for s in data['priority_samples']):
        raise ValueError('Priority evidence may only come from SOURCE/DEV')
    return data


def _known_costs(row):
    for arm in ('memory','baseline'):
        values=(row.get('costs') or {}).get(arm) or {}
        for key in ('flows','tokens','wall_s'):
            v=values.get(key)
            if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<0:
                return False
    return True


def quality_context(manifest):
    clock=manifest.get('expected_clock_period_ns')
    if isinstance(clock,bool) or not isinstance(clock,(int,float)) or not math.isfinite(clock) or clock<=0:
        raise ValueError('Priority needs a verified original task clock')
    return {'platform':manifest.get('platform'),'clock_period_ns':clock,
            'area_caps':{k:v.get('max_rel_increase') for k,v in manifest.get('area',{}).items()},
            'allowlist':manifest.get('allowlist',{}),
            'floorplan_mode_policy':manifest.get('floorplan_mode_policy')}


def prioritize(candidates, situation, samples, quality=None):
    """Earn first place using own matched full-task evidence, not local PASS.

    No positive evidence is invented from an absent cost or an unverified run.
    Unproven candidates remain available AFTER the shared catalogue. These
    evidence rows are frozen DEV training observations, not TEST writeback.
    """
    first=[];deferred=[]
    for order,candidate in enumerate(candidates):
        rule=candidate.get('rule_id');matched={};conflict=False
        for row in samples:
            if (row.get('split') not in ('DEV','SOURCE') or row.get('rule_id')!=rule
                    or row.get('situation')!=situation
                    or (quality is not None and row.get('quality_context')!=quality)):continue
            key=row.get('sample_id')
            if not isinstance(key,str) or not key:
                conflict=True;continue
            if key in matched and matched[key]!=row:conflict=True
            matched[key]=row
        rows=list(matched.values());meta={'pairs':len(rows),'gains':0,'losses':0}
        status_ok=all(r.get('memory_status') in ('ACCEPTED','NOT_ACCEPTED') and
                      r.get('baseline_status') in ('ACCEPTED','NOT_ACCEPTED') and
                      type(r.get('rolled_back')) is bool and _known_costs(r) for r in rows)
        if rows and status_ok and not conflict:
            positive=sum(r['memory_status']=='ACCEPTED' and not r['rolled_back'] for r in rows)
            meta.update(gains=sum(r['memory_status']=='ACCEPTED' and not r['rolled_back'] and
                                  r['baseline_status']=='NOT_ACCEPTED' for r in rows),
                        losses=sum(r['memory_status']=='NOT_ACCEPTED' and r['baseline_status']=='ACCEPTED' for r in rows),
                        positive=positive)
            delta={k:sum(r['costs']['memory'][k]-r['costs']['baseline'][k] for r in rows)
                   for k in ('flows','tokens','wall_s')}
            meta['cost_deltas']=delta
            earned=(meta['losses']==0 and positive>len(rows)-positive and
                    (meta['gains']>0 or any(v<0 for v in delta.values())))
        else:earned=False
        item=copy.deepcopy(candidate)
        item['task_priority_evidence']={**meta,'earned_first_place':earned,
            'reason':'matched own full-task gain or measured cost saving' if earned else
                     'insufficient, conflicting, harmful or non-saving full-task evidence'}
        if earned:
            first.append(((-meta['gains'],delta['flows'],delta['tokens'],delta['wall_s'],order),item))
        else:deferred.append(item)
    return [v for _,v in sorted(first,key=lambda item:item[0])],deferred


def samples_from_dev(root):
    """Read audited episode/ledger pairs; never derive full-task truth from a step.

    A co-used rule is not credited with another rule's task success. Keep such
    episodes in DEV reporting, but do not use them to earn individual priority.
    Original episode/ledger hashes remain attached to every retained observation.
    """
    import hashlib
    import t2a_batch as B
    import t2a_runtime as T
    root=Path(root);reg=B.preflight(root,'DEV');out=[]
    pairs=sorted({(r['task'],r['repeat']) for r in reg['plan']})
    def read_episode(task,arm,repeat):
        p=root/'episodes'/(task.replace('/','__')+'__'+arm+'__r'+str(repeat))
        data=json.loads((p/'episode.json').read_text())
        ledger=p/'reports/resource_ledger.jsonl'
        events=[json.loads(line) for line in ledger.read_text().splitlines() if line]
        if (data.get('split')!='DEV' or data.get('paper_data') is not False
                or data.get('task')!=task or data.get('arm')!=arm or data.get('repeat')!=repeat
                or not T.accounting_check(events,list((p/'backend').glob('RUN_*')))['passed']):
            raise ValueError('DEV pair provenance/accounting mismatch')
        usage=T.request_token_totals(events)
        status=T.terminal_status(data.get('final') or {})
        if status!=data.get('status'):raise ValueError('DEV final status mismatch')
        return data,{'flows':sum(e.get('type')=='flow_run' for e in events),
                     'tokens':usage['total_tokens'],'wall_s':data.get('total_worker_wall_s')}, {
            'episode':str(p/'episode.json'),'episode_sha256':hashlib.sha256((p/'episode.json').read_bytes()).hexdigest(),
            'ledger':str(ledger),'ledger_sha256':hashlib.sha256(ledger.read_bytes()).hexdigest()}
    for task,repeat in pairs:
        if not all({'task':task,'arm':a,'repeat':repeat} in reg['plan'] for a in ('NB','TM0')):continue
        baseline,bcost,bref=read_episode(task,'NB',repeat)
        memory,mcost,mref=read_episode(task,'TM0',repeat)
        mproject=Path(mref['episode']).parent
        scope=quality_context(json.loads((mproject/'manifest.json').read_text()))
        rows=memory.get('shared_repair',{}).get('fix_rows',[])
        own=[r for r in rows if r.get('memory_rule') and r.get('from_stage')]
        rules={r['memory_rule'] for r in own}
        if len(rules)!=1:continue
        rule=next(iter(rules))
        for situation in {json.dumps(r.get('situation'),sort_keys=True) for r in own}:
            matching=[r for r in own if json.dumps(r.get('situation'),sort_keys=True)==situation]
            out.append({'rule_id':rule,'situation':json.loads(situation),'split':'DEV','quality_context':scope,
                'sample_id':root.name+':'+task+':'+str(repeat)+':'+rule+':'+situation,
                'memory_status':memory['status'],'baseline_status':baseline['status'],
                'rolled_back':bool(memory.get('task_entry_fallback',{}).get('executed')) or
                              not any(r.get('rolled_back') is False for r in matching),
                'costs':{'memory':mcost,'baseline':bcost},'evidence':{'memory':mref,'baseline':bref,
                    'registration_sha256':hashlib.sha256((root/'registration.json').read_bytes()).hexdigest()},
                'attribution':'one executed rule in this whole episode; causal gain still requires ablation'})
    return out


def paired_gate(records, expected):
    """Engineering DEV gate over ALL registered pairs, never a paper significance claim."""
    wanted={(r['task'],r['arm'],r['repeat']) for r in expected}
    keys=[(r['task'],r['arm'],r['repeat']) for r in records]
    errors=[]
    if len(keys)!=len(set(keys)) or set(keys)!=wanted or len(wanted)!=len(expected):
        errors.append('missing, duplicate or unregistered DEV episodes')
    lookup=dict(zip(keys,records));pairs=[]
    for task,repeat in sorted({(r['task'],r['repeat']) for r in expected}):
        if not all((task,a,repeat) in lookup for a in ('NB','TM0')):continue
        b,m=(lookup[(task,a,repeat)] for a in ('NB','TM0'))
        valid=all(r.get('split')=='DEV' and r.get('paper_data') is False and
                  r.get('accounting_passed') is True and
                  r.get('status') in ('ACCEPTED','NOT_ACCEPTED') for r in (b,m))
        valid=valid and _known_costs({'costs':{'baseline':b.get('costs'),'memory':m.get('costs')}})
        if not valid:errors.append('unverified or unknown-cost pair: '+task)
        pairs.append({'task':task,'repeat':repeat,'baseline':b,'memory':m,'measured':valid})
    measured=[p for p in pairs if p['measured']]
    gains=sum(p['memory']['status']=='ACCEPTED' and p['baseline']['status']=='NOT_ACCEPTED' for p in measured)
    losses=sum(p['memory']['status']=='NOT_ACCEPTED' and p['baseline']['status']=='ACCEPTED' for p in measured)
    complete=bool(pairs and not errors and len(measured)*2==len(expected))
    deltas={k:sum(p['memory']['costs'][k]-p['baseline']['costs'][k] for p in measured)
            for k in ('flows','tokens','wall_s')} if complete else None
    return {'registered_episodes':len(expected),'registered_pairs':len(expected)//2,
            'measured_pairs':len(measured),'gains':gains,'losses':losses,
            'unverified_pairs':len(pairs)-len(measured),'cost_deltas_all_registered_pairs':deltas,
            'data_complete':complete,'errors':errors,'records':records,
            'dev_release_gate_passed':bool(complete and losses==0 and (gains>0 or any(v<0 for v in deltas.values()))),
            'qualification':'DEV engineering gate only; not a universal no-regression or significance claim',
            'formal_launch_allowed':False}


def collect_pairs(root):
    import hashlib
    import t2a_batch as B
    import t2a_runtime as T
    root=Path(root);reg=B.preflight(root,'DEV');records=[]
    if set(reg['arms'])!={'NB','TM0'}:raise ValueError('Paired mechanism collector needs NB/TM0 only')
    for row in reg['plan']:
        project=root/'episodes'/(row['task'].replace('/','__')+'__'+row['arm']+'__r'+str(row['repeat']))
        data=json.loads((project/'episode.json').read_text());ledger=project/'reports/resource_ledger.jsonl'
        events=[json.loads(line) for line in ledger.read_text().splitlines() if line]
        accounting=T.accounting_check(events,list((project/'backend').glob('RUN_*')))
        usage=T.request_token_totals(events)
        status=T.terminal_status(data.get('final') or {})
        valid=(accounting['passed'] and status==data.get('status') and
               all(data.get(k)==v for k,v in row.items()))
        records.append({**row,'split':data.get('split'),'paper_data':data.get('paper_data'),
            'status':data.get('status'),'accounting_passed':valid,
            'costs':{'flows':accounting['flows'],'tokens':usage['total_tokens'],
                     'wall_s':data.get('total_worker_wall_s')},
            'llm_requests':usage['llm_requests'],'repair_attempts':accounting['repair_attempts'],
            'tool_calls':sum(e.get('type')=='tool_call' for e in events),
            'checker_runs':sum(e.get('type')=='checker_run' for e in events),
            'rollback_flows':sum(e.get('type')=='flow_run' and e.get('component')=='rollback_recovery' for e in events),
            'episode':str(project/'episode.json'),'episode_sha256':hashlib.sha256((project/'episode.json').read_bytes()).hexdigest(),
            'ledger_sha256':hashlib.sha256(ledger.read_bytes()).hexdigest()})
    result=paired_gate(records,reg['plan']);result['schema']='t2a-mechanism-dev-results-r4-v1'
    result['data_audit_passed']=bool(len(records)==len(reg['plan']) and all(
        r['accounting_passed'] and r['split']=='DEV' and r['paper_data'] is False and
        all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) and v>=0 for v in r['costs'].values())
        for r in records))
    B.Q.save(root/'mechanism-results.json',result)
    return result
