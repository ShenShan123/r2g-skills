"""Run the real runner's clean-all block, with a fake Make, no EDA."""
import json
import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize('strict,clean_rc,expected',[(True,2,76),(True,0,0),(False,2,0)])
def test_clean_all_failure_is_not_a_fresh_strict_workspace(tmp_path,strict,clean_rc,expected):
    source=(Path(__file__).resolve().parents[1]/'scripts/flow/run_orfs.sh').read_text()
    begin=source.index('if [[ -z "$FROM_STAGE" ]]; then\n  echo "Cleaning previous ORFS state')
    end=source.index('# Timeout and CPU limit support',begin)
    body=f'set -euo pipefail\nmake() {{ echo mock-clean; return {clean_rc}; }}\n'+source[begin:end]
    env={**os.environ,'FROM_STAGE':'','BACKEND_DIR':str(tmp_path),'FLOW_VARIANT':'dev',
         'FLOW_DIR':str(tmp_path),'ORFS_DESIGN_DIR':str(tmp_path),
         'R2G_TASK_MANIFEST':'task.json' if strict else ''}
    result=subprocess.run(['bash','-c',body],env=env,capture_output=True,text=True)
    assert result.returncode==expected
    receipt=json.loads((tmp_path/'cleanup.json').read_text())
    assert receipt['clean_all_rc']==clean_rc and receipt['from_stage']==''
    assert receipt['status']==('PASS' if clean_rc==0 else 'FAIL')
