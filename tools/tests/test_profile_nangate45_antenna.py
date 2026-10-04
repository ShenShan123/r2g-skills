import importlib.util
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import pytest

TOOLS=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(TOOLS))
from profile_nangate45_antenna import instrument, log_operations, timings


def deck():
    root=ET.Element('klayout-macro')
    node=ET.SubElement(root,'text')
    node.text='\ndeep\nthreads(4)\ncont.not(active).not(poly).not(metal1)\n'+''.join(
        f'antenna_check(gate, metal{i}, 300.0, diode).output("METAL{i}_ANTENNA", "rule")\n'
        for i in range(1,11))
    return ET.tostring(root)


@pytest.mark.parametrize('threads',[1,4])
def test_instrument_preserves_all_rules(threads):
    original=ET.fromstring(deck()).findtext('text')
    modified=ET.fromstring(instrument(deck(),threads)).findtext('text')
    for line in original.splitlines():
        if line and not line.startswith('threads'):
            assert line in modified
    assert f'threads({threads})' in modified
    assert modified.count('antenna_check(')==10
    assert modified.index('{ netlist }')<modified.index('antenna_check(')


def test_rejects_unexpected_rule():
    with pytest.raises(ValueError):
        instrument(deck().replace(b'300.0',b'500.0'),4)


def test_timing_incomplete_is_not_success():
    result=timings('R2G_PROFILE_BEGIN netlist_extract\nR2G_PROFILE_END netlist_extract 1.25 1.1\nR2G_PROFILE_BEGIN metal1\n')
    assert result['phases']['netlist_extract']['wall_seconds']==1.25
    assert result['unfinished_phase']=='metal1'


def test_log_missing_total():
    result=log_operations('"antenna_check" in: test.lydrc:352\n    Elapsed: 30.0s\n"antenna_check" in: test.lydrc:353\n')
    assert result['total_seconds'] is None
    assert result['largest'][0]['seconds']==30
    assert len(result['operations'])==1
