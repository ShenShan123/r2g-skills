"""Combine strict remote results and completed ordinary replays without duplicates.

Locally verified results awaiting authorized transfer are explicit; this report
does not change campaign receipts or pretend remote import has happened.
"""
import json
from pathlib import Path
import sys
import time

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from prepare_normal_replay_import import prepare
from strict_signoff import inspect_run


def summarize():
    snapshot=json.loads((HERE/'status_snapshot_20261027_latest.json').read_text())
    if snapshot['changed_baseline_results'] or snapshot['baseline_hashes_checked']!=snapshot['baseline_total']:
        raise ValueError('frozen baseline verification incomplete')
    remote={r['design'] for r in snapshot['designs'] if r['physical_verdict_verified']}
    if len(remote)!=snapshot['physical_new_dual_clean']:raise ValueError('remote rescue count differs')
    index={r['design']:r for r in snapshot['designs']}
    auxiliary=Path('/tmp/sky130_aux208')
    roots=[auxiliary/'pdn_independent_replay/normal_replay',auxiliary/'area_independent_replay/normal_replay']
    roots += list((auxiliary/'port_independent_replay').glob('*/normal_replay'))
    roots += list((auxiliary/'area_timeout_replay').glob('*/normal_replay'))
    local={};observations=[]
    for root in roots:
        if not (root/'plan.json').exists():continue
        plan=json.loads((root/'plan.json').read_text())
        if plan.get('mode')!='normal_unforced_independent_replay' or plan.get('forced_selection') is not False or len(plan['arms'])!=1:
            raise ValueError('unexpected ordinary replay plan')
        arm=plan['arms'][0];project=Path(arm['project']);receipt=project/'execution.json'
        entry=dict(design=arm['case'],trials=str(root),strategy=plan['key']['strategy'],state='not_started')
        if receipt.exists():
            execution=json.loads(receipt.read_text());entry['state']=execution['status']
            if execution['status']=='completed':
                actual=inspect_run(execution['run'])
                if execution.get('strict_signoff') and execution['strict_signoff']!=actual:
                    raise ValueError('completed strict signoff changed')
                entry.update(flow_returncode=execution['flow_returncode'],strict_signoff=actual)
                if actual['dual_clean']:
                    if execution['flow_returncode']!=0:raise ValueError('passing artifacts from failed flow')
                    design=arm['case']
                    if design not in index:raise ValueError('replay is not a frozen failed baseline subject')
                    entry['already_reconciled_on_216']=design in remote
                    if design not in remote:
                        validation=prepare(root,validate_only=True)
                        if design in local:raise ValueError('duplicate local ordinary replay')
                        local[design]=dict(category=index[design]['category'],validation=validation,
                            strict_signoff=actual,execution_receipt=str(receipt),
                            durable_archive=str(HERE.parents[1]/'design_cases/sky130_normal_replays_20261027'/design.split('__')[0]))
        observations.append(entry)
    categories=json.loads(json.dumps(snapshot['categories']))
    for item in local.values():categories[item['category']]['physical_wins']+=1
    total=snapshot['baseline_dual_clean']+len(remote)+len(local)
    if total>snapshot['baseline_total']:raise ValueError('double-counted physical success')
    if any(not 0<=c['physical_wins']<=c['total'] for c in categories.values()):
        raise ValueError('invalid category yield')
    if sum(c['physical_wins'] for c in categories.values())!=len(remote)+len(local):
        raise ValueError('category yields differ from unique rescues')
    if sum(c['total'] for c in categories.values())+snapshot['baseline_dual_clean']!=snapshot['baseline_total']:
        raise ValueError('baseline category totals differ')
    return dict(at=time.strftime('%FT%T%z'),remote_snapshot_at=snapshot['at'],
        baseline_total=snapshot['baseline_total'],baseline_dual_clean=snapshot['baseline_dual_clean'],
        baseline_result_hashes_verified=snapshot['baseline_hashes_checked'],changed_baseline_results=[],
        reconciled_rescues_on_216=len(remote),combined_dual_clean_on_216=snapshot['physical_combined_dual_clean'],
        additional_local_strict_rescues=len(local),local_rescues_awaiting_reconciliation=local,
        total_verified_unique_rescues=len(remote)+len(local),total_verified_dual_clean=total,
        unresolved=snapshot['baseline_total']-total,categories=categories,ordinary_replays=observations,
        scope='Strict physical DRC/LVS with original source/config/SDC validation for each additional local win. Remote imports remain explicit. Timing closure, dataset checks and unbounded functional proof are separate. Running receipt states are not process-liveness claims.')


if __name__=='__main__':
    result=summarize()
    (HERE/'verified_replay_status_20261027_latest.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('ordinary_replays','categories','local_rescues_awaiting_reconciliation')}))
