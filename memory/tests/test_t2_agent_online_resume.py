"""Offline continuation checks; no provider calls, EDA, original-ledger writes."""
import json
from pathlib import Path
import shutil
import sys
import pytest

sys.path[:0]=[str(Path(__file__).parents[1]/'scripts'),str(Path(__file__).parents[1])]
import t2_agent_online_resume as O
import t2_agent_request_resume as R


def test_never_started_selector_preserves_all_started_statuses_and_partial_directories(tmp_path):
    base=tmp_path/'base';jobs=[]
    for i,status in enumerate(('ACCEPTED','NOT_ACCEPTED','ACCOUNTING_FAILURE','UNVERIFIED_OR_SAFETY_STOP',None,'new')):
        fold=base/'folds'/str(i);fold.mkdir(parents=True)
        j={'root':str(fold),'task':'task'+str(i)+'/initial','arm':'AMEM'};jobs.append(j)
        p=fold/'episodes'/('task'+str(i)+'__initial__AMEM__r1')
        if status!='new':
            p.mkdir(parents=True)
            if status:O.save(p/'episode.json',{'status':status})
    O.save(base/'frozen-memory-plan.json',{'jobs':jobs})
    before=R.sha(base/'frozen-memory-plan.json')
    pending,kept=O.pending_jobs(base)
    assert pending==jobs[-1:] and kept==jobs[:-1]
    assert R.sha(base/'frozen-memory-plan.json')==before


def test_selector_rejects_duplicates_and_foreign_folds(tmp_path):
    fold=tmp_path/'folds/one';fold.mkdir(parents=True)
    j={'root':str(fold),'task':'one','arm':'AMEM'}
    O.save(tmp_path/'frozen-memory-plan.json',{'jobs':[j,j]})
    with pytest.raises(ValueError,match='Duplicate'):O.pending_jobs(tmp_path)
    O.save(tmp_path/'frozen-memory-plan.json',{'jobs':[{**j,'root':str(tmp_path/'foreign')}]})
    with pytest.raises(ValueError,match='Foreign'):O.pending_jobs(tmp_path)


def test_actual_queue_has_exactly_56_untouched_cells_and_keeps_all_eight_keccak_cells():
    base=Path('/data2/zhangdy/tehm-experiments/t2-agent9-followup8-20261021-r5')
    pending,kept=O.pending_jobs(base)
    assert len(pending)==56 and len(kept)==8
    assert all('keccak' in x['task'] for x in kept)
    assert not any('keccak' in x['task'] for x in pending)


@pytest.mark.parametrize('fault',['no_approval','missing_hold','extra_hold','changed_bound','missing_user_text'])
def test_unknown_occupancy_cannot_be_inferred_or_broadened(tmp_path,fault):
    targets=[{'ledger_path':'/tmp/shared','hold_id':'phase','upper_bound':{'tokens_in':5104,'tokens_out':393216}},
             {'ledger_path':'/tmp/local','hold_id':'local','upper_bound':{'tokens_in':5104,'tokens_out':393216}}]
    grant={'schema':'r2g-unknown-occupancy-authorization-v1','user_approved':True,
           'user_text':'Explicit upper-bound occupancy approved','targets':json.loads(json.dumps(targets))}
    if fault=='no_approval':grant['user_approved']=False
    elif fault=='missing_hold':grant['targets'].pop()
    elif fault=='extra_hold':grant['targets'].append({**targets[0],'hold_id':'another'})
    elif fault=='changed_bound':grant['targets'][0]['upper_bound']['tokens_out']=0
    else:grant.pop('user_text')
    p=tmp_path/'grant.json';O.save(p,grant)
    with pytest.raises(ValueError,match='Explicit UNKNOWN'):O.validate_authorization(p,{'targets':targets})


def occupancy_fixture(tmp_path):
    base=tmp_path/'base';revision=tmp_path/'revision'
    code=revision/'code/r2g-skills/signoff-loop/scripts/flow';code.mkdir(parents=True)
    src=Path(__file__).parents[2]/'r2g-skills/signoff-loop/scripts/flow/resource_ledger.py'
    shutil.copy2(src,code/'resource_ledger.py')
    O.save(base/'online-budget.json',{'caps':{},'reserve':{},'halt_on_unknown_usage':True})
    O.save(revision/'registration.json',{'base_queue':str(base)})
    targets=[]
    for name in ('shared','local'):
        p=tmp_path/name/'reports/resource_ledger.jsonl';p.parent.mkdir(parents=True)
        hold={'type':'budget_hold','ts':1,'hold_id':name,'needs':{'tokens_in':5104,'tokens_out':393216,'llm_requests':1}}
        settle={'type':'budget_settle','ts':2,'hold_id':name,'booked':hold['needs'],
                'actual':{'tokens_in':'UNKNOWN','tokens_out':'UNKNOWN','llm_requests':1}}
        p.write_text(''.join(json.dumps(e)+'\n' for e in (hold,settle)))
        targets.append({'ledger_path':str(p),'hold_id':name,'upper_bound':{'tokens_in':5104,'tokens_out':393216}})
    O.save(revision/'incident.json',{'targets':targets})
    grant=tmp_path/'explicit-user-authorization.json'
    O.save(grant,{'schema':'r2g-unknown-occupancy-authorization-v1','user_approved':True,
        'user_text':'Test-only explicit approval','targets':targets})
    return revision,grant,targets


def test_authorized_occupancy_appends_only_safety_bounds_actual_stays_unknown(tmp_path):
    rev,grant,targets=occupancy_fixture(tmp_path)
    before={t['ledger_path']:Path(t['ledger_path']).read_bytes() for t in targets}
    O.occupy(rev,grant);L=O.ledger_module(rev/'code')
    for t in targets:
        p=Path(t['ledger_path']);assert p.read_bytes().startswith(before[str(p)])
        ev=O.rows(p)
        assert L.totals(ev)['tokens_out']=='UNKNOWN'
        assert L.budget_totals(ev)['tokens_out']==393216
        assert ev[-1]['actual_remains']=='UNKNOWN'
    with pytest.raises(ValueError,match='already exists'):O.occupy(rev,grant)


def test_unapproved_occupancy_does_not_touch_either_ledger(tmp_path):
    rev,grant,targets=occupancy_fixture(tmp_path)
    data=O.load(grant);data['user_approved']=False;O.save(grant,data)
    before={t['ledger_path']:R.sha(t['ledger_path']) for t in targets}
    with pytest.raises(ValueError,match='Explicit UNKNOWN'):O.occupy(rev,grant)
    assert all(R.sha(p)==h for p,h in before.items())


def test_real_approval_matches_both_original_bookings_without_claiming_actuals():
    base=Path('/data2/zhangdy/tehm-experiments/t2-agent9-followup8-20261021-r5')
    path=Path(__file__).parents[1]/'evaluation/r2g_Table2_JSON_UNKNOWN_authorization_20261022.json'
    if not path.is_file():
        pytest.skip('Local experiment authorization receipt is excluded from the scripts-only release')
    evidence=O.incident(base);grant=O.validate_authorization(path,evidence)
    assert grant['actual_usage']=='UNKNOWN' and grant['occupancy_is_not_measured_cost']
    assert len(grant['targets'])==2
