import json
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'code/memory/scripts'),str(ROOT/'code/memory')]
import sol_install as I
import sol_provider as P


class Harness:
    LLM_TIMEOUT_S=300
    LLM_KILL_GRACE_S=30
    def __init__(self):self.open={};self.spent={};self.events=[];self.settlements=[];self.next=0
    def _phase(self):return Path('phase'),{'scope':'phase'}
    def _ledger(self,project,operation,*args,env=None):
        scope=str(project);self.spent.setdefault(scope,{})
        def items(flag):return [args[i+1] for i,x in enumerate(args) if x==flag]
        if operation=='admit':
            needs=dict(s.split('=',1) for s in items('--need'));needs={k:float(v) for k,v in needs.items()}
            if scope!='phase':
                for resource in ['llm_requests','repair_attempts']:
                    reserved=sum(h['need'].get(resource,0) for h in self.open.values() if h['scope']==scope)
                    if self.spent[scope].get(resource,0)+reserved+needs.get(resource,0)>3:return 75,''
            self.next+=1;identity='hold-'+str(self.next);self.open[identity]={'scope':scope,'need':needs}
            return 0,identity
        if operation=='settle':
            identity=items('--hold-id')[0];assert identity in self.open
            self.open.pop(identity);actual=dict(s.split('=',1) for s in items('--actual'))
            self.settlements.append((scope,actual))
            for k,v in actual.items():
                if v!='UNKNOWN':self.spent[scope][k]=self.spent[scope].get(k,0)+float(v)
            return 0,''
        if operation=='remaining':return 0,json.dumps({'wall_s':None})
        if operation=='event':self.events.append(json.loads(items('--json')[0]));return 0,''
        raise AssertionError(operation)


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('R2G_REQUIRE_LLM_BUDGET','1')
    monkeypatch.setattr(P,'credentials',lambda *a:('fixture','https://api.openai.com/v1/responses'))
    monkeypatch.setattr(P,'ROOT',tmp_path)
    calls=[]
    response={'id':'fixture-response','status':'completed','model':P.MODEL,'reasoning':{'effort':'high'},
        'output':[{'type':'message','content':[{'type':'output_text','text':'{"operation":"stop","arguments":{"reason":"done"}}'}]}],
        'usage':{'input_tokens':10,'output_tokens':30,'total_tokens':40,'output_tokens_details':{'reasoning_tokens':25}}}
    receipt={'http_status':200,'wall_s':0.1,'request_id':'fixture-request'}
    def send(body,key,endpoint,prefix,timeout):calls.append(body);return response,receipt
    monkeypatch.setattr(P,'transport',send)
    return Harness(),calls,response,receipt,tmp_path


def request(c,index=0,role='repair'):
    h,calls,response,receipt,path=c
    return I.governed_call(h,'fixture',[{'role':'user','content':'Return JSON'}],
                          path/str(index),ledger=Path('episode'),role=role)


def test_real_metering_boundary_limits_total_requests_to_three(client):
    for i in range(3):
        content,usage=request(client,i);assert content and usage['repair_turn_id']
    content,usage=request(client,3)
    assert content is None and usage['budget_exhausted']
    assert len(client[1])==3 and not client[0].open
    assert client[0].spent['episode']['llm_requests']==3
    assert client[0].spent['phase']['llm_requests']==3


def test_auxiliary_requests_share_the_same_three_opportunities(client):
    request(client,0,role='auxiliary');request(client,1);request(client,2)
    assert request(client,3)[0] is None and len(client[1])==3
    assert client[0].spent['episode']['repair_attempts']==2


def test_reasoning_tokens_are_not_charged_twice(client):
    _,usage=request(client)
    assert usage['total_tokens']==40
    assert client[0].spent['episode']['tokens_in']==10
    assert client[0].spent['episode']['tokens_out']==30
    assert client[0].events[-1]['reasoning_tokens']==25


def test_refusal_releases_the_phase_reservation_without_charging_usage(client):
    h=client[0];h.spent['episode']={'llm_requests':3}
    result=request(client,role='auxiliary')
    assert result[0] is None and not client[1] and not h.open
    assert h.spent['phase']['llm_requests']==0


def test_unknown_usage_is_preserved_and_all_holds_are_settled(client):
    client[2].clear();client[2]['error']={'message':'transport interrupted'}
    client[3]['http_status']=None
    content,usage=request(client)
    assert content is None and usage['stopped'] and len(client[1])==1
    assert not client[0].open
    assert client[0].events[-1]['usage_reported']=='UNKNOWN'
    assert any(a.get('tokens_in')=='UNKNOWN' for _,a in client[0].settlements)


@pytest.mark.parametrize('defect',['model','permission','effort'])
def test_provider_failure_cannot_become_a_valid_candidate(client,defect):
    if defect=='model':client[2]['model']='wrong-model'
    elif defect=='permission':client[3]['http_status']=403
    else:client[2]['reasoning']['effort']='low'
    content,usage=request(client)
    assert content is None and usage['stopped']


def test_implicit_retry_is_refused_before_any_model_call(client):
    h,*_=client
    content,usage=I.governed_call(h,'fixture',[],client[-1]/'x',backoff=(1,),ledger=Path('episode'))
    assert content is None and usage['accounting_error'] and not client[1]


def test_repair_executor_and_original_task_templates_unchanged():
    import experiment as E
    reg=E.load(ROOT/'registration-staged.json');original=E.load(E.BASE/'nb/registration.json')
    assert reg['tasks']==original['tasks'] and reg['plan']==original['plan']
    for rel in ['memory/scripts/t2_agent_runtime.py','memory/scripts/t2a_envelope.py',
                'r2g-skills/signoff-loop/scripts/flow/run_orfs.sh',
                'r2g-skills/signoff-loop/scripts/flow/fix_signoff.sh']:
        assert E.sha(ROOT/'code'/rel)==E.sha(E.BASE/'nb/code'/rel)


def test_only_provider_identity_and_namespace_changed_in_shared_runtime():
    import experiment as E
    source=(E.BASE/'nb/code/memory/scripts/t2a_runtime.py').read_text()
    assert (ROOT/'code/memory/scripts/t2a_runtime.py').read_text()==source.replace(
        "r.get('model_reported') not in ('deepseek-flash','deepseek-v4.1-flash')",
        "r.get('model_reported') != 'gpt-6.1-sol'",1)
    source=(E.BASE/'nb/code/memory/scripts/t2a_batch.py').read_text()
    assert (ROOT/'code/memory/scripts/t2a_batch.py').read_text()==source.replace(
        "name=task.replace('/','__')+'__'+arm+'__r'+str(repeat)",
        "name=task.replace('/','__')+'__'+reg.get('namespace_arm',arm)+'__r'+str(repeat)",1)
