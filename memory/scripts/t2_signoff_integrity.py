"""Shared, opt-in Agent-study signoff validity and same-execution reuse.

Reuse only an actual verification performed in this worker on identical inputs,
layout and reports. No result is imported from a previous task or a disk cache.
Full-deck DRC, LVS, timing and area acceptance are never relaxed.
"""
from __future__ import annotations
import copy
import hashlib
import json
import os
from pathlib import Path

VERSION='same-execution-signoff-integrity-v1'
REPORTS=('drc','lvs','rcx','route','ppa','timing_check','acceptance','signoff_gate','strict_signoff','signoff_manifest')
UNKNOWN_STATUSES={'stuck','incomplete','error','failed','skipped','missing','unknown','timeout','timed_out',
                  'crash','stale','not_supported'}


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def load(path):return json.loads(Path(path).read_text())


def fingerprint(project,env):
    project=Path(project);runs=sorted((project/'backend').glob('RUN_*'))
    files=[project/'constraints/config.mk',project/'constraints/constraint.sdc',project/'manifest.json']
    files += [project/'reports'/(name+'.json') for name in REPORTS]
    if runs:
        run=runs[-1];files += [run/'effective-config.json',run/'stage_log.jsonl']
        for sub in ('final','results'):
            files += [run/sub/name for name in ('6_final.gds','6_final.odb','6_final.def','6_final.v','6_final.sdc','6_final.spef')]
    records={str(p):sha(p) if p.is_file() else None for p in files}
    records['runs']=[p.name for p in runs]
    # Purpose (repair vs terminal) does not change physical validity. Overrides
    # which CAN alter checks must not inherit a prior result.
    records['check_options']={k:str(v) for k,v in env.items() if k.startswith(('DRC_','LVS_','RCX_','R2G_LVS_'))}
    records['checker_tools']={k:{'path':str(env[k]),'sha256':sha(env[k]) if Path(env[k]).is_file() else None}
        for k in ('KLAYOUT_CMD','R2G_LVS_KLAYOUT_CMD','OPENROAD_EXE','NETGEN_EXE','MAGIC_EXE') if env.get(k)}
    return hashlib.sha256(json.dumps(records,sort_keys=True).encode()).hexdigest()


def classify(project,verdict):
    if not verdict.get('final_layout'):return verdict
    project=Path(project);tag=verdict.get('judged_run');issues=[]
    for leg,checks in [('drc',['A2_drc_full_deck','A5_antenna']),('lvs',['A3_lvs']),('rcx',['A10_rcx'])]:
        p=project/'reports'/(leg+'.json')
        if not p.exists():continue # missing acceptance obligations remain UNKNOWN in the common gate
        raw=load(p);bound=raw.get('run_tag') or (raw.get('provenance') or {}).get('run_tag')
        status=str(raw.get('status','unknown')).lower()
        measured_failure=(leg=='drc' and status in ('fail','failed','violations')
            and type(raw.get('total_violations')) in (int,float) and raw['total_violations']>=0)
        measured_failure|=(leg=='lvs' and status in ('fail','failed','mismatch')
            and ((type(raw.get('mismatch_count')) in (int,float) and raw['mismatch_count']>0)
                 or (raw.get('log_info') or {}).get('log_status')=='mismatch'
                 or (raw.get('log_info') or {}).get('match') is False))
        missing_count=leg=='drc' and status in ('fail','failed','violations') and not measured_failure
        if (status in UNKNOWN_STATUSES and not measured_failure) or missing_count or (bound and bound!=tag):
            issues.append({'leg':leg,'checks':checks,'status':status,'exit_code':raw.get('exit_code'),
                'reason':raw.get('reason'),'run_tag':bound,'bound_to_judged_run':bound==tag,
                'report':str(p),'report_sha256':sha(p)})
    if not issues:return verdict
    out=copy.deepcopy(verdict)
    out['accepted']=False
    out['technical_signoff']={'schema':VERSION,'kind':
        'CHECKER_TIMEOUT' if all(v['exit_code']==124 and v['bound_to_judged_run'] for v in issues) else 'CHECKER_UNVERIFIED',
        'judged_run':tag,'issues':issues}
    if out.get('acceptance'):
        acc=out['acceptance'];out['raw_acceptance_before_integrity']=copy.deepcopy(acc)
        affected={c for issue in issues for c in issue['checks']}
        acc['accepted']=False
        acc['unknown']=sorted(set(acc.get('unknown') or [])|affected)
        acc['failed']=[k for k in acc.get('failed') or [] if k not in affected]
        for key in affected:acc.setdefault('checks',{})[key]='unknown'
    return out


def known_checker_stop(project,verdict):
    if (Path(project)/'reports/t2a-workspace-unsafe.json').exists():return False
    if ((verdict.get('acceptance') or {}).get('checks') or {}).get('A11_provenance')=='fail':return False
    if verdict.get('execution_recovery') not in (None,'VERIFIED','PARTIAL_VERIFIED'):return False
    technical=verdict.get('technical_signoff') or {}
    if technical.get('schema')!=VERSION or technical.get('kind')!='CHECKER_TIMEOUT':return False
    issues=technical.get('issues') or []
    if not issues:return False
    for row in issues:
        p=Path(row['report'])
        if not p.resolve().is_relative_to(Path(project).resolve()) or not p.is_file() or sha(p)!=row['report_sha256']:
            return False
        data=load(p)
        if data.get('exit_code')!=124 or data.get('run_tag')!=technical['judged_run']:return False
    return True


def effective_episode(project):
    """Read-time classification; the original episode bytes remain unchanged."""
    project=Path(project);e=load(project/'episode.json')
    final=e.get('final') or e.get('initial') or {}
    checked=classify(project,final)
    if checked.get('technical_signoff'):
        e=copy.deepcopy(e);e['raw_status']=e.get('status')
        e['status']='UNVERIFIED_OR_SAFETY_STOP';e['terminal_reason']='unverified_checker'
        e['reviewed_verdict']=checked
    return e


def install(H):
    if getattr(H,'_SIGNOFF_INTEGRITY',None)==VERSION:return
    original=H.final_verdict;cache={}
    # Own leg watchdogs are unchanged. The outer supervisor must accommodate
    # DRC (2 h), max auto-scaled LVS (8 h), RCX (1 h) and normalization/grace.
    H.FINAL_SIGNOFF_TIMEOUT_S=max(H.FINAL_SIGNOFF_TIMEOUT_S,7200+28800+3600+1800)
    def verify(project,env,log):
        project=Path(project);key=(str(project.resolve()),fingerprint(project,env))
        if key in cache:
            rc,_=H._ledger(project,'event','--type','verification_reuse','--json',json.dumps({
                'policy':VERSION,'fingerprint':key[1],'judged_run':cache[key].get('judged_run'),
                'new_checker_invocations':0,'source':'actual verification earlier in this worker'}))
            if rc:raise RuntimeError('Cannot record verification reuse')
            return copy.deepcopy(cache[key])
        result=classify(project,original(project,env,log))
        cache[(str(project.resolve()),fingerprint(project,env))]=copy.deepcopy(result)
        return result
    H.final_verdict=verify;H._SIGNOFF_INTEGRITY=VERSION
