#!/usr/bin/env python3
"""Diagnostic: same SPICE tokens, bounded physical lines; real Netgen control."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from aux208_container_adapter import translated
from strict_signoff import final_lvs


def logical(text):
    out=[]
    for line in text.splitlines():
        if line.startswith('+') and out:out[-1]+=' '+line[1:].strip()
        else:out.append(line)
    return [line.split() for line in out]


def wrap_headers(text, width=160):
    out=[]
    for line in text.splitlines():
        if line.lower().startswith('.subckt ') and len(line)>width:
            current=''
            for token in line.split():
                if current and len(current)+len(token)+1>width:
                    out.append(current);current='+'
                current+=(' ' if current else '')+token
            out.append(current)
        else:out.append(line)
    result='\n'.join(out)+'\n'
    if logical(result)!=logical(text):raise ValueError('formatting changed SPICE logical tokens')
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('project',type=Path);a=p.parse_args()
    root=Path('/tmp/sky130_aux208');project=a.project.resolve()
    if not project.is_relative_to(root):raise ValueError('project outside auxiliary root')
    lock=(root/'worker.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    lvs=project/'lvs';source=(lvs/'extracted.spice').read_text();wrapped=wrap_headers(source)
    out=project/'line_format';out.mkdir(exist_ok=True)
    tcl=(lvs/'run_netgen_lvs.tcl').read_text();results=[]
    for variant in ('wrapped','negative_missing_first_buffer'):
        netlist=out/(variant+'.spice');script=out/(variant+'.tcl');report=out/(variant+'.rpt')
        if report.exists():raise ValueError('diagnostic already exists')
        content=wrapped
        if variant.startswith('negative'):
            lines=content.splitlines();idx=next(i for i,line in enumerate(lines) if line.startswith('Xsky130_fd_sc_hd__buf_4_'))
            deleted=lines.pop(idx);content='\n'.join(lines)+'\n'
        netlist.write_text(content)
        script.write_text(tcl.replace(str(lvs/'extracted.spice'),str(netlist)).replace(str(lvs/'netgen_lvs.rpt'),str(report)))
        conda='/proj/workarea/yangao/miniconda3/envs/eda'
        args=translated(['run','--rm','-v',str(project)+':'+str(project),'-v',conda+':'+conda+':ro',
                         '-e','MAGIC_EXT_USE_GDS=1','r2g-orfs:26Q3',conda+'/bin/netgen','-batch','source',str(script)],
                        root,'spiceformat_'+str(os.getpid()))
        with (out/(variant+'.log')).open('w') as log:
            run=subprocess.run(['docker',*args],stdout=log,stderr=subprocess.STDOUT,timeout=180)
        text=report.read_text() if report.exists() else ''
        results.append(dict(variant=variant,returncode=run.returncode,final_verdict=final_lvs(text),
                            spice_sha256=hashlib.sha256(netlist.read_bytes()).hexdigest(),
                            logical_tokens_unchanged=variant=='wrapped',
                            deleted_instance=deleted if variant.startswith('negative') else None))
    evidence=dict(original_spice_sha256=hashlib.sha256(source.encode()).hexdigest(),
                  longest_original_line=max(map(len,source.splitlines())),results=results)
    (out/'line_format_evidence.json').write_text(json.dumps(evidence,indent=2)+'\n');print(json.dumps(evidence))


if __name__=='__main__':main()
