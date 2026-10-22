"""Regression snapshot as a pre-action-run source (user decision 2026-10-17: adopt, but only
when uniquely owned; ann_v2/snapshot.py S1-S5).

Base case: a legacy regression row (no rollback marker) whose iteration-1 anchor search is
ambiguous (two runs reuse the same digest and both recorded the DRC failure). A snapshot that
passes every condition names the pre run; every condition broken leaves it UNKNOWN. The post
binding and the rollback judgement stay independent, a snapshot never shows "not rolled
back", and the verifier agrees in every case.
"""
import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'memory'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_ann_v2_fields as FX  # noqa: E402
import test_ann_v2_verify as TV  # noqa: E402

mkrun, T, why = FX.mkrun, FX.T, FX.why
REGS = ['drc_target_regression:12->14']
REUSED = {s: 'RUN_A' for s in ('synth', 'floorplan', 'place', 'cts')}


def put_def(run_dir: Path, body: str) -> str:
    (run_dir / 'results').mkdir(exist_ok=True)
    (run_dir / 'results' / '6_final.def').write_text(body)
    return hashlib.sha256(body.encode()).hexdigest()


def case(p, *, ambiguous=True, snap=None, extra_rows=(), drop_file=False):
    a = mkrun(p, 'RUN_A', drc=12, timing=T())
    da = put_def(a, 'DEF A\n')
    if ambiguous:
        put_def(mkrun(p, 'RUN_A2', drc=12, timing=T()), 'DEF A2\n')
    mkrun(p, 'RUN_B', [('route', 0), ('finish', 0)], reason=why('antenna_diode_iters', 'route'),
          from_stage='route', reused=REUSED, drc=14, timing=T())
    u = FX.unit(p, before_categories={'li.3': {'count': 12}})
    log = p / 'reports' / 'fix_log.jsonl'
    rows = [json.loads(l) for l in log.read_text().splitlines()]
    rows[-1].pop('rolled_back')
    rows[-1].update(verdict='regression', global_regressions=REGS, ts='2026-10-01T01:00:00-07:00')
    rows += list(extra_rows)
    log.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    u['raw_row'] = rows[0]
    body = {'iter': 1, 'strategy': 'antenna_diode_iters', 'config_restored': True, 'regressions': REGS,
            'pre': {'captured_at': '2026-10-01T00:30:00-07:00', 'layout': {'run_tag': 'RUN_A', 'def_digest': da},
                    'signals': {'drc': {'fresh': True}}},
            'post': {'layout': {'run_tag': 'RUN_B'}}}
    for k, v in (snap or {}).items():
        if k == 'pre':
            body['pre'] = {**body['pre'], **v}
        else:
            body[k] = v
    if not drop_file:
        (p / 'reports' / 'global_regression_it1.json').write_text(json.dumps(body))
    return u, body


def check_verifier(tmp_path, u):
    src, recs = TV.annotate_all(tmp_path, [u])
    rep = TV.run_verify(tmp_path, src, recs)
    assert rep['gate'] == 'PASS', rep['findings']
    return recs[0]


def test_without_a_snapshot_the_ambiguous_pre_stays_unknown(tmp_path):
    p = tmp_path / 'p'
    u, _ = case(p, drop_file=True)
    r = FX.annotate(u)
    assert r['binding']['pre']['unknown_reason'] == 'ambiguous_binding'
    assert r['rolled_back']['status'] == 'UNKNOWN'


def test_an_owned_fresh_verifiable_snapshot_names_the_pre_run(tmp_path):
    p = tmp_path / 'p'
    u, _ = case(p)
    r = check_verifier(tmp_path, u)
    assert r['binding']['pre']['status'] == 'KNOWN' and r['binding']['pre']['run'] == 'RUN_A'
    ev = r['binding']['pre']['evidence']['e1']['snapshot']
    assert ev['run_tag'] == 'RUN_A' and len(ev['snapshot_sha256']) == 64
    assert r['binding']['post']['run'] == 'RUN_B'                  # post bound independently
    assert r['rolled_back']['value'] is True                       # owned file: a rollback ...
    assert r['step_effect'] == {'drc': 'REGRESSED'} and r['positive'] is False   # ... and negative evidence


@pytest.mark.parametrize('name,snap,extra', [
    ('stale_target_signal', {'pre': {'signals': {'drc': {'fresh': False}}}}, ()),
    ('def_digest_mismatch', {'pre': {'layout': {'run_tag': 'RUN_A', 'def_digest': '0' * 64}}}, ()),
    ('run_missing', {'pre': {'layout': {'run_tag': 'RUN_GONE', 'def_digest': '0' * 64}}}, ()),
    ('post_disagrees', {'post': {'layout': {'run_tag': 'RUN_OTHER'}}}, ()),
    ('captured_after_the_row', {'pre': {'captured_at': '2026-10-01T02:00:00-07:00'}}, ()),
    ('other_strategy', {'strategy': 'density_relief'}, ()),
    ('other_regressions', {'regressions': ['route_regression:0->3']}, ()),
    ('config_not_restored', {'config_restored': False}, ()),
    ('cross_session_repeat', {}, ({'fix_session_id': 's2', 'iter': 1, 'strategy': 'antenna_diode_iters',
                                   'check': 'drc', 'verdict': 'regression', 'global_regressions': REGS},)),
])
def test_a_snapshot_that_breaks_a_condition_leaves_the_pre_run_unknown(tmp_path, name, snap, extra):
    p = tmp_path / 'p'
    u, _ = case(p, snap=snap, extra_rows=extra)
    r = check_verifier(tmp_path, u)
    assert r['binding']['pre']['status'] == 'UNKNOWN', name
    assert r['positive'] is False
    assert r['rolled_back']['value'] is not False                 # never "not rolled back"


def test_a_disagreeing_copy_of_the_snapshot_is_not_used(tmp_path):
    p, q = tmp_path / 'p', tmp_path / 'q'
    u, body = case(p)
    shutil.copytree(p, q)
    body2 = json.loads(json.dumps(body))
    body2['pre']['captured_at'] = '2026-10-01T00:31:00-07:00'
    (q / 'reports' / 'global_regression_it1.json').write_text(json.dumps(body2))
    u['provenance']['sources'].append({'path': str(q / 'reports' / 'fix_log.jsonl'), 'line': 1})
    r = FX.annotate(u)
    assert r['binding']['pre']['status'] == 'UNKNOWN'
    assert r['rolled_back']['status'] == 'UNKNOWN' and r['rolled_back']['unknown_reason'] == 'copy_conflict'


def test_a_snapshot_outside_the_ambiguous_candidates_is_a_contradiction(tmp_path):
    p = tmp_path / 'p'
    c = mkrun(p, 'RUN_C', drc=12, timing=T())                      # records the failure, other digests
    for row in (c / 'stage_artifact_manifest.jsonl',):
        row.write_text(row.read_text().replace(FX.SHA['cts'], '9' * 64))
    dc = put_def(c, 'DEF C\n')
    u, _ = case(p, snap={'pre': {'layout': {'run_tag': 'RUN_C', 'def_digest': dc}}})
    r = check_verifier(tmp_path, u)
    assert r['binding']['pre']['unknown_reason'] == 'contradicted_binding'
    assert {x['run'] for x in r['binding']['pre']['candidates']} >= {'RUN_A', 'RUN_A2', 'RUN_C'}


def test_a_snapshot_disagreeing_with_a_known_pre_makes_it_unknown(tmp_path):
    p = tmp_path / 'p'
    a2 = mkrun(p, 'RUN_A2', drc=12, timing=T())
    for row in (a2 / 'stage_artifact_manifest.jsonl',):              # not an anchor candidate
        row.write_text(row.read_text().replace(FX.SHA['cts'], '9' * 64))
    d2 = put_def(a2, 'DEF A2\n')
    u, _ = case(p, ambiguous=False, snap={'pre': {'layout': {'run_tag': 'RUN_A2', 'def_digest': d2}}})
    r = check_verifier(tmp_path, u)
    assert r['binding']['pre']['status'] == 'UNKNOWN' and r['binding']['pre']['unknown_reason'] == 'contradicted_binding'


def test_a_snapshot_agreeing_with_a_known_pre_is_recorded_as_corroboration(tmp_path):
    p = tmp_path / 'p'
    u, _ = case(p, ambiguous=False)
    r = check_verifier(tmp_path, u)
    assert r['binding']['pre']['run'] == 'RUN_A'
    assert r['binding']['pre']['evidence']['snapshot']['run_tag'] == 'RUN_A'


def test_the_verifier_fails_a_record_whose_snapshot_pre_was_tampered(tmp_path):
    p = tmp_path / 'p'
    u, _ = case(p)
    src, recs = TV.annotate_all(tmp_path, [u])
    recs[0]['binding']['pre']['run'] = 'RUN_A2'
    rep = TV.run_verify(tmp_path, src, recs)
    assert rep['gate'] == 'FAIL' and 'wrong_binding' in TV.kinds(rep)
