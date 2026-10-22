"""Regression cases from collection r1; no EDA, mutable production evidence, or model calls."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
FIX = SKILL / 'scripts/flow/fix_signoff.sh'


def shell_function(name, next_name):
    text = FIX.read_text()
    return text[text.index(name + '() {'):text.index(next_name + '() {')]


def project(tmp_path):
    (tmp_path / 'reports').mkdir()
    (tmp_path / 'constraints').mkdir()
    (tmp_path / 'backend/RUN_NEW').mkdir(parents=True)
    (tmp_path / 'backend/RUN_OLD').mkdir()
    (tmp_path / 'backend/.r2g_signoff_run').write_text(json.dumps({'run_tag': 'RUN_OLD'}))
    return tmp_path


def helper(p, name, args, env_extra=None):
    body = shell_function('_active_run_tag', '_next_action_id') + shell_function('_step_check', '_log_iter')
    result = subprocess.run(['bash', '-c', body + '\n' + name + ' "$@"', 'test', *args],
                            env={**os.environ, 'PROJECT_DIR': str(p), **(env_extra or {})},
                            capture_output=True, text=True, check=True)
    return result.stdout.strip()


def test_route_abort_pre_run_does_not_require_a_completed_signoff_pointer(tmp_path):
    p = project(tmp_path)
    (p / 'backend/.r2g_signoff_run').unlink()
    (p / 'reports/route.json').write_text(json.dumps({'backend_run': 'RUN_NEW', 'status': 'fail'}))
    assert helper(p, '_active_run_tag', ['route']) == 'RUN_NEW'


def test_pre_run_comes_from_the_target_report_not_old_signoff(tmp_path):
    p = project(tmp_path)
    (p / 'reports/timing_check.json').write_text(json.dumps({'run_tag': 'RUN_NEW', 'tier': 'clean'}))
    assert helper(p, '_active_run_tag', ['timing']) == 'RUN_NEW'


@pytest.mark.parametrize('payload', [
    {'backend_run': 'RUN_NEW'}, {'provenance': {'run_tag': 'RUN_NEW', 'source': 'explicit'}},
    {'run_tag': 'RUN_NEW'},
])
def test_recheck_uses_report_owner_even_if_signoff_still_names_old_run(tmp_path, payload):
    p = project(tmp_path)
    report = p / 'reports/route.json'
    report.write_text(json.dumps(payload))
    result = json.loads(helper(p, '_step_check', ['route', str(report), '0']))
    assert result['report_run_tag'] == 'RUN_NEW'


def test_conflicting_report_owners_are_unknown(tmp_path):
    p = project(tmp_path)
    report = p / 'reports/route.json'
    report.write_text(json.dumps({'run_tag': 'RUN_NEW', 'backend_run': 'RUN_OLD'}))
    result = json.loads(helper(p, '_step_check', ['route', str(report), '0']))
    assert result['report_run_tag'] is None
    assert result.get('binding_error') == 'conflicting_report_owners'


def test_active_pointer_alone_cannot_certify_an_untagged_recheck(tmp_path):
    p = project(tmp_path)
    (p / 'backend/.r2g_signoff_run').write_text('{"run_tag":"RUN_NEW"}')
    (p / 'backend/RUN_NEW/r2g_action.json').write_text('{"action_id":"s#1"}')
    report = p / 'reports/route.json'
    report.write_text('{"status":"clean"}')
    sc = json.loads(helper(p, '_step_check', ['route', str(report), '0']))
    body = shell_function('_log_iter', '_capture_vector')
    env = {**os.environ, 'PROJECT_DIR': str(p), 'FIX_SESSION_ID': 's',
           'LOG': str(p / 'reports/fix_log.jsonl'), 'R2G_LOG_STEP_CHECK': json.dumps(sc)}
    subprocess.run(['bash', '-c', body + '\n_log_iter route 1 route_relief 1 0 cleared floorplan route "{}" "{}"'],
                   env=env, capture_output=True, text=True, check=True)
    row = json.loads((p / 'reports/fix_log.jsonl').read_text())
    assert row['step_check']['graded_post_run'] is False


@pytest.mark.parametrize('timing', [
    {'setup_wns': 1, 'setup_tns': 0, 'max_slew_violations': 0, 'max_cap_violations': 0}, {},
])
def test_timing_owner_follows_the_ppa_it_actually_reads(tmp_path, timing):
    p = project(tmp_path)
    (p / 'reports/ppa.json').write_text(json.dumps({'run_dir': str(p / 'backend/RUN_NEW'),
                                                  'summary': {'timing': timing}}))
    subprocess.run([sys.executable, str(SKILL / 'scripts/reports/check_timing.py'), str(p)],
                   capture_output=True, text=True, check=False)
    result = json.loads((p / 'reports/timing_check.json').read_text())
    assert result['run_tag'] == 'RUN_NEW'
    assert result['provenance']['source'] == 'ppa_run_dir'


def test_extract_route_records_its_actual_input_run(tmp_path):
    p = project(tmp_path)
    (p / 'backend/RUN_OLD/stage_log.jsonl').write_text('{"stage":"route","status":2}\n')
    subprocess.run([sys.executable, str(SKILL / 'scripts/extract/extract_route.py'), str(p),
                    str(p / 'reports/route.json')], capture_output=True, text=True, check=True)
    result = json.loads((p / 'reports/route.json').read_text())
    assert result['run_tag'] == result['backend_run'] == 'RUN_OLD'
