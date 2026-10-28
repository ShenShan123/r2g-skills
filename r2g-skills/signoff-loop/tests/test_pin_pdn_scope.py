import copy
import json
import sqlite3
import pytest
import pin_pdn_scope as scope
import recipe_lifecycle
import diagnose_signoff_fix as dsf


def strategy():
    return dict(id=scope.KEY['strategy'],config_edits={'PRE_IO_PLACEMENT_TCL':str(scope.HOOK)},
        rerun_from='floorplan',recheck='drc',pdn_scope_evidence=dict(generated_default_pdn_config=True),
        geometry_evidence=dict(rule='m3.2',units_per_um=1000,required_spacing_um=0.3,
            marker_count=2,covered_marker_count=2,def_sha256='a'*64,report_sha256='b'*64,
            witnesses=[dict(layer='met3',power_net='VDD',gap_upper_bound_um=0.025,marker_indices=[0,1])]))


def database():
    conn=sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE recipe_status(symptom_id,design_class,platform,strategy,status,provenance)')
    conn.execute('CREATE TABLE ab_trials(symptom_id,design_class,platform,strategy,verdict,metrics_json)')
    conn.execute('CREATE TABLE runs(run_id,platform,design_family,orfs_status,drc_status,lvs_status)')
    conn.execute('INSERT INTO recipe_status VALUES(?,?,?,?,?,?)',(*scope.KEY.values(),'promoted','unit_test_only'))
    for family in ('first','second'):
        samples=lambda role:[dict(repeat=i,is_success=role=='B',run_id=f'{family}_{role}_{i}',source_repository=family,platform='sky130hd') for i in (0,1)]
        m=dict(provenance_complete=True,source='durable_copy_of_verified_physical_AB',arm_a_samples=samples('A'),arm_b_samples=samples('B'))
        for s in m['arm_a_samples']+m['arm_b_samples']:
            conn.execute('INSERT INTO runs VALUES(?,?,?,?,?,?)',(s['run_id'],'sky130hd',family,'partial','clean' if s['is_success'] else 'fail','clean'))
        conn.execute('INSERT INTO ab_trials VALUES(?,?,?,?,?,?)',(*scope.KEY.values(),'win',json.dumps(m)))
    return conn


def test_current_geometry_and_two_real_trial_groups_required():
    conn=database();s=strategy()
    assert scope.validated(conn,recipe_lifecycle,s)
    assert dsf._lifecycle_status_with_scope_transfer(conn,recipe_lifecycle,
        symptom_id=scope.KEY['symptom_id'],design_class='logic/large',platform='sky130hd',strategy=s)==('promoted','validated_pin_pdn_geometry')
    assert conn.execute("SELECT COUNT(*) FROM recipe_status WHERE design_class='*'").fetchone()[0]==0
    conn.execute('DELETE FROM ab_trials WHERE rowid=2')
    assert not scope.validated(conn,recipe_lifecycle,s)
    conn.close()


@pytest.mark.parametrize('problem',['custom_profile','insufficient_coverage','missing_witness','invalid_gap','nonfinite_gap','wrong_layer','wrong_supply','unbound_report','extra_edit'])
def test_unsupported_or_incomplete_current_witness_cannot_transfer(problem):
    s=strategy();e=s['geometry_evidence']
    if problem=='custom_profile':s['pdn_scope_evidence']['generated_default_pdn_config']=False
    if problem=='insufficient_coverage':e['covered_marker_count']=1
    if problem=='missing_witness':e['witnesses']=[]
    if problem=='invalid_gap':e['witnesses'][0]['gap_upper_bound_um']=0.3
    if problem=='nonfinite_gap':e['witnesses'][0]['gap_upper_bound_um']=float('nan')
    if problem=='wrong_layer':e['witnesses'][0]['layer']='met2'
    if problem=='wrong_supply':e['witnesses'][0]['power_net']='custom'
    if problem=='unbound_report':e.pop('report_sha256')
    if problem=='extra_edit':s['config_edits']['CORE_UTILIZATION']='1'
    assert not scope.matches(s)


@pytest.mark.parametrize('status',['shadow','candidate','parked'])
def test_exact_target_class_veto_remains(status):
    conn=database();target={**scope.KEY,'design_class':'logic/large'}
    conn.execute('INSERT INTO recipe_status VALUES(?,?,?,?,?,?)',(*target.values(),status,'manual_review'))
    assert dsf._lifecycle_status_with_scope_transfer(conn,recipe_lifecycle,
        symptom_id=target['symptom_id'],design_class=target['design_class'],platform=target['platform'],strategy=strategy())==(status,'exact')
    conn.close()


def test_untested_learner_may_transfer_but_tested_candidate_cannot():
    conn=database();target={**scope.KEY,'design_class':'logic/large'}
    conn.execute('INSERT INTO recipe_status VALUES(?,?,?,?,?,?)',(*target.values(),'candidate','learner_diff'))
    resolve=lambda:dsf._lifecycle_status_with_scope_transfer(conn,recipe_lifecycle,
        symptom_id=target['symptom_id'],design_class=target['design_class'],platform=target['platform'],strategy=strategy())
    assert resolve()==('promoted','validated_pdn_over_untested_candidate')
    conn.execute('INSERT INTO ab_trials VALUES(?,?,?,?,?,?)',(*target.values(),'inconclusive','{}'))
    assert resolve()==('candidate','exact')
    conn.close()
