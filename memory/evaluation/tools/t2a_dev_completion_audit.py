#!/usr/bin/env python3
"""Read-only DEV completion audit; writes separate receipts, never retries a run.

The episode collector remains authoritative and untouched. This operator-side
check independently requires terminal completeness, checks the frozen package,
reports every episode's costs, and detects surviving owned tool processes.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time

CHECKS={'A1_flow_complete','A2_drc_full_deck','A3_lvs','A4_route','A5_antenna',
        'A6_setup','A7_hold','A8_drv_limits','A9_task_clock','A10_rcx','A11_provenance'}
ARMS={'NB','TM0','RET','RECIPE','EXPEL','AWM','AMEM'}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path,value):
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value,sort_keys=True,indent=2)+'\n');temp.replace(path)


def terminal_complete(data):
    final=data.get('final') or {};acc=final.get('acceptance') or {};checks=acc.get('checks') or {}
    if (data.get('split')!='DEV' or data.get('paper_data') is not False
        or data.get('status') not in ('ACCEPTED','NOT_ACCEPTED')
        or final.get('execution_recovery') not in (None,'VERIFIED')
        or acc.get('unknown') or set(checks)!=CHECKS
        or any(v not in ('pass','fail') for v in checks.values())
        or (final.get('envelope') or {}).get('status') not in ('PASS','FAIL')):
        return False
    accepted=data['status']=='ACCEPTED'
    return (data.get('accepted') is accepted and final.get('accepted') is accepted
            and (not accepted or (acc.get('accepted') is True and all(v=='pass' for v in checks.values())
                                  and final['envelope']['status']=='PASS'))
            and (accepted or acc.get('accepted') is False or final['envelope']['status']=='FAIL'))


def alive(pid,ticks):
    try:
        fields=Path('/proc',str(pid),'stat').read_text().rsplit(')',1)[1].split()
        return fields[19]==ticks and fields[0] not in ('Z','X')
    except (OSError,ProcessLookupError):
        return False


def owned_survivors(root):
    found=[]
    native={'openroad','yosys','magic','netgen','sta','klayout','klayout.bin','netgenexec'}
    for path in Path('/proc').iterdir():
        if not path.name.isdigit() or int(path.name)==os.getpid():continue
        try:
            if path.stat().st_uid!=os.getuid():continue
            fields=(path/'stat').read_text().rsplit(')',1)[1].split()
            if fields[0] in ('Z','X'):continue
            argv=(path/'cmdline').read_bytes().split(b'\0')
            cmd=' '.join(a.decode(errors='replace') for a in argv)
            cwd=(path/'cwd').resolve()
            belongs=str(root) in cmd or cwd.is_relative_to(root)
            tool=(path/'comm').read_text().strip()
            if belongs and (tool in native or ('t2a_batch.py' in cmd and ' episode ' in ' '+cmd+' ')):
                found.append({'pid':int(path.name),'tool':tool})
        except (OSError,ProcessLookupError,PermissionError):
            continue
    return found


def native_checks_bound(items, runtime_hashes, runtime_registration_sha256=None):
    """All R3 failure paths, on this exact runtime, not one convenient probe."""
    required={'cts_partial','completed_area','place_partial'}
    if len(items)!=3 or {i.get('case') for i in items}!=required:return False
    try:
        for item in items:
            root=Path(item['root'])
            if (digest(root/'registration.json')!=item['registration_sha256']
                    or digest(root/'result.json')!=item['result_sha256']):return False
            reg=json.loads((root/'registration.json').read_text())
            result=json.loads((root/'result.json').read_text())
            if (reg.get('case')!=item['case'] or reg.get('runtime_hashes')!=runtime_hashes
                    or (runtime_registration_sha256 is not None and
                        reg.get('runtime_registration_sha256')!=runtime_registration_sha256)
                    or reg.get('paper_data') is not False or reg.get('llm_requests')!=0
                    or result.get('status')!='PASS' or result.get('paper_data') is not False
                    or not result.get('checks') or not all(result['checks'].values())
                    or not result.get('accounting',{}).get('passed')
                    or result['accounting'].get('flows')!=2
                    or result['accounting'].get('llm_requests')!=0
                    or result['accounting'].get('repair_attempts')!=0):return False
        return True
    except (OSError,ValueError,KeyError,TypeError):
        return False


def audit(root):
    sys.path.insert(0,str(root/'code/memory/scripts'))
    import t2a_batch as B
    registration=json.loads((root/'completion-audit-registration.json').read_text())
    assert digest(root/'registration.json')==registration['registration_sha256']
    assert digest(__file__)==registration['auditor_sha256']
    reg=B.preflight(root,split='DEV')
    receipt_path=root/'dev-validation.json'
    receipt=json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
    rows=[];missing=[];all_events=[]
    for item in reg['plan']:
        name=item['task'].replace('/','__')+'__'+item['arm']+'__r'+str(item['repeat'])
        project=root/'episodes'/name;path=project/'episode.json'
        if not path.exists():missing.append(name);continue
        data=json.loads(path.read_text())
        events=[json.loads(l) for l in (project/'reports/resource_ledger.jsonl').read_text().splitlines() if l]
        all_events.extend(events)
        rows.append({'arm':item['arm'],'task':item['task'],'episode':str(path),'episode_sha256':digest(path),
          'status':data.get('status'),'accepted':data.get('accepted'),
          'terminal_complete':terminal_complete(data),'accounting':data.get('accounting'),
          'token_usage':data.get('token_usage'),'wall_s':data.get('wall_s'),
          'total_worker_wall_s':data.get('total_worker_wall_s'),'harness_cpu_s':data.get('harness_cpu_s'),
          'tool_calls':sum(e.get('type')=='tool_call' for e in events),
          'rollback_recovery_flows':sum(e.get('type')=='flow_run' and e.get('component')=='rollback_recovery' for e in events),
          'resources':data.get('resources'),'repair_turns':data.get('repair_turns'),
          'error':data.get('error')})
    if 'native_checks' in registration:
        bound=native_checks_bound(registration['native_checks'],reg['runtime_hashes'],digest(root/'registration.json'))
        supplemental='all_native_restoration_paths_bound'
    else:
        probe=Path(registration['R2_probe_root']);pr=json.loads((probe/'registration.json').read_text())
        probe_result=json.loads((probe/'result.json').read_text())
        bound=(digest(probe/'registration.json')==registration['probe_registration_sha256']
               and digest(probe/'result.json')==registration['probe_result_sha256']
               and probe_result.get('status')=='PASS' and all(probe_result.get('checks',{}).values())
               and all(pr['files'][registration['source_repo']+'/'+k]==reg['runtime_hashes'][k]
                       for k in registration['R2_core_files']))
        supplemental='R2_supplemental_bound'
    survivors=owned_survivors(root)
    checks={'all_seven_ended':not missing and len(rows)==7 and {r['arm'] for r in rows}==ARMS
                              and all(r['status']!='RUNNING' for r in rows),
            'all_terminals_complete':len(rows)==7 and all(r['terminal_complete'] for r in rows),
            'all_accounting_complete':len(rows)==7 and all(r.get('accounting',{}).get('passed') for r in rows),
            'all_usage_complete':len(rows)==7 and all(r.get('token_usage',{}).get('measurement_complete') for r in rows),
            'original_collector_passed':receipt.get('real_seven_arm_dev_validated') is True,
            'frozen_preflight':True,supplemental:bound,'no_owned_tools_remaining':not survivors}
    tokens=B.T.request_token_totals(all_events)
    result={'schema':'t2a-dev-independent-completion-audit-v1','audited_at':time.time(),
      'split':'DEV','paper_data':False,'passed':all(checks.values()),'checks':checks,
      'missing':missing,'records':rows,'surviving_tools':survivors,'tokens':tokens,
      'flows':sum(r.get('accounting',{}).get('flows',0) for r in rows),
      'tool_calls':sum(r['tool_calls'] for r in rows),
      'strict_successes':sum(r['accepted'] is True for r in rows),
      'episode_count':len(rows),'design_count':1,'formal_launch_allowed':False,
      'collector_sha256':digest(receipt_path) if receipt_path.exists() else None,
      'registration_sha256':registration['registration_sha256'],
      'note':'Supplemental native branch checks are separate from this one real DEV task; no TEST restart.'}
    save(root/'completion-audit.json',result)
    return result


def main(root,wait):
    reg=json.loads((root/'completion-audit-registration.json').read_text())
    if wait:
        while alive(reg['supervisor_pid'],reg['supervisor_start_ticks']):
            save(root/'completion-audit-progress.json',{'status':'WAITING_FOR_DEV_BATCH','time':time.time(),
                 'paper_data':False,'formal_launch_allowed':False})
            time.sleep(30)
    result=audit(root)
    print(json.dumps({'passed':result['passed'],'episodes':result['episode_count'],
                     'tokens':result['tokens'],'flows':result['flows']}))
    return 0 if result['passed'] else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True);parser.add_argument('--wait',action='store_true')
    args=parser.parse_args();raise SystemExit(main(args.root.resolve(),args.wait))
