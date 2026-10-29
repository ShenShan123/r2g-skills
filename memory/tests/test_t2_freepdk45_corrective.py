import json
from pathlib import Path
import sys
import pytest
import subprocess
import os

sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
import t2_freepdk45_corrective as C


def test_normalize_uniform_util20_without_editing_clock_or_rtl():
    config={'CORE_UTILIZATION':'20','CORE_AREA':'2 2 34 34','DIE_AREA':'0 0 36 36',
            'SDC_FILE':'sdc','VERILOG_FILES':'rtl','PLACE_DENSITY':'0.30'}
    out=C.uniform_initial(config)
    assert out=={k:v for k,v in config.items() if k not in ('CORE_AREA','DIE_AREA')}
    assert 'DIE_AREA' in config
    assert C.uniform_initial(out)==out


def test_normalizer_does_not_guess_a_different_utilization():
    with pytest.raises(ValueError):C.uniform_initial({'CORE_UTILIZATION':'90'})


def test_no_measured_episode_is_repeated(tmp_path,monkeypatch):
    nb=tmp_path/'nb';p=nb/'episodes/A__initial__NB__r1';p.mkdir(parents=True)
    C.save(p/'episode.json',{'status':'ACCEPTED'})
    jobs=[{'root':str(nb),'task':x+'/initial','arm':'NB'} for x in 'AB']
    assert C.remaining_jobs(jobs)==jobs[1:]
    C.save(p/'episode.json',{'status':'UNVERIFIED_OR_SAFETY_STOP'})
    with pytest.raises(ValueError):C.remaining_jobs(jobs)


def test_only_explicit_bound_timeout_review_can_be_skipped(tmp_path,monkeypatch):
    nb=tmp_path/'nb';p=nb/'episodes/A__initial__NB__r1';p.mkdir(parents=True)
    C.save(p/'episode.json',{'status':'UNVERIFIED_OR_SAFETY_STOP'})
    import t2_freepdk45_main as M
    monkeypatch.setattr(M,'registered_checker_stop',lambda *args:True)
    jobs=[{'root':str(nb),'task':'A/initial','arm':'NB'}]
    assert C.remaining_jobs(jobs)==[]


def test_initial_technical_stop_guard_is_after_record_and_before_any_agent():
    root=Path('/data1/zhangdy/tehm-experiments/t2-freepdk45-main50-adamem-20261022-r3')
    # Test a one-time patch against its archived INPUT, not the live output to
    # which it has already been applied. Never alter the live runtime for a test.
    text=(root/'consolidated-correction-20261023/original/nb/code/memory/scripts/t2a_batch.py').read_text()
    fixed=C.integrate_batch(text);compile(fixed,'fixed','exec')
    assert fixed.index("record['initial']=initial")<fixed.index("if initial.get('technical_signoff'):")
    assert fixed.index("if initial.get('technical_signoff'):")<fixed.index('record[\'agent_run\']=run_agent')
    with pytest.raises(ValueError):C.integrate_batch('wrong source')


def test_checker_meter_records_native_exit_and_no_retry(tmp_path):
    project=tmp_path/'project';(project/'reports').mkdir(parents=True)
    helper=Path(__file__).parents[2]/'r2g-skills/signoff-loop/scripts/flow/meter_checker.py'
    p=subprocess.run([sys.executable,str(helper),str(project),'drc',sys.executable,'-c','raise SystemExit(124)'],
                     env=dict(os.environ),capture_output=True,text=True)
    assert p.returncode==124,p.stdout+p.stderr
    events=[json.loads(s) for s in (project/'reports/resource_ledger.jsonl').read_text().splitlines()]
    assert len(events)==1 and events[0]['type']=='checker_run'
    assert events[0]['exit_status']==124 and events[0]['implicit_retries']==0
    assert events[0]['cpu_s']>=0 and events[0]['cost_nested_in_parent_tool_call'] is True


def test_checker_count_same_for_legacy_and_new_metered_receipts(tmp_path):
    import t2_nb_followup as F
    project=tmp_path/'p';(project/'reports').mkdir(parents=True)
    log=project/'eval_arm.log';log.write_text('[strict-signoff] drc\n[strict-signoff] lvs\n[strict-signoff] rcx\n')
    ledger=project/'reports/resource_ledger.jsonl';ledger.write_text('{"type":"tool_call"}\n')
    legacy=F.ledger_cost(ledger)
    with ledger.open('a') as f:
        for checker in ('drc','lvs','rcx'):f.write(json.dumps({'type':'checker_run','source':'strict_signoff','checker':checker})+'\n')
    current=F.ledger_cost(ledger)
    assert legacy['tool_calls']==current['tool_calls']==4
    assert legacy['checker_runs']==current['checker_runs']==3


@pytest.mark.parametrize('marker',['applied.json','activation.json'])
def test_prepared_refresh_refuses_any_previously_installed_revision(tmp_path,marker):
    root=tmp_path/'root';revision=root/'revision';revision.mkdir(parents=True)
    (revision/marker).write_text('{}')
    with pytest.raises(ValueError,match='installed/activated'):
        C.refresh(root,revision,tmp_path/'not-needed.xml')


def test_prepared_refresh_refuses_an_operator_already_waiting_for_workers(tmp_path):
    root=tmp_path/'root';revision=root/'revision';revision.mkdir(parents=True)
    C.save(revision/'operator.json',{'status':'WAITING_FOR_STARTED_WORKERS'})
    with pytest.raises(ValueError,match='unlaunched'):
        C.refresh(root,revision,tmp_path/'not-needed.xml')


def test_failed_offline_tests_cannot_authorize_refresh(tmp_path):
    root=tmp_path/'root';revision=root/'revision';revision.mkdir(parents=True)
    C.save(revision/'operator.json',{'status':'PREPARED_VERIFIED'})
    tests=tmp_path/'tests.xml'
    tests.write_text('<testsuite>'+('<testcase/>'*30)+'<testcase><failure/></testcase></testsuite>')
    with pytest.raises(ValueError,match='Passing corrective tests'):
        C.refresh(root,revision,tests)
