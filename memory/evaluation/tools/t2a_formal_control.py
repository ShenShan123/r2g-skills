#!/usr/bin/env python3
"""Corrective formal-run control, using the unchanged DEV-validated runtime.

Operator-side only: check the original payment bank, retire the stopped F1
supervisor, run the private batch, retain every record and write a full matrix.
Never retries episodes, resets quotas, changes candidates or merges versions.
"""
import argparse
import csv
import fcntl
import hashlib
import importlib
import json
import os
from pathlib import Path
import signal
import sys
import time


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path,value):
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n');tmp.replace(path)


def identity(pid):
    try:
        p=Path('/proc',str(pid));s=(p/'stat').read_text().rsplit(')',1)[1].split()
        return {'pid':pid,'start_ticks':s[19],'state':s[0],
                'argv':(p/'cmdline').read_bytes().split(b'\0')[:-1]}
    except (OSError,ProcessLookupError):return None


def check_binding(root):
    grant=json.loads((root/'formal-control-registration.json').read_text())
    if digest(__file__)!=grant['controller_sha256'] or digest(root/'registration.json')!=grant['registration_sha256']:
        raise RuntimeError('Controller/package identity changed')
    bank=root/'phase/resource_ledger.jsonl'
    if bank.resolve()!=Path(grant['original_ledger']).resolve():raise RuntimeError('Payment bank changed')
    raw=bank.read_bytes()
    if hashlib.sha256(raw[:grant['prior_ledger_bytes']]).hexdigest()!=grant['prior_ledger_sha256']:
        raise RuntimeError('Original spending prefix changed')
    if digest(root/'phase-budget.json')!=grant['budget_sha256']:raise RuntimeError('Payment allocation changed')
    dev=Path(grant['dev_root'])
    if digest(dev/'dev-validation.json')!=grant['dev_receipt_sha256'] or digest(dev/'completion-audit.json')!=grant['dev_audit_sha256']:
        raise RuntimeError('DEV evidence changed')
    if not json.loads((dev/'completion-audit.json').read_text())['passed']:raise RuntimeError('DEV audit not passed')
    return grant


def retire_old(root,grant):
    old=grant['old_supervisor'];current=identity(old['pid'])
    if not current:raise RuntimeError('Old supervisor identity unavailable; no signals sent')
    if current['start_ticks']!=old['start_ticks'] or current['state'] not in ('T','t'):
        raise RuntimeError('Old supervisor no longer the expected stopped process')
    expected=Path(grant['old_root'])/'code/memory/scripts/t2a_batch.py'
    if str(expected).encode() not in current['argv'] or b'batch' not in current['argv']:
        raise RuntimeError('Wrong old supervisor command')
    p=Path('/proc',str(old['pid']));status=(p/'status').read_text().splitlines()
    for key in ('SigBlk','SigIgn','SigCgt'):
        bits=int(next(line.split(':',1)[1].strip() for line in status if line.startswith(key+':')),16)
        if bits & (1<<(signal.SIGTERM-1)):raise RuntimeError('SIGTERM not default/unblocked; no signals sent')
    children=(p/'task'/str(old['pid'])/'children').read_text().split()
    for text in children:
        child=identity(int(text))
        if child and child['state'] not in ('Z','X'):raise RuntimeError('A prior episode is still active; no signals sent')
    record={'time':time.time(),'pid':old['pid'],'start_ticks':old['start_ticks'],
            'children':children,'scope':'Stopped supervisor only; no process group or data removed'}
    save(root/'old-supervisor-retirement-pending.json',record)
    os.kill(old['pid'],signal.SIGTERM)
    try:os.kill(old['pid'],signal.SIGCONT)  # deliver pending default TERM, not resume dispatch
    except ProcessLookupError:pass
    for _ in range(40):
        now=identity(old['pid'])
        if not now or now['start_ticks']!=old['start_ticks'] or now['state'] in ('Z','X'):break
        time.sleep(.05)
    else:raise RuntimeError('Old supervisor did not terminate; refusing new launches')
    save(root/'old-supervisor-retired.json',{**record,'terminated':True})


def verify_completed_prior(grant):
    """A completed prior batch needs no retirement signals or resume/retry."""
    prior=grant['completed_prior'];root=Path(prior['root'])
    for name,key in [('registration.json','registration_sha256'),
                     ('batch-complete.json','completion_sha256'),
                     ('formal-results.json','results_sha256')]:
        if digest(root/name)!=prior[key]:raise RuntimeError('Prior completed evidence changed: '+name)
    result=json.loads((root/'formal-results.json').read_text())
    completed=json.loads((root/'batch-complete.json').read_text())
    if (result.get('execution_complete') is not True or result.get('data_audit_passed') is not True
            or result.get('registered')!=56 or len(result.get('records',[]))!=56
            or completed.get('episodes')!=56
            or any(r.get('status')=='RUNNING' for r in result['records'])):
        raise RuntimeError('Prior batch is not completely ended/audited')
    expected=prior.get('supervisor')
    current=identity(expected['pid']) if expected else None
    if current and current['start_ticks']==expected['start_ticks'] and current['state'] not in ('Z','X'):
        raise RuntimeError('Prior supervisor remains active; no new dispatch')
    return True


def collect(root,B):
    grant=check_binding(root);reg=B.preflight(root,'TEST')
    families={r['task']:r['family'] for r in json.loads((root/'inventory.json').read_text())['tasks']}
    rows=[];events_all=[];missing=[]
    for item in reg['plan']:
        name=item['task'].replace('/','__')+'__'+item['arm']+'__r'+str(item['repeat'])
        project=root/'episodes'/name;path=project/'episode.json'
        if not path.exists():missing.append(item);continue
        data=json.loads(path.read_text())
        events=[json.loads(l) for l in (project/'reports/resource_ledger.jsonl').read_text().splitlines() if l]
        events_all.extend(events)
        derived=B.T.terminal_status(data.get('final') or {})
        accounting=B.T.accounting_check(events,list((project/'backend').glob('RUN_*')))
        labels=(data.get('split')=='TEST' and data.get('paper_data') is True
                and all(data.get(k)==item[k] for k in ('task','arm','repeat')))
        consistent=(data['status']==derived or (data['status']=='ACCOUNTING_FAILURE' and not accounting['passed']))
        row={**item,'family':families[item['task']],'status':data['status'],
             'accepted':data.get('accepted'),'strict_success_confirmed':False,
             'episode':str(path),'episode_sha256':digest(path),'labels_valid':labels,
             'terminal_classification_consistent':consistent,'accounting':accounting,
             'tokens':B.T.request_token_totals(events),'wall_s':data.get('wall_s'),
             'total_worker_wall_s':data.get('total_worker_wall_s'),'harness_cpu_s':data.get('harness_cpu_s'),
             'tool_calls':sum(e.get('type')=='tool_call' for e in events),
             'flow_components':{},'final_failed':((data.get('final') or {}).get('acceptance') or {}).get('failed'),
             'final_unknown':((data.get('final') or {}).get('acceptance') or {}).get('unknown'),
             'terminal_reason':data.get('terminal_reason'),
             'error':data.get('error')}
        row['strict_success_confirmed']=bool(data['status']=='ACCEPTED' and derived=='ACCEPTED' and labels and consistent)
        for e in events:
            if e.get('type')=='flow_run':
                key=e.get('component','unspecified');row['flow_components'][key]=row['flow_components'].get(key,0)+1
        rows.append(row)
    by_arm={}
    index={(r['task'],r['repeat'],r['arm']):r for r in rows}
    for arm in reg['arms']:
        part=[r for r in rows if r['arm']==arm];denom=sum(r['arm']==arm for r in reg['plan'])
        complete=len(part)==denom and all(r['status']!='RUNNING' for r in part)
        usage=complete and all(r['tokens']['measurement_complete'] for r in part)
        paired={'G':0,'H':0,'eligible_pairs':0,'unverified_or_missing_pairs':0,'by_source':{}}
        for task in reg['tasks']:
            for rep in range(1,reg['repeats']+1):
                x=index.get((task['task'],rep,arm));nb=index.get((task['task'],rep,'NB'))
                source=families[task['task']];group=paired['by_source'].setdefault(source,{'G':0,'H':0,'eligible_pairs':0,'unverified_or_missing_pairs':0})
                if not x or not nb or any(r['status'] not in ('ACCEPTED','NOT_ACCEPTED') or not r['terminal_classification_consistent'] for r in (x,nb)):
                    paired['unverified_or_missing_pairs']+=1;group['unverified_or_missing_pairs']+=1;continue
                paired['eligible_pairs']+=1;group['eligible_pairs']+=1
                for label,condition in (('G',x['strict_success_confirmed'] and not nb['strict_success_confirmed']),
                                        ('H',nb['strict_success_confirmed'] and not x['strict_success_confirmed'])):
                    paired[label]+=int(condition);group[label]+=int(condition)
        by_arm[arm]={'registered':denom,'records':len(part),'strict_successes':sum(r['strict_success_confirmed'] for r in part),
          'status_counts':{s:sum(r['status']==s for r in part) for s in sorted({r['status'] for r in part})},
          'mean_total_tokens_all_registered':sum(r['tokens']['known_input_tokens']+r['tokens']['known_output_tokens'] for r in part)/denom if usage else 'UNKNOWN',
          'mean_requests_all_registered':sum(r['tokens']['llm_requests'] for r in part)/denom if complete else 'UNKNOWN',
          'mean_flows_all_registered':sum(r['accounting']['flows'] for r in part)/denom if complete else 'UNKNOWN',
          'mean_tool_calls_all_registered':sum(r['tool_calls'] for r in part)/denom if complete else 'UNKNOWN','paired_vs_NB':paired}
    result={'schema':grant.get('result_schema','t2a-corrective-formal-results-f2-v1'),'completed_at':time.time(),
       'execution_complete':not missing and len(rows)==len(reg['plan']) and all(r['status']!='RUNNING' for r in rows),
       'records':rows,'missing':missing,'registered':len(reg['plan']),'tasks':len(reg['tasks']),
       'source_families':len(set(families.values())),'tokens':B.T.request_token_totals(events_all),
       'flows':sum(r['accounting']['flows'] for r in rows),'tool_calls':sum(r['tool_calls'] for r in rows),
       'by_arm':by_arm,'registration_sha256':grant['registration_sha256'],
       'original_spending_prefix_retained':True,'old_records_not_merged':True,
       'TEST_exposure':grant.get('TEST_exposure','Previously inspected during F1 defect correction; not a new blind confirmation'),
       'cost_notes':'All-episode averages include failures/stops. Tool wrapper and native-stage CPU records are nested, not additive.',
       'data_audit_passed':not missing and all(r['labels_valid'] and r['terminal_classification_consistent'] and r['accounting']['passed'] and r['tokens']['measurement_complete'] for r in rows)}
    save(root/'formal-results.json',result)
    with (root/'table2-matrix.csv').open('w',newline='') as output:
        fields=('task','family','repeat','arm','status','strict_success_confirmed','requests','repair_requests','auxiliary_requests','input_tokens','output_tokens','flows','tool_calls','wall_s')
        writer=csv.DictWriter(output,fieldnames=fields);writer.writeheader()
        for r in rows:
            writer.writerow({k:v for k,v in {**r,'requests':r['tokens']['llm_requests'],
             'repair_requests':r['tokens']['repair_requests'],'auxiliary_requests':r['tokens']['auxiliary_requests'],
             'input_tokens':r['tokens']['input_tokens'],'output_tokens':r['tokens']['output_tokens'],
             'flows':r['accounting']['flows']}.items() if k in fields})
    return result


def main(root):
    lock=(root/'formal-control.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if (root/'formal-control-start.json').exists() or (root/'batch-start.json').exists():
        raise RuntimeError('No implicit control/batch restart')
    grant=check_binding(root)
    sys.path.insert(0,str(root/'code/memory/scripts'));B=importlib.import_module('t2a_batch')
    B.preflight(root,'TEST')
    if grant.get('old_supervisor'):
        retire_old(root,grant)
    elif grant.get('completed_prior'):
        verify_completed_prior(grant)
    else:
        raise RuntimeError('Prior execution state was not registered; refusing dispatch')
    save(root/'formal-control-start.json',{'started':time.time(),'registration_sha256':grant['registration_sha256'],
         'shared_payment_bank':str((root/'phase/resource_ledger.jsonl').resolve()),'old_spending_not_reset':True})
    try:
        B.batch(root,'TEST');result=collect(root,B)
        print(json.dumps({'execution_complete':result['execution_complete'],'data_audit_passed':result['data_audit_passed'],
                          'registered':result['registered'],'tokens':result['tokens'],'flows':result['flows']}))
        return 0 if result['execution_complete'] and result['data_audit_passed'] else 1
    except Exception as exc:
        save(root/'formal-control-error.json',{'time':time.time(),'error':str(exc),'no_retry':True});raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,required=True)
    raise SystemExit(main(parser.parse_args().root.resolve()))
