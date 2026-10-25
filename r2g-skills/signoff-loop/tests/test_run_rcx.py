"""Exercise executable pinning and fresh-output publication without EDA tools."""
import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

SKILL = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('mode,expected', [
    ('success', 0), ('failed', 9), ('failed_with_output', 9), ('empty', 1),
    ('env_error', 17), ('missing_orfs', 1),
])
def test_pinned_runner_never_publishes_stale_or_failed_output(tmp_path, mode, expected):
    scripts = tmp_path / 'skill/scripts/flow'
    scripts.mkdir(parents=True)
    (tmp_path / 'skill/knowledge').mkdir()
    shutil.copy2(SKILL / 'scripts/flow/run_rcx.sh', scripts / 'run_rcx.sh')
    (scripts / '_env.sh').write_text('export FLOW_DIR="$ORFS_ROOT/flow"\n')
    (scripts / '_backend_run.sh').write_text('r2g_pick_backend_run() { printf "%s/backend/RUN_1\\n" "$1"; }\n')
    project = tmp_path / 'project'
    (project / 'constraints').mkdir(parents=True)
    (project / 'constraints/config.mk').write_text('export DESIGN_NAME = top\n')
    (project / 'rcx').mkdir()
    old_spef = '*DESIGN "top"\n*D_NET old 1.0\n*END\n'
    (project / 'rcx/6_final.spef').write_text(old_spef)
    (project / 'rcx/rcx_result.json').write_text(json.dumps({
        'status': 'complete', 'exit_code': 0,
        'spef_sha256': hashlib.sha256(old_spef.encode()).hexdigest(),
    }))
    backend = project / 'backend/RUN_1/rcx'
    backend.mkdir(parents=True)
    (backend / '6_final.spef').write_text('old backend output')
    orfs = tmp_path / 'orfs'
    rules = orfs / 'flow/platforms/nangate45'
    rules.mkdir(parents=True)
    (rules / 'rcx_patterns.rules').write_text('rules')
    results = orfs / 'flow/results/nangate45/top/project'
    results.mkdir(parents=True)
    (results / '6_final.odb').write_text('database')
    (results / '6_final.spef').write_text('old ORFS output')
    tool = tmp_path / 'pinned-openroad'
    tool.write_text(f'#!{sys.executable}\n' + '''import os,re,sys
from pathlib import Path
mode=os.environ['RCX_TEST_MODE']
if mode in ('success','failed_with_output'):
 path=re.search(r'write_spef "([^"]+)"',Path(sys.argv[-1]).read_text())[1]
 Path(path).write_text('*DESIGN "top"\\n*D_NET fresh 1.0\\n*END\\n')
if mode.startswith('failed'):
 print('Error: incompatible database schema')
 sys.exit(9)
''')
    tool.chmod(0o755)
    env = {**os.environ, 'ORFS_ROOT': str(orfs), 'OPENROAD_EXE': str(tool), 'RCX_TEST_MODE': mode}
    if mode == 'env_error':
        (scripts / '_env.sh').write_text('exit 17\n')
    elif mode == 'missing_orfs':
        env['ORFS_ROOT'] = str(tmp_path / 'missing')
    run = subprocess.run(['bash', str(scripts / 'run_rcx.sh'), str(project), 'nangate45'],
                         cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20)
    assert run.returncode == expected, run.stdout + run.stderr
    receipt = json.loads((project / 'rcx/rcx_result.json').read_text())
    if mode in ('env_error', 'missing_orfs'):
        assert receipt['status'] == 'running'
    else:
        assert receipt['exit_code'] == expected
    if expected == 0:
        assert receipt['status'] == 'complete' and receipt['spef_sha256']
        assert 'fresh' in (project / 'rcx/6_final.spef').read_text()
        assert (backend / '6_final.spef').read_bytes() == (project / 'rcx/6_final.spef').read_bytes()
    else:
        assert receipt['status'] in ('failed', 'running')
        assert (project / 'rcx/6_final.spef').read_text() == old_spef
        assert (backend / '6_final.spef').read_text() == 'old backend output'
        assert 'completed successfully' not in run.stdout
    assert list((project / 'rcx').glob('6_final.spef.*')) == []
    report = project / 'reports/rcx.json'
    subprocess.run([sys.executable, str(SKILL / 'scripts/extract/extract_rcx.py'),
                    str(project), str(report)], check=True, capture_output=True, text=True)
    assert (json.loads(report.read_text())['status'] == 'complete') is (expected == 0)
