"""Prepare one normal-selected physical replay only after real shipped promotion."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[1]
SKILL=REPO/'r2g-skills/signoff-loop'
sys.path.insert(0,str(SKILL/'knowledge'))
import recipe_lifecycle

sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()


def require_promoted(conn, key):
    status=recipe_lifecycle.get_status(conn,default=None,**key)
    if status!='promoted':
        raise ValueError('independent normal replay requires shipped promotion; current status: '+str(status))


def main():
    root=Path('/tmp/sky130_aux208/pdn_independent_replay')
    prepared=json.loads((root/'preparation.json').read_text())
    key={**prepared['routing_context'],'strategy':'pin_pdn_clearance'}
    # Read-only gate before creating directories, cloning or applying anything.
    with sqlite3.connect('file:'+str(SKILL/'knowledge/knowledge.sqlite')+'?mode=ro',uri=True) as conn:
        require_promoted(conn,key)
    subject=Path(prepared['project'])
    if sha(subject/'constraints/config.mk')!=prepared['config_sha256']:
        raise ValueError('untouched subject configuration changed')
    prov=json.loads((subject/'source_provenance.json').read_text())
    for rel,digest in prov['source_files'].items():
        if sha(subject/'rtl'/rel)!=digest:raise ValueError('original RTL or ROM changed')
    if sha(subject/'constraints/constraint.sdc')!=prov['sdc_sha256']:raise ValueError('original SDC changed')
    trial=root/'normal_replay';trial.mkdir(exist_ok=False)
    with sqlite3.connect('file:'+str(SKILL/'knowledge/knowledge.sqlite')+'?mode=ro',uri=True) as src,sqlite3.connect(trial/'knowledge.sqlite') as dst:
        src.backup(dst)
    shutil.copy2(SKILL/'knowledge/heuristics.json',trial/'heuristics.json')
    project=trial/'r01493_sine_gen_normal';shutil.copytree(subject,project)
    cfg=project/'constraints/config.mk';cfg.write_text(cfg.read_text().replace(str(subject),str(project)))
    for path in (project/'reports').glob('*.json'):
        path.write_text(path.read_text().replace(str(subject),str(project)))
    env=os.environ.copy();env.update(R2G_JOURNAL='0',R2G_KNOWLEDGE_DB=str(trial/'knowledge.sqlite'),R2G_HEURISTICS_PATH=str(trial/'heuristics.json'))
    cli=[sys.executable,'-P',str(SKILL/'scripts/reports/diagnose_signoff_fix.py'),str(project),'--check','drc']
    selected=subprocess.run(cli+['--next'],env=env,text=True,capture_output=True)
    (trial/'selection.json').write_text(json.dumps(dict(returncode=selected.returncode,stdout=selected.stdout,stderr=selected.stderr),indent=2)+'\n')
    if selected.returncode!=0 or selected.stdout.strip().split('\t')!=['pin_pdn_clearance','floorplan','drc']:
        raise ValueError('ordinary selection did not choose promoted PDN recipe; retain diagnostic, do not force')
    applied=subprocess.run(cli+['--apply','pin_pdn_clearance'],env=env,text=True,capture_output=True)
    (trial/'application.json').write_text(json.dumps(dict(returncode=applied.returncode,stdout=applied.stdout,stderr=applied.stderr),indent=2)+'\n')
    if applied.returncode!=0:raise ValueError('ordinary application failed')
    result=json.loads(applied.stdout)
    if result['status']!='applied' or set(result['config_edits'])!={'PRE_IO_PLACEMENT_TCL'}:
        raise ValueError('unexpected physical intervention')
    if sha(project/'constraints/constraint.sdc')!=prov['sdc_sha256']:raise ValueError('application changed SDC')
    if any(sha(project/'rtl'/rel)!=digest for rel,digest in prov['source_files'].items()):raise ValueError('application changed RTL/ROM')
    arm=dict(project=str(project),subject=str(subject),source_repository=prov['family'],case=prov['design'],
             role='B',repeat=0,config_sha256=sha(cfg),source_provenance_sha256=sha(project/'source_provenance.json'))
    plan=dict(key=key,arms=[arm],mode='normal_unforced_independent_replay',
              promoted_store_sha256=sha(trial/'knowledge.sqlite'),forced_selection=False,
              physical_execution=False,selection_receipt=str(trial/'selection.json'),application_receipt=str(trial/'application.json'))
    (trial/'plan.json').write_text(json.dumps(plan,indent=2)+'\n')
    print(json.dumps(dict(prepared=str(trial),forced_selection=False,physical_execution=False)))


if __name__=='__main__':main()
