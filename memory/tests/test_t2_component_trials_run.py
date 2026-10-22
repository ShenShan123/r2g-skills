"""GRT component-trial executor helpers (no EDA): authorisation by hash, config edits applied
exactly once from the same state, reproduction rule, and the chain-v1 trial row."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'memory' / 'scripts'))
import t2_component_trials_run as R  # noqa: E402

CONTRACT = ROOT / 'memory' / 'evaluation' / 't2_grt_component_trials_contract_DRAFT.json'


def auth(tmp_path, **over):
    c = json.loads(CONTRACT.read_text())
    a = {'contract_sha256': R.sha(CONTRACT), 'user_approved': True, 'caps': c['caps'],
         'automatic_expansion': False, 'llm_requests': 0, 'states': ['picorv32@u84', 'des_area@u77'], **over}
    p = tmp_path / 'auth.json'
    p.write_text(json.dumps(a))
    return p


def test_authorisation_is_bound_to_the_contract_hash_and_scope(tmp_path):
    assert R.authorized(CONTRACT, auth(tmp_path))['schema'] == 't2-grt-component-trials-v3'
    for over in ({'contract_sha256': '0' * 64}, {'user_approved': False}, {'llm_requests': 1},
                 {'automatic_expansion': True}, {'states': ['picorv32@u84']}):
        with pytest.raises(ValueError):
            R.authorized(CONTRACT, auth(tmp_path, **over))


def test_edits_are_set_exactly_once_and_the_fixed_die_is_removed():
    text = 'export DIE_AREA = 0 0 200 200\nexport CORE_AREA = 10 10 190 190\nexport CORE_UTILIZATION = 40\nexport X = 1\n'
    out = R.set_knobs(text, {'CORE_UTILIZATION': '77'}, drop=('DIE_AREA', 'CORE_AREA'))
    assert 'DIE_AREA' not in out and 'CORE_AREA' not in out and out.count('CORE_UTILIZATION') == 1
    assert 'export CORE_UTILIZATION = 77' in out and 'export X = 1' in out
    out2 = R.set_knobs(out, {'ROUTING_LAYER_ADJUSTMENT': '0.1'})
    assert out2.count('ROUTING_LAYER_ADJUSTMENT') == 1 and 'export CORE_UTILIZATION = 77' in out2


def m(stage, code, present=True):
    return {'stage': {'measured': True, 'present': present,
                      'fields': {'stage': {'value': stage}, 'error_code': {'value': code}}}}


def test_only_a_route_grt_0116_abort_reproduces_the_state():
    assert R.reproduced(m('route', 'GRT-0116')) is True
    assert R.reproduced(m('route', 'DRT-0000')) is False
    assert R.reproduced(m('place', 'FLW-0024')) is False
    assert R.reproduced(m('finish', None, present=False)) is False


def test_the_trial_row_is_a_chain_v1_attempt_and_the_control_is_not():
    state = {'design': 'picorv32', 'CORE_UTILIZATION': 84}
    eff = {'CORE_UTILIZATION': {'value': '84'}, 'ROUTING_LAYER_ADJUSTMENT': {'value': '0.5'}}
    row = R.trial_row(state, {'id': 'routing_layer_adj_0.1', 'kind': 'action', 'edit': {'ROUTING_LAYER_ADJUSTMENT': '0.1'}},
                      'ctrial-picorv32__u84-routing_layer_adj_0.1#1', 'RUN_BASE', 'RUN_T', eff, 'cleared', {}, 2267, 0)
    assert row['chain_version'] == 1 and row['pre_run_tag'] == 'RUN_BASE' and row['post_run_tag'] == 'RUN_T'
    assert row['config_before'] == {'ROUTING_LAYER_ADJUSTMENT': '0.5'}
    assert json.loads(row['config_delta']) == {'ROUTING_LAYER_ADJUSTMENT': '0.1'}
    assert row['rolled_back'] is False and row['protocol_restored'] is True
    assert row['situation']['error_code'] == 'GRT-0116' and row['situation']['util_band'] == 'gt60'
