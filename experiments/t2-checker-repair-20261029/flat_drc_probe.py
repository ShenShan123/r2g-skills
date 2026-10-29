"""Experimental execution-mode delta, preserving every complete-deck check."""
import json
from pathlib import Path
import re
import shutil
import sys
import xml.etree.ElementTree as ET

import checker_probe as P


def flatten_mode(text):
    changed, count = re.subn(r'(?m)^deep$', 'flat', text)
    if count != 1: raise ValueError('Expected exactly one execution-mode directive')
    # No rule, threshold, input, FEOL/BEOL/antenna or output statement is removed.
    if changed.replace('\nflat\n','\ndeep\n',1) != text: raise ValueError('Unexpected deck delta')
    return changed


def check(name, gds, deck, seconds):
    out=P.HERE/'flat-mode-probes'/name;P.new_directory(out)
    for source in (gds,deck):shutil.copyfile(source,out/source.name)
    local_gds=out/gds.name;local_deck=out/deck.name
    pins={str(p):P.sha(p) for p in (gds,deck,local_gds,local_deck)};pins.update(P.tool_pins(P.NEW))
    database=out/'6_drc.lyrdb';cmd=[str(P.NEW),'-zz','-rd','in_gds='+str(local_gds),'-rd','report_file='+str(database),'-r',str(local_deck)]
    timing=P.bound_run(cmd,out,out/'checker.log',seconds)
    verdict=P.drc_verdict(timing['exit_code'],database,(out/'checker.log').read_text(errors='replace'))
    P.check_hashes(pins)
    receipt={'name':name,'verdict':verdict,'timing':timing,'pins':pins,'command':cmd,'paper_data':False,
             'full_deck':True,'mode_change_only':True,'model_requests':0,'eda_flows':0}
    P.atomic_json(out/'receipt.json',receipt);print(json.dumps({'name':name,'verdict':verdict,'wall_s':timing['wall_s']}),flush=True)
    return receipt


def main():
    root=P.HERE/'flat-mode-inputs'
    continuing = '--continue-controls' in sys.argv
    if not continuing:P.new_directory(root)
    original=P.FLOW/'platforms/nangate45/drc/FreePDK45.lydrc'
    original_text=original.read_text();flat=root/'FreePDK45_flat.lydrc'
    if continuing:
        if flat.read_text()!=flatten_mode(original_text):raise ValueError('Deck drift')
    else:
        flat.write_text(flatten_mode(original_text))
        P.atomic_json(root/'delta.json',{'original':str(original),'original_sha256':P.sha(original),'candidate':str(flat),
            'candidate_sha256':P.sha(flat),'only_change':'deep -> flat; all checks and thresholds unchanged','paper_data':False})
    invalid=root/'counterexample.gds'
    if not continuing:
        rc=P.bound_run([str(P.NEW),'-zz','-rd','output='+str(invalid),'-r',str(P.HERE/'make_drc_counterexample.rb')],root,root/'generate.log',30)
        if rc['exit_code']:raise RuntimeError('Counterexample generation failed')
        check('counterexample-deep',invalid,original,60);check('counterexample-flat',invalid,flat,60)
    # Deep stores one marker in the twice-instantiated leaf; flat stores two at
    # the top. Raw marker counts are NOT comparable between these modes.
    a=ET.parse(P.HERE/'flat-mode-probes/counterexample-deep/6_drc.lyrdb').getroot()
    b=ET.parse(P.HERE/'flat-mode-probes/counterexample-flat/6_drc.lyrdb').getroot()
    deep=[(i.findtext('category'),i.findtext('cell')) for i in a.findall('./items/item')]
    flat_items=[(i.findtext('category'),i.findtext('cell')) for i in b.findall('./items/item')]
    if deep!=[("'METAL1.1'",'NARROW_METAL')] or flat_items!=[("'METAL1.1'",'COUNTEREXAMPLE')]*2:
        raise RuntimeError('Injected violations not preserved; no real-layout probe')
    P.atomic_json(root/'counterexample-audit.json',{'deep_leaf_markers':1,'leaf_instances':2,'flat_top_markers':2,
        'category':'METAL1.1','both_detect_failure':True,'note':'Raw marker counts differ by hierarchy; not a repair gain'})
    plan=json.loads((P.HERE/'plan-v2.json').read_text())
    clean=next(j for j in plan['jobs'] if j['id']=='C02982-drc-new')
    result=check('C02982-clean-flat',Path(clean['inputs']['gds']),flat,180)
    if result['verdict']['status']!='clean':raise RuntimeError('Clean control changed; no real-layout probe')
    broken=next(j for j in plan['jobs'] if j['id']=='C00517-drc-new')
    check('C00517-timeout-flat',Path(broken['inputs']['gds']),flat,1200)


if __name__=='__main__':main()
