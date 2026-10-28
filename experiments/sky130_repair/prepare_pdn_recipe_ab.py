#!/usr/bin/env python3
"""Prepare two-family repeated PDN A/B trials from actual frozen failures.

Original file bytes are checked against a read-only export. The ordinary
planner selects current exhibitors; only B receives the CLI-applied hook.
Nothing is executed or promoted here.
"""
import base64
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ET

HERE=Path(__file__).resolve().parent;REPO=HERE.parents[1]
sys.path[:0]=[str(HERE),str(REPO/'r2g-skills/signoff-loop/knowledge')]
import knowledge_db
import ingest_run
import ab_runner
import recipe_lifecycle
from strict_signoff import inspect_run


def save(path,data):path.write_text(json.dumps(data,indent=2)+'\n')
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    bundle_path=Path('/tmp/sky130_pdn_recipe_source_bundle.json');bundle=json.loads(bundle_path.read_text())
    if set(bundle['cases'])!={'C01466','C03125'}:raise ValueError('unexpected experiment subjects')
    root=Path('/tmp/sky130_aux208/pdn_recipe');root.mkdir(exist_ok=False)
    frozen=root/'frozen';frozen.mkdir()
    for rel,item in bundle['files'].items():
        p=frozen/rel
        if not p.resolve().is_relative_to(frozen) or Path(rel).parts[0] not in ('baseline','package'):raise ValueError('invalid export path')
        raw=base64.b64decode(item['data'],validate=True)
        if hashlib.sha256(raw).hexdigest()!=item['sha256']:raise ValueError('export hash mismatch')
        p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
    skill=REPO/'r2g-skills/signoff-loop';db=root/'knowledge.sqlite'
    with sqlite3.connect('file:'+str(skill/'knowledge/knowledge.sqlite')+'?mode=ro',uri=True) as src,sqlite3.connect(db) as dst:src.backup(dst)
    shutil.copy2(skill/'knowledge/heuristics.json',root/'heuristics.json')
    os.environ.update(R2G_JOURNAL='0',R2G_KNOWLEDGE_DB=str(db),R2G_HEURISTICS_PATH=str(root/'heuristics.json'))
    projects=[];families={};original_prefix='/proj/workarea/yangao/rtl-certified-handoff-0002/sources/'
    for cid,case in bundle['cases'].items():
        run=frozen/'baseline'/case['run_name'];signed=inspect_run(run)
        if signed['lvs']!='clean' or signed['drc']!='violations':raise ValueError('subject must have real DRC failure and final LVS match')
        cfg=(run/'config.mk').read_text();sources=re.search(r'^export VERILOG_FILES\s*=\s*(.*)$',cfg,re.M)[1].split()
        if any(not p.startswith(original_prefix) for p in sources):raise ValueError('unreviewed source alias')
        repos={Path(p.removeprefix(original_prefix)).parts[0] for p in sources}
        if len(repos)!=1:raise ValueError('ambiguous RTL family')
        family=repos.pop();top=re.search(r'^export DESIGN_NAME\s*=\s*(.*)$',cfg,re.M)[1].strip()
        project=root/'subjects'/(family.split('__')[0].lower()+'_'+top);(project/'constraints').mkdir(parents=True);(project/'reports').mkdir();(project/'rtl').mkdir()
        shutil.copytree(frozen/'package/sources'/family,project/'rtl'/family)
        relocated=cfg.replace(original_prefix,str(project/'rtl')+'/')
        relocated=re.sub(r'^export SDC_FILE\s*=.*$', 'export SDC_FILE = '+str(project/'constraints/constraint.sdc'),relocated,flags=re.M)
        (project/'constraints/config.mk').write_text(relocated);shutil.copy2(run/'constraint.sdc',project/'constraints/constraint.sdc')
        results=project/'backend/RUN_FROZEN_SUBJECT/results';results.parent.mkdir(parents=True);shutil.copytree(run,results)
        (project/'drc').mkdir();shutil.copy2(run/'6_drc.lyrdb',project/'drc/6_drc.lyrdb')
        counts=Counter(i.findtext('category') for i in ET.parse(run/'6_drc.lyrdb').findall('.//items/item'))
        save(project/'reports/drc.json',dict(status='fail',total_violations=sum(counts.values()),categories={k:{'count':v} for k,v in counts.items()},report_file=str(results/'6_drc.lyrdb')))
        save(project/'reports/lvs.json',dict(status='clean',tool='netgen',report_file=str(results/'netgen_lvs.rpt')))
        result=json.loads((run/'result.json').read_text())
        save(project/'reports/ppa.json',dict(geometry={'instance_count':result['cells']},summary={'timing':{'setup_wns':result['worst_slack']}},source_result=str(results/'result.json'),source_result_sha256=sha(results/'result.json'),note='Only retained frozen driver measurements; no invented stage log or new execution'))
        provenance=dict(case=cid,design=case['run_name'].removesuffix('__sky130hd__p5'),family=family,
                        frozen_run=str(run),original_run=case['original_run'],baseline_signature=signed,
                        source_files={str(p.relative_to(project/'rtl')):sha(p) for p in (project/'rtl').rglob('*') if p.is_file()},
                        sdc_sha256=sha(project/'constraints/constraint.sdc'))
        save(project/'source_provenance.json',provenance);projects.append(project);families[top]=family
    save(root/'families.json',{'mappings':families,'patterns':[]})
    conn=knowledge_db.connect();knowledge_db.ensure_schema(conn);subjects=[]
    diagnose=skill/'scripts/reports/diagnose_signoff_fix.py'
    for project in projects:
        rid=ingest_run.ingest(project,conn,families_path=root/'families.json')
        plan=json.loads(subprocess.check_output([sys.executable,str(diagnose),str(project),'--check','drc','--list'],text=True))
        strategy=next(s for s in plan['strategies'] if s['id']=='pin_pdn_clearance')
        subjects.append(dict(project=str(project),run_id=rid,context=plan['routing_context'],geometry=strategy['geometry_evidence']))
    anchor=next(s for s in subjects if json.loads((Path(s['project'])/'source_provenance.json').read_text())['case']=='C03125')
    key={k:anchor['context'][k] for k in ('symptom_id','design_class','platform')};key['strategy']='pin_pdn_clearance'
    if len({s['context']['symptom_id'] for s in subjects})!=1:raise ValueError('different symptom keys require separate experiments')
    recipe_lifecycle.enqueue_candidate(conn,provenance='sky130_pdn_geometry_two_family_ab',**key)
    plan=ab_runner.plan_trial(conn,**key,n_designs=2,allowed_project_paths={str(p) for p in projects})
    if not plan or len(plan['designs'])!=2:raise ValueError('ordinary planner did not select both real subjects')
    trials=root/'trials';trials.mkdir();arms=[]
    h6=hashlib.sha1('|'.join(key[k] for k in ('symptom_id','design_class','platform','strategy')).encode()).hexdigest()[:6].upper()
    for subject in plan['designs']:
        source=Path(subject['project_path']);prov=json.loads((source/'source_provenance.json').read_text())
        for repeat in range(2):
            for role in ('A','B'):
                project=trials/(source.name+'_ab'+role+'_pin_pdn_'+h6+'_'+str(repeat));shutil.copytree(source,project)
                cfg=project/'constraints/config.mk';cfg.write_text(cfg.read_text().replace(str(source),str(project)))
                for p in (project/'reports').glob('*.json'):p.write_text(p.read_text().replace(str(source),str(project)))
                if role=='B':
                    applied=subprocess.run([sys.executable,str(diagnose),str(project),'--check','drc','--rank-first','pin_pdn_clearance','--apply','pin_pdn_clearance'],text=True,capture_output=True,check=True)
                    save(project/'ab_application.json',json.loads(applied.stdout))
                if sha(project/'constraints/constraint.sdc')!=prov['sdc_sha256']:raise ValueError('SDC changed')
                for rel,digest in prov['source_files'].items():
                    if sha(project/'rtl'/rel)!=digest:raise ValueError('RTL or ROM changed')
                arms.append(dict(project=str(project),subject=str(source),source_repository=prov['family'],case=prov['design'],role=role,repeat=repeat,config_sha256=sha(cfg),source_provenance_sha256=sha(project/'source_provenance.json'),status='prepared_not_executed'))
    save(trials/'plan.json',dict(key=key,planner=plan,arms=arms,independent_families=sorted(set(families.values())),source_bundle_sha256=sha(bundle_path),subjects=subjects,scope='Pooled-class physical evidence for the anchor key only; original per-run classes preserved; no promotion'))
    conn.close();print(json.dumps(dict(root=str(root),key=key,match_level=plan['match_level'],prepared_arms=len(arms),physical_execution=False)))


if __name__=='__main__':main()
