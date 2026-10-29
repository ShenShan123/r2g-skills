import json
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest

sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
import t2a_adapters as A
import t2_agent_memories as M


def test_full_awm_context_has_no_hidden_72kb_limit():
    m=object.__new__(A.PromptMemory);m.arm='AWM';m.data={'workflows':{A.stable([None,None,None]):'step '*20000}}
    text,ids=m.context('current',{},max_bytes=None)
    assert len(text.encode())==100000 and ids==[]


@pytest.mark.parametrize('answer',['[]','null','"text"','17','{"keywords":[]}',
                                    '{"keywords":["", "  "]}'])
def test_malformed_or_empty_amem_query_does_not_crash_or_retrieve_unrelated_memory(tmp_path,monkeypatch,answer):
    (tmp_path/'shared').mkdir();(tmp_path/'shared/manifest.json').write_text('{"units_sha256":"source"}')
    selected=[]
    class Index:
        def search(self,*args):selected.append(args);return []
    inner=object.__new__(A.PromptMemory);inner.arm='AMEM';inner.data={};inner.index=Index();inner.rows=[];inner.by_id={}
    memory=object.__new__(M.Memory);memory.H=SimpleNamespace();memory.project=tmp_path;memory.root=tmp_path
    memory.arm='AMEM';memory.helpers=0;memory.inner=inner;memory.current_situation=lambda:{}
    calls=[];gateway=SimpleNamespace(call=lambda *args,**kw:(calls.append(1) or (answer,{'total_tokens':3})))
    monkeypatch.setattr(M.T,'event',lambda *args:None)
    assert memory.agent_context({},gateway)==''
    assert calls==[1] and selected==[]


def test_retrieval_respects_native_top_k_without_an_extra_byte_budget():
    m=object.__new__(A.PromptMemory);m.arm='RET';m.data={};m.rows=[{'id':'one','text':'x'*100000}]
    m.index=SimpleNamespace(search=lambda query,k:[0])
    text,ids=m.context('current',{},max_bytes=None)
    assert text=='x'*100000 and ids==['one']


@pytest.mark.parametrize('arm',['NB','RET','RECIPE','EXPEL','AWM','AMEM','ADAMEM','REASONINGBANK','TM0'])
def test_all_nine_registered_arms_reach_their_own_adapter(tmp_path,monkeypatch,arm):
    import t2_adamem as ADA
    import t2_freepdk45_main as MAIN
    assert arm in MAIN.ARMS and 'REME' not in MAIN.ARMS
    made=[]
    def adapter(label):
        def make(*args,**kwargs):made.append(label);return SimpleNamespace(label=label)
        return make
    monkeypatch.setattr(M,'Memory',adapter('native-'+arm))
    monkeypatch.setattr(ADA,'AdaMemory',adapter('ADAMEM'))
    result=M.make_memory(None,tmp_path,arm,tmp_path,{})
    if arm=='NB':assert result is None and made==[]
    elif arm=='ADAMEM':assert result.label=='ADAMEM' and made==['ADAMEM']
    else:assert result.label=='native-'+arm and made==['native-'+arm]


@pytest.mark.parametrize('arm',['NB','RET','RECIPE','EXPEL','AWM','AMEM','ADAMEM','REASONINGBANK','TM0'])
def test_shared_agent_stops_after_three_invalid_requests_for_every_arm(tmp_path,monkeypatch,arm):
    import t2_agent_runtime as R
    from test_t2_agent_runtime import fake_runtime
    h,env,calls,events=fake_runtime(tmp_path,monkeypatch,['invalid'])
    # Adapter algorithms have separate tests. At the common executor boundary
    # no arm name may silently grant another generation or fallback request.
    memory=None if arm=='NB' else SimpleNamespace(agent_context=lambda *args:'',consume=lambda *args:None)
    if arm=='ADAMEM':
        memory.decide=lambda messages,gateway,prefix:gateway.call(messages,prefix,purpose='planning')
    result=R.run(h,tmp_path,arm,env,'unused',{'accepted':False},lambda p:None,memory)
    assert len(calls)==3 and result['stop_reason']=='online_allowance_spent'
