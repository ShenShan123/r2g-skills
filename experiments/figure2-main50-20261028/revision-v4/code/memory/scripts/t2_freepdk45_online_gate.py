#!/usr/bin/env python3
"""r15 (user, 2026-10-23): the online accounting gate follows the registered request-count-only policy.

Every fold registers online policy agent9-request-count-only-provider-maximum-json-v2 with
input_bytes_ceiling None (user, 2026-10-21: memory arms limit the number of calls only). The fold
workers' end-of-episode accounting_check still compared each request body to the legacy 96,000-byte
O1 ceiling, so a fully measured A-MEM request of 354,953 bytes (C00584) read as an accounting failure.

This module never edits a fold: the fold code and registrations stay byte-identical. It wraps the
fold's own accounting_check. A request's byte comparison is waived only when the request_policy
event recorded just before it is the registered policy with input_bytes_ceiling None; model
identity, finite usage, the three-request cap, open holds and flow correspondence are unchanged.

Commands:
  episode   worker shim: <fold t2_agent_request_resume.py> episode ...  (installs the gate, then the
            fold's own entrypoint runs unchanged)
  rederive  <episode dir> <archive dir> <expected episode.json sha256>: re-derive one episode whose
            only accounting error was the legacy byte check, from its unchanged ledger. No flow, no
            model call, no rerun; the original record is archived first.
"""
from __future__ import annotations
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import sys
import time

RULE='registered-request-count-only-input-gate-v1'
POLICY='agent9-request-count-only-provider-maximum-json-v2'
ERROR='provider identity/input/usage violation'
MODELS=('deepseek-flash','deepseek-v4.1-flash')
ROOT=Path(__file__).resolve().parents[3]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def finite_count(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>=0


def registered(policy):
    return (isinstance(policy,dict) and policy.get('revision')==POLICY
            and 'input_bytes_ceiling' in policy and policy['input_bytes_ceiling'] is None)


def request_ok(r, check_bytes, limit):
    usage=r.get('usage_reported');size=r.get('request_bytes')
    if r.get('model_mismatch') or r.get('model_reported') not in MODELS or not finite_count(size):return False
    if check_bytes and size>limit:return False
    return isinstance(usage,dict) and all(finite_count(usage.get(k)) for k in ('prompt_tokens','completion_tokens'))


def aligned(original, events, run_dirs, limit):
    report=original(events,run_dirs)
    if ERROR not in report['errors']:return report
    errors=[e for e in report['errors'] if e!=ERROR];policy=None;waived=[]
    for e in events:
        if e.get('type')=='request_policy':policy=e
        elif e.get('type')=='llm_request':
            covered=registered(policy)
            if not request_ok(e,not covered,limit):errors.append(ERROR)
            elif e['request_bytes']>limit:
                waived.append({'event_seq':e.get('event_seq'),'request_bytes':e['request_bytes'],
                               'policy_event_seq':policy.get('event_seq')})
            policy=None  # each request must carry its own policy record
    out={**report,'errors':errors,'passed':not errors}
    if waived and not errors:
        out['input_gate']={'rule':RULE,'policy':POLICY,'legacy_ceiling_bytes':limit,'waived_requests':waived}
    return out


def install(T):
    if getattr(T,'_INPUT_GATE',None)==RULE:return
    original=T.accounting_check
    def accounting_check(events, run_dirs):
        return aligned(original,events,run_dirs,T.ONLINE_INPUT_BYTES)
    T.accounting_check=accounting_check;T._INPUT_GATE=RULE;T._INPUT_GATE_ORIGINAL=original


def load_fold(entry):
    entry=Path(entry).resolve()
    if (entry.name!='t2_agent_request_resume.py' or entry.parents[4].name!='folds'
            or entry.parents[5]!=ROOT or entry.parents[2].name!='code'):
        raise ValueError('Not a registered fold entrypoint: '+str(entry))
    here=Path(__file__).resolve().parent
    sys.path[:]=[p for p in sys.path if Path(p or '.').resolve()!=here]
    for name in ('t2_agent_request_resume','t2a_runtime','t2a_batch','t2_agent_verdict'):
        if name in sys.modules:raise RuntimeError('Fold module imported too early: '+name)
    spec=importlib.util.spec_from_file_location('t2_agent_request_resume',entry)
    mod=importlib.util.module_from_spec(spec);sys.modules['t2_agent_request_resume']=mod;spec.loader.exec_module(mod)
    import t2a_runtime as T
    if Path(T.__file__).resolve().parent!=entry.parent:raise RuntimeError('Runtime is not the fold copy')
    install(T)
    return mod,T


def rows(path):
    return [json.loads(l) for l in Path(path).read_text().splitlines() if l]


def rederive(episode, archive, expected, write=True):
    p=Path(episode).resolve();archive=Path(archive).resolve();record=p/'episode.json'
    _,T=load_fold(p.parents[1]/'code/memory/scripts/t2_agent_request_resume.py')
    import t2_agent_verdict as AV
    if Path(AV.__file__).resolve().parent!=Path(T.__file__).resolve().parent:raise RuntimeError('Verdict is not the fold copy')
    saved=archive/p.name/'episode.json'
    e=json.loads(record.read_text())
    if e.get('accounting_rederivation'):
        r=e['accounting_rederivation']
        if r.get('rule')!=RULE or r.get('original_sha256')!=expected or sha(saved)!=expected:
            raise RuntimeError('Re-derivation drift: '+str(p))
        return e
    if sha(record)!=expected:raise RuntimeError('Episode changed since registration: '+str(p))
    if e.get('status')!='ACCOUNTING_FAILURE' or e.get('accounting',{}).get('errors')!=[ERROR]:
        raise ValueError('Only a legacy byte-gate accounting failure can be re-derived: '+str(p))
    if e.get('error') or e.get('technical_failures') or not isinstance(e.get('final'),dict):
        raise ValueError('Episode did not reach a measured final verdict: '+str(p))
    events=rows(p/'reports/resource_ledger.jsonl');runs=list((p/'backend').glob('RUN_*'))
    if T._INPUT_GATE_ORIGINAL(events,runs)!=e['accounting']:raise RuntimeError('Recorded accounting does not reproduce: '+str(p))
    acc=T.accounting_check(events,runs)
    if acc['llm_requests']>3:raise ValueError('Online request limit exceeded: '+str(p))
    if not acc['passed'] or not acc.get('input_gate'):raise ValueError('Accounting fails beyond the byte gate: '+str(p))
    if (e.get('token_usage') or {}).get('unknown_usage_requests'):raise ValueError('UNKNOWN usage: '+str(p))
    # The worker's terminal verdict attaches a native-infeasibility receipt only when accounting
    # passes, so the legacy gate also withheld it. Rebuild it read-only from the same files (the
    # default env reproduces every recorded sibling receipt), after checking that the legacy gate
    # reproduces the recorded absence.
    env={'R2G_TASK_MANIFEST':str(p/'manifest.json')}
    if e['final'].get('native_failure_receipt'):raise ValueError('Receipt already present: '+str(p))
    wrapped=T.accounting_check;T.accounting_check=T._INPUT_GATE_ORIGINAL
    try:withheld=AV.native_failure(p,env)
    finally:T.accounting_check=wrapped
    if withheld is not None:raise RuntimeError('Recorded verdict does not reproduce: '+str(p))
    final=AV.attach_failure(p,env,e['final'])
    status=AV.terminal_status(final)
    reason=AV.terminal_reason(final,acc['repair_attempts'])
    new={**e,'final':final,'accounting':acc,'status':status,'terminal_reason':reason,'accepted':status=='ACCEPTED',
         'accounting_rederivation':{'rule':RULE,'policy':POLICY,'original_sha256':expected,'original_record':str(saved),
            'native_failure_receipt_rebuilt':bool(final.get('native_failure_receipt')),
            'original_status':e['status'],'original_terminal_reason':e.get('terminal_reason'),
            'original_errors':e['accounting']['errors'],'waived_requests':acc['input_gate']['waived_requests'],
            'source':'unchanged resource ledger, recorded final verdict and run files; no flow, model call or rerun',
            'authorization':'user 2026-10-23: align the gate with the registered request-count-only policy',
            'recorded':time.time()}}
    if not write:return new
    saved.parent.mkdir(parents=True,exist_ok=True)
    with saved.open('xb') as f:f.write(record.read_bytes())
    if sha(saved)!=expected:raise RuntimeError('Archive mismatch: '+str(saved))
    tmp=record.with_name('.episode.json.r15');tmp.write_text(json.dumps(new,indent=1,sort_keys=True));os.replace(tmp,record)
    return new


def main(argv=None):
    argv=list(sys.argv if argv is None else argv)
    if len(argv)>=3 and argv[2]=='episode':
        mod,_=load_fold(argv[1])
        sys.argv=[str(Path(argv[1]).resolve()),*argv[2:]]
        mod.main();return
    if len(argv)==5 and argv[1]=='rederive':
        r=rederive(argv[2],argv[3],argv[4])
        print(json.dumps({'episode':argv[2],'status':r['status'],'terminal_reason':r['terminal_reason'],
                          'accounting_passed':r['accounting']['passed']}));return
    raise SystemExit('usage: t2_freepdk45_online_gate.py <fold entry> episode ... | rederive <episode> <archive> <sha256>')


if __name__=='__main__':main()
