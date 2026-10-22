"""ann-v2 verification gate (user decision 2026-10-17): automated verification replaces the
manual sign-off. Two groups:

* annotator counter-examples -- raw evidence shaped to make a careless annotator associate
  the wrong run, miss a rollback or treat missing data as valid; the annotator must answer
  UNKNOWN (or the correct KNOWN) every time;
* verifier -- an independent re-derivation that passes a correct annotation, fails every
  tampered record and every record whose evidence changed after annotation, and reports
  under-binding as information only.
"""
import copy
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'memory'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_ann_v2_fields as FX  # noqa: E402  (shared synthetic evidence builders)
from ann_v2 import annotate as AN  # noqa: E402
from ann_v2 import fields as FD  # noqa: E402
from ann_v2 import rollback as RB  # noqa: E402
from ann_v2 import verify as V  # noqa: E402

mkrun, T, why, unit, drc_pair = FX.mkrun, FX.T, FX.why, FX.unit, FX.drc_pair


def source_dir(tmp_path, units):
    src = tmp_path / 'SOURCE'
    src.mkdir(exist_ok=True)
    (src / 'source_export.jsonl').write_text(''.join(json.dumps(u) + '\n' for u in units))
    manifest = src / 'source_manifest.json'
    manifest.write_text(json.dumps({'split': {'family_of_design': FX.FAMILIES}}))
    AN.FAMILY_SOURCE[:] = [FD.src(manifest, 'split.family_of_design')]
    return src


def annotate_all(tmp_path, units):
    src = source_dir(tmp_path, units)
    binder = AN.Binder()
    return src, [AN.annotate_unit(u, binder, FX.FAMILIES) for u in units]


def run_verify(tmp_path, src, records):
    ann = tmp_path / 'ann.jsonl'
    ann.write_text(''.join(json.dumps(r, default=str) + '\n' for r in records))
    return V.verify(ann, src)


def kinds(report, severity='CRITICAL'):
    return sorted({f['kind'] for f in report['findings'] if f['severity'] == severity})


def legacy_unit(p, **kw):
    """A unit whose on-disk row has NO rolled_back marker (writer < e57acd03) while the
    export copy carries the enriched value -- the SOURCE-v1 situation."""
    u = unit(p, **kw)
    log = Path(p) / 'reports' / 'fix_log.jsonl'
    rows = [json.loads(l) for l in log.read_text().splitlines()]
    for r in rows:
        r.pop('rolled_back', None)
    log.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    u['raw_row'] = dict(u['raw_row'], rolled_back=False)          # export enrichment
    return u


# ---------------------------------------------------------------- annotator counter-examples

def test_an_export_only_rolled_back_value_is_unknown_and_never_positive(tmp_path):
    p = tmp_path / 'p'
    drc_pair(p)
    baseline = FX.annotate(unit(p, before_categories={'li.3': {'count': 12}}))
    assert baseline['positive'] is True and baseline['rolled_back']['status'] == 'KNOWN'
    q = tmp_path / 'q'
    drc_pair(q)
    r = FX.annotate(legacy_unit(q, before_categories={'li.3': {'count': 12}}))
    assert r['rolled_back'] == dict(r['rolled_back'], status='UNKNOWN', value=None, unknown_reason='export_only')
    assert r['positive'] is False and 'rollback_unknown' in r['positive_blockers']
    assert r['step_effect'] == {'drc': 'CLEARED'}                 # the measured effect is kept


REGS = ['drc_target_regression:12->14']


def make_owner(p, rows_extra=()):
    """Turn the unit's on-disk row into a regression row (verdict + global_regressions)."""
    log = p / 'reports' / 'fix_log.jsonl'
    rows = [json.loads(l) for l in log.read_text().splitlines()]
    rows[-1].update(verdict='regression', global_regressions=REGS)
    rows += list(rows_extra)
    log.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    return rows[-1 - len(rows_extra)]


def test_a_regression_file_owned_by_the_row_is_a_known_rollback(tmp_path):
    p = tmp_path / 'p'
    drc_pair(p)
    u = legacy_unit(p, before_categories={'li.3': {'count': 12}})
    u['raw_row'] = make_owner(p)
    (p / 'reports' / 'global_regression_it1.json').write_text(json.dumps({
        'iter': 1, 'strategy': 'antenna_diode_iters', 'config_restored': True, 'regressions': REGS,
        'pre': {'layout': {'run_tag': 'RUN_A'}}}))
    r = FX.annotate(u)
    assert r['rolled_back']['status'] == 'KNOWN' and r['rolled_back']['value'] is True
    assert r['rolled_back']['source'][0]['path'].endswith('global_regression_it1.json')
    assert r['positive'] is False and 'rolled_back' in r['positive_blockers']


def test_a_regression_file_not_uniquely_owned_by_the_row_shows_no_rollback(tmp_path):
    cases = {
        'same_iteration_only': ({'strategy': 'antenna_diode_iters', 'regressions': REGS}, False, ()),
        'other_strategy': ({'strategy': 'density_relief', 'regressions': REGS}, True, ()),
        'other_regressions': ({'strategy': 'antenna_diode_iters', 'regressions': ['route_regression:0->3']}, True, ()),
        'cross_session_repeat': ({'strategy': 'antenna_diode_iters', 'regressions': REGS}, True,
                                 ({'fix_session_id': 's2', 'iter': 1, 'strategy': 'antenna_diode_iters',
                                   'check': 'drc', 'verdict': 'regression', 'global_regressions': REGS},)),
    }
    for name, (body, owner, extra) in cases.items():
        p = tmp_path / name
        drc_pair(p)
        u = legacy_unit(p, before_categories={'li.3': {'count': 12}})
        if owner:
            u['raw_row'] = make_owner(p, extra)
        (p / 'reports' / 'global_regression_it1.json').write_text(json.dumps({
            'iter': 1, 'config_restored': True, 'pre': {'layout': {'run_tag': 'RUN_A'}}, **body}))
        r = FX.annotate(u)
        assert r['rolled_back']['status'] == 'UNKNOWN', name
        assert r['rolled_back']['unknown_reason'] in ('ambiguous_binding', 'no_explicit_link', 'export_only'), name
        assert r['positive'] is False


def test_iteration_two_pre_is_unknown_when_the_predecessor_rollback_is_unrecorded(tmp_path):
    p = tmp_path / 'p'
    mkrun(p, 'RUN_A', drc=12, timing=T())
    mkrun(p, 'RUN_B', [('route', 0), ('finish', 0)], reason=why('s_one', 'route'), from_stage='route',
          reused={s: 'RUN_A' for s in ('synth', 'floorplan', 'place', 'cts')}, drc=6, timing=T())
    mkrun(p, 'RUN_C', [('route', 0), ('finish', 0)], reason=why('s_two', 'route'), from_stage='route',
          reused={s: 'RUN_A' for s in ('synth', 'floorplan', 'place', 'cts')}, drc=0, timing=T())
    first = {'fix_session_id': 's1', 'iter': 1, 'strategy': 's_one', 'from_stage': 'route', 'check': 'drc',
             'violation_class': 'li.3', 'config_before': {}, 'config_delta': '{}'}
    u = unit(p, uid='u2', it=2, strategy='s_two', extra_rows=(first,), before_categories={'li.3': {'count': 6}})
    u['provenance']['sources'][0]['line'] = 2
    log = p / 'reports' / 'fix_log.jsonl'
    rows = [json.loads(l) for l in log.read_text().splitlines()]
    rows[1].pop('rolled_back')                                   # both rows legacy
    log.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    r = FX.annotate(u)
    assert r['binding']['post']['run'] == 'RUN_C'
    assert r['binding']['pre']['status'] == 'UNKNOWN'
    assert r['binding']['pre']['unknown_reason'] == 'predecessor_rollback_unknown'
    assert [c['run'] for c in r['binding']['pre']['candidates']] == ['RUN_B', 'RUN_A']
    rows[0]['rolled_back'] = False                               # predecessor marker recorded -> KNOWN via post
    log.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    r = FX.annotate(u)
    assert r['binding']['pre']['run'] == 'RUN_B'


def test_row_recorded_fields_cite_the_on_disk_row_or_stay_unknown(tmp_path):
    p = tmp_path / 'p'
    sit = {'check': 'orfs_stage', 'violation_class': 'place', 'error_code': 'FLW-0024', 'timeout': False}
    u = unit(p, check='orfs_stage', vclass='place', strategy='route_relief', from_stage='floorplan', situation=sit)
    r = FX.annotate(u)                                           # no runs: typed from the recorded situation
    t = r['targets'][0]
    assert t['type'] == 'stage_abort' and t['fields']['error_code']['value'] == 'FLW-0024'
    assert all(s['locator'] == 'line 1: situation.error_code' for s in t['fields']['error_code']['source'])
    (p / 'reports' / 'fix_log.jsonl').unlink()                   # export copy only
    r = FX.annotate(u)
    assert r['targets'][0]['type'] == 'UNKNOWN'
    assert r['rolled_back']['unknown_reason'] == 'row_missing_on_disk'


def test_a_json_list_source_is_cited_by_item(tmp_path):
    trial = tmp_path / 'g-trials' / 'flw_hd__chacha_core__u90.json'
    trial.parent.mkdir(parents=True)
    row = {'fix_session_id': 'gtrial_x', 'iter': 1, 'strategy': 'component:CORE_UTILIZATION', 'from_stage': 'floorplan',
           'check': 'orfs_stage', 'violation_class': 'place',
           'situation': {'error_code': 'FLW-0024', 'timeout': False, 'violation_class': 'place'}}
    trial.write_text(json.dumps([row]))
    u = {'unit_id': 'g1', 'identity': {'design': 'chacha_core'}, 'raw_row': dict(row, rolled_back=False),
         'action': {'strategy': row['strategy'], 'from_stage': 'floorplan'},
         'provenance': {'sources': [{'path': str(trial), 'line': 1}]}}
    r = FX.annotate(u)
    f = r['targets'][0]['fields']
    assert f['error_code']['value'] == 'FLW-0024' and f['error_code']['source'][0]['locator'] == 'item 1: situation.error_code'
    assert r['rolled_back']['unknown_reason'] == 'export_only'


def test_a_nearer_unrelated_run_changes_nothing(tmp_path):
    p = tmp_path / 'p'
    drc_pair(p)
    mkrun(p, 'RUN_Z', [('route', 0), ('finish', 0)], reason=why('something_else', 'route'), from_stage='route',
          reused={s: 'RUN_A' for s in ('synth', 'floorplan', 'place', 'cts')}, drc=0, timing=T())
    r = FX.annotate(unit(p, before_categories={'li.3': {'count': 12}}))
    assert r['binding']['post']['run'] == 'RUN_B' and r['positive'] is True


def test_a_disagreeing_copy_of_the_post_run_is_a_copy_conflict(tmp_path):
    p, q = tmp_path / 'p', tmp_path / 'q'
    drc_pair(p)
    shutil.copytree(p, q)
    (q / 'backend' / 'RUN_B' / 'drc' / 'drc_result.json').write_text(json.dumps({'status': 'fail', 'violations': '3'}))
    u = unit(p, before_categories={'li.3': {'count': 12}})
    u['provenance']['sources'].append({'path': str(q / 'reports' / 'fix_log.jsonl'), 'line': 1})
    r = FX.annotate(u)
    assert r['binding']['post']['status'] == 'UNKNOWN' and r['binding']['post']['unknown_reason'] == 'copy_conflict'
    assert r['positive'] is False


# ---------------------------------------------------------------- verifier

def good_case(tmp_path):
    p = tmp_path / 'p'
    drc_pair(p)
    src, recs = annotate_all(tmp_path, [unit(p, before_categories={'li.3': {'count': 12}})])
    assert recs[0]['positive'] is True
    return p, src, recs


def test_the_verifier_passes_a_correct_annotation(tmp_path):
    p, src, recs = good_case(tmp_path)
    rep = run_verify(tmp_path, src, recs)
    assert rep['gate'] == 'PASS' and rep['critical'] == 0, rep['findings']
    assert rep['recorded_positive'] == rep['rederived_positive'] == 1


def tamper(rec, how):
    r = copy.deepcopy(rec)
    if how == 'wrong_post_run':
        r['binding']['post']['run'] = 'RUN_A'
    elif how == 'unknown_as_known_no_source':
        r['targets'][0]['fields']['count'] = dict(r['targets'][0]['fields']['count'], source=[])
    elif how == 'fabricated_value':
        r['targets'][0]['fields']['count']['value'] = 99
    elif how == 'effect_relabelled':
        r['step_effect']['drc'] = 'IMPROVED'
    elif how == 'rollback_invented':
        r['rolled_back'] = dict(r['rolled_back'], source=[dict(r['rolled_back']['source'][0], locator='line 1: verdict')])
    elif how == 'identity_wrong':
        r['identity']['source_group']['value'] = 'opencores'
    elif how == 'advisory_counted':
        r['post_run_advisory'] = {'status': 'ADVISORY', 'counted': True, 'hash_match': []}
    elif how == 'new_failure_relabelled':
        r['new_failures']['lvs'] = 'KNOWN-none'
    elif how == 'unknown_reason_invented':
        r['targets'][0]['fields']['tns'] = FD.unknown('metric_absent', 'x')
        r['targets'][0]['fields']['tns']['unknown_reason'] = 'probably_fine'
    return r


@pytest.mark.parametrize('how,kind', [
    ('wrong_post_run', 'wrong_binding'), ('unknown_as_known_no_source', 'unknown_as_known'),
    ('fabricated_value', 'provenance'), ('effect_relabelled', 'effect_mismatch'),
    ('rollback_invented', 'provenance'), ('identity_wrong', 'provenance'),
    ('advisory_counted', 'advisory_counted'), ('new_failure_relabelled', 'new_failure_mismatch'),
    ('unknown_reason_invented', 'shape')])
def test_the_verifier_fails_every_tampered_record(tmp_path, how, kind):
    p, src, recs = good_case(tmp_path)
    rep = run_verify(tmp_path, src, [tamper(recs[0], how)])
    assert rep['gate'] == 'FAIL' and kind in kinds(rep), (how, rep['findings'])


def test_a_positive_whose_parts_do_not_support_it_is_miscounted(tmp_path):
    p, src, recs = good_case(tmp_path)
    r = copy.deepcopy(recs[0])
    r['rolled_back'] = FD.unknown('not_recorded', 'fix_log_row')
    rep = run_verify(tmp_path, src, [r])
    assert 'miscounted_positive' in kinds(rep)


@pytest.mark.parametrize('mutation,kind,miscounted', [
    ('delete_post_resume_meta', 'wrong_binding', True), ('duplicate_post_run', 'wrong_binding', True),
    ('add_claimant_row', 'wrong_binding', True), ('add_regression_file', 'missed_rollback', True),
    ('edit_post_drc', 'effect_mismatch', True), ('edit_pre_drc', 'provenance', False)])
def test_the_verifier_fails_a_record_whose_evidence_changed_after_annotation(tmp_path, mutation, kind, miscounted):
    p, src, recs = good_case(tmp_path)
    if mutation == 'delete_post_resume_meta':
        (p / 'backend' / 'RUN_B' / 'resume_meta.json').unlink()
    elif mutation == 'duplicate_post_run':
        shutil.copytree(p / 'backend' / 'RUN_B', p / 'backend' / 'RUN_B2')
    elif mutation == 'add_claimant_row':
        with (p / 'reports' / 'fix_log.jsonl').open('a') as f:
            f.write(json.dumps({'fix_session_id': 's9', 'iter': 1, 'strategy': 'antenna_diode_iters',
                                'from_stage': 'route', 'check': 'drc'}) + '\n')
    elif mutation == 'add_regression_file':
        log = p / 'reports' / 'fix_log.jsonl'
        rows = [json.loads(l) for l in log.read_text().splitlines()]
        rows[0].pop('rolled_back')
        rows[0].update(verdict='regression', global_regressions=REGS)
        log.write_text(''.join(json.dumps(r) + '\n' for r in rows))
        (p / 'reports' / 'global_regression_it1.json').write_text(json.dumps({
            'iter': 1, 'strategy': 'antenna_diode_iters', 'config_restored': True, 'regressions': REGS,
            'pre': {'layout': {'run_tag': 'RUN_A'}}}))
    elif mutation == 'edit_post_drc':
        (p / 'backend' / 'RUN_B' / 'drc' / 'drc_result.json').write_text(
            json.dumps({'status': 'fail', 'violations': '4', 'checker': 'klayout_direct'}))
    elif mutation == 'edit_pre_drc':
        (p / 'backend' / 'RUN_A' / 'drc' / 'drc_result.json').write_text(
            json.dumps({'status': 'fail', 'violations': '13', 'checker': 'klayout_direct'}))
    rep = run_verify(tmp_path, src, recs)
    assert rep['gate'] == 'FAIL' and kind in kinds(rep), (mutation, rep['findings'])
    assert ('miscounted_positive' in kinds(rep)) is miscounted
    if mutation == 'edit_pre_drc':
        assert 'target_mismatch' in kinds(rep)                   # 12 recorded, 13 on disk now


def test_under_binding_is_information_never_a_failure(tmp_path):
    p, src, recs = good_case(tmp_path)
    r = copy.deepcopy(recs[0])
    r['binding']['post'] = {'status': 'UNKNOWN', 'run': None, 'unknown_reason': 'no_explicit_link',
                            'candidates': [], 'evidence': None}
    r['positive'], r['positive_blockers'] = False, ['binding_post_unknown']
    r['admission'] = {'status': 'NOT_ADMITTED', 'reasons': ['not_positive']}
    r['step_effect'], r['new_failures'] = {'drc': 'UNKNOWN'}, {c: 'UNKNOWN' for c in FD.CHECKS}
    rep = run_verify(tmp_path, src, [r])
    assert rep['gate'] == 'PASS' and 'under_bound' in kinds(rep, 'INFO')


def test_source_hash_and_coverage_are_checked(tmp_path):
    p, src, recs = good_case(tmp_path)
    ann = tmp_path / 'ann.jsonl'
    ann.write_text(json.dumps(recs[0], default=str) + '\n')
    rep = V.verify(ann, src, expect_source_sha='0' * 64)
    assert 'input_integrity' in kinds(rep)
    rep = V.verify(ann, src)
    assert rep['gate'] == 'PASS'
    ann.write_text('')
    assert 'input_integrity' in kinds(V.verify(ann, src))


def test_the_verifier_shares_no_code_with_the_annotator():
    import ann_v2.verify as mod
    text = Path(mod.__file__).read_text()
    assert 'from ann_v2' not in text and 'import ann_v2' not in text
    assert 'situation' not in text.split('"""', 2)[2].split('import')[0]


def test_rollback_state_helper_is_tri_state(tmp_path):
    assert RB.rollback_state([], {'rolled_back': True}, None)[0] is True
    assert RB.rollback_state([], {'rolled_back': False}, None)[0] is False
    assert RB.rollback_state([str(tmp_path)], {'iter': 1, 'strategy': 's'}, 'RUN_A')[0] is None


def test_effect_cleared_with_unknown_rollback_is_a_labelled_candidate_never_positive(tmp_path):
    q = tmp_path / 'q'
    drc_pair(q)
    r = FX.annotate(legacy_unit(q, before_categories={'li.3': {'count': 12}}))
    assert r['positive'] is False and r['candidate_class'] == 'effect_cleared_rollback_unknown'
    p = tmp_path / 'p'
    drc_pair(p)
    assert FX.annotate(unit(p, before_categories={'li.3': {'count': 12}}))['candidate_class'] is None


def test_the_verifier_fails_a_candidate_label_on_a_positive(tmp_path):
    p, src, recs = good_case(tmp_path)
    r = copy.deepcopy(recs[0])
    r['candidate_class'] = 'effect_cleared_rollback_unknown'
    assert 'miscounted_positive' in kinds(run_verify(tmp_path, src, [r]))
