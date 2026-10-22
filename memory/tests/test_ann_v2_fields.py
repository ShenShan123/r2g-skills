"""ann-v2 typed fields, step effects, new failures and positive evidence (spec §2-§7).

Raw metrics with units, from BOUND runs; a missing auxiliary field never erases a type;
an unmeasured check is UNKNOWN, never "no failure"; a strategy name never types a failure.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'memory'))
from ann_v2 import annotate as AN  # noqa: E402
from ann_v2 import effects as EF  # noqa: E402
from ann_v2 import fields as FD  # noqa: E402

SHA = {s: (s[0] * 64) for s in ('synth', 'floorplan', 'place', 'cts', 'route', 'finish')}
FULL = [('synth', 0), ('floorplan', 0), ('place', 0), ('cts', 0), ('route', 0), ('finish', 0)]
FAMILIES = {'aes_core': 'secworks/aes', 'chacha_core': 'secworks/chacha', 'sha256_core': 'secworks/sha256',
            'picorv32': 'yosyshq/picorv32'}


def mkrun(project, tag, stages=FULL, *, reason=None, from_stage=None, reused=None, drc=None, checker='klayout_direct',
          lvs=None, timing=None, time_unit='1ns', error=None, route_rpt=None):
    d = Path(project) / 'backend' / tag
    (d / 'logs').mkdir(parents=True)
    (d / 'stage_log.jsonl').write_text(''.join(
        json.dumps({'stage': s, 'status': st, 'ts_start': i}) + '\n' for i, (s, st) in enumerate(stages)))
    (d / 'stage_artifact_manifest.jsonl').write_text(''.join(
        json.dumps({'stage': s, 'status': 0, 'run_tag': tag, 'sha256': SHA[s]}) + '\n' for s, st in stages if st == 0))
    (d / 'flow.log').write_text('ok\n' + (f'[ERROR {error}] something failed\n' if error else ''))
    if reason is not None:
        (d / 'resume_meta.json').write_text(json.dumps({'from_stage': from_stage, 'reason': reason, 'parent_lineage': {
            s: {'sha256': SHA[s], 'parent_run': p, 'verified': True} for s, p in (reused or {}).items()}}))
    if time_unit:
        (d / 'logs' / '2_1_floorplan.json').write_text(json.dumps({'run__flow__platform__time_units': time_unit}))
    if timing is not None:
        (d / 'logs' / '6_report.json').write_text(json.dumps(timing))
    if drc is not None:
        (d / 'drc').mkdir()
        (d / 'drc' / 'drc_result.json').write_text(json.dumps(
            {'status': 'clean' if drc == 0 else 'fail', 'violations': str(drc), 'checker': checker}))
    if lvs is not None:
        (d / 'lvs').mkdir()
        (d / 'lvs' / 'netgen_lvs_result.json').write_text(json.dumps(
            {'tool': 'netgen', 'status': lvs, 'mismatch_class': '' if lvs == 'clean' else 'netgen_topology'}))
    if route_rpt is not None:
        (d / 'reports_orfs').mkdir()
        (d / 'reports_orfs' / '5_route_drc.rpt').write_text(route_rpt)
    return d


def T(ws=0.5, tns=0.0, viol=0, hold=0.2, slew=0, cap=0, fanout=0, drop=()):
    t = {'finish__timing__setup__ws': ws, 'finish__timing__setup__tns': tns,
         'finish__timing__drv__setup_violation_count': viol, 'finish__timing__hold__ws': hold,
         'finish__timing__hold__tns': 0.0, 'finish__timing__drv__hold_violation_count': 0,
         'finish__timing__drv__max_slew': slew, 'finish__timing__drv__max_cap': cap,
         'finish__timing__drv__max_fanout': fanout}
    for k in drop:
        t.pop(k)
    return t


def why(strategy, stage):
    return f'signoff fix: strategy={strategy} rerun_from={stage} (config edit)'


def unit(project, *, uid='u1', design='aes_core', check='drc', vclass='li.3', strategy='antenna_diode_iters',
         from_stage='route', session='s1', it=1, rolled_back=False, situation=None, before_categories=None,
         extra_rows=()):
    row = {'fix_session_id': session, 'iter': it, 'strategy': strategy, 'from_stage': from_stage, 'check': check,
           'violation_class': vclass, 'rolled_back': rolled_back, 'config_before': {},
           'config_delta': '{}', 'situation': situation or {}, 'before_categories': before_categories}
    reports = Path(project) / 'reports'
    reports.mkdir(parents=True, exist_ok=True)
    with (reports / 'fix_log.jsonl').open('a') as f:
        for r in (*extra_rows, row):
            f.write(json.dumps(r) + '\n')
    return {'unit_id': uid, 'identity': {'design': design, 'platform': 'sky130hd'}, 'raw_row': row,
            'action': {'strategy': strategy, 'from_stage': from_stage},
            'provenance': {'sources': [{'path': str(reports / 'fix_log.jsonl'), 'line': 1}]}}


def drc_pair(p, pre=12, post=0, pre_t=None, post_t=None, post_drc_measured=True):
    mkrun(p, 'RUN_A', drc=pre, timing=pre_t or T())
    mkrun(p, 'RUN_B', [('route', 0), ('finish', 0)], reason=why('antenna_diode_iters', 'route'), from_stage='route',
          reused={s: 'RUN_A' for s in ('synth', 'floorplan', 'place', 'cts')},
          drc=post if post_drc_measured else None, timing=post_t or T())


def annotate(u):
    return AN.annotate_unit(u, AN.Binder(), FAMILIES)


# -- typed fields -------------------------------------------------------------------

def test_setup_fields_carry_metric_unit_value_stage_and_hashed_sources(tmp_path):
    d = mkrun(tmp_path / 'p', 'RUN_A', timing=T(ws=-0.31, tns=-4.2, viol=17))
    m = FD.measure(d)
    s = m['setup']
    assert s['present'] is True and s['type_status'] == 'KNOWN'
    f = s['fields']
    assert (f['metric']['value'], f['unit']['value'], f['value']['value'], f['stage']['value']) == \
        ('setup_ws', '1ns', -0.31, 'finish')
    assert f['tns']['value'] == -4.2 and f['violators']['value'] == 17
    assert all(src['sha256'] and src['locator'] for src in f['value']['source'])


def test_a_missing_time_unit_leaves_setup_untyped_but_drv_typed(tmp_path):
    d = mkrun(tmp_path / 'p', 'RUN_A', timing=T(ws=-0.31, slew=4), time_unit=None)
    m = FD.measure(d)
    assert m['setup']['type_status'] == 'UNKNOWN' and m['setup']['fields']['unit']['unknown_reason'] == 'metric_absent'
    assert m['drv']['type_status'] == 'KNOWN' and m['drv']['fields']['max_slew']['value'] == 4


def test_a_missing_auxiliary_field_does_not_erase_the_type(tmp_path):
    d = mkrun(tmp_path / 'p', 'RUN_A', timing=T(ws=-0.31, drop=('finish__timing__setup__tns',)))
    s = FD.measure(d)['setup']
    assert s['type_status'] == 'KNOWN'
    assert s['fields']['tns']['status'] == 'UNKNOWN' and s['fields']['tns']['value'] is None


def test_unknown_fields_never_hold_values_and_known_fields_have_sources(tmp_path):
    d = mkrun(tmp_path / 'p', 'RUN_A', timing=T(drop=('finish__timing__setup__tns',)), drc=3)
    m = FD.measure(d)
    for chk in (m[c] for c in FD.CHECKS):
        for f in chk['fields'].values():
            if f['status'] == 'UNKNOWN':
                assert f['value'] is None and f['unknown_reason'] in FD.UNKNOWN_REASONS
            if f['status'] == 'KNOWN':
                assert f['source']


def test_stage_abort_fields_from_the_run(tmp_path):
    d = mkrun(tmp_path / 'p', 'RUN_A', [('synth', 0), ('floorplan', 0), ('place', 124)], error='FLW-0024')
    s = FD.measure(d)['stage']
    assert s['present'] is True
    assert (s['fields']['stage']['value'], s['fields']['error_code']['value'], s['fields']['timeout']['value']) == \
        ('place', 'FLW-0024', True)


def test_route_residual_is_distinct_from_a_route_abort(tmp_path):
    d = mkrun(tmp_path / 'p', 'RUN_A', route_rpt='violation type: Short\n' * 3)
    m = FD.measure(d)
    assert m['stage']['present'] is False and m['route_residual']['present'] is True
    assert m['route_residual']['fields']['count']['value'] == 3


def test_an_unmeasured_check_is_unknown_not_clean(tmp_path):
    m = FD.measure(mkrun(tmp_path / 'p', 'RUN_A'))
    assert m['drc']['measured'] is False and m['drc']['present'] is None
    assert m['lvs']['measured'] is False and m['setup']['measured'] is False


# -- targets: from evidence, never from the strategy name ---------------------------

def test_a_drv_margin_strategy_on_a_setup_failure_targets_setup(tmp_path):
    p = tmp_path / 'p'
    mkrun(p, 'RUN_A', timing=T(ws=-0.4, viol=9))
    mkrun(p, 'RUN_B', [('route', 0), ('finish', 0)], reason=why('drv_route_margin_20', 'route'), from_stage='route',
          reused={s: 'RUN_A' for s in ('synth', 'floorplan', 'place', 'cts')}, timing=T(ws=-0.4, viol=9))
    r = annotate(unit(p, check='timing', vclass='timing', strategy='drv_route_margin_20'))
    assert [t['type'] for t in r['targets']] == ['setup']


def test_a_setup_named_strategy_on_a_drv_failure_targets_drv(tmp_path):
    p = tmp_path / 'p'
    mkrun(p, 'RUN_A', timing=T(ws=0.3, slew=11, cap=3))
    mkrun(p, 'RUN_B', [('route', 0), ('finish', 0)], reason=why('setup_slack_margin', 'route'), from_stage='route',
          reused={s: 'RUN_A' for s in ('synth', 'floorplan', 'place', 'cts')}, timing=T(ws=0.3))
    r = annotate(unit(p, check='timing', vclass='timing', strategy='setup_slack_margin'))
    assert [t['type'] for t in r['targets']] == ['drv']
    assert r['step_effect'] == {'drv': 'CLEARED'} and r['positive'] is True


def test_drc_signature_from_recorded_categories_cross_checked_with_the_pre_run(tmp_path):
    p = tmp_path / 'p'
    drc_pair(p, pre=12)
    r = annotate(unit(p, before_categories={'li.3': {'count': 10}, 'm1.2': {'count': 2}}))
    t = r['targets'][0]
    assert t['type'] == 'drc' and t['fields']['rule_class']['value'] == 'li.3'
    assert t['fields']['checker']['value'] == 'klayout_direct' and t['fields']['count']['value'] == 12


def test_disagreeing_recorded_categories_leave_the_rule_class_unknown_not_the_type(tmp_path):
    p = tmp_path / 'p'
    drc_pair(p, pre=12)
    r = annotate(unit(p, before_categories={'li.3': {'count': 30}}))
    t = r['targets'][0]
    assert t['type'] == 'drc' and t['type_status'] == 'KNOWN'
    assert t['fields']['rule_class']['status'] == 'UNKNOWN'
    assert t['fields']['rule_class']['unknown_reason'] == 'conflicting_sources'


def test_non_applicable_fields_are_marked_not_applicable(tmp_path):
    p = tmp_path / 'p'
    drc_pair(p, pre=12)
    t = annotate(unit(p, before_categories={'li.3': {'count': 12}}))['targets'][0]
    assert t['fields']['slack']['status'] == 'NOT_APPLICABLE' and t['fields']['slack']['source'] == []


# -- step effect -----------------------------------------------------------------------

@pytest.mark.parametrize('pre,post,effect', [(12, 0, 'CLEARED'), (12, 5, 'IMPROVED'), (12, 12, 'NO_CHANGE'),
                                             (12, 20, 'REGRESSED')])
def test_drc_step_effect(tmp_path, pre, post, effect):
    p = tmp_path / 'p'
    drc_pair(p, pre=pre, post=post)
    assert annotate(unit(p, before_categories={'li.3': {'count': pre}}))['step_effect'] == {'drc': effect}


@pytest.mark.parametrize('pre,post,effect', [(-0.2, 0.1, 'CLEARED'), (-0.2, -0.1, 'IMPROVED'),
                                             (-0.2, -0.2, 'NO_CHANGE'), (-0.2, -0.5, 'REGRESSED')])
def test_setup_step_effect(tmp_path, pre, post, effect):
    p = tmp_path / 'p'
    mkrun(p, 'RUN_A', timing=T(ws=pre, viol=3))
    mkrun(p, 'RUN_B', [('route', 0), ('finish', 0)], reason=why('x', 'route'), from_stage='route',
          reused={s: 'RUN_A' for s in ('synth', 'floorplan', 'place', 'cts')}, timing=T(ws=post))
    assert annotate(unit(p, check='timing', vclass='timing', strategy='x'))['step_effect'] == {'setup': effect}


def test_setup_effect_is_unknown_when_units_differ(tmp_path):
    p = tmp_path / 'p'
    mkrun(p, 'RUN_A', timing=T(ws=-0.2, viol=3))
    mkrun(p, 'RUN_B', [('route', 0), ('finish', 0)], reason=why('x', 'route'), from_stage='route',
          reused={s: 'RUN_A' for s in ('synth', 'floorplan', 'place', 'cts')}, timing=T(ws=0.1), time_unit='1ps')
    assert annotate(unit(p, check='timing', vclass='timing', strategy='x'))['step_effect'] == {'setup': 'UNKNOWN'}


# -- new failures, rollback and the positive gate --------------------------------------

def test_cleared_with_no_new_failure_measured_on_both_runs_is_positive(tmp_path):
    p = tmp_path / 'p'
    drc_pair(p)
    r = annotate(unit(p, before_categories={'li.3': {'count': 12}}))
    assert r['step_effect'] == {'drc': 'CLEARED'} and r['new_failures']['setup'] == 'KNOWN-none'
    assert r['positive'] is True and r['positive_blockers'] == []


def test_a_new_failure_blocks_positive(tmp_path):
    p = tmp_path / 'p'
    drc_pair(p, post_t=T(ws=-0.3, viol=5))
    r = annotate(unit(p, before_categories={'li.3': {'count': 12}}))
    assert r['new_failures']['setup'] == 'KNOWN-present' and r['positive'] is False
    assert 'new_failure:setup=KNOWN-present' in r['positive_blockers']


def test_a_check_measured_before_but_not_after_blocks_positive(tmp_path):
    p = tmp_path / 'p'
    mkrun(p, 'RUN_A', drc=12, timing=T(), lvs='clean')
    mkrun(p, 'RUN_B', [('route', 0), ('finish', 0)], reason=why('antenna_diode_iters', 'route'), from_stage='route',
          reused={s: 'RUN_A' for s in ('synth', 'floorplan', 'place', 'cts')}, drc=0, timing=T())
    r = annotate(unit(p, before_categories={'li.3': {'count': 12}}))
    assert r['new_failures']['lvs'] == 'UNKNOWN' and r['positive'] is False


def test_a_check_not_measured_before_does_not_block_and_is_recorded(tmp_path):
    p = tmp_path / 'p'
    drc_pair(p)
    r = annotate(unit(p, before_categories={'li.3': {'count': 12}}))
    assert r['new_failures']['lvs'] == 'NOT_MEASURED_BEFORE' and r['positive'] is True


@pytest.mark.parametrize('rolled_back,blocker', [(True, 'rolled_back'), (None, 'rollback_unknown')])
def test_rollback_true_or_unknown_blocks_positive(tmp_path, rolled_back, blocker):
    p = tmp_path / 'p'
    drc_pair(p)
    r = annotate(unit(p, rolled_back=rolled_back, before_categories={'li.3': {'count': 12}}))
    assert r['positive'] is False and blocker in r['positive_blockers']


def test_post_target_unmeasured_is_unknown_effect_and_not_positive(tmp_path):
    p = tmp_path / 'p'
    drc_pair(p, post_drc_measured=False)
    r = annotate(unit(p, before_categories={'li.3': {'count': 12}}))
    assert r['step_effect'] == {'drc': 'UNKNOWN'} and r['positive'] is False


# -- stage aborts ------------------------------------------------------------------------

def place_case(p, post_stages, error=None):
    mkrun(p, 'RUN_A', [('synth', 0), ('floorplan', 0), ('place', 2)], error='FLW-0024')
    mkrun(p, 'RUN_B', post_stages, reason=why('route_relief', 'floorplan'), from_stage='floorplan',
          reused={'synth': 'RUN_A'}, error=error)


@pytest.mark.parametrize('post_stages,error,effect,new', [
    ([('floorplan', 0), ('place', 0), ('cts', 0), ('route', 0), ('finish', 0)], None, 'CLEARED', 'KNOWN-none'),
    ([('floorplan', 0), ('place', 0), ('cts', 0), ('route', 2)], 'GRT-0116', 'CLEARED', 'KNOWN-present'),
    ([('floorplan', 0), ('place', 2)], 'FLW-0024', 'NO_CHANGE', 'KNOWN-none'),
    ([('floorplan', 2)], 'IFP-0001', 'REGRESSED', 'KNOWN-present')])
def test_place_abort_step_effect(tmp_path, post_stages, error, effect, new):
    p = tmp_path / 'p'
    place_case(p, post_stages, error)
    r = annotate(unit(p, check='orfs_stage', vclass='place', strategy='route_relief', from_stage='floorplan',
                      situation={'check': 'orfs_stage', 'violation_class': 'place', 'error_code': 'FLW-0024'}))
    assert r['step_effect'] == {'stage_abort': effect} and r['new_failures']['stage'] == new
    assert r['positive'] is (effect == 'CLEARED' and new == 'KNOWN-none')


def test_an_unbound_attempt_keeps_recorded_fields_but_no_effect(tmp_path):
    p = tmp_path / 'p'
    mkrun(p, 'RUN_A', [('synth', 0), ('floorplan', 0), ('place', 2)], error='FLW-0024')
    r = annotate(unit(p, check='orfs_stage', vclass='place', strategy='core_util_relief', from_stage='place',
                      session='resize_abc', situation={'check': 'orfs_stage', 'violation_class': 'place',
                                                        'error_code': 'FLW-0024', 'timeout': False}))
    t = r['targets'][0]
    assert t['type'] == 'stage_abort' and t['type_status'] == 'KNOWN'
    assert t['fields']['error_code']['value'] == 'FLW-0024'
    assert t['fields']['error_code']['source'][0]['path'].endswith('fix_log.jsonl')
    assert r['step_effect'] == {'stage_abort': 'UNKNOWN'} and r['positive'] is False
    assert 'binding_post_unknown' in r['positive_blockers']


# -- D6 advisory: a hash-referenced run is never a binding -------------------------------

def test_a_record_time_hash_gives_an_advisory_never_a_binding(tmp_path):
    import hashlib
    p = tmp_path / 'p'
    mkrun(p, 'RUN_A', [('synth', 0), ('floorplan', 0), ('place', 2)], error='FLW-0024')
    mkrun(p, 'RUN_B')
    sid = 'resize_' + hashlib.sha1(f'{p}:RUN_B'.encode()).hexdigest()[:12]
    r = annotate(unit(p, check='orfs_stage', vclass='place', strategy='core_util_relief', from_stage='place',
                      session=sid, situation={'check': 'orfs_stage', 'violation_class': 'place',
                                              'error_code': 'FLW-0024'}))
    adv = r['post_run_advisory']
    assert adv['status'] == 'ADVISORY' and adv['hash_match'] == ['RUN_B'] and adv['counted'] is False
    assert 'action attribution not established' in adv['unresolved']
    assert r['binding']['post']['status'] == 'UNKNOWN' and r['positive'] is False


# -- identity -----------------------------------------------------------------------------

@pytest.mark.parametrize('design,family,group', [
    ('aes_core', 'secworks/aes', 'secworks'), ('chacha_core', 'secworks/chacha', 'secworks'),
    ('picorv32', 'yosyshq/picorv32', 'yosyshq/picorv32')])
def test_three_identity_layers(design, family, group):
    ident = AN.identity({'identity': {'design': design}}, FAMILIES)
    assert (ident['design_id']['value'], ident['ip_family']['value'], ident['source_group']['value']) == \
        (design, family, group)


def test_a_design_without_a_family_has_unknown_lineage():
    ident = AN.identity({'identity': {'design': 'mystery'}}, FAMILIES)
    assert ident['ip_family']['status'] == 'UNKNOWN' and ident['source_group']['status'] == 'UNKNOWN'


def test_a_non_attempt_is_kept_and_never_counted(tmp_path):
    u = unit(tmp_path / 'p', strategy='none')
    u['raw_row']['verdict'] = 'stop_clean'
    r = annotate(u)
    assert r['status'] == 'NOT_AN_ATTEMPT' and r['positive'] is False


def test_a_finish_report_carried_into_an_aborted_rerun_is_not_this_runs_evidence(tmp_path):
    """failure-patterns #61 (ann-v2-dev.7): a rerun that aborted at global route carried the
    previous flow's logs/6_report.json. The annotator, the binder and the independent verifier
    must all ignore it; the same report in a run that finished is measured."""
    from ann_v2 import binding as BD, verify as VF
    aborted = mkrun(tmp_path / 'p', 'RUN_ab', [('floorplan', 0), ('place', 0), ('cts', 0), ('route', 2)],
                    timing=T(ws=-0.4, slew=3))
    m = FD.measure(aborted)
    for k in ('setup', 'hold', 'drv'):
        assert m[k]['measured'] is False and m[k]['finish_report'] == 'not_produced_by_this_run'
    assert m['constraint']['timing_unconstrained'] is None
    assert BD._read_run(aborted)['timing'] is None
    assert VF.read_run(aborted)['timing'] is None
    done = mkrun(tmp_path / 'q', 'RUN_ok', FULL, timing=T(ws=-0.4, slew=3))
    m = FD.measure(done)
    assert m['setup']['present'] is True and m['drv']['present'] is True
    assert BD._read_run(done)['timing'] is not None and VF.read_run(done)['timing'] is not None
