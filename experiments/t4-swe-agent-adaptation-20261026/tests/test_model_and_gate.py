import json
import os
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
os.environ['LITELLM_LOCAL_MODEL_COST_MAP']='True'
sys.path[:0]=[str(ROOT/'adapters'),str(ROOT/'sources/SWE-agent')]


def test_metered_model_preserves_reasoning_and_native_tool_protocol():
    from model_adapter import MeteredModel
    from sweagent.tools.tools import ToolConfig
    from sweagent.tools.parsing import FunctionCallingParser
    class Provider:
        model='deepseek-flash';blocked=False
        def __init__(self):self.payloads=[]
        def request(self,body,**kwargs):
            self.payloads.append(body)
            return {'id':'offline-fixture','model':'deepseek-flash',
                    'choices':[{'message':{'content':'inspect','reasoning_content':'exact reasoning',
                      'tool_calls':[{'id':'call1','type':'function','function':{'name':'bash','arguments':'{"command":"echo ok"}'}}]}}],
                    'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}}
    p=Provider();m=MeteredModel(p,ToolConfig(parse_function=FunctionCallingParser()))
    result=m.query([{'role':'user','content':'public issue','private':'not for wire'}])
    assert result['tool_calls'][0]['id']=='call1' and m.stats.api_calls==1
    m.query([{'role':'assistant','content':result['message'],'tool_calls':result['tool_calls']},
             {'role':'tool','content':'ok','tool_call_ids':['call1']}])
    assert p.payloads[1]['messages'][0]['reasoning_content']=='exact reasoning'
    assert p.payloads[1]['messages'][1]['tool_call_id']=='call1'
    assert 'private' not in p.payloads[0]['messages'][0]
    assert m.billing_cost is None


def test_network_loss_is_one_request_unknown_and_never_retried(tmp_path):
    from meter import MeteredProvider,UnknownUsage
    import urllib.error
    calls=[]
    p=MeteredProvider(tmp_path/'provider','offline-placeholder-not-a-real-key')
    def fail(*args,**kwargs):calls.append(1);raise urllib.error.URLError('offline connection reset')
    with pytest.raises(UnknownUsage):p.request({'messages':[]},transport=fail)
    with pytest.raises(UnknownUsage):p.request({'messages':[]},transport=fail)
    es=[json.loads(l) for l in p.journal.path.read_text().splitlines()]
    assert len(calls)==1 and sum(e['event']=='request_started' for e in es)==1
    assert next(e for e in es if e['event']=='request_ended')['usage']=='UNKNOWN'
    assert es[-1]['sent'] is False


def test_task_inputs_reject_private_evaluator_answers():
    from prepare import public_tasks
    d={'public_tasks':[{'instance_id':'i','repo':'r','base_commit':'b','problem_statement':'issue','patch':'secret'}]}
    with pytest.raises(ValueError):public_tasks(d)


def test_official_result_cannot_be_satisfied_by_shell_exit_or_boolean():
    from memory_adapter import official_resolved
    assert official_resolved({'exit_code':0},'task','patch-digest') is None
    assert official_resolved({'resolved':True},'task','patch-digest') is None
    receipt={'producer':'swebench_official_harness','instance_id':'task','patch_sha256':'patch-digest',
             'report':{'task':{'resolved':True}}}
    assert official_resolved(receipt,'task','patch-digest') is True
    assert official_resolved(receipt,'other','patch-digest') is None
    assert official_resolved(receipt,'task','different') is None


def test_modified_native_source_or_wrong_version_is_refused(monkeypatch):
    import prepare
    def clean(args,**kw):return prepare.COMMIT+'\n' if args[-1]=='HEAD' else ''
    monkeypatch.setattr(prepare.subprocess,'check_output',clean)
    assert prepare.verify_upstream()==prepare.COMMIT
    def dirty(args,**kw):return prepare.COMMIT+'\n' if args[-1]=='HEAD' else ' M sweagent/agent/agents.py\n'
    monkeypatch.setattr(prepare.subprocess,'check_output',dirty)
    with pytest.raises(ValueError):prepare.verify_upstream()


def test_native_and_tehm_construct_with_identical_original_tools(tmp_path):
    import yaml
    from bridge import FullMemory,State
    from host import build_agent
    cfg=yaml.safe_load((ROOT/'sources/SWE-agent/config/default.yaml').read_text())['agent']
    cfg['model']={'name':'deepseek-flash','per_instance_cost_limit':0,'total_cost_limit':0}
    class OfflineProvider:
        model='deepseek-flash';blocked=False
        def request(self,*a,**kw):raise AssertionError('construction must not query a model')
    memories=[FullMemory('swe-agent',arm=arm,root=tmp_path) for arm in ('native','tehm')]
    try:
        state=lambda:State('fixture','fixture','software','python','issue_tests',{}, {},{})
        agents=[build_agent(cfg,m,OfflineProvider(),state) for m in memories]
        assert agents[0].tools.config.model_dump()==agents[1].tools.config.model_dump()
        assert agents[0].templates.model_dump()==agents[1].templates.model_dump()
        assert agents[0].max_requeries==agents[1].max_requeries
        assert len(agents[0].tools.config.bundles)==3
    finally:
        for m in memories:m.close()
