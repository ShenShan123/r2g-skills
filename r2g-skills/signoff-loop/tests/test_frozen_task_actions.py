"""A shared deterministic repair cannot change an evaluator-frozen task."""
import hashlib
import json
import pytest

import diagnose_signoff_fix as D


def task(tmp_path, monkeypatch):
    (tmp_path/'constraints').mkdir()
    (tmp_path/'reports').mkdir()
    cfg=tmp_path/'constraints/config.mk'
    cfg.write_text('export PLATFORM = nangate45\nexport CORE_UTILIZATION = 30\n')
    sdc=tmp_path/'constraints/constraint.sdc';sdc.write_text('set clk_period 6.0\n')
    man={'initial_config':D.parse_config(cfg.read_text()),
         'allowlist':{'CORE_UTILIZATION':{'type':'number','min':20,'max':50}},
         'immutable':{'files':{'constraints/constraint.sdc':'sha256:'+hashlib.sha256(sdc.read_bytes()).hexdigest()}}}
    path=tmp_path/'manifest.json';path.write_text(json.dumps(man))
    monkeypatch.setenv('R2G_TASK_MANIFEST',str(path))
    monkeypatch.setenv('R2G_MEMORY_BACKEND','none')
    monkeypatch.delenv('R2G_REPAIR_ACTION_POLICY_FILE',raising=False)
    (tmp_path/'reports/timing_check.json').write_text(json.dumps(
        {'tier':'severe','wns_ns':-1.2,'clock_period_ns':6.0}))
    return cfg,sdc,path


@pytest.mark.parametrize('tier',['minor','moderate','severe'])
def test_frozen_clock_is_filtered_from_every_timing_tier(tmp_path,monkeypatch,capsys,tier):
    cfg,sdc,_=task(tmp_path,monkeypatch)
    (tmp_path/'reports/timing_check.json').write_text(json.dumps(
        {'tier':tier,'wns_ns':-1.2,'clock_period_ns':6.0}))
    (tmp_path/'reports/fmax_search.json').write_text(json.dumps({'status':'ok','winner':{'period':6.0}}))
    before=(cfg.read_bytes(),sdc.read_bytes())
    assert D.main([str(tmp_path),'--check','timing','--list'])==0
    plan=json.loads(capsys.readouterr().out)
    assert 'period_relax' not in [s['id'] for s in plan['strategies']]
    assert 'utilization_reduce' in [s['id'] for s in plan['strategies']]
    assert any(r['strategy']=='period_relax' for r in plan['task_policy_rejections'])
    assert (cfg.read_bytes(),sdc.read_bytes())==before


def test_apply_rechecks_task_policy_even_if_selection_was_bypassed(tmp_path,monkeypatch,capsys):
    cfg,sdc,_=task(tmp_path,monkeypatch);before=(cfg.read_bytes(),sdc.read_bytes())
    # Mutation witness: removing the apply-time guard must write the SDC.
    monkeypatch.setattr(D,'_apply_task_action_policy',lambda plan,*a:plan,raising=False)
    assert D.main([str(tmp_path),'--check','timing','--apply','period_relax','--rank-first','period_relax'])==6
    assert (cfg.read_bytes(),sdc.read_bytes())==before


def test_invalid_task_manifest_cannot_fall_back_to_unrestricted_repairs(tmp_path,monkeypatch,capsys):
    _,_,path=task(tmp_path,monkeypatch);path.write_text('{bad json')
    assert D.main([str(tmp_path),'--check','timing','--list'])==0
    plan=json.loads(capsys.readouterr().out)
    assert not plan['strategies'] and plan['task_policy_gate_ok'] is False


def test_no_manifest_preserves_legacy_fmax_clock_search(tmp_path,monkeypatch,capsys):
    cfg,sdc,_=task(tmp_path,monkeypatch)
    monkeypatch.delenv('R2G_TASK_MANIFEST')
    assert D.main([str(tmp_path),'--check','timing','--apply','period_relax'])==0
    assert sdc.read_text()!='set clk_period 6.0\n'
