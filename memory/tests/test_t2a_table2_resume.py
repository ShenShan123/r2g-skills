"""T2a Table 2 resume controller (2026-10-16): continue a paused batch, never restart, never re-run."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('resume', ROOT / 'memory/evaluation/tools/t2a_table2_resume.py')
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)

PLAN = [{'task': 'riscv32i/default', 'arm': a, 'repeat': 1} for a in ('AMEM', 'RECIPE', 'NB', 'TM0', 'RET', 'AWM')]


def _started(root, *arms):
    for arm in arms:
        d = root / 'episodes' / ('riscv32i__default__%s__r1' % arm)
        d.mkdir(parents=True)
        (d / 'episode.json').write_text(json.dumps({'status': 'X', 'ended': 1}))


def test_only_never_started_rows_are_dispatched_in_plan_order(tmp_path):
    _started(tmp_path, 'AMEM', 'RECIPE')
    assert [r['arm'] for r in R.remaining_rows(tmp_path, PLAN)] == ['NB', 'TM0', 'RET', 'AWM']


def test_an_unended_episode_refuses_resume(tmp_path):
    _started(tmp_path, 'AMEM')
    d = tmp_path / 'episodes/riscv32i__default__RECIPE__r1'
    d.mkdir(parents=True)
    (d / 'episode.json').write_text(json.dumps({'status': 'RUNNING'}))
    with pytest.raises(RuntimeError, match='not ended'):
        R.ended_episodes(tmp_path)


def test_only_the_authorized_incident_is_acknowledged():
    hold, ep = '5e5d74a7c81d', 'riscv32i__default__AMEM__r1'
    ok = ['ledger UNKNOWN usage on hold 5e5d74a7c81d', ep + ': accounting failure', ep + ': UNKNOWN usage']
    assert R.incident_only(ok, hold, ep) == []
    assert R.incident_only(ok + ['riscv32i__default__NB__r1: runtime exception: x'], hold, ep) == [
        'riscv32i__default__NB__r1: runtime exception: x']
    assert R.incident_only(['ledger UNKNOWN usage on hold aaaa'], hold, ep) == ['ledger UNKNOWN usage on hold aaaa']


class _Done:
    returncode = 0
    def poll(self): return 0


def _pairs(rows, width):
    return [rows[i:i + width] for i in range(0, len(rows), width)]


def test_dispatch_runs_waves_and_stops_at_a_pause_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(R.time, 'sleep', lambda s: None)
    rows = R.remaining_rows(tmp_path, PLAN)
    launched = []

    def spawn(slot, row):
        launched.append(row['arm'])
        return _Done()
    pause = {'on': False}
    done, planned, paused = R.dispatch(tmp_path, rows, [[0], [1]], spawn, lambda: pause['on'], _pairs,
                                       on_wave=lambda: None)
    assert (done, planned, paused) == (3, 3, False) and launched == [r['arm'] for r in PLAN]
    launched.clear()

    def spawn_then_pause(slot, row):
        launched.append(row['arm'])
        if len(launched) == 2:
            pause['on'] = True
        return _Done()
    other = tmp_path / 'other'
    other.mkdir()
    assert R.dispatch(other, R.remaining_rows(other, PLAN), [[0], [1]], spawn_then_pause, lambda: pause['on'],
                      _pairs)[2] is True
    assert launched == ['AMEM', 'RECIPE']


def test_dispatch_never_reruns_an_existing_episode(tmp_path):
    rows = R.remaining_rows(tmp_path, PLAN)
    _started(tmp_path, 'AMEM')                    # appears after planning: must be refused, not re-run
    with pytest.raises(RuntimeError, match='re-run'):
        R.dispatch(tmp_path, rows, [[0], [1]], lambda s, r: _Done(), lambda: False, _pairs)


def test_a_failed_worker_is_not_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(R.time, 'sleep', lambda s: None)

    class Fail(_Done):
        returncode = 1
    calls = []
    with pytest.raises(RuntimeError, match='no implicit retry'):
        R.dispatch(tmp_path, R.remaining_rows(tmp_path, PLAN), [[0], [1]],
                   lambda s, r: calls.append(r['arm']) or Fail(), lambda: False, _pairs)
    assert calls == ['AMEM', 'RECIPE']


# --- numbered resume, ledger-refusal classification, unified summary (incident 2, 2026-10-16) ---
spec_s = importlib.util.spec_from_file_location('summary', ROOT / 'memory/evaluation/tools/t2a_table2_summary.py')
S = importlib.util.module_from_spec(spec_s)
spec_s.loader.exec_module(S)


def test_incidents_may_name_several_holds_and_collateral_episodes():
    holds, eps = ['5e5d74a7c81d', '3f32409bc5bb'], ['gcd__default__AMEM__r2', 'gcd__default__RET__r2']
    problems = ['ledger UNKNOWN usage on hold 3f32409bc5bb', 'gcd__default__RET__r2: ledger-refusal stop',
                'gcd__default__AMEM__r2: accounting failure']
    assert R.incident_only(problems, holds, eps) == []
    assert R.incident_only(problems + ['gcd__default__NB__r2: runtime exception: x'], holds, eps) == [
        'gcd__default__NB__r2: runtime exception: x']


def _episode(root, name, data, events=()):
    d = root / 'episodes' / name
    (d / 'reports').mkdir(parents=True)
    (d / 'episode.json').write_text(json.dumps({'ended': 1, **data}))
    (d / 'reports/resource_ledger.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events))


def test_a_ledger_refusal_stop_is_flagged_even_with_a_not_accepted_status(tmp_path):
    plan = [{'task': 'gcd/default', 'arm': a, 'repeat': 2} for a in ('RET', 'NB')]
    _episode(tmp_path, 'gcd__default__RET__r2', {'status': 'NOT_ACCEPTED', 'repair_turns': [
        {'status': 'REQUEST_STOP', 'usage': {'budget_exhausted': True, 'scope': 'phase'}}]})
    _episode(tmp_path, 'gcd__default__NB__r2', {'status': 'NOT_ACCEPTED', 'repair_turns': [{'status': 'REGRESSION'}]})
    flagged = R.ledger_refusal_problems(tmp_path, plan)
    assert len(flagged) == 1 and flagged[0].startswith('gcd__default__RET__r2: ledger-refusal stop')


def test_resume_names_keep_resume_one_and_number_later_ones():
    assert R.names(1)['registration'] == 'table2-resume-registration.json'
    assert R.names(1)['operator'] == 'operator/t2a_table2_resume.py'
    two = R.names(2)
    assert two['registration'] == 'table2-resume-registration-2.json' and two['paused_before'] == 'batch-paused-2.json'
    assert two['operator'] == 'operator/t2a_table2_resume_2.py' and two['paused_after'] == 'batch-paused-3.json'


@pytest.mark.parametrize('data,events,cls', [
    ({'status': 'ACCEPTED'}, [], 'ACCEPTED'),
    ({'status': 'NOT_ACCEPTED'}, [], 'MEASURED_FAILURE'),
    ({'status': 'NOT_ACCEPTED', 'repair_turns': [{'status': 'REQUEST_STOP', 'usage': {'budget_exhausted': True}}]},
     [], 'INFRA_BLOCKED'),
    ({'status': 'NOT_ACCEPTED'}, [{'type': 'budget_refused'}], 'INFRA_BLOCKED'),
    ({'status': 'ACCOUNTING_FAILURE'}, [], 'INFRA_BLOCKED'),
    ({'status': 'UNVERIFIED_OR_SAFETY_STOP', 'terminal_reason': 'repair_allowance_exhausted_known_incomplete'}, [],
     'KNOWN_INCOMPLETE'),
    ({'status': 'UNVERIFIED_OR_SAFETY_STOP', 'terminal_reason': 'restoration_unverified_or_safety_stop'}, [],
     'HARNESS_STOP'),
])
def test_every_cell_gets_one_outcome_class(data, events, cls):
    assert S.classify(data, events) == cls


class _T:
    @staticmethod
    def request_token_totals(events):
        return {'llm_requests': 0, 'repair_requests': 0, 'auxiliary_requests': 0, 'known_input_tokens': 0,
                'known_output_tokens': 0, 'unknown_usage_requests': 0}


def test_summary_counts_known_incomplete_losses_and_excludes_infra_blocked_pairs(tmp_path):
    plan = [{'task': 'ibex/u80', 'arm': a, 'repeat': r} for r in (1, 2) for a in ('NB', 'RECIPE')]
    (tmp_path / 'registration.json').write_text(json.dumps(
        {'plan': plan, 'arms': ['NB', 'RECIPE'], 'tasks': [{'task': 'ibex/u80'}], 'repeats': 2}))
    _episode(tmp_path, 'ibex__u80__NB__r1', {'status': 'ACCEPTED'})
    _episode(tmp_path, 'ibex__u80__NB__r2', {'status': 'ACCEPTED'})
    _episode(tmp_path, 'ibex__u80__RECIPE__r1', {'status': 'UNVERIFIED_OR_SAFETY_STOP',
                                                  'terminal_reason': 'repair_allowance_exhausted_known_incomplete'})
    _episode(tmp_path, 'ibex__u80__RECIPE__r2', {'status': 'NOT_ACCEPTED'})
    s = S.summarize(tmp_path, _T)
    recipe = s['by_arm']['RECIPE']
    assert recipe['paired_vs_NB_frozen_view'] == {'G': 0, 'H': 1, 'eligible': 1}
    assert recipe['paired_vs_NB_measured_view']['H'] == 2 and recipe['paired_vs_NB_measured_view']['eligible'] == 2
    assert s['raw_records_modified'] is False and s['complete'] is True


def _ev(seq, **kw):
    return json.dumps({'event_seq': seq, **kw})


def test_a_ledger_refusal_is_acknowledged_only_inside_a_reviewed_unknown_window():
    lines = [_ev(1, type='budget_hold', hold_id='h'),
             _ev(2, type='budget_settle', hold_id='h', actual={'tokens_in': 'UNKNOWN'}),
             _ev(3, type='budget_refused', reason='usage is UNKNOWN; queue paused'),
             _ev(4, type='budget_unknown_occupancy', hold_id='h')]
    assert R.refusals_within_incident_windows(lines, ['h']) is True
    late = lines + [_ev(5, type='budget_refused', reason='usage is UNKNOWN; queue paused')]
    assert R.refusals_within_incident_windows(late, ['h']) is False          # after the occupancy: not reviewed
    other = lines + [_ev(5, type='budget_refused', reason='cap exceeded')]
    assert R.refusals_within_incident_windows(other, ['h']) is False
    assert R.refusals_within_incident_windows(lines[:3], ['h']) is False     # no authorized occupancy yet


def test_a_technical_failure_is_its_own_outcome_class():
    assert S.classify({'status': 'UNVERIFIED_OR_SAFETY_STOP', 'terminal_reason': 'technical_failure'}, []) == 'TECHNICAL_FAILURE'
    assert S.classify({'status': 'UNVERIFIED_OR_SAFETY_STOP', 'technical_failures': [{'kind': 'diagnose_crash'}]}, []) == 'TECHNICAL_FAILURE'
