"""Agent-study terminal handling for proven native physical infeasibility.

The older experiment's terminal rules remain intact. A necessary flow-complete
condition can be false while later signoff checks were never measured. This
module records that distinction without manufacturing pass/fail signoff data.
"""
from __future__ import annotations
import json
from pathlib import Path
import t2a_runtime as T

SCHEMA='agent-native-infeasibility-v1'


def native_failure(project,env):
    project=Path(project)
    if (project/'reports/t2a-workspace-unsafe.json').exists():return None
    proof=T.partial_execution_proof(project,env=env)
    if not proof:return None
    try:
        events=[json.loads(l) for l in (project/'reports/resource_ledger.jsonl').read_text().splitlines() if l.strip()]
        report=T.accounting_check(events,list((project/'backend').glob('RUN_*')))
        outcomes=[e for e in events if e.get('type')=='flow_run' and e.get('run_dir')==proof['run_dir']]
        if (not report['passed'] or len(outcomes)!=1 or outcomes[0].get('rc')!=2
                or outcomes[0].get('timeout') or outcomes[0].get('budget_stop')):return None
        return {'schema':SCHEMA,'proof':proof,'flow_outcome':outcomes[0],
                'accounting':report,'success_condition_failed':'A1_flow_complete',
                'unmeasured_checks':[k for k in T.ACCEPTANCE_CHECKS if k!='A1_flow_complete']}
    except (OSError,ValueError,TypeError,KeyError):return None


def attach_failure(project,env,verdict):
    if verdict.get('accepted') or verdict.get('final_layout'):return verdict
    if verdict.get('verification_stop') or verdict.get('execution_recovery') not in (None,'VERIFIED','PARTIAL_VERIFIED'):
        return verdict
    receipt=native_failure(project,env)
    return {**verdict,'accepted':False,'native_failure_receipt':receipt} if receipt else verdict


def terminal_verdict(H,project,env):
    return attach_failure(project,env,T.terminal_verdict(H,project,env))


def terminal_status(verdict):
    if verdict.get('technical_signoff'):
        return 'UNVERIFIED_OR_SAFETY_STOP'
    r=verdict.get('native_failure_receipt') or {}
    if (r.get('schema')==SCHEMA and (r.get('accounting') or {}).get('passed')
            and (r.get('proof') or {}).get('status')=='KNOWN_INCOMPLETE'
            and (r.get('flow_outcome') or {}).get('rc')==2 and verdict.get('accepted') is False
            and not verdict.get('final_layout') and not verdict.get('verification_stop')
            and verdict.get('execution_recovery') in (None,'VERIFIED','PARTIAL_VERIFIED')):
        return 'NOT_ACCEPTED'
    return T.terminal_status(verdict)


def terminal_reason(verdict,requests=0):
    if verdict.get('native_failure_receipt') and terminal_status(verdict)=='NOT_ACCEPTED':
        return 'measured_native_infeasibility'
    return T.terminal_reason(verdict,requests)
