import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import t2a_batch as B
import t2a_adapters as A


def test_seven_dev_slots_are_disjoint_and_all_available():
    import t2a_dev as D
    B.validate_cpu_slots(D.SEVEN_CPU_SLOTS,set(range(192)),split='DEV')
    with pytest.raises(RuntimeError,match='overlap'):
        B.validate_cpu_slots([D.SEVEN_CPU_SLOTS[0]]*2,set(range(192)),split='DEV')
    with pytest.raises(RuntimeError,match='unavailable'):
        B.validate_cpu_slots(D.SEVEN_CPU_SLOTS,{0},split='DEV')
    with pytest.raises(RuntimeError,match='two-slot'):
        B.validate_cpu_slots(D.SEVEN_CPU_SLOTS,set(range(192)),split='TEST')


def test_parallel_waves_keep_task_repeat_groups_and_launch_each_row_once():
    rows=[{'task':task,'arm':a,'repeat':rep} for rep in [1,2]
          for task in ['dev/a','dev/b'] for a in B.T.ARMS]
    waves=B.plan_waves(rows,7)
    assert len(waves)==4 and [r for wave in waves for r in wave]==rows
    assert all(len({(r['task'],r['repeat']) for r in wave})==1 for wave in waves)
    pairs=B.plan_waves(rows,2)
    assert all(len(w)<=2 for w in pairs) and len(pairs)==16


def test_shared_binding_hashes_one_byte_snapshot_even_while_ledger_grows(tmp_path,monkeypatch):
    phase=tmp_path/'t2a-control-construction-c2-20261011';(phase/'phase').mkdir(parents=True)
    budget=phase/'C8-budget.json';budget.write_text(json.dumps({'budget_id':'t2a-construction-dev-A3',
        'caps':{'llm_requests':5000},'combined_tokens_cap':17000000,'reserve':{}}))
    ledger=phase/'phase/resource_ledger.jsonl';ledger.write_bytes(b'historical\n')
    monkeypatch.setattr(B.Q,'EVAL',tmp_path)
    real_read=Path.read_bytes
    captured=[]
    def growing_read(path):
        if path==ledger:path.write_bytes(real_read(path)+b'new event\n')
        raw=real_read(path)
        if path==ledger:
            captured.append(raw)
            path.write_bytes(raw+b'next event\n')
        return raw
    monkeypatch.setattr(Path,'read_bytes',growing_read)
    binding=B.construction_dev_binding()
    import hashlib
    assert binding['prior_ledger_bytes']==len(captured[0])
    assert binding['prior_ledger_sha256']==hashlib.sha256(captured[0]).hexdigest()


def test_workspace_rollback_is_not_hidden_by_a_later_stop_row(tmp_path):
    reports=tmp_path/'reports';reports.mkdir()
    rows=[{'from_stage':'floorplan','rolled_back':True,'verdict':'regression'},
          {'from_stage':None,'rolled_back':False,'verdict':'stop_clean'}]
    (reports/'fix_log.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    B.mark_engineer_restore(tmp_path)
    assert (reports/'t2a-workspace-unsafe.json').exists()


def test_later_kept_execution_supersedes_an_older_rollback(tmp_path):
    reports=tmp_path/'reports';reports.mkdir()
    rows=[{'from_stage':'floorplan','rolled_back':True},
          {'from_stage':'place','rolled_back':False,'verdict':'applied'},
          {'from_stage':None,'rolled_back':False,'verdict':'stop_clean'}]
    (reports/'fix_log.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    B.mark_engineer_restore(tmp_path)
    assert not (reports/'t2a-workspace-unsafe.json').exists()


@pytest.mark.parametrize('bound',[True,False])
def test_shared_boundary_checks_config_binding_before_llm_repairs(tmp_path,bound):
    (tmp_path/'reports').mkdir();(tmp_path/'constraints').mkdir()
    config=tmp_path/'constraints/config.mk';config.write_text('export CORE_UTILIZATION = 27\n')
    run=tmp_path/'backend/RUN_A';run.mkdir(parents=True)
    (tmp_path/'manifest.json').write_text('{"require_execution_binding":true}')
    (tmp_path/'backend/.r2g_signoff_run').write_text(json.dumps({'run_dir':str(run)}))
    (run/'effective-config.json').write_text(json.dumps({'status':'PASS',
        'project_config_sha256':B.Q.digest(config) if bound else 'other-executed-config'}))
    B.mark_engineer_restore(tmp_path)
    assert (tmp_path/'reports/t2a-workspace-unsafe.json').exists() is (not bound)


def test_partial_controls_and_offline_validation_are_not_a_launch_pass(tmp_path):
    for arm in ('RET','RECIPE','AWM'):
        (tmp_path/(arm+'.json')).write_text(json.dumps({'status':'FROZEN','source_sha256':A.SOURCE_SHA}))
    inventory={'status':'FROZEN','source_isolation':{'ok':True},'tasks':[{'task':'d/u80','status':'ADMITTED'}]}
    dev={'real_seven_arm_dev_validated':False,'records':[]}
    errors=B.validate_gates(inventory,tmp_path,dev)
    assert any('EXPEL' in e for e in errors) and any('AMEM' in e for e in errors)
    assert any('real seven-arm' in e for e in errors)


def test_blocked_package_cannot_execute_even_one_initial_flow(tmp_path,monkeypatch):
    (tmp_path/'registration.json').write_text(json.dumps({'status':'BLOCKED_PREPARATION',
                       'launch_blockers':['qualification pending'],'files':{}}))
    monkeypatch.setattr(B.Q,'module',lambda *a:pytest.fail('No execution before preflight'))
    with pytest.raises(RuntimeError,match='qualification pending'):B.episode(tmp_path,'d/u80','NB',1)


def test_changed_frozen_input_is_rejected(tmp_path):
    p=tmp_path/'config';p.write_text('initial')
    (tmp_path/'registration.json').write_text(json.dumps({'status':'FROZEN_READY','launch_blockers':[],
                         'online_policy':B.T.online_policy(),
                         'files':{str(p):B.Q.digest(p)}}))
    assert B.preflight(tmp_path)['status']=='FROZEN_READY'
    p.write_text('changed')
    with pytest.raises(RuntimeError,match='Frozen input changed'):B.preflight(tmp_path)


def test_all_seven_real_validation_records_are_required(tmp_path):
    for arm in ('RET','RECIPE','EXPEL','AWM','AMEM'):
        (tmp_path/(arm+'.json')).write_text(json.dumps({'status':'FROZEN','source_sha256':A.SOURCE_SHA}))
    inventory={'status':'FROZEN','source_isolation':{'ok':True},'tasks':[{'task':'d/u80','status':'ADMITTED'}]}
    dev={'real_seven_arm_dev_validated':True,'validation_kind':'REAL_PROVIDER_AND_EDA',
         'online_policy':B.T.online_policy(),
         'records':[{'arm':a,'criteria_passed':True,'accounting':{'passed':True}} for a in B.T.ARMS]}
    assert B.validate_gates(inventory,tmp_path,dev)==[]
    dev['records'].pop()
    assert B.validate_gates(inventory,tmp_path,dev)


def test_wrong_tehm_store_is_not_silently_substituted(tmp_path):
    (tmp_path/'m0_receipt.json').write_text(json.dumps({'source_export_sha256':A.SOURCE_SHA,'captured':1933,'d1':True}))
    (tmp_path/'tehm.sqlite').write_bytes(b'wrong-store')
    assert B.store_errors(tmp_path)==['TEHM-M0-v2/SOURCE identity mismatch']


def test_formal_episode_primary_budget_limits_only_repair_opportunities():
    budget=B.episode_budget()
    assert budget['caps']=={'repair_attempts':3}
    assert budget['reserve']=={}
    assert not any(k in budget['caps'] for k in ('flows','wall_s','tokens_in','tokens_out','llm_requests'))


def test_formal_preflight_cannot_relabel_an_old_policy_as_o1(tmp_path):
    policy={**B.T.online_policy(),'input_bytes_ceiling':12000}
    (tmp_path/'registration.json').write_text(json.dumps({'status':'FROZEN_READY',
        'launch_blockers':[],'files':{},'online_policy':policy}))
    with pytest.raises(RuntimeError,match='Frozen online policy differs'):B.preflight(tmp_path)


def test_dev_registration_is_never_a_formal_launch_pass(tmp_path):
    ledger=tmp_path/'shared';ledger.write_text('')
    budget=tmp_path/'budget';budget.write_text('{}')
    binding={'ledger_path':str(ledger),'budget_file':str(budget),
             'budget_sha256':B.Q.digest(budget),'prior_ledger_bytes':0,
             'prior_ledger_sha256':B.Q.digest(ledger)}
    (tmp_path/'registration.json').write_text(json.dumps({'status':'FROZEN_DEV_READY',
        'split':'DEV','launch_blockers':[],'files':{},'online_policy':B.T.online_policy(),
        'phase_binding':binding}))
    with pytest.raises(RuntimeError,match='not launchable'):B.preflight(tmp_path)
    assert B.preflight(tmp_path,split='DEV')['status']=='FROZEN_DEV_READY'


def test_dev_cannot_replace_the_shared_construction_ledger(tmp_path):
    binding={'ledger_path':str(tmp_path/'shared.jsonl'),'budget_file':str(tmp_path/'budget.json')}
    (tmp_path/'shared.jsonl').write_text('historical costs\n')
    (tmp_path/'budget.json').write_text('{}')
    binding.update(budget_sha256=B.Q.digest(tmp_path/'budget.json'),
        prior_ledger_bytes=(tmp_path/'shared.jsonl').stat().st_size,
        prior_ledger_sha256=B.Q.digest(tmp_path/'shared.jsonl'))
    reg={'status':'FROZEN_DEV_READY','split':'DEV','launch_blockers':[],'files':{},
         'online_policy':B.T.online_policy(),'phase_binding':binding}
    (tmp_path/'registration.json').write_text(json.dumps(reg))
    assert B.preflight(tmp_path,split='DEV')['split']=='DEV'
    (tmp_path/'shared.jsonl').write_text('rewritten costs\n')
    with pytest.raises(RuntimeError,match='shared DEV ledger'):B.preflight(tmp_path,split='DEV')
