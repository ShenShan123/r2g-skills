"""ann-v2 run binding (spec r2g_T2_ann_v2_annotation_spec_DRAFT_20261017.md §4).

Ownership, not proximity: a binding is KNOWN only on a unique explicit (E1) link that no
E2 check contradicts. Time order, an equal configuration and ordinal pairing never decide.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'memory'))
from ann_v2 import binding as B  # noqa: E402

SHA = {s: (s[0] * 64) for s in ('synth', 'floorplan', 'place', 'cts', 'route')}


def run(project, tag, stages, *, reason=None, from_stage=None, reused=None, digests=None,
        drc=None, eff_config=None, ts=0):
    """A backend run: stages=[(stage, status)], digests for stages it ran, reused digests."""
    d = Path(project) / 'backend' / tag
    d.mkdir(parents=True)
    (d / 'stage_log.jsonl').write_text(''.join(
        json.dumps({'stage': s, 'status': st, 'ts_start': ts + i}) + '\n' for i, (s, st) in enumerate(stages)))
    (d / 'stage_artifact_manifest.jsonl').write_text(''.join(
        json.dumps({'stage': s, 'status': 0, 'run_tag': tag, 'sha256': (digests or SHA)[s]}) + '\n'
        for s, st in stages if st == 0))
    if reason is not None:
        (d / 'resume_meta.json').write_text(json.dumps({
            'from_stage': from_stage, 'reason': reason,
            'parent_lineage': {s: {'sha256': sha, 'parent_run': p, 'verified': True}
                               for s, (sha, p) in (reused or {}).items()}}))
    if drc is not None:
        (d / 'drc').mkdir()
        (d / 'drc' / 'drc_result.json').write_text(json.dumps(
            {'status': 'clean' if drc == 0 else 'fail', 'violations': str(drc)}))
    if eff_config is not None:
        (d / 'effective-config.json').write_text(json.dumps({     # the real receipt shape
            'status': 'PASS', 'effective': {k: {'origin': 'file', 'value': v} for k, v in eff_config.items()}}))
    return tag


def why(strategy, stage):
    return f'signoff fix: strategy={strategy} rerun_from={stage} (config edit)'


def attempt(project, **kw):
    a = {'projects': [str(project)], 'session': 's1', 'iter': 1, 'strategy': 'route_relief',
         'from_stage': 'floorplan', 'check': 'orfs_stage', 'violation_class': 'place',
         'config_before': {'CORE_UTILIZATION': '60'}, 'config_delta': {'CORE_UTILIZATION': '50'},
         'rolled_back': False}
    a.update(kw)
    return a


def place_fail(project, tag, ts=0, synth_from=None):
    """A run that ran synth+floorplan and aborted at place (or reused synth)."""
    if synth_from:
        return run(project, tag, [('floorplan', 0), ('place', 2)], reason='stage-scoped resume',
                   from_stage='floorplan', reused={'synth': (SHA['synth'], synth_from)}, ts=ts)
    return run(project, tag, [('synth', 0), ('floorplan', 0), ('place', 2)], ts=ts)


def post(project, tag, strategy='route_relief', ts=100, parent='RUN_A', eff=None):
    return run(project, tag, [('floorplan', 0), ('place', 0), ('cts', 0), ('route', 0)],
               reason=why(strategy, 'floorplan'), from_stage='floorplan',
               reused={'synth': (SHA['synth'], parent)}, ts=ts, eff_config=eff)


# 1. a unique explicit association -> KNOWN ----------------------------------------------

def test_a_unique_explicit_link_binds_post_and_pre(tmp_path):
    p = tmp_path / 'proj'
    place_fail(p, 'RUN_A', ts=0)
    post(p, 'RUN_B')
    a = attempt(p)
    runs = B.load_runs(a['projects'])
    post_b = B.bind_post(a, runs)
    assert post_b['status'] == 'KNOWN' and post_b['run'] == 'RUN_B' and post_b['evidence']['e1']
    pre_b = B.bind_pre(a, runs, post_b)
    assert pre_b['status'] == 'KNOWN' and pre_b['run'] == 'RUN_A'


def test_iteration_two_pre_is_the_bound_post_of_iteration_one(tmp_path):
    p = tmp_path / 'proj'
    place_fail(p, 'RUN_A')
    run(p, 'RUN_B', [('floorplan', 0), ('place', 2)], reason=why('route_relief', 'floorplan'),
        from_stage='floorplan', reused={'synth': (SHA['synth'], 'RUN_A')}, ts=100)
    run(p, 'RUN_C', [('floorplan', 0), ('place', 0)], reason=why('density_relief', 'floorplan'),
        from_stage='floorplan', reused={'synth': (SHA['synth'], 'RUN_A')}, ts=200)
    a1 = attempt(p)
    a2 = attempt(p, iter=2, strategy='density_relief')
    runs = B.load_runs(a1['projects'])
    b1 = B.bind_post(a1, runs)
    b2 = B.bind_post(a2, runs)
    pre2 = B.bind_pre(a2, runs, b2, prev=(a1, b1, B.bind_pre(a1, runs, b1)))
    assert (b2['run'], pre2['status'], pre2['run']) == ('RUN_C', 'KNOWN', 'RUN_B')


# 2. no link, several candidates, conflicting evidence -> UNKNOWN ------------------------

def test_no_explicit_link_is_unknown_even_when_one_run_is_nearest_in_time(tmp_path):
    p = tmp_path / 'proj'
    place_fail(p, 'RUN_A', ts=0)
    run(p, 'RUN_B', [('floorplan', 0), ('place', 0)], reason='stage-scoped resume: config edit',
        from_stage='floorplan', reused={'synth': (SHA['synth'], 'RUN_A')}, ts=100)
    b = B.bind_post(attempt(p), B.load_runs([str(p)]))
    assert b['status'] == 'UNKNOWN' and b['unknown_reason'] == 'no_explicit_link' and b['run'] is None
    assert b['candidates'] == []


def test_two_explicit_candidates_are_ambiguous_and_both_kept(tmp_path):
    p = tmp_path / 'proj'
    place_fail(p, 'RUN_A')
    post(p, 'RUN_B', ts=100)
    post(p, 'RUN_C', ts=200)            # the same strategy again (another session)
    b = B.bind_post(attempt(p), B.load_runs([str(p)]))
    assert b['status'] == 'UNKNOWN' and b['unknown_reason'] == 'ambiguous_binding'
    assert sorted(c['run'] for c in b['candidates']) == ['RUN_B', 'RUN_C']


def test_an_e2_contradiction_of_the_only_candidate_is_unknown(tmp_path):
    p = tmp_path / 'proj'
    place_fail(p, 'RUN_A')
    post(p, 'RUN_B', eff={'CORE_UTILIZATION': '45'})            # not config_before + delta (50)
    b = B.bind_post(attempt(p), B.load_runs([str(p)]))
    assert b['status'] == 'UNKNOWN' and b['unknown_reason'] == 'contradicted_binding'
    assert b['candidates'][0]['e2']['config'] == 'contradicted'


def test_e2_separates_two_explicit_candidates(tmp_path):
    p = tmp_path / 'proj'
    place_fail(p, 'RUN_A')
    post(p, 'RUN_B', eff={'CORE_UTILIZATION': '45'})
    post(p, 'RUN_C', ts=200, eff={'CORE_UTILIZATION': '50'})
    b = B.bind_post(attempt(p), B.load_runs([str(p)]))
    assert (b['status'], b['run']) == ('KNOWN', 'RUN_C')


def test_e2_unavailable_is_recorded_never_a_pass(tmp_path):
    p = tmp_path / 'proj'
    place_fail(p, 'RUN_A')
    post(p, 'RUN_B')                                             # SOURCE-v1 era: no effective config
    b = B.bind_post(attempt(p), B.load_runs([str(p)]))
    assert b['status'] == 'KNOWN' and b['evidence']['e2']['config'] == 'unavailable'


def test_disagreeing_copies_of_one_run_are_a_copy_conflict(tmp_path):
    p1, p2 = tmp_path / 'root1' / 'proj', tmp_path / 'root2' / 'proj'
    for p in (p1, p2):
        place_fail(p, 'RUN_A')
    post(p1, 'RUN_B')
    post(p2, 'RUN_B', strategy='density_relief')                 # same tag, different content
    b = B.bind_post(attempt(p1, projects=[str(p1), str(p2)]), B.load_runs([str(p1), str(p2)]))
    assert b['status'] == 'UNKNOWN' and b['unknown_reason'] == 'copy_conflict'


def test_identical_copies_of_one_run_are_one_run(tmp_path):
    p1, p2 = tmp_path / 'root1' / 'proj', tmp_path / 'root2' / 'proj'
    for p in (p1, p2):
        place_fail(p, 'RUN_A')
        post(p, 'RUN_B')
    b = B.bind_post(attempt(p1, projects=[str(p1), str(p2)]), B.load_runs([str(p1), str(p2)]))
    assert (b['status'], b['run']) == ('KNOWN', 'RUN_B')


# 3. a nearer but unrelated run is never selected ----------------------------------------

def test_a_later_unrelated_run_is_not_selected(tmp_path):
    p = tmp_path / 'proj'
    place_fail(p, 'RUN_A', ts=0)
    post(p, 'RUN_B', ts=100)
    run(p, 'RUN_Z', [('floorplan', 0), ('place', 0)], reason=why('pin_side_rebalance', 'floorplan'),
        from_stage='floorplan', reused={'synth': (SHA['synth'], 'RUN_A')}, ts=101)   # nearest after
    b = B.bind_post(attempt(p), B.load_runs([str(p)]))
    assert (b['status'], b['run']) == ('KNOWN', 'RUN_B')


def test_rerun_stage_must_match_not_only_the_strategy(tmp_path):
    p = tmp_path / 'proj'
    place_fail(p, 'RUN_A')
    run(p, 'RUN_B', [('place', 0)], reason=why('route_relief', 'place'), from_stage='place',
        reused={'synth': (SHA['synth'], 'RUN_A'), 'floorplan': (SHA['floorplan'], 'RUN_A')})
    b = B.bind_post(attempt(p), B.load_runs([str(p)]))
    assert b['unknown_reason'] == 'no_explicit_link'


# 4. rollback / restore / recovery runs are never the action's result -------------------

def test_a_restore_run_after_rollback_is_not_the_post_action_run(tmp_path):
    p = tmp_path / 'proj'
    place_fail(p, 'RUN_A', ts=0)
    post(p, 'RUN_B', ts=100)
    run(p, 'RUN_R', [('floorplan', 0), ('place', 2)], reason='task-entry restoration (rollback)',
        from_stage='floorplan', reused={'synth': (SHA['synth'], 'RUN_A')}, ts=200)
    a = attempt(p, rolled_back=True)
    b = B.bind_post(a, B.load_runs([str(p)]))
    assert (b['status'], b['run']) == ('KNOWN', 'RUN_B')


def test_after_a_rolled_back_iteration_the_next_pre_is_the_restored_state(tmp_path):
    p = tmp_path / 'proj'
    place_fail(p, 'RUN_A')
    post(p, 'RUN_B', ts=100)                                     # iteration 1, rolled back
    run(p, 'RUN_C', [('floorplan', 0), ('place', 0)], reason=why('density_relief', 'floorplan'),
        from_stage='floorplan', reused={'synth': (SHA['synth'], 'RUN_A')}, ts=200)
    a1 = attempt(p, rolled_back=True)
    a2 = attempt(p, iter=2, strategy='density_relief')
    runs = B.load_runs([str(p)])
    b1 = B.bind_post(a1, runs)
    pre1 = B.bind_pre(a1, runs, b1)
    pre2 = B.bind_pre(a2, runs, B.bind_post(a2, runs), prev=(a1, b1, pre1))
    assert pre1['run'] == 'RUN_A' and (pre2['status'], pre2['run']) == ('KNOWN', 'RUN_A')


def test_a_previous_unbound_iteration_leaves_the_next_pre_unknown(tmp_path):
    p = tmp_path / 'proj'
    place_fail(p, 'RUN_A')
    run(p, 'RUN_C', [('floorplan', 0), ('place', 0)], reason=why('density_relief', 'floorplan'),
        from_stage='floorplan', reused={'synth': (SHA['synth'], 'RUN_A')}, ts=200)
    a1, a2 = attempt(p), attempt(p, iter=2, strategy='density_relief')
    runs = B.load_runs([str(p)])
    b1 = B.bind_post(a1, runs)                                   # no run named route_relief
    pre2 = B.bind_pre(a2, runs, B.bind_post(a2, runs), prev=(a1, b1, B.bind_pre(a1, runs, b1)))
    assert pre2['status'] == 'UNKNOWN' and pre2['unknown_reason'] == 'predecessor_unbound'


# 5. an artifact ancestor is not the failing pre-action run -----------------------------

def test_the_artifact_ancestor_is_not_taken_as_the_failing_run(tmp_path):
    p = tmp_path / 'proj'
    run(p, 'RUN_ANC', [('synth', 0), ('floorplan', 0), ('place', 0), ('cts', 0), ('route', 0)])  # clean
    place_fail(p, 'RUN_F', ts=50, synth_from='RUN_ANC')          # reused synth, failed at place
    post(p, 'RUN_B', parent='RUN_ANC')                           # parent_run names the ANCESTOR
    a = attempt(p)
    runs = B.load_runs([str(p)])
    pre_b = B.bind_pre(a, runs, B.bind_post(a, runs))
    assert (pre_b['status'], pre_b['run']) == ('KNOWN', 'RUN_F')


def test_several_failing_runs_with_the_anchor_digest_are_ambiguous(tmp_path):
    p = tmp_path / 'proj'
    place_fail(p, 'RUN_F1')
    place_fail(p, 'RUN_F2', ts=10, synth_from='RUN_F1')
    post(p, 'RUN_B', parent='RUN_F1')
    a = attempt(p)
    runs = B.load_runs([str(p)])
    pre_b = B.bind_pre(a, runs, B.bind_post(a, runs))
    assert pre_b['status'] == 'UNKNOWN' and pre_b['unknown_reason'] == 'ambiguous_binding'
    assert sorted(c['run'] for c in pre_b['candidates']) == ['RUN_F1', 'RUN_F2']


def test_a_run_without_recorded_failure_evidence_is_not_a_pre_candidate(tmp_path):
    p = tmp_path / 'proj'
    run(p, 'RUN_A', [('synth', 0), ('floorplan', 0), ('place', 0), ('cts', 0), ('route', 0)])
    post(p, 'RUN_B', parent='RUN_A')
    a = attempt(p)
    runs = B.load_runs([str(p)])
    pre_b = B.bind_pre(a, runs, B.bind_post(a, runs))
    assert pre_b['status'] == 'UNKNOWN' and pre_b['unknown_reason'] == 'no_explicit_link'


def test_an_unbound_post_leaves_the_iteration_one_pre_unknown(tmp_path):
    p = tmp_path / 'proj'
    place_fail(p, 'RUN_A')
    a = attempt(p)
    runs = B.load_runs([str(p)])
    pre_b = B.bind_pre(a, runs, B.bind_post(a, runs))
    assert pre_b['status'] == 'UNKNOWN' and pre_b['unknown_reason'] == 'anchor_unbound'


def test_drc_pre_candidates_need_recorded_violations(tmp_path):
    p = tmp_path / 'proj'
    full = [('synth', 0), ('floorplan', 0), ('place', 0), ('cts', 0), ('route', 0)]
    run(p, 'RUN_CLEAN', full, drc=0)
    run(p, 'RUN_DIRTY', full, drc=12, ts=10)
    run(p, 'RUN_NODRC', full, ts=20)                             # never checked: not evidence
    run(p, 'RUN_B', [('route', 0)], reason=why('antenna_diode_iters', 'route'), from_stage='route',
        reused={s: (SHA[s], 'RUN_DIRTY') for s in ('synth', 'floorplan', 'place', 'cts')}, ts=100)
    a = attempt(p, strategy='antenna_diode_iters', from_stage='route', check='drc', violation_class='li.3')
    runs = B.load_runs([str(p)])
    pre_b = B.bind_pre(a, runs, B.bind_post(a, runs))
    assert (pre_b['status'], pre_b['run']) == ('KNOWN', 'RUN_DIRTY')


# 6. ordinal pairing never confirms, even with equal counts -----------------------------

def test_equal_counts_with_a_missing_and_a_duplicate_run_still_do_not_bind(tmp_path):
    p = tmp_path / 'proj'
    place_fail(p, 'RUN_A')
    # Two attempts of route_relief in two sessions. The first attempt's run is MISSING;
    # a crash-resume DUPLICATE of the second carries the same reason. Counts are equal (2 = 2).
    post(p, 'RUN_X2', ts=300)
    post(p, 'RUN_X2DUP', ts=301)
    a1, a2 = attempt(p, session='s1'), attempt(p, session='s2')
    runs = B.load_runs([str(p)])
    for a in (a1, a2):
        b = B.bind_post(a, runs)
        assert b['status'] == 'UNKNOWN' and b['unknown_reason'] == 'ambiguous_binding'
    adv = B.ordinal_advisory([a1, a2], runs)
    assert adv['advisory_only'] is True and adv['counted'] is False
    assert len(adv['pairs']) == 2                                # offered for manual review only
    assert all(p_['status'] == 'ADVISORY' for p_ in adv['pairs'])


def test_ordinal_advisory_offers_nothing_when_counts_differ(tmp_path):
    p = tmp_path / 'proj'
    place_fail(p, 'RUN_A')
    post(p, 'RUN_B', ts=100)
    adv = B.ordinal_advisory([attempt(p, session='s1'), attempt(p, session='s2')], B.load_runs([str(p)]))
    assert adv['pairs'] == [] and adv['counted'] is False


def test_a_binding_never_reads_ordinal_or_time_evidence(tmp_path):
    """E3 is recorded for the audit but has no field that can change a status."""
    p = tmp_path / 'proj'
    place_fail(p, 'RUN_A')
    post(p, 'RUN_B', ts=100)
    post(p, 'RUN_C', ts=100000)
    b = B.bind_post(attempt(p), B.load_runs([str(p)]))
    assert b['status'] == 'UNKNOWN' and all('e3' in c for c in b['candidates'])


# uniqueness both ways: one run for the attempt AND one attempt that can claim the run ---

def fix_log(project, rows):
    r = Path(project) / 'reports'
    r.mkdir(parents=True, exist_ok=True)
    (r / 'fix_log.jsonl').write_text(''.join(json.dumps(x) + '\n' for x in rows))


def test_a_single_surviving_run_claimed_by_two_attempts_is_ambiguous(tmp_path):
    """Session s1's run is MISSING; the only strategy-named run belongs to s2. Each attempt
    sees exactly one candidate, so run-side uniqueness alone would bind s1 to s2's run."""
    p = tmp_path / 'proj'
    place_fail(p, 'RUN_A')
    post(p, 'RUN_S2', ts=300)
    fix_log(p, [{'fix_session_id': s, 'iter': 1, 'strategy': 'route_relief', 'from_stage': 'floorplan'}
                for s in ('s1', 's2')])
    runs = B.load_runs([str(p)])
    for s in ('s1', 's2'):
        b = B.bind_post(attempt(p, session=s), runs)
        assert b['status'] == 'UNKNOWN' and b['unknown_reason'] == 'ambiguous_binding', s
        assert b['claimants'] == [['s1', 1], ['s2', 1]]


def test_the_attempts_own_row_counts_once_across_copies(tmp_path):
    p1, p2 = tmp_path / 'root1' / 'proj', tmp_path / 'root2' / 'proj'
    for p in (p1, p2):
        place_fail(p, 'RUN_A')
        post(p, 'RUN_B')
        fix_log(p, [{'fix_session_id': 's1', 'iter': 1, 'strategy': 'route_relief', 'from_stage': 'floorplan'}])
    b = B.bind_post(attempt(p1, projects=[str(p1), str(p2)]), B.load_runs([str(p1), str(p2)]))
    assert (b['status'], b['run']) == ('KNOWN', 'RUN_B')


def test_another_strategy_or_stage_is_not_a_claimant(tmp_path):
    p = tmp_path / 'proj'
    place_fail(p, 'RUN_A')
    post(p, 'RUN_B')
    fix_log(p, [{'fix_session_id': 's1', 'iter': 1, 'strategy': 'route_relief', 'from_stage': 'floorplan'},
                {'fix_session_id': 's1', 'iter': 2, 'strategy': 'density_relief', 'from_stage': 'floorplan'},
                {'fix_session_id': 's3', 'iter': 1, 'strategy': 'route_relief', 'from_stage': 'place'}])
    b = B.bind_post(attempt(p), B.load_runs([str(p)]))
    assert (b['status'], b['run']) == ('KNOWN', 'RUN_B')
