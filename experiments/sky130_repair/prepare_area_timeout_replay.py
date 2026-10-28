"""Prepare one preflighted original-source timeout replay with the existing area recipe."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
HERE=Path(__file__).resolve().parent;REPO=HERE.parents[1]
sys.path.insert(0,str(HERE))
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()


def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--case',required=True);args=parser.parse_args();cid=args.case
    if not re.fullmatch(r'C[0-9]{5}',cid):raise ValueError('invalid case ID')
    preflight=json.loads((HERE/('area_'+cid.lower()+'_timeout_preflight_20261027.json')).read_text())
    if preflight['normal_next']['stdout'].strip().split('\t')!=['fixed_area_route_relief','floorplan','route']:raise ValueError('ordinary geometry preflight not passed')
    auxiliary=Path('/tmp/sky130_aux208');root=auxiliary/'area_timeout_replay'/cid;root.mkdir(parents=True,exist_ok=False)
    original=root/'original';original.mkdir()
    with tarfile.open('/tmp/sky130_original_sources_'+cid+'.tar.gz') as archive:archive.extractall(original,filter='data')
    metadata=json.loads((original/'manifest.json').read_text());assert metadata['case']==cid
    frozen=auxiliary/('area_timeout_'+cid.lower()+'_preflight')/'frozen'
    export_manifest=json.loads((frozen/'manifest.json').read_text())
    for name in ('config.mk','constraint.sdc','result.json','flow.log','3_place.odb'):
        assert sha(frozen/name)==export_manifest[name]['sha256']
    for name,digest in metadata['baseline_proof'].items():assert sha(frozen/name)==digest
    for rel,item in metadata['files'].items():
        path=original/rel
        if not path.resolve().is_relative_to(original) or sha(path)!=item['sha256']:raise ValueError('original source export changed')
    published=json.loads((REPO/'design_cases/sky130_area_recipe_validation_20261027/published.json').read_text())
    assert metadata['family'] not in {s['source_repository'] for s in published['runs']}
    baseline_result=json.loads((frozen/'result.json').read_text())
    if baseline_result['status']!='fail' or baseline_result.get('failure_class')!='timeout' or not baseline_result['stage'].startswith('5_'):raise ValueError('not a retained route timeout')
    subject=root/(metadata['family'].split('__')[0].lower()+'_original')
    (subject/'constraints').mkdir(parents=True);(subject/'reports').mkdir();shutil.copytree(original/'rtl',subject/'rtl')
    results=subject/'backend/RUN_FROZEN_ROUTE_FAILURE/results';shutil.copytree(frozen,results)
    cfg=(frozen/'config.mk').read_text().replace('/proj/workarea/yangao/rtl-certified-handoff-0002/sources/',str(subject/'rtl')+'/')
    cfg=re.sub(r'^export SDC_FILE\s*=.*$','export SDC_FILE = '+str(subject/'constraints/constraint.sdc'),cfg,flags=re.M)
    (subject/'constraints/config.mk').write_text(cfg);shutil.copy2(frozen/'constraint.sdc',subject/'constraints/constraint.sdc')
    result=json.loads((frozen/'result.json').read_text())
    save(subject/'reports/ppa.json',dict(geometry=dict(instance_count=result['cells']),source_result_sha256=sha(frozen/'result.json')))
    save(subject/'reports/route.json',dict(status='timeout',stage=result['stage'],source_result_sha256=sha(frozen/'result.json')))
    event=dict(stage='route',status='fail',failure_class='timeout',
               provenance='reconstructed_from_frozen_terminal_result_and_full_log',
               source_result_sha256=sha(frozen/'result.json'),source_log_sha256=sha(frozen/'flow.log'))
    (results.parent/'stage_log.jsonl').write_text(json.dumps(event)+'\n')
    provenance=dict(case=cid,design=result['design'],family=metadata['family'],
        source_files={str(p.relative_to(subject/'rtl')):sha(p) for p in (subject/'rtl').rglob('*') if p.is_file()},
        sdc_sha256=sha(subject/'constraints/constraint.sdc'),original_export_manifest_sha256=sha(original/'manifest.json'))
    save(subject/'source_provenance.json',provenance)
    trial=root/'normal_replay';trial.mkdir();project=trial/(metadata['family'].split('__')[0].lower()+'_normal');shutil.copytree(subject,project)
    cfgpath=project/'constraints/config.mk';cfgpath.write_text(cfgpath.read_text().replace(str(subject),str(project)))
    for path in (project/'reports').glob('*.json'):path.write_text(path.read_text().replace(str(subject),str(project)))
    skill=REPO/'r2g-skills/signoff-loop'
    with sqlite3.connect('file:'+str(skill/'knowledge/knowledge.sqlite')+'?mode=ro',uri=True) as src,sqlite3.connect(trial/'knowledge.sqlite') as dst:src.backup(dst)
    shutil.copy2(skill/'knowledge/heuristics.json',trial/'heuristics.json')
    env=os.environ.copy();env.update(R2G_JOURNAL='0',R2G_KNOWLEDGE_DB=str(trial/'knowledge.sqlite'),R2G_HEURISTICS_PATH=str(trial/'heuristics.json'))
    tool=Path('/home/yangao/r2g_toolchain/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad')
    if sha(tool)!='6454121a552fc8625fa27f474dfc699f2d82a10a8959238168f2c2249988fd48':raise ValueError('frozen geometry tool changed')
    env['R2G_LAYOUT_OPENROAD']=str(tool)
    cli=[sys.executable,'-P',str(skill/'scripts/reports/diagnose_signoff_fix.py'),str(project),'--check','route']
    plan=json.loads(subprocess.check_output(cli+['--list'],env=env,text=True));strategy=next(s for s in plan['strategies'] if s['id']=='fixed_area_route_relief')
    selected=subprocess.run(cli+['--next'],env=env,text=True,capture_output=True)
    save(trial/'selection.json',dict(returncode=selected.returncode,stdout=selected.stdout,stderr=selected.stderr))
    assert selected.returncode==0 and selected.stdout.strip().split('\t')==['fixed_area_route_relief','floorplan','route']
    assert strategy['lifecycle_status']=='promoted'
    applied=subprocess.run(cli+['--apply','fixed_area_route_relief'],env=env,text=True,capture_output=True)
    save(trial/'application.json',dict(returncode=applied.returncode,stdout=applied.stdout,stderr=applied.stderr))
    assert applied.returncode==0 and json.loads(applied.stdout)['config_edits']==strategy['config_edits']
    assert sha(project/'constraints/constraint.sdc')==provenance['sdc_sha256']
    assert all(sha(project/'rtl'/name)==digest for name,digest in provenance['source_files'].items())
    arm=dict(project=str(project),subject=str(subject),source_repository=metadata['family'],case=result['design'],role='B',repeat=0,
             config_sha256=sha(cfgpath),source_provenance_sha256=sha(project/'source_provenance.json'))
    record=dict(key={**plan['routing_context'],'strategy':'fixed_area_route_relief'},arms=[arm],mode='normal_unforced_independent_replay',
        forced_selection=False,physical_execution=False,lifecycle_scope=strategy.get('lifecycle_match_level'),
        original_source_files=len(provenance['source_files']),source_manifest_sha256=sha(original/'manifest.json'))
    save(trial/'plan.json',record);save(HERE/('area_'+cid.lower()+'_normal_prepared_20261027.json'),record)
    print(json.dumps(dict(case=cid,trials=str(trial),source_files=record['original_source_files'],forced_selection=False,physical_execution=False)))


if __name__=='__main__':main()
