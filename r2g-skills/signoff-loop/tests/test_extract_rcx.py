"""A failed extraction cannot borrow a previous SPEF's success."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
import extract_rcx

SCRIPT = Path(extract_rcx.__file__)
SPEF = '*DESIGN "counter8"\n*DESIGN_FLOW "NAME_SCOPE LOCAL"\n*C_UNIT 1 PF\n*R_UNIT 1 OHM\n*D_NET net1 0.1\n*END\n'


def report(tmp_path, receipt=None, log=''):
    rcx = tmp_path / 'rcx'
    rcx.mkdir()
    (rcx / '6_final.spef').write_text(SPEF)
    (rcx / 'rcx.log').write_text(log)
    if receipt is not None:
        (rcx / 'rcx_result.json').write_text(json.dumps(receipt))
    out = tmp_path / 'reports/rcx.json'
    subprocess.run([sys.executable, str(SCRIPT), str(tmp_path), str(out)], check=True, capture_output=True)
    return json.loads(out.read_text())


@pytest.mark.parametrize('receipt', [
    {'status': 'failed', 'exit_code': 1},
    {'status': 'running'},
    {'status': 'complete', 'exit_code': 1},
    {'status': 'complete', 'exit_code': 0, 'spef_sha256': 'wrong'},
])
def test_previous_output_does_not_mask_unconfirmed_execution(tmp_path, receipt):
    assert report(tmp_path, receipt)['status'] != 'complete'


def test_success_binds_current_output_and_exact_design_header(tmp_path):
    result = report(tmp_path, {'status': 'complete', 'exit_code': 0,
                              'spef_sha256': hashlib.sha256(SPEF.encode()).hexdigest()})
    assert result['status'] == 'complete'
    assert result['header']['design'] == 'counter8'


def test_legacy_error_overrides_preexisting_spef(tmp_path):
    result = report(tmp_path, log='Error: run_rcx.tcl, 2 incompatible database schema revision 0.131 > 0.98\n')
    assert result['status'] == 'failed'
