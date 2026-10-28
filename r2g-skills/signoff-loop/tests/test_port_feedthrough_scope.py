import copy
import json
import sqlite3
import pytest
import port_feedthrough_scope as scope
import recipe_lifecycle
import diagnose_signoff_fix as dsf


def validated_subject(tmp_path):
    strategy=dsf._feedthrough_recipe()
    strategy['port_feedthrough_evidence']=dict(mechanism='unidirectional_top_port_feedthrough',
        all_aliases_have_single_input=True,inout_declaration_count=0,shared_top_port_nets=1,
        aliased_output_count=2,def_sha256='a'*64,mapped_netlist_sha256='b'*64)
    conn=sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE recipe_status(symptom_id,design_class,platform,strategy,status,provenance)')
    conn.execute('CREATE TABLE ab_trials(symptom_id,design_class,platform,strategy,verdict,metrics_json)')
    conn.execute('CREATE TABLE runs(run_id,platform,design_family,orfs_status,drc_status,lvs_status)')
    conn.execute('INSERT INTO recipe_status VALUES(?,?,?,?,?,?)',(*scope.KEY.values(),'promoted','unit_test_only'))
    for family in ('first','second'):
        samples=lambda role:[dict(repeat=i,is_success=role=='B',run_id=f'{family}_{role}_{i}',source_repository=family,platform='sky130hd') for i in (0,1)]
        metrics=dict(provenance_complete=True,source='durable_copy_of_verified_physical_AB',
                     arm_a_samples=samples('A'),arm_b_samples=samples('B'))
        for sample in metrics['arm_a_samples']+metrics['arm_b_samples']:
            ok=sample['is_success']
            conn.execute('INSERT INTO runs VALUES(?,?,?,?,?,?)',(sample['run_id'],'sky130hd',family,'partial' if ok else 'fail','clean' if ok else None,'clean' if ok else None))
        conn.execute('INSERT INTO ab_trials VALUES(?,?,?,?,?,?)',(*scope.KEY.values(),'win',json.dumps(metrics)))
    return conn,strategy


def test_supported_geometry_reuses_real_anchor_without_wildcard(tmp_path):
    conn,strategy=validated_subject(tmp_path)
    assert scope.validated(conn,recipe_lifecycle,strategy)
    assert dsf._lifecycle_status_with_scope_transfer(conn,recipe_lifecycle,
        symptom_id=scope.KEY['symptom_id'],design_class='bus_heavy/large',platform='sky130hd',strategy=strategy)==('promoted','validated_port_feedthrough_geometry')
    assert conn.execute("SELECT COUNT(*) FROM recipe_status WHERE design_class='*'").fetchone()[0]==0
    conn.execute('DELETE FROM ab_trials WHERE rowid=2')
    assert not scope.validated(conn,recipe_lifecycle,strategy)
    conn.close()


@pytest.mark.parametrize('status',['shadow','parked','candidate'])
def test_exact_target_class_veto_kept(tmp_path,status):
    conn,strategy=validated_subject(tmp_path);key={**scope.KEY,'design_class':'bus_heavy/large'}
    conn.execute('INSERT INTO recipe_status VALUES(?,?,?,?,?,?)',(*key.values(),status,'manual_review'))
    assert dsf._lifecycle_status_with_scope_transfer(conn,recipe_lifecycle,
        symptom_id=key['symptom_id'],design_class=key['design_class'],platform=key['platform'],strategy=strategy)==(status,'exact')
    conn.close()


def test_missing_geometry_and_extra_edits_cannot_transfer(tmp_path):
    conn,strategy=validated_subject(tmp_path)
    for field in ('sdc_edits','env_edits'):
        bad=copy.deepcopy(strategy);bad[field]={'bad':'1'};assert not scope.matches(bad)
    bad=copy.deepcopy(strategy);bad['config_edits']['CORE_UTILIZATION']='1';assert not scope.matches(bad)
    bad=copy.deepcopy(strategy);bad['port_feedthrough_evidence']['inout_declaration_count']=1;assert not scope.matches(bad)
    bad=copy.deepcopy(strategy);bad['port_feedthrough_evidence']={};assert not scope.matches(bad)
    conn.close()


@pytest.mark.parametrize('corruption',[None,'platform','design_family','orfs_status','missing_run'])
def test_legacy_samples_require_consistent_real_run_records(tmp_path,corruption):
    conn,strategy=validated_subject(tmp_path)
    for rid,raw in conn.execute('SELECT rowid,metrics_json FROM ab_trials').fetchall():
        metrics=json.loads(raw)
        for sample in metrics['arm_a_samples']+metrics['arm_b_samples']:sample.pop('platform')
        conn.execute('UPDATE ab_trials SET metrics_json=? WHERE rowid=?',(json.dumps(metrics),rid))
    if corruption=='missing_run':conn.execute("DELETE FROM runs WHERE run_id='second_B_1'")
    elif corruption:
        value='fail' if corruption=='orfs_status' else 'unrelated'
        conn.execute(f"UPDATE runs SET {corruption}=? WHERE run_id='second_B_1'",(value,))
    assert scope.validated(conn,recipe_lifecycle,strategy)==(corruption is None)
    conn.close()
