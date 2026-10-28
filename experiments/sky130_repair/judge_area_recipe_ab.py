#!/usr/bin/env python3
"""Ingest only freshly executed arms, then use the existing A/B lifecycle judge."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
HERE=Path(__file__).resolve().parent;REPO=HERE.parents[1]
sys.path[:0]=[str(HERE),str(REPO/'r2g-skills/signoff-loop/knowledge')]
from area_trial_evidence import classify
from area_trial_materialize import materialize
import knowledge_db
import ingest_run
import ab_runner
import recipe_lifecycle


def validate_arm_scope(plan, arm, row):
    """Keep actual run classes; pooling is allowed only by the recorded planner."""
    key=plan['key'];planner=plan['planner']
    if planner.get('key',key)!=key:raise ValueError('planner key differs')
    if row['platform']!=key['platform'] or row['design_family']!=arm['source_repository']:
        raise ValueError('trial platform or family differs')
    if row['design_class']!=key['design_class'] and planner['match_level'] not in ('current_exhibitor_pooled_class','pooled_class'):
        raise ValueError('trial class differs without planner pooling')
    subjects={d['project_path'] for d in planner['designs']}
    if arm['subject'] not in subjects:raise ValueError('subject was not selected by planner')
    h6=hashlib.sha1('|'.join(key[k] for k in ('symptom_id','design_class','platform','strategy')).encode()).hexdigest()[:6].upper()
    expected=Path(arm['subject']).name+'_ab'+arm['role']+'_'+key['strategy'][:8]+h6+'_'+str(arm['repeat'])
    if Path(arm['project']).name!=expected:raise ValueError('arm is not owned by this full recipe key')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--trials',type=Path,default=Path('/tmp/sky130_aux208/area_recipe/trials'))
    args=parser.parse_args();trials=args.trials.resolve();root=trials.parent
    if trials!=Path('/tmp/sky130_aux208/area_recipe/trials'):raise ValueError('unreviewed trial root')
    db=root/'knowledge.sqlite';heuristics=root/'heuristics.json';families=root/'families.json'
    os.environ.update(R2G_JOURNAL='0',R2G_KNOWLEDGE_DB=str(db),R2G_HEURISTICS_PATH=str(heuristics))
    plan=json.loads((trials/'plan.json').read_text());key=plan['key']
    if key['strategy']!='fixed_area_route_relief':raise ValueError('unexpected strategy')
    if len(plan['arms'])!=8:raise ValueError('expected two families and two repeats per role')
    identities={(a['subject'],a['role'],a['repeat']) for a in plan['arms']}
    subjects={a['subject'] for a in plan['arms']}
    if len(subjects)!=2 or identities!={(s,r,n) for s in subjects for r in ('A','B') for n in (0,1)}:
        raise ValueError('incomplete independent repeated plan')
    # Validate every execution before inserting any trial or changing lifecycle.
    for arm in plan['arms']:
        e=json.loads((Path(arm['project'])/'execution.json').read_text())
        if e['status']!='completed':raise ValueError('incomplete physical arm')
        actual=classify(Path(e['run']),e['flow_returncode'])
        if actual!=e['acceptance']:raise ValueError('execution evidence changed')
        if actual['classification']=='inconclusive':raise ValueError('unsupported or infrastructure failure')
    conn=knowledge_db.connect();knowledge_db.ensure_schema(conn);groups={};rows=[]
    for arm in plan['arms']:
        project=Path(arm['project']);e=json.loads((project/'execution.json').read_text())
        accepted=e['acceptance'];materialize(project,e)
        rid=ingest_run.ingest(project,conn,families_path=families)
        cur=conn.execute('SELECT * FROM runs WHERE run_id=?',(rid,));row=dict(zip([c[0] for c in cur.description],cur.fetchone()))
        validate_arm_scope(plan,arm,row)
        success=knowledge_db.is_success(row)
        if success!=accepted['is_success']:raise ValueError('learning success disagrees with strict physical acceptance')
        sample=dict(run_id=rid,project=str(project),is_success=success,wall_s=e['wall_seconds'],
                    role=arm['role'],repeat=arm['repeat'],source_repository=arm['source_repository'],
                    design_class=row['design_class'],platform=row['platform'],acceptance=accepted)
        groups.setdefault(arm['subject'],{}).setdefault(arm['role'],[]).append(sample);rows.append(sample)
    verdicts=[]
    for subject,arms in groups.items():
        for role in ('A','B'):
            arms[role].sort(key=lambda r:r['repeat'])
            if [r['repeat'] for r in arms[role]]!=[0,1]:raise ValueError('missing independent physical repeat')
        verdict,reason=ab_runner.judge_repeated_ex(arms['A'],arms['B'])
        ids=[r['run_id'] for role in ('A','B') for r in arms[role]]
        uuid=hashlib.sha256('|'.join(ids).encode()).hexdigest()
        tid=ab_runner.record_trial(conn,key=key,verdict=verdict,
            arm_a_run_id=arms['A'][0]['run_id'],arm_b_run_id=arms['B'][0]['run_id'],
            metrics=dict(arm_a_samples=arms['A'],arm_b_samples=arms['B'],reason=reason,
                         source='fresh_standard_R2G_diagnose_apply_frozen_toolchain'),
            match_level=plan['planner']['match_level'],trial_uuid=uuid)
        metrics=json.loads(conn.execute('SELECT metrics_json FROM ab_trials WHERE trial_id=?',(tid,)).fetchone()[0])
        if metrics.get('provenance_complete') is not True:raise ValueError('trial ownership failed')
        verdicts.append(dict(subject=subject,trial_id=tid,verdict=verdict,reason=reason))
    status=recipe_lifecycle.get_status(conn,**key)
    evidence=dict(key=key,arms=rows,trials=verdicts,lifecycle_status=status,
                  knowledge_db=str(db),shipped_store_changed=False)
    (trials/'judgement.json').write_text(json.dumps(evidence,indent=2)+'\n');conn.close()
    print(json.dumps(dict(trials=verdicts,lifecycle_status=status,shipped_store_changed=False)))


if __name__=='__main__':main()
