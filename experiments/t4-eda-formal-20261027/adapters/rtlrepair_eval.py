"""Fresh, sandboxed RTLRepair verification, never compilation-as-success.

Output-oracle cases use unmodified upstream testbenches and strict complete-row
comparison (expected X remains a don't-care). Trace-only cases are retained as
unqualified until a trace-semantics adapter passes differential qualification.
No reference RTL, oracle contents or previous result enters an Agent's workspace.
"""
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

from tool_sandbox import command


def record(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.'+str(os.getpid())+'.tmp')
    tmp.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n');tmp.replace(path)


def compare_oracle(expected,actual):
    parse=lambda text:[[s.strip().strip('"').lower() for s in row] for row in csv.reader(text.splitlines()) if row]
    e,a=parse(expected),parse(actual)
    if len(e)<2 or len(a)<2 or not e[0] or e[0]!=a[0]:
        return {'status':'UNKNOWN','reason':'missing or incompatible oracle output/header','checks':0}
    if any(len(row)!=len(e[0]) for row in e[1:]+a[1:]):
        return {'status':'UNKNOWN','reason':'truncated or malformed output rows','checks':0}
    if len(e)!=len(a):
        return {'status':'FAIL','reason':'output row count differs','checks':0}
    start=1 if e[0][0]=='time' else 0
    checked=errors=0;first=[]
    for cycle,(ee,aa) in enumerate(zip(e[1:],a[1:])):
        for field,(wanted,got) in enumerate(zip(ee[start:],aa[start:]),start=start):
            if wanted=='x':continue
            checked+=1
            if wanted!=got:
                errors+=1
                if len(first)<5:first.append(f'{e[0][field]}@{cycle}: got {got}, expected {wanted}')
    if not checked:return {'status':'UNKNOWN','reason':'no measured output checks','checks':0}
    return {'status':'FAIL' if errors else 'PASS','checks':checked,'errors':errors,'first_failures':first}


def run_tool(tool,args,cwd,timeout,log):
    start=time.monotonic();cpu=time.process_time()
    try:
        result=subprocess.run(command(tool,args,cwd),capture_output=True,text=True,timeout=timeout)
        data={'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr,
              'timeout':False,'wall_s':time.monotonic()-start,'controller_cpu_s':time.process_time()-cpu}
    except subprocess.TimeoutExpired as exc:
        data={'returncode':None,'stdout':str(exc.stdout or ''),'stderr':str(exc.stderr or ''),
              'timeout':True,'wall_s':time.monotonic()-start,'controller_cpu_s':time.process_time()-cpu}
    record(log,data)
    return data


def copy_inputs(task,folder,candidate):
    folder.mkdir(parents=True,exist_ok=False)
    # No whole upstream directory is mounted: it also contains golden and other
    # buggy implementations. Copy only declared unchanged support and headers.
    files=[]
    for i,source in enumerate(task['sources']):
        if source==task['reference']:continue
        target=folder/f'support_{i:03d}.v';shutil.copy2(source,target);files.append(target.name)
    for p in Path(task['include_dir']).rglob('*.vh'):
        relative=p.relative_to(task['include_dir']);target=folder/relative
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)
    (folder/'candidate.v').write_text(candidate);files.append('candidate.v')
    return files


def evaluate(task,candidate,out,*,compile_only=False):
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    started=time.monotonic()
    checks=[];tool_calls=0
    if compile_only:
        work=out/'compile';files=copy_inputs(task,work,candidate)
        args=['-g2012','-I','.','-o','check.vvp',*files]
        if task.get('top'):args[1:1]=['-s',task['top']]
        result=run_tool('iverilog',args,work,120,out/'compile-tool.json');tool_calls+=1
        status='UNKNOWN' if result['timeout'] else 'PASS' if result['returncode']==0 else 'FAIL'
        checks=[{'status':status,'checks':1,'errors':0 if status=='PASS' else 1,
                 'compiler_log':result['stderr']}]
    else:
        oracles=[t for t in task['tests'] if t['kind']=='output_oracle']
        if not oracles:
            checks=[{'status':'UNKNOWN','reason':'trace-only verifier not yet qualified','checks':0}]
        for i,test in enumerate(oracles):
            work=out/f'test-{i:02d}';files=copy_inputs(task,work,candidate)
            for j,source in enumerate(test['sources']):
                name=f'test_{j:02d}.v';shutil.copy2(source,work/name);files.append(name)
            for source in test['init_files']:shutil.copy2(source,work/Path(source).name)
            verilator=test.get('simulator')=='verilator'
            compiler='verilator' if verilator else 'iverilog'
            args=(['--binary','--timing','--sv','-Wno-fatal','--top-module','tb',
                   '--x-initial','0','--x-assign','0','-I.','-o','simv',*files] if verilator
                  else ['-g2012','-I','.','-o','sim.vvp',*files])
            compiled=run_tool(compiler,args,work,120,out/f'compile-{i:02d}.json');tool_calls+=1
            if compiled['timeout']:
                checks.append({'status':'UNKNOWN','reason':'compile safety timeout','checks':0});continue
            if compiled['returncode']!=0:
                checks.append({'status':'FAIL','reason':'candidate does not compile with testbench',
                               'checks':0,'compiler_log':compiled['stderr']});continue
            simulated=run_tool(str((work/'obj_dir/simv').resolve()) if verilator else 'vvp',
                              [] if verilator else ['-n','sim.vvp'],work,float(test['timeout']),out/f'sim-{i:02d}.json');tool_calls+=1
            output=work/test['output']
            if simulated['timeout'] or simulated['returncode']!=0 or not output.is_file():
                checks.append({'status':'UNKNOWN','reason':'simulation timeout/error or no oracle output','checks':0});continue
            checks.append({'test':test['name'],**compare_oracle(Path(test['oracle']).read_text(),output.read_text())})
    status='UNKNOWN' if any(c['status']=='UNKNOWN' for c in checks) else 'PASS' if all(c['status']=='PASS' for c in checks) else 'FAIL'
    result={'status':status,'checks':checks,'tool_calls':tool_calls,'wall_s':time.monotonic()-started,
        'candidate_sha256':hashlib.sha256(candidate.encode()).hexdigest(),
        'scope':'compile_only' if compile_only else 'RTLRepair upstream output oracles (Icarus) or explicitly qualified zero-initial CSV replay (Verilator); not official VCS/gate-level reproduction'}
    record(out/'grade.json',result)
    return result
