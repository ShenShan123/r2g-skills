"""Explicit fixture choices, not evidence of learned transfer or SWE-bench performance."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
os.environ['LITELLM_LOCAL_MODEL_COST_MAP']='True'
sys.path[:0]=[str(ROOT/'adapters'),str(ROOT/'sources/SWE-agent')]


def fixture(tmp_path, replacement):
    from bridge import FullMemory,State,digest,stable,Verification
    from memory_adapter import attach,PatchReuse
    from sweagent.agent.agents import DefaultAgent,DefaultAgentConfig
    from sweagent.tools.tools import ToolConfig
    from sweagent.tools.parsing import ThoughtActionParser
    from tehm.ids import stable_dumps
    repo=tmp_path/'repo';repo.mkdir()
    subprocess.run(['git','init','-q',str(repo)],check=True)
    (repo/'a.py').write_text('def answer(): return 1\n')
    subprocess.run(['git','-C',str(repo),'add','a.py'],check=True)
    subprocess.run(['git','-C',str(repo),'-c','user.name=offline-fixture','-c','user.email=offline@example.invalid',
                    'commit','-qm','fixture'],check=True)
    memory=FullMemory('swe-agent',root=tmp_path)
    def state():
        diff=subprocess.check_output(['git','-C',str(repo),'diff','--binary','HEAD'],text=True)
        return State('OFFLINE-FIXTURE','OFFLINE-FIXTURE','software','python','fixture_test',{}, {'git_diff':diff},{})
    class NativeEnvFixture:
        calls=[]
        def communicate(self,input,**kwargs):
            assert repo.resolve().is_relative_to((ROOT/'reports').resolve())
            self.calls.append(input)
            cmd=['/usr/bin/bwrap','--unshare-all','--die-with-parent','--new-session',
                 '--ro-bind','/usr','/usr','--ro-bind','/lib','/lib','--ro-bind','/lib64','/lib64',
                 '--symlink','usr/bin','/bin','--proc','/proc','--dev','/dev','--tmpfs','/tmp',
                 '--bind',str(repo),str(repo),'--chdir',str(repo),'--clearenv',
                 '--setenv','PATH','/usr/bin:/bin','--','/bin/bash','-c',input]
            result=subprocess.run(cmd,capture_output=True,text=True,timeout=30)
            return result.stdout+result.stderr
    env=NativeEnvFixture()
    agent=DefaultAgent.from_config(DefaultAgentConfig(model={'name':'instant_empty_submit'},
        tools=ToolConfig(parse_function=ThoughtActionParser())))
    agent._env=env;agent.tools.mock_state={'working_dir':str(repo)}
    hook=attach(agent,memory,state)
    patch=('diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n'
           '-def answer(): return 1\n+def answer(): return '+str(replacement)+'\n')
    action={'domain':'software.GIT_PATCH','transformation_family':'native_patch','payload':{'git_patch':patch}}
    choice={'mode':'direct_reuse','routing':{'decision':'APPLY'},'selected_ids':['EXPLICIT-UNIT-FIXTURE'],
            'action':action,'direct_proposal':{'candidate_id':'EXPLICIT-UNIT-FIXTURE',
              'binding':{'status':'BOUND','lifecycle_status':'promoted','action':action,
                         'proof':{'target_context_digest':hashlib.sha256(stable_dumps(state().context().to_dict()).encode()).hexdigest()}},
              'obligations':[{'obligation':'PRESERVE_FROZEN_REGRESSION','status':'SYNTHESIZABLE'}]}}
    def verifier(before,action,after,step):
        output=env.communicate("python3 -c 'import a; assert a.answer()==2; print(\"FIXTURE_PASS\")'")
        passed='FIXTURE_PASS' in output
        path=memory.arm_root/'episodes/fixture-verification.json'
        data={'producer':'native_evaluator','verdict':'PASS' if passed else 'FAIL',
              'oracle_type':'REGRESSION','scope':'OFFLINE-FIXTURE:python-assert',
              'complete':True,'before_measured':True,'original_failure_removed':passed,'regressions':[],
              'before_digest':digest(before.canonical()),'after_digest':digest(after.canonical()),
              'action_digest':digest(action),'raw':{'output':output,'explicit_fixture_not_benchmark':True}}
        path.write_text(stable(data))
        return Verification(data['verdict'],'REGRESSION',data['scope'],True,str(path),
                            hashlib.sha256(path.read_bytes()).hexdigest(),True,passed)
    return memory,agent,state,hook,choice,verifier,repo,env


@pytest.mark.parametrize('replacement',[2,3])
def test_patch_uses_native_handler_and_failed_trial_restores_entry(tmp_path,replacement):
    from memory_adapter import PatchReuse
    m,a,state,hook,choice,verify,repo,env=fixture(tmp_path,replacement)
    driver=PatchReuse(a,m,state,verify);entry=state()
    try:
        result=driver(choice,entry,hook)
        events=[json.loads(l) for l in m.journal.path.read_text().splitlines()]
        writes=[e for e in events if e['event']=='memory_write']
        assert len(writes)==1 and writes[0]['rolled_back'] is (replacement!=2)
        assert (result is not None) is (replacement==2)
        if replacement==2:
            assert writes[0]['outcome']=='PASS' and 'return 2' in (repo/'a.py').read_text()
        else:
            assert state().config==entry.config and writes[0]['outcome']!='PASS'
        assert a.model.stats.api_calls==0
        assert driver(choice,state(),hook) is None  # stale/repeated action never loops
    finally:m.close()


def test_verifier_exception_rolls_back_before_technical_stop(tmp_path):
    from memory_adapter import PatchReuse
    m,a,state,hook,choice,verify,repo,env=fixture(tmp_path,2)
    def broken(*args):raise RuntimeError('fixture verifier unavailable')
    entry=state()
    try:
        with pytest.raises(RuntimeError):PatchReuse(a,m,state,broken)(choice,entry,hook)
        assert state().config==entry.config and 'return 1' in (repo/'a.py').read_text()
    finally:m.close()
