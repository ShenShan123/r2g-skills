#!/usr/bin/env python3
"""Bounded-resource independent frontend proofs; never changes campaign inputs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shlex
import shutil
import subprocess
import time

HERE=Path(__file__).resolve().parent


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--case',choices=('C03963','C03965'),required=True)
    parser.add_argument('--method',choices=('simple-first','induction-first'),default='simple-first')
    parser.add_argument('--cpu-seconds',type=int,choices=(90,300),default=90,
                        help='One CPU, at most 4 GiB for native proof; retain longer attempts separately')
    a=parser.parse_args()
    root=HERE/'semantic_evidence/fpu_original_review';out=root/('independent_'+a.case.lower()+('_induction_first' if a.method=='induction-first' else ''))
    if a.cpu_seconds!=90:out=out.with_name(out.name+'_'+str(a.cpu_seconds)+'s')
    witness=json.loads((HERE/'fpu_package_order_evidence.json').read_text());case=witness['cases'][a.case]
    source_root=root/'package/sources'/witness['repository']
    for rel,digest in case['source_sha256'].items():
        if sha(source_root/rel.removeprefix('rtl/'))!=digest:raise ValueError('original source changed')
    yosys=Path('/home/yangao/r2g_toolchain/OpenROAD-flow-scripts/tools/install/yosys/bin/yosys');sv2v=Path(shutil.which('sv2v'))
    if sha(yosys)!=witness['local_yosys_sha256']:raise ValueError('unexpected native compiler')
    if sha(sv2v)!='b80a8ac6212aa72a196d9d6129cda35f16dbfa799ec61848c46321f845b6b635':raise ValueError('unexpected independent compiler')
    lines=(root/(a.case+'_packages_first_defer.ys')).read_text().splitlines();read=lines[0];top=lines[1].split()[-1]
    args=[x for x in shlex.split(read)[1:] if x not in ('-defer','-sv')]
    expected=[str(source_root/p.removeprefix('rtl/')) for p in case['ordered_sources']]
    if [x for x in args if not x.startswith('-')]!=expected:raise ValueError('proof source order differs from candidate')
    out.mkdir(exist_ok=False)
    cpu=min(os.sched_getaffinity(0));env=dict(os.environ,OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1')
    def run(command,log,stdout=None,native=False):
        def limits():
            os.sched_setaffinity(0,{cpu});resource.setrlimit(resource.RLIMIT_CPU,(a.cpu_seconds,a.cpu_seconds))
            if native:resource.setrlimit(resource.RLIMIT_AS,(4*1024**3,4*1024**3))
        start=time.monotonic()
        with log.open('w') as err:
            dest=stdout.open('w') if stdout else err
            try:
                try:rc=subprocess.run(command,stdout=dest,stderr=err,env=env,preexec_fn=limits,timeout=a.cpu_seconds+5).returncode
                except subprocess.TimeoutExpired:rc=124
            finally:
                if stdout:dest.close()
        return dict(returncode=rc,seconds=round(time.monotonic()-start,3))
    translated=out/'sv2v.v';conversion=run([str(sv2v),'+RTS','-N1','-M1G','-RTS',*args],out/'sv2v.log',translated)
    report=dict(case=a.case,conversion=conversion,original_source_sha256=case['source_sha256'],physical_signoff=False,
                method='Independent SV2V/native Yosys, clk2fflogic, joint internal/output induction, no input/reset constraints',
                proof_order=a.method,cpu_seconds_limit=a.cpu_seconds,native_memory_gib=4,cores=1,
                yosys_sha256=sha(yosys),sv2v_sha256=sha(sv2v))
    if conversion['returncode']==0:
        prep='\nhierarchy -check -top '+top+'\nproc\nflatten\nopt\nmemory\nopt\nclk2fflogic\n'
        proof='equiv_induct -seq 4\nequiv_simple -seq 2\n' if a.method=='induction-first' else 'equiv_simple -seq 2\nequiv_induct -seq 4\n'
        script=read+prep+'rename '+top+' gold\ndesign -stash gold\nread_verilog '+str(translated)+prep+'rename '+top+' gate\ndesign -stash gate\ndesign -copy-from gold -as gold gold\ndesign -copy-from gate -as gate gate\nequiv_make gold gate equiv\nhierarchy -top equiv\n'+proof+'equiv_status -assert\n'
        path=out/'equiv.ys';path.write_text(script);report['positive']=run([str(yosys),'-s',str(path)],out/'equiv.log',native=True)
        log=(out/'equiv.log').read_text();match=re.search(r'Found (\d+) \$equiv cells.*?(\d+) are proven and (\d+) are unproven',log,re.S)
        report['positive']['equiv_summary']=list(map(int,match.groups())) if match else None
        if report['positive']['returncode']==0:
            text=translated.read_text();start=re.search(r'\bmodule\s+'+top+r'\b',text).start();end=text.index('endmodule',start)
            section=text[start:end];signal='result_o' if a.case=='C03963' else 'result_valid_o'
            pattern=r'(assign\s+'+signal+r'\s*=\s*)([^;]+);'
            mutated,n=re.subn(pattern,lambda m:m[1]+'('+m[2]+") ^ 1'b1;",section)
            if n!=1:raise ValueError('negative control must alter exactly one actual top output')
            negative=out/'negative.v';negative.write_text(text[:start]+mutated+text[end:])
            path=out/'equiv_negative.ys';path.write_text(script.replace(str(translated),str(negative)))
            report['negative']=run([str(yosys),'-s',str(path)],out/'equiv_negative.log',native=True)
            report['negative']['mutation']='top '+signal+' XOR 1'
    report['files']={p.name:sha(p) for p in out.iterdir() if p.is_file()}
    (out/'result.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k not in ('files','original_source_sha256')}),flush=True)


if __name__=='__main__':main()
