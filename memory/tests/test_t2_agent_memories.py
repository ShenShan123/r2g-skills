"""Native ports, metering, consumer receipts and automatic continuation: no paid calls."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest

sys.path[:0]=[str(Path(__file__).parents[1]/'scripts'),str(Path(__file__).parents[1])]
import t2_agent_new_methods as M
import t2_agent_memories as P
import t2_agent_followup as F
import t2_agent_dispatch as D
import t2_agent_runtime as R


def test_reme_declares_pinned_default_not_full_rewrite_or_main():
    c=M.CONFIG['REME']
    assert c['upstream_commit']==M.REME_SHA and c['upstream_tag']=='v0.2.0.6'
    assert not c['llm_rerank'] and not c['llm_rewrite'] and not c['online_update']
    assert c['soft_comparison'] and c['validation_threshold']==.5


def test_reasoningbank_no_matts_and_one_prior_trajectory():
    c=M.CONFIG['REASONINGBANK']
    assert c['retrieved_trajectories']==1 and c['max_items_per_trajectory']==3
    assert c['MaTTS'] is False and c['online_update'] is False


def test_pinned_prompts_are_available_and_parse_without_sdks():
    upstream=F.upstream_sources()
    rb=next(p for p,r in upstream if str(r).endswith('/instruction.py'))
    assert 'failed' in M.rb_prompts(rb)['FAILED_SI']
    p=next(p for p,r in upstream if str(r).endswith('/success_extraction_prompt.yaml'))
    text=M.yaml_prompt(p,'success_step_task_memory_prompt').format(query='x',step_sequence='y',context='z',outcome='successful')
    assert 'when_to_use' in text and 'successful' in text


@pytest.mark.parametrize('answer',[{}, {'memories':None}, {'memories':[{}]},
    {'memories':[{'when_to_use':'','experience':'x'}]}, {'memories':[{'when_to_use':'a','experience':'b'}]*4}])
def test_reme_malformed_not_silently_valid(answer):
    with pytest.raises(ValueError):M.native_items(answer)


def test_empty_reme_is_valid_but_no_invented_memory():
    assert M.native_items({'memories':[]})==[]


def test_reasoningbank_parses_native_headings_and_all_items():
    item='# Memory Item 1\n## Title A\n## Description B\n## Content C'
    assert M.parse_rb(item)==[{'title':'A','description':'B','content':'C'}]
    assert len(M.parse_rb(item+'\n'+item.replace('Item 1','Item 2')))==2
    with pytest.raises(ValueError):M.parse_rb('ordinary summary')


def test_cosine_dedup_preserves_first_and_records_dropped_ids():
    kept,dropped=M.cosine_dedup([{'id':'a'},{'id':'b'},{'id':'c'}],[[1,0],[.9,.1],[0,1]])
    assert [r['id'] for r in kept]==['a','c'] and dropped==['b']
    with pytest.raises(ValueError):M.cosine_dedup([{'id':'bad'}],[[0,0]])


@pytest.mark.parametrize('arm', ['REME','REASONINGBANK'])
def test_native_retrieval_preserves_method_specific_selection(tmp_path,monkeypatch,arm):
    queries=[]
    class Index:
        def __init__(self,model,texts):assert model==M.MODEL;assert len(texts)==2
        def search(self,q,k):queries.append(k);return [1]
    monkeypatch.setattr(M.A,'DenseIndex',Index)
    rows=([{'id':str(i),'when_to_use':'scenario'+str(i),'experience':'rule'+str(i)} for i in range(2)] if arm=='REME'
          else [{'id':str(i),'query':'scenario'+str(i),'items':[{'content':'one'},{'content':'two'}]} for i in range(2)])
    (tmp_path/(arm+'.json')).write_text(json.dumps({'status':'FROZEN','configuration':M.CONFIG[arm],
                              'memories' if arm=='REME' else 'trajectories':rows}))
    text,ids=M.NativeMemory(arm,tmp_path).context('query')
    assert ids==['1'] and queries==([5] if arm=='REME' else [1])
    if arm=='REASONINGBANK':assert 'one' in text and 'two' in text


def test_all_eight_arms_and_eight_disjoint_slots_without_lc_overlap():
    assert set(F.ARMS)==set(D.ARMS)-{'NB'} and len(F.ARMS)==8
    cpus=[c for slot in F.SLOTS for c in slot]
    assert len(cpus)==128==len(set(cpus))
    assert not set(cpus)&(set(range(74,82))|set(range(170,178)))


def test_worker_keeps_new_nb_host_and_binds_full_native_memories():
    from t2_agent_nb_package import integrate_batch
    repo=Path(__file__).parents[2]
    old=(repo/'memory/scripts/t2a_batch.py').read_text()
    text=F.integrate_worker(integrate_batch(old))
    compile(text,'frozen-memory-worker','exec')
    assert "bind_snapshot(root/'agent-store'" in text
    assert 'make_memory(H,project,arm,root,env)' in text
    assert 'mark_engineer_restore,memory=memory)' in text
    assert "This first stage registers NB only" not in text
    assert "['llm_requests']>3" in text


def test_old_nb_version_cannot_trigger_new_queue(tmp_path):
    F.save(tmp_path/'registration.json',{'agent_selected_policy':'deterministic-first'})
    with pytest.raises(ValueError,match='deterministic'):F.nb_gate(tmp_path)


def test_waiting_new_nb_does_not_build_or_launch(tmp_path,monkeypatch):
    F.save(tmp_path/'registration.json',{'agent_selected_policy':D.VERSION,'online_total_request_cap':3})
    monkeypatch.setattr(F.F,'nb_gate',lambda p:False)
    assert F.nb_gate(tmp_path) is False


def test_fourth_nb_request_blocks_queue(tmp_path,monkeypatch):
    F.save(tmp_path/'registration.json',{'agent_selected_policy':D.VERSION,'online_total_request_cap':3})
    F.save(tmp_path/'episodes/x/episode.json',{'accounting':{'llm_requests':4}})
    monkeypatch.setattr(F.F,'nb_gate',lambda p:True)
    with pytest.raises(ValueError,match='opportunity'):F.nb_gate(tmp_path)


def test_native_policy_never_executes_catalogue_head():
    m=object.__new__(P.Memory);m.arm='TM0';m.native_failures=0;m.used=[]
    m.H=SimpleNamespace(observation=lambda p:{});m.project=None;m.route_state=None
    m.current_situation=lambda:{}
    def route(*a):m.route_state={'knowledge_route':'NO_SKILL'};return '',[]
    m.route_knowledge=route
    m.diagnose=lambda:{'strategies':[{'id':'public','source':'catalogue'},
                                                            {'id':'native','source':'tehm_rule'}]}
    original=P.T.event
    try:
        P.T.event=lambda *a:None
        assert m.native_action() is None
        m.diagnose=lambda:{'strategies':[{'id':'native','source':'tehm_rule'}]}
        assert m.native_action() is None # TEHM offers adaptable advice, never forced replay.
        m.used=['native'];assert m.native_action() is None
    finally:P.T.event=original


def test_native_apply_never_forges_model_turn(tmp_path,monkeypatch):
    from test_t2_agent_runtime import fake_runtime
    h,env,calls,events=fake_runtime(tmp_path,monkeypatch,['invalid'])
    selected=[]
    class Mem:
        def native_action(self):return {'id':'recipe1'}
        def apply_native(self,a):selected.append(a);return 'place',{'applied':'recipe1'}
        def native_outcome(self,a,r):assert r['verdict']['accepted']
    def execute(*a,native_apply=None):
        assert a[5]=={} and native_apply is not None
        native_apply();return {'status':'RETAINED','rolled_back':False,'verdict':{'accepted':True}}
    monkeypatch.setattr(R,'execute_candidate',execute)
    out=R.run(h,tmp_path,'RECIPE',env,'unused',{'accepted':False},lambda p:None,Mem())
    assert out['last']['accepted'] and calls==[] and selected==[{'id':'recipe1'}]


def test_consumer_receipt_requires_actual_serialized_request(tmp_path,monkeypatch):
    m=object.__new__(P.Memory);m.receipt={'arm':'REME'};m.project=tmp_path;m.H=None
    monkeypatch.setattr(P.T,'event',lambda *a:None)
    messages=[{'role':'user','content':'selected memory'}]
    with pytest.raises(D.IntegrityStop):m.consume(messages,tmp_path/'agent-decision-1')
    (tmp_path/'agent-decision-1.try0.request.json').write_text(json.dumps({'messages':messages}))
    m.consume(messages,tmp_path/'agent-decision-1')
    with pytest.raises(ValueError):m.consume([{'role':'user','content':'changed'}],tmp_path/'agent-decision-1')


def test_new_adapters_cannot_import_provider_clients():
    for module in (M,P):
        tree=__import__('ast').parse(Path(module.__file__).read_text())
        for node in __import__('ast').walk(tree):
            if isinstance(node,__import__('ast').Import):names=[n.name for n in node.names]
            elif isinstance(node,__import__('ast').ImportFrom):names=[node.module or '']
            else:continue
            assert not any(n.split('.')[0] in ('openai','google','httpx','requests','urllib') for n in names)


@pytest.mark.parametrize('arm',['REME','REASONINGBANK'])
def test_full_construction_port_with_mocked_provider_preserves_labels(tmp_path,monkeypatch,arm):
    import shutil
    for src,rel in F.upstream_sources():
        dest=tmp_path/'code/upstream'/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dest)
    F.save(tmp_path/'registration.json',{'code':{}})
    traces=[{'id':'success','task':'taskA','text':'SUCCESS TRACE','success':True,'situation':{},'units':['a']},
            {'id':'failure','task':'taskB','text':'FAILED TRACE','success':False,'situation':{},'units':['b']}]
    monkeypatch.setattr(M.C,'construction_source',lambda r:([], 'shared123'))
    monkeypatch.setattr(M.A,'trajectories',lambda u:traces)
    calls=[]
    class Calls:
        def __init__(self,*a,**kw):self.count=0
        def ask(self,ident,prompt,temp):
            calls.append((ident,prompt));self.count+=1
            if arm=='REASONINGBANK':return {'markdown':'# Memory Item 1\n## Title Rule\n## Description Test\n## Content Advice'}
            if ident.startswith('validate'):return {'is_valid':True,'score':.8}
            return {'memories':[{'when_to_use':ident,'experience':'advice'}]}
    class Index:
        def __init__(self,*a):pass
        def encode(self,texts):return [[1 if i==j else 0 for j in range(len(texts))] for i in range(len(texts))]
    monkeypatch.setattr(M.C,'Calls',Calls);monkeypatch.setattr(M.A,'DenseIndex',Index)
    M.build(tmp_path,arm)
    built=F.load(tmp_path/(arm+'.json'))
    assert built['units_sha256']=='shared123' and built['status']=='FROZEN'
    assert built['configuration']['online_update'] is False
    if arm=='REME':
        assert [i for i,p in calls]==['extract-000','extract-001','soft-comparison','validate-000','validate-001','validate-002']
        assert len(built['memories'])==3
    else:
        assert len(calls)==2 and built['trajectories'][1]['success'] is False
        assert 'successfully resolved' in calls[0][1] and 'attempted to resolve' in calls[1][1]


def test_construction_helpers_bind_shared_unknown_stop_ledger(tmp_path,monkeypatch):
    import t2a_construct as C
    budget=tmp_path/'global.json';F.save(budget,{'caps':{},'reserve':{},'halt_on_unknown_usage':True})
    F.save(tmp_path/'registration.json',{'phase_binding':{'budget_file':str(budget),
        'budget_sha256':F.sha(budget),'ledger_path':str(tmp_path/'global-ledger.jsonl')}})
    for name in ('R2G_BUDGET_FILE','R2G_REQUIRE_LLM_BUDGET','R2G_PHASE_LEDGER','R2G_PHASE_BUDGET_FILE','R2G_BUDGET_COMPONENT'):
        monkeypatch.setenv(name,'before')
    monkeypatch.setattr(C.Q,'module',lambda *a:SimpleNamespace(llm_key=lambda p:'test-not-real'))
    C.Calls(tmp_path,'REME')
    import os
    assert os.environ['R2G_PHASE_LEDGER']==str(tmp_path/'global-ledger.jsonl')
    assert os.environ['R2G_PHASE_BUDGET_FILE']==str(budget)


@pytest.mark.parametrize('fault',[False,True])
def test_construction_parallel_no_implicit_retry_and_drain(tmp_path,fault):
    (tmp_path/'logs').mkdir();seen=[];peak=[0];active=[0]
    class Worker:
        def __init__(self,cmd,**kwargs):
            self.pid=len(seen)+100;self.returncode=None;self.polled=False;self.finished=False
            key=(cmd[cmd.index('--root')+1],cmd[cmd.index('--arm')+1]);assert key not in seen
            seen.append(key);self.fail=fault and len(seen)==1
            active[0]+=1;peak[0]=max(peak[0],active[0])
        def poll(self):
            if not self.polled:self.polled=True;return None
            if not self.finished:
                self.finished=True;active[0]-=1;self.returncode=1 if self.fail else 0
            return self.returncode
    folds=[tmp_path/'foldA',tmp_path/'foldB']
    if fault:
        with pytest.raises(RuntimeError):F.construct_all(tmp_path,folds,popen=Worker,sleep=lambda _:None)
    else:F.construct_all(tmp_path,folds,popen=Worker,sleep=lambda _:None)
    assert peak[0]==4 and active[0]==0 and len(seen)==(4 if fault else 10)
