"""Route-abort step effect compares the error code and the GRT overflow, not only the stage
(labels v2, ann-v2-dev.6; the frozen v1 labels are unchanged).

Audit 2026-10-18 of the 8 "bound NO_CHANGE GRT-0116" learning-side units: on des_area/u77
route_relief (util 77 -> 61) cleared the congestion (overflow 96 -> 0, global route clean)
and detailed routing then timed out (rc 124); on picorv32/u84 (util 84 -> 76) overflow rose
2267 -> 6027. Both were labelled NO_CHANGE because both runs aborted in the `route` stage.
"""
import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'memory'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_ann_v2_fields as FX  # noqa: E402
import test_ann_v2_verify as TV  # noqa: E402
from ann_v2 import fields as FD  # noqa: E402

PRE_STAGES = [('synth', 0), ('floorplan', 0), ('place', 0), ('cts', 0), ('route', 2)]


def grt_log(run_dir, total, error=True):
    (run_dir / 'logs' / '5_1_grt.log').write_text(
        'Layer  Resource  Demand  Usage (%)  Max H / Max V / Total Congestion\n'
        f'Total            65306         59881           91.69%            36 / 41 / {total}\n'
        + ('[ERROR GRT-0116] Global routing finished with congestion.\n' if error else
           '[INFO GRT-0018] Total wirelength: 652733 um\n'))


def case(tmp_path, post_status, post_total, post_error, pre_total=96):
    p = tmp_path / 'p'
    a = FX.mkrun(p, 'RUN_A', PRE_STAGES, error='GRT-0116')
    grt_log(a, pre_total, error=True)
    b = FX.mkrun(p, 'RUN_B', [('floorplan', 0), ('place', 0), ('cts', 0), ('route', post_status)],
                 reason=FX.why('route_relief', 'floorplan'), from_stage='floorplan', reused={'synth': 'RUN_A'},
                 error='GRT-0116' if post_error else None)
    if post_total is not None:
        grt_log(b, post_total, error=post_error)
    u = FX.unit(p, check='orfs_stage', vclass='route', strategy='route_relief', from_stage='floorplan',
                situation={'check': 'orfs_stage', 'violation_class': 'route', 'error_code': 'GRT-0116'})
    return p, u


def test_grt_overflow_and_global_route_state_are_typed_fields(tmp_path):
    p, _ = case(tmp_path, 124, 0, False)
    pre, post = FD.measure(p / 'backend' / 'RUN_A'), FD.measure(p / 'backend' / 'RUN_B')
    assert pre['stage']['fields']['grt_overflow']['value'] == 96
    assert pre['stage']['fields']['global_route_clean']['value'] is False
    assert post['stage']['fields']['grt_overflow']['value'] == 0
    assert post['stage']['fields']['global_route_clean']['value'] is True
    assert all(s['sha256'] for s in post['stage']['fields']['grt_overflow']['source'])


def test_congestion_cleared_then_detail_route_timeout_is_cleared_with_a_new_failure(tmp_path):
    p, u = case(tmp_path, 124, 0, False)
    r = FX.annotate(u)
    assert r['step_effect'] == {'stage_abort': 'CLEARED'}
    assert r['new_failures']['stage'] == 'KNOWN-present' and r['positive'] is False


@pytest.mark.parametrize('post_total,effect', [(6027, 'REGRESSED'), (40, 'IMPROVED'), (96, 'NO_CHANGE')])
def test_the_same_grt_abort_is_graded_by_overflow(tmp_path, post_total, effect):
    p, u = case(tmp_path, 2, post_total, True)
    r = FX.annotate(u)
    assert r['step_effect'] == {'stage_abort': effect}
    assert r['new_failures']['stage'] == 'KNOWN-none'


def test_the_same_grt_abort_without_overflow_is_no_change(tmp_path):
    p, u = case(tmp_path, 2, None, True)
    assert FX.annotate(u)['step_effect'] == {'stage_abort': 'NO_CHANGE'}


def test_a_different_route_failure_without_global_route_evidence_is_unknown(tmp_path):
    p, u = case(tmp_path, 124, None, False)
    assert FX.annotate(u)['step_effect'] == {'stage_abort': 'UNKNOWN'}


@pytest.mark.parametrize('post', [(124, 0, False), (2, 6027, True), (2, 40, True)])
def test_the_verifier_agrees_and_fails_a_tampered_route_effect(tmp_path, post):
    p, u = case(tmp_path, *post)
    src, recs = TV.annotate_all(tmp_path, [u])
    rep = TV.run_verify(tmp_path, src, recs)
    assert rep['gate'] == 'PASS', rep['findings']
    r = copy.deepcopy(recs[0])
    r['step_effect'] = {'stage_abort': 'NO_CHANGE' if r['step_effect']['stage_abort'] != 'NO_CHANGE' else 'CLEARED'}
    assert 'effect_mismatch' in TV.kinds(TV.run_verify(tmp_path, src, [r]))
