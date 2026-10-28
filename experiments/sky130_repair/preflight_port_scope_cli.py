"""Real frozen report/geometry CLI selection checks; no HDL execution or edits."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile

HERE=Path(__file__).resolve().parent;REPO=HERE.parents[1]
sys.path.insert(0,str(HERE))
from strict_signoff import inspect_run
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    root=Path('/tmp/sky130_aux208/port_scope_preflight');root.mkdir(exist_ok=False)
    frozen=root/'frozen';frozen.mkdir()
    with tarfile.open('/tmp/sky130_port_scope_inputs.tar.gz') as archive:
        archive.extractall(frozen,filter='data')
    manifest=json.loads((frozen/'manifest.json').read_text())
    for name,metadata in manifest.items():
        if sha(frozen/name)!=metadata['sha256']:raise ValueError('frozen export changed')
    skill=REPO/'r2g-skills/signoff-loop'
    with sqlite3.connect('file:'+str(skill/'knowledge/knowledge.sqlite')+'?mode=ro',uri=True) as source,sqlite3.connect(root/'knowledge.sqlite') as target:
        source.backup(target)
    shutil.copy2(skill/'knowledge/heuristics.json',root/'heuristics.json')
    env=os.environ.copy();env.update(R2G_JOURNAL='0',R2G_KNOWLEDGE_DB=str(root/'knowledge.sqlite'),R2G_HEURISTICS_PATH=str(root/'heuristics.json'))
    records=[]
    for cid in ('C00559','C01164','C03715','C02197','C02994'):
        original=frozen/cid;result=json.loads((original/'result.json').read_text())
        signed=inspect_run(original)
        if signed['drc']!='clean' or signed['lvs']!='pin_mismatch':raise ValueError('frozen final reports do not support the selected symptom')
        project=root/cid;(project/'constraints').mkdir(parents=True);(project/'reports').mkdir()
        shutil.copy2(original/'config.mk',project/'constraints/config.mk')
        shutil.copy2(original/'constraint.sdc',project/'constraints/constraint.sdc')
        results=project/'backend/RUN_FROZEN_SUBJECT/results';shutil.copytree(original,results)
        def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n')
        save(project/'reports/ppa.json',dict(geometry=dict(instance_count=result['cells']),source_result_sha256=sha(original/'result.json')))
        save(project/'reports/drc.json',dict(status='clean',total_violations=0,report_file=str(results/'6_drc.lyrdb')))
        save(project/'reports/lvs.json',dict(status='mismatch',mismatch_class='top_pin_mismatch',tool='netgen',report_file=str(results/'netgen_lvs.rpt')))
        cfg=sha(project/'constraints/config.mk');sdc=sha(project/'constraints/constraint.sdc')
        cli=[sys.executable,'-P',str(skill/'scripts/reports/diagnose_signoff_fix.py'),str(project),'--check','lvs']
        plan=json.loads(subprocess.check_output(cli+['--list'],env=env,text=True))
        strategy=next(s for s in plan['strategies'] if s['id']=='lvs_port_feedthrough_buffer')
        selected=subprocess.run(cli+['--next'],env=env,text=True,capture_output=True)
        assert selected.returncode==0 and selected.stdout.strip().split('\t')==['lvs_port_feedthrough_buffer','place','lvs'],selected.stdout+selected.stderr
        assert strategy['lifecycle_status']=='promoted'
        assert sha(project/'constraints/config.mk')==cfg and sha(project/'constraints/constraint.sdc')==sdc
        records.append(dict(case=cid,project=str(project),context=plan['routing_context'],strategy=strategy,
                            next=dict(returncode=selected.returncode,stdout=selected.stdout),config_unchanged=True,sdc_unchanged=True))
    output=dict(records=records,physical_execution=False,forced_selection=False,shipped_store_changed=False,
                source_manifest_sha256=sha(frozen/'manifest.json'),
                scope='Actual frozen geometry/reports only. Original RTL paths remain remote; these are selection fixtures, not executable physical subjects or successful repairs.')
    save(HERE/'port_scope_cli_preflight_20261027.json',output)
    print(json.dumps(dict(cases=[r['case'] for r in records],normal_selection='pass',physical_execution=False)))


if __name__=='__main__':main()
