"""TEHM-v2 policy layer (memory/tehm.policy/policy.py): typed matching, applicability, evidence
ranking, repeat control -- each switchable, every decision explained. Offline only."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'memory'))
from tehm.policy import evidence_index as EI  # noqa: E402
from tehm.policy import policy as P  # noqa: E402

ALL = {m: True for m in P.MECHANISMS}


def mem(id_, edits):
    return {'id': id_, 'source': 'tehm_rule', 'config_edits': edits}


def cat(id_, edits):
    return {'id': id_, 'source': 'catalogue', 'config_edits': edits}


def entry(sig, knob, d, outcome, grp, task, fam=None):
    return {'unit_id': f'{grp}:{task}:{knob}:{outcome}', 'signature': sig, 'signature_type': sig.split(':')[0],
            'knob': knob, 'direction': d, 'outcome': outcome, 'source_group': grp, 'ip_family': fam or grp,
            'task': task}


def decide(strats, state, index=None, episode=(), flags=ALL, exclude=frozenset()):
    return P.decide(strats, state, index or {'entries': []}, list(episode), flags, exclude)


def ids(out):
    return [s['id'] for s in out[0]]


def reasons(out):
    return {d['id']: (d['mechanism'], d['reason']) for d in out[1] if d['decision'] == 'rejected'}


SETUP = {'failures': ['setup'], 'signature': 'setup', 'cfg': {'CORE_UTILIZATION': '40'}}
DRC = {'failures': ['drc:li.3'], 'signature': 'drc:li.3', 'cfg': {'CORE_UTILIZATION': '40'}}
GRT = {'failures': ['route:GRT-0116'], 'signature': 'route:GRT-0116', 'cfg': {'CORE_UTILIZATION': '77'}}


# -- typed matching ---------------------------------------------------------------------

def test_a_drv_margin_rule_is_rejected_on_a_setup_only_failure():
    out = decide([mem('drv_margin', {'R2G_ROUTE_SLEW_MARGIN': '10'})], SETUP)
    assert ids(out) == [] and reasons(out)['drv_margin'][0] == 'typed_matching'


def test_the_same_rule_runs_on_a_drv_failure():
    out = decide([mem('drv_margin', {'R2G_ROUTE_SLEW_MARGIN': '10'})], {**SETUP, 'failures': ['drv']})
    assert ids(out) == ['drv_margin']


def test_typed_matching_off_keeps_the_mismatch():
    out = decide([mem('drv_margin', {'R2G_ROUTE_SLEW_MARGIN': '10'})], SETUP, flags={**ALL, 'typed_matching': False})
    assert ids(out) == ['drv_margin']


def test_catalogue_strategies_are_never_rejected_by_typing():
    out = decide([cat('drv_cat', {'R2G_ROUTE_SLEW_MARGIN': '10'})], SETUP)
    assert ids(out) == ['drv_cat']


# -- applicability -------------------------------------------------------------------------

@pytest.mark.parametrize('edits,needle', [
    ({'CORE_UTILIZATION': '40'}, 'no-op'),
    ({'CORE_UTILIZATION': '5'}, 'outside'),
    ({}, 'no edits')])
def test_inapplicable_memory_edits_are_rejected_before_execution(edits, needle):
    out = decide([mem('m', edits)], DRC)
    assert ids(out) == [] and needle in reasons(out)['m'][1] and reasons(out)['m'][0] == 'applicability'


def test_an_applicable_edit_runs_in_either_direction():
    # no knob direction is assumed: history shows utilisation moves in both directions clearing li.3
    assert ids(decide([mem('m', {'CORE_UTILIZATION': '30'})], DRC)) == ['m']
    assert ids(decide([mem('m', {'CORE_UTILIZATION': '55'})], DRC)) == ['m']


# -- evidence ranking ----------------------------------------------------------------------

def test_memory_is_ordered_by_independent_admitted_support_not_by_order():
    idx = {'entries': [entry('route:GRT-0116', 'PLACE_DENSITY', 'down', 'admitted_positive', 'g1', 't1'),
                       entry('route:GRT-0116', 'PLACE_DENSITY', 'down', 'admitted_positive', 'g2', 't2')]}
    out = decide([mem('util', {'CORE_UTILIZATION': '61'}), mem('dens', {'PLACE_DENSITY': '0.5'})],
                 {**GRT, 'cfg': {'CORE_UTILIZATION': '77', 'PLACE_DENSITY': '0.7'}}, idx)
    assert ids(out) == ['dens', 'util']


def test_repeated_records_of_one_task_count_once():
    idx = {'entries': [entry('route:GRT-0116', 'CORE_UTILIZATION', 'down', 'harm', 'g1', 't1')] * 3 +
                      [entry('route:GRT-0116', 'CORE_UTILIZATION', 'down', 'admitted_positive', 'g2', 't2')]}
    out = decide([mem('util', {'CORE_UTILIZATION': '61'})], GRT, idx)
    ev = [d for d in out[1] if d['id'] == 'util'][0]['evidence']
    assert (ev['support'], ev['harm']) == (1, 1)


def test_replicated_net_harm_defers_memory_after_the_catalogue():
    idx = {'entries': [entry('route:GRT-0116', 'CORE_UTILIZATION', 'down', 'harm', 'g1', 't1'),
                       entry('route:GRT-0116', 'CORE_UTILIZATION', 'down', 'harm', 'g2', 't2')]}
    out = decide([mem('util', {'CORE_UTILIZATION': '61'}), cat('relief', {'PLACE_DENSITY': '0.5'})],
                 {**GRT, 'cfg': {'CORE_UTILIZATION': '77', 'PLACE_DENSITY': '0.7'}}, idx)
    assert ids(out) == ['relief', 'util']


def test_harm_from_one_source_group_never_defers():
    idx = {'entries': [entry('route:GRT-0116', 'CORE_UTILIZATION', 'down', 'harm', 'g1', t) for t in ('t1', 't2')]}
    out = decide([mem('util', {'CORE_UTILIZATION': '61'}), cat('c', {'PLACE_DENSITY': '0.5'})],
                 {**GRT, 'cfg': {'CORE_UTILIZATION': '77', 'PLACE_DENSITY': '0.7'}}, idx)
    assert ids(out) == ['util', 'c']


def test_harm_outweighed_by_partial_progress_never_defers():
    idx = {'entries': [entry('route:GRT-0116', 'CORE_UTILIZATION', 'down', 'harm', g, 'th' + g) for g in ('g1', 'g2')] +
                      [entry('route:GRT-0116', 'CORE_UTILIZATION', 'down', 'improved', g, 'ti' + g) for g in ('g1', 'g2', 'g3')]}
    out = decide([mem('util', {'CORE_UTILIZATION': '61'}), cat('c', {'PLACE_DENSITY': '0.5'})],
                 {**GRT, 'cfg': {'CORE_UTILIZATION': '77', 'PLACE_DENSITY': '0.7'}}, idx)
    assert ids(out) == ['util', 'c']


def test_a_harmful_catalogue_move_is_deferred_never_removed():
    idx = {'entries': [entry('drc:li.3', 'CELL_PAD_IN_SITES_GLOBAL_PLACEMENT', 'set', 'harm', g, 't' + g)
                       for g in ('g1', 'g2')]}
    out = decide([cat('pad', {'CELL_PAD_IN_SITES_GLOBAL_PLACEMENT': '2'}), cat('dens', {'CORE_UTILIZATION': '30'})],
                 DRC, idx)
    assert ids(out) == ['dens', 'pad']


def test_single_step_unconfirmed_clears_never_rank():
    idx = {'entries': [entry('route:GRT-0116', 'CORE_UTILIZATION', 'down', 'cleared_unconfirmed', 'g1', 't1')]}
    ev = [d for d in decide([mem('util', {'CORE_UTILIZATION': '61'})], GRT, idx)[1]][0]['evidence']
    assert ev['support'] == 0 and ev['unconfirmed'] == 1


def test_the_tasks_own_ip_family_is_never_evidence():
    idx = {'entries': [entry('route:GRT-0116', 'CORE_UTILIZATION', 'down', 'harm', g, 't' + g, fam='own/ip')
                       for g in ('g1', 'g2')]}
    out = decide([mem('util', {'CORE_UTILIZATION': '61'}), cat('c', {'PLACE_DENSITY': '0.5'})],
                 {**GRT, 'cfg': {'CORE_UTILIZATION': '77', 'PLACE_DENSITY': '0.7'}}, idx, exclude={'own/ip'})
    assert ids(out) == ['util', 'c']


def test_ranking_off_keeps_the_given_order():
    idx = {'entries': [entry('route:GRT-0116', 'CORE_UTILIZATION', 'down', 'harm', g, 't' + g) for g in ('g1', 'g2')]}
    out = decide([mem('util', {'CORE_UTILIZATION': '61'}), cat('c', {'PLACE_DENSITY': '0.5'})],
                 {**GRT, 'cfg': {'CORE_UTILIZATION': '77', 'PLACE_DENSITY': '0.7'}}, idx,
                 flags={**ALL, 'evidence_ranking': False})
    assert ids(out) == ['util', 'c']


# -- repeat control ----------------------------------------------------------------------

def ep(edits, verdict, before, rolled_back=True):
    return {'action_key': P.action_key(edits), 'moves': P.moves(edits, before), 'verdict': verdict,
            'rolled_back': rolled_back}


def test_an_identical_edit_is_never_repeated_in_an_episode():
    out = decide([cat('c', {'CORE_UTILIZATION': '30'})], DRC,
                 episode=[ep({'CORE_UTILIZATION': '30'}, 'no_improvement', {'CORE_UTILIZATION': '40'})])
    assert ids(out) == [] and reasons(out)['c'][0] == 'repeat_control'


def test_after_no_improvement_a_different_magnitude_may_run():
    out = decide([cat('c', {'CORE_UTILIZATION': '25'})], DRC,
                 episode=[ep({'CORE_UTILIZATION': '30'}, 'no_improvement', {'CORE_UTILIZATION': '40'})])
    assert ids(out) == ['c']


def test_after_a_regression_the_same_push_further_is_blocked():
    out = decide([mem('m', {'CORE_UTILIZATION': '25'}), cat('c', {'CORE_UTILIZATION': '35'})], DRC,
                 episode=[ep({'CORE_UTILIZATION': '30'}, 'regression', {'CORE_UTILIZATION': '40'})])
    assert ids(out) == ['c'] and 'regressed' in reasons(out)['m'][1]


def test_an_unrolled_regression_stops_every_memory_candidate():
    out = decide([mem('m', {'CORE_UTILIZATION': '35'}), cat('c', {'CORE_UTILIZATION': '36'})], DRC,
                 episode=[ep({'PLACE_DENSITY': '0.5'}, 'regression', {'PLACE_DENSITY': '0.7'}, rolled_back=None)])
    assert ids(out) == ['c'] and 'not rolled back' in reasons(out)['m'][1]


def test_every_decision_is_recorded():
    out = decide([mem('a', {'R2G_ROUTE_SLEW_MARGIN': '10'}), cat('b', {'CORE_UTILIZATION': '30'})], SETUP)
    assert {d['id']: d['decision'] for d in out[1]} == {'a': 'rejected', 'b': 'kept'}


# -- evidence index rows ------------------------------------------------------------------

def test_recorder_rows_and_unset_knobs_yield_moves():
    assert EI.row_moves({'config_delta': '{"CORE_UTILIZATION": {"before": "89", "after": "53"}}'}) == \
        {'CORE_UTILIZATION': (89.0, 53.0, 'down')}
    assert EI.row_moves({'config_before': {}, 'config_delta': '{"R2G_ROUTE_SLEW_MARGIN": "10"}'}) == \
        {'R2G_ROUTE_SLEW_MARGIN': (None, 10.0, 'set')}
    assert EI.row_moves({'config_before': {'CORE_UTILIZATION': '84'}, 'config_delta': '{}',
                         'cumulative_config': '{"CORE_UTILIZATION": "76"}'}) == {'CORE_UTILIZATION': (84.0, 76.0, 'down')}


@pytest.mark.parametrize('rec,out', [
    ({'admission': {'status': 'ADMITTED'}, 'step_effect': {'stage_abort': 'CLEARED'}}, 'admitted_positive'),
    ({'admission': {'status': 'NOT_ADMITTED'}, 'candidate_class': 'effect_cleared_rollback_unknown',
      'step_effect': {'drc': 'CLEARED'}}, 'cleared_unconfirmed'),
    ({'admission': {}, 'step_effect': {'stage_abort': 'REGRESSED'}}, 'harm'),
    ({'admission': {}, 'step_effect': {'stage_abort': 'CLEARED'}, 'new_failures': {'stage': 'KNOWN-present'}},
     'cleared_new_failure'),
    ({'admission': {}, 'step_effect': {'drc': 'IMPROVED'}}, 'improved'),
    ({'admission': {}, 'step_effect': {'drc': 'NO_CHANGE'}}, 'no_change')])
def test_outcome_classes(rec, out):
    assert EI.outcome_of(rec) == out


# -- rejection attribution (DEV) -----------------------------------------------------------

def test_typed_rejections_are_paired_with_the_ablation_arm_from_the_same_pre_state():
    from tehm.policy.attribution import pre_state, rejection_ledger
    e = {'R2G_ROUTE_SLEW_MARGIN': '10'}
    s_gcd = pre_state('setup', -1.73, {'CORE_UTILIZATION': '40'}, e)
    s_ibex = pre_state('setup', -0.001, {'CORE_UTILIZATION': '80'}, e)
    log = [{'task': 'gcd', 'id': 'm1', 'edits': e, 'pre_state': s_gcd, 'decision': 'rejected', 'mechanism': 'typed_matching'},
           {'task': 'ibex', 'id': 'm1', 'edits': e, 'pre_state': s_ibex, 'decision': 'rejected', 'mechanism': 'typed_matching'},
           {'task': 'x', 'id': 'm2', 'edits': {'CORE_UTILIZATION': '30'}, 'pre_state': 'p', 'decision': 'rejected',
            'mechanism': 'typed_matching'},
           {'task': 'gcd', 'id': 'c', 'edits': {}, 'decision': 'kept', 'mechanism': None}]
    abl = [{'task': 'gcd', 'strategy': 's', 'edits': e, 'pre_state': s_gcd, 'verdict': 'no_improvement'},
           {'task': 'ibex', 'strategy': 's', 'edits': e, 'pre_state': s_ibex, 'verdict': 'cleared'}]
    r = rejection_ledger(log, abl)
    assert (r['rejected'], r['avoided_waste'], r['wrongly_rejected'], r['unmatched']) == (3, 1, 1, 1)


@pytest.mark.parametrize('ablation_state', [
    ('setup', -1.73, {'CORE_UTILIZATION': '35'}),      # same edit, different starting config
    ('setup', -0.90, {'CORE_UTILIZATION': '40'}),      # same config, different failure metric
    ('drv', -1.73, {'CORE_UTILIZATION': '40'})])       # different failure type
def test_a_different_pre_state_is_unmatched_never_attributed(ablation_state):
    from tehm.policy.attribution import pre_state, rejection_ledger
    e = {'R2G_ROUTE_SLEW_MARGIN': '10'}
    log = [{'task': 'gcd', 'id': 'm1', 'edits': e, 'pre_state': pre_state('setup', -1.73, {'CORE_UTILIZATION': '40'}, e),
            'decision': 'rejected', 'mechanism': 'typed_matching'}]
    abl = [{'task': 'gcd', 'strategy': 's', 'edits': e, 'pre_state': pre_state(*ablation_state, e), 'verdict': 'cleared'}]
    r = rejection_ledger(log, abl)
    assert (r['wrongly_rejected'], r['avoided_waste'], r['unmatched']) == (0, 0, 1)


def test_shared_index_uses_the_package_label_so_overrides_bind_tehm_too(tmp_path):
    """build_shared reads outcome/signature/binding from shared_label (package overrides included),
    identity from the label records; a tool no-op is an ineffective action (no_change); unbound or
    unclassified units are skipped and counted."""
    import json
    def unit(uid, outcome, sig='route:GRT-0116', pre='KNOWN', post='KNOWN'):
        return {'unit_id': uid, 'provenance': {'sources': [{'path': '/x/proj__u72/reports/fix_log.jsonl'}]},
                'raw_row': {'config_delta': json.dumps({'CORE_UTILIZATION': '52'}),
                            'config_before': {'CORE_UTILIZATION': '77'}},
                'shared_label': {'outcome_class': outcome, 'signature': sig, 'binding': {'pre': pre, 'post': post}}}
    labels = tmp_path / 'labels.jsonl'
    labels.write_text(''.join(json.dumps({'unit_id': u, 'identity': {'source_group': {'value': 'opencores/des'},
                                                                      'ip_family': {'value': 'opencores/des'}}}) + '\n'
                              for u in ('a', 'b', 'c', 'd', 'e')))
    idx = EI.build_shared([unit('a', 'admitted_positive'), unit('b', 'cleared_new_failure'),
                           unit('c', None), unit('d', 'harm', post='UNKNOWN'), unit('e', 'tool_no_op')],
                          [labels], 'dev_verification')
    assert [(e['unit_id'], e['outcome'], e['knob'], e['direction'], e['source_group']) for e in idx['entries']] == [
        ('a', 'admitted_positive', 'CORE_UTILIZATION', 'down', 'opencores/des'),
        ('b', 'cleared_new_failure', 'CORE_UTILIZATION', 'down', 'opencores/des'),
        ('e', 'no_change', 'CORE_UTILIZATION', 'down', 'opencores/des')]
    assert idx['skipped'] == {'untyped_or_unclassified': 1, 'not_bound': 1}


def test_an_unset_knob_is_measured_against_the_orfs_default_in_index_and_policy():
    """ROUTING_LAYER_ADJUSTMENT unset -> 0.10 and a recorded 0.5 -> 0.1 are the same 'down' move, so
    recorded evidence on one binds a candidate written as the other; GPL_ROUTABILITY_DRIVEN=1 is a
    no-op on a config that leaves it unset."""
    assert P.moves({'ROUTING_LAYER_ADJUSTMENT': '0.10'}, {})['ROUTING_LAYER_ADJUSTMENT'] == (0.5, 0.1, 'down')
    assert EI.row_moves({'config_delta': '{"ROUTING_LAYER_ADJUSTMENT": "0.1"}',
                         'config_before': {}})['ROUTING_LAYER_ADJUSTMENT'] == (0.5, 0.1, 'down')
    assert P.moves({'GPL_ROUTABILITY_DRIVEN': '1'}, {})['GPL_ROUTABILITY_DRIVEN'][2] == 'same'
    assert P.moves({'CORE_UTILIZATION': '57'}, {})['CORE_UTILIZATION'] == (None, 57.0, 'set')
    idx = {'entries': [entry('route:GRT-0116', 'ROUTING_LAYER_ADJUSTMENT', 'down', 'harm', g, 't' + g)
                       for g in ('g1', 'g2')]}
    out = decide([cat('layer', {'ROUTING_LAYER_ADJUSTMENT': '0.10'}), cat('relief', {'CORE_UTILIZATION': '57'})], GRT, idx)
    assert ids(out) == ['relief', 'layer']          # replicated net harm now binds the unset-knob candidate
