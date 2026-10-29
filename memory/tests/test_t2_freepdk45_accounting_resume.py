"""Incident receipts do not turn unknown usage or blocked runs into repair data."""
import importlib.util
import json
from pathlib import Path

import pytest

SOURCE = Path(__file__).parents[1] / 'scripts/t2_freepdk45_accounting_resume.py'
spec = importlib.util.spec_from_file_location('accounting_incident', SOURCE)
A = importlib.util.module_from_spec(spec)
spec.loader.exec_module(A)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def events(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(v) + '\n' for v in values))


def unknown(tmp_path):
    p = tmp_path / 'folds/C1/episodes/C1__RECIPE__r1'
    b = {'tokens_in': 24016, 'tokens_out': 393216, 'llm_requests': 1}
    def ledger(hold):
        return [{'type': 'budget_hold', 'hold_id': hold, 'needs': dict(b), 'ts': 1},
                {'type': 'budget_settle', 'hold_id': hold, 'booked': dict(b),
                 'actual': {'tokens_in': 'UNKNOWN', 'tokens_out': 'UNKNOWN', 'llm_requests': 1}, 'ts': 2}]
    local = ledger('local')
    local.append({'type': 'llm_request', 'hold_id': 'local', 'phase_hold_id': 'phase',
                  'usage_reported': 'UNKNOWN', 'status': -1, 'model_reported': None, 'request_id': None})
    e = {'status': 'ACCOUNTING_FAILURE', 'terminal_reason': 'accounting_failure', 'error': A.UNKNOWN_ERROR,
         'accounting': {'passed': False, 'errors': [A.USAGE_ERROR], 'llm_requests': 1}}
    write(p / 'episode.json', e)
    events(p / 'reports/resource_ledger.jsonl', local)
    return p, e, local, ledger('phase')


def blocked(tmp_path):
    p = tmp_path / 'folds/C2/episodes/C2__TM0__r1'
    prefix = p / 'agent-decision-2'
    e = {'status': 'UNVERIFIED_OR_SAFETY_STOP', 'terminal_reason': 'episode_exception',
         'error': A.BLOCKED_ERROR, 'ended': 7,
         'accounting': {'passed': True, 'errors': [], 'llm_requests': 0}}
    local = [{'type': 'agent_request_intent', 'prefix': str(prefix), 'ts': 3},
             {'type': 'budget_hold', 'hold_id': 'turn', 'ts': 4, 'needs': {'repair_attempts': 1}},
             {'type': 'budget_settle', 'hold_id': 'turn', 'ts': 6, 'actual': {'repair_attempts': 0.0}}]
    phase = [{'type': 'budget_settle', 'hold_id': 'unknown', 'ts': 2, 'actual': {'tokens_in': 'UNKNOWN'}},
             {'type': 'budget_refused', 'reason': A.PAUSE_REASON, 'ts': 5}]
    write(p / 'episode.json', e)
    events(p / 'reports/resource_ledger.jsonl', local)
    return p, e, local, phase


def test_no_response_keeps_actual_unknown(tmp_path):
    p, e, local, phase = unknown(tmp_path)
    before = (p / 'episode.json').read_bytes(), (p / 'reports/resource_ledger.jsonl').read_bytes()
    result = A.classify(p, phase)
    assert result['classification'] == 'ACCOUNTING_FAILURE'
    assert sum(result['upper_bound'].values()) == 417232
    assert before == ((p / 'episode.json').read_bytes(), (p / 'reports/resource_ledger.jsonl').read_bytes())


@pytest.mark.parametrize('change', ['response_id', 'model', 'status', 'second_unknown', 'error',
                                   'extra_accounting_error', 'open_hold', 'wrong_bound', 'wrong_count'])
def test_unrelated_accounting_failure_rejected(tmp_path, change):
    p, e, local, phase = unknown(tmp_path)
    if change == 'response_id': local[-1]['request_id'] = 'received'
    if change == 'model': local[-1]['model_reported'] = 'deepseek-flash'
    if change == 'status': local[-1]['status'] = 400
    if change == 'second_unknown': local.append(dict(local[-1])); e['accounting']['llm_requests'] = 2
    if change == 'error': e['error'] = 'different failure'
    if change == 'extra_accounting_error': e['accounting']['errors'].append('missing flow')
    if change == 'open_hold': local.append({'type': 'budget_hold', 'hold_id': 'open'})
    if change == 'wrong_bound': phase[1]['booked']['tokens_in'] = 999
    if change == 'wrong_count': e['accounting']['llm_requests'] = 0
    write(p / 'episode.json', e); events(p / 'reports/resource_ledger.jsonl', local)
    with pytest.raises(ValueError): A.classify(p, phase)


def test_phase_refusal_is_not_measured_failure(tmp_path):
    p, e, local, phase = blocked(tmp_path)
    d = A.classify(p, phase)
    assert d['classification'] == 'INFRASTRUCTURE_BLOCKED' and d['request_not_sent']


@pytest.mark.parametrize('change', ['missing_refusal', 'two_refusals', 'other_reason', 'no_unknown',
                                   'outside_opportunity', 'charged_turn', 'provider_artifact',
                                   'wrong_owner', 'wrong_status', 'accounting_error'])
def test_blocked_classification_requires_both_ledgers(tmp_path, change):
    p, e, local, phase = blocked(tmp_path)
    if change == 'missing_refusal': phase.pop()
    if change == 'two_refusals': phase.append(dict(phase[-1]))
    if change == 'other_reason': phase[-1]['reason'] = 'ordinary three-call completion'
    if change == 'no_unknown': phase.pop(0)
    if change == 'outside_opportunity': phase[-1]['ts'] = 9
    if change == 'charged_turn': local[-1]['actual'] = {'repair_attempts': 1}
    if change == 'provider_artifact': (p / 'agent-decision-2.try0.request.json').write_text('{}')
    if change == 'wrong_owner': local[0]['prefix'] = str(tmp_path / 'outside')
    if change == 'wrong_status': e['status'] = 'NOT_ACCEPTED'
    if change == 'accounting_error': e['accounting']['errors'] = ['missing flow']
    write(p / 'episode.json', e); events(p / 'reports/resource_ledger.jsonl', local)
    with pytest.raises(ValueError): A.classify(p, phase)


def receipt(tmp_path):
    p, e, local, phase = blocked(tmp_path)
    led = tmp_path / 'online-phase/resource_ledger.jsonl'; events(led, phase)
    rev = tmp_path / 'revision'
    report = rev / 'accounting-reviews/cell.json'
    write(report, {'rule': A.RULE, 'project': str(p), 'classification': 'INFRASTRUCTURE_BLOCKED',
                   'raw_status': e['status'], 'raw_error': e['error'], 'no_rerun': True,
                   'measured_repair_outcome': False,
                   'files': {str(p / 'episode.json'): A.sha(p / 'episode.json'),
                             str(p / 'reports/resource_ledger.jsonl'): A.sha(p / 'reports/resource_ledger.jsonl')},
                   'phase_ledger': str(led), 'phase_prefix_bytes': led.stat().st_size,
                   'phase_prefix_sha256': A.sha(led)})
    grant = rev / 'registration.json'
    write(grant, {'root': str(tmp_path), 'accounting_incident_rule': A.RULE,
                  'accounting_incident_reviews': {str(p): {'path': str(report), 'sha256': A.sha(report)}}})
    return p, e, led, report, grant


def test_valid_receipt_survives_only_append_to_phase(tmp_path):
    p, e, led, report, grant = receipt(tmp_path)
    assert A.reviewed_stop(tmp_path, p, e, grant)
    with led.open('a') as f: f.write(json.dumps({'type': 'new_event'}) + '\n')
    assert A.reviewed_stop(tmp_path, p, e, grant)


@pytest.mark.parametrize('target', ['episode.json', 'reports/resource_ledger.jsonl', 'review', 'phase'])
def test_tampering_fails_closed(tmp_path, target):
    p, e, led, report, grant = receipt(tmp_path)
    path = {'review': report, 'phase': led}.get(target, p / target)
    path.write_text('tampered')
    with pytest.raises(ValueError): A.reviewed_stop(tmp_path, p, e, grant)


@pytest.mark.parametrize('change', ['not_registered', 'wrong_rule', 'wrong_root', 'outside_folds'])
def test_no_blanket_future_authorization(tmp_path, change):
    p, e, led, report, grant = receipt(tmp_path)
    data = A.load(grant)
    if change == 'not_registered': data['accounting_incident_reviews'] = {}
    if change == 'wrong_rule': data['accounting_incident_rule'] = 'other'
    if change == 'wrong_root': data['root'] = '/elsewhere'
    if change == 'outside_folds': p = tmp_path / 'nb/episodes/other'
    write(grant, data)
    assert not A.reviewed_stop(tmp_path, p, e, grant)


def test_delta_changes_controller_not_worker_policy():
    root = Path('/data1/zhangdy/tehm-experiments/t2-freepdk45-main50-adamem-20261022-r3')
    archived = root / 'accounting-resume-20261025-r17/original/code/memory/scripts/t2_freepdk45_main.py'
    runtime = archived if archived.exists() else root / 'code/memory/scripts/t2_freepdk45_main.py'
    text = runtime.read_text()
    out = A.controller_delta(text)
    assert 'reviewed_accounting_stop' in out
    assert "if not p.exists():pending.append(row);continue" in out
    assert "if e['accounting']['llm_requests']>3" in out
    assert out.count('reviewed_accounting_stop(p,e)') == 1
    assert 'STANDING_LVS_INTERNAL' in out and 'INPUT_GATE' in out
    with pytest.raises(ValueError): A.controller_delta(out)


def test_prepare_requires_authority_before_changes(tmp_path):
    with pytest.raises(ValueError): A.prepare(tmp_path, tmp_path / 'new', tmp_path / 'tests', '')
    assert not (tmp_path / 'new').exists()
