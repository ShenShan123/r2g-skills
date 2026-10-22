"""Raw agent step ownership for the existing independent ann-v2 verifier.

Records observations and rollback, never asserts evidence admission. The run
runner writes the same action ID at launch. The full canonical row lives once
in fix_log; duplicate ledger events point to it rather than becoming new units.
"""
import hashlib
import json
from pathlib import Path
import time
import sys

import t2a_envelope as E


def begin(H,project,sequence,component,content):
    sys.path.insert(0,str(H.SKILL/'knowledge'))
    import situation
    check,vclass,count=H._task_failure(project)
    runs=sorted((project/'backend').glob('RUN_*'))
    config=E.effective_config((project/'constraints/config.mk').read_text())
    path=project/'reports/drc.json'
    drc=json.loads(path.read_text()) if path.exists() else {}
    log=project/'reports/fix_log.jsonl'
    old=[json.loads(l) for l in log.read_text().splitlines() if l.strip()] if log.exists() else []
    session='agent:'+project.name
    return {'chain_version':1,'fix_session_id':session,'iter':sequence,
        'action_id':session+'#'+str(sequence),'parent_action':next((r['action_id'] for r in reversed(old)
                       if r.get('fix_session_id')==session and r.get('action_id')),None),
        'pre_run_tag':runs[-1].name if runs else None,'post_run_tag':None,
        'check':check,'violation_class':vclass,'before':count,
        'before_categories':drc.get('categories',{}) if check=='drc' else {},
        'situation':situation.from_project(project,check,vclass,count),
        'config_before':config,'component':component,'proposal':content,'ts':time.time()}


def finish(project,entry,result,stage,component):
    if not result.get('flow_launched'):return
    matches=[]
    for p in (project/'backend').glob('RUN_*/r2g_action.json'):
        if json.loads(p.read_text()).get('action_id')==entry['action_id']:matches.append(p.parent.name)
    after=E.effective_config((project/'constraints/config.mk').read_text())
    before=entry['config_before']
    changes={k:after.get(k) for k in sorted(set(before)|set(after)) if before.get(k)!=after.get(k)}
    status=result.get('status');verdict=result.get('verdict') or {}
    raw={**entry,'post_run_tag':matches[0] if len(matches)==1 else None,
         'post_run_candidates':matches,'strategy':str((result.get('applied') or {}).get('applied')
                                                    or result.get('strategy_id') or component+':agent_edit'),
         'from_stage':stage,'config_delta':changes,'cumulative_config':after,
         'verdict':'regression' if status=='REGRESSION' else
                   'cleared' if verdict.get('accepted') else 'unverified' if status=='UNVERIFIED' else 'not_cleared',
         'rolled_back':result['rolled_back'],'flow_rc':result.get('flow_rc'),
         'acceptance_result':verdict,'comparison':result.get('comparison'),
         'usage':result.get('usage'),'evidence_admission':'NOT_ASSERTED'}
    log=project/'reports/fix_log.jsonl'
    line=json.dumps(raw,sort_keys=True)
    with log.open('a') as f:f.write(line+'\n')
    result['journal_ref']={'file':'reports/fix_log.jsonl','action_id':entry['action_id'],
                           'row_sha256':hashlib.sha256(line.encode()).hexdigest()}
