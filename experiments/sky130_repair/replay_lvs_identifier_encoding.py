#!/usr/bin/env python3
"""Isolated alpha-renaming diagnostic for a Netgen escaped-port parser issue."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from aux208_container_adapter import translated
from strict_signoff import final_lvs


def main():
    p=argparse.ArgumentParser();p.add_argument('project',type=Path);a=p.parse_args()
    root=Path('/tmp/sky130_aux208');project=a.project.resolve()
    if not project.is_relative_to(root):raise ValueError('project escapes diagnostic root')
    lock=(root/'worker.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    src=project/'lvs';out=project/'identifier_encoding';out.mkdir(exist_ok=False)
    spice=(src/'extracted.spice').read_text();verilog=(src/'powered.v').read_text()
    tcl=(src/'run_netgen_lvs.tcl').read_text()
    old='cfgfb[-1]';encoded='r2g_lvs_ident_'+hashlib.sha256(old.encode()).hexdigest()[:20]
    assert encoded.lower() not in (spice+verilog).lower()
    pattern=re.compile(r'(?<!\S)'+re.escape(old)+r'(?!\S)')
    vpattern=re.compile(re.escape('\\'+old)+r'(?=\s)')
    s,scount=pattern.subn(encoded,spice);v,vcount=vpattern.subn(encoded,verilog)
    assert scount==1 and vcount==2, 'this probe is scoped to the observed unused input port'
    # Exact inverse establishes that no node, cell, expression or connection
    # changed, and that the mapping is bijective in both representations.
    assert s.replace(encoded,old)==spice and v.replace(encoded,'\\'+old)==verilog
    (out/'encoded.v').write_text(v);results=[]
    for variant in ('encoded','negative_missing_layout_port'):
        net=out/(variant+'.spice');script=out/(variant+'.tcl');report=out/(variant+'.rpt')
        content=s if variant=='encoded' else s.replace(encoded,'r2g_intentionally_wrong_port')
        net.write_text(content)
        script.write_text(tcl.replace(str(src/'extracted.spice'),str(net))
                          .replace(str(src/'powered.v'),str(out/'encoded.v'))
                          .replace(str(src/'netgen_lvs.rpt'),str(report)))
        conda='/proj/workarea/yangao/miniconda3/envs/eda'
        args=translated(['run','--rm','-v',str(project)+':'+str(project),'-v',conda+':'+conda+':ro',
                        '-e','MAGIC_EXT_USE_GDS=1','r2g-orfs:26Q3',conda+'/bin/netgen','-batch','source',str(script)],root,'identifier_'+str(os.getpid()))
        with (out/(variant+'.log')).open('w') as f:
            run=subprocess.run(['docker',*args],stdout=f,stderr=subprocess.STDOUT,timeout=180)
        results.append(dict(variant=variant,returncode=run.returncode,final_verdict=final_lvs(report.read_text())))
    evidence=dict(mapping={old:encoded},layout_occurrences=scount,verilog_occurrences=vcount,
                  exact_inverse_verified=True,scope='same Netgen, PDK, comparison deck; alpha-renaming only',
                  original_sha256={n:hashlib.sha256((src/n).read_bytes()).hexdigest() for n in ('extracted.spice','powered.v')},results=results)
    (out/'evidence.json').write_text(json.dumps(evidence,indent=2)+'\n');print(json.dumps(evidence))


if __name__=='__main__':main()
