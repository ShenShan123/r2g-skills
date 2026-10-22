"""A completed check must not be reopened by a memory candidate."""
import json
import sys
from types import SimpleNamespace

import pytest
import diagnose_signoff_fix as D


@pytest.mark.parametrize('backend', ['tehm', 'recipe'])
@pytest.mark.parametrize('check,status', [
    ('drc', 'clean'), ('drc', 'clean_beol'), ('drc', 'skipped'),
    ('lvs', 'clean'), ('route', 'clean'), ('timing', 'clean')])
def test_clean_check_never_consults_executable_memory(tmp_path, monkeypatch, capsys, backend, check, status):
    project = tmp_path / 'project'
    (project / 'constraints').mkdir(parents=True)
    (project / 'reports').mkdir()
    (project / 'constraints/config.mk').write_text('export CORE_UTILIZATION = 60\n')
    monkeypatch.setenv('R2G_MEMORY_BACKEND', 'tehm' if backend == 'tehm' else 'none')
    monkeypatch.delenv('R2G_RECIPE_FILE', raising=False)
    monkeypatch.setattr(D, 'build_plan', lambda *a, **k: {
        'check': check, 'status': status, 'strategies': [], 'residual_reason': None})
    def unexpected(*args, **kwargs):
        pytest.fail('A completed check consulted executable memory')
    monkeypatch.setitem(sys.modules, 'runtime_router', SimpleNamespace(
        signoff_strategies=unexpected, signoff_vetoes=unexpected))
    if backend == 'recipe':
        monkeypatch.setenv('R2G_RECIPE_FILE', str(tmp_path / 'not-read.json'))
        monkeypatch.setitem(sys.modules, 't2a_adapters', SimpleNamespace(recipe_strategies=unexpected))
    assert D.main([str(project), '--check', check, '--next']) == 0
    assert capsys.readouterr().out.startswith('STOP\t' + status)


def test_a_clean_check_cannot_force_apply_a_memory_candidate(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    (project / 'constraints').mkdir(parents=True)
    (project / 'reports').mkdir()
    config = project / 'constraints/config.mk'
    config.write_text('export CORE_UTILIZATION = 60\n')
    (project / 'reports/drc.json').write_text(json.dumps({
        'status': 'clean', 'total_violations': 0, 'categories': {}}))
    monkeypatch.setenv('R2G_MEMORY_BACKEND', 'tehm')
    monkeypatch.delenv('R2G_RECIPE_FILE', raising=False)
    strategy = {'id': 'tehm_test', 'config_edits': {'CORE_UTILIZATION': '40'},
                'auto_apply': True, 'rerun_from': 'floorplan', 'recheck': 'drc'}
    monkeypatch.setitem(sys.modules, 'runtime_router', SimpleNamespace(
        signoff_strategies=lambda **kw: [strategy], signoff_vetoes=lambda **kw: {}))
    assert D.main([str(project), '--check', 'drc', '--apply', 'tehm_test']) != 0
    assert config.read_text() == 'export CORE_UTILIZATION = 60\n'
