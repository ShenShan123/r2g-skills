#!/usr/bin/env python3
"""Replay original-RTL/Slang traffic comparison; this is not a formal proof."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess

HERE=Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True)
    p.add_argument('--yosys',default='/opt/OpenROAD/oss-cad-suite/bin/yosys')
    p.add_argument('--verilator',default='/opt/OpenROAD/oss-cad-suite/bin/verilator')
    a=p.parse_args();e=json.loads((HERE/'c03980_translation_evidence.json').read_text())
    fixture=HERE/'semantic_evidence/c03980_translation_review';root=a.out.resolve()
    if any(c in str(root) for c in '{}\r\n'):raise ValueError('unsupported Tcl output path')
    for rel,digest in {**e['source_sha256'],**e['sha256']}.items():
        if sha(fixture/rel)!=digest:raise ValueError('fixture changed: '+rel)
    root.mkdir(parents=True,exist_ok=False)
    shutil.copytree(fixture/'rtl',root/'rtl')
    for name in ('differential_tb.sv','slang_negative.v'):
        shutil.copy2(fixture/name,root/name)
    (root/'slang.tcl').write_text((fixture/'slang.tcl').read_text().replace(e['original_workspace'],str(root)))
    env=dict(os.environ)
    for key in ('PYTHONHOME','PYTHONPATH','PYTHONSAFEPATH'):env.pop(key,None)
    env.update(OMP_NUM_THREADS='1',MALLOC_ARENA_MAX='2')
    def no_core():resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    def run(argv,filename,timeout):
        with (root/filename).open('w') as out:
            return subprocess.run(argv,stdout=out,stderr=subprocess.STDOUT,
                                  env=env,timeout=timeout,preexec_fn=no_core).returncode
    if run([a.yosys,'-T','-c',str(root/'slang.tcl')],'slang.log',60):raise RuntimeError('Slang failed')
    if sha(root/'slang.v')!=e['sha256']['slang.v']:raise ValueError('actual translation differs')
    source=(root/'slang.v').read_text()
    if source.count('module system_top(')!=1:raise ValueError('unexpected top declaration')
    (root/'slang_sim.v').write_text(source.replace('module system_top(','module system_top_gate(',1))
    if sha(root/'slang_sim.v')!=e['sha256']['slang_sim.v']:raise ValueError('simulation wrapper differs')
    files=[root/f for f in e['source_sha256']]
    files.sort(key=lambda x:(0 if x.name=='pkg_defines.sv' else 1,str(x)))
    results=[]
    for negative in (False,True):
        label='negative' if negative else 'positive';build=root/(label+'_build')
        cmd=[a.verilator,'--binary','--timing','--top-module','tb','-Wno-fatal',
             '-DSYNTHESIS','--Mdir',str(build),'-j','2','-o','sim',
             str(root/'differential_tb.sv'),str(root/('slang_negative.v' if negative else 'slang_sim.v')),
             *map(str,files)]
        if run(cmd,label+'_build.log',180):raise RuntimeError(label+' compilation failed')
        rc=run([str(build/'sim')],label+'.log',60);text=(root/(label+'.log')).read_text()
        if negative:
            if rc==0 or 'DIFFERENCE t=484000' not in text:raise RuntimeError('active-data negative control was not detected')
        elif rc or 'PASS original RTL / Slang differential traffic simulation' not in text:
            raise RuntimeError('traffic comparison failed')
        if not negative:
            for key,value in e['coverage'].items():
                if f'{key}={value}' not in text:raise RuntimeError('traffic coverage changed: '+key)
        results.append(dict(case=label,returncode=rc))
    record=dict(results=results,coverage=e['coverage'],formal_equivalence=False,physical_signoff=False)
    (root/'result.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record))


if __name__=='__main__':main()
