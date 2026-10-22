#!/usr/bin/env python3
"""Q4: append-only native LVS evidence audit; no new layout, repair, or LLM call.

Recompare the unchanged extracted layout/library after preserving the original
schematic identifier separators. Original projects and receipts are read-only.
Register every input, corrected helper and tool before running either comparison.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time

import t2a_qualify as Q

PARSER_ERROR = re.compile(r'Expected to find (?:instance pin block|end of instance)|'
                          r'Error:  No match in call for pin|has no pins', re.I)
BASE_SETUP = Path('/data1/zhangdy/Tools/tehm-toolchain/pdks/sky130A/libs.tech/netgen/sky130A_setup.tcl')


def classify(log, report, rc):
    if PARSER_ERROR.search(log):
        return {'status':'INFRASTRUCTURE_ERROR','reason':'netgen_parser_error'}
    if rc:
        return {'status':'INFRASTRUCTURE_ERROR','reason':'netgen_nonzero','rc':rc}
    if re.search('property errors', log+'\n'+report, re.I):
        return {'status':'VERIFIED_MISMATCH','reason':'netgen_property'}
    if re.search(r'(?mi)^\s*Final result:.*(?:failed|not match|mismatch)',report):
        return {'status':'VERIFIED_MISMATCH','reason':'native_terminal_failure'}
    if re.search(r'Circuits match uniquely|Result: PASS|netlists match', log, re.I):
        return {'status':'VERIFIED_MATCH'}
    if re.search(r'mismatch|NOT match|Result: FAIL|netlists do not match', log+'\n'+report, re.I):
        return {'status':'VERIFIED_MISMATCH','reason':'native_comparison'}
    return {'status':'UNKNOWN','reason':'no_terminal_comparison'}


def prepare(root, projects, amendment='Q4'):
    root.mkdir(parents=True, exist_ok=False)
    flow = Q.REPO/'r2g-skills/signoff-loop/scripts/flow'
    for name in ('normalize_power_connectivity.py','power_verilog_from_spice.py'):
        dst=root/'code'/name;dst.parent.mkdir(exist_ok=True);shutil.copy2(flow/name,dst)
    rows=[];files={}
    for project in projects:
        old=project/'lvs/netgen_lvs_result.json'
        raw=json.loads(old.read_text())
        source=json.loads(Path(raw['power_connectivity_receipt']).read_text())['source']
        if Q.digest(BASE_SETUP)!=raw['netgen_setup_sha256']:
            raise RuntimeError('PDK setup differs from original comparison')
        inputs=[old,Path(source),Path(raw['log_file']),Path(raw['report_file']),
                Path(raw['extracted_spice']),Path(raw['standard_cell_library']),
                Path(raw['gds_path']),Path(raw['netgen_compat_setup']),
                project/'lvs/run_netgen_lvs.tcl',project/'constraints/config.mk',
                project/'constraints/constraint.sdc',Path(raw['netgen_executable']),BASE_SETUP]
        files.update({str(p):Q.digest(p) for p in inputs})
        out=root/project.name;out.mkdir()
        rows.append({'project':str(project),'source':source,'top':raw['design'],
                     'netgen':raw['netgen_executable'],'original_result':str(old),
                     'original_classification':classify(Path(raw['log_file']).read_text(),
                                                       Path(raw['report_file']).read_text(),0),
                     'directory':str(out)})
    files.update({str(p):Q.digest(p) for p in (root/'code').glob('*.py')})
    for path in (Path(__file__),Q.REPO/'memory/scripts/r2g_memory_eval.py',
                 Q.REPO/'r2g-skills/signoff-loop/scripts/flow/resource_ledger.py'):
        files[str(path.resolve())]=Q.digest(path)
    Q.save(root/'registration.json',{'amendment':amendment,'registered_at':time.time(),'files':files,'tasks':rows,
           'allowed':'Normalize derived schematic power ports; native Netgen comparison only',
           'no_new_eda_flow':True,'no_repair':True,'no_llm':True,'no_retry':True,
           'original_projects_read_only':True,'timeout_s':3600})


def run(root):
    if (root/'result.json').exists():raise RuntimeError('No implicit retry')
    reg=json.loads((root/'registration.json').read_text())
    for p,h in reg['files'].items():
        if Q.digest(p)!=h:raise RuntimeError('Registered input changed: '+p)
    H=Q.module('q4_harness',Q.REPO/'memory/scripts/r2g_memory_eval.py')
    H.SKILL=Q.REPO/'r2g-skills/signoff-loop'
    audit_project=root/'accounting';(audit_project/'reports').mkdir(parents=True,exist_ok=False)
    H._EPISODE['ledger']=audit_project
    env=dict(os.environ)
    for key in ('R2G_BUDGET_FILE','R2G_LEDGER_PATH','R2G_PHASE_LEDGER','R2G_PHASE_BUDGET_FILE'):
        env.pop(key,None);os.environ.pop(key,None)
    env.update(R2G_BUDGET_COMPONENT='qualification_'+reg['amendment']+'_LVS',MAGIC_EXT_USE_GDS='1',
               R2G_NETGEN_BASE_SETUP=str(BASE_SETUP),PYTHONDONTWRITEBYTECODE='1')
    results=[]
    for row in reg['tasks']:
        out=Path(row['directory']);project=Path(row['project']);raw=json.loads(Path(row['original_result']).read_text())
        if (out/'native.log').exists():raise RuntimeError('Already launched: no implicit retry')
        fixed=out/'powered_hierarchical.v'
        rc=H.run([sys.executable,root/'code/normalize_power_connectivity.py',row['source'],row['top'],fixed,
                  '--receipt',out/'power_connectivity.json'],env,out/'normalization.log',120)
        if rc:raise RuntimeError('Normalization failed')
        original=(project/'lvs/run_netgen_lvs.tcl').read_text()
        tcl=original.replace('"'+raw['reference_netlist']+'"','"'+str(fixed)+'"').replace(
                             '"'+raw['report_file']+'"','"'+str(out/'native.rpt')+'"')
        if tcl==original:raise RuntimeError('Native comparison substitution missing')
        (out/'run.tcl').write_text(tcl)
        started=time.time()
        rc=H.run([row['netgen'],'-batch','source',out/'run.tcl'],env,out/'native.log',reg['timeout_s'])
        log=(out/'native.log').read_text(errors='replace')
        report=(out/'native.rpt').read_text(errors='replace') if (out/'native.rpt').exists() else ''
        result={**row,**classify(log,report,rc),'started':started,'ended':time.time(),
                'fixed_reference_sha256':Q.digest(fixed),'new_eda_flows':0,'llm_requests':0,
                'paper_episode':False,'acceptance_result':'NOT_REEVALUATED'}
        Q.save(out/'result.json',result);results.append(result)
        print(json.dumps({'task':project.name,'status':result['status']}),flush=True)
    # Check originals after the comparison; nothing in either original ledger is rewritten.
    for p,h in reg['files'].items():
        if Q.digest(p)!=h:raise RuntimeError('Original input changed during comparison: '+p)
    Q.save(root/'result.json',{'registration_sha256':Q.digest(root/'registration.json'),'tasks':results,
                             'formal_test_manifest_frozen':False,'new_eda_flows':0,'llm_requests':0})


def mutated_reference(text,kind):
    """A single explicit invalid leaf connection, only in an audit copy."""
    pattern=re.compile(r'(?ms)^\s*sky130_fd_sc_hd__(?:buf|inv|nand2|xor2)_\d+\s+[^\s(]+\s*\((.*?)\);')
    for m in pattern.finditer(text):
        pin='VGND' if kind=='power' else 'A'
        p=re.search(r'\.'+pin+r'\(\s*([^()]+?)\s*\)',m[1])
        if not p:continue
        value='VDD' if kind=='power' else 'VSS'
        if p[1].strip()==value:continue
        start=m.start(1)+p.start(1);end=m.start(1)+p.end(1)
        return text[:start]+value+text[end:],{'pin':pin,'old':p[1],'new':value,'offset':start}
    raise ValueError('No eligible leaf connection for the registered negative control')


def negative_controls(root,audits):
    root.mkdir(parents=True,exist_ok=False)
    rows=[];files={str(Path(__file__).resolve()):Q.digest(__file__),str(BASE_SETUP):Q.digest(BASE_SETUP)}
    for audit in audits:
        receipt=audit/'result.json';data=json.loads(receipt.read_text());files[str(receipt)]=Q.digest(receipt)
        for row in data['tasks']:
            if row['status']!='VERIFIED_MATCH':continue
            directory=Path(row['directory']);source=directory/'powered_hierarchical.v'
            tcl=directory/'run.tcl';raw=json.loads(Path(row['original_result']).read_text())
            for p in (source,tcl,Path(row['original_result']),Path(raw['extracted_spice']),
                      Path(raw['standard_cell_library']),Path(row['netgen']),Path(raw['netgen_compat_setup'])):
                files[str(p)]=Q.digest(p)
            for kind in ('power','signal'):
                out=root/(directory.name+'__'+kind);out.mkdir()
                changed,detail=mutated_reference(source.read_text(),kind)
                target=out/'invalid_reference.v';target.write_text(changed)
                script=out/'run.tcl';script.write_text(tcl.read_text().replace(str(source),str(target)).replace(
                    str(directory/'native.rpt'),str(out/'native.rpt')))
                files[str(target)]=Q.digest(target);files[str(script)]=Q.digest(script)
                rows.append({'task':directory.name,'kind':kind,'directory':str(out),'mutation':detail,
                             'netgen':row['netgen'],'script':str(script)})
    if len(rows)!=4:raise ValueError('Need two verified native positive designs, two controls each')
    H=Q.module('negative_control_harness',Q.REPO/'memory/scripts/r2g_memory_eval.py')
    H.SKILL=Q.REPO/'r2g-skills/signoff-loop';account=root/'accounting';(account/'reports').mkdir(parents=True)
    H._EPISODE['ledger']=account
    for p in (Q.REPO/'memory/scripts/r2g_memory_eval.py',H.SKILL/'scripts/flow/resource_ledger.py'):
        files[str(p)]=Q.digest(p)
    Q.save(root/'registration.json',{'schema':'t2a-native-lvs-negative-controls-v1','registered_at':time.time(),
        'files':files,'controls':rows,'new_eda_flows':0,'llm_requests':0,'originals_read_only':True,
        'expected':'VERIFIED_MISMATCH for each deliberately invalid derived reference','no_retry':True})
    env=dict(os.environ)
    for k in ('R2G_BUDGET_FILE','R2G_LEDGER_PATH','R2G_PHASE_LEDGER','R2G_PHASE_BUDGET_FILE'):
        env.pop(k,None);os.environ.pop(k,None)
    env.update(R2G_BUDGET_COMPONENT='qualification_LVS_negative_control',MAGIC_EXT_USE_GDS='1',
               R2G_NETGEN_BASE_SETUP=str(BASE_SETUP),PYTHONDONTWRITEBYTECODE='1')
    results=[]
    for row in rows:
        out=Path(row['directory']);rc=H.run([row['netgen'],'-batch','source',row['script']],env,out/'native.log',3600)
        report=out/'native.rpt';classification=classify((out/'native.log').read_text(errors='replace'),
            report.read_text(errors='replace') if report.exists() else '',rc)
        results.append({**row,**classification,'passed':classification['status']=='VERIFIED_MISMATCH'})
    for p,h in files.items():
        if Q.digest(p)!=h:raise RuntimeError('Negative-control registered inputs changed: '+p)
    Q.save(root/'result.json',{'schema':'t2a-native-lvs-negative-controls-result-v1',
        'passed':all(r['passed'] for r in results),'records':results,'new_eda_flows':0,'llm_requests':0,
        'registration_sha256':Q.digest(root/'registration.json'),'formal_test_manifest_frozen':False})
    print(json.dumps({'controls':len(results),'passed':all(r['passed'] for r in results)}),flush=True)


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('command',choices=('prepare','run','negative'));ap.add_argument('--root',type=Path,required=True)
    ap.add_argument('--audit',type=Path,action='append',default=[])
    ap.add_argument('--amendment',choices=('Q4','Q5','Q6','Q7'),default='Q4')
    ap.add_argument('--project',type=Path,action='append',default=[])
    a=ap.parse_args()
    if a.command=='prepare':prepare(a.root.resolve(),[p.resolve() for p in a.project],a.amendment)
    elif a.command=='negative':negative_controls(a.root.resolve(),[p.resolve() for p in a.audit])
    else:run(a.root.resolve())
