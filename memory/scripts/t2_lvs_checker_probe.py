#!/usr/bin/env python3
"""One explicit, separately costed native checker probe on preserved inputs.

Never changes an episode or counts as a repair result. No implicit retries.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import signal
import subprocess
import time


def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()


def probe(source, output, executable, timeout=600, negative_control=False):
    source,output,executable=map(lambda p:Path(p).resolve(),(source,output,executable))
    if output.exists():raise FileExistsError('Probe output must be new; no implicit retry')
    prov=json.loads((source/'lvs/lvs_provenance.json').read_text())
    gds=Path(prov['gds_path']);rule=Path(prov['rule_path'])
    if sha(gds)!=prov['gds_sha256'] or sha(rule)!=prov['rule_sha256']:
        raise ValueError('Recorded LVS inputs changed')
    cdl=list((source/'.orfs-work/objects').glob('*/*/*/6_final_concat.cdl'))
    if len(cdl)!=1:raise ValueError('Ambiguous schematic')
    inputs={str(p):sha(p) for p in (gds,rule,cdl[0],source/'episode.json',source/'reports/lvs.json')}
    output.mkdir(parents=True)
    for p,name in ((gds,'input.gds'),(rule,'deck.lylvs'),(cdl[0],'input.cdl')):
        shutil.copy2(p,output/name)
    if negative_control:
        # Explicit oracle check on the small accepted accelerator fixture only.
        # Same pin count, opposite logic. Never changes the source or a TEST task.
        path=output/'input.cdl';text=path.read_text()
        old='Xinput62 reg_ctrl[9] net61 VDD VSS BUF_X1'
        if text.count(old)!=1:raise ValueError('Negative-control fixture differs')
        path.write_text(text.replace(old,old.replace('BUF_X1','INV_X1'),1))
    cmd=[str(executable),'-b','-rd','in_gds='+str(output/'input.gds'),
         '-rd','cdl_file='+str(output/'input.cdl'),'-rd','report_file='+str(output/'result.lvsdb'),
         '-rd','target_netlist='+str(output/'extracted.cir'),'-r',str(output/'deck.lylvs')]
    start=time.time();cpu=resource.getrusage(resource.RUSAGE_CHILDREN)
    timed_out=False
    with (output/'checker.log').open('x') as log:
        process=subprocess.Popen(cmd,cwd=output,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        try:rc=process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out=True;os.killpg(process.pid,signal.SIGTERM)
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()
            rc=124
    after=resource.getrusage(resource.RUSAGE_CHILDREN)
    text=(output/'checker.log').read_text(errors='replace')
    crash=any(s in text for s in ('Signal number','Internal error','net2id.end',
                                'compare raised exception','report_lvs failed'))
    verdict='UNKNOWN'
    if rc==0 and not crash and not timed_out and (output/'result.lvsdb').is_file():
        if 'CONGRATULATIONS! Netlists match.' in text:verdict='MATCH'
        elif "ERROR : Netlists don't match" in text:verdict='MISMATCH'
    unchanged=all(sha(p)==h for p,h in inputs.items())
    receipt={'source':str(source),'paper_data':False,'command':cmd,'executable_sha256':sha(executable),
        'inputs':inputs,'source_unchanged':unchanged,'exit_status':rc,'crash':crash,'timed_out':timed_out,
        'verdict':verdict,'native_checker_invocations':1,'implicit_retries':0,'implementation_flows':0,
        'model_requests':0,'tokens':0,'wall_s':time.time()-start,
        'cpu_s':after.ru_utime+after.ru_stime-cpu.ru_utime-cpu.ru_stime,
        'negative_control':negative_control,
        'copied_schematic_sha256':sha(output/'input.cdl'),
        'passed':unchanged and (verdict=='MISMATCH' if negative_control else verdict!='UNKNOWN')}
    (output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt,indent=2))
    return 0 if receipt['passed'] else 1


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    for key in ('source','output','executable'):ap.add_argument('--'+key,type=Path,required=True)
    ap.add_argument('--timeout',type=int,default=600)
    ap.add_argument('--negative-control',action='store_true');a=ap.parse_args()
    raise SystemExit(probe(a.source,a.output,a.executable,a.timeout,a.negative_control))
