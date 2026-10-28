#!/usr/bin/env python3
"""Replay saved independent-frontend proofs and intentional negative controls."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

HERE=Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser()
    p.add_argument('case',choices=['C03962','C03986'])
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--yosys',default='/home/yangao/r2g_toolchain/OpenROAD-flow-scripts/tools/install/yosys/bin/yosys')
    p.add_argument('--slang-yosys',default='/opt/OpenROAD/oss-cad-suite/bin/yosys')
    p.add_argument('--sv2v',default='/opt/OpenROAD/2_convert_verilog_tool/sv2v-Linux/sv2v')
    a=p.parse_args();e=json.loads((HERE/(a.case.lower()+'_translation_evidence.json')).read_text())
    fixture=HERE/'semantic_evidence'/(a.case.lower()+'_translation')
    for rel,digest in e['sha256'].items():
        if sha(fixture/rel)!=digest:raise ValueError('evidence changed: '+rel)
    root=a.out.resolve()
    if any(c in str(root) for c in '{}\n\r'):raise ValueError('unsupported Tcl output path')
    root.mkdir(parents=True,exist_ok=False);shutil.copytree(fixture/'rtl',root/'rtl')
    for filename in ('slang.tcl',e['proof_script'],'equiv_negative.tcl'):
        (root/filename).write_text((fixture/filename).read_text().replace(e['original_workspace'],str(root)))
    def run(argv,log):
        with (root/log).open('w') as f:
            return subprocess.run(argv,stdout=f,stderr=subprocess.STDOUT,timeout=120).returncode
    if run([a.slang_yosys,'-T','-c',str(root/'slang.tcl')],'slang.log'):
        raise RuntimeError('Slang failed')
    cfg=(fixture/'config.mk').read_text()
    # The frozen configs contain one repository, no source-path spaces and no
    # parameter/define overrides. Fail if this fixture contract ever changes.
    if re.search(r'^export (VERILOG_DEFINES|SYNTH_PARAMETERS)\s*=',cfg,re.M):
        raise ValueError('unreviewed parameter or define override')
    sources=list(e['source_sha256'])
    includes=sorted({root/'rtl'} | {(root/f).parent for f in sources})
    with (root/'sv2v.v').open('w') as out,(root/'sv2v.log').open('w') as err:
        subprocess.run([a.sv2v,'-DSYNTHESIS',*['-I'+str(i) for i in includes],
                        *[str(root/f) for f in sources]],stdout=out,stderr=err,timeout=120,check=True)
    for filename in ('slang.v','sv2v.v'):
        if sha(root/filename)!=e['sha256'][filename]:raise ValueError('regenerated translation differs: '+filename)
    # The negative file contains exactly the archived intentional error. It is
    # never supplied to production synthesis or physical implementation.
    shutil.copy2(fixture/'negative.v',root/'negative.v')
    good=run([a.yosys,'-Q','-T','-c',str(root/e['proof_script'])],'proof.log')
    bad=run([a.yosys,'-Q','-T','-c',str(root/'equiv_negative.tcl')],'negative.log')
    total=e['results'][0]['equivalence_cells']
    if good or f'{total} are proven and 0 are unproven.' not in (root/'proof.log').read_text():
        raise RuntimeError('equivalence not proven')
    if bad==0 or 'ERROR: Found 1 unproven' not in (root/'negative.log').read_text():
        raise RuntimeError('negative control not rejected as expected')
    result=dict(case=a.case,proof_returncode=good,negative_returncode=bad,
                equivalence_cells=total,physical_signoff=False)
    (root/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':main()
