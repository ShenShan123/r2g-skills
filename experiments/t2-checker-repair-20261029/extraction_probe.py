"""Experimental Netgen comparison of the SAME fully extracted MOS netlists.

Not an acceptance fallback: controls and defect counterexamples must pass first.
Nothing in the extraction/connectivity prefix is removed or black-boxed.
"""
import json
import os
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET

import checker_probe as P


def prepare_deck(source, dest):
    tree=ET.parse(source); text=tree.getroot().find('text')
    prefix,tail=text.text.split('compare_result = false',1)
    if 'compare_result = false' in tail:raise ValueError('Ambiguous comparison boundary')
    text.text=prefix+'''
# Export complete, independently simplified layout and schematic netlists.
# Keep all MOS classes, terminal connectivity, W/L and source/drain properties.
writer = RBA::NetlistSpiceWriter.new
writer.use_net_names = true
writer.with_comments = true
netlist.write($export_layout, writer)
schematic.write($export_schematic, writer)
puts "EXTRACTION_COMPLETE_NO_COMPARE"
'''
    tree.write(dest,encoding='utf-8',xml_declaration=True)


def main(ident='C02982-lvs-new'):
    manifest=json.loads((P.HERE/'plan-v2.json').read_text());job=next(j for j in manifest['jobs'] if j['id']==ident)
    P.check_hashes(job['pins']);out=P.HERE/'extraction-probes'/ident;P.new_directory(out)
    inputs={}
    for key,value in job['inputs'].items():
        p=out/Path(value).name;shutil.copyfile(value,p);inputs[key]=p
    deck=out/'extract_only.lylvs';prepare_deck(inputs['deck'],deck)
    layout=out/'layout.cir';schematic=out/'schematic.cir'
    command=[str(P.NEW),'-zz','-rd','in_gds='+str(inputs['gds']),'-rd','cdl_file='+str(inputs['cdl']),
             '-rd','report_file='+str(out/'extraction.lvsdb'),'-rd','target_netlist='+str(out/'automatic.cir'),
             '-rd','export_layout='+str(layout),'-rd','export_schematic='+str(schematic),'-r',str(deck)]
    pins={str(p):P.sha(p) for p in (*inputs.values(),deck,P.HERE/'netgen_control.tcl',P.HERE/'extraction_probe.py')}
    timing=P.bound_run(command,out,out/'extraction.log',1800)
    receipt={'paper_data':False,'status':'UNKNOWN','extraction':timing,'pins':pins,'source_job':job,'model_requests':0,'eda_flows':0,
             'cannot_be_used_as_formal_acceptance':True}
    log=(out/'extraction.log').read_text(errors='replace')
    if timing['exit_code']==0 and 'EXTRACTION_COMPLETE_NO_COMPARE' in log and layout.exists() and schematic.exists() and not P.CRASH.search(log):
        # Determine top from the recorded RTL project config, not a model hint.
        raw=Path(job['inputs']['cdl']).read_text(); tops=re.findall(r'(?im)^\.SUBCKT\s+(\S+)',raw)
        if not tops:raise ValueError('Reference has no top subcircuit')
        os.environ.update(PROBE_EXTRACTED=str(layout),PROBE_REFERENCE=str(schematic),PROBE_TOP=tops[0],PROBE_REPORT=str(out/'netgen.rpt'))
        result=P.bound_run([str(P.TOOLS/'netgen-1.5-current/bin/netgen'),'-batch','source',str(P.HERE/'netgen_control.tcl')],out,out/'netgen.log',600)
        receipt['comparison']=result
        receipt['comparison_log_tail']=(out/'netgen.log').read_text(errors='replace')[-1800:]
        receipt['status']='DIAGNOSTIC_COMPLETED_NOT_ADMITTED'
    P.check_hashes(pins);P.check_hashes(job['pins']);P.atomic_json(out/'receipt.json',receipt)
    print(json.dumps({'job':ident,'status':receipt['status'],'comparison_log_tail':receipt.get('comparison_log_tail')}),flush=True)


if __name__=='__main__':
    import sys
    main(sys.argv[1] if len(sys.argv)>1 else 'C02982-lvs-new')
