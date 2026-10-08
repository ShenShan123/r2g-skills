import importlib.util
import json
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).resolve().parents[2]/'r2g-skills/def-graph/scripts/r2g2/01_build_base_graph.py'
spec = importlib.util.spec_from_file_location('base_indices', SOURCE)
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


@pytest.mark.parametrize('detail,expected', [
    ({'bits': [2, 3], 'offset': 6}, ['bus[6]', 'bus[7]']),
    ({'bits': [2, 3], 'offset': 6, 'upto': 1}, ['bus[7]', 'bus[6]']),
    ({'bits': [2], 'offset': 4}, ['bus[4]']),
    ({'bits': [2, 3], 'offset': -2}, ['bus[-2]', 'bus[-1]']),
    ({'bits': [2]}, ['bus']),
])
def test_signal_indices(detail, expected):
    assert base.yosys_signal_names('bus', detail) == expected


def test_nonzero_bus_names_in_connections_and_io(monkeypatch, tmp_path):
    detail = {'bits': [2, 3], 'offset': 6}
    module = {'netnames': {'VGA': detail}, 'ports': {'VGA': dict(detail, direction='output')},
              'cells': {'g0': {'type': 'BUF', 'connections': {'Y': [2]}},
                        'g1': {'type': 'BUF', 'connections': {'Y': [3]}}}}
    def fake_run(command, **kwargs):
        path = json.loads(re.search(r'write_json (".*?")', command[-1])[1])
        Path(path).write_text(json.dumps({'modules': {'top': module}}))
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    monkeypatch.setattr(base.subprocess, 'run', fake_run)
    _, nets, io, _ = base.parse_synth_verilog_with_yosys(tmp_path/'top.v', 'top', [], '/bin/true', {}, {})
    assert set(nets) == {'VGA[6]', 'VGA[7]'}
    assert {r['iopin_name'] for r in io} == {'VGA[6]', 'VGA[7]'}
    assert nets['VGA[6]'] == [('g0', 'Y')]
