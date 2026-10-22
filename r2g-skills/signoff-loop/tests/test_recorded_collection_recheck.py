"""Opt-in offline replay of the two r1 counterexamples, not repaired history."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_recheck_run_binding as binding

SOURCE = os.environ.get('R2G_CHAIN_REPLAY_ROOT')
pytestmark = pytest.mark.skipif(not SOURCE, reason='requires explicit read-only r1 evidence root')


def copy_reports(source, dest):
    for p in source.rglob('*'):
        if p.is_file() and p.suffix in ('.json', '.jsonl', '.log', '.rpt') and not p.is_symlink():
            target = dest / p.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)


@pytest.mark.parametrize('strategy,check', [('route_relief', 'route'), ('drv_route_margin_10', 'timing')])
def test_real_recorded_reports_bind_to_their_actual_run_in_scratch(tmp_path, strategy, check):
    source = Path(SOURCE) / 'projects/picorv32__u80'
    rows = [json.loads(x) for x in (source / 'reports/fix_log.jsonl').read_text().splitlines() if x.strip()]
    action = next(r for r in rows if r['strategy'] == strategy)
    pre = action.get('pre_run_tag') or json.loads((source / 'exploration.json').read_text())['run_tag']
    post = action['post_run_tag']
    assert pre != post
    project = tmp_path / 'project'
    (project / 'reports').mkdir(parents=True)
    shutil.copytree(source / 'constraints', project / 'constraints')
    copy_reports(source / 'backend' / pre, project / 'backend' / pre)
    # Retain the stale pointer that caused the old logging failure. The raw
    # action/report files are never changed; only new scratch reports are parsed.
    (project / 'backend/.r2g_signoff_run').write_text(json.dumps({'run_tag': pre}))
    extract = binding.SKILL / 'scripts/extract'
    def regenerate():
        for tool, output in [('extract_route.py', 'route.json'), ('extract_ppa.py', 'ppa.json')]:
            subprocess.run([sys.executable, str(extract / tool), str(project), str(project / 'reports' / output)],
                           capture_output=True, text=True, check=True)
        subprocess.run([sys.executable, str(binding.SKILL / 'scripts/reports/check_timing.py'), str(project)],
                       capture_output=True, text=True, check=False)
    regenerate()
    assert binding.helper(project, '_active_run_tag', [check]) == pre
    copy_reports(source / 'backend' / post, project / 'backend' / post)
    regenerate()
    report = project / 'reports' / ('timing_check.json' if check == 'timing' else 'route.json')
    step = json.loads(binding.helper(project, '_step_check', [check, str(report), '0']))
    assert step['report_run_tag'] == post
    assert step['active_run_tag'] == pre
    assert binding.helper(project, '_active_run_tag', [check]) == post
    assert step['report_sha256'] is not None
