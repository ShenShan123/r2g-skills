"""Shared-history collection contract, queue, reality check and accounting (user scope
2026-10-17). Nothing here launches a flow: the module plans and gates.
"""
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'memory' / 'scripts'))
sys.path.insert(0, str(ROOT / 'memory'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import t2_history_collection as HC  # noqa: E402
import test_ann_v2_fields as FX  # noqa: E402

CONTRACT = ROOT / 'memory' / 'evaluation' / 't2_history_collection_contract.json'


def contract():
    return json.loads(CONTRACT.read_text())


def test_the_proposed_contract_is_valid_and_its_worst_case_fits_the_flow_cap():
    c = contract()
    assert HC.validate(c) == []
    w = HC.worst_case(c)
    assert w['total_max'] <= c['caps']['flows_total'] == 80
    assert c['caps']['repair_episodes'] == 12 and len(c['designs']) <= 6
    assert set(d['cluster'] for d in c['designs']) <= set(c['split']['learn'])


@pytest.mark.parametrize('mutate,needle', [
    (lambda c: c['designs'].append({'design': 'aes_core', 'cluster': 'secworks/aes'}), 'not a learning cluster'),
    (lambda c: c['designs'].append({'design': 'gcd', 'cluster': 'opencores/des'}), 'excluded design'),
    (lambda c: c.__setitem__('ladder', ['default', 'u85', 'u90']), 'ladder'),
    (lambda c: c['repair'].__setitem__('memory_backend', 'legacy'), 'memory_backend none'),
    (lambda c: c['repair'].__setitem__('llm', True), 'memory_backend none'),
    (lambda c: c['caps'].__setitem__('llm_requests', 3), 'llm_requests'),
    (lambda c: c['designs'].append({'design': 'x', 'cluster': 'opencores/i2c'}), 'worst case'),
    (lambda c: c.__setitem__('no_automatic_expansion', False), 'no_automatic_expansion'),
    (lambda c: c['split']['learn'].append('secworks/aes'), 'both learn and verify')])
def test_contract_violations_are_reported(mutate, needle):
    c = contract()
    mutate(c)
    assert any(needle in e for e in HC.validate(c)), HC.validate(c)


def run_queue(c, outcomes, flows_per_repair=6, chain_ok=True):
    """Drive next_action with scripted exploration outcomes {(design, step): outcome}."""
    state = {'flows_spent': 0, 'records': [], 'suspended': None, 'chain_checked': False}
    trace = []
    for _ in range(200):
        a = HC.next_action(c, state)
        trace.append(a)
        if a['op'] == 'stop':
            return trace, state
        if a['op'] == 'explore':
            o = outcomes.get((a['design'], a['step']), 'clean')
            state['records'].append({'op': 'explore', 'design': a['design'], 'step': a['step'],
                                     'outcome': o, 'target': 'route_abort_GRT-0116' if o == 'target' else None})
            state['flows_spent'] += 1
        elif a['op'] == 'repair':
            state['records'].append({'op': 'repair', 'design': a['design'], 'step': a['step']})
            state['flows_spent'] += min(flows_per_repair, a['flows_cap'])
        elif a['op'] == 'check_chain':
            state['chain_checked'] = True
            if not chain_ok:
                state['suspended'] = 'reality check failed'
    raise AssertionError('queue did not stop')


def test_the_queue_repairs_a_target_then_checks_the_chain_before_anything_else():
    c = contract()
    trace, state = run_queue(c, {('picorv32', 'default'): 'target'})
    ops = [(a['op'], a.get('design'), a.get('step')) for a in trace[:4]]
    assert ops == [('explore', 'picorv32', 'default'), ('repair', 'picorv32', 'default'),
                   ('check_chain', 'picorv32', 'default'), ('explore', 'picorv32', 'u80')]
    explored = [r for r in state['records'] if r['op'] == 'explore']
    assert len(explored) == 15                                  # every ladder run kept and billed
    assert trace[-1] == {'op': 'stop', 'reason': 'queue complete'}


def test_a_failed_reality_check_suspends_the_queue():
    trace, state = run_queue(contract(), {('picorv32', 'default'): 'target'}, chain_ok=False)
    assert trace[-1]['op'] == 'stop' and 'suspended' in trace[-1]['reason']
    assert [r['op'] for r in state['records']] == ['explore', 'repair']


def test_continuation_gates_the_first_new_episode_without_replaying_old_episodes():
    old = [{'op': 'explore', 'design': 'picorv32', 'step': 'default', 'outcome': 'clean'},
           {'op': 'explore', 'design': 'picorv32', 'step': 'u80', 'outcome': 'target'},
           {'op': 'repair', 'design': 'picorv32', 'step': 'u80'}]
    state = {'records': old, 'flows_spent': 4, 'chain_checked': False, 'chain_gate_record_offset': 3}
    a = HC.next_action(contract(), state)
    assert a['op'] == 'explore' and a['step'] == 'u90'
    state['records'].extend([{'op': 'explore', 'design': 'picorv32', 'step': 'u90', 'outcome': 'target'},
                             {'op': 'repair', 'design': 'picorv32', 'step': 'u90'}])
    assert HC.next_action(contract(), state) == {'op': 'check_chain', 'design': 'picorv32', 'step': 'u90'}


def test_continuation_does_not_reset_the_total_flow_or_episode_cap():
    records = [{'op': 'explore', 'design': 'picorv32', 'step': 'default', 'outcome': 'target'}]
    assert HC.next_action(contract(), {'records': records, 'flows_spent': 80})['op'] == 'stop'
    records += [{'op': 'repair', 'design': 'other', 'step': str(i)} for i in range(12)]
    a = HC.next_action(contract(), {'records': records, 'flows_spent': 4, 'chain_checked': True})
    assert a['op'] == 'stop' and 'episode cap' in a['reason']


def test_two_failures_end_a_designs_ladder_and_non_targets_get_no_repair():
    c = contract()
    trace, state = run_queue(c, {('picorv32', 'default'): 'non_target', ('picorv32', 'u80'): 'target'})
    mine = [r for r in state['records'] if r['design'] == 'picorv32']
    assert [(r['op'], r['step']) for r in mine] == [('explore', 'default'), ('explore', 'u80'), ('repair', 'u80')]


def test_an_infrastructure_outcome_ends_that_design_only():
    trace, state = run_queue(contract(), {('picorv32', 'default'): 'infra'})
    assert [r['step'] for r in state['records'] if r['design'] == 'picorv32'] == ['default']
    assert any(r['design'] == 'des_area' for r in state['records'])


def test_caps_end_the_queue_without_expansion():
    c = contract()
    allt = {(d['design'], s): 'target' for d in c['designs'] for s in HC.LADDER}
    trace, state = run_queue(c, allt)
    assert sum(r['op'] == 'repair' for r in state['records']) == 10   # 2 per design x 5
    assert state['flows_spent'] <= 80
    c2 = copy.deepcopy(c)
    c2['caps']['flows_total'] = 20
    trace, state = run_queue(c2, allt)
    assert trace[-1]['reason'].startswith('flow cap reached') and state['flows_spent'] <= 20
    c3 = copy.deepcopy(c)
    c3['caps']['repair_episodes'] = 1
    trace, _ = run_queue(c3, allt)
    assert trace[-1]['reason'].startswith('repair episode cap reached')


def meas(stage=None, code=None, drv=False, setup=False):
    f = lambda v: {'value': v, 'status': 'KNOWN'}  # noqa: E731
    st = {'measured': True, 'present': stage is not None,
          'fields': {'stage': f(stage), 'error_code': f(code)} if stage else {}}
    m = {k: {'measured': True, 'present': False} for k in ('route_residual', 'lvs', 'setup', 'hold', 'drv', 'drc')}
    m['stage'] = st
    m['drv']['present'] = drv
    m['setup']['present'] = setup
    return m


@pytest.mark.parametrize('m,cats,want', [
    (meas('route', 'GRT-0116'), None, ('target', 'route_abort_GRT-0116')),
    (meas('place', 'FLW-0024'), None, ('target', 'place_abort_FLW-0024')),
    (meas('place', 'PPL-0024'), None, ('non_target', None)),
    (meas('synth', None), None, ('infra', None)),
    (meas(), {'li.3': {'count': 4}}, ('target', 'drc_li.3')),
    (meas(), {'m3.2': 2}, ('target', 'drc_m3.2')),
    (meas(drv=True), {}, ('target', 'drv')),
    (meas(setup=True), {}, ('non_target', None)),
    (meas(), {'met1.1': 3}, ('non_target', None)),
    (meas(), {}, ('clean', None))])
def test_exploration_outcomes(m, cats, want):
    r = HC.classify(m, cats)
    assert (r['outcome'], r.get('target')) == want


def chain_project(tmp_path, *, post='RUN_B', action_ok=True, graded=True, marker=True):
    p = tmp_path / 'picorv32__u80'
    FX.mkrun(p, 'RUN_A', drc=4, timing=FX.T())
    d = FX.mkrun(p, 'RUN_B', [('floorplan', 0), ('place', 0), ('cts', 0), ('route', 0), ('finish', 0)],
                 drc=0, timing=FX.T())
    (d / 'r2g_action.json').write_text(json.dumps({'action_id': 's1#1' if action_ok else 'x#9', 'run_tag': 'RUN_B'}))
    row = {'fix_session_id': 's1', 'iter': 1, 'strategy': 'density_relief', 'from_stage': 'floorplan',
           'check': 'drc', 'violation_class': 'li.3', 'verdict': 'cleared', 'config_before': {},
           'config_delta': '{}', 'before_categories': {'li.3': {'count': 4}}, 'ts': '2026-10-18T00:00:00-07:00',
           'chain_version': 1, 'action_id': 's1#1', 'parent_action': None, 'pre_run_tag': 'RUN_A',
           'post_run_tag': post, 'step_check': {'check': 'drc', 'graded_post_run': graded}}
    if marker:
        row.update(rolled_back=False, rollback_reason=None)
    (p / 'reports').mkdir(parents=True)
    (p / 'reports' / 'fix_log.jsonl').write_text(json.dumps(row) + '\n')
    return p


@pytest.mark.parametrize('kw,needle', [
    (dict(post=None), 'post_run_tag missing'), (dict(action_ok=False), 'names another action'),
    (dict(graded=False), 'graded a run other'), (dict(marker=False), 'rolled_back marker missing')])
def test_raw_chain_problems_are_found(tmp_path, kw, needle):
    assert any(needle in x for x in HC.chain_problems(chain_project(tmp_path, **kw)))


def fake_source_v1(tmp_path):
    s = tmp_path / 'SOURCE-v1'
    s.mkdir()
    (s / 'source_export.jsonl').write_text('')
    (s / 'source_manifest.json').write_text(json.dumps({'split': {'family_of_design': {'picorv32': 'yosyshq/picorv32'}}}))
    lock = tmp_path / 'lock.json'
    lock.write_text(json.dumps({'entries': []}))
    return s, lock


def test_the_reality_check_passes_a_complete_chain_and_fails_a_broken_one(tmp_path):
    s, lock = fake_source_v1(tmp_path)
    good = chain_project(tmp_path / 'g')
    r = HC.reality_check(good, s, lock, tmp_path / 'out-g')
    assert r == {'ok': True, 'problems': [], 'verifier_gate': 'PASS'}
    bad = chain_project(tmp_path / 'b', action_ok=False)
    r = HC.reality_check(bad, s, lock, tmp_path / 'out-b')
    assert r['ok'] is False and any('names another action' in x for x in r['problems'])
    assert any('post run not bound' in x for x in r['problems'])


def test_accounting_separates_exploration_repair_and_final_and_keeps_unknown(tmp_path):
    led = tmp_path / 'l.jsonl'
    ev = [{'type': 'budget_charge', 'resource': 'flows', 'amount': 1, 'component': 'initial'},
          {'type': 'budget_charge', 'resource': 'flows', 'amount': 1, 'component': 'catalogue'},
          {'type': 'budget_charge', 'resource': 'flows', 'amount': 1, 'component': 'memory'},
          {'type': 'budget_charge', 'resource': 'flows', 'amount': 1, 'component': 'final_acceptance'},
          {'type': 'budget_charge', 'resource': 'flows', 'amount': 'UNKNOWN', 'component': 'catalogue'},
          {'type': 'budget_charge', 'resource': 'wall_s', 'amount': 99, 'component': 'initial'}]
    led.write_text(''.join(json.dumps(e) + '\n' for e in ev))
    a = HC.account([led])
    assert (a['exploration'], a['repair'], a['final'], a['unknown_amounts'], a['total_known']) == (1, 2, 1, 1, 4)


def test_the_dev_verification_view_drops_every_verify_cluster_unit():
    def rec(fam, grp):
        return {'identity': {'ip_family': {'value': fam}, 'source_group': {'value': grp}}}
    recs = [rec('secworks/chacha', 'secworks'), rec('ultraembedded/riscv', 'ultraembedded/riscv'),
            rec('opencores/des', 'opencores/des'), rec(None, None)]
    split = contract()['split']
    view = HC.history_view(recs, split, 'dev_verification')
    assert [r['identity']['ip_family']['value'] for r in view] == ['opencores/des']
    assert len(HC.history_view(recs, split, 't2')) == 4
