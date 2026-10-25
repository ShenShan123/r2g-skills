"""Execution-bound ORFS effective config receipt. No EDA is launched by the query."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import subprocess
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[4]/'memory'))
from tehm.policy import envelope as E


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify(manifest, project, executed, values, environment):
    config=E.effective_config((project/'constraints/config.mk').read_text())
    errors=[]
    # run_orfs adds these as later command-line overrides, outside Make's query.
    # Formal tasks have no fast mode; reject it rather than certify a different command.
    for key in ('PLACE_FAST','ROUTE_FAST','ROUTE_FAST_SKIP_DRT','ROUTE_FAST_DRT_ITERS'):
        if environment.get(key) not in (None,'','0') or config.get(key) not in (None,'','0'):
            errors.append({'kind':'unbound_fast_override','knob':key})
    for key,want in config.items():
        got=values.get(key)
        if not got or got['value'].strip()!=str(want).strip():
            errors.append({'kind':'execution_config_mismatch','knob':key,'configured':want,'effective':got})
    for key,row in values.items():
        if row['origin'].startswith('environment') and (key in manifest.get('allowlist',{}) or
                                                       key in manifest.get('immutable',{}).get('knobs',[])):
            errors.append({'kind':'execution_environment_override','knob':key,'effective':row})
    guard=E.check_config(manifest,config,env=environment,project=project)
    errors+=guard['violations']
    if digest(executed)!=digest(project/'constraints/config.mk'):
        errors.append({'kind':'executed_config_copy_changed'})
    return errors


def capture(project, backend, flow, design_config, variant):
    manifest=json.loads(Path(os.environ['R2G_TASK_MANIFEST']).read_text())
    keys=set(E.effective_config((project/'constraints/config.mk').read_text()))
    keys.update(manifest.get('allowlist',{}));keys.update(manifest.get('immutable',{}).get('knobs',[]))
    keys.update(('CORE_UTILIZATION','PLACE_DENSITY','PLACE_DENSITY_LB_ADDON','DIE_AREA','CORE_AREA'))
    if any(not re.fullmatch('[A-Z_][A-Z_0-9]*',k) for k in keys):raise ValueError('Invalid config key')
    start,cpu=time.time(),resource.getrusage(resource.RUSAGE_CHILDREN)
    cmd=['make','--no-print-directory','-f',str(flow/'Makefile'),'-f',str(Path(__file__).with_suffix('.mk')),
         'DESIGN_CONFIG='+str(design_config),'FLOW_VARIANT='+variant,'R2G_SNAPSHOT_KEYS='+' '.join(sorted(keys)),
         'r2g-effective-config']
    result=subprocess.run(cmd,capture_output=True,text=True,timeout=60)
    values={}
    for line in result.stdout.splitlines():
        if line.startswith('__R2G_CONFIG__'):
            key,origin,value=line[len('__R2G_CONFIG__'):].split('|',2)
            values[key]={'origin':origin,'value':value}
    errors=verify(manifest,project,design_config,values,os.environ) if result.returncode==0 else [
          {'kind':'make_resolution_failed','rc':result.returncode}]
    after=resource.getrusage(resource.RUSAGE_CHILDREN)
    receipt={'status':'FAIL' if errors else 'PASS','run_dir':str(backend),'errors':errors,'effective':values,
         'project_config_sha256':digest(project/'constraints/config.mk'),'executed_config_sha256':digest(design_config),
         'manifest_sha256':digest(os.environ['R2G_TASK_MANIFEST']),'makefile_sha256':digest(flow/'Makefile'),
         'resolver_sha256':digest(__file__),'fragment_sha256':digest(Path(__file__).with_suffix('.mk')),
         'command':cmd,'wall_s':time.time()-start,'cpu_s':after.ru_utime-cpu.ru_utime+after.ru_stime-cpu.ru_stime}
    (backend/'effective-config.json').write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n')
    (backend/'effective-config.log').write_text(result.stdout+result.stderr)
    return 76 if errors else 0


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    for key in ('project','backend','flow','design-config'):ap.add_argument('--'+key,type=Path,required=True)
    ap.add_argument('--variant',required=True)
    a=ap.parse_args()
    sys.exit(capture(a.project,a.backend,a.flow,a.design_config,a.variant))
