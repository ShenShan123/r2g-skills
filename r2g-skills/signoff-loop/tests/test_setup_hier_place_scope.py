import pytest
import diagnose_signoff_fix as dsf
import engineer_loop as loop
import knowledge_db
import recipe_lifecycle
import setup_scope


def candidate(wns=-1, platform='sky130hd', area='1', clean=True):
    plan = dsf._timing_plan({'tier': 'moderate', 'wns_ns': wns, 'wns': wns},
                           {'PLATFORM': platform, 'ABC_AREA': area}, set(), routing_clean=clean)
    return next((s for s in plan['strategies'] if s['id'] == setup_scope.STRATEGY), None)


@pytest.mark.parametrize('wns', [-2.69817, -.932676, -3.0, -.01])
def test_combo_keeps_clock_and_remains_gated(wns):
    s = candidate(wns)
    assert s['config_edits'] == setup_scope.EDITS
    assert s['sdc_edits'] == {} and s['rerun_from'] == 'synth'
    assert s['requires_ab_promotion']
    assert dsf._live_auto_strategy({'strategies': [s]}) is None
    assert loop._symptom_check(None, None, setup_scope.STRATEGY) == 'timing'


@pytest.mark.parametrize('kwargs', [dict(wns=0), dict(wns=-3.01), dict(wns=float('nan')),
                                  dict(platform='asap7'), dict(area='0'), dict(clean=False)])
def test_outside_scope_not_emitted(kwargs):
    assert candidate(**kwargs) is None


def test_exact_negative_verdict_beats_scope_promotion(tmp_path):
    conn = knowledge_db.connect(tmp_path / 'knowledge.sqlite')
    knowledge_db.ensure_schema(conn)
    recipe_lifecycle.enqueue_candidate(conn, **setup_scope.KEY)
    recipe_lifecycle.promote(conn, evidence='unit_test_fixture', **setup_scope.KEY)
    exact = dict(symptom_id='original_severity', design_class='crypto/large',
                 platform='sky130hd', strategy=setup_scope.STRATEGY)
    args = {k: v for k, v in exact.items() if k != 'strategy'}
    assert dsf._lifecycle_status_with_scope_transfer(conn, recipe_lifecycle,
           strategy=candidate(), **args) == ('promoted', 'validated_setup_scope')
    recipe_lifecycle.enqueue_candidate(conn, **exact)
    assert dsf._lifecycle_status_with_scope_transfer(conn, recipe_lifecycle,
           strategy=candidate(), **args) == ('candidate', 'exact')
    conn.close()


def test_scope_planner_preserves_severity_and_filters_controls(tmp_path):
    import json
    import ab_runner
    import ingest_run
    import symptom
    conn = knowledge_db.connect(tmp_path / 'plan.sqlite')
    knowledge_db.ensure_schema(conn)
    paths = set()
    for i, (tier, wns) in enumerate([('severe', -2.7), ('moderate', -.9), ('clean', 1.0)]):
        p = tmp_path / ('subject' + str(i))
        (p / 'reports').mkdir(parents=True)
        (p / 'reports/route.json').write_text(json.dumps({'status': 'clean'}))
        paths.add(str(p))
        sig = symptom.canonical_signature('timing', tier)
        sid = symptom.symptom_id(sig)
        ingest_run._upsert_symptom(conn, sig, sid)
        conn.execute('INSERT INTO runs(run_id,project_path,design_name,platform,ingested_at,abc_area,'
                     'orfs_status,wns_ns,drc_status,lvs_status) VALUES(?,?,?,?,?,?,?,?,?,?)',
                     (str(i), str(p), p.name, 'sky130hd', 'test', 1, 'pass', wns, 'clean', 'clean'))
        conn.execute('INSERT INTO run_violations(run_id,symptom_id) VALUES(?,?)', (str(i), sid))
    before = list(conn.execute('SELECT run_id,symptom_id FROM run_violations'))
    plan = ab_runner.plan_trial(conn, **setup_scope.KEY, allowed_project_paths=paths)
    assert [d['design_name'] for d in plan['designs']] == ['subject0', 'subject1']
    assert plan['match_level'] == 'measured_setup_scope'
    assert list(conn.execute('SELECT run_id,symptom_id FROM run_violations')) == before
    assert ab_runner.plan_trial(conn, **setup_scope.KEY,
           allowed_project_paths={str(tmp_path / 'subject0')}) is None
    conn.close()
