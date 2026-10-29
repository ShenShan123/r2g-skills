"""Construct the pinned native host with the metered model and optional TEHM hook."""
from __future__ import annotations

import time

from sweagent.agent.agents import DefaultAgent, DefaultAgentConfig
from sweagent.tools.tools import ToolHandler

from bridge import State
from memory_adapter import PatchReuse, attach
from model_adapter import MeteredModel


class SoftwareState:
    """Only observable repo diff and verifier-owned feedback enter memory.

    `read_diff` is supplied by the qualified SWE-ReX task environment. Private
    evaluator answers and harness internals never become agent state.
    """
    def __init__(self,memory,task,read_diff):
        self.memory,self.task,self.read_diff=memory,task,read_diff
        self.check={'status':'UNKNOWN'}

    def __call__(self):
        start=time.monotonic()
        diff=self.read_diff()
        if not isinstance(diff,str):
            raise ValueError('repository snapshot must be text')
        self.memory.journal.append('memory_state_io',task=self.task['instance_id'],
                                   wall_s=time.monotonic()-start,component='state_capture')
        check=dict(self.check)
        if diff and check.get('status')=='FAIL':
            check={'status':'UNKNOWN','scope':'repository changed since qualified baseline; not re-verified'}
        return State(self.task['instance_id'],self.task['repo'],'software','swe-agent:python',
            'issue_tests',{'tests':check},
            {'git_diff':diff,'repository':self.task['repo'],'base_commit':self.task['base_commit']},
            {'observable_symptom':{'check':'issue_tests','class':check['status']}})


def build_agent(config,memory,provider,state_builder,verifier=None,direct_driver_factory=None):
    if not isinstance(config,DefaultAgentConfig):
        config=DefaultAgentConfig.model_validate(config)
    if config.action_sampler is not None:
        raise ValueError('multi-trajectory action sampling needs its own metered adapter; not prepared')
    model=MeteredModel(provider,config.tools)
    agent=DefaultAgent(templates=config.templates,tools=ToolHandler(config.tools),
        history_processors=config.history_processors,model=model,
        max_requeries=config.max_requeries,name=config.name)
    driver=PatchReuse(agent,memory,state_builder,verifier) if verifier else None
    if direct_driver_factory:
        driver=direct_driver_factory(agent,memory,state_builder)
    attach(agent,memory,state_builder,verifier=verifier,direct_driver=driver)
    return agent
