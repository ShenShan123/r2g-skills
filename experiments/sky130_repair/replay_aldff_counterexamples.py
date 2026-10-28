#!/usr/bin/env python3
"""Rebuild, rather than trust saved simulation output, both semantic rejections."""
import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--yosys',type=Path,default=Path('/home/yangao/r2g_toolchain/OpenROAD-flow-scripts/tools/install/yosys/bin/yosys'))
    parser.add_argument('--iverilog',default='/opt/OpenROAD/oss-cad-suite/bin/iverilog')
    parser.add_argument('--vvp',default='/opt/OpenROAD/oss-cad-suite/bin/vvp')
    args=parser.parse_args()
    here=Path(__file__).resolve().parent; evidence=here/'semantic_evidence'
    hook=evidence/'aldff_to_dffsr_rejected_original.tcl'
    simcells=args.yosys.resolve().parent.parent/'share/yosys/simcells.v'
    cases=[('c01169','debouncer',['debouncer.v']),
           ('c03509','spi_core',['spi_core.v','APBSlaveI.v','BaudRateGen.v','ShiftReg.v','Slave_select.v'])]
    results=[]
    with tempfile.TemporaryDirectory(prefix='sky130-aldff-review-') as tmp:
        for case,top,names in cases:
            work=Path(tmp)/case;work.mkdir()
            for name in names+['tb.v']:shutil.copy2(evidence/(case+'_'+name),work/name)
            script=work/'convert.tcl'
            script.write_text('yosys read_verilog '+' '.join('{'+str(work/n)+'}' for n in names)+'\n'
                +'yosys hierarchy -check -top '+top+'\nyosys flatten\nsource {'+str(hook)+'}\n'
                +'yosys rename '+top+' '+top+'_repaired\nyosys select '+top+'_repaired\n'
                +'yosys write_verilog -selected -noattr {'+str(work/'repaired.v')+'}\n')
            with (work/'yosys.log').open('w') as log:
                subprocess.run([str(args.yosys),'-T','-c',str(script)],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=60)
            subprocess.run([args.iverilog,'-s','tb','-o',str(work/'sim'),
                *[str(work/n) for n in names],str(work/'repaired.v'),str(simcells),str(work/'tb.v')],check=True,capture_output=True,timeout=60)
            log=subprocess.check_output([args.vvp,str(work/'sim')],text=True,timeout=20)
            if 'COUNTEREXAMPLE:' not in log:raise RuntimeError(case+' no longer reproduces: '+log)
            results.append(dict(design=case,semantic_status='rejected_counterexample',simulation=log))
    print(json.dumps(results,indent=2))


if __name__=='__main__':main()
