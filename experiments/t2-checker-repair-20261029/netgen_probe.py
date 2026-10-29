"""Strict, diagnostic-only compare controls and fresh electrical counterexamples."""
import json
import os
from pathlib import Path
import re

import checker_probe as P


def strict_verdict(code, report, log):
    if code or re.search(r'(?:^|\n)(?:Error|ERROR):|invalid command name|can.t read ',log):
        return {'status':'UNKNOWN','reason':'comparison_execution_error'}
    finals=re.findall(r'Final result:\s*([^\n]+)',report)
    if len(finals)!=1:return {'status':'UNKNOWN','reason':'missing_unique_final_result'}
    if ('property errors' in report.lower() or 'failed pin matching' in finals[0].lower()
        or re.search(r'(?:do not match|not match|mismatch)',finals[0],re.I)):
        return {'status':'fail','reason':'measured_mismatch_or_parameter_error'}
    if finals[0].strip()=='Circuits match uniquely.' and 'PROBE_EQUIVALENT=1' in log:
        return {'status':'clean'}
    return {'status':'UNKNOWN','reason':'unrecognized_or_nonunique_comparison'}


def mutate(text, kind):
    if kind=='remove_instance':
        changed,n=re.subn(r'(?m)^X[^\n]+\n(?:\+[^\n]+\n)*','',text,count=1)
    elif kind=='change_width':
        # Change just one transistor width by 0.4 um, without touching connectivity.
        def replacement(m):return m[1]+format(float(m[2])+0.4,'.12g')+'U'
        changed,n=re.subn(r'(?im)(^M[^\n]*\bW=)([0-9.]+)U',replacement,text,count=1)
    else:raise ValueError(kind)
    if n!=1:raise ValueError('Expected exactly one mutation')
    return changed


def run(name, layout, reference, top):
    out=P.HERE/'netgen-strict-probes'/name;P.new_directory(out)
    exe=P.TOOLS/'netgen-1.5-current/bin/netgen';setup=P.HERE/'nangate45_netgen_strict.tcl';driver=P.HERE/'netgen_strict.tcl'
    pins={str(p):P.sha(p) for p in (layout,reference,exe,setup,driver)}
    for p in sorted((P.TOOLS/'netgen-1.5-current/lib/netgen').rglob('*')):
        if p.is_file() and p.suffix in ('.so','.tcl'):pins[str(p)]=P.sha(p)
    os.environ.update(PROBE_EXTRACTED=str(layout),PROBE_REFERENCE=str(reference),PROBE_TOP=top,
                      PROBE_REPORT=str(out/'comparison.rpt'),PROBE_SETUP=str(setup))
    timing=P.bound_run([str(exe),'-batch','source',str(driver)],out,out/'checker.log',600)
    report=out/'comparison.rpt';text=report.read_text(errors='replace') if report.exists() else ''
    verdict=strict_verdict(timing['exit_code'],text,(out/'checker.log').read_text(errors='replace'))
    P.check_hashes(pins);receipt={'verdict':verdict,'timing':timing,'pins':pins,'paper_data':False,'model_requests':0,'eda_flows':0}
    P.atomic_json(out/'receipt.json',receipt);print(name,verdict,flush=True);return verdict


def main():
    root=P.HERE/'extraction-probes/C02982-lvs-new';layout=root/'layout.cir';reference=root/'schematic.cir';top='fsm_bolme_islemi'
    if run('clean_control',layout,reference,top)['status']!='clean':raise RuntimeError('Clean control rejected')
    inputs=P.HERE/'netgen-counterexamples';P.new_directory(inputs)
    for kind in ('remove_instance','change_width'):
        mutated=inputs/(kind+'.cir');mutated.write_text(mutate(layout.read_text(),kind))
        if run(kind,mutated,reference,top)['status']!='fail':raise RuntimeError('Missed injected defect: '+kind)


if __name__=='__main__':main()
