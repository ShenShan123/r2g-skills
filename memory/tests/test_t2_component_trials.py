"""GRT component-trial contract validation and per-trial classification (offline only)."""
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'memory' / 'scripts'))
import t2_component_trials as CT  # noqa: E402

CONTRACT = ROOT / 'memory' / 'evaluation' / 't2_grt_component_trials_contract_DRAFT.json'


def c():
    return json.loads(CONTRACT.read_text())


def test_the_draft_contract_is_valid_with_its_inputs_hash_checked():
    assert CT.validate(c()) == []


@pytest.mark.parametrize('mutate,needle', [
    (lambda x: x['states'][0]['components'][3].__setitem__('edit', {'GPL_ROUTABILITY_DRIVEN': '1'}), 'already in effect'),
    (lambda x: x['states'][0]['components'][1].__setitem__('edit', {'CORE_UTILIZATION': '84'}), 'already in effect'),
    (lambda x: x['states'][0]['components'][2].__setitem__('edit', {'CORE_UTILIZATION': '56'}), 'duplicates'),
    (lambda x: x['states'][0]['components'][4].__setitem__('edit', {'CORE_UTILIZATION': '50'}), 'control carries no edit'),
    (lambda x: x['states'][1]['components'].pop(), 'differ between states'),
    (lambda x: x['caps'].__setitem__('flows_total', 20), 'caps'),
    (lambda x: x['caps'].__setitem__('llm_requests', 1), 'caps'),
    (lambda x: x.__setitem__('platform', 'sky130hs'), 'sky130hd'),
    (lambda x: x.__setitem__('no_automatic_expansion', False), 'expansion'),
    (lambda x: x['area'].__setitem__('caps', {'stdcell': 0.55, 'core': 2.0, 'die': 1.0}), 'area caps'),
    (lambda x: x['area'].__setitem__('manifest', ''), 'area reference')])
def test_contract_violations(mutate, needle):
    x = c()
    mutate(x)
    assert any(needle in e for e in CT.validate(x, check_files=False)), CT.validate(x, check_files=False)


def test_a_changed_immutable_input_is_reported(tmp_path):
    x = c()
    f = tmp_path / 'rtl.v'
    f.write_text('module m; endmodule\n')
    x['states'][0]['immutable']['rtl'] = {str(f): '0' * 64}
    assert any('hash' in e for e in CT.validate(x))


ACTION = {'id': 'routing_layer_adj_0.1', 'kind': 'action', 'edit': {'ROUTING_LAYER_ADJUSTMENT': '0.1'}}
CONTROL = {'id': 'noop_rerun_control', 'kind': 'control', 'edit': {}}


def test_an_edit_already_in_effect_is_a_no_op_never_a_fix():
    r = CT.classify(ACTION, {'ROUTING_LAYER_ADJUSTMENT': '0.1'}, {'ROUTING_LAYER_ADJUSTMENT': '0.1'}, 'CLEARED',
                    {'present': False}, {'accepted': True})
    assert r['applied'] == 'NO_OP' and r['fix_credit'] is False and r['task_success'] is False


def test_an_unverified_edit_earns_nothing():
    r = CT.classify(ACTION, None, None, 'CLEARED', {'present': False}, {'accepted': True})
    assert r['applied'] == 'UNVERIFIED' and not r['fix_credit']
    r = CT.classify(ACTION, {'ROUTING_LAYER_ADJUSTMENT': '0.5'}, {'ROUTING_LAYER_ADJUSTMENT': '0.5'}, 'CLEARED',
                    {'present': False}, None)
    assert r['applied'] == 'UNVERIFIED'


def test_congestion_later_failure_and_acceptance_are_reported_separately():
    r = CT.classify(ACTION, {'ROUTING_LAYER_ADJUSTMENT': '0.5'}, {'ROUTING_LAYER_ADJUSTMENT': '0.1'}, 'CLEARED',
                    {'present': True, 'stage': 'route', 'error_code': None, 'timeout': True}, {'accepted': False},
                    {'status': 'PASS'})
    assert (r['applied'], r['congestion'], r['acceptance']) == ('APPLIED', 'CLEARED', 'NOT_ACCEPTED')
    assert r['later_failure'] == {'stage': 'route', 'error_code': None, 'timeout': True}
    assert r['fix_credit'] is True and r['task_success'] is False


def test_a_control_never_earns_credit():
    r = CT.classify(CONTROL, {}, {}, 'CLEARED', {'present': False}, {'accepted': True})
    assert r['applied'] == 'CONTROL' and not r['fix_credit'] and not r['task_success']


def test_credit_needs_the_area_envelope_to_pass():
    base, trial = {'ROUTING_LAYER_ADJUSTMENT': '0.5'}, {'ROUTING_LAYER_ADJUSTMENT': '0.1'}
    for env, credit in (({'status': 'PASS'}, True), ({'status': 'FAIL'}, False), ({'status': 'UNVERIFIED'}, False),
                        (None, False)):
        r = CT.classify(ACTION, base, trial, 'CLEARED', {'present': False}, {'accepted': True}, env)
        assert r['fix_credit'] is credit and r['task_success'] is credit
