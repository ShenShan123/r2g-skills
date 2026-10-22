"""Auto followup, no EDA or provider: readiness, leakage, source and dispatch."""
import json
import sys
from pathlib import Path
import pytest

sys.path[:0]=[str(Path(__file__).parents[1]/'scripts'),str(Path(__file__).parents[1])]
import t2_nb_followup as F
import t2a_adapters as A
import t2a_construct as C


def fake_nb(tmp_path,status='RUNNING',bad=None):
    root=tmp_path/'nb';root.mkdir()
    plan=[{'task':f'C{i:05d}/initial','arm':'NB','repeat':1} for i in range(8)]
    F.save(root/'registration.json',{'schema':'nb-firstlook8-v1','arms':['NB'],'plan':plan})
    F.save(root/'dispatch-state.json',{'status':status})
    for i,row in enumerate(plan):
        p=root/'episodes'/(row['task'].replace('/','__')+'__NB__r1')
        F.save(p/'episode.json',{'status':bad if i==0 and bad else 'NOT_ACCEPTED' if i%2 else 'ACCEPTED',
                               'accounting':{'passed':True}})
    return root


def test_waits_for_all_nb_without_starting_anything(tmp_path):
    root=fake_nb(tmp_path)
    assert F.nb_gate(root) is False


def test_nb_measured_failures_do_not_block(tmp_path):
    assert F.nb_gate(fake_nb(tmp_path,'COMPLETED')) is True


@pytest.mark.parametrize('bad',['RUNNING','ACCOUNTING_FAILURE','UNVERIFIED_OR_SAFETY_STOP'])
def test_incomplete_and_technical_nb_block(tmp_path,bad):
    with pytest.raises(ValueError):F.nb_gate(fake_nb(tmp_path,'COMPLETED',bad))


def test_unknown_nb_usage_blocks(tmp_path):
    r=fake_nb(tmp_path,'COMPLETED');p=next((r/'episodes').iterdir())/'reports/resource_ledger.jsonl'
    p.parent.mkdir();p.write_text('{"type":"llm_request","usage_reported":"UNKNOWN"}\n')
    with pytest.raises(ValueError,match='UNKNOWN'):F.nb_gate(r)


def test_seven_cpu_slots_disjoint_from_learning_curve():
    cpus=[x for s in F.SLOTS for x in s]
    assert len(F.SLOTS)==7 and len(cpus)==len(set(cpus))
    assert not set(cpus)&(set(range(74,82))|set(range(170,178)))


def test_new_construction_reads_exact_filtered_history_not_old_source(tmp_path,monkeypatch):
    package=tmp_path/'shared';package.mkdir()
    (package/'shared_units.jsonl').write_text('{"unit_id":"other-only"}\n')
    F.save(package/'manifest.json',{'units_sha256':F.sha(package/'shared_units.jsonl')})
    reg={'shared_package':str(package),'shared_manifest_sha256':F.sha(package/'manifest.json'),
         'shared_units_sha256':F.sha(package/'shared_units.jsonl')}
    monkeypatch.setattr(C.A,'load_source',lambda _:pytest.fail('old SOURCE must not be read'))
    assert C.construction_source(reg)[0]==[{'unit_id':'other-only'}]
    (package/'shared_units.jsonl').write_text('{"unit_id":"target"}\n')
    with pytest.raises(ValueError):C.construction_source(reg)


def test_whole_trajectory_success_does_not_mean_intermediate_positive():
    def unit(status,admitted):
        return {'unit_id':'x','lineage':{'design':'x','platform':'hd','project':'p'},'identity':{'session':'s','ts':'1'},
                'capture_context':{'step_index':0},'raw_row':{},'text_view':'x',
                'shared_label':{'admitted':admitted},'episode_trace_id':'episode','episode_status':status}
    assert A.trajectories([unit('NOT_ACCEPTED',True)])[0]['success'] is False
    assert A.trajectories([unit('ACCEPTED',False)])[0]['success'] is True
    with pytest.raises(ValueError):A.trajectories([unit('RUNNING',True)])


def test_candidate_verdict_object_is_not_a_fix_log_success_string():
    import t2_shared_history as SH
    verdict={'accepted':True,'checks':{'drc':'pass'}}
    u={'unit_id':'llm','raw_row':{'verdict':verdict},'identity':{},
       'shared_label':{'admitted':False,'rolled_back':{'status':'UNKNOWN'}}}
    out=SH.normalise([u])[0]
    assert out['raw_row']['verdict']==verdict
    assert out['capture_context']['session_verdicts']==[None]
    assert out['shared_label']['admitted'] is False


@pytest.mark.parametrize('violations,outcome',[([{'kind':'area_cap_exceeded'}],'cleared_new_failure'),([], 'cleared_unconfirmed')])
def test_area_rejection_cannot_keep_admitted_positive_class(tmp_path,monkeypatch,violations,outcome):
    import t2a_envelope as E
    import t2_shared_history as SH
    (tmp_path/'backend/R').mkdir(parents=True)
    monkeypatch.setattr(E,'area_metrics',lambda _: {})
    monkeypatch.setattr(E,'check_area',lambda *a: {'ok':False,'violations':violations})
    rec={'unit_id':'x','admission':{'status':'ADMITTED'},'binding':{'post':{'run':'R'}}}
    override=F.step_overrides([rec],tmp_path,{})['x']
    result=SH.apply_override({'admitted':True,'outcome_class':'admitted_positive'},override)
    assert result['admitted'] is False and result['outcome_class']==outcome


@pytest.mark.parametrize('fault',[False,True])
def test_dispatch_seven_once_only_no_nb_rerun_and_fault_drains(tmp_path,fault):
    root=tmp_path/'follow';(root/'logs').mkdir(parents=True)
    jobs=[{'root':str(tmp_path/f'fold{i//6}'),'task':f'd{i//6}/initial','arm':F.ARMS[i%6]} for i in range(48)]
    active=[0];peak=[0];seen=[]
    class Worker:
        def __init__(self,cmd,**kwargs):
            self.returncode=None;self.pid=90000000+len(seen);self.waited=False;self.done=False
            arm=cmd[cmd.index('--arm')+1];task=cmd[cmd.index('--task')+1];fold=Path(cmd[cmd.index('--root')+1])
            key=(str(fold),task,arm);assert key not in seen and arm!='NB';seen.append(key)
            active[0]+=1;peak[0]=max(peak[0],active[0])
            name=task.replace('/','__')+'__'+arm+'__r1'
            F.save(fold/'episodes'/name/'episode.json',{'status':'ACCOUNTING_FAILURE' if fault and len(seen)==1 else 'NOT_ACCEPTED',
                                                     'accounting':{'passed':not(fault and len(seen)==1)}})
        def poll(self):
            if not self.waited:self.waited=True;return None
            if not self.done:self.done=True;active[0]-=1;self.returncode=0
            return self.returncode
    state=F.dispatch(root,jobs,popen=Worker,sleep=lambda _:None)
    assert peak[0]==7 and active[0]==0
    assert len(seen)==(7 if fault else 48)
    assert state['pending']==(41 if fault else 0)
    assert state['status']==('PAUSED' if fault else 'COMPLETED')


def test_nb_llm_and_clean_initial_are_kept_without_fabricating_fix_evidence(tmp_path):
    nb=fake_nb(tmp_path,'COMPLETED');reg={}
    for i,row in enumerate(F.load(nb/'registration.json')['plan']):
        hid=f'C{i:05d}';reg[hid]={'task':row['task'],'handoff_id':hid,'verified_ip_key':hid,'core_rtl_hashes':[hid]}
        p=nb/'episodes'/(row['task'].replace('/','__')+'__NB__r1')
        F.save(p/'manifest.json',{'initial_config':{'CORE_UTILIZATION':'20'}})
        e=F.load(p/'episode.json');e.update(started=1,resources={},repair_turns=[{'proposal':'{"CORE_UTILIZATION":15}','rolled_back':True}])
        F.save(p/'episode.json',e);(p/'reports').mkdir()
        (p/'reports/resource_ledger.jsonl').write_text('{"type":"repair_candidate","proposal":"raw proposal","rolled_back":true}\n')
    units,inputs=F.raw_units(nb,reg)
    assert len(units)==16
    kept,dropped=F.N.filter_history(units,'C00000',reg)
    assert len(kept)==14 and len(dropped)==2
    assert {u['source_handoff_id'] for u in kept}==set(reg)-{'C00000'}
    assert any(u.get('episode_context',{}).get('LLM_candidates') for u in kept)
    assert all(not u['action']['from_stage'] for u in units)
    assert len(inputs)==24


def test_embedding_environment_does_not_replace_r2g_interpreter(monkeypatch):
    import t2_embedding_broker as EB
    sentinel=object();seen=[]
    monkeypatch.setenv('R2G_EMBEDDING_PYTHON','/pinned/ml/python')
    monkeypatch.setattr(EB,'RemoteIndex',lambda model,texts:(seen.append((model,texts)) or sentinel))
    assert A.DenseIndex('model',['text']) is sentinel
    assert seen==[('model',['text'])]


def test_embedding_costs_separate_from_provider_tokens(tmp_path):
    p=tmp_path/'resource_ledger.jsonl'
    p.write_text('{"type":"llm_request","usage_reported":{"prompt_tokens":10,"completion_tokens":2}}\n')
    (tmp_path/'embedding-calls.jsonl').write_text('{"op":"search","cpu_s":0.2,"wall_s":0.3}\n')
    c=F.ledger_cost(p)
    assert c['requests']==1 and c['known_tokens']==12
    assert c['embedding_operations']==1 and c['embedding_cpu_s']==0.2


def test_collector_preserves_incomplete_pairs_and_full_denominator(tmp_path,monkeypatch):
    nb=fake_nb(tmp_path,'COMPLETED');root=tmp_path/'follow';root.mkdir()
    reg={f'C{i:05d}':{'task':f'C{i:05d}/initial'} for i in range(8)}
    monkeypatch.setattr(F.N,'IDS',tuple(reg))
    F.save(nb/'origin-registry.json',reg);F.save(root/'queue-registration.json',{'nb':str(nb)})
    for hid,status in [('C00000','NOT_ACCEPTED'),('C00001','ACCEPTED'),('C00002','ACCOUNTING_FAILURE')]:
        name=reg[hid]['task'].replace('/','__')+'__TM0__r1'
        F.save(root/'folds'/hid/'episodes'/name/'episode.json',{'status':status})
    F.collect(root);out=F.load(root/'table2-results.json');tm=out['arms']['TM0']
    assert len(out['episodes'])==56 and out['complete'] is False
    assert tm['registered']==8 and tm['measured_pairs']==2 and tm['G']==1 and tm['H']==1
    assert tm['status_counts']['ACCOUNTING_FAILURE']==1 and tm['status_counts']['NOT_STARTED']==5
