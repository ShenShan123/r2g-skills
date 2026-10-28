#!/usr/bin/env python3
"""Derive an audited inventory alongside the untouched historical inventory."""
import argparse
from collections import Counter
import json
from pathlib import Path
import time
from strict_signoff import inspect_run


def audit(inventory):
    inv=json.loads(inventory.read_text());root=Path(inv['baseline'])
    cases={r['design']:r.copy() for r in inv['failures']}
    checks=[];changed=[];strict=0
    for p in sorted((root/'runs').glob('*/result.json')):
        r=json.loads(p.read_text())
        if r.get('status')!='pass':continue
        result=inspect_run(p.parent);checks.append(result)
        strict+=result['dual_clean']
        old_clean=r.get('drc_status')==r.get('lvs_status')=='clean'
        if old_clean and not result['dual_clean']:
            changed.append(result)
            if r['design'] not in cases:
                category=('lvs_pin_mismatch' if result['lvs']=='pin_mismatch' else
                          'lvs_topology_review' if result['lvs']=='mismatch' else
                          'signoff_evidence_review')
                cases[r['design']]=dict(design=r['design'],run=str(p.parent),category=category,
                    status=r.get('status'),stage=r.get('stage'),failure_class=r.get('failure_class'),
                    drc=result['drc'],lvs=result['lvs'],cells=r.get('cells'),seconds=r.get('seconds'),
                    errors=['Frozen result.json conflicts with actual top-level signoff evidence'],
                    drc_violations=r.get('drc_violations_signoff'),evidence_audit=result)
    result=dict(inv)
    result.update(at=time.strftime('%FT%T%z'),historical_reported_dual_clean=inv['dual_clean'],
                  dual_clean=strict,failures=list(cases.values()),
                  category_counts=dict(Counter(c['category'] for c in cases.values())),
                  acceptance_audit=dict(reader='strict_signoff.py',reclassified=changed,checks=checks),
                  criterion='Original DRC deck: zero report items; Netgen top-level final verdict uniquely matched; fresh completed layout')
    assert len(cases)+strict==inv['total'], 'unclassified or overlapping results'
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('inventory',type=Path);a=p.parse_args()
    print(json.dumps(audit(a.inventory),ensure_ascii=False,indent=2))
