#!/usr/bin/env python3
"""Read-only campaign inventory, signed-result checks, and frozen hash audit.

This reports physical evidence; it does not infer RTL equivalence from a clean
signoff or from a receipt's semantic_review label. No experiments are started.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import time
from strict_signoff import inspect_run


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def under(root, value):
    path=Path(value).resolve()
    if not path.is_relative_to(root.resolve()):raise ValueError('evidence escapes campaign: '+str(path))
    return path


def snapshot(root):
    inventory=root/'tools/inventory_audited.json'
    if not inventory.exists():raise ValueError('top-level audited baseline inventory is required')
    inv=json.loads(inventory.read_text())
    baseline=Path(inv['baseline'])
    # Some archived inventories use a host-prefixed display name.
    if not baseline.is_absolute():raise ValueError('inventory baseline is not an absolute path')
    changed=[]
    for rel,digest in inv['baseline_result_sha256'].items():
        path=baseline/rel
        if not path.is_file() or sha(path)!=digest:changed.append(rel)
    rows=[]
    for case in inv['failures']:
        row={k:case.get(k) for k in ('design','category','stage','status','drc','lvs')}
        row={'baseline_'+k if k not in ('design','category') else k:v for k,v in row.items()}
        row.update(current_status='not_attempted',physical_verdict_verified=False)
        sources=[root/branch/'cases'/case['design']/kind for branch in ('verified_imports','campaign','canary')
                 for kind in ('repair.json','progress.json')]
        source=next((p for p in sources if p.is_file()),None)
        if source:
            receipt=json.loads(source.read_text());attempts=receipt.get('attempts',[])
            row.update(current_status=receipt['status'],receipt=str(source),
                       policy_revision=receipt.get('policy_revision',1),reason=receipt.get('reason'),
                       attempted_strategies=[a['strategy'] for a in attempts])
            if attempts:
                last=attempts[-1]
                row.update(latest_attempt_status=last.get('status'),semantic_review=last.get('semantic_review'))
                if last.get('run'):
                    run=under(root,last['run']);result=run/'result.json'
                    if result.is_file():
                        signed=json.loads(result.read_text())
                        row['latest_result']={k:signed.get(k) for k in ('status','stage','failure_class','drc_status','drc_violations_signoff','lvs_status','cells','worst_slack')}
                        required=['6_final.gds','6_drc.lyrdb','drc_signoff.log','netgen_lvs.rpt','netgen_lvs.log']
                        audited=inspect_run(run);row['acceptance_audit']=audited
                        row['physical_verdict_verified']=(receipt['status']=='dual_clean' and audited['dual_clean']
                            and signed.get('design')==case['design'] and signed.get('platform')=='sky130hd'
                            and signed.get('status')=='pass' and signed.get('drc_status')=='clean'
                            and signed.get('lvs_status')=='clean'
                            and all((run/n).is_file() and (run/n).stat().st_size>0 for n in required))
                        if receipt['status']=='dual_clean' and not row['physical_verdict_verified']:
                            row['evidence_error']='receipt says dual_clean but signed result/artifacts do not support it'
        rows.append(row)
    wins=sum(r['physical_verdict_verified'] for r in rows)
    return dict(at=time.strftime('%FT%T%z'),baseline=str(baseline),
        baseline_total=inv['total'],baseline_dual_clean=inv['dual_clean'],
        baseline_hashes_checked=len(inv['baseline_result_sha256']),changed_baseline_results=changed,
        failures_total=len(rows),current_status_counts=dict(Counter(r['current_status'] for r in rows)),
        physical_new_dual_clean=wins,physical_combined_dual_clean=inv['dual_clean']+wins,
        acceptance_scope='Physical result/artifact presence checked; semantic labels are not formal or independent behavioral proof',
        categories={c:dict(total=sum(r['category']==c for r in rows),
                          physical_wins=sum(r['category']==c and r['physical_verdict_verified'] for r in rows))
                    for c in sorted({r['category'] for r in rows})},designs=rows)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);a=p.parse_args()
    print(json.dumps(snapshot(a.root),ensure_ascii=False,indent=2))
