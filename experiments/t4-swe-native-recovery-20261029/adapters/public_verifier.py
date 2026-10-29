"""Direct reuse is checked against native PUBLIC tests, never hidden issue tests.

No hard-coded edits or failure-to-action maps. If the public suite does not
witness the target failure, direct authority is unavailable and native reasoning
continues. Passing public regression alone is not full issue acceptance.
"""
import hashlib
import time
import uuid

from bridge import Verification,digest,stable
from memory_adapter import PatchReuse
from official import MAP_REPO_TO_PARSER,spec_for
from types import SimpleNamespace


class PublicDirectDriver:
    def __init__(self,agent,memory,state_builder,task):
        self.agent,self.memory,self.state_builder,self.task=agent,memory,state_builder,task
        self.pre=None
        self.driver=PatchReuse(agent,memory,state_builder,self.verify)

    def measure(self):
        cmd=spec_for(self.task)['test_cmd']
        stamp=uuid.uuid4().hex
        started=time.monotonic()
        # This uses only tests already present in the public base checkout.
        output=self.agent._env.communicate('cd /testbed; '+cmd,timeout=1800,check='ignore')
        parsed=MAP_REPO_TO_PARSER[self.task['repo']](output,SimpleNamespace(repo=self.task['repo']))
        path=self.memory.arm_root/'episodes'/('public-eval-'+stamp+'.json')
        data={'public_tests_only':True,'command':cmd,'output':output,'test_status':parsed,
              'wall_s':time.monotonic()-started,'hidden_test_patch_applied':False}
        path.write_text(stable(data)+'\n')
        self.memory.journal.append('public_verification_cost',task=self.task['instance_id'],
                                   wall_s=data['wall_s'],receipt=str(path))
        return parsed

    def verify(self,before,action,after,step):
        post=self.measure()
        pre=self.pre or {}
        failures=set(before.reports.get('tests',{}).get('public_failing_tests',[]))
        passed={'PASSED','XFAIL'}
        complete=bool(pre) and set(pre)<=set(post)
        removed=complete and bool(failures) and all(post[k] in passed for k in failures)
        regressions=[k for k,v in pre.items() if v in passed and post.get(k) in {'FAILED','ERROR'}]
        verdict='PASS' if complete and removed and not regressions else 'FAIL' if complete else 'UNKNOWN'
        scope='native-public-regression-not-full-issue-verification'
        path=self.memory.arm_root/'episodes'/('direct-verification-'+uuid.uuid4().hex+'.json')
        data={'producer':'native_evaluator','verdict':verdict,'oracle_type':'REGRESSION','scope':scope,
              'complete':complete,'before_measured':bool(pre),
              'original_failure_removed':removed if complete else None,'regressions':regressions,
              'before_digest':digest(before.canonical()),'after_digest':digest(after.canonical()),
              'action_digest':digest(action),'raw':{'public_pre':pre,'public_post':post,'hidden_tests':False}}
        path.write_text(stable(data)+'\n')
        return Verification(verdict,'REGRESSION',scope,complete,str(path),
            hashlib.sha256(path.read_bytes()).hexdigest(),bool(pre),data['original_failure_removed'],tuple(regressions))

    def __call__(self,choice,state,hook):
        if PatchReuse.validate(choice,state,verifier_available=True) is None:
            return None
        # A failure of unrelated public tests cannot stand in for the task's
        # target. No target identity means advice/native reasoning, not reuse.
        witnesses=state.reports.get('tests',{}).get('public_failing_tests',[])
        if not witnesses:
            self.memory.journal.append('local_reuse_rejected',task=self.task['instance_id'],
                reason='no public target-test identity bound to this state; hidden verdict never substituted')
            return None
        self.pre=self.measure()
        if not all(self.pre.get(k) in {'FAILED','ERROR'} for k in witnesses):
            self.memory.journal.append('local_reuse_rejected',task=self.task['instance_id'],
                reason='public suite does not witness the target failure; no extra authority inferred')
            return None
        return self.driver(choice,state,hook)
