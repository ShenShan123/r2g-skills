import importlib.util
import json
from pathlib import Path
import sys
import pytest


CAMPAIGN = Path('/home/yangao/r2g_nangate45_baseline_20260917')
sys.path.insert(0, str(CAMPAIGN))
spec = importlib.util.spec_from_file_location('n45_recovery', CAMPAIGN/'recover_drc.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def job(tmp_path, status='inconclusive_timeout', make_status=0):
    work = tmp_path/'job'
    run = work/'project/backend/RUN_test'
    put(work/'complete.json', {'status': status, 'elapsed_seconds': 7200, 'trial_timeout': True})
    put(run/'run-meta.json', {'make_status': make_status})
    for name in ('results/6_final.gds', 'drc/6_drc.log'):
        p = run/name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('original')
    put(work/'project/repair_family_probe_input.json', {})
    return work, run


def test_only_incomplete_drc_is_eligible(tmp_path):
    work, run = job(tmp_path)
    assert recovery.eligible(work) == run
    put(run/'drc/drc_result.json', {'exit_code': 0, 'status': 'violations'})
    assert recovery.eligible(work) is None


def test_valid_results_and_backend_failures_are_not_retried(tmp_path):
    for i, status in enumerate(('baseline_clean_single_run', 'physical_failure_pending_reproduction')):
        work, _ = job(tmp_path/str(i), status=status)
        assert recovery.eligible(work) is None
    work, _ = job(tmp_path/'failed_flow', make_status=2)
    assert recovery.eligible(work) is None


def test_recovery_preserves_old_attempt_and_accounts_both_times(tmp_path, monkeypatch):
    work, run = job(tmp_path)
    root = tmp_path/'recoveries'
    root.mkdir()
    monkeypatch.setattr(recovery, 'RECOVERY', root)
    monkeypatch.setattr(recovery.runner, 'classify', lambda *args: 'baseline_clean_single_run')

    def call(*args):
        assert '--skip-orfs' in args[0]
        assert not (run/'drc/6_drc.log').exists()
        assert (root/work.name/'previous_checker_outputs/backend/RUN_test/drc/6_drc.log').exists()
        put(work/'project/repair_family_probe_result.json', {'run_id': run.name})
        put(run/'drc/drc_result.json', {'exit_code': 0, 'status': 'clean', 'drc_mode': 'full',
            'gds_sha256': recovery.runner.sha(run/'results/6_final.gds')})
        return 0, False

    monkeypatch.setattr(recovery.runner, 'call', call)
    recovery.recover(work, run)
    old = json.loads((root/work.name/'original_job/complete.json').read_text())
    new = json.loads((work/'complete.json').read_text())
    assert old['status'] == 'inconclusive_timeout'
    assert new['original_trial_timeout'] is True
    assert new['elapsed_seconds'] >= 7200
    assert new['recovery']['backend_not_rerun'] is True
    assert new['recovery']['accepted'] is True


def test_changed_gds_cannot_replace_result(tmp_path, monkeypatch):
    work, run = job(tmp_path)
    root = tmp_path/'recoveries'
    root.mkdir()
    monkeypatch.setattr(recovery, 'RECOVERY', root)
    monkeypatch.setattr(recovery.runner, 'classify', lambda *args: 'baseline_clean_single_run')

    def call(*args):
        (run/'results/6_final.gds').write_text('changed')
        put(work/'project/repair_family_probe_result.json', {'run_id': run.name})
        return 0, False

    monkeypatch.setattr(recovery.runner, 'call', call)
    recovery.recover(work, run)
    assert json.loads((work/'complete.json').read_text())['status'] == 'inconclusive_timeout'
    assert json.loads((root/work.name/'result.json').read_text())['accepted'] is False


def test_active_checker_blocks_recovery_without_touching_outputs(tmp_path, monkeypatch):
    work, run = job(tmp_path)
    monkeypatch.setattr(recovery, 'matching_checkers', lambda _: [{'pid': 123, 'ppid': 456}])
    monkeypatch.setattr(recovery, 'RECOVERY', tmp_path/'recoveries')
    with pytest.raises(BlockingIOError):
        recovery.recover(work, run)
    assert not (tmp_path/'recoveries').exists()
    assert (run/'drc/6_drc.log').read_text() == 'original'


def test_stale_success_cannot_be_accepted_after_failed_checker(tmp_path, monkeypatch):
    work, run = job(tmp_path)
    root = tmp_path/'recoveries'
    root.mkdir()
    monkeypatch.setattr(recovery, 'RECOVERY', root)
    put(work/'project/repair_family_probe_result.json', {'run_id': run.name})
    put(run/'drc/drc_result.json', {'exit_code': 0, 'status': 'clean', 'drc_mode': 'full',
        'gds_sha256': recovery.runner.sha(run/'results/6_final.gds')})
    monkeypatch.setattr(recovery.runner, 'call', lambda *args: (0, False))
    recovery.recover(work, run)
    assert not json.loads((root/work.name/'result.json').read_text())['accepted']
    assert json.loads((work/'complete.json').read_text())['status'] == 'inconclusive_timeout'
