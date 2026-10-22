"""Shared strict Table 2 candidate loop. No online learning, no additional repair opportunities.

Uses the existing metered provider client, hard-knob executor, ORFS supervisor,
global regression comparator and full acceptance checker. Legacy result runs are
not silently reinterpreted through this new loop.
"""
import json
import hashlib
import math
import os
from pathlib import Path
import re
import shutil
import sys
import time

import t2a_adapters as A
import t2a_envelope as E

ARMS = ('NB', 'TM0', 'RET', 'RECIPE', 'EXPEL', 'AWM', 'AMEM')
ONLINE_INPUT_BYTES = 96000  # O1, user-authorized common formal/DEV request body
MEMORY_CONTEXT_BYTES = 80000  # leave nominal room for evidence, history and JSON
REPORTS = ('drc.json', 'lvs.json', 'route.json', 'ppa.json', 'timing_check.json', 'rcx.json',
           'signoff_manifest.json', 'signoff_gate.json', 'acceptance.json')
ACCEPTANCE_CHECKS = ('A1_flow_complete','A2_drc_full_deck','A3_lvs','A4_route','A5_antenna',
                     'A6_setup','A7_hold','A8_drv_limits','A9_task_clock','A10_rcx','A11_provenance')


def terminal_status(final):
    """A failed restoration/unknown check is never an ordinary repair failure."""
    acceptance=final.get('acceptance') or {}
    checks=acceptance.get('checks') or {}
    # A producer/provenance failure cannot be repaired with design knobs and
    # is not evidence that the repair method failed. Preserve it as technical.
    if checks.get('A11_provenance')=='fail' or final.get('verification_stop') in (75,76,-9,124,137):
        return 'UNVERIFIED_OR_SAFETY_STOP'
    if (final.get('execution_recovery') not in (None,'VERIFIED')
            or (final.get('envelope') or {}).get('status')=='UNVERIFIED'
            or acceptance.get('unknown') or set(checks)!=set(ACCEPTANCE_CHECKS)
            or any(v not in ('pass','fail') for v in checks.values())):
        return 'UNVERIFIED_OR_SAFETY_STOP'
    if final.get('accepted'):
        if acceptance.get('accepted') is not True or any(v!='pass' for v in checks.values()):
            return 'UNVERIFIED_OR_SAFETY_STOP'
        return 'ACCEPTED'
    if acceptance.get('accepted') is True and (final.get('envelope') or {}).get('status')!='FAIL':
        return 'UNVERIFIED_OR_SAFETY_STOP'
    return 'NOT_ACCEPTED'


def online_policy():
    return {'revision':'O1','scope':'seven_arm_formal_and_dev_online',
            'input_bytes_ceiling':ONLINE_INPUT_BYTES,'memory_context_bytes':MEMORY_CONTEXT_BYTES,
            'output_tokens':800,'outer_output_ceiling':2000,'repair_generations':3,
            'transport_retries':0,'full_insights_and_workflows_required':True}


def configure_online(H):
    """Opt in a private seven-arm client; leave historical/general clients intact."""
    if H.LLM_MAX_TOKENS!=800 or H.LLM_OUTPUT_CEILING!=2000 or H.REPAIR_TURNS!=3:
        raise RuntimeError('Online sampling/output/repair policy drift')
    H.LLM_MAX_INPUT_BYTES=ONLINE_INPUT_BYTES
    return online_policy()


def event(H, project, kind, data):
    rc, _ = H._ledger(project, 'event', '--type', kind, '--json', A.stable(data))
    if rc:
        raise RuntimeError('Ledger event failed: ' + kind)


def restore(project, snapshot, execution_changed=False):
    """Restore just owned config/evidence/pointer; keep rejected artifacts and accounting."""
    manifest = json.loads((snapshot/'manifest.json').read_text())
    rejected = snapshot/'rejected'
    rejected.mkdir(exist_ok=True)
    for rel, existed in manifest.items():
        dst = project/rel
        if dst.exists():
            backup = rejected/rel
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, backup)
        if existed:
            dst.parent.mkdir(parents=True, exist_ok=True)
            temp = Path(str(dst)+'.t2a-restore')
            shutil.copy2(snapshot/rel, temp)
            temp.replace(dst)
        elif dst.exists():
            dst.unlink()  # preserved above; no ledger or raw run is removed
    if execution_changed:
        # Config/report rollback cannot restore ORFS's intermediate workspace.
        # No resume or final acceptance is allowed until a fresh charged flow.
        proof_path=snapshot/'baseline-execution.json'
        baseline=json.loads(proof_path.read_text()) if proof_path.exists() else None
        (project/'reports/t2a-workspace-unsafe.json').write_text(A.stable({
            'snapshot':str(snapshot),'reason':'rejected execution remains in .orfs-work',
            'baseline_partial':baseline})+'\n')


def snapshot_state(project, turn, env=None):
    dest = project/'reports/t2a-rollback'/('turn-%d'%turn)
    dest.mkdir(parents=True, exist_ok=False)
    paths = ['constraints/config.mk', 'constraints/constraint.sdc', 'backend/.r2g_signoff_run']
    paths += ['reports/'+n for n in REPORTS]
    manifest = {}
    for rel in paths:
        src = project/rel
        manifest[rel] = src.is_file()
        if src.is_file():
            dst = dest/rel; dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(src, dst)
    (dest/'manifest.json').write_text(A.stable(manifest)+'\n')
    (dest/'baseline-execution.json').write_text(A.stable(partial_execution_proof(project,env=env))+'\n')
    return dest


def _task_control_file(path):
    # Append-only accounting, fix history and raw request/tool logs never rewind.
    return (path.is_file() and path.suffix not in ('.jsonl','.log') and
            not path.name.endswith(('.request.json','.response.json')))


def task_entry_snapshot(project, env):
    """Save the pre-memory entry and consumable control reports, not raw history."""
    entry=snapshot_state(project,0,env)
    manifest=json.loads((entry/'manifest.json').read_text())
    for src in (project/'reports').iterdir():
        if not _task_control_file(src):continue
        rel=str(src.relative_to(project))
        if rel not in manifest:
            manifest[rel]=True
            dst=entry/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
    (entry/'manifest.json').write_text(A.stable(manifest)+'\n')
    return entry


def restore_task_entry(project, entry):
    manifest=json.loads((entry/'manifest.json').read_text())
    # Newly created non-convergence/rollback controls belong to the discarded
    # branch. Archive them; leave ledgers, raw runs and audit directories intact.
    for src in (project/'reports').iterdir():
        rel=str(src.relative_to(project))
        if _task_control_file(src) and rel not in manifest:
            dst=entry/'rejected'/rel;dst.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(src,dst);src.unlink()
    restore(project,entry,execution_changed=True)


def task_entry_fallback(H, project, env, entry, mark_boundary):
    """One charged restoration and a new no-memory catalogue process. No refund."""
    once=entry/'fallback-started.json'
    if once.exists():raise RuntimeError('Task entry fallback already attempted')
    with once.open('x') as output:output.write(A.stable({'started':time.time()})+'\n')
    fallback=dict(env)
    for key in ('TEHM_DB','TEHM_ARTIFACTS_ROOT','R2G_RECIPE_FILE','R2G_RECIPE_SUSPENDED',
                'R2G_TEHM_PRIORITY_FILE','R2G_FIX_EXCLUDE','R2G_FIX_RANK_FIRST'):
        fallback.pop(key,None)
    fallback.update(R2G_MEMORY_BACKEND='none',R2G_ENGINEER_BRANCH='task_fallback',
                    R2G_JOURNAL_DB=str(project/'task-fallback-journal.sqlite'))
    event(H,project,'task_entry_fallback',{'stage':'start','entry':str(entry),
          'reason':'retained memory branch did not reach complete acceptance','refund_repair_turns':False})
    restore_task_entry(project,entry)
    recovered=recover_execution(H,project,fallback)
    meta={'executed':True,'entry':str(entry),'restoration':recovered,'repair_turns_refunded':0}
    if recovered is None or not repair_continuation_verified(recovered):
        return recovered or {'accepted':False,'execution_recovery':'UNVERIFIED'},fallback,meta
    if recovered.get('accepted'):return recovered,fallback,meta
    log=project/'reports/fix_log.jsonl'
    offset=len(log.read_text().splitlines()) if log.exists() else 0
    shared=H.run_engineer(project,'NB',fallback)
    mark_boundary(project,fix_row_offset=offset)
    restored=recover_execution(H,project,fallback)
    if restored is not None:shared=restored
    event(H,project,'task_entry_fallback',{'stage':'end','accepted':shared.get('accepted'),
          'execution_recovery':shared.get('execution_recovery'),'refund_repair_turns':False})
    return shared,fallback,meta


def retained_memory_action(rows):
    return any(r.get('from_stage') and r.get('rolled_back') is False and
               (r.get('memory_rule') or str(r.get('strategy','')).startswith(('tehm_','recipe_')))
               for r in rows)


def candidate_execution_identity(manifest, snapshot, project, stage, pre):
    """Conservative same-state/same-input identity, not a cached repair verdict.

    A fresh recovery may have a new run tag but identical bound geometry. Never
    deduplicate when that geometry or literal inputs are unavailable. The cache
    is episode-local: no cross-task reuse of outcomes or acceptance authority.
    """
    layout = (pre.get('layout') or {}).get('def_digest')
    if not isinstance(layout, str) or not re.fullmatch(r'[0-9a-f]{64}', layout):
        return None
    try:
        inputs = {'manifest': manifest, 'baseline_layout': layout, 'from_stage': stage}
        for label, root in (('baseline', snapshot), ('candidate', project)):
            inputs[label] = {
                'config': E.effective_config((root / 'constraints/config.mk').read_text()),
                'sdc_sha256': hashlib.sha256((root / 'constraints/constraint.sdc').read_bytes()).hexdigest()}
        return hashlib.sha256(A.stable(inputs).encode()).hexdigest()
    except (OSError, ValueError, TypeError):
        return None


class TechnicalFailure(RuntimeError):
    """An invalid candidate or a crashed diagnose/consultation in the shared repair; the
    episode stops before any repair turn and the failure is surfaced (2026-10-17)."""


def technical_failures(project):
    path=Path(project)/'reports/technical_failures.jsonl'
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []


def raise_on_technical_failure(project):
    rows=technical_failures(project)
    if rows:
        raise TechnicalFailure('technical failure in shared repair: '+'; '.join(
            '%s/%s: %s'%(r.get('kind'),r.get('source'),str(r.get('detail'))[:160]) for r in rows))


def partial_execution_proof(project, run=None, env=None, full_clean=False):
    """Proven input-bound native infeasibility, NOT a verified final layout.

    Reuse the existing action-infeasibility vocabulary. Missing tools/models,
    unknown errors, killed stages, stale receipts and complete baselines do not
    qualify. For recovery require an unbroken clean synth-to-failure prefix.
    """
    try:
        env=env or {'R2G_TASK_MANIFEST':str(project/'manifest.json')}
        raw=Path(env['R2G_TASK_MANIFEST']).read_bytes();manifest=json.loads(raw)
        if not manifest.get('require_execution_binding'):
            return None
        if run is None:
            # Bind the CURRENT execution, never a stale pointer (failure-patterns "Turn-entry
            # proof bound a stale ppa.json run", 2026-10-17): ppa.json is not refreshed after a
            # recovery flow, so on gcd/u80 every turn snapshot pointed at the first (FLW-0024,
            # old-config) run and stored no proof. The newest run is the current execution;
            # it must still bind the current config below, or there is no proof at all.
            runs=sorted(p for p in (project/'backend').glob('RUN_*') if p.is_dir())
            if not runs:
                return None
            run=runs[-1]
        run=Path(run)
        if not run.resolve().is_relative_to((project/'backend').resolve()):
            return None
        if any((run/rel).exists() for rel in ('final/6_final.odb','final/6_final.gds',
                                             'results/6_final.odb','results/6_final.gds')):
            return None
        config=E.effective_config((project/'constraints/config.mk').read_text())
        if not E.check_config(manifest,config,project=project,env=env)['ok']:
            return None
        if not E.execution_binding(manifest,project,run,env):
            return None
        if full_clean:
            cleanup=json.loads((run/'cleanup.json').read_text())
            if cleanup!={'from_stage':'','clean_all_rc':0,'status':'PASS'}:
                return None
        stage_bytes=(run/'stage_log.jsonl').read_bytes()
        stages=[json.loads(line) for line in stage_bytes.splitlines() if line.strip()]
        order=['synth','floorplan','place','cts','route','finish']
        names=[s['stage'] for s in stages]
        start=0 if full_clean else order.index(names[0])
        if (names!=order[start:start+len(names)] or not stages
                or any(s['status']!=0 for s in stages[:-1]) or stages[-1]['status']!=2):
            return None
        log_bytes=(run/'flow.log').read_bytes()
        codes=re.findall(r'\[ERROR ([A-Z]+-\d+)\]',log_bytes.decode(errors='replace'))
        if not codes:
            return None
        memory=Path(__file__).resolve().parents[1]
        if str(memory) not in sys.path:
            sys.path.insert(0,str(memory))
        from tehm.adapters.r2g_evidence import ACTION_INFEASIBILITY_CODES
        if codes[-1] not in ACTION_INFEASIBILITY_CODES:
            return None
        return {'status':'KNOWN_INCOMPLETE','run_dir':str(run),'error_code':codes[-1],
                'failed_stage':names[-1],'stage_log_sha256':hashlib.sha256(stage_bytes).hexdigest(),
                'flow_log_sha256':hashlib.sha256(log_bytes).hexdigest(),
                'config_sha256':hashlib.sha256((project/'constraints/config.mk').read_bytes()).hexdigest(),
                'sdc_sha256':hashlib.sha256((project/'constraints/constraint.sdc').read_bytes()).hexdigest(),
                'manifest_sha256':hashlib.sha256(raw).hexdigest(),'full_clean':full_clean}
    except (OSError,ValueError,KeyError,IndexError,TypeError,AttributeError):
        return None


def _refresh_partial_evidence(H,project,env,run):
    # Preserve displaced signoff reports, but do not expose stale good checks to
    # the next observation/comparator or a terminal acceptance check.
    archive=project/'reports/t2a-restoration-displaced'/run.name
    archive.mkdir(parents=True,exist_ok=False)
    for name in REPORTS:
        path=project/'reports'/name
        if path.exists():
            shutil.move(str(path),str(archive/name))
    pointer=project/'backend/.r2g_signoff_run'
    if pointer.exists():
        shutil.move(str(pointer),str(archive/'.r2g_signoff_run'))
    for script,name in (('extract_ppa.py','ppa.json'),('extract_route.py','route.json')):
        rc=H.run(['python3',H.SKILL/'scripts/extract'/script,project,project/'reports'/name],
                 env,project/'eval_arm.log',600)
        if rc:
            return False
    try:
        ppa=json.loads((project/'reports/ppa.json').read_text())
        return ppa.get('run_dir')==str(run) and ppa.get('orfs_status') in ('fail','partial')
    except (OSError,ValueError):
        return False


def repair_continuation_verified(verdict):
    """Continuation authority is separate from terminal/signoff acceptance."""
    return verdict.get('execution_recovery') in ('VERIFIED','PARTIAL_VERIFIED')


def charge_usage(budget, usage):
    if isinstance(usage.get('total_tokens'), (int,float)):
        budget['tokens'] += usage['total_tokens']
    elif not any(usage.get(k) for k in ('budget_exhausted','refused','repair_allowance_exhausted')):
        budget['tokens_unknown'] = True
    if usage.get('accounting_error'):
        budget['accounting_error'] = usage['accounting_error']
    # The ledger is authoritative, especially for charged transport retries.


def request_token_totals(events):
    """All episode network requests, not just the successful candidate's usage.

    Read the arm's request ledger once: phase mirrors, holds and cache subcategories
    are not extra costs. Missing usage stays UNKNOWN with known subtotals/coverage.
    """
    requests=[e for e in events if e.get('type')=='llm_request']
    known_in=known_out=complete=0
    inputs=[];outputs=[]
    for request in requests:
        usage=request.get('usage_reported')
        usage=usage if isinstance(usage,dict) else {}
        values=[]
        for key in ('prompt_tokens','completion_tokens'):
            value=usage.get(key)
            valid=(isinstance(value,(int,float)) and not isinstance(value,bool)
                   and math.isfinite(value) and value>=0)
            values.append(value if valid else None)
        inputs.append(values[0]);outputs.append(values[1])
        if values[0] is not None:known_in+=values[0]
        if values[1] is not None:known_out+=values[1]
        complete+=all(v is not None for v in values)
    input_tokens=known_in if all(v is not None for v in inputs) else 'UNKNOWN'
    output_tokens=known_out if all(v is not None for v in outputs) else 'UNKNOWN'
    return {'llm_requests':len(requests),
            'repair_requests':sum(r.get('role')=='repair' for r in requests),
            'auxiliary_requests':sum(r.get('role')=='auxiliary' for r in requests),
            'input_tokens':input_tokens,'output_tokens':output_tokens,
            'total_tokens':known_in+known_out if complete==len(requests) else 'UNKNOWN',
            'known_input_tokens':known_in,'known_output_tokens':known_out,
            'requests_with_complete_usage':complete,'unknown_usage_requests':len(requests)-complete,
            'measurement_complete':complete==len(requests)}


def _charged_recovery(project, run, code):
    """One new recovery outcome, not a cached/unmetered reconstruction."""
    try:
        events=[json.loads(line) for line in
                (project/'reports/resource_ledger.jsonl').read_text().splitlines() if line]
        flows=[e for e in events if e.get('type')=='flow_run']
        outcomes=[e for e in flows if e.get('run_dir')==str(run)]
        return (len(outcomes)==1 and outcomes[0].get('component')=='rollback_recovery'
                and outcomes[0].get('rc')==code and not outcomes[0].get('budget_stop')
                and not outcomes[0].get('timeout')
                and sum(e.get('type')=='budget_charge' and e.get('resource')=='flows' for e in events)==len(flows))
    except (OSError,ValueError,TypeError):
        return False


def _completed_quality_failure(project, run, env, verdict):
    """Restored execution may be verified while its measured quality still fails.

    Only the new, fully clean/charged, input-bound completed flow qualifies.
    This never turns an area failure or missing checker into acceptance.
    """
    try:
        acc=verdict.get('acceptance') or {};checks=acc.get('checks') or {}
        stated=verdict.get('envelope') or {}
        if (verdict.get('accepted') is not False or verdict.get('final_layout') is not True
                or set(checks)!=set(ACCEPTANCE_CHECKS) or acc.get('unknown')
                or any(v not in ('pass','fail') for v in checks.values())
                or any(checks[k]!='pass' for k in ('A1_flow_complete','A11_provenance'))
                or stated.get('status')!='FAIL' or not (stated.get('config') or {}).get('ok')
                or (stated.get('area') or {}).get('unverified')):
            return False
        manifest=json.loads(Path(env['R2G_TASK_MANIFEST']).read_text())
        actual=E.envelope(manifest,project,run,env)
        area=actual.get('area') or {}
        if (actual.get('status')!='FAIL' or not (actual.get('config') or {}).get('ok')
                or area.get('unverified') or not area.get('violations')
                or any(v.get('kind')!='area_cap_exceeded' for v in area['violations'])):
            return False
        if json.loads((run/'cleanup.json').read_text())!={'from_stage':'','clean_all_rc':0,'status':'PASS'}:
            return False
        stages=[json.loads(line) for line in (run/'stage_log.jsonl').read_text().splitlines() if line]
        if ([s['stage'] for s in stages]!=['synth','floorplan','place','cts','route','finish']
                or any(s['status']!=0 for s in stages)):
            return False
        return (run.resolve().is_relative_to((project/'backend').resolve())
                and any((run/rel).is_file() for rel in ('final/6_final.odb','final/6_final.gds'))
                and _charged_recovery(project,run,0))
    except (OSError,ValueError,KeyError,TypeError,AttributeError):
        return False


def public_task_contract(manifest):
    """Public constraints only: no evaluator files, raw RTL or private answers."""
    return {'task':manifest.get('task'),
            'expected_clock_period_ns':manifest.get('expected_clock_period_ns'),
            'allowlist':manifest.get('allowlist',{}),
            'immutable_knobs':(manifest.get('immutable') or {}).get('knobs',[]),
            'rtl_and_sdc_immutable':True,
            'area':{name:{k:v for k,v in spec.items() if k in ('ref','max_rel_increase','unit')}
                    for name,spec in (manifest.get('area') or {}).items() if isinstance(spec,dict)},
            'required_checks':list(ACCEPTANCE_CHECKS)}


def envelope_feedback(envelope):
    """Recorded measured feedback, not a domain-specific repair hint."""
    area=envelope.get('area') or {}
    violations=(envelope.get('config') or {}).get('violations',[])+area.get('violations',[])
    return {'status':envelope.get('status'),'reason':envelope.get('reason'),
            'unverified_area':area.get('unverified',[]),
            'violations':[{k:v for k,v in item.items() if k in
                          ('kind','knob','metric','relative_change','cap')} for item in violations],
            'area':{name:{k:v for k,v in value.items() if k in ('final','ref','relative_change','cap')}
                    for name,value in area.get('metrics',{}).items()}}


def terminal_reason(final, repair_attempts=0):
    recovery=final.get('execution_recovery')
    if recovery=='PARTIAL_VERIFIED':
        return ('repair_allowance_exhausted_known_incomplete' if repair_attempts>=3
                else 'known_incomplete_before_allowance_exhaustion')
    if recovery not in (None,'VERIFIED'):
        return 'restoration_unverified_or_safety_stop'
    status=terminal_status(final)
    if status=='ACCEPTED':return 'strict_acceptance'
    if status=='NOT_ACCEPTED':
        return 'measured_area_or_config_failure' if (final.get('envelope') or {}).get('status')=='FAIL' else 'measured_acceptance_failure'
    return 'incomplete_or_inconsistent_verification'


def recover_execution(H, project, env, final=False):
    marker=project/'reports/t2a-workspace-unsafe.json'
    if not marker.exists():return None
    marker_data=json.loads(marker.read_text())
    config_sha=hashlib.sha256((project/'constraints/config.mk').read_bytes()).hexdigest()
    sdc=project/'constraints/constraint.sdc'
    sdc_sha=hashlib.sha256(sdc.read_bytes()).hexdigest() if sdc.exists() else None
    failed=marker_data.get('failed_recovery') or {}
    if failed.get('config_sha256')==config_sha and failed.get('sdc_sha256')==sdc_sha:
        # A terminal check is not authority to retry an identical flow already
        # proved unable to restore this configuration. Preserve the stop and its
        # original cost; a new restore transaction/config has a new identity.
        event(H,project,'restoration_not_retried',{'config_sha256':config_sha,
              'recovery_rc':failed['recovery_rc'],'reason':'same restored configuration already failed'})
        return {'accepted':False,'execution_recovery':'UNVERIFIED',
                'recovery_rc':failed['recovery_rc'],'recovery_not_retried':True}
    purpose='final_acceptance' if final else 'repair'
    rc,raw=H._ledger(project,'remaining','--purpose',purpose)
    remaining=json.loads(raw) if rc==0 else {}
    left=remaining.get('wall_s')
    if rc or left=='UNKNOWN' or (left is not None and left<=H.KILL_GRACE_S):
        return {'accepted':False,'execution_recovery':'SAFETY_OR_ACCOUNTING_STOP'}
    timeout=max(1,min(28800,left-H.KILL_GRACE_S)) if left is not None else 28800
    recovery_env={**env,'FROM_STAGE':'','R2G_BUDGET_PURPOSE':purpose,
                  'R2G_BUDGET_COMPONENT':'rollback_recovery','R2G_VERIFICATION_PURPOSE':purpose}
    recovery_env.pop('R2G_RESUME_NO_CLEAN',None)
    before=set((project/'backend').glob('RUN_*'))
    code=H.run(['bash',H.FLOW/'run_orfs.sh',project,H.CFG['platform']],recovery_env,project/'eval_arm.log',timeout)
    event(H,project,'restored_state_reflow',{'rc':code,'purpose':purpose,'from_stage':'full_clean',
          'reason':'same restored config, not a new repair candidate'})
    if code:
        new_runs=set((project/'backend').glob('RUN_*'))-before
        baseline=marker_data.get('baseline_partial') or {}
        baseline_now=partial_execution_proof(project,Path(baseline['run_dir']),env) if baseline.get('run_dir') else None
        proof=partial_execution_proof(project,next(iter(new_runs)),env,full_clean=True) if code==2 and len(new_runs)==1 else None
        matching=bool(proof and baseline_now==baseline and baseline.get('status')=='KNOWN_INCOMPLETE' and all(
            baseline.get(k)==proof[k] for k in ('config_sha256','sdc_sha256','manifest_sha256')))
        charged=bool(proof and _charged_recovery(project,Path(proof['run_dir']),2))
        if matching and charged and _refresh_partial_evidence(H,project,recovery_env,Path(proof['run_dir'])):
            event(H,project,'restored_partial_execution',{'baseline':baseline,'recovery':proof,
                  'accepted':False,'continuation_only':True,'no_extra_repair_opportunity':True})
            marker.unlink()
            return {'accepted':False,'final_layout':False,'execution_recovery':'PARTIAL_VERIFIED',
                    'recovery_rc':code,'partial_execution':proof,'continuation_only':True}
        if code not in (75,76,-9,124,137):
            marker_data['failed_recovery']={'config_sha256':config_sha,'sdc_sha256':sdc_sha,'recovery_rc':code}
            temporary=marker.with_suffix('.tmp');temporary.write_text(A.stable(marker_data)+'\n')
            temporary.replace(marker)
        return {'accepted':False,'execution_recovery':'SAFETY_OR_ACCOUNTING_STOP' if code==76 else
                'SAFETY_STOP' if code in (75,-9,124,137) else 'UNVERIFIED',
                'recovery_rc':code}
    H.run(['python3',H.SKILL/'scripts/extract/extract_ppa.py',project,project/'reports/ppa.json'],
          recovery_env,project/'eval_arm.log',600)
    verdict=H.final_verdict(project,recovery_env,project/'eval_arm.log')
    new_runs=set((project/'backend').glob('RUN_*'))-before
    measured_quality_failure=bool(len(new_runs)==1 and
        _completed_quality_failure(project,next(iter(new_runs)),recovery_env,verdict))
    if ((verdict.get('envelope') or {}).get('status')!='PASS' or not verdict.get('final_layout')) and not measured_quality_failure:
        # A completed but unverifiable recovery is no authority to retry it again
        # during terminal verification. Preserve the first run and its full cost.
        marker_data['failed_recovery']={'config_sha256':config_sha,'sdc_sha256':sdc_sha,'recovery_rc':code}
        temporary=marker.with_suffix('.tmp');temporary.write_text(A.stable(marker_data)+'\n');temporary.replace(marker)
        return {**verdict,'accepted':False,'execution_recovery':'UNVERIFIED'}
    if measured_quality_failure:
        event(H,project,'restored_execution_quality_failure',{
            'run_dir':str(next(iter(new_runs))),'accepted':False,'continuation_only':True,
            'envelope':envelope_feedback(verdict['envelope'])})
    marker.unlink()  # marker only; all rejected runs, receipts and costs remain
    return {**verdict,'execution_recovery':'VERIFIED'}


def terminal_verdict(H,project,env):
    recovered=recover_execution(H,project,env,final=True)
    if recovered is not None:return recovered
    return H.final_verdict(project,{**env,'R2G_VERIFICATION_PURPOSE':'final_acceptance'},project/'eval_arm.log')


def strict_turns(H, project, arm, env, key, situation_module, memory=None):
    """At most three candidate-generating requests; auxiliary text is never executable."""
    if arm not in ARMS:
        raise ValueError('Unknown arm')
    tehm_agent = (arm == 'TM0' and env.get('R2G_TEHM_AGENT_CONSUMER')
                  and getattr(memory, 'arm', None) == 'TM0')
    if (arm in ('RET','EXPEL','AWM','AMEM') or bool(tehm_agent)) != (memory is not None):
        raise ValueError('Arm/representation mismatch')
    manifest_path = env.get('R2G_TASK_MANIFEST')
    if not manifest_path:
        raise ValueError('Strict episode needs the frozen constraint/area manifest')
    manifest = json.loads(Path(manifest_path).read_text())
    import sys
    sys.path.insert(0,str(H.SKILL/'knowledge'))
    import result_vector as V
    budget={'tokens':0}
    records=[]; last=None; history=[]; failed_executions={}
    for turn in range(1,H.REPAIR_TURNS+1):
        recovered=recover_execution(H,project,env)
        if recovered is not None:
            if not repair_continuation_verified(recovered):
                records.append({'turn':turn,'status':'RESTORATION_STOP','verdict':recovered});break
            last=recovered
            if recovered.get('accepted'):break
        rc, raw = H._ledger(project,'remaining')
        remaining=json.loads(raw) if rc==0 else {}
        if rc or remaining.get('wall_s') == 'UNKNOWN':
            records.append({'turn':turn,'status':'ACCOUNTING_STOP'});break
        if remaining.get('wall_s') is not None and remaining['wall_s'] <= H.KILL_GRACE_S:
            records.append({'turn':turn,'status':'SAFETY_STOP'});break
        if budget.get('tokens_unknown') or budget.get('accounting_error'):
            records.append({'turn':turn,'status':'ACCOUNTING_STOP'});break
        check, vclass, count=H._task_failure(project)
        sit=situation_module.from_project(project,check,vclass,count)
        observation=H.observation(project)
        observation['task_contract']=public_task_contract(manifest)
        runs=sorted((project/'backend').glob('RUN_*'))
        if runs:
            observation['task_quality_feedback']=envelope_feedback(E.envelope(manifest,project,runs[-1],env))
        obs=A.stable(observation)
        system=H.llm_system(H.CFG['platform'],manifest)
        base_messages=[{'role':'system','content':system}, *history[-4:],
                       {'role':'user','content':'Current evidence:\n'+obs+'\nReturn one repair candidate.'}]
        def auxiliary(query):
            content, usage=H.llm_call(key,[{'role':'system','content':'Return JSON {"keywords": ["relevant failure keywords"]}. '
                   'Only extract query keywords. Do not propose edits or executable actions.'},
                   {'role':'user','content':query}],project/('amem-query-%d'%turn),ledger=project,role='auxiliary')
            charge_usage(budget,usage)
            if content is None or budget.get('tokens_unknown') or budget.get('accounting_error'):
                raise RuntimeError('A-MEM auxiliary request stopped')
            obj=json.loads(content)
            if not isinstance(obj,dict) or set(obj)!={'keywords'} or not isinstance(obj['keywords'],list) or not all(
                  isinstance(k,str) for k in obj['keywords']):
                raise ValueError('A-MEM query produced something other than keywords')
            return obj['keywords']
        if memory is not None:
            t0,cpu=time.time(),time.process_time()
            # The final provider preflight checks the fully serialized body. No
            # truncation of full insight/workflow sets, and no hidden empty-memory fallback.
            context,ids=memory.context(obs,sit,auxiliary=auxiliary,max_bytes=MEMORY_CONTEXT_BYTES)
            event(H,project,'memory_retrieval',{'arm':arm,'turn':turn,'unit_ids':ids,
                  'context_bytes':len(context.encode()),'wall_s':time.time()-t0,'cpu_s':time.process_time()-cpu})
            base_messages[-1]['content']+='\nFrozen historical memory (not current-task evidence):\n'+context
        agent_receipt = None
        if tehm_agent:
            agent_receipt=memory.request_receipt(base_messages,turn)
            event(H,project,'tehm_agent_request_planned',agent_receipt)
        content,usage=H.llm_call(key,base_messages,project/('repair-turn-%d'%turn),ledger=project,role='repair')
        if agent_receipt is not None:
            from t2_tehm_agent_memory import verify_request
            consumed=verify_request(project,agent_receipt)
            event(H,project,'tehm_agent_consumption',consumed)
        charge_usage(budget,usage)
        if content is None:
            records.append({'turn':turn,'status':'REQUEST_STOP','usage':usage});break
        state=snapshot_state(project,turn,env=env)
        pre=V.capture(project)
        stage,applied=H.apply_llm(project,content,turn=usage.get('repair_turn_id'),manifest=manifest)
        row={'turn':turn,'repair_turn_id':usage.get('repair_turn_id'),'usage':usage,
             'situation':sit,'proposal':content,'flow_launched':False,'rolled_back':False}
        if agent_receipt is not None:
            row['memory_consumption']=consumed
        if stage is None:
            row['status']='REJECTED_PROPOSAL';row['reason']=applied
            records.append(row);event(H,project,'repair_candidate',row)
            history += [{'role':'assistant','content':content}, {'role':'user','content':
                        'The candidate was not executed: '+str(applied)+'. Its repair turn is spent.'}]
            continue
        cfg=E.effective_config((project/'constraints/config.mk').read_text())
        allowed=E.check_config(manifest,cfg,project=project,env=env)
        if not allowed['ok']:
            restore(project,state);row.update(status='CONSTRAINT_REJECTION',rolled_back=True,violations=allowed['violations'])
            records.append(row);event(H,project,'repair_candidate',row)
            history += [{'role':'assistant','content':content},{'role':'user','content':'Forbidden constraint change: '+A.stable(allowed)}]
            continue
        identity=candidate_execution_identity(manifest,state,project,stage,pre)
        if identity is not None and identity in failed_executions:
            # Generation still spends its turn and tokens. Repeating the same
            # measured failure is not authority for another EDA flow or recovery.
            restore(project,state)
            row.update(status='DUPLICATE_NOT_EXECUTED',applied=applied,
                       execution_identity=identity,duplicate_of_turn=failed_executions[identity],
                       reason='identical candidate already failed from the same bound state')
            records.append(row);event(H,project,'repair_candidate',row)
            history += [{'role':'assistant','content':content},{'role':'user','content':
                'This identical execution already failed on turn %d from the same bound state. '
                'No flow was launched; this repair turn and its model cost are still spent.' % failed_executions[identity]}]
            continue
        limit=remaining.get('wall_s')
        timeout=min(28800,limit-H.KILL_GRACE_S) if limit is not None else 28800
        flow_env={**env,'R2G_BUDGET_COMPONENT':'llm_repair','FROM_STAGE':stage}
        flow_rc=H.run(['bash',H.FLOW/'run_orfs.sh',project,H.CFG['platform']],flow_env,project/'eval_arm.log',max(1,timeout))
        row['flow_launched']=flow_rc!=75;row['flow_rc']=flow_rc
        if flow_rc in (75,76,-9,124,137):
            restore(project,state,execution_changed=flow_rc!=75)
            row.update(status='SAFETY_OR_ACCOUNTING_STOP' if flow_rc==76 else 'SAFETY_STOP',rolled_back=True)
            records.append(row);event(H,project,'repair_candidate',row);break
        H.run(['python3',H.SKILL/'scripts/extract/extract_ppa.py',project,project/'reports/ppa.json'],env,project/'eval_arm.log',600)
        verdict=H.final_verdict(project,{**env,'R2G_VERIFICATION_PURPOSE':'repair'},project/'eval_arm.log')
        comparison=V.compare(pre,V.capture(project),check)
        # Unknown is not a measured failure or a safety pass. Unverified
        # executed candidates return to the pre-candidate state, with raw costs retained.
        unverified=bool(comparison['unknowns'] or (verdict.get('acceptance') or {}).get('unknown')
                        or (verdict.get('envelope') or {}).get('status')=='UNVERIFIED')
        reject=bool(flow_rc or comparison['regressions'] or unverified
                    or (verdict.get('envelope') or {}).get('status')=='FAIL')
        row.update(applied=applied,comparison=comparison,verdict=verdict,status='RETAINED' if not reject else
                   'UNVERIFIED' if unverified else 'REGRESSION' if comparison['regressions'] else 'FAILED')
        if identity is not None and flow_rc==0 and reject and not unverified and terminal_status(verdict)=='NOT_ACCEPTED':
            failed_executions[identity]=turn
            row['execution_identity']=identity
        if reject:
            restore(project,state,execution_changed=True);row['rolled_back']=True
        else:
            last=verdict
        records.append(row);event(H,project,'repair_candidate',row)
        history += [{'role':'assistant','content':content},{'role':'user','content':A.stable({
             'accepted':verdict['accepted'],'status':row['status'],'comparison':comparison,
             'failed':(verdict.get('acceptance') or {}).get('failed'),
             'envelope':envelope_feedback(verdict.get('envelope') or {}),'rolled_back':row['rolled_back']})}]
        if not reject and verdict['accepted']:
            break
    return records,last


def accounting_check(events, run_dirs):
    """Fail-closed formal-run gate; all records/holds, not just successful episodes."""
    flows=[e for e in events if e.get('type')=='flow_run']
    charges=[e for e in events if e.get('type')=='budget_charge' and e.get('resource')=='flows']
    requests=[e for e in events if e.get('type')=='llm_request']
    settled={e['hold_id'] for e in events if e.get('type')=='budget_settle'}
    holds={e['hold_id'] for e in events if e.get('type')=='budget_hold'}
    errors=[]
    if (len(flows)!=len(charges) or len(flows)!=len(run_dirs) or len({e['run_dir'] for e in flows})!=len(flows)
        or {e['run_dir'] for e in flows} != {str(p) for p in run_dirs}):
        errors.append('flow/run/charge correspondence')
    if holds-settled:errors.append('open holds')
    turns=sum(e.get('actual',{}).get('repair_attempts',0) for e in events if e.get('type')=='budget_settle')
    if turns>3:errors.append('repair opportunities exceeded')
    for r in requests:
        usage=r.get('usage_reported')
        if (r.get('model_mismatch') or r.get('model_reported') not in ('deepseek-flash','deepseek-v4.1-flash')
            or r.get('request_bytes',float('inf'))>ONLINE_INPUT_BYTES or not isinstance(usage,dict)
            or not all(isinstance(usage.get(k),(int,float)) and not isinstance(usage.get(k),bool)
                       and math.isfinite(usage[k]) and usage[k]>=0 for k in ('prompt_tokens','completion_tokens'))):
            errors.append('provider identity/input/usage violation')
    return {'passed':not errors,'errors':errors,'flows':len(flows),'llm_requests':len(requests),
            'repair_attempts':turns,'zero_repair_success_possible':turns==0,
            'zero_online_requests':len(requests)==0}
