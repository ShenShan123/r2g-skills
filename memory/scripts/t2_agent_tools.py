"""Read-only discovery and checked execution of existing R2G tools.

All arms see the same public catalogue. Discovery does not run a repair; each
selection names one currently offered, config-bound action. Native memory uses
its separate eligibility policy and never enters the public catalogue here.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import resource
import subprocess
import sys
import time

import t2a_runtime as T
import t2a_envelope as E
from t2_agent_dispatch import IntegrityStop

MEMORY_SOURCES={'tehm_rule','tehm_compose','recipe'}


def public_env(env):
    clean=dict(env)
    for key in ('TEHM_DB','TEHM_ARTIFACTS_ROOT','R2G_RECIPE_FILE','R2G_TEHM_V2_POLICY',
                'R2G_TEHM_PRIORITY_FILE','R2G_TEHM_AGENT_CONSUMER'):
        clean.pop(key,None)
    clean.update(R2G_MEMORY_BACKEND='none',R2G_BUDGET_COMPONENT='catalogue')
    return clean


class PublicTools:
    def __init__(self,H,project,env):
        self.H,self.project,self.env=H,Path(project),public_env(env)
        sys.path.insert(0,str(H.SKILL/'knowledge'))
        self.used=set();self.offered={};self.check=None;self.config_sha=None

    def _query(self,check,apply=None):
        cmd=[sys.executable,str(self.H.SKILL/'scripts/reports/diagnose_signoff_fix.py'),
             str(self.project),'--check',check]
        cmd+=['--apply',apply] if apply else ['--list']
        start,cpu=time.time(),resource.getrusage(resource.RUSAGE_CHILDREN)
        try:p=subprocess.run(cmd,env=self.env,capture_output=True,text=True,timeout=120)
        except subprocess.TimeoutExpired as exc:
            T.event(self.H,self.project,'tool_call',{'tool':'public_repair_discovery','timed_out':True,
                    'elapsed_s':time.time()-start})
            raise IntegrityStop('Public repair discovery timed out') from exc
        after=resource.getrusage(resource.RUSAGE_CHILDREN)
        T.event(self.H,self.project,'tool_call',{'tool':'public_repair_apply' if apply else 'public_repair_discovery',
                'component':'catalogue','exit_status':p.returncode,'elapsed_s':time.time()-start,
                'cpu_s':after.ru_utime+after.ru_stime-cpu.ru_utime-cpu.ru_stime})
        with (self.project/'public-tool.log').open('a') as f:f.write(p.stderr+'\n'+p.stdout+'\n')
        if p.returncode:raise IntegrityStop('Public repair lookup/apply failed: '+str(p.returncode))
        try:plan=json.loads(p.stdout)
        except ValueError as exc:raise IntegrityStop('Public repair returned invalid JSON') from exc
        if plan.get('technical_failures'):raise IntegrityStop('Public repair candidate contract failed')
        return plan

    def discover(self):
        check,_,_=self.H._task_failure(self.project)
        check='route' if check=='orfs_stage' else check
        self.offered={};self.check=check
        self.config_sha=hashlib.sha256((self.project/'constraints/config.mk').read_bytes()).hexdigest()
        if check not in ('drc','lvs','route','timing'):return []
        plan=self._query(check)
        # The native producer has already applied the common knob/task gates.
        # Only immutable-input, config-edit repair actions fit this host tool.
        from candidate_contract import contract_errors
        for c in plan.get('strategies') or []:
            if c.get('source') in MEMORY_SOURCES:raise IntegrityStop('Memory leaked into public tool listing')
            if not c.get('auto_apply') or c.get('rerun_from') not in ('synth','floorplan','place','cts','route'):
                continue
            if c.get('sdc_edits') or c.get('env_edits'):continue
            if contract_errors(c):raise IntegrityStop('Invalid public candidate: '+str(c.get('id')))
            key=hashlib.sha256(json.dumps([check,self.config_sha,c],sort_keys=True).encode()).hexdigest()
            ident=c['id']+'@'+key[:12]
            if ident not in self.used:self.offered[ident]=c
        result=[{'tool_id':i,'strategy':c['id'],'applicable_check':self.check,
                 'rationale':c.get('rationale') or c.get('description'),
                 'set':{k:v for k,v in c['config_edits'].items() if v!='<unset>'},
                 'unset':[k for k,v in c['config_edits'].items() if v=='<unset>'],
                 'rerun_from':c['rerun_from'],'cost':'one flow plus checks; rollback recovery if rejected'}
                for i,c in self.offered.items()]
        T.event(self.H,self.project,'public_tools_offered',{'tools':result,'config_sha256':self.config_sha,
                'repair_executed':False,'cross_task_memory':False})
        return result

    def apply(self,ident):
        if ident not in self.offered or ident in self.used:raise IntegrityStop('Tool was not offered or already executed')
        if hashlib.sha256((self.project/'constraints/config.mk').read_bytes()).hexdigest()!=self.config_sha:
            raise IntegrityStop('Tool configuration changed after selection')
        expected=self.offered[ident]
        fresh=self._query(self.check)
        current=next((c for c in fresh.get('strategies') or [] if c['id']==expected['id']),None)
        if current!=expected:raise IntegrityStop('Public tool changed after discovery')
        self.used.add(ident)
        applied=self._query(self.check,expected['id'])
        if applied.get('status') in ('applied_no_op','no_effect'):return None,'no_change'
        if (applied.get('status')!='applied' or applied.get('applied')!=expected['id']
                or applied.get('config_edits')!=expected['config_edits'] or applied.get('sdc_edits')):
            raise IntegrityStop('Public executor did not apply the offered action')
        return expected['rerun_from'],applied
