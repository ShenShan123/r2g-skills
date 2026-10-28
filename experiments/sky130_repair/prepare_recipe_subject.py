#!/usr/bin/env python3
"""Adapt a real failing A run to R2G's project/report schema, with provenance.

No trial or success is invented: original artifacts are copied, the shipped
PPA reader extracts metrics, and the final top-level LVS verdict is required.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from strict_signoff import inspect_run


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser();p.add_argument('--case',required=True);a=p.parse_args()
    root=Path('/tmp/sky130_aux208');repo=Path(__file__).resolve().parents[2]
    case=json.loads((root/'cases.json').read_text())[a.case];design=case['run_name'].removesuffix('__sky130hd__p5')
    receipt=json.loads((root/'results_control/cases'/design/'repair.json').read_text());run=Path(receipt['attempts'][-1]['run'])
    check=inspect_run(run)
    if check['drc']!='clean' or check['lvs']!='pin_mismatch':raise ValueError('subject does not exhibit the verified target symptom')
    source_prefix='/proj/workarea/yangao/rtl-certified-handoff-0002/sources'
    cfg=(run/'config.mk').read_text();files=re.search(r'^export VERILOG_FILES\s*=\s*(.*)$',cfg,re.M).group(1).split()
    source_repos={Path(name.removeprefix(source_prefix+'/')).parts[0] for name in files}
    if len(source_repos)!=1 or any(not name.startswith(source_prefix+'/') for name in files):raise ValueError('needs source-scope review')
    family=next(iter(source_repos));project=root/'recipe_projects'/(family.split('__')[0].lower()+'_'+design.split('__')[1]);project.mkdir(parents=True,exist_ok=False)
    constraints=project/'constraints';constraints.mkdir();rtl=project/'rtl';rtl.mkdir()
    original=root/'package/sources'/family;shutil.copytree(original,rtl/family)
    source_hashes={str(f.relative_to(original)):sha(f) for f in original.rglob('*') if f.is_file()}
    assert all(sha(rtl/family/name)==digest for name,digest in source_hashes.items())
    cfg=cfg.replace(source_prefix,str(rtl))
    cfg=re.sub(r'^export SDC_FILE\s*=.*$', 'export SDC_FILE = '+str(constraints/'constraint.sdc'),cfg,flags=re.M)
    (constraints/'config.mk').write_text(cfg);shutil.copy2(run/'constraint.sdc',constraints/'constraint.sdc')
    backend=project/'backend/RUN_IMPORTED_A';results=backend/'results';results.mkdir(parents=True)
    for f in run.iterdir():
        if f.is_file():shutil.copy2(f,results/f.name)
    logs=list((run/'logs').glob('sky130hd/*/base'))
    if len(logs)!=1:raise ValueError('ambiguous original ORFS logs')
    shutil.copytree(logs[0],backend/'logs');shutil.copy2(run/'flow.log',backend/'flow.log')
    reports=project/'reports';reports.mkdir()
    subprocess.run([sys.executable,str(repo/'r2g-skills/signoff-loop/scripts/extract/extract_ppa.py'),str(project),str(reports/'ppa.json')],check=True)
    (reports/'drc.json').write_text(json.dumps(dict(status='clean',total_violations=0,report_file=str(results/'6_drc.lyrdb')))+'\n')
    (reports/'lvs.json').write_text(json.dumps(dict(status='fail',raw_final_status='pin_mismatch',tool='netgen',mismatch_class='top_pin_mismatch',report_file=str(results/'netgen_lvs.rpt')))+'\n')
    provenance=dict(original_run=str(run),original_control_receipt=receipt,source_repository=family,
        source_sha256=source_hashes,source_files=files,
        config_path_relocation_only=True,original_config_sha256=sha(run/'config.mk'),
        sdc_sha256=sha(constraints/'constraint.sdc'),strict_signoff=check,
        scope='real completed A run adapted for recipe planning; no new execution or A/B verdict')
    (project/'source_provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
    print(json.dumps(dict(project=str(project),family=family,case=design)))


if __name__=='__main__':main()
