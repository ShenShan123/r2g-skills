from pathlib import Path

import pytest
import diagnose_signoff_fix as dsf
from diagnose_pin_pdn_spacing import diagnose


def fixture(project: Path):
    project.mkdir()
    (project/'6_final.def').write_text('''UNITS DISTANCE MICRONS 1000 ;
DIEAREA ( 0 0 ) ( 100000 100000 ) ;
PINS 1 ;
- data + NET data + DIRECTION OUTPUT + USE SIGNAL
  + PORT + LAYER met3 ( -400 -150 ) ( 400 150 )
  + PLACED ( 99600 25000 ) N ;
END PINS
SPECIALNETS 1 ;
- VDD + USE POWER
  + ROUTED met3 330 + SHAPE DRCFILL ( 98000 24660 ) ( 99500 24660 ) ;
END SPECIALNETS
''')
    (project/'6_drc.lyrdb').write_text('''<report-database><items>
<item><category>'m3.2'</category><values><value>edge-pair: (99200,24825;99500,24825)|(99200,24850;99500,24850)</value></values></item>
<item><category>'m3.2'</category><values><value>edge-pair: (99200,24825;99400,24825)|(99200,24850;99400,24850)</value></values></item>
</items></report-database>'''.replace('99200','99.2').replace('99500','99.5').replace('99400','99.4').replace('24825','24.825').replace('24850','24.85'))
    return project


def test_real_geometry_required_and_candidate_cannot_auto_apply(tmp_path):
    project=fixture(tmp_path/'project');cfg={'PLATFORM':'sky130hd'}
    strategy=dsf._pin_pdn_clearance_strategy(project,{'status':'fail'},cfg)
    assert strategy['id']=='pin_pdn_clearance'
    evidence=strategy['geometry_evidence']
    assert evidence['covered_marker_count']==evidence['marker_count']==2
    assert evidence['witnesses'][0]['gap_upper_bound_um']==0.025
    assert evidence['witnesses'][0]['power_net']=='VDD'
    assert dsf._live_auto_strategy({'strategies':[strategy.copy()]}) is None
    assert dsf._live_auto_strategy({'strategies':[strategy.copy()]},rank_first='pin_pdn_clearance')['id']=='pin_pdn_clearance'
    original='export PLATFORM = sky130hd\nexport DIE_AREA = 0 0 100 100\nexport SDC_FILE = original.sdc\n'
    applied=dsf.apply_edits(original,strategy['config_edits'])
    assert 'export DIE_AREA = 0 0 100 100' in applied
    assert 'export SDC_FILE = original.sdc' in applied
    assert 'PRE_IO_PLACEMENT_TCL' in applied
    assert dsf._pin_pdn_clearance_strategy(project,{'status':'clean'},cfg) is None
    for key,value in [('PLATFORM','nangate45'),('PRE_IO_PLACEMENT_TCL','custom.tcl'),('PLACE_PINS_ARGS','-exclude left:*'),('IO_PLACER_H','met2')]:
        assert dsf._pin_pdn_clearance_strategy(project,{'status':'fail'},{**cfg,key:value}) is None


@pytest.mark.parametrize('old,new',[
    ('24660','23000'),                 # sufficient clearance
    ('+ USE POWER','+ USE SIGNAL'),    # not a power/ground obstacle
    (') N ;',') S ;'),                 # unsupported rotation
    ('MICRONS 1000','MICRONS 2000'),    # different database-unit regime
    ('+ PORT + LAYER','+ LAYER'),      # missing interpreted port structure
])
def test_geometry_counterexamples_do_not_offer_recipe(tmp_path,old,new):
    project=fixture(tmp_path/'project');p=project/'6_final.def';p.write_text(p.read_text().replace(old,new))
    assert diagnose(p,project/'6_drc.lyrdb') is None


def test_unrelated_or_invalid_drc_does_not_offer_recipe(tmp_path):
    project=fixture(tmp_path/'project');p=project/'6_drc.lyrdb'
    p.write_text(p.read_text().replace('24.8','54.8'))
    assert diagnose(project/'6_final.def',p) is None
    p.write_text('<truncated>')
    assert dsf._pin_pdn_clearance_strategy(project,{'status':'fail'},{'PLATFORM':'sky130hd'}) is None


@pytest.mark.parametrize('status,valid_geometry,expected',[
    ('promoted',True,'pin_pdn_clearance'),
    ('candidate',True,'pin_side_rebalance'),
    ('shadow',True,'pin_side_rebalance'),
    ('promoted',False,'pin_side_rebalance'),
])
def test_normal_priority_requires_promotion_and_geometry(tmp_path,status,valid_geometry,expected):
    project=fixture(tmp_path/'project')
    strategy=dsf._pin_pdn_clearance_strategy(project,{'status':'fail'},{'PLATFORM':'sky130hd'})
    strategy['lifecycle_status']=status
    if not valid_geometry:strategy['geometry_evidence']['covered_marker_count']=0
    fallback={'id':'pin_side_rebalance','auto_apply':True}
    plan={'routing_context':{'platform':'sky130hd'},'lifecycle_gate_ok':True,
          'strategies':[fallback,strategy]}
    dsf._prioritize_geometric_pin_strategy(plan)
    assert plan['strategies'][0]['id']==expected
    plan['strategies']=[fallback,strategy];plan['action_policy_gate_ok']=False
    dsf._prioritize_geometric_pin_strategy(plan)
    assert plan['strategies'][0]['id']=='pin_side_rebalance'


def test_custom_pdn_config_does_not_enter_default_scope(tmp_path):
    project=fixture(tmp_path/'project')
    ordinary=dsf._pin_pdn_clearance_strategy(project,{'status':'fail'},{'PLATFORM':'sky130hd'})
    custom=dsf._pin_pdn_clearance_strategy(project,{'status':'fail'},{'PLATFORM':'sky130hd','PDN_CFG':'custom.tcl'})
    assert ordinary['pdn_scope_evidence']['generated_default_pdn_config'] is True
    assert custom['pdn_scope_evidence']['generated_default_pdn_config'] is False
