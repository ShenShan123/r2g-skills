"""Qualification review must not convert tool errors or missing checks to repair evidence."""
import importlib.util
from pathlib import Path

import pytest

FILE = Path(__file__).resolve().parents[1] / 'evaluation/tools/learning_pilot_review.py'
spec = importlib.util.spec_from_file_location('learning_review_test', FILE)
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)


def acceptance(failed=(), unknown=()):
    checks = {k: {'result': 'unknown' if k in unknown else 'fail' if k in failed else 'pass'}
              for k in R.CHECKS}
    return {'accepted': not failed and not unknown, 'failed': list(failed),
            'unknown': list(unknown), 'checks': checks}


def test_complete_acceptance_is_not_a_repair_gain():
    result = R.classify(True, acceptance(), [], [], True)
    assert result == 'INITIAL_PASS_CONTROL'


def test_missing_check_never_becomes_accepted_or_a_measured_failure():
    a = acceptance()
    a['checks'].pop('A10_rcx')
    assert R.classify(True, a, [], [], True) == 'EVIDENCE_REVIEW_REQUIRED'
    assert R.classify(True, acceptance(unknown=['A6_setup']), [], [], True) == 'EVIDENCE_REVIEW_REQUIRED'


def test_five_pin_failures_require_a_clean_synth_to_failure_prefix():
    rows = [{'stage': 'synth', 'status': 0}, {'stage': 'floorplan', 'status': 0},
            {'stage': 'place', 'status': 2}]
    assert R.classify(False, {}, ['PPL-0024'], rows, True) == 'NATIVE_PIN_CAPACITY_FAILURE'
    rows[0]['status'] = 2
    assert R.classify(False, {}, ['PPL-0024'], rows, True) == 'EVIDENCE_REVIEW_REQUIRED'
    assert R.classify(False, {}, ['PPL-0024'], [], True) == 'EVIDENCE_REVIEW_REQUIRED'


def test_internal_router_error_is_not_congestion_evidence():
    rows = [{'stage': n, 'status': 2 if n == 'route' else 0}
            for n in ('synth', 'floorplan', 'place', 'cts', 'route')]
    assert R.classify(False, {}, ['RSZ-0074'], rows, True) == 'TOOL_ROUTING_ERROR_HELD'


def test_clock_area_and_accounting_are_not_optional():
    a = acceptance(failed=['A6_setup'])
    assert R.classify(True, a, [], [], False) == 'ACCOUNTING_OR_INPUT_REVIEW'
    assert R.classify(True, a, [], [], True) == 'VERIFIED_INITIAL_SIGNOFF_FAILURE'


def test_lvs_mismatch_is_not_silently_waived():
    a = acceptance(failed=['A3_lvs', 'A6_setup'])
    assert R.classify(True, a, [], [], True) == 'NATIVE_LVS_FAILURE_REVIEW'


def test_hook_allowance_is_literal_and_does_not_change_area_or_clock(tmp_path):
    hook = tmp_path / 'buffer_port_feedthroughs.tcl'
    hook.write_text('# known shared executor')
    original = {'allowlist': {}, 'area': {'core': {'max_rel_increase': 1}},
                'expected_clock_period_ns': 5, 'immutable': {'files': {}}}
    m = R.with_shared_hook(original, hook)
    assert not original['allowlist']
    assert m['allowlist']['POST_GLOBAL_PLACE_TCL'] == {'type': 'enum', 'values': [str(hook)]}
    assert m['area'] == original['area'] and m['expected_clock_period_ns'] == 5
    assert m['immutable']['files'][str(hook)] == 'sha256:' + R.digest(hook)


def test_no_overwrite_of_a_completed_review(tmp_path):
    with pytest.raises(RuntimeError, match='overwrite'):
        R.review(tmp_path, tmp_path)
