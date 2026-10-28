import copy
import json
import sqlite3
import pytest
import fixed_area_scope as scope
import recipe_lifecycle
import diagnose_signoff_fix as dsf


def strategy():
    return dict(id=scope.KEY['strategy'],rerun_from='floorplan',recheck='route',
        config_edits={'DIE_AREA':'0 0 125 125','CORE_AREA':'2 2 123 123'},
        fixed_area_scope_evidence=dict(platform='sky130hd',standard_cell_generated_config=True,
                                      original_die='0 0 100 100',original_core='2 2 98 98'),
        geometry_evidence=dict(log_sha256='a'*64,def_sha256='b'*64,standard_cell_count=500))


def database():
    conn=sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE recipe_status(symptom_id,design_class,platform,strategy,status,provenance)')
    conn.execute('CREATE TABLE ab_trials(symptom_id,design_class,platform,strategy,verdict,metrics_json)')
    conn.execute('CREATE TABLE runs(run_id,platform,design_family,orfs_status,drc_status,lvs_status)')
    conn.execute('INSERT INTO recipe_status VALUES(?,?,?,?,?,?)',(*scope.KEY.values(),'promoted','unit_test_only'))
    for family in ('first','second'):
        samples=lambda role:[dict(repeat=i,is_success=role=='B',run_id=f'{family}_{role}_{i}',source_repository=family,platform='sky130hd') for i in (0,1)]
        m=dict(provenance_complete=True,source='durable_copy_of_verified_physical_AB',arm_a_samples=samples('A'),arm_b_samples=samples('B'))
        for sample in m['arm_a_samples']+m['arm_b_samples']:
            ok=sample['is_success']
            conn.execute('INSERT INTO runs VALUES(?,?,?,?,?,?)',(sample['run_id'],'sky130hd',family,'partial' if ok else 'fail','clean' if ok else None,'clean' if ok else None))
        conn.execute('INSERT INTO ab_trials VALUES(?,?,?,?,?,?)',(*scope.KEY.values(),'win',json.dumps(m)))
    return conn


def test_requires_complete_independent_evidence():
    conn=database()
    assert scope.validated(conn,recipe_lifecycle,strategy())
    conn.execute('DELETE FROM ab_trials WHERE rowid=2')
    assert not scope.validated(conn,recipe_lifecycle,strategy())
    conn.close()


@pytest.mark.parametrize('status',[None,'candidate','shadow','parked'])
def test_absent_or_unpromoted_anchor_never_transfers(status):
    conn=database()
    if status is None:conn.execute('DELETE FROM recipe_status')
    else:conn.execute('UPDATE recipe_status SET status=?',(status,))
    assert not scope.validated(conn,recipe_lifecycle,strategy())
    conn.close()


@pytest.mark.parametrize('mutation',['provenance','failed_b','duplicate_family','duplicate_runs','missing_repeat','other_source','loss'])
def test_invalid_trials_reject_transfer(mutation):
    conn=database();m=json.loads(conn.execute('SELECT metrics_json FROM ab_trials WHERE rowid=2').fetchone()[0])
    if mutation=='provenance':m['provenance_complete']=False
    if mutation=='failed_b':m['arm_b_samples'][1]['is_success']=False
    if mutation=='duplicate_family':
        for s in m['arm_a_samples']+m['arm_b_samples']:s['source_repository']='first'
    if mutation=='duplicate_runs':m['arm_b_samples'][1]['run_id']=m['arm_b_samples'][0]['run_id']
    if mutation=='missing_repeat':m['arm_a_samples'].pop()
    if mutation=='other_source':m['source']='handwritten'
    if mutation=='loss':conn.execute("UPDATE ab_trials SET verdict='loss' WHERE rowid=2")
    conn.execute('UPDATE ab_trials SET metrics_json=? WHERE rowid=2',(json.dumps(m),))
    assert not scope.validated(conn,recipe_lifecycle,strategy())
    conn.close()


def test_geometry_and_exact_class_veto():
    conn=database();s=strategy()
    key={**scope.KEY,'design_class':'logic/medium'}
    assert dsf._lifecycle_status_with_scope_transfer(conn,recipe_lifecycle,
        symptom_id=key['symptom_id'],design_class=key['design_class'],platform=key['platform'],strategy=s)==('promoted','validated_fixed_area_geometry')
    conn.execute('INSERT INTO recipe_status VALUES(?,?,?,?,?,?)',(*key.values(),'shadow','unit_test_only'))
    assert dsf._lifecycle_status_with_scope_transfer(conn,recipe_lifecycle,
        symptom_id=key['symptom_id'],design_class=key['design_class'],platform=key['platform'],strategy=s)==('shadow','exact')
    for field in ('sdc_edits','env_edits'):
        bad=copy.deepcopy(s);bad[field]={'bad':'1'};assert not scope.matches(bad)
    bad=copy.deepcopy(s);bad['config_edits']['CORE_AREA']='2 2 120 120';assert not scope.matches(bad)
    bad=copy.deepcopy(s);bad['fixed_area_scope_evidence']['standard_cell_generated_config']=False;assert not scope.matches(bad)
    conn.close()


def test_untested_learner_candidate_has_no_negative_verdict():
    conn=database();key={**scope.KEY,'design_class':'logic/medium'}
    conn.execute('INSERT INTO recipe_status VALUES(?,?,?,?,?,?)',(*key.values(),'candidate','learner_diff'))
    resolve=lambda:dsf._lifecycle_status_with_scope_transfer(conn,recipe_lifecycle,
        symptom_id=key['symptom_id'],design_class=key['design_class'],platform=key['platform'],strategy=strategy())
    assert resolve()==('promoted','validated_fixed_area_over_untested_candidate')
    conn.execute('INSERT INTO ab_trials VALUES(?,?,?,?,?,?)',(*key.values(),'inconclusive','{}'))
    assert resolve()==('candidate','exact')
    conn.close()


def test_untested_candidate_still_requires_validated_anchor():
    conn=database();key={**scope.KEY,'design_class':'logic/medium'}
    conn.execute('INSERT INTO recipe_status VALUES(?,?,?,?,?,?)',(*key.values(),'candidate','learner_diff'))
    conn.execute('DELETE FROM ab_trials')
    assert dsf._lifecycle_status_with_scope_transfer(conn,recipe_lifecycle,
        symptom_id=key['symptom_id'],design_class=key['design_class'],platform=key['platform'],strategy=strategy())==('candidate','exact')
    conn.close()
