"""R3: real CTS infeasibility and measured quality failure are not lost turns."""
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import t2a_runtime as T
from test_repair_turns import H, ep, OK, _Resp, _spent
from test_t2a_partial_restoration import bound_run, partial_task, recover_stub, write


def cts_failure(run):
    (run / 'flow.log').write_text('[ERROR DPL-0038] Utilization greater than 100%, impossible to legalize\n')
    (run / 'stage_log.jsonl').write_text(''.join(json.dumps({
        'stage': s, 'status': 2 if s == 'cts' else 0}) + '\n'
        for s in ('synth', 'floorplan', 'place', 'cts')))


def test_cts_dpl0038_baseline_and_clean_recovery_keep_remaining_turns(ep, monkeypatch):
    project, _, _ = ep
    env = partial_task(project)
    cts_failure(project / 'backend/RUN_00')
    proof = T.partial_execution_proof(project, env=env)
    assert proof and proof['error_code'] == 'DPL-0038' and proof['failed_stage'] == 'cts'
    T.restore(project, T.snapshot_state(project, 0, env), execution_changed=True)
    recover_stub(project, env, monkeypatch, error='DPL-0038')
    original = H.run
    def cts_run(cmd, *args):
        rc = original(cmd, *args)
        if str(cmd[1]).endswith('run_orfs.sh'): cts_failure(project / 'backend/RUN_01')
        return rc
    monkeypatch.setattr(H, 'run', cts_run)
    result = T.recover_execution(H, project, env)
    assert result['execution_recovery'] == 'PARTIAL_VERIFIED'
    assert result['partial_execution']['failed_stage'] == 'cts'
    assert result['accepted'] is False and T.repair_continuation_verified(result)
    monkeypatch.setattr(H, '_task_failure', lambda p: ('orfs_stage', 'cts', 1))
    monkeypatch.setattr(H, 'observation', lambda p: {'orfs_status': 'fail'})
    monkeypatch.setattr(H.urllib.request, 'urlopen', lambda *a, **k: _Resp(json.dumps({
        **OK, 'choices': [{'message': {'content': '{}'}}]}).encode()))
    rows, _ = T.strict_turns(H, project, 'NB', env, 'unused',
                            SimpleNamespace(from_project=lambda *a: {}))
    assert len(rows) == 3 and _spent(project, 'repair_attempts') == 3


def area_failure():
    checks = {key: 'pass' for key in T.ACCEPTANCE_CHECKS}
    checks['A6_setup'] = 'fail'
    return {'accepted': False, 'final_layout': True,
            'acceptance': {'accepted': False, 'checks': checks,
                           'failed': ['A6_setup'], 'unknown': []},
            'envelope': {'status': 'FAIL', 'config': {'ok': True, 'violations': []},
                         'area': {'ok': False, 'unverified': [], 'violations': [
                             {'kind': 'area_cap_exceeded', 'metric': 'stdcell',
                              'relative_change': 0.84, 'cap': 0.55}]}}}


def full_recovery(project, env, monkeypatch, verdict, charged=True):
    man = json.loads(Path(env['R2G_TASK_MANIFEST']).read_text())
    man['area'] = {name: {'ref': ref, 'max_rel_increase': cap}
                   for name, ref, cap in [('stdcell', 100, 0.55), ('core', 100, 1), ('die', 120, 1)]}
    write(Path(env['R2G_TASK_MANIFEST']), man)
    T.restore(project, T.snapshot_state(project, 0, env), execution_changed=True)
    def run(cmd, run_env, *args):
        if str(cmd[1]).endswith('run_orfs.sh'):
            new = bound_run(project, 'RUN_01', env['R2G_TASK_MANIFEST'], full=True)
            (new / 'flow.log').write_text('completed\n')
            (new / 'stage_log.jsonl').write_text(''.join(json.dumps({
                'stage': s, 'status': 0}) + '\n' for s in (
                    'synth', 'floorplan', 'place', 'cts', 'route', 'finish')))
            (new / 'final').mkdir()
            (new / 'final/6_final.odb').write_text('fresh completed layout')
            import t2a_envelope as E
            write(new / 'logs/6_report.json', {E.AREA_KEYS[k]: v for k, v in
                  {'stdcell': 184, 'core': 100, 'die': 120}.items()})
            if charged:
                T.event(H, project, 'budget_charge', {'resource': 'flows', 'amount': 1,
                                                      'component': 'rollback_recovery'})
                T.event(H, project, 'flow_run', {'run_dir': str(new), 'rc': 0,
                    'component': 'rollback_recovery', 'timeout': False, 'budget_stop': False})
            return 0
        return 0
    monkeypatch.setattr(H, 'run', run)
    monkeypatch.setattr(H, 'final_verdict', lambda *a: copy.deepcopy(verdict))


def test_completed_area_failing_restore_is_verified_but_never_accepted(ep, monkeypatch):
    project, _, _ = ep
    env = partial_task(project)
    full_recovery(project, env, monkeypatch, area_failure())
    result = T.recover_execution(H, project, env)
    assert result['execution_recovery'] == 'VERIFIED' and result['accepted'] is False
    assert T.repair_continuation_verified(result)
    assert T.terminal_status(result) == 'NOT_ACCEPTED'
    assert result['envelope']['status'] == 'FAIL'
    assert not (project / 'reports/t2a-workspace-unsafe.json').exists()


@pytest.mark.parametrize('arm', T.ARMS)
def test_completed_quality_failure_preserves_three_turns_for_every_arm(ep, monkeypatch, arm):
    project, _, _ = ep
    env = partial_task(project)
    full_recovery(project, env, monkeypatch, area_failure())
    assert T.recover_execution(H, project, env)['execution_recovery'] == 'VERIFIED'
    monkeypatch.setattr(H, '_task_failure', lambda p: ('setup', 'timing', 1))
    monkeypatch.setattr(H, 'observation', lambda p: {'timing_tier': 'moderate'})
    def transport(req, **kwargs):
        messages = json.loads(req.data)['messages']
        content = '{"keywords":["timing"]}' if 'Only extract query keywords' in messages[0]['content'] else '{}'
        return _Resp(json.dumps({**OK, 'choices': [{'message': {'content': content}}]}).encode())
    monkeypatch.setattr(H.urllib.request, 'urlopen', transport)
    class Memory:
        def context(self, query, sit, auxiliary=None, **kwargs):
            if arm == 'AMEM': auxiliary(query)
            return 'SOURCE memory', ['unit']
    rows, _ = T.strict_turns(H, project, arm, env, 'unused',
        SimpleNamespace(from_project=lambda *a: {}), Memory() if arm in ('RET', 'EXPEL', 'AWM', 'AMEM') else None)
    assert len(rows) == 3 and _spent(project, 'repair_attempts') == 3
    assert _spent(project, 'llm_requests') == (6 if arm == 'AMEM' else 3)


@pytest.mark.parametrize('fault', ['config', 'unknown_check', 'missing_area',
                                  'wrong_binding', 'immutable_input', 'uncharged'])
def test_area_failure_does_not_hide_an_unverified_or_forbidden_restore(ep, monkeypatch, fault):
    project, _, _ = ep
    env = partial_task(project)
    verdict = area_failure()
    if fault == 'config':
        verdict['envelope']['config']['ok'] = False
    if fault == 'unknown_check':
        verdict['acceptance']['checks']['A3_lvs'] = 'unknown'
        verdict['acceptance']['unknown'] = ['A3_lvs']
    if fault == 'missing_area':
        verdict['envelope']['area']['unverified'] = ['die']
    full_recovery(project, env, monkeypatch, verdict, charged=fault != 'uncharged')
    if fault in ('wrong_binding', 'immutable_input'):
        original = H.final_verdict
        def final(*args):
            if fault == 'wrong_binding':
                write(project / 'backend/RUN_01/effective-config.json', {'status': 'FAIL'})
            else:
                (project / 'constraints/constraint.sdc').write_text('set clk_period 999\n')
            return original(*args)
        monkeypatch.setattr(H, 'final_verdict', final)
    result = T.recover_execution(H, project, env)
    assert result['execution_recovery'] == 'UNVERIFIED'
    assert (project / 'reports/t2a-workspace-unsafe.json').exists()


def test_unverified_completed_recovery_is_not_launched_twice(ep, monkeypatch):
    project, _, _ = ep
    env = partial_task(project)
    verdict = area_failure()
    verdict['acceptance']['checks']['A3_lvs'] = 'unknown'
    verdict['acceptance']['unknown'] = ['A3_lvs']
    full_recovery(project, env, monkeypatch, verdict)
    original = H.run
    launches = []
    def once(cmd, *args):
        if str(cmd[1]).endswith('run_orfs.sh'):
            launches.append(cmd)
            if len(launches) > 1: pytest.fail('Unverified completed recovery was implicitly retried')
        return original(cmd, *args)
    monkeypatch.setattr(H, 'run', once)
    assert T.recover_execution(H, project, env)['execution_recovery'] == 'UNVERIFIED'
    assert T.terminal_verdict(H, project, env)['execution_recovery'] == 'UNVERIFIED'
    assert len(launches) == 1


@pytest.mark.parametrize('arm', T.ARMS)
def test_all_arms_receive_same_public_ranges_area_bounds_and_no_extra_turn(ep, monkeypatch, arm):
    project, _, _ = ep
    env = partial_task(project)
    mpath = Path(env['R2G_TASK_MANIFEST'])
    man = json.loads(mpath.read_text())
    man['expected_clock_period_ns'] = 10
    man['area'] = {'stdcell': {'ref': 100, 'max_rel_increase': 0.55,
                              'reference_file': '/private/evaluator/not-for-the-model'}}
    write(mpath, man)
    monkeypatch.setattr(H, '_task_failure', lambda p: ('orfs_stage', 'cts', 1))
    monkeypatch.setattr(H, 'observation', lambda p: {'orfs_status': 'fail'})
    messages = []
    def transport(req, **kwargs):
        msg = json.loads(req.data)['messages']
        if 'Only extract query keywords' in msg[0]['content']:
            content = '{"keywords":["placement"]}'
        else:
            messages.append(msg)
            content = '{}'
        return _Resp(json.dumps({**OK, 'choices': [{'message': {'content': content}}]}).encode())
    monkeypatch.setattr(H.urllib.request, 'urlopen', transport)
    class Memory:
        def context(self, query, sit, auxiliary=None, **kwargs):
            if arm == 'AMEM': auxiliary(query)
            return 'frozen SOURCE', ['unit']
    memory = Memory() if arm in ('RET', 'EXPEL', 'AWM', 'AMEM') else None
    rows, _ = T.strict_turns(H, project, arm, env, 'unused',
                            SimpleNamespace(from_project=lambda *a: {}), memory)
    assert len(messages) == len(rows) == 3 and _spent(project, 'repair_attempts') == 3
    for message in messages:
        text = message[-1]['content']
        assert 'task_contract' in text and 'max_rel_increase' in text and '0.55' in text
        assert 'expected_clock_period_ns' in text and 'allowlist' in text
        assert '/private/evaluator' not in text


def test_known_incomplete_reason_does_not_relabel_missing_checks_as_failure():
    final = {'accepted': False, 'execution_recovery': 'PARTIAL_VERIFIED'}
    assert T.terminal_status(final) == 'UNVERIFIED_OR_SAFETY_STOP'
    assert T.terminal_reason(final, 3) == 'repair_allowance_exhausted_known_incomplete'
    assert T.terminal_reason(final, 2) == 'known_incomplete_before_allowance_exhaustion'


@pytest.mark.parametrize('mutation', ['missing_code', 'quality_equals_restoration',
                                    'missing_contract', 'retry_unverified'])
def test_regressions_detect_removing_each_r3_correction(ep, monkeypatch, mutation):
    if mutation == 'missing_code':
        from tehm.adapters.r2g_evidence import ACTION_INFEASIBILITY_CODES
        monkeypatch.delitem(ACTION_INFEASIBILITY_CODES, 'DPL-0038')
        target = lambda: test_cts_dpl0038_baseline_and_clean_recovery_keep_remaining_turns(ep, monkeypatch)
    elif mutation == 'quality_equals_restoration':
        monkeypatch.setattr(T, '_completed_quality_failure', lambda *a: False)
        target = lambda: test_completed_area_failing_restore_is_verified_but_never_accepted(ep, monkeypatch)
    elif mutation == 'missing_contract':
        monkeypatch.setattr(T, 'public_task_contract', lambda *a: {})
        target = lambda: test_all_arms_receive_same_public_ranges_area_bounds_and_no_extra_turn(ep, monkeypatch, 'NB')
    else:
        import inspect
        source = inspect.getsource(T.recover_execution)
        old = "marker_data['failed_recovery']={'config_sha256':config_sha,'sdc_sha256':sdc_sha,'recovery_rc':code}"
        assert source.count(old) == 2
        # Only the rc=0 branch is removed, not the existing native-rc=2 rule.
        prefix, suffix = source.rsplit(old, 1)
        namespace = dict(T.__dict__)
        exec(compile(prefix + 'pass' + suffix, '<removed-full-recovery-stop>', 'exec'), namespace)
        monkeypatch.setattr(T, 'recover_execution', namespace['recover_execution'])
        target = lambda: test_unverified_completed_recovery_is_not_launched_twice(ep, monkeypatch)
    with pytest.raises((AssertionError, pytest.fail.Exception)):
        target()
