import json
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import t2a_qualification_inventory as I


def acceptance(failed):
    keys=['A1_flow_complete','A2_drc_full_deck','A3_lvs','A4_route','A5_antenna','A6_setup',
          'A7_hold','A8_drv_limits','A9_task_clock','A10_rcx','A11_provenance']
    return {'checks':{k:{'result':'fail' if k in failed else 'pass','detail':{'unchanged':k}} for k in keys}}


def test_lvs_resolution_excludes_initial_pass_but_keeps_natural_setup_failure():
    initial=acceptance({'A3_lvs'});before=json.dumps(initial,sort_keys=True)
    corrected=I.resolved_acceptance(initial,'VERIFIED_MATCH')
    assert corrected['accepted'] and json.dumps(initial,sort_keys=True)==before
    corrected=I.resolved_acceptance(acceptance({'A3_lvs','A6_setup'}),'VERIFIED_MATCH')
    assert not corrected['accepted'] and corrected['failed']==['A6_setup']
    assert corrected['checks']['A6_setup']['detail']=={'unchanged':'A6_setup'}


def test_unverified_lvs_or_unknown_other_check_cannot_freeze_admission():
    with pytest.raises(ValueError,match='not verified'):
        I.resolved_acceptance(acceptance({'A3_lvs'}),'UNKNOWN')
    initial=acceptance({'A3_lvs'});initial['checks']['A11_provenance']['result']='unknown'
    with pytest.raises(ValueError,match='Unresolved'):
        I.resolved_acceptance(initial,'VERIFIED_MATCH')
