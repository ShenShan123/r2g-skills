#!/usr/bin/env python3
"""Probe a CLK/clk collision using distinct Magic nodes and invertible names."""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from aux208_container_adapter import translated
from strict_signoff import final_lvs


def main():
    root=Path('/tmp/sky130_aux208');project=root/'diagnostics/C03797_case_sensitive_ports/lvs_projects/C03797__Clockworks__gatemate-riscv__sky130hd__p5'
    lock=(root/'worker.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    src=project/'lvs';out=project/'case_encoding';out.mkdir(exist_ok=False)
    spec=importlib.util.spec_from_file_location('ext_ports',root/'environment/r2g/signoff-loop/scripts/flow/restore_ext_ports.py')
    ext=importlib.util.module_from_spec(spec);spec.loader.exec_module(ext)
    declared,uf=ext.parse_ext(str(src/'magic_ext/Clockworks.ext'))
    if not {'CLK','clk'}<=declared or uf.find('CLK')==uf.find('clk'):raise ValueError('Magic does not prove distinct original port nodes')
    spice=(src/'extracted.spice').read_text();verilog=(src/'powered.v').read_text()
    mapping={name:'r2g_lvs_ident_'+hashlib.sha256(name.encode()).hexdigest()[:20] for name in ('CLK','clk')}
    if any(n.lower() in (spice+verilog).lower() for n in mapping.values()):raise ValueError('name collision')
    lines=spice.splitlines(keepends=True);start=next(i for i,l in enumerate(lines) if l.startswith('.subckt Clockworks '));end=next(i for i in range(start+1,len(lines)) if lines[i].startswith('.ends'))
    body=re.sub(r'(?<!\S)(CLK|clk)(?!\S)',lambda m:mapping[m[0]],''.join(lines[start:end]))
    s=''.join(lines[:start])+body+''.join(lines[end:])
    # Named cell pins (.CLK(...)) are library interfaces, not top-level nets.
    # Preserve those while renaming the actual signal identifier arguments.
    v=re.sub(r'\b(CLK|clk)\b',lambda m:m[0] if verilog[:m.start()].rstrip().endswith('.') else mapping[m[0]],verilog)
    inverse_s=s;inverse_v=v
    for old,new in mapping.items():inverse_s=inverse_s.replace(new,old);inverse_v=inverse_v.replace(new,old)
    assert inverse_s==spice and inverse_v==verilog
    assert re.findall(r'\.\s*CLK\s*\(',v)==re.findall(r'\.\s*CLK\s*\(',verilog)
    (out/'encoded.v').write_text(v);tcl=(src/'run_netgen_lvs.tcl').read_text();results=[]
    for variant in ('encoded','negative_shorted_ports'):
        net=out/(variant+'.spice');script=out/(variant+'.tcl');report=out/(variant+'.rpt')
        net.write_text(s if variant=='encoded' else s.replace(mapping['clk'],mapping['CLK']))
        script.write_text(tcl.replace(str(src/'extracted.spice'),str(net)).replace(str(src/'powered.v'),str(out/'encoded.v')).replace(str(src/'netgen_lvs.rpt'),str(report)))
        conda='/proj/workarea/yangao/miniconda3/envs/eda'
        args=translated(['run','--rm','-v',str(project)+':'+str(project),'-v',conda+':'+conda+':ro','-e','MAGIC_EXT_USE_GDS=1','r2g-orfs:26Q3',conda+'/bin/netgen','-batch','source',str(script)],root,'portcase_'+str(os.getpid()))
        with (out/(variant+'.log')).open('w') as f:r=subprocess.run(['docker',*args],stdout=f,stderr=subprocess.STDOUT,timeout=180)
        results.append(dict(variant=variant,returncode=r.returncode,final_verdict=final_lvs(report.read_text())))
    evidence=dict(mapping=mapping,exact_inverse_verified=True,magic_port_nodes_distinct=True,library_pin_names_preserved=True,results=results)
    (out/'evidence.json').write_text(json.dumps(evidence,indent=2)+'\n');print(json.dumps(evidence))


if __name__=='__main__':main()
