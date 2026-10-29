"""AWM empty output is zero induced workflows, not a retry or fabricated rule."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path[:0]=[str(Path(__file__).parents[1]/'scripts'),str(Path(__file__).parents[1])]
import t2a_construct as C


def test_empty_list_is_an_explicit_zero_workflow_result():
    assert C.workflow_text([])==''


@pytest.mark.parametrize('value',[None,{},'',False,0])
def test_missing_and_malformed_workflows_still_fail(value):
    with pytest.raises(ValueError):C.workflow_text(value)


@pytest.mark.parametrize('value',['workflow text',[{'name':'w','steps':['a','b']}],{'name':'w','steps':['a','b']}])
def test_nonempty_workflows_are_preserved(value):
    assert C.workflow_text(value)==(value if isinstance(value,str) else C.A.stable(value))


def test_exact_real_cached_awm_replay_makes_zero_new_calls(tmp_path):
    root=Path('/data1/zhangdy/RTL/RTL_testbench/_r2g_eval/t2-nb-memory-followup-20261020-r6/folds/C02786/controls')
    if not root.exists():pytest.skip('real paused AWM cache not available')
    script=r'''
import json,sys,shutil,hashlib
from pathlib import Path
source,out,code=map(Path,sys.argv[1:])
sys.path[:0]=[str(code/'memory/scripts'),str(code/'memory')]
import t2a_construct as C
import t2a_adapters as A
shutil.copytree(source,out)
source_output=source/'AWM.json'
source_output_sha=None
if source_output.exists():
 source_output_sha=hashlib.sha256(source_output.read_bytes()).hexdigest()
 # The historical queue has since completed. Only reset the derived output
 # in this scratch copy; all paid replies and accounting remain identical.
 (out/'AWM.json').rename(out/'AWM.completed-source.json')
assert not (out/'AWM.json').exists()
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
old={str(p.relative_to(out)):digest(p) for p in out.rglob('*') if p.is_file() and
     (p.name.endswith(('.answer.json','.request.json','.response.json')) or p.name=='resource_ledger.jsonl')}
Calls=C.Calls;seen=[]
class CachedOnly(Calls):
 def __init__(self,root,arm,*args,**kwargs):
  self.root=root;self.arm=arm;self.project=root/'construction'/arm;self.count=0
 def ask(self,ident,*args,**kwargs):
  assert (self.project/(ident+'.answer.json')).exists(),'Would send a new request: '+ident
  seen.append(ident)
  return super().ask(ident,*args,**kwargs)
C.Calls=CachedOnly
C.construct(out,'AWM')
data=json.loads((out/'AWM.json').read_text())
assert data['status']=='FROZEN' and len(data['empty_workflow_results'])==1
assert seen==['workflow-00-00','workflow-01-00']
assert data['empty_workflow_results'][0]['call_id']=='workflow-01-00'
assert data['workflows'][data['empty_workflow_results'][0]['group']]==''
for rel,h in old.items():assert digest(out/rel)==h,'Historical artifact changed: '+rel
m=A.PromptMemory('AWM',out)
sit=dict(zip(A.GROUP_KEYS,json.loads(data['empty_workflow_results'][0]['group'])))
assert m.context('repair',sit)==('',[])
assert (hashlib.sha256(source_output.read_bytes()).hexdigest() if source_output.exists() else None)==source_output_sha,'Replay modified original queue'
print('REAL_CACHE_REPLAY_PASS: 2 cached answers, 0 new requests, unchanged raw files/ledgers, empty context accepted')
'''
    result=subprocess.run([sys.executable,'-c',script,str(root),str(tmp_path/'controls'),
        str(Path(__file__).parents[2])],capture_output=True,text=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
    assert result.returncode==0,result.stdout+result.stderr


def test_delta_gate_rejects_unrelated_constructor_change(tmp_path):
    import t2_nb_awm_resume as R
    base=R.QUEUE/'code/memory/scripts/t2a_construct.py'
    revised=R.QUEUE/'revisions/awm-empty-v1-20261020/code/memory/scripts/t2a_construct.py'
    old=base.read_text();new=revised.read_text()
    # This gate bound the historical delta, not all later development work.
    R.validate_builder_delta(old,new)
    with pytest.raises(ValueError):R.validate_builder_delta(old,new+'\n# unrelated delta\n')


def test_prefix_protection_rejects_rewriting_prior_cost(tmp_path):
    import t2_nb_awm_resume as R
    p=tmp_path/'ledger';p.write_bytes(b'old cost\n')
    prior=R.prefix_snapshot([p]);p.write_bytes(b'old cost\nnew cost\n');R.verify_prefixes(prior)
    p.write_bytes(b'zero cost\n')
    with pytest.raises(ValueError):R.verify_prefixes(prior)
