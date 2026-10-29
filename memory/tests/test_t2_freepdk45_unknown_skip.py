"""A transport UNKNOWN may end a cell, but cannot masquerade as a measured result."""
import ast
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import types

import pytest

SCRIPTS = Path(__file__).parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import t2_freepdk45_unknown_skip as S

ROOT = Path('/data1/zhangdy/tehm-experiments/t2-freepdk45-main50-adamem-20261022-r3')


def input_source(name):
    old = ROOT / 'unknown-skip-20261025-r18/original/code/memory/scripts' / name
    return (old if old.exists() else ROOT / 'code/memory/scripts' / name).read_text()


def call(**over):
    base = {'type': 'llm_request', 'endpoint': 'https://api.deepseek.com/chat/completions',
            'model_requested': 'deepseek-flash', 'model_reported': None, 'request_id': None,
            'status': -1, 'usage_reported': 'UNKNOWN', 'model_mismatch': False}
    return {**base, **over}


def test_no_response_keeps_unknown():
    x = call(); before = copy.deepcopy(x)
    assert S.validate_calls([x]) == [x]
    assert x == before and x['usage_reported'] == 'UNKNOWN'


@pytest.mark.parametrize('change', [
    {'model_mismatch': True}, {'model_requested': 'another-model'},
    {'endpoint': 'https://other.example'}, {'request_id': 'response-received'},
    {'model_reported': 'deepseek-flash'}, {'status': 400}, {'status': 200},
    {'usage_reported': {}}, {'usage_reported': None},
])
def test_other_provider_problems_still_pause(change):
    with pytest.raises(ValueError): S.validate_calls([call(**change)])


@pytest.mark.parametrize('usage', [
    {'prompt_tokens': True, 'completion_tokens': 1},
    {'prompt_tokens': -1, 'completion_tokens': 1},
    {'prompt_tokens': float('nan'), 'completion_tokens': 1},
    {'prompt_tokens': 1},
])
def test_invalid_known_usage_not_quarantined(usage):
    with pytest.raises(ValueError):
        S.validate_calls([call(status=200, model_reported='deepseek-flash', usage_reported=usage)])


def test_three_total_requests_and_known_usage_unchanged():
    good = call(status=200, model_reported='deepseek-flash', request_id='id',
                usage_reported={'prompt_tokens': 12, 'completion_tokens': 3})
    assert S.validate_calls([good, good, call()]) == [call()]
    with pytest.raises(ValueError): S.validate_calls([good] * 4)


@pytest.mark.parametrize('override', [{'caps': {'tokens_in': 100}}, {'reserve': {'flows': 1}},
                                    {'combined_tokens_cap': 100}, {'halt_on_unknown_usage': True}])
def test_unknown_continue_cannot_bypass_any_existing_cost_cap(override):
    b = {'caps': {}, 'reserve': {}, 'halt_on_unknown_usage': False}
    assert not S.uncapped_continue({**b, **override})


def test_uncapped_phase_admits_without_fabricating_cost():
    path = ROOT / 'code/r2g-skills/signoff-loop/scripts/flow/resource_ledger.py'
    spec = importlib.util.spec_from_file_location('unknown_skip_ledger', path)
    L = importlib.util.module_from_spec(spec); spec.loader.exec_module(L)
    ev = [{'type': 'budget_settle', 'hold_id': 'lost', 'actual': {'tokens_in': 'UNKNOWN', 'tokens_out': 'UNKNOWN'},
           'ts': 1}]
    budget = {'caps': {}, 'reserve': {}, 'halt_on_unknown_usage': False}
    assert L.check(ev, budget, {'llm_requests': 1}, 'repair')[0]
    assert L.totals(ev)['tokens_in'] == L.totals(ev)['tokens_out'] == 'UNKNOWN'
    assert not L.check(ev, {**budget, 'halt_on_unknown_usage': True}, {'llm_requests': 1}, 'repair')[0]
    assert not L.check(ev, {**budget, 'caps': {'tokens_in': 100}}, {'llm_requests': 1}, 'repair')[0]


def dispatch_fixture(tmp_path):
    text = S.dispatch_delta(input_source('t2_nb_followup.py'))
    tree = ast.parse(text)
    node = next(x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == 'dispatch')
    def load(p): return json.loads(Path(p).read_text())
    def save(p, x): Path(p).write_text(json.dumps(x))
    ns = dict(Path=Path, sys=sys, os=os, subprocess=subprocess, time=time, SLOTS=[[0]], load=load, save=save)
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'isolated-real-dispatch', 'exec'), ns)
    (tmp_path / 'logs').mkdir()
    jobs = [{'root': str(tmp_path / 'folds/A'), 'task': 'A/initial', 'arm': x} for x in ['RECIPE', 'TM0']]
    launched = []
    def popen(cmd, **kw):
        arm = cmd[cmd.index('--arm') + 1]
        p = tmp_path / 'folds/A/episodes' / ('A__initial__' + arm + '__r1')
        p.mkdir(parents=True)
        data = {'status': 'ACCOUNTING_FAILURE' if arm == 'RECIPE' else 'ACCEPTED',
                'accounting': {'passed': arm != 'RECIPE'}}
        save(p / 'episode.json', data); launched.append(arm)
        return types.SimpleNamespace(pid=len(launched), returncode=0, poll=lambda: 0)
    return ns['dispatch'], jobs, launched, popen


def test_dispatch_continues_after_explicitly_quarantined_unknown(tmp_path):
    dispatch, jobs, launched, popen = dispatch_fixture(tmp_path)
    result = dispatch(tmp_path, jobs, popen=popen, sleep=lambda _: None,
                      terminal_policy=lambda p, e: e['status'] == 'ACCOUNTING_FAILURE',
                      accounting_policy=lambda p, e: e['status'] == 'ACCOUNTING_FAILURE')
    assert result['status'] == 'COMPLETED' and launched == ['RECIPE', 'TM0']
    assert result['done'][0]['status'] == 'ACCOUNTING_FAILURE'
    assert json.loads((tmp_path / 'folds/A/episodes/A__initial__RECIPE__r1/episode.json').read_text())['accounting']['passed'] is False


@pytest.mark.parametrize('missing', ['terminal', 'accounting', 'both'])
def test_dispatch_preserves_default_fault_stop(tmp_path, missing):
    dispatch, jobs, launched, popen = dispatch_fixture(tmp_path)
    result = dispatch(tmp_path, jobs, popen=popen, sleep=lambda _: None,
                      terminal_policy=(lambda p, e: True) if missing == 'accounting' else None,
                      accounting_policy=(lambda p, e: True) if missing == 'terminal' else None)
    assert result['status'] == 'PAUSED' and launched == ['RECIPE'] and result['pending'] == 1


def test_controller_uses_the_same_typed_accounting_callback():
    text = input_source('t2_freepdk45_main.py')
    out = S.main_delta(text)
    assert 'accounting_policy=reviewed_accounting_stop' in out
    assert 'return standing_skip(' in out and "if not p.exists():pending.append(row);continue" in out
    assert "llm_requests']>3" in out
    with pytest.raises(ValueError): S.main_delta(out)


def rebind_fixture(root):
    files = [root / 'online-budget.json', root / 'frozen-memory-plan.json']
    files += [root / f'folds/C{i:05}/registration.json' for i in range(50)]
    grant = {'files': {}, 'policy_rebinds': {}}
    for p in files:
        staged = root / 'staged' / p.relative_to(root); staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_text('{}')
        if p.name == 'registration.json': p.parent.mkdir(parents=True, exist_ok=True); p.write_text('{}')
        h = S.A.sha(staged)
        grant['files'][str(p)] = {'staged': str(staged), 'sha256': h}
        grant['policy_rebinds'][str(p)] = h
    return grant


def test_rebind_only_updates_exact_registered_policy_references(tmp_path):
    grant = rebind_fixture(tmp_path); reg = {'files': {'unchanged-runtime': 'original'}}
    S.policy_rebind(tmp_path, grant, reg)
    assert len(reg['files']) == 53 and reg['files']['unchanged-runtime'] == 'original'


@pytest.mark.parametrize('change', ['missing', 'extra', 'wrong_hash', 'changed_stage'])
def test_partial_or_unregistered_rebind_is_refused(tmp_path, change):
    grant = rebind_fixture(tmp_path); key = next(iter(grant['policy_rebinds']))
    if change == 'missing': del grant['policy_rebinds'][key]
    if change == 'extra': grant['policy_rebinds']['/outside/file'] = 'bad'
    if change == 'wrong_hash': grant['policy_rebinds'][key] = 'bad'
    if change == 'changed_stage': Path(grant['files'][key]['staged']).write_text('changed')
    with pytest.raises(ValueError): S.policy_rebind(tmp_path, grant, {'files': {}})


def test_prepare_requires_authorization_before_writes(tmp_path):
    with pytest.raises(ValueError): S.prepare(tmp_path, tmp_path / 'revision', None, '')
    assert not (tmp_path / 'revision').exists()


def standing_fixture(tmp_path, monkeypatch):
    p = tmp_path / 'folds/C1/episodes/C1__RECIPE__r1'
    p.mkdir(parents=True); (p / 'reports').mkdir()
    b = {'tokens_in': 10, 'tokens_out': 20, 'llm_requests': 1}
    def ledger(hold):
        return [{'type': 'budget_hold', 'hold_id': hold, 'needs': dict(b), 'ts': 1},
                {'type': 'budget_settle', 'hold_id': hold, 'booked': dict(b),
                 'actual': {'tokens_in': 'UNKNOWN', 'tokens_out': 'UNKNOWN', 'llm_requests': 1}, 'ts': 2}]
    c = call(hold_id='local', phase_hold_id='phase')
    local = ledger('local') + [c]; phase = ledger('phase')
    e = {'status': 'ACCOUNTING_FAILURE', 'terminal_reason': 'accounting_failure', 'error': S.A.UNKNOWN_ERROR,
         'accounting': {'passed': False, 'errors': [S.A.USAGE_ERROR], 'llm_requests': 1}}
    def write(path, data):
        path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(data))
    write(p / 'episode.json', e)
    led = p / 'reports/resource_ledger.jsonl'
    led.write_text(''.join(json.dumps(x) + '\n' for x in local))
    shared = tmp_path / 'online-phase/resource_ledger.jsonl'; shared.parent.mkdir()
    shared.write_text(''.join(json.dumps(x) + '\n' for x in phase))
    write(tmp_path / 'online-budget.json', {'caps': {}, 'reserve': {}, 'halt_on_unknown_usage': False})
    grant = tmp_path / 'rev/registration.json'
    write(grant, {'root': str(tmp_path), 'unknown_skip_rule': S.RULE})
    T = types.ModuleType('t2a_runtime'); T.accounting_check = lambda events, runs: copy.deepcopy(e['accounting'])
    G = types.ModuleType('t2_freepdk45_online_gate'); G.install = lambda _: None
    monkeypatch.setitem(sys.modules, 't2a_runtime', T)
    monkeypatch.setitem(sys.modules, 't2_freepdk45_online_gate', G)
    return p, e, grant, shared, T


def test_future_unknown_is_receipted_without_rewriting_or_retry(tmp_path, monkeypatch):
    p, e, grant, shared, T = standing_fixture(tmp_path, monkeypatch)
    before = {str(x): x.read_bytes() for x in [p / 'episode.json', p / 'reports/resource_ledger.jsonl', shared]}
    assert S.standing_skip(tmp_path, p, e, grant)
    assert S.standing_skip(tmp_path, p, e, grant)
    report = json.loads((grant.parent / 'unknown-reviews' / (p.name + '.json')).read_text())
    assert report['no_retry'] and report['no_rerun'] and not report['measured_repair_outcome']
    assert report['actual_usage'] == 'UNKNOWN'
    assert all(Path(x).read_bytes() == data for x, data in before.items())
    assert len((grant.parent / 'unknown-reviews/incidents.jsonl').read_text().splitlines()) == 1
    with shared.open('a') as stream:
        stream.write(json.dumps({'type': 'budget_hold', 'hold_id': 'another-live-worker', 'needs': {'llm_requests': 1}, 'ts': 3}) + '\n')
    assert S.standing_skip(tmp_path, p, e, grant)  # another worker may still be live


def test_independent_flow_accounting_failure_is_not_waived(tmp_path, monkeypatch):
    p, e, grant, shared, T = standing_fixture(tmp_path, monkeypatch)
    T.accounting_check = lambda events, runs: {**e['accounting'], 'errors': [S.A.USAGE_ERROR, 'missing flow outcome']}
    assert not S.standing_skip(tmp_path, p, e, grant)


def test_standing_rule_is_not_enabled_by_default(tmp_path, monkeypatch):
    p, e, grant, shared, T = standing_fixture(tmp_path, monkeypatch)
    grant.write_text(json.dumps({'root': str(tmp_path)}))
    assert not S.standing_skip(tmp_path, p, e, grant)


def test_old_phase_prefix_drift_is_detected(tmp_path, monkeypatch):
    p, e, grant, shared, T = standing_fixture(tmp_path, monkeypatch)
    assert S.standing_skip(tmp_path, p, e, grant)
    # Semantically equal reformatting still changes the original evidence bytes.
    shared.write_text(shared.read_text().replace('"ts": 1', '"ts": 1.0'))
    with pytest.raises(ValueError, match='review drift'): S.standing_skip(tmp_path, p, e, grant)
