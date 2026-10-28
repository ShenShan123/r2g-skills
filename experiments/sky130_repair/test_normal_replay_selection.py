import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parent))
from prepare_normal_replay_import import validate_normal_selection


def receipts(root,strategy='lvs_port_feedthrough_buffer'):
    choices={
        'lvs_port_feedthrough_buffer':('place','lvs',{'POST_GLOBAL_PLACE_TCL':'/hook.tcl'}),
        'pin_pdn_clearance':('floorplan','drc',{'PRE_IO_PLACEMENT_TCL':'/hook.tcl'}),
        'fixed_area_route_relief':('floorplan','route',{'DIE_AREA':'0 0 100 100','CORE_AREA':'2 2 98 98'}),
    }
    stage,check,edits=choices[strategy]
    plan={'key':{'strategy':strategy}}
    selected={'returncode':0,'stdout':f'{strategy}\t{stage}\t{check}\n'}
    action={'status':'applied','applied':strategy,'config_edits':edits}
    (root/'selection.json').write_text(json.dumps(selected))
    (root/'application.json').write_text(json.dumps({'returncode':0,'stdout':json.dumps(action)}))
    config=''.join(f'export {k} = {v}\n' for k,v in edits.items())
    return plan,config,selected,action


@pytest.mark.parametrize('strategy',['lvs_port_feedthrough_buffer','pin_pdn_clearance','fixed_area_route_relief'])
def test_actual_recipe_stage_and_knobs(tmp_path,strategy):
    plan,config,_,_=receipts(tmp_path,strategy)
    validate_normal_selection(tmp_path,plan,config)


def test_wrong_rerun_stage_cannot_claim_normal_selection(tmp_path):
    plan,config,selected,_=receipts(tmp_path)
    selected['stdout']=selected['stdout'].replace('\tplace\t','\tfloorplan\t')
    (tmp_path/'selection.json').write_text(json.dumps(selected))
    with pytest.raises(ValueError,match='selection receipt'):
        validate_normal_selection(tmp_path,plan,config)


def test_auto_fix_block_uses_effective_later_make_assignment(tmp_path):
    plan,config,_,_=receipts(tmp_path,'fixed_area_route_relief')
    original='export DIE_AREA = 0 0 80 80\nexport CORE_AREA = 2 2 78 78\n'
    validate_normal_selection(tmp_path,plan,original+config)
    with pytest.raises(ValueError,match='edits differ'):
        validate_normal_selection(tmp_path,plan,config+original)


@pytest.mark.parametrize('change',['different_strategy','extra_knob','wrong_value','sdc_edit','failed_application'])
def test_reject_unverified_application(tmp_path,change):
    plan,config,_,action=receipts(tmp_path)
    status=0
    if change=='different_strategy':action['applied']='pin_pdn_clearance'
    if change=='extra_knob':action['config_edits']['CORE_UTILIZATION']='5'
    if change=='wrong_value':action['config_edits']['POST_GLOBAL_PLACE_TCL']='/different.tcl'
    if change=='sdc_edit':action['sdc_edits']={'clock_period':'100'}
    if change=='failed_application':status=5
    (tmp_path/'application.json').write_text(json.dumps({'returncode':status,'stdout':json.dumps(action)}))
    with pytest.raises(ValueError):
        validate_normal_selection(tmp_path,plan,config)
