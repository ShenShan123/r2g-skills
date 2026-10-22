#!/usr/bin/env python3
"""Check the LVS frozen-input fix on a scratch copy; never rerun a repair.

No provider calls, no physical implementation. One native LVS invocation is
costed and all original episode bytes remain untouched. Not a paper result.
"""
import argparse
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import time

from t2_nb_firstlook import load,save,sha


def replay(source,destination):
    source,destination=Path(source).resolve(),Path(destination).resolve()
    if destination.exists():raise FileExistsError('No implicit diagnostic replay')
    before={str(p):sha(p) for p in source.rglob('*') if p.is_file() and '.orfs-work' not in p.parts}
    destination.mkdir(parents=True)
    for name in ('constraints','rtl','backend'):
        shutil.copytree(source/name,destination/name)
    (destination/'reports').mkdir()
    # Keep original immutable RTL/SDC/config paths as read-only dependencies.
    # Only workspace ownership moves. Rewriting artifact/config text would
    # change timestamps and no longer replay the recorded physical state.
    pointers=[destination/'backend/.r2g_signoff_run',*list((destination/'backend').glob('RUN_*/run-meta.json'))]
    for p in pointers:
        if p.exists():p.write_text(p.read_text().replace(str(source),str(destination)))
    env={**os.environ,'LVS_CRASH_RETRIES':'1','PYTHONDONTWRITEBYTECODE':'1'}
    script=Path(__file__).resolve().parents[2]/'r2g-skills/signoff-loop/scripts/flow/run_lvs.sh'
    start=time.time();cpu=resource.getrusage(resource.RUSAGE_CHILDREN)
    with (destination/'lvs-check.log').open('x') as output:
        result=subprocess.run(['bash',str(script),str(destination),'nangate45',source.name],
            env=env,stdout=output,stderr=subprocess.STDOUT,timeout=600)
    after=resource.getrusage(resource.RUSAGE_CHILDREN)
    for p,h in before.items():
        if sha(p)!=h:raise RuntimeError('Original evidence changed: '+p)
    run=next((destination/'backend').glob('RUN_*'))
    frozen=run/'results/6_final.gds'
    # The provenance sidecar records the actual input path, independent of
    # task-specific run-meta naming conventions.
    path=destination/'lvs/lvs_provenance.json'
    provenance=load(path) if path.exists() else {}
    staged=Path(provenance['gds_path']) if provenance.get('gds_path') else None
    receipt={'paper_data':False,'purpose':'native LVS immutable-input technical replay',
        'source':str(source),'source_files_unchanged':True,'exit_status':result.returncode,
        'eda_implementation_flows':0,'native_LVS_invocations':1,'model_requests':0,'tokens':0,
        'wall_s':time.time()-start,'child_cpu_s':after.ru_utime+after.ru_stime-cpu.ru_utime-cpu.ru_stime,
        'script_sha256':sha(script),'frozen_gds_sha256':sha(frozen),'staged_gds_sha256':sha(staged) if staged else None,
        'reported_gds_sha256':provenance.get('gds_sha256')}
    receipt['passed']=result.returncode==0 and len({receipt[k] for k in
        ('frozen_gds_sha256','staged_gds_sha256','reported_gds_sha256')})==1
    save(destination/'technical-receipt.json',receipt)
    print(json.dumps(receipt,indent=1))
    if not receipt['passed']:raise RuntimeError('Native immutable-input replay failed')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--destination',type=Path,required=True)
    a=p.parse_args();replay(a.source,a.destination)
