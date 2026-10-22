"""ann-v2 never reads unconstrained timing (slack 1e+39: no constrained path) as a measured
clean check, and a run with unconstrained timing is not admitted to history
(user decision 2026-10-18: repair unconstrained-timing acceptance, labelling and admission).
"""
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'memory'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_ann_v2_fields as FX  # noqa: E402
import test_ann_v2_verify as TV  # noqa: E402
from ann_v2 import fields as FD  # noqa: E402

mkrun, T = FX.mkrun, FX.T
UNC = 1e39


def test_unconstrained_slack_is_not_measured_and_is_flagged(tmp_path):
    d = mkrun(tmp_path / 'p', 'RUN_A', timing=T(ws=UNC, hold=UNC, slew=2))
    m = FD.measure(d)
    assert m['setup']['measured'] is False and m['hold']['measured'] is False
    assert m['constraint']['timing_unconstrained'] is True
    assert m['drv']['measured'] is True and m['drv']['present'] is True    # DRV limits need no clock
    m2 = FD.measure(mkrun(tmp_path / 'q', 'RUN_A', timing=T()))
    assert m2['constraint']['timing_unconstrained'] is False


def test_a_post_run_that_lost_its_clock_is_a_new_failure_unknown(tmp_path):
    p = tmp_path / 'p'
    FX.drc_pair(p, pre_t=T(ws=0.4), post_t=T(ws=UNC, hold=UNC))
    r = FX.annotate(FX.unit(p, before_categories={'li.3': {'count': 12}}))
    assert r['new_failures']['setup'] == 'UNKNOWN' and r['positive'] is False


def unconstrained_positive(tmp_path):
    """A unit that is positive by the single-step gate (setup not measured before) but whose
    post run has unconstrained timing."""
    p = tmp_path / 'p'
    FX.mkrun(p, 'RUN_A', [('synth', 0), ('floorplan', 0), ('place', 2)], error='FLW-0024')
    FX.mkrun(p, 'RUN_B', [('floorplan', 0), ('place', 0), ('cts', 0), ('route', 0), ('finish', 0)],
             reason=FX.why('core_util_relief', 'floorplan'), from_stage='floorplan',
             reused={'synth': 'RUN_A'}, timing=T(ws=UNC, hold=UNC))
    u = FX.unit(p, check='orfs_stage', vclass='place', strategy='core_util_relief', from_stage='floorplan',
                situation={'check': 'orfs_stage', 'violation_class': 'place', 'error_code': 'FLW-0024'})
    return p, u


def test_a_positive_with_unconstrained_timing_is_not_admitted(tmp_path):
    p, u = unconstrained_positive(tmp_path)
    r = FX.annotate(u)
    assert r['positive'] is True                                  # the single-step evidence stands
    assert r['admission'] == {'status': 'NOT_ADMITTED', 'reasons': ['timing_unconstrained']}


def test_a_clean_positive_is_admitted(tmp_path):
    p = tmp_path / 'p'
    FX.drc_pair(p)
    r = FX.annotate(FX.unit(p, before_categories={'li.3': {'count': 12}}))
    assert r['positive'] is True and r['admission'] == {'status': 'ADMITTED', 'reasons': []}


def test_the_verifier_rederives_admission_and_fails_a_tampered_one(tmp_path):
    p, u = unconstrained_positive(tmp_path)
    src, recs = TV.annotate_all(tmp_path, [u])
    rep = TV.run_verify(tmp_path, src, recs)
    assert rep['gate'] == 'PASS', rep['findings']
    r = copy.deepcopy(recs[0])
    r['admission'] = {'status': 'ADMITTED', 'reasons': []}
    assert 'admission_mismatch' in TV.kinds(TV.run_verify(tmp_path, src, [r]))


def test_the_verifier_does_not_read_unconstrained_slack_as_clean(tmp_path):
    import ann_v2.verify as V
    d = mkrun(tmp_path / 'p', 'RUN_A', timing=T(ws=UNC, hold=UNC))
    run = V.read_run(d)
    assert V.slack_state(run, 'setup') == {'measured': False, 'unconstrained': True}
    assert V.timing_unconstrained(run) is True
