import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
PREP = ROOT.parent/'t4-swe-agent-adaptation-20261026'
os.environ['LITELLM_LOCAL_MODEL_COST_MAP']='True'
sys.path[:0]=[str(ROOT/'adapters'),str(PREP/'sources/SWE-agent')]


def test_mounts_exclude_keys_history_and_evaluator(tmp_path):
    from local_sandbox import sandbox_args
    repo=tmp_path/'repo';repo.mkdir()
    home=tmp_path/'home';home.mkdir()
    args=sandbox_args(repo,home)
    assert '--clearenv' in args and '--unshare-all' in args
    assert '/data1/zhangdy/.llm_env' not in args
    assert not any('evaluator-private' in v for v in args)
    assert '/data1/zhangdy' not in args
    binds=[args[i+1:i+3] for i,v in enumerate(args) if v=='--bind']
    assert binds==[[str(repo),'/testbed'],[str(home),'/root']]


def test_selector_is_order_independent_and_spreads_repositories():
    from select_tasks import select
    rows=[{'instance_id':f'{r}-{i}','repo':r} for r in ['a','b','c'] for i in range(10)]
    a=select(rows,6)
    assert a==select(list(reversed(rows)),6)
    assert {r:sum(t['repo']==r for t in a) for r in ['a','b','c']}=={'a':2,'b':2,'c':2}


def test_live_native_local_runtime_is_isolated(tmp_path):
    from local_sandbox import native_environment
    repo=tmp_path/'repo';repo.mkdir()
    home=tmp_path/'home';home.mkdir()
    (repo/'hello.txt').write_text('fixture only')
    env=native_environment(repo,home,PREP/'envs/runtime',tmp_path/'server.log')
    try:
        env.start()
        assert 'fixture only' in env.communicate('cat /testbed/hello.txt',check='raise')
        output=env.communicate('test ! -e /data1/zhangdy/.llm_env && test ! -e /data1/zhangdy/.aws && echo ISOLATED',check='raise')
        assert 'ISOLATED' in output
        env.write_file('/root/native-file.txt','native SWE-ReX write')
        assert env.read_file('/root/native-file.txt')=='native SWE-ReX write'
    finally:
        env.close()
