import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from t2a_lvs_evidence_audit import classify, mutated_reference


@pytest.mark.parametrize('report',['FAIL mismatch','Circuits match uniquely. PASS'])
def test_parser_failure_is_infrastructure_even_with_terminal_report(report):
    result=classify('Expected to find instance pin block but got "32"',report,0)
    assert result=={'status':'INFRASTRUCTURE_ERROR','reason':'netgen_parser_error'}


def test_missing_terminal_and_nonzero_are_never_match():
    assert classify('','','0')['status']=='INFRASTRUCTURE_ERROR'
    assert classify('','',0)['status']=='UNKNOWN'
    assert classify('Circuits match uniquely.','',0)['status']=='VERIFIED_MATCH'


def test_top_pin_failure_cannot_be_overridden_by_a_subcircuit_match():
    result=classify('Circuits match uniquely.',
                    'Netlists match uniquely.\nFinal result: Top level cell failed pin matching.',0)
    assert result['status']=='VERIFIED_MISMATCH'


@pytest.mark.parametrize('kind,pin,value',[('power','VGND','VDD'),('signal','A','VSS')])
def test_negative_control_changes_exactly_one_real_leaf_connection(kind,pin,value):
    text='module t();\n sky130_fd_sc_hd__buf_1 n (.A(data), .X(out), .VGND(VSS), .VPWR(VDD));\nendmodule\n'
    result,change=mutated_reference(text,kind)
    assert change['pin']==pin and change['new']==value
    start=change['offset'];old=change['old']
    assert result==text[:start]+value+text[start+len(old):]
    assert '.X(out)' in result and 'module t();' in result
