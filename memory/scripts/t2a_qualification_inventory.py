#!/usr/bin/env python3
"""Append qualification disposition, include all six attempted tasks, no task replacement.

Only evidence review; unresolved LVS representation and timeouts are not declared
design failures. Source-family and exact RTL/module-overlap checks are disclosed,
not a proof that any generic combinational primitive has an independent invention.
"""
import argparse
import copy
import json
from pathlib import Path
import re
import sys
import time

import t2a_adapters as A
import t2a_qualify as Q
from t2a_lvs_evidence_audit import classify

FAMILY={'ibex':'lowRISC/ibex','riscv32i':'OSU-course/riscv32i-2019','gcd':'Cornell-BRG/OpenCelerity-gcd'}
DEV_DIRECTORY_ALIASES={'eth_demux':'verilog_ethernet_eth_demux','udp_ip_rx_64':'verilog_ethernet_udp_ip_rx_64'}


def rtl_identity(project):
    hashes={};modules=set()
    for p in (project/'rtl').rglob('*'):
        if p.is_file() and p.suffix in ('.v','.sv'):
            hashes[str(p)]=Q.digest(p)
            modules.update(re.findall(r'(?m)^\s*module\s+(\w+)',p.read_text(errors='replace')))
    return hashes,modules


def build(output,envelopes,q4):
    output.mkdir(parents=True,exist_ok=False);start=time.time()
    source=Q.EVAL/'p1c-export/SOURCE-v1/source_export.jsonl'
    manifest=json.loads(source.with_name('source_manifest.json').read_text());split=copy.deepcopy(manifest['split'])
    # Protocol: three secworks IPs, one conservative exclusion/analysis group.
    def grouped(family):return 'secworks/crypto' if family.startswith('secworks/') else family
    for partition in ('SOURCE','DEV','PROBE','TEST','exposed_in_development'):
        split[partition]=sorted({grouped(f) for f in split.get(partition,[])})
    split['family_of_design']={d:grouped(f) for d,f in split['family_of_design'].items()}
    split['TEST']=list(FAMILY.values());split['family_of_design'].update(FAMILY)
    sys.path.insert(0,str(Q.REPO/'memory'))
    from tehm.neutral_export import check_isolation
    isolation=check_isolation(A.load_source(source),split)
    dev_hashes=set();dev_modules=set();dev_inputs={};missing=[]
    for design in manifest['split']['family_of_design']:
        base=Q.EVAL/'sandbox-f/design_cases'/DEV_DIRECTORY_ALIASES.get(design,design)
        if not (base/'rtl').is_dir():base=base.with_name(base.name+'__sky130hd')
        hashes,modules=rtl_identity(base)
        if not hashes:missing.append(design)
        dev_hashes.update(hashes.values());dev_modules.update(modules);dev_inputs.update(hashes)
    attempted=[];proposed=[];bindings={str(source):Q.digest(source),str(q4/'result.json'):Q.digest(q4/'result.json')}
    for design in ('ibex','riscv32i','gcd'):
        root=Q.EVAL/('t2a-qualification-ibex-q3-20261011' if design=='ibex' else 't2a-qualification-a3-20261011-b')
        registration=json.loads((root/'registration.json').read_text())
        bindings[str(root/'registration.json')]=Q.digest(root/'registration.json')
        for task in registration['tasks']:
            if task['design']!=design:continue
            path=root/(design+'__'+task['variant']+'.json')
            if not path.exists():continue
            raw=json.loads(path.read_text());project=Path(task['project']);bindings[str(path)]=Q.digest(path)
            hashes,modules=rtl_identity(project)
            overlap={'rtl_hashes':sorted(set(hashes.values())&dev_hashes),'module_names':sorted(modules&dev_modules)}
            bindings.update(hashes)
            log='\n'.join(p.read_text(errors='replace') for p in (project/'backend').glob('RUN_*/flow.log'))
            key=design+'/'+task['variant'];status='REVIEW_REQUIRED';reason='No automatic admission'
            if key in ('ibex/default','gcd/default'):
                status='LVS_RESIDUAL_REVIEW';reason='Q4 removes parse errors, but native LVS still mismatches; cause not established'
            elif key=='riscv32i/u80':
                status='TIMEOUT_CENSORED';reason='5400-second detail-route timeout; runner termination cause UNKNOWN; no retry'
            elif key=='ibex/u80' and '[ERROR GRT-0116]' in log:
                status='VERIFIED_FAILURE_CANDIDATE';reason='Deterministic global-route congestion, no tool/parser error'
            elif key=='gcd/u80' and '[ERROR FLW-0024]' in log:
                status='VERIFIED_FAILURE_CANDIDATE';reason='Initial density+addon infeasibility; no silent correction before repair'
            elif key=='riscv32i/default':
                q1=root/'riscv32i-evidence-completion.json';bindings[str(q1)]=Q.digest(q1)
                rec=next(r for r in json.loads(q1.read_text())['records'] if r['task']=='default')
                acc=rec['acceptance']
                if not acc.get('unknown') and acc.get('failed')==['A6_setup']:
                    status='VERIFIED_FAILURE_CANDIDATE';reason='Complete initial layout/signoff, fixed-clock setup failure only'
                else:reason='Read Q1 acceptance checks; expected only setup failure'
            env=envelopes/(design+'__'+task['variant']+'.json')
            row={'task':key,'family':FAMILY[design],'status':status,'reason':reason,
                 'original_receipt':str(path),'original_receipt_sha256':Q.digest(path),
                 'overlap_with_dev':overlap,'paper_episode':False}
            if env.exists():row.update(manifest=str(env),manifest_sha256=Q.digest(env));bindings[str(env)]=Q.digest(env)
            attempted.append(row)
            if status=='VERIFIED_FAILURE_CANDIDATE':proposed.append(row)
    if missing or any(r['overlap_with_dev']['rtl_hashes'] or r['overlap_with_dev']['module_names'] for r in attempted):
        isolation['ok']=False
    inventory={'schema':'t2a-test-qualification-review-v1','status':'QUALIFICATION_REVIEW',
        'all_attempted_tasks':attempted,'tasks':proposed,'source_isolation':{**isolation,'missing_dev_inputs':missing},
        'source_split':split,'source_families_evidence':{
          'ibex':'Pinned source lowRISC package headers and license',
          'riscv32i':'Pinned README: OSU undergraduate course, July 2019',
          'gcd':'Pinned README: PyMTL, OpenCelerity'},
        'dev_rtl_files':len(dev_inputs),'input_hashes':{**dev_inputs,**bindings},
        'source_original_families_unchanged':8,'conservative_secworks_group':'secworks/crypto',
        'formal_launch_allowed':False,'remaining_gate':'Native LVS residual cause/full checker readiness on Ibex/gcd',
        'no_fill_after_stop':True,'eda_flows':0,'llm_requests':0,'wall_s':time.time()-start}
    Q.save(output/'inventory.json',inventory)
    print(json.dumps({'attempted':len(attempted),'proposed':[(r['task'],r['status']) for r in proposed],
                      'isolation':inventory['source_isolation'],'formal_launch_allowed':False}))


def resolved_acceptance(acceptance,native_status):
    """Only replace the erroneous LVS view with independently executed evidence."""
    if native_status!='VERIFIED_MATCH':raise ValueError('Native LVS readiness not verified')
    result=copy.deepcopy(acceptance)
    if len(result.get('checks',{}))!=11:raise ValueError('Incomplete initial acceptance checklist')
    result['checks']['A3_lvs']={'result':'pass','detail':{'basis':'independent_native_LVS_readiness_audit'}}
    result['failed']=sorted(k for k,v in result['checks'].items() if v['result']=='fail')
    result['unknown']=sorted(k for k,v in result['checks'].items() if v['result']=='unknown')
    result['accepted']=not result['failed'] and not result['unknown']
    if result['unknown']:raise ValueError('Unresolved initial evidence after LVS correction')
    return result


def audited_input_bindings(audit):
    """Use preserved byte-identical controllers when their worktree path advanced."""
    reg=json.loads((audit/'registration.json').read_text());result={}
    for name,digest in reg['files'].items():
        p=Path(name)
        if p.exists() and Q.digest(p)==digest:result[name]=digest;continue
        if p.name=='t2a_lvs_evidence_audit.py':
            preserved=Q.EVAL/'t2a-lvs-controller-prior-20261012'
            matches=[f for f in preserved.glob('*.py') if Q.digest(f)==digest]
            if len(matches)==1:result[str(matches[0])]=digest;continue
        raise ValueError('Native-audit input/code binding changed: '+name)
    return result


def freeze(output,review,q6,q7,negative,envelopes):
    start,cpu=time.time(),time.process_time()
    inventory=json.loads(review.read_text())
    if inventory.get('status')!='QUALIFICATION_REVIEW' or inventory.get('source_isolation',{}).get('ok') is not True:
        raise ValueError('Qualification/source isolation review not ready')
    controls=json.loads((negative/'result.json').read_text())
    records=controls.get('records',[])
    if (controls.get('passed') is not True or len(records)!=4 or
        {(r['task'],r['kind']) for r in records}!={(d,k) for d in ('gcd__default','ibex__default') for k in ('power','signal')}
        or not all(r.get('status')=='VERIFIED_MISMATCH' and r.get('passed') is True for r in records)):
        raise ValueError('Native LVS power/signal negative controls did not pass')
    bindings={str(review):Q.digest(review),str(negative/'result.json'):Q.digest(negative/'result.json')}
    bindings.update(audited_input_bindings(negative))
    readiness={}
    for audit,design in ((q6,'gcd'),(q7,'ibex')):
        result=audit/'result.json';data=json.loads(result.read_text())
        bindings.update(audited_input_bindings(audit))
        row=next(r for r in data['tasks'] if Path(r['project']).name==design+'__default')
        directory=Path(row['directory'])
        native=classify((directory/'native.log').read_text(),(directory/'native.rpt').read_text(),0)
        if row['status']!='VERIFIED_MATCH' or native['status']!='VERIFIED_MATCH':
            raise ValueError('Native readiness mismatch: '+design)
        if Q.digest(directory/'powered_hierarchical.v')!=row['fixed_reference_sha256']:
            raise ValueError('Native reference changed')
        readiness[design]={**row,'audit_result':str(result),'audit_result_sha256':Q.digest(result)}
        for p in (result,audit/'registration.json',directory/'native.log',directory/'native.rpt',
                  directory/'powered_hierarchical.v',directory/'power_connectivity.json'):
            bindings[str(p)]=Q.digest(p)
    output.mkdir(parents=True,exist_ok=False)
    attempted=copy.deepcopy(inventory['all_attempted_tasks']);tasks=[];resolutions={}
    for row in attempted:
        design,var=row['task'].split('/')
        if row['task'] in ('gcd/default','ibex/default'):
            root=Path(row['original_receipt']).parent
            project=Path(readiness[design]['project'])
            acceptance=project/'reports'/('qualification_acceptance_q1.json' if design=='gcd' else 'acceptance.json')
            resolved=resolved_acceptance(json.loads(acceptance.read_text()),readiness[design]['status'])
            resolutions[row['task']]={'acceptance':resolved,'native_readiness':readiness[design],
                                     'original_acceptance':str(acceptance),'original_sha256':Q.digest(acceptance)}
            bindings[str(acceptance)]=Q.digest(acceptance)
            row['status']='INITIAL_PASS_EXCLUDED' if resolved['accepted'] else 'VERIFIED_FAILURE_CANDIDATE'
            row['reason']='Corrected initial LVS evidence; all other initial checks unchanged'
        if row['status']=='VERIFIED_FAILURE_CANDIDATE':
            path=envelopes/(design+'__'+var+'.json')
            if not path.is_file():raise ValueError('No frozen envelope for '+row['task'])
            row.update(status='ADMITTED',manifest=str(path),manifest_sha256=Q.digest(path))
            bindings[str(path)]=Q.digest(path);tasks.append(copy.deepcopy(row))
    if len(tasks)>6 or any(sum(r['family']==family for r in tasks)>2 for family in FAMILY.values()):
        raise ValueError('Qualification family/task quota exceeded')
    # Recheck every recorded RTL, original receipt, immutable input and source
    # overlap binding. No NB/memory repair outcome has been observed for TEST.
    for p,h in inventory['input_hashes'].items():
        if Q.digest(p)!=h:raise ValueError('Qualification input changed: '+p)
    sys.path.insert(0,str(Q.REPO/'memory'))
    from tehm.neutral_export import check_isolation
    isolation=check_isolation(A.load_source(Q.EVAL/'p1c-export/SOURCE-v1/source_export.jsonl'),inventory['source_split'])
    if not isolation['ok']:raise ValueError('Final TEST source isolation failed')
    frozen={**inventory,'schema':'t2a-frozen-test-inventory-v1','status':'FROZEN','tasks':tasks,
            'all_attempted_tasks':attempted,'source_isolation':{**inventory['source_isolation'],**isolation},
            'initial_evidence_resolutions':resolutions,'native_LVS_readiness':readiness,
            'negative_controls':str(negative/'result.json'),'negative_controls_sha256':Q.digest(negative/'result.json'),
            'input_hashes':{**inventory['input_hashes'],**bindings},'frozen_at':time.time(),
            'generator_sha256':Q.digest(__file__),'source_selection':'all verified initial failures within registered stop rules',
            'formal_launch_allowed':False,'remaining_gate':'Exact-version real seven-arm DEV validation',
            'new_eda_flows':0,'llm_requests':0,'wall_s':time.time()-start,'cpu_s':time.process_time()-cpu}
    Q.save(output/'inventory.json',frozen)
    print(json.dumps({'status':'FROZEN','tasks':[r['task'] for r in tasks],'formal_launch_allowed':False}))


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--freeze',action='store_true');ap.add_argument('--review',type=Path)
    ap.add_argument('--q6',type=Path);ap.add_argument('--q7',type=Path);ap.add_argument('--negative',type=Path)
    ap.add_argument('--envelopes',type=Path,required=True);ap.add_argument('--q4',type=Path)
    a=ap.parse_args()
    if a.freeze:freeze(a.output.resolve(),a.review.resolve(),a.q6.resolve(),a.q7.resolve(),a.negative.resolve(),a.envelopes.resolve())
    else:build(a.output.resolve(),a.envelopes.resolve(),a.q4.resolve())
