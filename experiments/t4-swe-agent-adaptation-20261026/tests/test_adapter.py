import copy
import json
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
sys.path[:0] = [str(ROOT/'adapters'), str(ROOT/'sources/SWE-agent')]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket
    def reject(*a, **kw):
        raise AssertionError('offline preparation must never use network')
    monkeypatch.setattr(socket.socket, 'connect', reject)
    monkeypatch.setattr(socket.socket, 'connect_ex', reject)


def test_native_and_tehm_stores_are_independent_and_no_skill_uses_native_forward(tmp_path):
    from bridge import FullMemory, State
    from memory_adapter import attach
    native = FullMemory('swe-agent', arm='native', root=tmp_path)
    tehm = FullMemory('swe-agent', arm='tehm', root=tmp_path)
    class Agent:
        def __init__(self): self.calls=[];self.hooks=[]
        def forward(self, history):self.calls.append(copy.deepcopy(history));return 'native-result'
        def add_hook(self, hook):hook.on_init(agent=self);self.hooks.append(hook)
    state = State('task', 'repo', 'software', 'swe-agent:python', 'issue_tests',
                  {'tests': {'status': 'UNKNOWN'}}, {'git_diff': ''}, {})
    history=[{'role':'user','content':'public issue'}]
    try:
        for memory in (native,tehm):
            a=Agent();attach(a,memory,lambda:state)
            assert a.forward(history)=='native-result'
            assert a.calls==[history] and history==[{'role':'user','content':'public issue'}]
        assert native.backend is None
        assert native.arm_root!=tehm.arm_root
        events=[json.loads(l) for l in tehm.journal.path.read_text().splitlines()]
        consultations=[e for e in events if e['event']=='memory_consult']
        assert consultations[-1]['routing']['decision']=='NO_SKILL'
    finally: native.close();tehm.close()


def test_advice_is_ephemeral_and_does_not_replace_native_history(tmp_path):
    from bridge import Journal,State
    from memory_adapter import attach
    class Memory:
        arm='tehm';journal=Journal(tmp_path/'events.jsonl')
        def consult(self,state):return {'mode':'experience_assisted','context':'one scoped hint',
                                       'selected_ids':['verified-reference']}
    class Agent:
        def add_hook(self,h):h.on_init(agent=self)
        def forward(self,h):self.got=h;return 'native'
    a=Agent();s=State('t','r','software','p','c',{}, {},{})
    attach(a,Memory(),lambda:s)
    h=[{'role':'user','content':'issue'}];a.forward(h)
    assert a.got[-1]['content']=='one scoped hint' and len(h)==1


def test_arbitrary_shell_success_is_written_unknown_not_positive(tmp_path):
    from bridge import FullMemory,State
    from memory_adapter import attach
    from sweagent.types import StepOutput
    class Agent:
        def forward(self,h):return None
        def add_hook(self,h):h.on_init(agent=self);self.hook=h
    m=FullMemory('swe-agent',root=tmp_path)
    s=State('t','r','software','p','issue_tests',{'tests':{'status':'UNKNOWN'}}, {'git_diff':''},{})
    try:
        a=Agent();attach(a,m,lambda:s)
        step=StepOutput(action='echo ok',observation='ok',execution_time=.01)
        a.hook.on_action_started(step=step);a.hook.on_action_executed(step=step)
        rows=[json.loads(l) for l in m.journal.path.read_text().splitlines()]
        writes=[r for r in rows if r['event']=='memory_write']
        assert len(writes)==1 and writes[0]['outcome']=='UNKNOWN'
    finally:m.close()


@pytest.mark.parametrize('mutation',['unpromoted','wrong_state','wrong_action','test_file','absolute','no_verifier'])
def test_local_reuse_rejects_unqualified_or_unsafe_patches(tmp_path,mutation):
    from bridge import State,digest
    from memory_adapter import PatchReuse
    s=State('t','r','software','p','issue_tests',{}, {'git_diff':''},{})
    patch='diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-bad\n+good\n'
    action={'domain':'software.GIT_PATCH','transformation_family':'native_patch',
            'payload':{'git_patch':patch}}
    from tehm.ids import stable_dumps
    import hashlib
    choice={'mode':'direct_reuse','routing':{'decision':'APPLY'},'selected_ids':['r1'],'action':action,
            'direct_proposal':{'candidate_id':'r1','binding':{'lifecycle_status':'promoted','status':'BOUND',
              'action':copy.deepcopy(action),'proof':{'target_context_digest':hashlib.sha256(stable_dumps(s.context().to_dict()).encode()).hexdigest()}},
              'obligations':[{'obligation':'PRESERVE_FROZEN_REGRESSION','status':'SYNTHESIZABLE'}]}}
    if mutation=='unpromoted':choice['direct_proposal']['binding']['lifecycle_status']='candidate'
    elif mutation=='wrong_state':choice['direct_proposal']['binding']['proof']['target_context_digest']='wrong'
    elif mutation=='wrong_action':choice['action']={**action,'payload':{'git_patch':patch+'x'}}
    elif mutation=='test_file':
        choice['action']['payload']['git_patch']=patch.replace('a.py','tests/test_a.py')
        choice['direct_proposal']['binding']['action']=copy.deepcopy(choice['action'])
    elif mutation=='absolute':
        choice['action']['payload']['git_patch']=patch.replace('a.py','/etc/hosts')
        choice['direct_proposal']['binding']['action']=copy.deepcopy(choice['action'])
    assert PatchReuse.validate(choice,s,verifier_available=mutation!='no_verifier') is None


def test_native_default_agent_step_executes_through_original_handler(tmp_path):
    from sweagent.agent.agents import DefaultAgent,DefaultAgentConfig
    from model_adapter import MeteredModel
    from sweagent.tools.tools import ToolConfig
    from sweagent.tools.parsing import ThoughtActionParser
    from sweagent.agent.problem_statement import TextProblemStatement
    from sweagent.environment.swe_env import SWEEnv,EnvironmentConfig
    from swerex.deployment.config import DummyDeploymentConfig
    from bridge import FullMemory,State
    from memory_adapter import attach
    env=SWEEnv.from_config(EnvironmentConfig(deployment=DummyDeploymentConfig(),repo=None));env.start()
    config=DefaultAgentConfig(model={'name':'instant_empty_submit'},tools=ToolConfig(parse_function=ThoughtActionParser()))
    agent=DefaultAgent.from_config(config)
    class OfflineProvider:
        model='deepseek-flash';blocked=False
        def request(self,body,**kwargs):
            return {'choices':[{'message':{'content':'Inspect files\n```bash\necho offline\n```'}}],
                    'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}}
    agent.model=MeteredModel(OfflineProvider(),config.tools)
    agent.tools.mock_state={'working_dir':'/testbed','open_file':''}
    memory=FullMemory('swe-agent',root=tmp_path)
    state=State('fixture','fixture','software','python','issue_tests',{}, {},{})
    try:
        attach(agent,memory,lambda:state)
        agent.setup(env=env,problem_statement=TextProblemStatement(text='OFFLINE FIXTURE ONLY'),output_dir=tmp_path/'native')
        step=agent.step()
        assert step.action.strip()=='echo offline' and len(agent.trajectory)==1
        assert agent.model.stats.api_calls==1
    finally:env.close();memory.close()
