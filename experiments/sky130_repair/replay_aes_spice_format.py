#!/usr/bin/env python3
"""Compare original and corrected formatting on the same AES extraction."""
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
from replay_spice_line_format import logical


def main():
    root=Path('/tmp/sky130_aux208');lock=(root/'review_worker.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    project=root/'results_recheck/lvs_projects/C01641__aes_top__aes-128-encryption-core__sky130hd__p5'
    lvs=project/'lvs';out=project/'format_replay';out.mkdir(exist_ok=False)
    original=(lvs/'extracted.spice').read_text();old_script=root/'environment/r2g/signoff-loop/scripts/flow/normalize_diode_spice.py'
    if hashlib.sha256(old_script.read_bytes()).hexdigest()!='479131edc4280bb3cdd69b20fe112eeff8a527fef63e6a45e4b5defc4d25cd3b':raise ValueError('wrong frozen normalizer')
    old_net=out/'old_collapsed.spice';old_net.write_text(original)
    subprocess.run([sys.executable,str(old_script),str(old_net)],check=True,capture_output=True)
    if logical(old_net.read_text())!=logical(original):raise ValueError('formatting comparison changed circuit tokens')
    lines=original.splitlines(keepends=True);start=next(i for i,l in enumerate(lines) if l.startswith('.subckt aes_top '))
    first=next(i for i in range(start+1,len(lines)) if lines[i].startswith('X'))
    end=first+1
    while end<len(lines) and lines[end].startswith('+'):end+=1
    deleted=''.join(lines[first:end]);negative=''.join(lines[:first]+lines[end:])
    tcl=(lvs/'run_netgen_lvs.tcl').read_text();results=[]
    for variant,text in [('old_collapsed',old_net.read_text()),('preserved',original),('negative_missing_device',negative)]:
        net=out/(variant+'.spice');script=out/(variant+'.tcl');report=out/(variant+'.rpt');net.write_text(text)
        script.write_text(tcl.replace(str(lvs/'extracted.spice'),str(net)).replace(str(lvs/'netgen_lvs.rpt'),str(report)))
        conda='/proj/workarea/yangao/miniconda3/envs/eda'
        args=translated(['run','--rm','-v',str(project)+':'+str(project),'-v',conda+':'+conda+':ro',
            '-e','MAGIC_EXT_USE_GDS=1','r2g-orfs:26Q3',conda+'/bin/netgen','-batch','source',str(script)],root,'aesformat_'+str(os.getpid()))
        with (out/(variant+'.log')).open('w') as f:r=subprocess.run(['docker',*args],stdout=f,stderr=subprocess.STDOUT,timeout=600)
        results.append(dict(variant=variant,returncode=r.returncode,final_verdict=final_lvs(report.read_text() if report.exists() else ''),
                            spice_sha256=hashlib.sha256(net.read_bytes()).hexdigest(),longest_line=max(map(len,text.splitlines()))))
        print(json.dumps(results[-1]),flush=True)
    proof=dict(results=results,formatting_logical_tokens_identical=True,deleted_negative_device=deleted,
               unchanged_verilog_sha256=hashlib.sha256((lvs/'powered.v').read_bytes()).hexdigest(),
               scope='same extracted circuit, reference, Netgen and PDK; only physical SPICE line wrapping differs in A/B')
    (out/'evidence.json').write_text(json.dumps(proof,indent=2)+'\n')


if __name__=='__main__':main()
