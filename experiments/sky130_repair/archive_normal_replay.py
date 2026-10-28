"""Retain completed ordinary-selection replay evidence without counting a win twice."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import sys

HERE=Path(__file__).resolve().parent;REPO=HERE.parents[1]
sys.path.insert(0,str(HERE))
from area_trial_evidence import classify
from run_recipe_ab import verify_pdn_arm_inputs

sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--trials',type=Path,required=True);args=parser.parse_args()
    trials=args.trials.resolve()
    if not trials.is_relative_to('/tmp/sky130_aux208'):raise ValueError('outside auxiliary evidence root')
    plan=json.loads((trials/'plan.json').read_text())
    if plan.get('mode')!='normal_unforced_independent_replay' or plan.get('forced_selection') is not False or len(plan['arms'])!=1:
        raise ValueError('not an independent normal-selection replay')
    arm=plan['arms'][0];source=Path(arm['project']);cid=arm['case'].split('__')[0]
    if not re.fullmatch(r'C\d{5}',cid) or not source.resolve().is_relative_to(trials):raise ValueError('invalid subject')
    execution=json.loads((source/'execution.json').read_text())
    if execution['status']!='completed':raise ValueError('execution still incomplete')
    actual=classify(execution['run'],execution['flow_returncode'])
    if 'acceptance' in execution and actual!=execution['acceptance']:raise ValueError('acceptance changed')
    if execution.get('strict_signoff') and actual.get('strict_signoff')!=execution['strict_signoff']:raise ValueError('strict signoff changed')
    verify_pdn_arm_inputs(arm)
    if sha(source/'constraints/config.mk')!=arm['config_sha256']:raise ValueError('prepared config changed')
    dest=REPO/'design_cases/sky130_normal_replays_20261027'/cid
    manifest=dest/'durable_evidence.json'
    if dest.exists():
        prior=json.loads(manifest.read_text())
        for name,digest in prior['files'].items():
            if sha(dest/name)!=digest:raise ValueError('archived evidence changed')
        if prior['original_plan_sha256']!=sha(trials/'plan.json'):raise ValueError('different replay at destination')
        print(json.dumps(dict(case=cid,archive=str(dest),physical_success=prior['acceptance']['is_success'],already_archived=True)))
        return
    dest.mkdir(parents=True)
    shutil.copytree(source,dest/'project',ignore=shutil.ignore_patterns('ab_work','drc_projects','lvs_projects'))
    for name in ('plan.json','selection.json','application.json'):
        shutil.copy2(trials/name,dest/name)
    for path in trials.glob('execution_manifest*.json'):shutil.copy2(path,dest/path.name)
    if (trials/'knowledge.sqlite').exists():
        with sqlite3.connect('file:'+str(trials/'knowledge.sqlite')+'?mode=ro',uri=True) as src,sqlite3.connect(dest/'selection_knowledge.sqlite') as dst:src.backup(dst)
    if (trials/'heuristics.json').exists():shutil.copy2(trials/'heuristics.json',dest/'selection_heuristics.json')
    copied=classify(dest/'project/physical_run',execution['flow_returncode'])
    expected=json.loads(json.dumps(actual))
    if 'strict_signoff' in expected:expected['strict_signoff']['lvs_final_source']=str(dest/'project/physical_run/netgen_lvs.rpt')
    if copied!=expected:raise ValueError('archive differs from actual physical evidence')
    record=dict(case=cid,key=plan['key'],original_project=str(source),original_plan_sha256=sha(trials/'plan.json'),
                acceptance=copied,source_and_sdc_verified=True,normal_selection_mode=plan['mode'],
                scope='Archived raw replay evidence; configs/receipts retain original paths. This archive alone does not increment unique campaign yield.',
                files={str(p.relative_to(dest)):sha(p) for p in dest.rglob('*') if p.is_file()})
    manifest.write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps(dict(case=cid,archive=str(dest),physical_success=copied['is_success'],campaign_count_changed=False)))


if __name__=='__main__':main()
