"""Read-only placed-DB audit: each witnessed output gets the intended identity buffer."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

HERE=Path(__file__).resolve().parent
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()


def audit(trials):
    trials=Path(trials);plan=json.loads((trials/'plan.json').read_text())
    if plan['key']['strategy']!='lvs_port_feedthrough_buffer' or len(plan['arms'])!=1:raise ValueError('not a single port-buffer replay')
    arm=plan['arms'][0];project=Path(arm['project']);execution=json.loads((project/'execution.json').read_text());work=Path(execution['work'])
    definitions=list(Path(arm['subject']).glob('backend/RUN_FROZEN*/results/6_final.def'))
    if len(definitions)!=1:raise ValueError('ambiguous original topology')
    body=definitions[0].read_text();section=re.search(r'\bPINS\s+(\d+)\s*;(.*?)END PINS',body,re.S)
    if section is None:raise ValueError('missing original port section')
    groups={};expected={}
    literal=lambda name:re.sub(r'\\(.)',r'\1',name)
    for m in re.finditer(r'-\s+(\S+)\s+\+ NET\s+(\S+)(.*?);',section[2],re.S):
        name,net,attrs=m.groups();direction=re.search(r'\+ DIRECTION\s+(\w+)',attrs)[1]
        if re.search(r'\+ SPECIAL\b',attrs):continue
        groups.setdefault(net,[]).append((literal(name),direction))
    for ports in groups.values():
        if len(ports)<2:continue
        inputs=[name for name,direction in ports if direction=='INPUT']
        outputs=[name for name,direction in ports if direction=='OUTPUT']
        if len(inputs)!=1 or len(inputs)+len(outputs)!=len(ports):raise ValueError('unsupported original alias')
        for output in outputs:expected[output]=inputs[0]
    if not expected:raise ValueError('no original feedthroughs')
    databases=list(work.glob('results/sky130hd/*/base/3_place.odb'))
    if len(databases)!=1:raise ValueError('placed database unavailable or ambiguous')
    odb=databases[0];before=sha(odb);tool=Path('/home/yangao/r2g_toolchain/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad')
    if sha(tool)!='6454121a552fc8625fa27f474dfc699f2d82a10a8959238168f2c2249988fd48':raise ValueError('frozen tool changed')
    if any(c in str(odb.resolve()) for c in '{}\\\n\r'):raise ValueError('unsupported Tcl path')
    tcl='''set_thread_count 1
read_db {ODB_PATH}
set block [ord::get_db_block]
foreach inst [$block getInsts] {
  if {![regexp {^fdbuf_[0-9]+$} [$inst getName]]} {continue}
  if {[[$inst getMaster] getName] ne "sky130_fd_sc_hd__buf_4"} {error "unexpected feedthrough master"}
  set inputs {}; set outputs {}
  foreach bt [[[$inst findITerm A] getNet] getBTerms] {
    if {[$bt getIoType] ne "INPUT"} {error "source net retains a non-input top port"}
    lappend inputs [$bt getName]
  }
  foreach bt [[[$inst findITerm X] getNet] getBTerms] {
    if {[$bt getIoType] ne "OUTPUT"} {error "output net has a non-output top port"}
    lappend outputs [$bt getName]
  }
  if {[llength $inputs]!=1 || [llength $outputs]!=1} {error "unexpected buffered port fanout"}
  puts "FBUF_MAP\t[lindex $outputs 0]\t[lindex $inputs 0]"
}
exit
'''.replace('ODB_PATH',str(odb.resolve()))
    with tempfile.TemporaryDirectory(prefix='port_effect_audit_') as tmp:
        script=Path(tmp)/'read.tcl';script.write_text(tcl)
        process=subprocess.run([str(tool),'-exit',str(script)],text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=30)
    if process.returncode:raise ValueError(process.stdout[-2000:])
    pairs=[line.split('\t')[1:] for line in process.stdout.splitlines() if line.startswith('FBUF_MAP\t')]
    if len({p[0] for p in pairs})!=len(pairs) or dict(pairs)!=expected:raise ValueError('actual buffered connections differ from witnessed original port mapping')
    if sha(odb)!=before:raise ValueError('source ODB changed during read-only audit')
    return dict(case=arm['case'],matched_output_buffers=len(expected),mapping=expected,
                original_def_sha256=sha(definitions[0]),placed_odb=str(odb),placed_odb_sha256=before,
                openroad_sha256=sha(tool),read_only=True,
                scope='Actual placed connectivity and standard identity-buffer master checked; final DRC/LVS and timing remain separate.')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--trials',type=Path,required=True);args=parser.parse_args()
    result=audit(args.trials);cid=result['case'].split('__')[0].lower()
    (HERE/('port_'+cid+'_placed_connectivity_20261027.json')).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='mapping'}))
