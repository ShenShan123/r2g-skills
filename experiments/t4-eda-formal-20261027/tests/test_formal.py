import json
from pathlib import Path
import sys
import tempfile

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'adapters'))
from rtlrepair_eval import compare_oracle,evaluate


def test_complete_corpus_and_shared_order():
    rows=json.loads((ROOT/'inputs/rtlrepair-private.json').read_text())
    public=json.loads((ROOT/'inputs/rtlrepair-roster.json').read_text())
    assert len(rows)==len(public)==77
    assert len({t['source_group'] for t in rows})==24
    assert len({t['task'] for t in rows})==77
    assert [t['task'] for t in rows]==[t['task'] for t in public]
    assert all('reference' not in t and 'oracle' not in t for t in public)


@pytest.mark.parametrize('actual,status',[
    ('time,y\n1,1\n2,0\n','PASS'),
    ('time,y\n1,0\n2,0\n','FAIL'),
    ('time,y\n1,1\n','FAIL'),
    ('time,y\n1,1,0\n2,0\n','UNKNOWN'),
    ('time,y\n','UNKNOWN'),
    ('','UNKNOWN'),
])
def test_oracle_requires_complete_measured_outputs(actual,status):
    assert compare_oracle('time,y\n1,1\n2,0\n',actual)['status']==status


def test_all_dontcare_never_becomes_a_positive():
    assert compare_oracle('time,y\n1,x\n','time,y\n1,0\n')['status']=='UNKNOWN'


def test_unknown_usage_remains_unknown_and_does_not_retry(tmp_path):
    from formal_runtime import EpisodeProvider,EpisodeInterrupted,usage
    p=EpisodeProvider(tmp_path/'provider','OFFLINE')
    calls=[]
    def failed(*args,**kwargs):calls.append(1);raise OSError('offline transport test')
    for _ in range(2):
        with pytest.raises(EpisodeInterrupted):p.request({'messages':[]},transport=failed)
    assert len(calls)==1
    assert usage(p)['actual_total_tokens'] is None
    assert usage(p)['unknown_requests']==1


def fixture(work):
    source=work/'input';source.mkdir()
    buggy='module fixture(input a,b,output y);assign y=a&b;endmodule'
    correct='module fixture(input a,b,output y);assign y=a^b;endmodule'
    (source/'bug.v').write_text(buggy);(source/'gold.v').write_text(correct)
    (source/'tb.v').write_text('module tb;reg a,b;wire y;fixture d(a,b,y);integer f;initial begin '
        'f=$fopen("out.csv","w");$fwrite(f,"time,y\\n");a=0;b=1;#1;$fwrite(f,"1,%b\\n",y);$fclose(f);$finish;end endmodule')
    (source/'oracle.csv').write_text('time,y\n1,1\n')
    return {'task':'OFFLINE-FIXTURE','source_group':'OFFLINE-FIXTURE','top':'fixture',
        'buggy':str(source/'bug.v'),'reference':str(source/'gold.v'),'sources':[str(source/'gold.v')],
        'include_dir':str(source),'tests':[{'kind':'output_oracle','name':'fixture',
            'sources':[str(source/'tb.v')],'oracle':str(source/'oracle.csv'),'output':'out.csv',
            'init_files':[],'timeout':5}]},correct


def test_real_iverilog_differential_and_gold_not_copied():
    work=Path(tempfile.mkdtemp(prefix='differential-',dir=ROOT/'reports'))
    task,correct=fixture(work)
    assert evaluate(task,correct,work/'reference')['status']=='PASS'
    assert evaluate(task,Path(task['buggy']).read_text(),work/'buggy')['status']=='FAIL'
    assert evaluate(task,Path(task['buggy']).read_text(),work/'compile',compile_only=True)['status']=='PASS'
    assert not (work/'buggy/test-00/gold.v').exists()


def test_native_fixer_functional_loop_and_rag_offline():
    if sys.version_info[:2]!=(3,10):pytest.skip('native RTLFixer isolated Python 3.10')
    import os
    from formal_runtime import run_fixer
    from bridge import FullMemory
    work=Path(tempfile.mkdtemp(prefix='fixer-native-',dir=ROOT/'reports'))
    task,correct=fixture(work)
    episode=work/'episode';episode.mkdir();(episode/'error_log').mkdir()
    old=Path.cwd();os.chdir(episode);tempfile.tempdir=str(episode)
    os.environ['PATH']=str(ROOT/'bin')+':/usr/bin:/bin'
    class OfflineProvider:
        model='offline-fixture'
        calls=0
        def request(self,body,**kwargs):
            self.calls+=1
            if body.get('tools'):
                message={'role':'assistant','content':None,'tool_calls':[{'id':'fixture',
                    'type':'function','function':{'name':'verilog_compiler',
                    'arguments':json.dumps({'code_completion':correct})}}]}
            elif 'Is the' in body['messages'][-1].get('content',''):
                message={'role':'assistant','content':'YES'}
            else:
                message={'role':'assistant','content':'```json\n'+json.dumps({
                    'action':'Final Answer','action_input':'The AND should be XOR.'})+'\n```'}
            return {'choices':[{'message':message,'finish_reason':'stop'}],
                'usage':{'prompt_tokens':0,'completion_tokens':0,'total_tokens':0},'model':'offline-fixture','id':'offline'}
    memory=FullMemory('rtlfixer',arm='tehm',root=work)
    try:
        result=run_fixer(task,memory,OfflineProvider(),episode)
        assert result['status']=='ACCEPTED'
        assert (episode/'submitted.v').exists()
        assert any(p.name=='grade.json' for p in (episode/'evaluations').rglob('grade.json'))
    finally:
        memory.close();os.chdir(old);tempfile.tempdir=None


def test_native_scout_tools_and_cost_offline():
    if sys.version_info[:2]<(3,12):pytest.skip('native RTLScout modern Python')
    import os
    from formal_runtime import run_scout
    from bridge import FullMemory
    work=Path(tempfile.mkdtemp(prefix='scout-native-',dir=ROOT/'reports'))
    task,correct=fixture(work)
    episode=work/'episode';episode.mkdir()
    old=Path.cwd();os.chdir(episode);tempfile.tempdir=str(episode)
    os.environ['PATH']=str(ROOT/'bin')+':/usr/bin:/bin'
    os.environ['RTLSCOUT_ENV_FILE']=str(ROOT/'inputs/empty.env')
    class OfflineProvider:
        model='offline-fixture'
        calls=0
        def request(self,body,**kwargs):
            self.calls+=1
            tools={1:('replace_file',{'filename':'design.v','content':correct}),
                   2:('run_evaluation',{'filename':'design.v'})}
            if self.calls in tools:
                name,args=tools[self.calls]
                msg={'role':'assistant','content':None,'tool_calls':[{'id':'fixture'+str(self.calls),
                    'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}
            else:msg={'role':'assistant','content':'Fixture complete.'}
            # Native ReAct has 20 steps and a summary; fake idle replies are
            # local decisions, not provider requests or a measured result.
            return {'choices':[{'message':msg,'finish_reason':'stop'}],
                'usage':{'prompt_tokens':0,'completion_tokens':0,'total_tokens':0},'model':'offline-fixture','id':'offline'}
    memory=FullMemory('rtlscout',arm='tehm',root=work)
    try:
        result=run_scout(task,memory,OfflineProvider(),episode)
        assert result['status']=='ACCEPTED'
        assert (episode/'submitted.v').exists()
    finally:
        memory.close();os.chdir(old);tempfile.tempdir=None
