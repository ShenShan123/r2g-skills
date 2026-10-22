"""Offline executor checks; these tests launch no EDA or provider requests."""
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'memory/scripts'))
import t2_history_collect_run as run

CONTRACT = ROOT / 'memory/evaluation/t2_history_collection_contract.json'
AUTH = ROOT / 'memory/evaluation/r2g_T2_history_collection_authorization_20261018.json'
LEDGER = ROOT / 'r2g-skills/signoff-loop/scripts/flow/resource_ledger.py'


def setup_auth(tmp_path):
    c, a = tmp_path / 'contract.json', tmp_path / 'authorization.json'
    c.write_bytes(CONTRACT.read_bytes())
    a.write_bytes(AUTH.read_bytes())
    return c, a


def test_exact_user_authorization():
    c = run.authorized(CONTRACT, AUTH)
    assert len(c['designs']) == 5
    assert run.HC.worst_case(c)['total_max'] == 75


@pytest.mark.parametrize('change', ['hash', 'approval', 'design', 'cap', 'missing_caps', 'formal', 'expansion'])
def test_bad_authorization_fails_closed(tmp_path, change):
    c, a = setup_auth(tmp_path)
    data = run.load(a)
    if change == 'hash':
        c.write_text(c.read_text() + '\n')
    elif change == 'approval':
        data['user_approved'] = False
    elif change == 'design':
        data['designs'].append('ibex')
    elif change == 'cap':
        data['caps']['flows_total'] = 81
    elif change == 'missing_caps':
        data['caps'] = {}
    elif change == 'formal':
        data['formal_table2_launch'] = True
    else:
        data['automatic_expansion'] = True
    run.write(a, data)
    with pytest.raises(ValueError):
        run.authorized(c, a)


def test_environment_disables_memory_keys_and_budget_bypasses(tmp_path, monkeypatch):
    for key in ('R2G_RECIPE_FILE', 'R2G_LEDGER_DISABLE', 'R2G_PHASE_LEDGER', 'OPENAI_API_KEY',
                'DEEPSEEK_API_KEY', 'FROM_STAGE', 'R2G_MEMORY_BACKEND', 'PYTHONPATH'):
        monkeypatch.setenv(key, 'poison')
    env = run.environment(tmp_path, tmp_path / 'p', 'catalogue')
    assert env['R2G_MEMORY_BACKEND'] == 'none'
    assert env['R2G_MEMORY_READ_ONLY_EVAL'] == '1'
    assert env['R2G_BUDGET_COMPONENT'] == 'catalogue'
    assert env['R2G_FIX_EXCLUDE'] == 'period_relax'
    assert not any(k in env for k in ('R2G_RECIPE_FILE', 'R2G_LEDGER_DISABLE', 'R2G_PHASE_LEDGER',
                                    'OPENAI_API_KEY', 'DEEPSEEK_API_KEY', 'FROM_STAGE', 'PYTHONPATH'))


def ledger_call(p, env, *args):
    return subprocess.run([sys.executable, str(LEDGER), args[0], str(p), *args[1:]],
                          env=env, capture_output=True, text=True)


def test_real_ledger_counts_initial_once_and_caps_all_six_inner_flows(tmp_path):
    p = tmp_path / 'p'
    env = run.environment(tmp_path, p, 'initial')
    run.budget(p, 1)
    assert ledger_call(p, env, 'admit', '--resource', 'flows', '--amount', '1').returncode == 0
    assert ledger_call(p, env, 'admit', '--resource', 'flows', '--amount', '1').returncode == 75
    run.budget(p, 7)
    env['R2G_BUDGET_COMPONENT'] = 'catalogue'
    for _ in range(6):
        assert ledger_call(p, env, 'admit', '--resource', 'flows', '--amount', '1').returncode == 0
    for comp in ('catalogue', 'memory', 'final_acceptance', 'initial'):
        env['R2G_BUDGET_COMPONENT'] = comp
        assert ledger_call(p, env, 'admit', '--resource', 'flows', '--amount', '1').returncode == 75
    assert ledger_call(p, env, 'admit', '--resource', 'llm_requests', '--amount', '1').returncode == 75
    summary = json.loads(ledger_call(p, env, 'summary', '--json').stdout)
    assert summary['spent']['flows'] == 7
    assert summary['spent']['llm_requests'] == 0


def fake_event(p, event):
    log = p / 'reports/resource_ledger.jsonl'
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('a') as f:
        f.write(json.dumps(event) + '\n')


def test_missing_flow_outcome_fails_closed(tmp_path):
    p = tmp_path / 'projects/p'
    fake_event(p, {'type': 'budget_charge', 'resource': 'flows', 'amount': 1, 'component': 'initial'})
    with pytest.raises(RuntimeError, match='outcome'):
        run.completed_accounting(tmp_path, p)


def test_flow_outcome_and_directory_bijection(tmp_path):
    p = tmp_path / 'projects/p'
    d = p / 'backend/RUN_1'
    d.mkdir(parents=True)
    fake_event(p, {'type': 'budget_charge', 'resource': 'flows', 'amount': 1, 'component': 'initial'})
    fake_event(p, {'type': 'flow_run', 'run_dir': str(d)})
    assert run.completed_accounting(tmp_path, p)['total_known'] == 1
    (p / 'backend/RUN_orphan').mkdir()
    with pytest.raises(RuntimeError, match='mismatch'):
        run.completed_accounting(tmp_path, p)


@pytest.mark.parametrize('event', [
    {'type': 'budget_charge', 'resource': 'flows', 'amount': 81, 'component': 'catalogue'},
    {'type': 'budget_charge', 'resource': 'flows', 'amount': 'UNKNOWN', 'component': 'catalogue'},
    {'type': 'budget_charge', 'resource': 'flows', 'amount': 1, 'component': 'unspecified'},
    {'type': 'budget_charge', 'resource': 'llm_requests', 'amount': 1},
    {'type': 'budget_hold', 'amounts': {'llm_requests': 1}},
    {'type': 'budget_hold', 'needs': {'llm_requests': 1}},
])
def test_global_cost_invariants(tmp_path, event):
    fake_event(tmp_path / 'projects/p', event)
    with pytest.raises(RuntimeError):
        run.accounting(tmp_path)


def test_guard_rejects_changed_clock_and_sdc_redirect(tmp_path, monkeypatch):
    monkeypatch.setattr(run, 'immutable_ok', lambda root: None)
    p = tmp_path / 'projects/picorv32__default'
    sdc = p / 'constraints/constraint.sdc'
    sdc.parent.mkdir(parents=True)
    sdc.write_text('clock 10')
    reference = tmp_path / 'inputs/picorv32/constraints/constraint.sdc'
    reference.parent.mkdir(parents=True)
    reference.write_bytes(sdc.read_bytes())
    cfg = p / 'constraints/config.mk'
    cfg.write_text('export SDC_FILE = ' + str(sdc) + '\n')
    run.flow_guard(tmp_path, p)
    sdc.write_text('clock 20')
    with pytest.raises(RuntimeError, match='SDC changed'):
        run.flow_guard(tmp_path, p)
    sdc.write_bytes(reference.read_bytes())
    cfg.write_text('export SDC_FILE = /other.sdc\n')
    with pytest.raises(RuntimeError, match='redirected'):
        run.flow_guard(tmp_path, p)


def test_already_started_campaign_cannot_implicitly_resume(tmp_path):
    run.write(tmp_path / 'state.json', {'status': 'PAUSED'})
    with pytest.raises(RuntimeError, match='no implicit resume'):
        run.execute(tmp_path)


def test_continuation_keeps_prior_flow_charges_in_total(tmp_path):
    old = tmp_path / 'prior'
    fake_event(old / 'projects/p', {'type': 'budget_charge', 'resource': 'flows',
                                    'component': 'initial', 'amount': 4})
    run.write(tmp_path / 'continuation.json', {'prior_root': str(old)})
    fake_event(tmp_path / 'projects/q', {'type': 'budget_charge', 'resource': 'flows',
                                       'component': 'initial', 'amount': 1})
    assert run.accounting(tmp_path)['total_known'] == 5


def test_continuation_cannot_dispatch_a_previously_started_task(tmp_path):
    old = tmp_path / 'prior'
    p = old / 'projects/picorv32__u80/backend/RUN_1'
    p.mkdir(parents=True)
    run.write(tmp_path / 'continuation.json', {'prior_root': str(old)})
    with pytest.raises(RuntimeError, match='already started'):
        run.never_started(tmp_path, 'picorv32__u80')


def test_failed_first_episode_gate_dispatches_nothing_further(tmp_path, monkeypatch):
    setup_auth(tmp_path)
    p = tmp_path / 'projects/picorv32__default'
    (p / 'constraints').mkdir(parents=True)
    (p / 'constraints/constraint.sdc').write_text('set clk_period 10')
    run.write(p / 'exploration.json', {'orfs_rc': 2})
    record = {'op': 'explore', 'design': 'picorv32', 'step': 'default',
              'outcome': 'target', 'target': 'route_abort_GRT-0116'}
    run.write(tmp_path / 'state.json', {'status': 'READY', 'records': [record],
                                     'chain_checked': False, 'flows_spent': 1})
    monkeypatch.setattr(run, 'immutable_ok', lambda root: None)
    monkeypatch.setattr(run, 'accounting', lambda root: {'total_known': 1})
    monkeypatch.setattr(run, 'completed_accounting', lambda *args: {})
    monkeypatch.setattr(run, 'verdict', lambda *args: {'outcome': 'non_target'})
    commands = []

    class Harness:
        LOOP = 'engineer_loop.py'

        @staticmethod
        def run(cmd, env, log, timeout):
            commands.append(list(map(str, cmd)))
            if cmd[2] == 'add':
                (p / 'engineer.jsonl').write_text('{"status":"pending"}\n')
            return 0

    monkeypatch.setattr(run, 'harness', lambda root: Harness())
    monkeypatch.setattr(run.HC, 'reality_check', lambda *args: {'ok': False, 'problems': ['bad link']})
    # execute updates os.environ for legacy helpers; isolate that update in this test.
    monkeypatch.setattr(run, 'environment', lambda *args: {})
    with pytest.raises(RuntimeError, match='first episode chain gate failed'):
        run.execute(tmp_path)
    state = run.load(tmp_path / 'state.json')
    assert state['status'] == 'PAUSED'
    assert [r['op'] for r in state['records']] == ['explore', 'repair']
    assert len(commands) == 2
    assert '--reuse-flow-returncode' in commands[0]
    assert '--no-learn' in commands[1]
    assert not state['chain_checked']
