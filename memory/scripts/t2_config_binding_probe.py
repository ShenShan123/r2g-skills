#!/usr/bin/env python3
"""Replay a rejected config through native Make, without running a flow."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def probe(source, candidate, output, flow):
    if output.exists():raise FileExistsError('No overwriting a probe')
    inputs={str(p):sha(p) for p in (source/'constraints/config.mk',source/'manifest.json',candidate,source/'episode.json')}
    output.mkdir(parents=True)
    for name in ('rtl','constraints'):shutil.copytree(source/name,output/name)
    shutil.copy2(source/'manifest.json',output/'manifest.json')
    shutil.copy2(candidate,output/'constraints/config.mk')
    backend=output/'backend/RUN_probe';backend.mkdir(parents=True)
    resolver=Path(__file__).resolve().parents[2]/'r2g-skills/signoff-loop/scripts/flow/t2a_effective_config.py'
    env=dict(os.environ);manifest=json.loads((output/'manifest.json').read_text())
    for k in set(manifest['initial_config'])|set(manifest['allowlist'])|set(manifest['immutable']['knobs']):env.pop(k,None)
    env['R2G_TASK_MANIFEST']=str(output/'manifest.json')
    cmd=[sys.executable,str(resolver),'--project',str(output),'--backend',str(backend),
         '--flow',str(flow),'--design-config',str(output/'constraints/config.mk'),'--variant','config_probe']
    result=subprocess.run(cmd,env=env,cwd=flow,capture_output=True,text=True,timeout=90)
    (output/'probe.log').write_text(result.stdout+result.stderr)
    receipt=json.loads((backend/'effective-config.json').read_text())
    record={'paper_data':False,'implementation_flows':0,'model_requests':0,'tokens':0,
        'source_unchanged':all(sha(p)==h for p,h in inputs.items()),'source_hashes':inputs,
        'exit_status':result.returncode,'effective_config_receipt':str(backend/'effective-config.json'),
        'receipt_sha256':sha(backend/'effective-config.json'),'status':receipt['status'],
        'MAX_ROUTING_LAYER':receipt['effective'].get('MAX_ROUTING_LAYER'),
        'wall_s':receipt['wall_s'],'cpu_s':receipt['cpu_s']}
    (output/'receipt.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps(record,indent=2))
    return result.returncode if record['source_unchanged'] else 1


def audit_registration(registration, output, flow):
    """Input-resolution audit of the registered roster, no task execution."""
    if output.exists():raise FileExistsError('Audit directory must be new')
    output.mkdir(parents=True)
    resolver=Path(__file__).resolve().parents[2]/'r2g-skills/signoff-loop/scripts/flow/t2a_effective_config.py'
    records=[]
    for row in json.loads(registration.read_text())['tasks']:
        project=Path(row['template']);manifest=json.loads((project/'manifest.json').read_text())
        env=dict(os.environ)
        for k in set(manifest['initial_config'])|set(manifest['allowlist'])|set(manifest['immutable']['knobs']):env.pop(k,None)
        env['R2G_TASK_MANIFEST']=str(project/'manifest.json')
        backend=output/row['handoff_id'];backend.mkdir()
        p=subprocess.run([sys.executable,str(resolver),'--project',str(project),'--backend',str(backend),
            '--flow',str(flow),'--design-config',str(project/'constraints/config.mk'),'--variant',project.name],
            env=env,cwd=flow,capture_output=True,text=True,timeout=90)
        (backend/'probe.log').write_text(p.stdout+p.stderr)
        receipt=backend/'effective-config.json'
        records.append({'design':row['handoff_id'],'exit_status':p.returncode,
            'receipt':str(receipt),'sha256':sha(receipt) if receipt.exists() else None})
    passed=all(row['exit_status']==0 for row in records)
    result={'status':'PASS' if passed else 'FAIL','tasks':records,'registration_sha256':sha(registration),
            'implementation_flows':0,'model_requests':0,'tokens':0}
    (output/'receipt.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'status':result['status'],'designs':len(records)}))
    return 0 if passed else 1


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    for key in ('source','candidate','registration'):ap.add_argument('--'+key,type=Path)
    for key in ('output','flow'):ap.add_argument('--'+key,type=Path,required=True)
    a=ap.parse_args()
    if a.registration:raise SystemExit(audit_registration(a.registration,a.output,a.flow))
    if not a.source or not a.candidate:ap.error('source and candidate are required for a single probe')
    raise SystemExit(probe(a.source,a.candidate,a.output,a.flow))
