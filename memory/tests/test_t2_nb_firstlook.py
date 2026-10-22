import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

P = Path(__file__).parents[1] / 'scripts/t2_nb_firstlook.py'


@pytest.fixture
def op():
    spec = importlib.util.spec_from_file_location('nb_firstlook_test', P)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def identity(i, *, owner='same-author', ip=None, hashes=None):
    return dict(handoff_id=i, verified_ip_key=ip or i, owner=owner,
                repository='https://github.com/shared-mirror/collection',
                core_rtl_hashes=hashes or [i], candidate_group_id='coarse-owner-group')


def test_same_author_mirror_coarse_group_and_generic_module_do_not_exclude(op):
    a, b = identity('A'), identity('B')
    a['modules'] = b['modules'] = ['top', 'fifo']
    assert op.relationship(a, b) == 'KEEP'


def test_same_ip_variants_and_complete_rtl_duplicates_excluded(op):
    assert op.relationship(identity('A', ip='ip'), identity('B', ip='ip')) == 'EXCLUDE'
    assert op.relationship(identity('A', hashes=['x']), identity('B', hashes=['x'])) == 'EXCLUDE'


def test_partial_shared_implementation_requires_review_not_blanket_exclusion(op):
    assert op.relationship(identity('A', hashes=['a', 'common']), identity('B', hashes=['b', 'common'])) == 'REVIEW'


def test_filter_uses_origin_not_mentions_and_keeps_every_other_design(op):
    registry = {x: identity(x) for x in 'ABCDEFGH'}
    units = [dict(unit_id=x, source_handoff_id=x, text='A fifo same-author') for x in registry]
    for target in registry:
        keep, drop = op.filter_history(units, target, registry)
        assert [u['source_handoff_id'] for u in drop] == [target]
        assert {u['source_handoff_id'] for u in keep} == set(registry) - {target}
    with pytest.raises(ValueError, match='unbound origin'):
        op.filter_history([dict(unit_id='bad', text='A')], 'A', registry)


def test_filter_excludes_reviewed_same_ip_but_not_other_ip_from_same_repo(op):
    registry = {'A': identity('A', ip='ip'), 'variant': identity('variant', ip='ip'), 'B': identity('B')}
    units = [dict(source_handoff_id=x) for x in registry]
    keep, drop = op.filter_history(units, 'A', registry)
    assert [u['source_handoff_id'] for u in keep] == ['B']
    assert len(drop) == 2


def project_fixture(op, tmp_path):
    p = tmp_path / 'project'
    (p/'constraints').mkdir(parents=True)
    (p/'constraints/config.mk').write_text('export CORE_UTILIZATION = 20\n')
    m = dict(source_project=str(p), initial_config_sha256=op.sha(p/'constraints/config.mk'),
             area_reference_policy=op.REFERENCE_POLICY,
             area={k:dict(ref=None, max_rel_increase=c, unit='um^2') for k,c in op.CAPS.items()})
    op.save(p/'manifest.json', m)
    r = p/'backend/RUN_0'
    (r/'logs').mkdir(parents=True)
    op.save(r/'effective-config.json', dict(status='PASS', project_config_sha256=m['initial_config_sha256'],
                                             manifest_sha256=op.sha(p/'manifest.json'), run_dir=str(r)))
    (r/'stage_log.jsonl').write_text('{"stage":"floorplan","status":0}\n')
    op.save(r/'logs/2_1_floorplan.json', {v:100+i for i,v in enumerate(op.FLOOR_KEYS.values())})
    return p,r,m


def test_reference_is_first_unrepaired_flow_and_never_changes_manifest(op, tmp_path):
    p,r,m=project_fixture(op,tmp_path);before=op.sha(p/'manifest.json')
    out=op.resolve_area_reference(m)
    assert [out['area'][k]['ref'] for k in op.CAPS] == [100,101,102]
    assert op.sha(p/'manifest.json') == before and m['area']['core']['ref'] is None
    later=p/'backend/RUN_1';(later/'logs').mkdir(parents=True)
    op.save(later/'logs/2_1_floorplan.json', {v:999 for v in op.FLOOR_KEYS.values()})
    assert op.resolve_area_reference(m)==out


@pytest.mark.parametrize('bad', ['config', 'binding', 'metrics', 'stage'])
def test_bad_initial_reference_fails_closed(op,tmp_path,bad):
    p,r,m=project_fixture(op,tmp_path)
    if bad=='config':(p/'constraints/config.mk').write_text('export CORE_UTILIZATION = 40\n')
    if bad=='binding':op.save(r/'effective-config.json',{'status':'PASS'})
    if bad=='metrics':op.save(r/'logs/2_1_floorplan.json',{})
    if bad=='stage':(r/'stage_log.jsonl').write_text('{"stage":"floorplan","status":2}\n')
    with pytest.raises(ValueError):op.resolve_area_reference(m)


def test_existing_reference_receipt_cannot_be_tampered(op,tmp_path):
    p,r,m=project_fixture(op,tmp_path);op.resolve_area_reference(m)
    f=p/'reports/initial-area-reference.json';v=json.loads(f.read_text());v['areas']['core']=1e9;op.save(f,v)
    with pytest.raises(ValueError,match='reference receipt'):op.resolve_area_reference(m)


def test_four_slots_do_not_overlap_learning_curve(op):
    cpus=[n for s in op.SLOTS for n in s]
    assert len(op.SLOTS)==4 and len(cpus)==len(set(cpus))
    assert not set(cpus)&(set(range(74,82))|set(range(170,178)))


def test_clock_annotation_does_not_change_original_clock_commands(op):
    raw='if {1} {\n create_clock -name core_clk -period 5 clk\n}\n'
    out=op.annotate_clock_period(raw,5.0)
    assert out.endswith(raw) and 'set clk_period 5.0\n' in out
    with pytest.raises(ValueError):op.annotate_clock_period(raw,10.)


def test_checked_replacement_never_changes_ambiguous_dependency(op,tmp_path):
    p=tmp_path/'dependency.py';p.write_text('old\nold\n')
    with pytest.raises(ValueError,match='unexpected frozen dependency'):
        op.checked_replace(p,'old','new')
    assert p.read_text()=='old\nold\n'


@pytest.mark.skipif(not os.environ.get('T2_NB_PACKAGE'),reason='needs a prepared private runtime')
def test_exact_prepared_private_runtime_and_four_slot_dispatch(tmp_path):
    # A fresh process avoids worktree modules cached by other test suites. No
    # provider or EDA function is called. Only worker Popen is faked below.
    script=r'''
import json,os,sys
from pathlib import Path
root,scratch=map(Path,sys.argv[1:])
sys.path.insert(0,str(root/'code/memory/scripts'))
import t2_nb_firstlook as O
import t2a_batch as B
import t2a_envelope as E
import t2a_runtime as T
assert Path(O.__file__).is_relative_to(root/'code')
assert Path(B.__file__).is_relative_to(root/'code')
reg=B.preflight(root,'TEST')
before=O.sha(root/'registration.json')
assert reg['arms']==['NB'] and len(reg['plan'])==8 and reg['paper_data'] is False
assert O.load(root/'episode-budget.json')['caps']=={'repair_attempts':3}
assert O.load(root/'phase-budget.json')['caps']=={}
assert O.load(root/'phase-budget.json')['halt_on_unknown_usage'] is True
audit=O.load(root/'lineage-audit.json')
assert sum(v=='EXCLUDE' for row in audit['matrix'].values() for v in row.values())==8
assert sum(v=='KEEP' for row in audit['matrix'].values() for v in row.values())==56
for task in reg['tasks']:
    m=O.load(Path(task['template'])/'manifest.json')
    assert m['platform']=='sky130hd' and m['expected_clock_period_ns']==5
    assert not m['allowlist'].keys() & {'SDC_FILE','CLOCK_PERIOD','VERILOG_FILES','DESIGN_NAME'}
    text=(Path(task['template'])/'constraints/constraint.sdc').read_text()
    original=(Path(task['template'])/'constraints/source-constraint.sdc.reference').read_text()
    assert text.endswith(original) and 'set clk_period 5.0\n' in text

p=scratch/'project';(p/'constraints').mkdir(parents=True)
(p/'constraints/config.mk').write_text('export CORE_UTILIZATION = 20\n')
m={'source_project':str(p),'initial_config_sha256':O.sha(p/'constraints/config.mk'),
   'area_reference_policy':O.REFERENCE_POLICY,
   'area':{k:{'ref':None,'max_rel_increase':cap,'unit':'um^2'} for k,cap in O.CAPS.items()}}
O.save(p/'manifest.json',m);manifest_sha=O.sha(p/'manifest.json')
r=p/'backend/RUN_0';(r/'logs').mkdir(parents=True)
O.save(r/'effective-config.json',{'status':'PASS','run_dir':str(r),
 'manifest_sha256':manifest_sha,'project_config_sha256':m['initial_config_sha256']})
(r/'stage_log.jsonl').write_text('{"stage":"floorplan","status":0}\n')
O.save(r/'logs/2_1_floorplan.json',{v:100 for v in O.FLOOR_KEYS.values()})
assert E.check_area(m,{'stdcell':110,'core':150,'die':150})['ok']
assert not E.check_area(m,{'stdcell':156,'core':150,'die':150})['ok']
assert {k:v['ref'] for k,v in T.public_task_contract(m)['area'].items()}==dict.fromkeys(O.CAPS,100)
assert O.sha(p/'manifest.json')==manifest_sha
receipt=p/'reports/initial-area-reference.json';bad=O.load(receipt);bad['areas']['core']=1e9;O.save(receipt,bad)
try:T.public_task_contract(m)
except ValueError:pass
else:raise AssertionError('tampered area receipt accepted')

def dispatcher_check(label,fault):
    work=scratch/label;work.mkdir();O.save(work/'registration.json',reg)
    B.preflight=lambda *args:reg
    O.os.sched_getaffinity=lambda _:set(range(192))
    O.time.sleep=lambda _:None
    active=[0];peak=[0];launches=[]
    class Worker:
        def __init__(self,cmd,**kwargs):
            assert cmd[0]=='taskset' and cmd[cmd.index('--arm')+1]=='NB'
            assert '--repeat' in cmd and cmd[cmd.index('--repeat')+1]=='1'
            name=cmd[cmd.index('--task')+1].replace('/','__')+'__NB__r1'
            assert name not in launches;launches.append(name)
            active[0]+=1;peak[0]=max(peak[0],active[0]);self.waited=False;self.done=False
            self.pid=90000000+len(launches);self.returncode=None
            O.save(work/'episodes'/name/'episode.json',{'status':
                'ACCOUNTING_FAILURE' if fault and len(launches)==1 else
                'ACCEPTED' if len(launches)%2 else 'NOT_ACCEPTED',
                'accounting':{'passed':not(fault and len(launches)==1)}})
        def poll(self):
            if not self.waited:self.waited=True;return None
            if not self.done:active[0]-=1;self.done=True;self.returncode=0
            return self.returncode
    O.subprocess.Popen=Worker
    O.run(work)
    state=O.load(work/'dispatch-state.json')
    assert peak[0]==4 and active[0]==0
    assert len(launches)==(4 if fault else 8)
    assert state['status']==('PAUSED' if fault else 'COMPLETED')
    assert state['pending']==(4 if fault else 0)
    assert len(O.load(work/'nb-results.json')['rows'])==8
dispatcher_check('complete',False)
dispatcher_check('fault',True)
assert O.sha(root/'registration.json')==before
assert not (root/'batch-start.json').exists()
print('private runtime + real eight-fold matrix + area references + four-slot dispatch + technical pause: PASS; zero EDA/model')
'''
    result=subprocess.run([sys.executable,'-c',script,os.environ['T2_NB_PACKAGE'],str(tmp_path)],
        text=True,capture_output=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
    assert result.returncode==0,result.stdout+result.stderr
