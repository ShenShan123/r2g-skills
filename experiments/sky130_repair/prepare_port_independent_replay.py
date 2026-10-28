"""Prepare original-source, normal-selected port-buffer reuse; no physical run."""
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
from strict_signoff import inspect_run
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
CASES=('C00559','C01164','C03715','C02197','C02994')


def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--case',choices=CASES,required=True);args=parser.parse_args();cid=args.case
    auxiliary=Path('/tmp/sky130_aux208');root=auxiliary/'port_independent_replay'/cid;root.mkdir(parents=True,exist_ok=False)
    original=root/'original';original.mkdir()
    with tarfile.open('/tmp/sky130_port_replay_sources_'+cid+'.tar.gz') as archive:archive.extractall(original,filter='data')
    metadata=json.loads((original/'manifest.json').read_text());assert metadata['case']==cid
    frozen=auxiliary/'port_scope_preflight/frozen'/cid
    export_manifest=json.loads((frozen.parent/'manifest.json').read_text())
    for name in ('config.mk','constraint.sdc','result.json','6_final.def','6_final.v','6_drc.lyrdb','netgen_lvs.rpt'):
        assert sha(frozen/name)==export_manifest[cid+'/'+name]['sha256']
    for name,digest in metadata['baseline_proof'].items():assert sha(frozen/name)==digest
    for rel,item in metadata['files'].items():
        path=original/rel
        if not path.resolve().is_relative_to(original) or sha(path)!=item['sha256']:raise ValueError('original source export changed')
    published=json.loads((REPO/'design_cases/sky130_recipe_validation_20261026/published.json').read_text())
    assert metadata['family'] not in {s['source_repository'] for s in published['runs']}
    signed=inspect_run(frozen);assert signed['drc']=='clean' and signed['lvs']=='pin_mismatch'
    subject=root/(metadata['family'].split('__')[0].lower()+'_original')
    (subject/'constraints').mkdir(parents=True);(subject/'reports').mkdir();shutil.copytree(original/'rtl',subject/'rtl')
    results=subject/'backend/RUN_FROZEN_SUBJECT/results';shutil.copytree(frozen,results)
    cfg=(frozen/'config.mk').read_text().replace('/proj/workarea/yangao/rtl-certified-handoff-0002/sources/',str(subject/'rtl')+'/')
    cfg=re.sub(r'^export SDC_FILE\s*=.*$','export SDC_FILE = '+str(subject/'constraints/constraint.sdc'),cfg,flags=re.M)
    (subject/'constraints/config.mk').write_text(cfg);shutil.copy2(frozen/'constraint.sdc',subject/'constraints/constraint.sdc')
    result=json.loads((frozen/'result.json').read_text())
    save(subject/'reports/ppa.json',dict(geometry=dict(instance_count=result['cells']),source_result_sha256=sha(frozen/'result.json')))
    save(subject/'reports/drc.json',dict(status='clean',total_violations=0,report_file=str(results/'6_drc.lyrdb')))
    save(subject/'reports/lvs.json',dict(status='mismatch',mismatch_class='top_pin_mismatch',tool='netgen',report_file=str(results/'netgen_lvs.rpt')))
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
    cli=[sys.executable,'-P',str(skill/'scripts/reports/diagnose_signoff_fix.py'),str(project),'--check','lvs']
    plan=json.loads(subprocess.check_output(cli+['--list'],env=env,text=True));strategy=next(s for s in plan['strategies'] if s['id']=='lvs_port_feedthrough_buffer')
    selected=subprocess.run(cli+['--next'],env=env,text=True,capture_output=True)
    save(trial/'selection.json',dict(returncode=selected.returncode,stdout=selected.stdout,stderr=selected.stderr))
    assert selected.returncode==0 and selected.stdout.strip().split('\t')==['lvs_port_feedthrough_buffer','place','lvs']
    assert strategy['lifecycle_status']=='promoted'
    applied=subprocess.run(cli+['--apply','lvs_port_feedthrough_buffer'],env=env,text=True,capture_output=True)
    save(trial/'application.json',dict(returncode=applied.returncode,stdout=applied.stdout,stderr=applied.stderr))
    assert applied.returncode==0 and json.loads(applied.stdout)['config_edits']==strategy['config_edits']
    assert sha(project/'constraints/constraint.sdc')==provenance['sdc_sha256']
    assert all(sha(project/'rtl'/name)==digest for name,digest in provenance['source_files'].items())
    arm=dict(project=str(project),subject=str(subject),source_repository=metadata['family'],case=result['design'],role='B',repeat=0,
             config_sha256=sha(cfgpath),source_provenance_sha256=sha(project/'source_provenance.json'))
    record=dict(key={**plan['routing_context'],'strategy':'lvs_port_feedthrough_buffer'},arms=[arm],mode='normal_unforced_independent_replay',
        forced_selection=False,physical_execution=False,lifecycle_scope=strategy.get('lifecycle_match_level'),
        original_source_files=len(provenance['source_files']),source_manifest_sha256=sha(original/'manifest.json'))
    save(trial/'plan.json',record);save(HERE/('port_'+cid.lower()+'_normal_prepared_20261027.json'),record)
    print(json.dumps(dict(case=cid,trials=str(trial),source_files=record['original_source_files'],forced_selection=False,physical_execution=False)))


if __name__=='__main__':main()
