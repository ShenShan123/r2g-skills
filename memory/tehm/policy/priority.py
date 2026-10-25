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
