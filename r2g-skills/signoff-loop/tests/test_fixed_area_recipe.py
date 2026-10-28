import pytest
from fixed_area_recipe import candidate
import diagnose_signoff_fix as dsf

def subject(tmp_path):
    (tmp_path/'flow.log').write_text('[ERROR GRT-0118] Routing congestion too high\n')
    (tmp_path/'6_final.def').write_text('''UNITS DISTANCE MICRONS 1000 ;
DIEAREA ( 0 0 ) ( 521000 521000 ) ;
COMPONENTS 1 ;
- u1 sky130_fd_sc_hd__nand2_1 + PLACED ( 4000 4000 ) N ;
END COMPONENTS
''')
    return {'PLATFORM':'sky130hd','DIE_AREA':'0 0 521 521','CORE_AREA':'2 2 519 519','SDC_FILE':'original.sdc'}

def test_bounded_geometry_and_unpromoted_gate(tmp_path):
    cfg=subject(tmp_path);s=candidate(tmp_path,{'status':'fail'},cfg)
    assert s['config_edits']=={'DIE_AREA':'0 0 652 652','CORE_AREA':'2 2 650 650'}
    assert dsf._live_auto_strategy({'strategies':[s.copy()]}) is None
    assert dsf._live_auto_strategy({'strategies':[s.copy()]},rank_first=s['id'])['id']==s['id']
    original='\n'.join('export '+k+' = '+v for k,v in cfg.items())+'\n'
    assert 'export SDC_FILE = original.sdc' in dsf.apply_edits(original,s['config_edits'])

@pytest.mark.parametrize('key,value',[
 ('PLATFORM','gf180'),('CORE_UTILIZATION','50'),('PDN_TCL','custom.tcl'),
 ('MACRO_PLACEMENT_TCL','custom.tcl'),('PLACE_PINS_ARGS','fixed'),
 ('DIE_AREA','0 0 NaN 521'),('DIE_AREA','0 0 0 521'),
 ('CORE_AREA','-1 2 519 519'),('CORE_AREA','2 2 521 519'),
])
def test_unsupported_configs_rejected(tmp_path,key,value):
    cfg=subject(tmp_path);cfg[key]=value;assert candidate(tmp_path,{'status':'fail'},cfg) is None

@pytest.mark.parametrize('old,new',[
 ('521000','522000'),('sky130_fd_sc_hd__nand2_1','ram_macro'),
 ('+ PLACED','+ FIXED'),('COMPONENTS 1','COMPONENTS 2'),
 ('UNITS DISTANCE MICRONS 1000','UNITS DISTANCE MICRONS 0'),
])
def test_geometry_counterexamples(tmp_path,old,new):
    cfg=subject(tmp_path);p=tmp_path/'6_final.def';p.write_text(p.read_text().replace(old,new))
    assert candidate(tmp_path,{'status':'timeout'},cfg) is None

def test_missing_failure_and_hard_floorplan(tmp_path):
    cfg=subject(tmp_path)
    for status in ['clean','unknown','skipped']:assert candidate(tmp_path,{'status':status},cfg) is None
    log=tmp_path/'flow.log';log.write_text('synthesis failed')
    assert candidate(tmp_path,{'status':'fail'},cfg) is None
    log.write_text('[ERROR GRT-0118] congestion')
    (tmp_path/'constraints').mkdir();(tmp_path/'constraints/floorplan.json').write_text('{}')
    assert candidate(tmp_path,{'status':'fail'},cfg) is None

def test_generated_taps_only_and_terminal_congestion(tmp_path):
    cfg=subject(tmp_path);p=tmp_path/'6_final.def'
    old=p.read_text();p.write_text(old.replace('COMPONENTS 1','COMPONENTS 2').replace('END COMPONENTS','- TAP_TAPCELL_ROW_0_0 sky130_fd_sc_hd__tapvpwrvgnd_1 + FIXED ( 2000 2000 ) N ;\nEND COMPONENTS'))
    assert candidate(tmp_path,{'status':'fail'},cfg)
    p.write_text(p.read_text().replace('TAP_TAPCELL_ROW_0_0','USER_FIXED'))
    assert candidate(tmp_path,{'status':'fail'},cfg) is None
    p.write_text(old)
    (tmp_path/'flow.log').write_text('[INFO GPL-0048] Routing congestion improved\n')
    assert candidate(tmp_path,{'status':'fail'},cfg) is None
    for code in ('0116','0232'):
        (tmp_path/'flow.log').write_text('[ERROR GRT-'+code+'] congestion\n')
        assert candidate(tmp_path,{'status':'fail'},cfg)

def test_route_index_uses_ingester_key(tmp_path):
    import json
    import symptom
    sid=symptom.symptom_id(symptom.canonical_signature('orfs_stage','route',symptom.predicates_for('orfs_stage',{})))
    hp=tmp_path/'heuristics.json'
    hp.write_text(json.dumps({'recipes':{sid:{'logic/small':{'sky130hd':{'strategies':{'fixed_area_route_relief':{'attempts':4,'successes':2}}}}}}}))
    row,pooled,level=dsf.load_indexed_recipe(check='route',platform='sky130hd',design_class='logic/small',drc={},lvs={},heuristics=hp)
    assert level=='exact' and 'fixed_area_route_relief' in row['strategies']

@pytest.mark.parametrize('status,allowed',[('candidate',False),('shadow',False),('promoted',True)])
def test_new_route_recipe_lifecycle_gate(tmp_path,monkeypatch,status,allowed):
    import knowledge_db
    cfg=subject(tmp_path);s=candidate(tmp_path,{'status':'fail'},cfg)
    monkeypatch.setattr(dsf,'_lifecycle_status_with_scope_transfer',lambda *a,**k:(status,'exact'))
    plan={'strategies':[s]}
    dsf._annotate_live_gates(plan,tmp_path,check='route',sid='route-test',design_class='logic/small',platform='sky130hd',db_path=tmp_path/'private.sqlite')
    assert bool(dsf._live_auto_strategy(plan))==allowed
