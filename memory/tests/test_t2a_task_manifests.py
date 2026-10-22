import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import t2a_task_manifests as M
import t2a_envelope as E


def test_reference_missing_is_not_zero(tmp_path):
    p=tmp_path/'floor.json';p.write_text('{}')
    with pytest.raises(ValueError):M.areas(p,M.FLOOR_KEYS)


def test_allowed_geometry_and_distinct_utilisation_variables():
    allow=M.allowlist({})
    assert allow['CORE_UTILIZATION']['max']==90
    assert allow['PLACE_DENSITY']['max']==0.99
    man={'initial_config':{},'allowlist':allow}
    assert E.check_config(man,{'DIE_AREA':'0 0 100 120'})['ok']
    for val in ('0 0 0 10','0 0 nan 10','10 10 0 0'):
        assert not E.check_config(man,{'DIE_AREA':val})['ok']


def test_constraint_removal_remains_visible_in_audit():
    man={'initial_config':{'DESIGN_NAME':'fixed'},'immutable':{'knobs':['DESIGN_NAME']}}
    rows=[{'config_delta':{'DESIGN_NAME':{'before':'fixed','after':None}},'rolled_back':True}]
    assert E.recorded_violations(man,rows)[0]['kind']=='immutable_knob_changed'


def test_floorplan_mode_must_be_explicit_and_core_inside_die():
    man={'initial_config':{},'allowlist':M.allowlist({}),'floorplan_mode_policy':'explicit_util_or_fixed_rectangles'}
    assert E.check_config(man,{'CORE_UTILIZATION':'60'})['ok']
    assert E.check_config(man,{'DIE_AREA':'0 0 100 100','CORE_AREA':'10 10 90 90'})['ok']
    for cfg in ({},{'CORE_UTILIZATION':'60','DIE_AREA':'0 0 100 100'},
                {'DIE_AREA':'0 0 100 100','CORE_AREA':'10 10 110 110'}):
        assert not E.check_config(man,cfg)['ok']
