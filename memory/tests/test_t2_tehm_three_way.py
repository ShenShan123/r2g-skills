"""Opt-in procedural execution is evidence-gated, not a retrieval-hit replay."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

sys.path[:0] = [str(Path(__file__).parents[1] / 'scripts'), str(Path(__file__).parents[1])]
import t2_agent_memories as M
import t2_agent_runtime as R
from test_t2_agent_runtime import fake_runtime
from test_repair_turns import ep


def example():
    from t2_tehm_execution_policy import choose
    candidate = {'id': 'arbitrary-tool', 'auto_apply': True, 'recheck': 'drc',
                 'config_edits': {'CORE_UTILIZATION': '17'}, 'rerun_from': 'floorplan'}
    situation = {'platform': 'sky130hd', 'check': 'drc', 'violation_class': 'm3.2',
                 'die_mode': 'auto', 'util_band': '13-30', 'timeout': False}
    units = []
    entries = []
    for ident in ('independent-a', 'independent-b'):
        raw = {'situation': situation.copy(), 'from_stage': 'floorplan',
               'config_before': {'CORE_UTILIZATION': '20'},
               'config_delta': {'CORE_UTILIZATION': '17'},
               'pre_run_tag': 'pre-' + ident, 'post_run_tag': 'post-' + ident,
               'rolled_back': False,
               'acceptance_result': {'accepted': True, 'acceptance': {'accepted': True,
                   'failed': [], 'unknown': []}, 'envelope': {'status': 'PASS'}}}
        units.append({'unit_id': ident, 'source_handoff_id': ident, 'source_eligible': True,
                      'raw_row': raw, 'shared_label': {'admitted': True,
                          'outcome_class': 'admitted_positive', 'signature': 'drc:m3.2',
                          'binding': {'pre': 'KNOWN', 'post': 'KNOWN'},
                          'rolled_back': {'status': 'KNOWN', 'value': False},
                          'edits': candidate['config_edits'].copy()}})
        entries.append({'unit_id': ident, 'source_group': ident, 'ip_family': ident,
                        'signature': 'drc:m3.2', 'knob': 'CORE_UTILIZATION',
                        'before': 20, 'after': 17, 'direction': 'down',
                        'outcome': 'admitted_positive'})
    plan = {'strategies': [candidate], 'tehm_v2': {'state': {
        'signature': 'drc:m3.2', 'failures': ['drc:m3.2'], 'timing_unconstrained': False},
        'decisions': [{'id': candidate['id'], 'decision': 'kept', 'deferred': False}]}}
    args = dict(plan=plan, situation=situation, cfg={'CORE_UTILIZATION': '20'},
                units=units, index={'entries': entries}, exclude_families={'target'}, used=[])
    return choose, args


@pytest.mark.parametrize('value', ['17', '31'])
def test_reliable_exact_procedure_can_execute_without_causal_promotion(value):
    choose, a = example()
    a['plan']['strategies'][0]['config_edits']['CORE_UTILIZATION'] = value
    for u in a['units']:
        u['shared_label']['edits']['CORE_UTILIZATION'] = value
        u['raw_row']['config_delta']['CORE_UTILIZATION'] = value
    for e in a['index']['entries']: e['after'] = float(value)
    result = choose(**a)
    assert result['choice'] == 'local_experience'
    assert result['candidate']['config_edits']['CORE_UTILIZATION'] == value
    assert result['causal_authority_granted'] is False
    assert result['witness_ids'] == ['independent-a', 'independent-b']


@pytest.mark.parametrize('defect', ['no_positive', 'unconfirmed', 'rollback_unknown',
    'rolled_back', 'not_whole_task', 'same_lineage', 'own_family', 'different_action',
    'different_before', 'different_stage', 'different_platform', 'unknown_situation',
    'historical_harm', 'no_effect', 'used', 'deferred', 'unconstrained', 'multi_fault',
    'missing_raw_link', 'unknown_signature', 'not_auto_apply'])
def test_match_alone_never_grants_execution(defect):
    choose, a = example(); c = a['plan']['strategies'][0]
    if defect == 'no_positive':
        for u in a['units']: u['shared_label']['admitted'] = False
    elif defect == 'unconfirmed':
        for u in a['units']: u['shared_label']['outcome_class'] = 'cleared_unconfirmed'
    elif defect == 'rollback_unknown':
        for u in a['units']: u['shared_label']['rolled_back'] = {'status': 'UNKNOWN'}
    elif defect == 'rolled_back':
        for u in a['units']: u['raw_row']['rolled_back'] = True
    elif defect == 'not_whole_task':
        for u in a['units']: u['raw_row']['acceptance_result']['accepted'] = False
    elif defect == 'same_lineage':
        a['index']['entries'][1]['source_group'] = 'independent-a'
    elif defect == 'own_family':
        a['index']['entries'][1]['ip_family'] = 'target'
    elif defect == 'different_action': c['config_edits']['CORE_UTILIZATION'] = '16'
    elif defect == 'different_before': a['cfg']['CORE_UTILIZATION'] = '25'
    elif defect == 'different_stage': c['rerun_from'] = 'route'
    elif defect == 'different_platform': a['situation']['platform'] = 'sky130hs'
    elif defect == 'unknown_situation': a['situation']['die_mode'] = None
    elif defect in ('historical_harm', 'no_effect'):
        a['index']['entries'].append({**a['index']['entries'][0], 'unit_id': 'negative',
            'outcome': 'harm' if defect == 'historical_harm' else 'no_change'})
    elif defect == 'used': a['used'] = [c['id']]
    elif defect == 'deferred': a['plan']['tehm_v2']['decisions'][0]['deferred'] = True
    elif defect == 'unconstrained': a['plan']['tehm_v2']['state']['timing_unconstrained'] = True
    elif defect == 'multi_fault': a['plan']['tehm_v2']['state']['failures'].append('setup')
    elif defect == 'missing_raw_link':
        for u in a['units']: u['raw_row'].pop('post_run_tag')
    elif defect == 'unknown_signature': a['plan']['tehm_v2']['state']['signature'] = None
    elif defect == 'not_auto_apply': c['auto_apply'] = False
    assert choose(**a)['choice'] != 'local_experience'


def test_no_experience_is_not_forced_catalogue_execution():
    choose, a = example(); a['plan']['strategies'] = []
    assert choose(**a)['choice'] == 'no_experience_online'


def test_two_successful_knobs_from_different_trials_are_not_a_verified_combination():
    choose, a = example()
    a['plan']['strategies'][0]['config_edits']['PLACE_DENSITY'] = '.4'
    assert choose(**a)['choice'] == 'experience_assisted_online'


def test_failed_local_trial_withdraws_memory_before_online_and_keeps_three_calls(tmp_path, monkeypatch):
    choose, a = example()
    h, env, calls, events = fake_runtime(tmp_path, monkeypatch, [
        json.dumps({'operation': 'repair', 'arguments': {
            'set': {'CORE_UTILIZATION': '23'}, 'rerun_from': 'floorplan'}})])
    m = object.__new__(M.Memory)
    m.arm = 'TM0'; m.execution_policy = 'tehm-three-way-evidence-gated-v1'
    m.native_failures = 0; m.used = []; m.experience_disabled = False
    m.guidance_active = False; m.fallback_reason = None; m.route_state = None
    m.H = h; m.project = tmp_path
    selected = []
    def native():
        if m.experience_disabled or m.used: return None
        return a['plan']['strategies'][0]
    m.native_action = native
    m.agent_context = lambda *args: '' if m.experience_disabled else 'must be withdrawn'
    m.consume = lambda *args: None
    def execute(*args, **kwargs):
        selected.append(kwargs.get('native_apply') is not None)
        return {'status': 'FAILED', 'rolled_back': True,
                'target_progress': {'improved': False}, 'verdict': {'accepted': False}}
    monkeypatch.setattr(R, 'execute_candidate', execute)
    result = R.run(h, tmp_path, 'TM0', env, 'unused', {'accepted': False}, lambda p: None, m)
    assert selected == [True, False, False, False]
    assert len(calls) == 3 and m.experience_disabled
    assert all('Selected historical memory' not in messages[-1]['content'] for messages in calls)
    assert result['stop_reason'] == 'online_allowance_spent'
    assert any(k == 'memory_fallback' for k, _ in events)


def test_legacy_policy_stays_advice_only(monkeypatch):
    m = object.__new__(M.Memory); m.arm = 'TM0'; m.native_failures = 0
    m.diagnose = lambda: pytest.fail('old policy must not gain execution')
    assert m.native_action() is None


def gated_memory(tmp_path, monkeypatch):
    import t2_tehm_execution_policy as P
    _, args = example()
    (tmp_path / 'constraints').mkdir()
    (tmp_path / 'constraints/config.mk').write_text('export CORE_UTILIZATION = 20\n')
    manifest = {'initial_config': {'CORE_UTILIZATION': '20'},
                'allowlist': {'CORE_UTILIZATION': {'type': 'number', 'min': 5, 'max': 90}}}
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    m = object.__new__(M.Memory)
    m.H = SimpleNamespace(SKILL=Path(__file__).parents[2] / 'r2g-skills/signoff-loop',
                          observation=lambda p: {'drc': 18})
    m.arm = 'TM0'; m.execution_policy = P.VERSION; m.project = tmp_path; m.root = tmp_path
    m.env = {'R2G_TASK_MANIFEST': str(tmp_path / 'manifest.json')}
    m.used = []; m.native_failures = 0; m.experience_disabled = False
    m.guidance_active = False; m.fallback_reason = None; m.local_selection = None
    def route(*a):
        m.route_state = {'knowledge_route': 'NO_SKILL'}
        return '', []
    m.route_knowledge = route
    m.current_situation = lambda: args['situation']
    applied = []
    def diagnose(apply=None):
        if not apply: return args['plan']
        applied.append(apply)
        return {'status': 'applied', 'applied': apply,
                'config_edits': args['plan']['strategies'][0]['config_edits']}
    m.diagnose = diagnose
    monkeypatch.setattr(P, 'load_evidence', lambda *a: (args['units'], args['index'], {'target'}, {'index_sha256': 'frozen'}))
    events = []
    monkeypatch.setattr(M.T, 'event', lambda h,p,k,v: events.append((k,v)))
    return m, args, applied, events


def test_consumer_reuses_verified_action_and_rechecks_before_apply(tmp_path, monkeypatch):
    m, args, applied, events = gated_memory(tmp_path, monkeypatch)
    selected = m.native_action()
    assert selected['id'] == 'arbitrary-tool' and applied == []
    assert m.apply_native(selected)[0] == 'floorplan'
    assert applied == ['arbitrary-tool']
    m.native_outcome(selected, {'rolled_back': False, 'flow_launched': True,
                             'status': 'RETAINED', 'verdict': {'accepted': True},
                             'journal_ref': {'action_id': 'test#1'}})
    assert not m.experience_disabled
    assert m.native_action() is None
    receipt = next(v for k,v in events if k == 'memory_procedure_consumption')
    assert receipt['model_requests'] == 0 and receipt['journal_ref']['action_id'] == 'test#1'
    assert receipt['selection']['selection_sha256']


@pytest.mark.parametrize('change', ['config', 'evidence', 'candidate', 'veto'])
def test_selection_cannot_be_rebound_to_changed_execution(tmp_path, monkeypatch, change):
    m, args, applied, events = gated_memory(tmp_path, monkeypatch)
    selected = copy.deepcopy(m.native_action())
    if change == 'config': (tmp_path / 'constraints/config.mk').write_text('export CORE_UTILIZATION = 25\n')
    elif change == 'evidence': args['units'][0]['raw_row']['rolled_back'] = True
    elif change == 'candidate': args['plan']['strategies'][0]['config_edits']['CORE_UTILIZATION'] = '16'
    else:
        def route(*a):
            m.route_state = {'knowledge_route': 'INAPPLICABLE'}
            return '', []
        m.route_knowledge = route
    with pytest.raises(M.IntegrityStop, match='changed'):
        m.apply_native(selected)
    assert applied == []


def test_task_contract_can_refuse_local_reuse_without_blocking_the_agent(tmp_path, monkeypatch):
    m, args, applied, events = gated_memory(tmp_path, monkeypatch)
    manifest = json.loads((tmp_path / 'manifest.json').read_text())
    manifest['allowlist']['CORE_UTILIZATION']['min'] = 18
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    assert m.native_action() is None and not m.experience_disabled and applied == []
    assert events[-1][1]['reason'] == 'current_task_contract_rejects_local_action'


def test_invalid_candidate_is_technical_not_no_experience(tmp_path, monkeypatch):
    m, args, applied, events = gated_memory(tmp_path, monkeypatch)
    args['plan']['strategies'][0].pop('recheck')
    with pytest.raises(M.IntegrityStop, match='contract'):
        m.native_action()


def test_changed_or_missing_proof_is_technical_not_online_fallback(tmp_path, monkeypatch):
    import t2_tehm_execution_policy as P
    m, args, applied, events = gated_memory(tmp_path, monkeypatch)
    def invalid(*a): raise ValueError('Independent verification failed')
    monkeypatch.setattr(P, 'load_evidence', invalid)
    with pytest.raises(M.IntegrityStop, match='evidence integrity'):
        m.native_action()


def test_direct_reuse_can_finish_with_zero_online_calls(tmp_path, monkeypatch):
    h, env, calls, events = fake_runtime(tmp_path, monkeypatch, ['invalid'])
    m = object.__new__(M.Memory)
    m.H = h; m.project = tmp_path; m.arm = 'TM0'; m.execution_policy = M.TM0_THREE_WAY_POLICY
    m.used = []; m.native_failures = 0; m.experience_disabled = False; m.guidance_active = False
    m.local_selection = {'selection_sha256': 'selection'}
    action = {'id': 'data-derived-procedure'}
    m.native_action = lambda: action
    def execute(*a, native_apply=None, **kw):
        assert native_apply is not None and a[5] == {}
        assert kw['require_full_acceptance'] is True
        return {'status': 'RETAINED', 'rolled_back': False, 'flow_launched': True,
                'verdict': {'accepted': True}}
    monkeypatch.setattr(R, 'execute_candidate', execute)
    result = R.run(h, tmp_path, 'TM0', env, 'unused', {'accepted': False}, lambda p: None, m)
    assert result['last']['accepted'] and calls == [] and len(result['decisions']) == 1


@pytest.mark.parametrize('reason', ['RISK', 'STATE_SHIFT', 'UNRECOGNISED_REASON'])
def test_clean_slate_risk_or_state_shift_is_not_overridden_by_procedural_support(tmp_path, monkeypatch, reason):
    m, args, applied, events = gated_memory(tmp_path, monkeypatch)
    def route(*a):
        m.route_state = {'knowledge_route': 'NO_SKILL', 'no_skill_reason': reason}
        return '', []
    m.route_knowledge = route
    m.diagnose = lambda *a: pytest.fail('do not bypass clean-slate risk/state-shift decision')
    assert m.native_action() is None
    assert m.adaptive_context('query', args['situation']) == ('', [])


def test_real_ledger_counts_three_fallback_requests_after_one_local_trial(ep, monkeypatch):
    import os
    from test_repair_turns import H, OK, _Resp, _spent
    from test_t2a_runtime import manifest
    import t2a_runtime as T
    import t2_agent_dispatch as D
    p, calls, _ = ep; env = manifest(p)
    (p / 'backend').mkdir(); (p / 'shared').mkdir()
    (p / 'shared/manifest.json').write_text(json.dumps({'units_sha256': 'same-history'}))
    Path(os.environ['R2G_BUDGET_FILE']).write_text(json.dumps(D.episode_budget()))
    monkeypatch.setattr(T, 'recover_execution', lambda *a: None)
    monkeypatch.setattr(H, 'observation', lambda p: {'drc': 18})
    monkeypatch.setattr(R, 'PublicTools', lambda *a: SimpleNamespace(discover=lambda: [], offered={}))
    executions = []
    def execute(*a, **kw):
        executions.append(kw.get('native_apply') is not None)
        return {'status': 'FAILED', 'rolled_back': True, 'flow_launched': True,
                'target_progress': {'improved': False}, 'verdict': {'accepted': False}}
    monkeypatch.setattr(R, 'execute_candidate', execute)
    def transport(request, timeout=0):
        body = json.loads(request.data); calls.append(body)
        content = json.dumps({'operation': 'repair', 'arguments': {
            'set': {'CORE_UTILIZATION': '23'}, 'rerun_from': 'floorplan'}})
        return _Resp(json.dumps({**OK, 'choices': [{'message': {'content': content}}]}).encode())
    monkeypatch.setattr(H.urllib.request, 'urlopen', transport)
    m = object.__new__(M.Memory)
    m.H = H; m.project = p; m.root = p; m.arm = 'TM0'; m.execution_policy = M.TM0_THREE_WAY_POLICY
    m.helpers = 0; m.used = []; m.native_failures = 0; m.receipt = None
    m.experience_disabled = False; m.guidance_active = False; m.fallback_reason = None
    m.inner = SimpleNamespace(last_receipt={'decision': {'decision': 'NO_SKILL'}})
    m.current_situation = lambda: {}
    m.native_action = lambda: None if m.experience_disabled else {'id': 'verified-trial'}
    result = R.run(H, p, 'TM0', env, 'unused', {'accepted': False}, lambda p: None, m)
    assert executions == [True, False, False, False]
    assert len(calls) == 3 and _spent(p, 'llm_requests') == 3
    assert _spent(p, 'tokens_in') == 150 and _spent(p, 'tokens_out') == 60
    assert result['stop_reason'] == 'online_allowance_spent'
    events = [json.loads(l) for l in (p / 'reports/resource_ledger.jsonl').read_text().splitlines()]
    assert sum(e['type'] == 'memory_procedure_consumption' for e in events) == 1
    assert sum(e['type'] == 'memory_consumption' for e in events) == 3
    assert all(e['mode'] == 'no_experience' for e in events if e['type'] == 'memory_agent_context')


@pytest.mark.parametrize('local_trial,expected_rollback', [(False, False), (True, True)])
def test_unaccepted_local_procedure_restores_entry_even_without_a_vector_regression(tmp_path, monkeypatch,
                                                                                  local_trial, expected_rollback):
    (tmp_path / 'constraints').mkdir(); (tmp_path / 'constraints/config.mk').write_text('')
    restores = []
    monkeypatch.setitem(sys.modules, 'result_vector', SimpleNamespace(
        capture=lambda p: {}, compare=lambda *a: {'unknowns': [], 'regressions': []}))
    monkeypatch.setattr(R.T, 'snapshot_state', lambda *a, **kw: tmp_path / 'snapshot')
    monkeypatch.setattr(R.J, 'begin', lambda *a: {'action_id': 'test#1'})
    monkeypatch.setattr(R.J, 'finish', lambda *a: None)
    monkeypatch.setattr(R.T, 'candidate_execution_identity', lambda *a: None)
    monkeypatch.setattr(R.T, 'restore', lambda *a, **kw: restores.append(kw))
    monkeypatch.setattr(R.E, 'check_config', lambda *a, **kw: {'ok': True})
    h = SimpleNamespace(SKILL=tmp_path, FLOW=tmp_path, CFG={'platform': 'sky130hd'},
        _task_failure=lambda p: ('drc', 'm3.2', 5), run=lambda *a: 0,
        apply_llm=lambda *a, **kw: ('place', {}),
        final_verdict=lambda *a: {'accepted': False, 'acceptance': {}, 'envelope': {'status': 'PASS'}})
    row = R.execute_candidate(h, tmp_path, {}, {}, '{}', {}, 1, {},
                              require_full_acceptance=local_trial)
    assert row['rolled_back'] is expected_rollback and bool(restores) is expected_rollback


def test_immutable_input_corruption_is_not_treated_as_missing_experience(tmp_path, monkeypatch):
    m, args, applied, events = gated_memory(tmp_path, monkeypatch)
    manifest = json.loads((tmp_path / 'manifest.json').read_text())
    manifest['immutable'] = {'files': {'constraints/constraint.sdc': 'sha256:original'}}
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(M.IntegrityStop, match='immutable'):
        m.native_action()
