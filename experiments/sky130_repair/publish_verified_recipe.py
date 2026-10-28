#!/usr/bin/env python3
"""Curate real SKY130 A/B evidence through the ordinary ingest/lifecycle APIs.

Default: rehearse against a SQLite backup. --publish: append the same verified
evidence to the shipped store, retaining a pre-change backup. Never write a
promotion status or success metric directly.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys

HERE=Path(__file__).resolve().parent;REPO=HERE.parents[1]
sys.path[:0]=[str(HERE),str(REPO/'r2g-skills/signoff-loop/knowledge')]
from strict_signoff import inspect_run
import knowledge_db
import ingest_run
import ab_runner
import recipe_lifecycle
from judge_recipe_ab import validate_arm_scope


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n')


def prepare(source,dest):
    plan=json.loads((source/'plan.json').read_text())
    private=json.loads((source/'judgement.json').read_text())
    if private['lifecycle_status']!='promoted':raise ValueError('private validation not promoted')
    if len(plan['arms'])!=8:raise ValueError('requires two subjects, two repeats and both arms')
    dest.mkdir(parents=True,exist_ok=True)
    manifest=dest/'durable_evidence.json'
    if manifest.exists():
        evidence=json.loads(manifest.read_text())
        for rel,digest in evidence['files'].items():
            if sha(dest/rel)!=digest:raise ValueError('durable evidence changed: '+rel)
        if evidence['key']!=plan['key'] or sha(dest/'original_plan.json')!=sha(source/'plan.json'):
            raise ValueError('destination contains evidence from a different plan')
        return evidence
    arms=[]
    for arm in plan['arms']:
        old=Path(arm['project']);record=json.loads((old/'execution.json').read_text())
        if record['status']!='completed' or record['flow_returncode']!=0:raise ValueError('incomplete execution')
        if inspect_run(Path(record['run']))!=record['strict_signoff']:raise ValueError('changed signed execution')
        new=dest/old.name
        shutil.copytree(old,new,ignore=shutil.ignore_patterns('ab_work','drc_projects','lvs_projects'))
        # Keep raw physical artifacts and historical metadata byte-identical.
        # Only report references/config source paths used by ingest relocate.
        relocations={}
        for path in list((new/'reports').glob('*.json'))+[new/'constraints/config.mk']:
            original=path.read_text();updated=original.replace(str(old),str(new))
            if original!=updated:
                relocations[str(path.relative_to(new))]=dict(before=sha(path),old_prefix=str(old),new_prefix=str(new))
                path.write_text(updated)
        actual=inspect_run(new/'physical_run');expected=dict(record['strict_signoff'])
        expected['lvs_final_source']=str(new/'physical_run/netgen_lvs.rpt')
        if actual!=expected:raise ValueError('durable copy differs from verified execution')
        if sha(new/'constraints/constraint.sdc')!=sha(old/'constraints/constraint.sdc'):raise ValueError('SDC changed')
        arms.append(dict(arm,project=str(new),original_project=str(old),strict_signoff=actual,
                         wall_seconds=record['wall_seconds'],path_relocations=relocations))
    pdn=plan['key']['strategy']=='pin_pdn_clearance'
    shutil.copy2(source.parent/('families.json' if pdn else 'recipe_families.json'),dest/'families.json')
    save(dest/'original_plan.json',plan);save(dest/'original_judgement.json',private)
    evidence=dict(key=plan['key'],arms=arms,planner=plan['planner'],
                  source_plan_sha256=sha(source/'plan.json'),
                  files={str(p.relative_to(dest)):sha(p) for p in dest.rglob('*') if p.is_file()})
    save(manifest,evidence);return evidence


def main():
    p=argparse.ArgumentParser();p.add_argument('--publish',action='store_true')
    p.add_argument('--recipe',choices=('port_buffer','pin_pdn'),default='port_buffer');a=p.parse_args()
    source=Path('/tmp/sky130_aux208')/('pdn_recipe/trials' if a.recipe=='pin_pdn' else 'recipe_trials')
    dest=REPO/'design_cases'/('sky130_pdn_recipe_validation_20261026' if a.recipe=='pin_pdn' else 'sky130_recipe_validation_20261026')
    evidence=prepare(source,dest)
    key=evidence['key'];store=knowledge_db.DEFAULT_DB_PATH
    if a.publish:
        rehearsal=json.loads((dest/'rehearsal.json').read_text())
        if rehearsal['status']!='promoted' or rehearsal['evidence_manifest_sha256']!=sha(dest/'durable_evidence.json'):
            raise ValueError('successful rehearsal of this exact evidence is required')
        backup=dest/'before_publish.sqlite'
        if not backup.exists():
            with sqlite3.connect(str(store)) as src,sqlite3.connect(str(backup)) as target:src.backup(target)
        db=store;os.environ['R2G_ALLOW_SHIPPED_STORE_WRITE']='1'
        heuristics=knowledge_db.DEFAULT_HEURISTICS_PATH
    else:
        db=dest/'rehearsal.sqlite'
        if not db.exists():
            with sqlite3.connect(str(store)) as src,sqlite3.connect(str(db)) as target:src.backup(target)
        heuristics=dest/'rehearsal_heuristics.json'
        if not heuristics.exists():shutil.copy2(knowledge_db.DEFAULT_HEURISTICS_PATH,heuristics)
    os.environ.update(R2G_JOURNAL='0',R2G_KNOWLEDGE_DB=str(db),R2G_HEURISTICS_PATH=str(heuristics))
    conn=knowledge_db.connect();knowledge_db.ensure_schema(conn)
    recipe_lifecycle.enqueue_candidate(conn,provenance='verified_sky130_dev_physical_ab_20261026',**key)
    groups={};rows=[]
    for arm in evidence['arms']:
        project=Path(arm['project']);actual=inspect_run(project/'physical_run')
        if actual!=arm['strict_signoff']:raise ValueError('signed evidence changed before ingest')
        rid=ingest_run.ingest(project,conn,families_path=dest/'families.json')
        cursor=conn.execute('SELECT * FROM runs WHERE run_id=?',(rid,));row=dict(zip([c[0] for c in cursor.description],cursor.fetchone()))
        validate_arm_scope(evidence,arm,row)
        if knowledge_db.is_success(row)!=actual['dual_clean']:raise ValueError('learning acceptance disagrees with physical evidence')
        sample=dict(run_id=rid,project=str(project),is_success=actual['dual_clean'],wall_s=arm['wall_seconds'],
                    repeat=arm['repeat'],source_repository=arm['source_repository'],
                    design_class=row['design_class'],platform=row['platform'])
        groups.setdefault(arm['subject'],{}).setdefault(arm['role'],[]).append(sample);rows.append(sample)
    verdicts=[]
    for subject,arms in groups.items():
        for role in ('A','B'):
            arms[role].sort(key=lambda s:s['repeat'])
            if [s['repeat'] for s in arms[role]]!=[0,1]:raise ValueError('missing physical repeats')
        verdict,reason=ab_runner.judge_repeated_ex(arms['A'],arms['B'])
        if verdict!='win':raise ValueError('replayed A/B judge did not validate strategy')
        ids=[s['run_id'] for role in ('A','B') for s in arms[role]]
        tid=ab_runner.record_trial(conn,key=key,verdict=verdict,arm_a_run_id=arms['A'][0]['run_id'],
            arm_b_run_id=arms['B'][0]['run_id'],metrics=dict(arm_a_samples=arms['A'],arm_b_samples=arms['B'],reason=reason,
                source='durable_copy_of_verified_physical_AB',evidence_manifest=str(dest/'durable_evidence.json')),
            match_level=evidence['planner']['match_level'],trial_uuid=hashlib.sha256('|'.join(ids).encode()).hexdigest())
        metrics=json.loads(conn.execute('SELECT metrics_json FROM ab_trials WHERE trial_id=?',(tid,)).fetchone()[0])
        if metrics.get('provenance_complete') is not True:raise ValueError('trial ownership failed')
        verdicts.append(dict(subject=subject,trial_id=tid,verdict=verdict,reason=reason))
    status=recipe_lifecycle.get_status(conn,**key)
    if status!='promoted':raise ValueError('normal lifecycle did not promote')
    report=dict(key=key,status=status,store=str(db),published=a.publish,trials=verdicts,runs=rows,
                evidence_manifest_sha256=sha(dest/'durable_evidence.json'))
    save(dest/('published.json' if a.publish else 'rehearsal.json'),report)
    conn.close();print(json.dumps(report))


if __name__=='__main__':main()
