#!/usr/bin/env python3
"""Materialize the standard planner's real subjects and apply B via R2G CLI.

Only prepares trials. No arm becomes evidence until fresh execution/signoff
and normal ingestion/judging; copied pre-intervention reports are not results.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    root=Path('/tmp/sky130_aux208');repo=Path(__file__).resolve().parents[2]
    document=json.loads((root/'recipe_subjects_and_plan.json').read_text());plan=document['plan']
    if not plan or plan['match_level']!='exact' or len(plan['designs'])!=2:raise ValueError('expected exact two-family planned experiment')
    key=plan['key'];strategy=key['strategy']
    h6=hashlib.sha1('|'.join(key[k] for k in ('symptom_id','design_class','platform','strategy')).encode()).hexdigest()[:6].upper()
    env=os.environ.copy();env.update(R2G_JOURNAL='0',R2G_KNOWLEDGE_DB=str(root/'recipe_knowledge.sqlite'),R2G_HEURISTICS_PATH=str(root/'recipe_heuristics.json'))
    for name in ('PYTHONHOME','PYTHONPATH','PYTHONSAFEPATH'):env.pop(name,None)
    diagnose=repo/'r2g-skills/signoff-loop/scripts/reports/diagnose_signoff_fix.py'
    out=root/'recipe_trials';out.mkdir(exist_ok=False);rows=[]
    for subject in plan['designs']:
        source=Path(subject['project_path']);provenance=json.loads((source/'source_provenance.json').read_text())
        for repeat in range(2):
            for role in ('A','B'):
                project=out/(source.name+'_ab'+role+'_'+strategy[:8]+h6+'_'+str(repeat))
                shutil.copytree(source,project)
                cfg=project/'constraints/config.mk';cfg.write_text(cfg.read_text().replace(str(source),str(project)))
                for p in (project/'reports').glob('*.json'):
                    p.write_text(p.read_text().replace(str(source),str(project)))
                before=cfg.read_text()
                if role=='B':
                    args=[sys.executable,str(diagnose),str(project),'--check','lvs','--rank-first',strategy]
                    selected=subprocess.run(args+['--next'],env=env,text=True,capture_output=True,check=True)
                    applied=subprocess.run(args+['--apply',strategy],env=env,text=True,capture_output=True,check=True)
                    (project/'ab_selection.txt').write_text(selected.stdout+selected.stderr)
                    (project/'ab_application.json').write_text(applied.stdout)
                    if 'POST_GLOBAL_PLACE_TCL' not in cfg.read_text():raise ValueError('recipe did not land')
                elif cfg.read_text()!=before:raise ValueError('A arm config changed')
                if (project/'constraints/constraint.sdc').read_bytes()!=(source/'constraints/constraint.sdc').read_bytes():raise ValueError('constraints changed')
                for p in (source/'rtl').rglob('*'):
                    if p.is_file() and p.read_bytes()!=(project/'rtl'/p.relative_to(source/'rtl')).read_bytes():raise ValueError('RTL changed')
                rows.append(dict(project=str(project),subject=str(source),source_repository=provenance['source_repository'],
                    case=provenance['original_control_receipt']['design'],role=role,repeat=repeat,
                    config_sha256=hashlib.sha256(cfg.read_bytes()).hexdigest(),status='prepared_not_executed'))
    manifest=dict(key=key,planner=plan,arms=rows,independent_families=sorted({r['source_repository'] for r in rows}),
                  note='B uses standard R2G diagnose/apply with the intended A/B-only rank-first flag; no live promotion claimed')
    (out/'plan.json').write_text(json.dumps(manifest,indent=2)+'\n');print(json.dumps(dict(prepared_arms=len(rows),families=manifest['independent_families'])))


if __name__=='__main__':main()
