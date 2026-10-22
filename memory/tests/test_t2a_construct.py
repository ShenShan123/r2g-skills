"""Construction algorithm/caching checks. No provider request or embedding download."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import t2a_construct as C
import t2a_adapters as A


@pytest.mark.parametrize('arm', ['EXPEL', 'AWM', 'AMEM'])
def test_original_mechanisms_construct_and_stop_before_overwrite(tmp_path, monkeypatch, arm):
    (tmp_path/'registration.json').write_text(json.dumps({'code':{},'amem_order':['u'],
                                                        'input_bytes_ceiling':12000,
                                                        'amem_order_sha256': A.fingerprint(['u'])}))
    u={'unit_id':'u','identity':{'ts':'2026-10-01'},'text_view':'Retained placement repair.'}
    trace={'id':'t','task':'task','text':u['text_view'],'success':True,'time':'2026-10-01',
           'situation':{'platform':'sky130hd','check':'orfs_stage','violation_class':'place'}}
    monkeypatch.setattr(A,'load_source',lambda p:[u])
    monkeypatch.setattr(A,'trajectories',lambda u:[trace])
    sent=[]
    class Calls:
        count=0
        def __init__(self,*a,**kw): pass
        def ask(self,ident,prompt,temperature,max_tokens=800):
            sent.append((ident,temperature)); self.count+=1
            if ident.startswith('critique'): return {'operations':'ADD: Reduce pressure before rerouting.'}
            if ident.startswith('workflow'): return {'workflows':'Repair\nGiven that placement fails:\n1. Inspect density.\n2. Reduce density.'}
            if ident.startswith('attribute'): return {'context':'place','keywords':['density'],'tags':['place']}
            return {'should_evolve':False}
    class Index:
        def __init__(self,*a):pass
        def search(self,*a):return []
        def add(self,*a):pass
        def rebuild(self,*a):pass
    monkeypatch.setattr(C,'Calls',Calls)
    monkeypatch.setattr(A,'DenseIndex',Index)
    C.construct(tmp_path,arm)
    result=json.loads((tmp_path/(arm+'.json')).read_text())
    assert result['status']=='FROZEN'
    if arm=='EXPEL': assert result['rules']==[['Reduce pressure before rerouting.',2]] and len(sent)==1
    elif arm=='AWM': assert len(result['workflows'])==1 and len(sent)==1
    else: assert len(result['notes'])==1 and len(sent)==2 and sent[0][1]==0.7
    with pytest.raises(RuntimeError,match='already frozen'): C.construct(tmp_path,arm)


def test_request_checkpoint_reuses_exact_answer_and_never_silently_resends(tmp_path):
    calls=C.Calls.__new__(C.Calls)
    calls.project=tmp_path;calls.key='unused';calls.count=0
    sent=[]
    def call(*args,**kwargs):
        sent.append(kwargs);return '{"answer":1}',{'total_tokens':20}
    calls.H=SimpleNamespace(llm_call=call)
    assert calls.ask('unit','evidence',0)=={'answer':1}
    assert calls.ask('unit','evidence',0)=={'answer':1} and len(sent)==1
    assert sent[0]['role']=='auxiliary'
    with pytest.raises(RuntimeError,match='changed'):calls.ask('unit','changed evidence',0)
    (tmp_path/'broken.try0.response.json').write_text('{}')
    with pytest.raises(RuntimeError,match='Unreconciled'):calls.ask('broken','evidence',0)


def test_private_construction_snapshot_has_provider_client_dependencies(tmp_path):
    root=tmp_path/'construction'
    C.prepare(root)
    H=C.Q.module('test_private_construction_client',root/'code/memory/scripts/r2g_memory_eval.py')
    assert 'CORE_UTILIZATION' in H.KNOBS
    registration=json.loads((root/'registration.json').read_text())
    assert 'code/r2g-skills/signoff-loop/knowledge/knob_policy.py' in registration['code']
    assert registration['expel_compare_calls']==893 and registration['expel_success_calls']==30


def test_workflow_object_rendering_preserves_all_steps_without_regeneration():
    obj={'relief':{'description':'Given congestion','steps':['Inspect density','Reduce density']}}
    assert json.loads(C.workflow_text(obj))==obj
    with pytest.raises(ValueError): C.workflow_text(None)


def test_native_parse_fallback_never_completes_a_truncated_operation():
    raw='{"operations":"ADD: Verified complete insight.\\nADD: Unfinished'
    answer=C.parse_answer(raw,True)
    parse,_=A.expel_operations()
    assert parse(C.complete_operations(answer))==[('ADD','Verified complete insight.')]
    with pytest.raises(ValueError):C.parse_answer(raw,False)


def test_c6_input_ceiling_is_registered_and_private_to_construction(tmp_path, monkeypatch):
    root=tmp_path/'construction'
    C.prepare(root)
    stopped={'status':'STOPPED','reason':'input_ceiling'}
    (root/'EXPEL-stop.json').write_text(json.dumps(stopped))
    C.amend(root,'C6')
    addition=json.loads((root/'revisions/C6/registration.json').read_text())
    original=json.loads((root/'registration.json').read_text())
    assert original['input_bytes_ceiling']==12000
    assert addition['input_bytes_ceiling']==48000
    assert addition['formal_input_bytes_ceiling']==12000
    assert addition['ceiling_scope']=='offline_external_memory_construction_only'
    assert json.loads((root/'revisions/C6/prior-state/EXPEL-stop.json').read_text())==stopped
    assert 'revisions/C6/prior-state/EXPEL-stop.json' in addition['prior_state']
    import r2g_memory_eval as shared
    monkeypatch.setattr(shared,'LLM_MAX_INPUT_BYTES',12000)
    # No credential reads and no provider requests in this test.
    loader=C.Q.module
    def private_module(name,path):
        H=loader(name,path)
        H.llm_key=lambda path:'unused'
        return H
    monkeypatch.setattr(C.Q,'module',private_module)
    for key in ('R2G_BUDGET_FILE','R2G_REQUIRE_LLM_BUDGET','R2G_PHASE_LEDGER',
                'R2G_PHASE_BUDGET_FILE','R2G_BUDGET_COMPONENT'):
        monkeypatch.delenv(key,raising=False)
    calls=C.Calls(root,'EXPEL',root/'revisions/C6/code',input_bytes_ceiling=48000)
    assert calls.H.LLM_MAX_INPUT_BYTES==48000
    assert shared.LLM_MAX_INPUT_BYTES==12000
    with pytest.raises(ValueError,match='Unregistered'):
        C.Calls(root,'EXPEL',input_bytes_ceiling=96001)


def _authorized_retry_calls(tmp_path, failed=False):
    calls=C.Calls.__new__(C.Calls)
    calls.project=tmp_path;calls.root=tmp_path;calls.arm='AMEM';calls.key='unused';calls.count=0;calls.tolerate=True
    messages=[{'role':'system','content':'Respond with one JSON object only.'},{'role':'user','content':'evidence'}]
    body={'model':'deepseek-flash','temperature':0.7,'max_tokens':1000,'stream':False,
          'thinking':{'type':'disabled'},'response_format':{'type':'json_object'},'messages':messages}
    req=tmp_path/'unit.try0.request.json';req.write_text(json.dumps(body))
    res=tmp_path/'unit.try0.response.json';res.write_text('SSL failure')
    grant=tmp_path/'grant.json'
    ledgers=[tmp_path/'reports/resource_ledger.jsonl',tmp_path/'phase/resource_ledger.jsonl']
    targets=[{'ledger_path':str(p),'hold_id':str(i),'upper_bound':{'tokens_in':len(req.read_bytes()),'tokens_out':1000}}
             for i,p in enumerate(ledgers)]
    data={'schema':'r2g-unknown-occupancy-authorization-v1','user_approved':True,'targets':targets,
          'retry':{'arm':'AMEM','ident':'unit','attempts':1,'revision':'C7',
             'signature':A.fingerprint([messages,0.7,1000]),'request_sha256':C.Q.digest(req),
             'response_sha256':C.Q.digest(res),'endpoint':'https://api.deepseek.com/chat/completions','model':'deepseek-flash'}}
    grant.write_text(json.dumps(data));sha=C.Q.digest(grant)
    for i,p in enumerate(ledgers):
        p.parent.mkdir(parents=True,exist_ok=True)
        rows=[{'type':'budget_unknown_occupancy','hold_id':str(i),'authorization':str(grant),
               'authorization_sha256':sha,'upper_bound':targets[i]['upper_bound']}]
        if i==0:rows.append({'type':'llm_request','hold_id':'0','phase_hold_id':'1','status':-1,
                              'usage_reported':'UNKNOWN','model_mismatch':False})
        p.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    sent=[]
    def call(*args,**kwargs):
        sent.append((args,kwargs))
        return (None,{'role':'auxiliary'}) if failed else ('{"answer":1}',{'total_tokens':20})
    calls.H=SimpleNamespace(llm_call=call,LLM_API_MODEL='deepseek-flash',
           LLM_ENDPOINT='https://api.deepseek.com/chat/completions',_ledger=lambda *a,**k:(0,''))
    calls.retry_authorization={'path':str(grant),'sha256':sha}
    return calls,sent,req,res


def test_explicit_retry_is_scoped_preserves_raw_and_reuses_success(tmp_path):
    calls,sent,req,res=_authorized_retry_calls(tmp_path)
    before=(req.read_bytes(),res.read_bytes())
    assert calls.ask('unit','evidence',0.7,1000)=={'answer':1}
    assert calls.ask('unit','evidence',0.7,1000)=={'answer':1} and len(sent)==1
    assert str(sent[0][0][2]).endswith('unit.authorized-retry-C7-1')
    assert sent[0][1].get('backoff',())==()
    assert (req.read_bytes(),res.read_bytes())==before
    cached=json.loads((tmp_path/'unit.answer.json').read_text())
    assert cached['explicit_retry']['authorization_sha256']==calls.retry_authorization['sha256']


def test_failed_explicit_retry_cannot_be_sent_again(tmp_path):
    calls,sent,req,res=_authorized_retry_calls(tmp_path,failed=True)
    with pytest.raises(RuntimeError,match='Construction request stopped'):calls.ask('unit','evidence',0.7,1000)
    with pytest.raises(RuntimeError,match='already claimed'):calls.ask('unit','evidence',0.7,1000)
    assert len(sent)==1


def test_changed_request_cannot_use_retry_grant(tmp_path):
    calls,sent,req,res=_authorized_retry_calls(tmp_path)
    with pytest.raises(RuntimeError,match='Authorized retry request changed'):
        calls.ask('unit','changed evidence',0.7,1000)
    assert sent==[]


def _quota_amendment(root, requests=5000):
    C.prepare(root)
    prior=root/'revisions/C7/registration.json';prior.parent.mkdir(parents=True)
    prior.write_text('{}')
    ledger=root/'phase/resource_ledger.jsonl';ledger.parent.mkdir();ledger.write_text('')
    budget=root/'C8-budget.json'
    budget.write_text(json.dumps({'budget_id':'t2a-construction-dev-A3','caps':{'llm_requests':requests},
                                 'combined_tokens_cap':17000000,'reserve':{}}))
    grant=root/'C8-quota-authorization.json'
    grant.write_text(json.dumps({'schema':'t2a-construction-quota-amendment-v1','user_approved':True,
        'previous_token_cap':7000000,'new_token_cap':17000000,'request_cap':5000,
        'new_budget_file':str(budget),'previous_budget_sha256':C.Q.digest(root/'budget.json'),
        'previous_revision_sha256':C.Q.digest(prior),'phase_ledger_path':str(ledger),
        'prior_phase_ledger_sha256':C.Q.digest(ledger)}))
    return grant,budget


def test_c8_quota_binding_preserves_original_budget_and_same_phase(tmp_path,monkeypatch):
    root=tmp_path/'construction';grant,budget=_quota_amendment(root)
    original=(root/'budget.json').read_bytes()
    C.amend(root,'C8',grant)
    reg=json.loads((root/'revisions/C8/registration.json').read_text())
    assert reg['input_bytes_ceiling']==48000 and reg['formal_input_bytes_ceiling']==12000
    assert reg['retry_authorization'] is None
    assert reg['budget_amendment']['path']==str(budget)
    assert (root/'budget.json').read_bytes()==original
    loader=C.Q.module
    def module(name,path):
        H=loader(name,path);H.llm_key=lambda path:'unused';return H
    monkeypatch.setattr(C.Q,'module',module)
    for key in ('R2G_BUDGET_FILE','R2G_REQUIRE_LLM_BUDGET','R2G_PHASE_LEDGER',
                'R2G_PHASE_BUDGET_FILE','R2G_BUDGET_COMPONENT'):
        monkeypatch.delenv(key,raising=False)
    C.Calls(root,'EXPEL',root/'revisions/C8/code',input_bytes_ceiling=48000,budget_file=budget)
    assert C.os.environ['R2G_BUDGET_FILE']==C.os.environ['R2G_PHASE_BUDGET_FILE']==str(budget)
    assert C.os.environ['R2G_PHASE_LEDGER']==str(root/'phase/resource_ledger.jsonl')


def test_c8_cannot_expand_request_cap_or_hide_budget_mutation(tmp_path):
    root=tmp_path/'construction';grant,budget=_quota_amendment(root,requests=5001)
    with pytest.raises(ValueError,match='quota'):C.amend(root,'C8',grant)
    assert not (root/'revisions/C8').exists()
    data=json.loads(budget.read_text());data['caps']['llm_requests']=5000;budget.write_text(json.dumps(data))
    C.amend(root,'C8',grant)
    budget.write_text(budget.read_text()+' ')
    with pytest.raises(RuntimeError,match='budget changed'):C.construct(root,'EXPEL','C8')


def _input_amendment(root):
    previous,budget=_quota_amendment(root);C.amend(root,'C8',previous)
    ledger=root/'phase/resource_ledger.jsonl'
    grant=root/'C9-input-authorization.json'
    grant.write_text(json.dumps({'schema':'t2a-construction-input-amendment-v1','user_approved':True,
        'previous_revision_sha256':C.Q.digest(root/'revisions/C8/registration.json'),
        'previous_input_bytes_ceiling':48000,'new_input_bytes_ceiling':64000,
        'formal_input_bytes_ceiling':12000,'output_tokens_ceiling':2000,
        'combined_tokens_cap':17000000,'request_cap':5000,'transport_retries':0,
        'budget_file':str(budget),'budget_sha256':C.Q.digest(budget),
        'phase_ledger_path':str(ledger),'prior_phase_ledger_sha256':C.Q.digest(ledger),
        'source_sha256':A.SOURCE_SHA}))
    return grant,budget


def test_c9_accepts_new_construction_body_only_and_keeps_hard_limits(tmp_path,monkeypatch):
    root=tmp_path/'construction';grant,budget=_input_amendment(root)
    unchanged=budget.read_bytes();C.amend(root,'C9',grant)
    reg=json.loads((root/'revisions/C9/registration.json').read_text())
    assert reg['input_bytes_ceiling']==64000 and reg['formal_input_bytes_ceiling']==12000
    assert reg['transport_retries']==0 and reg['retry_authorization'] is None
    assert reg['budget_amendment']['path']==str(budget) and budget.read_bytes()==unchanged
    import r2g_memory_eval as shared
    assert shared.LLM_MAX_INPUT_BYTES==12000
    loader=C.Q.module
    def module(name,path):
        H=loader(name,path);H.llm_key=lambda path:'unused';return H
    monkeypatch.setattr(C.Q,'module',module)
    for key in ('R2G_BUDGET_FILE','R2G_REQUIRE_LLM_BUDGET','R2G_PHASE_LEDGER',
                'R2G_PHASE_BUDGET_FILE','R2G_BUDGET_COMPONENT'):
        monkeypatch.delenv(key,raising=False)
    calls=C.Calls(root,'EXPEL',root/'revisions/C9/code',input_bytes_ceiling=64000,budget_file=budget)
    sent=[]
    class Response:
        status=200
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return json.dumps({'id':'offline-c9','model':'deepseek-flash',
          'usage':{'prompt_tokens':12000,'completion_tokens':1,'total_tokens':12001},
          'choices':[{'message':{'content':'{"answer":1}'},'finish_reason':'stop'}]}).encode()
    def urlopen(req,**kwargs):sent.append(req.data);return Response()
    monkeypatch.setattr(calls.H.urllib.request,'urlopen',urlopen)
    assert calls.ask('fits','x'*48000,0)=={'answer':1}
    assert 48000<len(sent[0])<64000 and calls.H.LLM_MAX_INPUT_BYTES==64000
    with pytest.raises(RuntimeError,match='input_ceiling'):calls.ask('over','x'*64000,0)
    assert len(sent)==1 and shared.LLM_MAX_INPUT_BYTES==12000


def test_c9_cannot_expand_total_quota_and_rejects_changed_authorization(tmp_path):
    root=tmp_path/'construction';grant,budget=_input_amendment(root)
    data=json.loads(grant.read_text());data['combined_tokens_cap']=17000001
    grant.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='input amendment'):C.amend(root,'C9',grant)
    assert not (root/'revisions/C9').exists()
    data['combined_tokens_cap']=17000000;grant.write_text(json.dumps(data));C.amend(root,'C9',grant)
    grant.write_text(grant.read_text()+' ')
    with pytest.raises(RuntimeError,match='Input authorization changed'):C.construct(root,'EXPEL','C9')


def _c10_input_amendment(root):
    previous,budget=_input_amendment(root);C.amend(root,'C9',previous)
    grant=root/'C10-input-authorization.json'
    data=json.loads(previous.read_text())
    data.update(previous_revision_sha256=C.Q.digest(root/'revisions/C9/registration.json'),
                previous_input_bytes_ceiling=64000,new_input_bytes_ceiling=96000)
    grant.write_text(json.dumps(data))
    return grant,budget


def test_c10_resumes_larger_construction_without_changing_prior_evidence(tmp_path,monkeypatch):
    root=tmp_path/'construction';grant,budget=_c10_input_amendment(root)
    prior=root/'revisions/C9/registration.json'
    before=(prior.read_bytes(),budget.read_bytes(),(root/'phase/resource_ledger.jsonl').read_bytes())
    stopped={'status':'STOPPED','reason':'input_ceiling','request_bytes':64374}
    C.write(root/'EXPEL-stop.json',stopped)
    C.amend(root,'C10',grant)
    reg=json.loads((root/'revisions/C10/registration.json').read_text())
    assert reg['input_bytes_ceiling']==96000 and reg['formal_input_bytes_ceiling']==12000
    assert reg['input_amendment']['previous_revision_sha256']==C.Q.digest(prior)
    assert reg['budget_amendment']['path']==str(budget)
    assert reg['transport_retries']==0 and reg['retry_authorization'] is None
    assert json.loads((root/'revisions/C10/prior-state/EXPEL-stop.json').read_text())==stopped
    assert before==(prior.read_bytes(),budget.read_bytes(),(root/'phase/resource_ledger.jsonl').read_bytes())
    import r2g_memory_eval as shared
    loader=C.Q.module
    def module(name,path):
        H=loader(name,path);H.llm_key=lambda path:'unused';return H
    monkeypatch.setattr(C.Q,'module',module)
    for key in ('R2G_BUDGET_FILE','R2G_REQUIRE_LLM_BUDGET','R2G_PHASE_LEDGER',
                'R2G_PHASE_BUDGET_FILE','R2G_BUDGET_COMPONENT'):
        monkeypatch.delenv(key,raising=False)
    calls=C.Calls(root,'EXPEL',root/'revisions/C10/code',input_bytes_ceiling=96000,budget_file=budget)
    sent=[]
    class Response:
        status=200
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return json.dumps({'id':'offline-c10','model':'deepseek-flash',
          'usage':{'prompt_tokens':18000,'completion_tokens':1,'total_tokens':18001},
          'choices':[{'message':{'content':'{"answer":1}'},'finish_reason':'stop'}]}).encode()
    def urlopen(req,**kwargs):sent.append(req.data);return Response()
    monkeypatch.setattr(calls.H.urllib.request,'urlopen',urlopen)
    assert calls.ask('fits','x'*64000,0)=={'answer':1}
    assert 64000<len(sent[0])<96000
    with pytest.raises(RuntimeError,match='input_ceiling'):calls.ask('over','x'*96000,0)
    assert len(sent)==1 and shared.LLM_MAX_INPUT_BYTES==12000


def test_c10_input_amendment_rejects_quota_changes_and_changed_authorization(tmp_path):
    root=tmp_path/'construction';grant,budget=_c10_input_amendment(root)
    data=json.loads(grant.read_text());data['combined_tokens_cap']=17000001
    grant.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='input amendment'):C.amend(root,'C10',grant)
    assert not (root/'revisions/C10').exists()
    data['combined_tokens_cap']=17000000;grant.write_text(json.dumps(data));C.amend(root,'C10',grant)
    grant.write_text(grant.read_text()+' ')
    with pytest.raises(RuntimeError,match='Input authorization changed'):C.construct(root,'EXPEL','C10')
