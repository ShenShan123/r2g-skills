"""Validate the 203 CPU runtime and real model/tool paths before starting the study."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

from campaign import ARMS, config_for
from runner_native import run_native
from run_batch import run_container


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    a = ap.parse_args(); root = a.root.resolve(); image_root = root/'image/rootfs'
    smoke = root/'smoke/migration'; smoke.mkdir(parents=True)
    probe = '''import os,socket,json,errno
from pathlib import Path
import torch,torch_geometric
assert not Path('/r2g/def-graph').exists() and not Path('/agent/r2g_agent').exists()
assert socket.if_nameindex()==[(1,'lo')],socket.if_nameindex()
assert not list(Path('/dev').glob('nvidia*'))
try:
 Path('/etc/exp4-write-test').write_text('x')
except OSError as e: assert e.errno in (errno.EROFS,errno.EACCES)
else: raise AssertionError('root filesystem was writable')
Path('/tmp/write-test').write_text('ok')
caps=next(x for x in Path('/proc/self/status').read_text().splitlines() if x.startswith('CapEff:'))
assert int(caps.split()[1],16)==0,caps
print(json.dumps({'torch':torch.__version__,'pyg':torch_geometric.__version__,'cpus':sorted(os.sched_getaffinity(0)),'network':socket.if_nameindex(),'gpu_exposed':False,'read_only_root':True}))
'''
    rc=run_container([],['python3','-c',probe],smoke/'isolation.log','pinned',
                     image_root=image_root,cpuset='16,17,18,19,64,65,66,67')
    if rc:raise RuntimeError((smoke/'isolation.log').read_text())
    task=smoke/'task.txt'
    task.write_text("This is only an infrastructure smoke test, not dataset production. Use your shell tool to create /work/converter/run.sh containing '#!/bin/bash' and 'echo EXP4_CPU_SMOKE_OK', then execute it and reply OK. Do not inspect design inputs or run a conversion. The dummy converter is only for verifying the harness delivery path.\n")
    def check(arm):
        i=ARMS.index(arm)
        cfg=config_for(root,{'arm':arm,'repeat':0,'cpuset':','.join(map(str,range(16+i*4,20+i*4)))},'pinned')
        cfg.update(image_root=str(image_root),task_file=str(task),out=str(smoke/arm))
        cfg['caps'].update(model_calls=8,wall_seconds=180,cpu_count=4)
        result=run_native(cfg)
        row={k:result.get(k) for k in ['arm','exit_code','stop_reason','successful_model_calls','usage_complete','cpu_affinity','harness_version','wall_seconds']}
        print(json.dumps(row),flush=True)
        return row
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows=list(pool.map(check,ARMS))
    (smoke/'summary.json').write_text(json.dumps(rows,indent=2))
    assert all(r['exit_code']==0 and r['successful_model_calls']>0 and r['usage_complete'] for r in rows),rows


if __name__=='__main__':
    main()
