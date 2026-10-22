"""Request-only policy and append-only continuation; provider transport is mocked."""
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import pytest

sys.path[:0]=[str(Path(__file__).parents[1]/'scripts'),str(Path(__file__).parents[1])]
import t2_agent_request_resume as R
import t2_agent_dispatch as D
import t2a_construct as C
from test_repair_turns import H,ep,OK,_Resp,_spent


def opt_in(monkeypatch):
    for name in ('llm_call','LLM_MAX_TOKENS','LLM_OUTPUT_CEILING','LLM_MAX_INPUT_BYTES'):
        monkeypatch.setattr(H,name,getattr(H,name))
    monkeypatch.setattr(H,'_AGENT_REQUEST_POLICY',None,raising=False)
    R.configure_harness(H)


@pytest.mark.parametrize('arm',D.ARMS)
def test_every_arm_uses_provider_max_and_only_three_actual_online_calls(ep,monkeypatch,arm):
    import os
    p,calls,_=ep
    Path(os.environ['R2G_BUDGET_FILE']).write_text(json.dumps(D.episode_budget()))
    opt_in(monkeypatch)
    def transport(req,timeout=0):
        body=json.loads(req.data);calls.append(body)
        assert body['max_tokens']==393216
        # Actual usage, not the reservation or an 800/2000 cap, is charged.
        raw={**OK,'choices':[{'finish_reason':'stop','message':{'content':'{"ok":true}'}}],
             'usage':{'prompt_tokens':50,'completion_tokens':3000,'total_tokens':3050}}
        return _Resp(json.dumps(raw).encode())
    monkeypatch.setattr(H.urllib.request,'urlopen',transport)
    gateway=D.OnlineGateway(H,p,'unused')
    for i in range(3):
        content,usage=gateway.call([{'role':'user','content':'x'*100000}],p/('q'+str(i)),
            purpose='planning',candidate_generating=True,sampling_override={'max_tokens':800})
        assert content and usage['completion_tokens']==3000
    with pytest.raises(D.OnlineAllowanceSpent):
        gateway.call([],p/'fourth',purpose='planning',candidate_generating=True)
    assert len(calls)==3 and _spent(p,'llm_requests')==3 and _spent(p,'tokens_out')==9000


def test_unknown_usage_still_stops_without_hidden_retry(ep,monkeypatch):
    import os
    p,calls,respond=ep
    Path(os.environ['R2G_BUDGET_FILE']).write_text(json.dumps(D.episode_budget()))
    opt_in(monkeypatch)
    respond({k:v for k,v in OK.items() if k!='usage'})
    gateway=D.OnlineGateway(H,p,'unused')
    with pytest.raises(D.IntegrityStop):gateway.call([],p/'unknown',purpose='planning',candidate_generating=True)
    with pytest.raises(D.IntegrityStop):gateway.remaining()
    assert len(calls)==1


def test_provider_length_stop_never_becomes_valid_partial_content(ep,monkeypatch):
    p,calls,respond=ep;opt_in(monkeypatch)
    respond({**OK,'choices':[{'finish_reason':'length','message':{'content':'{"valid_but_incomplete":true}'}}]})
    content,usage=H.llm_call('unused',[],p/'length',ledger=p,role='auxiliary')
    assert content is None and usage['technical_stop']=='provider_output_truncated'
    assert usage['completion_tokens']==20 and len(calls)==1


def test_no_implicit_transport_retry_is_enabled(ep,monkeypatch):
    p,calls,_=ep;opt_in(monkeypatch)
    with pytest.raises(RuntimeError,match='Implicit retries'):H.llm_call('unused',[],p/'retry',backoff=(1,),ledger=p)
    assert calls==[]


@pytest.mark.parametrize('arm',D.ARMS)
def test_json_mode_helpers_have_explicit_json_instruction_before_transmission(ep,monkeypatch,arm):
    import os
    p,calls,_=ep
    Path(os.environ['R2G_BUDGET_FILE']).write_text(json.dumps(D.episode_budget()))
    opt_in(monkeypatch)
    messages=[{'role':'system','content':'Extract retrieval keywords only. Return {"keywords":["..."]}. Do not propose repair actions.'},
              {'role':'user','content':'route overflow'}]
    before=json.loads(json.dumps(messages))
    def transport(req,timeout=0):
        body=json.loads(req.data);calls.append(body)
        assert body['response_format']=={'type':'json_object'}
        assert any('json' in m['content'].lower() for m in body['messages'])
        assert body['messages'][1:]==before[1:]
        return _Resp(json.dumps({**OK,'choices':[{'finish_reason':'stop','message':{'content':'{"keywords":["route"]}'}}]}).encode())
    monkeypatch.setattr(H.urllib.request,'urlopen',transport)
    gateway=D.OnlineGateway(H,p,'unused')
    content,usage=gateway.call(messages,p/'amem-keywords',purpose='query')
    assert json.loads(content)['keywords']==['route'] and len(calls)==1
    assert messages==before and _spent(p,'llm_requests')==1


def test_json_mode_compatibility_is_idempotent_and_does_not_touch_text_requests():
    messages=[{'role':'user','content':'Extract keywords.'}]
    fixed=R.json_mode_messages(messages,{'type':'json_object'})
    assert fixed[0]['role']=='system' and 'JSON' in fixed[0]['content']
    assert fixed[1:]==messages
    assert R.json_mode_messages(fixed,{'type':'json_object'})==fixed
    assert R.json_mode_messages(messages,{'type':'text'})==messages


def test_policy_reports_real_provider_boundary_not_infinity():
    d=R.policy()
    assert d['input_bytes_ceiling'] is None and d['cumulative_token_cap'] is None
    assert d['total_online_requests']==3 and d['output_tokens']==393216
    assert d['provider_default_if_omitted']==8192
    json.dumps(d,allow_nan=False)


def test_online_opt_in_does_not_change_historical_policy(monkeypatch):
    import t2a_runtime as T
    old=T.online_policy()
    for key in ('configure_online','online_policy'):monkeypatch.setattr(T,key,getattr(T,key))
    monkeypatch.setattr(T,'_AGENT_REQUEST_POLICY',None,raising=False)
    R.install_online()
    assert T.online_policy()['revision']==R.POLICY
    assert old['output_tokens']==800


@pytest.mark.parametrize('missing,unknown',[(True,False),(False,True)])
def test_unsettled_or_unknown_prior_usage_blocks_continuation(tmp_path,missing,unknown):
    ev=[{'type':'budget_hold','hold_id':'one'}]
    if not missing:ev.append({'type':'budget_settle','hold_id':'one'})
    if unknown:ev.append({'type':'llm_request','usage_reported':'UNKNOWN'})
    p=tmp_path/'ledger';p.write_text(''.join(json.dumps(e)+'\n' for e in ev))
    with pytest.raises(ValueError):R.ledger_complete(p)


def test_invariants_allow_only_append_to_prior_cost(tmp_path):
    q=tmp_path/'queue';q.mkdir();rev=tmp_path/'revision';rev.mkdir()
    p=q/'ledger';p.write_bytes(b'old cost\n');fixed=q/'old-response';fixed.write_text('old result')
    R.save(rev/'registration.json',{'files':{}})
    R.save(rev/'protected-artifacts.json',{str(fixed):R.sha(fixed)})
    R.save(rev/'ledger-prefixes.json',{str(p):{'bytes':p.stat().st_size,'sha256':R.sha(p)}})
    p.write_bytes(b'old cost\nnew cost\n');R.invariant(q,rev)
    p.write_bytes(b'zero cost\n')
    with pytest.raises(RuntimeError):R.invariant(q,rev)
    p.write_bytes(b'old cost\n');fixed.write_text('rewritten')
    with pytest.raises(RuntimeError):R.invariant(q,rev)


@pytest.mark.parametrize('fault',[None,'response_changed','prompt_changed','no_grant','already_sent'])
def test_only_explicit_bound_truncation_is_retried_once_and_cache_is_reused(tmp_path,monkeypatch,fault):
    root=tmp_path/'controls';p=root/'construction/REME';p.mkdir(parents=True)
    (p/'reports').mkdir();rev=tmp_path/'revision';rev.mkdir()
    messages=[{'role':'system','content':'Respond with one JSON object only.'},{'role':'user','content':'same prompt'}]
    prefix=p/'extract-005';request=Path(str(prefix)+'.try0.request.json');response=Path(str(prefix)+'.try0.response.json')
    R.save(request,{'messages':messages,'temperature':0,'max_tokens':800,'model':'deepseek-flash'})
    R.save(response,{'choices':[{'finish_reason':'length'}],'usage':{'prompt_tokens':50,'completion_tokens':800}})
    old={str(x):R.sha(x) for x in (request,response)}
    R.save(rev/'authorization.json',{'truncated_requests':{} if fault=='no_grant' else {str(response.resolve()):{
        'request_sha256':R.sha(request),'response_sha256':R.sha(response)}}})
    (p/'reports/resource_ledger.jsonl').write_text('')
    sent=[]
    def wire(key,messages,new,**kwargs):
        sent.append((new,kwargs));assert kwargs['sampling_override']['max_tokens']==393216
        R.save(str(new)+'.try0.response.json',{'choices':[{'finish_reason':'stop'}]})
        return '{"memories":[]}',{'prompt_tokens':50,'completion_tokens':2000,'total_tokens':2050}
    fake=SimpleNamespace(llm_call=wire,LLM_API_MODEL='deepseek-flash',_ledger=lambda *a:(0,''))
    monkeypatch.setattr(C,'Calls',C.Calls)
    R.install_constructor(rev)
    c=object.__new__(C.Calls);c.project=p;c.H=fake;c.count=0;c.key='unused';c.tolerate=False
    R.configure_harness(fake)
    if fault=='response_changed':response.write_text('{}')
    if fault=='already_sent':R.save(str(prefix)+'.authorized-output-resume-1.try0.response.json',{})
    prompt='different prompt' if fault=='prompt_changed' else 'same prompt'
    if fault:
        with pytest.raises(RuntimeError):c.ask('extract-005',prompt,0)
        assert not sent
    else:
        assert c.ask('extract-005',prompt,0)=={'memories':[]}
        assert c.ask('extract-005',prompt,0)=={'memories':[]} # exact cache; no second request
        assert len(sent)==1 and c.count==1
        for f,h in old.items():assert R.sha(f)==h


def test_existing_frozen_representations_are_not_scheduled_again(tmp_path):
    for i in range(2):
        p=tmp_path/'folds'/str(i);(p/'controls').mkdir(parents=True);(p/'shared').mkdir()
        R.save(p/'shared/manifest.json',{'units_sha256':str(i)})
        for arm in R.BUILDS:
            if (i,arm)==(1,'REME'):continue
            R.save(p/'controls'/(arm+'.json'),{'status':'FROZEN','units_sha256':str(i)})
    assert R.missing_builds(tmp_path)==[{'target':'1','arm':'REME'}]
    R.save(tmp_path/'folds/0/controls/EXPEL.json',{'status':'FROZEN','units_sha256':'wrong'})
    with pytest.raises(ValueError):R.missing_builds(tmp_path)


def test_new_frozen_policy_requires_opt_in_entrypoint(tmp_path,monkeypatch):
    import t2a_batch as B
    import t2a_runtime as T
    R.save(tmp_path/'registration.json',{'status':'FROZEN_READY','split':'TEST',
        'files':{},'online_policy':R.policy(),'mechanism_policy':None})
    with pytest.raises(RuntimeError,match='policy differs'):B.preflight(tmp_path,'TEST')
    for key in ('configure_online','online_policy'):monkeypatch.setattr(T,key,getattr(T,key))
    monkeypatch.setattr(T,'_AGENT_REQUEST_POLICY',None,raising=False)
    R.install_online();B.preflight(tmp_path,'TEST')
    fake=SimpleNamespace(LLM_MAX_TOKENS=800,LLM_OUTPUT_CEILING=2000,REPAIR_TURNS=3,llm_call=lambda *a:None)
    T.configure_online(fake)
    assert fake.LLM_MAX_TOKENS==393216 and fake.LLM_MAX_INPUT_BYTES==float('inf')


def test_real_reme_checkpoint_replay_with_mocked_retry_preserves_five_cached_responses(tmp_path,monkeypatch):
    import shutil
    import t2_agent_new_methods as M
    source=Path('/data2/zhangdy/tehm-experiments/t2-agent9-followup8-20261021-r5/folds/C00876/controls')
    if not source.exists():pytest.skip('Recorded ReMe case not available')
    root=tmp_path/'controls';shutil.copytree(source,root)
    project=root/'construction/REME';rev=tmp_path/'revision';rev.mkdir()
    # The live source has since completed. Reconstruct the incident checkpoint
    # in this disposable copy only, rather than depending on live queue timing.
    (root/'REME.json').unlink(missing_ok=True)
    keep=tuple('extract-%03d.'%i for i in range(5))
    for p in project.iterdir():
        if p.is_file() and not (p.name.startswith(keep) or p.name in
                ('extract-005.try0.request.json','extract-005.try0.response.json')):
            p.unlink()
    old={str(p):R.sha(p) for p in project.glob('*') if p.name.endswith(('.answer.json','.request.json','.response.json'))}
    failed=project/'extract-005.try0.response.json';request=project/'extract-005.try0.request.json'
    R.save(rev/'authorization.json',{'truncated_requests':{str(failed.resolve()):{
        'response_sha256':R.sha(failed),'request_sha256':R.sha(request)}}})
    paid=[]
    def wire(key,messages,prefix,**kwargs):
        assert kwargs['sampling_override']['max_tokens']==393216
        paid.append(prefix.name)
        content=({'is_valid':True,'score':.8} if prefix.name.startswith('validate-') else
                 {'memories':[]} if prefix.name=='soft-comparison' else
                 {'memories':[{'when_to_use':'a related state','experience':'a fallible observed action'}]})
        R.save(str(prefix)+'.try0.response.json',{'choices':[{'finish_reason':'stop'}]})
        return json.dumps(content),{'prompt_tokens':50,'completion_tokens':2000,'total_tokens':2050}
    fake=SimpleNamespace(llm_call=wire,LLM_API_MODEL='deepseek-flash',_ledger=lambda *a:(0,''))
    R.configure_harness(fake)
    original=C.Calls
    monkeypatch.setattr(C,'Calls',original)
    R.install_constructor(rev)
    cls=C.Calls
    def init(self,root,arm,*args,**kwargs):
        self.root=root;self.arm=arm;self.project=project;self.count=0;self.H=fake
        self.key='mock';self.tolerate=False;self.retry_authorization=None
    monkeypatch.setattr(cls,'__init__',init)
    class Index:
        def __init__(self,*a):pass
        def encode(self,texts):return [[1.,0.] for _ in texts]
    monkeypatch.setattr(M.A,'DenseIndex',Index)
    M.build(root,'REME')
    assert R.load(root/'REME.json')['status']=='FROZEN'
    assert paid[0]=='extract-005.authorized-output-resume-1'
    assert not any(x.startswith(('extract-000','extract-001','extract-002','extract-003','extract-004')) for x in paid)
    assert sum(x=='extract-005.authorized-output-resume-1' for x in paid)==1
    for path,h in old.items():assert R.sha(path)==h


@pytest.mark.parametrize('colon',['',':'])
def test_reasoningbank_heading_colon_is_lossless_transport_compatibility(colon):
    from t2_agent_new_methods import parse_rb
    text=f'# Memory Item 1\n## Title{colon} the title\n## Description{colon} the description\n## Content{colon} exact content: keep this colon'
    assert parse_rb(text)==[{'title':'the title','description':'the description','content':'exact content: keep this colon'}]


def test_real_reasoningbank_returned_answers_parse_without_reissuing_requests():
    from t2_agent_new_methods import parse_rb
    root=Path('/data2/zhangdy/tehm-experiments/t2-agent9-followup8-20261021-r5/folds/C00237/controls/construction/REASONINGBANK')
    paths=[root/'extract-000.answer.json',root/'extract-001.answer.json']
    before={str(p):R.sha(p) for p in paths}
    for p in paths:
        answer=R.load(p)['answer']['markdown'];items=parse_rb(answer)
        assert len(items)==3 and all(all(item[k] for k in ('title','description','content')) for item in items)
    for p,h in before.items():assert R.sha(p)==h


def test_native_method_delta_changes_only_colon_delimiters():
    base=Path('/data2/zhangdy/tehm-experiments/t2-agent9-followup8-20261021-r5/code/memory/scripts/t2_agent_new_methods.py')
    current=Path(__file__).parents[1]/'scripts/t2_agent_new_methods.py'
    assert base.read_text().count(R.RB_OLD)==1
    assert base.read_text().replace(R.RB_OLD,R.RB_NEW,1)==current.read_text()


def test_amem_summary_instruction_preserves_all_input_and_other_methods():
    messages=[{'role':'system','content':'Respond with one JSON object only.'},
              {'role':'user','content':'all original history, neighbors and rollback labels'}]
    amended=R.amem_summary_messages(messages)
    assert amended[1:]==messages[1:] and messages[0]['content']=='Respond with one JSON object only.'
    assert 'retrieval abstract' in amended[0]['content']
    assert 'rollback' in amended[0]['content'] and 'Do not copy' in amended[0]['content']
    assert 'tokens' not in amended[0]['content']


def test_amem_repetition_is_rejected_without_a_length_quota():
    block='export DESIGN_NAME = sqrt; export VERILOG_FILES = '+('long_unique_path/'*20)
    with pytest.raises(RuntimeError,match='repeated'):
        R.verify_amem_contexts({'new_context_neighborhood':[block*20]})
    # Not a token/character ceiling: a large non-repetitive context is not rejected.
    text=' '.join('distinct_fact_%06d'%i for i in range(30000))
    R.verify_amem_contexts({'new_context_neighborhood':[text]})
    R.verify_amem_contexts({'new_context_neighborhood':['DRV cleared; task failed; rolled back.']})


def test_successful_explicit_retry_is_recognised_but_tampering_is_not(tmp_path):
    original=tmp_path/'extract-005';prefix=Path(str(original)+'.authorized-output-resume-1')
    request=Path(str(prefix)+'.try0.request.json');response=Path(str(prefix)+'.try0.response.json')
    R.save(request,{'messages':[]})
    usage={'prompt_tokens':5,'completion_tokens':10,'total_tokens':15}
    R.save(response,{'choices':[{'finish_reason':'stop','message':{'content':'{"memories":[]}'}}], 'usage':usage})
    R.save(str(original)+'.answer.json',{'answer':{'memories':[]},'usage':usage,
        'explicit_retry':{'new_prefix':str(prefix)}})
    assert R.response_has_cache(response)
    R.save(response,{'choices':[{'finish_reason':'stop','message':{'content':'{"memories":["changed"]}'}}], 'usage':usage})
    with pytest.raises(ValueError,match='cache'):
        R.response_has_cache(response)


def test_actual_runaway_amem_reply_is_rejected_offline():
    path=Path('/data2/zhangdy/tehm-experiments/t2-agent9-followup8-20261021-r5/folds/C02881/controls/construction/AMEM/evolution-0011.try0.response.json')
    raw=R.load(path);text=raw['choices'][0]['message']['content']
    # The first string is complete, but a later context never terminates.
    prefix=text.split('"new_context_neighborhood": [',1)[1].lstrip()
    first,end=json.JSONDecoder().raw_decode(prefix)
    partial=prefix[end:]
    R.verify_amem_contexts({'new_context_neighborhood':[first]})
    with pytest.raises(RuntimeError,match='repeated'):
        R.verify_amem_contexts({'new_context_neighborhood':[partial]})


@pytest.mark.parametrize('repetitive',[False,True])
def test_real_amem_failed_request_uses_one_explicit_summary_retry(tmp_path,monkeypatch,repetitive):
    import shutil
    source=Path('/data2/zhangdy/tehm-experiments/t2-agent9-followup8-20261021-r5/folds/C02881/controls/construction/AMEM')
    project=tmp_path/'controls/construction/AMEM';shutil.copytree(source,project)
    # Restore the original incident checkpoint in scratch; the live source now
    # includes the successful authorised recovery and must never be modified.
    (project/'evolution-0011.answer.json').unlink(missing_ok=True)
    for p in project.glob('evolution-0011.authorized-output-resume-1.*'):p.unlink()
    before={str(p):R.sha(p) for p in project.glob('*.json')}
    rev=tmp_path/'revision';rev.mkdir()
    response=project/'evolution-0011.try0.response.json';request=project/'evolution-0011.try0.request.json'
    body=R.load(request);prompt=body['messages'][1]['content']
    R.save(rev/'authorization.json',{'amem_context_policy':R.AMEM_CONTEXT_POLICY,'truncated_requests':{
        str(response.resolve()):{'response_sha256':R.sha(response),'request_sha256':R.sha(request)}}})
    sent=[]
    answer={'should_evolve':True,'actions':['strengthen','update_neighbor'],
        'suggested_connections':[],'tags_to_update':['DRC','rolled_back'],
        'new_context_neighborhood':[
            'C01295: m3.2 cleared by density relief, but task failed its area cap and rolled back.'
        ]*5,'new_tags_neighborhood':[['DRC','rolled_back']]*5}
    if repetitive:answer['new_context_neighborhood'][0]=('export DESIGN_NAME = sqrt; '+'full_report/'*30)*20
    def wire(key,messages,prefix,**kwargs):
        sent.append(prefix)
        assert messages[1:]==body['messages'][1:] # No history, neighbors or labels dropped.
        assert R.AMEM_CONTEXT_INSTRUCTION in messages[0]['content']
        assert kwargs['sampling_override']['max_tokens']==393216
        raw={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(answer)}}],
             'usage':{'prompt_tokens':8000,'completion_tokens':600,'total_tokens':8600}}
        R.save(str(prefix)+'.try0.request.json',{'messages':messages,'max_tokens':393216})
        R.save(str(prefix)+'.try0.response.json',raw)
        return json.dumps(answer),raw['usage']
    fake=SimpleNamespace(llm_call=wire,LLM_API_MODEL='deepseek-flash',_ledger=lambda *a:(0,''))
    monkeypatch.setattr(C,'Calls',C.Calls);R.install_constructor(rev)
    c=object.__new__(C.Calls);c.project=project;c.H=fake;c.count=0;c.key='mock';c.tolerate=False
    R.configure_harness(fake)
    if repetitive:
        with pytest.raises(RuntimeError,match='repeated'):c.ask('evolution-0011',prompt,.7,1000)
        assert not (project/'evolution-0011.answer.json').exists()
        with pytest.raises(RuntimeError,match='already sent'):c.ask('evolution-0011',prompt,.7,1000)
    else:
        assert c.ask('evolution-0011',prompt,.7,1000)==answer
        assert c.ask('evolution-0011',prompt,.7,1000)==answer
        assert R.response_has_cache(Path(str(sent[0])+'.try0.response.json'))
        assert R.load(project/'evolution-0011.answer.json')['amem_context_policy']==R.AMEM_CONTEXT_POLICY
    assert len(sent)==1
    for p,h in before.items():assert R.sha(p)==h
