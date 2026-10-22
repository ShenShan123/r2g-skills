"""ann-v2 manual-audit package (spec §8): fixed-seed stratified sample, reviewer worksheet
with raw-file pointers, and the audit summary (any critical error blocks the freeze; the
non-critical rate carries an exact binomial upper bound, never a "below 2%" claim).
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'memory'))
from ann_v2 import audit as AU  # noqa: E402


def rec(i, kind='drc', effect='CLEARED', positive=False, bound=True):
    f = lambda v, p='/p/reports/fix_log.jsonl': {'value': v, 'status': 'KNOWN', 'unknown_reason': None,  # noqa: E731
                                                 'source': [{'path': p, 'locator': 'line 1', 'sha256': 'ab'}],
                                                 'method': 'm'}
    run = lambda tag: {'status': 'KNOWN' if bound else 'UNKNOWN', 'run': tag if bound else None,  # noqa: E731
                       'unknown_reason': None if bound else 'no_explicit_link', 'candidates': [],
                       'evidence': {'e1': {'resume_meta_reason': 'signoff fix: strategy=x rerun_from=route',
                                           'paths': ['/p/backend/' + tag]}, 'e2': {}} if bound else None}
    return {'unit_id': f'u{i}', 'status': 'ATTEMPT', 'row_kind': 'drc/li.3',
            'identity': {'design_id': f('aes_core'), 'ip_family': f('secworks/aes'), 'source_group': f('secworks')},
            'binding': {'post': run('RUN_B'), 'pre': run('RUN_A')},
            'targets': [{'type': kind, 'type_status': 'KNOWN', 'fields': {'count': f(12), 'rule_class': f('li.3')}}],
            'step_effect': {kind: effect}, 'new_failures': {'drc': 'KNOWN-none', 'lvs': 'NOT_MEASURED_BEFORE'},
            'rolled_back': f(False), 'positive': positive, 'positive_blockers': [] if positive else ['x'],
            'post_run_advisory': None}


def records():
    out = [rec(i, 'drc', 'CLEARED', positive=True) for i in range(30)]
    out += [rec(100 + i, 'drc', 'CLEARED', positive=False) for i in range(4)]
    out += [rec(200 + i, 'stage_abort', 'NO_CHANGE') for i in range(25)]
    out += [rec(300 + i, 'drv', 'UNKNOWN', bound=False) for i in range(3)]
    out += [{'unit_id': 'n1', 'status': 'NOT_AN_ATTEMPT'}]
    return out


def test_strata_are_type_by_effect_with_positives_as_their_own_substratum():
    s = AU.strata(records())
    assert set(s) == {('drc', 'CLEARED', 'positive'), ('drc', 'CLEARED', 'not_positive'),
                      ('stage_abort', 'NO_CHANGE', 'not_positive'), ('drv', 'UNKNOWN', 'not_positive')}
    assert len(s[('drc', 'CLEARED', 'positive')]) == 30 and 'n1' not in sum(s.values(), [])


def test_sample_is_deterministic_for_a_seed_and_takes_at_least_ten_or_all():
    a = AU.sample(records(), seed=20261017)
    b = AU.sample(records(), seed=20261017)
    assert a == b and a['seed'] == 20261017
    sizes = {tuple(k.split('|')): len(v) for k, v in a['by_stratum'].items()}
    assert sizes[('drc', 'CLEARED', 'positive')] == 10 and sizes[('drc', 'CLEARED', 'not_positive')] == 4
    assert sizes[('stage_abort', 'NO_CHANGE', 'not_positive')] == 10 and sizes[('drv', 'UNKNOWN', 'not_positive')] == 3
    assert AU.sample(records(), seed=1)['by_stratum'] != a['by_stratum']


def test_sample_is_independent_of_record_order():
    a = AU.sample(records(), seed=7)
    b = AU.sample(list(reversed(records())), seed=7)
    assert a['by_stratum'] == b['by_stratum']


def test_worksheet_points_the_reviewer_at_raw_files_and_lists_the_critical_checks():
    r = rec(1, positive=True)
    text = AU.worksheet(r, {'raw_row': {'strategy': 'x', 'iter': 1, 'fix_session_id': 's'},
                            'provenance': {'sources': [{'path': '/p/reports/fix_log.jsonl', 'line': 3}]}})
    for frag in ('/p/reports/fix_log.jsonl', 'line 3', '/p/backend/RUN_B', '/p/backend/RUN_A',
                 'strategy=x rerun_from=route', 'rule_class', 'li.3', 'CLEARED', 'positive: True'):
        assert frag in text, frag
    for check in AU.CRITICAL_CHECKS:
        assert f'[ ] {check}' in text
    assert 'VERDICT:' in text


@pytest.mark.parametrize('k,n,expected', [(0, 10, 0.2589), (0, 50, 0.0582), (1, 20, 0.2161)])
def test_exact_binomial_upper_bound(k, n, expected):
    assert abs(AU.binomial_upper_bound(k, n, 0.95) - expected) < 5e-4


def test_summary_blocks_the_freeze_on_any_critical_error_and_never_claims_below_two_percent():
    verdicts = [{'unit_id': 'u1', 'stratum': 'drc|CLEARED|positive', 'critical': [], 'non_critical': []},
                {'unit_id': 'u2', 'stratum': 'drc|CLEARED|positive', 'critical': ['wrong run binding'],
                 'non_critical': []}]
    s = AU.summary(verdicts, {'drc|CLEARED|positive': 30})
    assert s['freeze_blocked'] is True and s['critical_errors'] == 1
    assert s['by_stratum']['drc|CLEARED|positive']['expand_to_full_stratum'] is True
    ok = AU.summary([{'unit_id': 'u1', 'stratum': 's', 'critical': [], 'non_critical': ['aux value']},
                     *[{'unit_id': f'u{i}', 'stratum': 's', 'critical': [], 'non_critical': []} for i in range(9)]],
                    {'s': 100})
    assert ok['freeze_blocked'] is False
    st = ok['by_stratum']['s']
    assert st['non_critical'] == 1 and st['reviewed'] == 10 and st['non_critical_rate_upper_95'] > 0.02
    assert 'below 2%' not in json.dumps(ok) and ok['note'].startswith('2% is an operating threshold')
