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
sys.path[:0]=[str(SKILL/'knowledge'),str(SKILL/'scripts/reports')]
import recipe_lifecycle
import fixed_area_recipe
import diagnose_signoff_fix as diagnose
import re

sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()


def require_promoted(conn, context, strategy):
    status,scope=diagnose._lifecycle_status_with_scope_transfer(
        conn,recipe_lifecycle,**context,strategy=strategy)
    if status!='promoted':
        raise ValueError('independent normal area replay requires validated promotion; current status: '+str(status))
    return scope


def main():
    root=Path('/tmp/sky130_aux208/area_independent_replay')
    prepared=json.loads((root/'preparation.json').read_text())
    key={**prepared['routing_context'],'strategy':'fixed_area_route_relief'}
    subject=Path(prepared['project'])
    tool=Path('/home/yangao/r2g_toolchain/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad')
    if sha(tool)!='6454121a552fc8625fa27f474dfc699f2d82a10a8959238168f2c2249988fd48':
        raise ValueError('frozen geometry tool changed')
    os.environ['R2G_LAYOUT_OPENROAD']=str(tool)
    cfg=dict(re.findall(r'^export\s+(\w+)\s*=\s*(.*)$',(subject/'constraints/config.mk').read_text(),re.M))
    current=fixed_area_recipe.candidate(subject,{'status':'fail'},cfg)
    if current is None:raise ValueError('original area geometry no longer proves applicability')
    # Check actual lifecycle/evidence before creating a replay directory.
    with sqlite3.connect('file:'+str(SKILL/'knowledge/knowledge.sqlite')+'?mode=ro',uri=True) as conn:
        scope=require_promoted(conn,prepared['routing_context'],current)
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
    project=trial/'r01217_montgomery_engine_normal';shutil.copytree(subject,project)
    cfg=project/'constraints/config.mk';cfg.write_text(cfg.read_text().replace(str(subject),str(project)))
    for path in (project/'reports').glob('*.json'):
        path.write_text(path.read_text().replace(str(subject),str(project)))
    env=os.environ.copy();env.update(R2G_JOURNAL='0',R2G_KNOWLEDGE_DB=str(trial/'knowledge.sqlite'),R2G_HEURISTICS_PATH=str(trial/'heuristics.json'))
    cli=[sys.executable,'-P',str(SKILL/'scripts/reports/diagnose_signoff_fix.py'),str(project),'--check','route']
    selected=subprocess.run(cli+['--next'],env=env,text=True,capture_output=True)
    (trial/'selection.json').write_text(json.dumps(dict(returncode=selected.returncode,stdout=selected.stdout,stderr=selected.stderr),indent=2)+'\n')
    if selected.returncode!=0 or selected.stdout.strip().split('\t')!=['fixed_area_route_relief','floorplan','route']:
        raise ValueError('ordinary selection did not choose validated area recipe; retain diagnostic, do not force')
    applied=subprocess.run(cli+['--apply','fixed_area_route_relief'],env=env,text=True,capture_output=True)
    (trial/'application.json').write_text(json.dumps(dict(returncode=applied.returncode,stdout=applied.stdout,stderr=applied.stderr),indent=2)+'\n')
    if applied.returncode!=0:raise ValueError('ordinary application failed')
    result=json.loads(applied.stdout)
    if result['status']!='applied' or result['config_edits']!=current['config_edits']:
        raise ValueError('unexpected physical intervention')
    if sha(project/'constraints/constraint.sdc')!=prov['sdc_sha256']:raise ValueError('application changed SDC')
    if any(sha(project/'rtl'/rel)!=digest for rel,digest in prov['source_files'].items()):raise ValueError('application changed RTL/ROM')
    arm=dict(project=str(project),subject=str(subject),source_repository=prov['family'],case=prov['design'],
             role='B',repeat=0,config_sha256=sha(cfg),source_provenance_sha256=sha(project/'source_provenance.json'))
    plan=dict(key=key,arms=[arm],mode='normal_unforced_independent_replay',
              promoted_store_sha256=sha(trial/'knowledge.sqlite'),forced_selection=False,
              physical_execution=False,lifecycle_scope=scope,selection_receipt=str(trial/'selection.json'),application_receipt=str(trial/'application.json'))
    (trial/'plan.json').write_text(json.dumps(plan,indent=2)+'\n')
    print(json.dumps(dict(prepared=str(trial),forced_selection=False,physical_execution=False)))


if __name__=='__main__':main()
