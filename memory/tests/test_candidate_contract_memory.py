"""Memory-side candidate contract, technical stop and stale-proof binding (2026-10-17).

RECIPE and TEHM candidates must carry a `recheck` mapped from their own recorded evidence
(no default that hides a missing value); a recipe must round-trip through the shared
executor (candidate -> --next -> --apply on a scratch project); an episode whose shared
repair recorded a technical failure stops before any repair turn; and the turn-entry
partial-execution proof binds the CURRENT run and config, never a stale ppa.json pointer.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'memory/scripts'))
sys.path.insert(0, str(ROOT / 'r2g-skills/signoff-loop/knowledge'))
sys.path.insert(0, str(ROOT / 'r2g-skills/signoff-loop/scripts/reports'))
import candidate_contract as C  # noqa: E402
import t2a_adapters as A  # noqa: E402
import t2a_runtime as T  # noqa: E402
import technical_failure as TF  # noqa: E402
from test_t2a_partial_restoration import bound_run, partial_task, write  # noqa: E402

SIT = {'check': 'drc', 'violation_class': 'm3.2', 'platform': 'sky130hd', 'die_mode': 'auto',
       'util_band': '31-60', 'timeout': False}


def _recipe_file(tmp_path, check='drc'):
    sit = {k: SIT.get(k) for k in A.RECIPE_KEYS}
    sit['check'] = check
    path = tmp_path / 'RECIPE.json'
    path.write_text(json.dumps({'status': 'FROZEN', 'recipes': [{
        'id': 'recipe_abc', 'situation': sit, 'config_edits': {'CORE_UTILIZATION': '40'},
        'rerun_from': 'floorplan', 'unit_ids': ['u1', 'u2'], 'first_time': ''}]}))
    return path, sit


@pytest.mark.parametrize('evidence,expected', [('drc', 'drc'), ('orfs_stage', 'route'), ('timing', 'timing')])
def test_recipe_recheck_is_mapped_from_the_recipes_recorded_check(tmp_path, evidence, expected):
    path, sit = _recipe_file(tmp_path, evidence)
    out = A.recipe_strategies(path, sit, {'CORE_UTILIZATION': '60'})
    assert out and out[0]['recheck'] == expected and C.contract_errors(out[0]) == []


def test_a_recipe_with_an_unmappable_check_is_invalid_not_defaulted(tmp_path):
    path, sit = _recipe_file(tmp_path, 'UNKNOWN')
    out = A.recipe_strategies(path, sit, {'CORE_UTILIZATION': '60'})
    assert out and out[0].get('recheck') is None and C.contract_errors(out[0])


def test_every_frozen_t1_recipe_now_maps_to_a_valid_candidate():
    frozen = Path('/data1/zhangdy/RTL/RTL_testbench/_r2g_eval/t2a-table2-t1-20261016/controls/RECIPE.json')
    if not frozen.exists():
        pytest.skip('frozen T1 recipe library not present on this host')
    data = json.loads(frozen.read_text())
    assert len(data['recipes']) == 112
    bad = []
    for recipe in data['recipes']:
        out = A.recipe_strategies(frozen, recipe['situation'], {}, limit=200)
        cand = next(c for c in out if c['id'] == recipe['id'])
        if C.contract_errors(cand):
            bad.append((recipe['id'], recipe['situation'].get('check')))
    assert bad == []


@pytest.mark.parametrize('rule_check,query,expected,ok', [
    ('orfs_stage', 'route', 'route', True), ('drc', 'drc', 'drc', True), ('timing', 'timing', 'timing', True),
    (None, 'drc', None, False), ('drc', 'timing', None, False), ('UNKNOWN', 'drc', None, False)])
def test_tehm_recheck_comes_from_the_rules_recorded_check(rule_check, query, expected, ok):
    import runtime_router as R
    value, error = R.recheck_for_rule(rule_check, query)
    assert value == expected and (error is None) == ok


def test_a_recipe_round_trips_through_the_shared_executor_on_a_scratch_project(tmp_path, monkeypatch, capsys):
    import diagnose_signoff_fix as D
    project = tmp_path / 'project'
    (project / 'constraints').mkdir(parents=True)
    (project / 'reports').mkdir()
    cfg = project / 'constraints/config.mk'
    cfg.write_text('export CORE_UTILIZATION = 60\nexport PLATFORM = sky130hd\n')
    (project / 'reports/drc.json').write_text(json.dumps({'status': 'fail', 'total_violations': 4,
                                                          'categories': {'m3.2': {'count': 4}}}))
    path, sit = _recipe_file(tmp_path, 'drc')
    monkeypatch.setenv('R2G_MEMORY_BACKEND', 'none')
    monkeypatch.setenv('R2G_RECIPE_FILE', str(path))
    monkeypatch.setattr(D, 'build_plan', lambda *a, **k: {
        'check': 'drc', 'status': 'fail', 'strategies': [], 'residual_reason': None})
    monkeypatch.setattr(D, '_current_situation', lambda *a, **k: dict(sit))
    assert D.main([str(project), '--check', 'drc', '--next']) == 0
    assert capsys.readouterr().out.strip() == 'recipe_abc\tfloorplan\tdrc'
    assert D.main([str(project), '--check', 'drc', '--apply', 'recipe_abc']) == 0
    assert 'CORE_UTILIZATION = 40' in cfg.read_text()


def test_the_episode_stops_on_a_recorded_technical_failure(tmp_path):
    (tmp_path / 'reports').mkdir()
    assert T.technical_failures(tmp_path) == []
    TF.record(tmp_path, 'invalid_candidate', source='recipe', detail='recipe_x: missing recheck', check='drc')
    rows = T.technical_failures(tmp_path)
    assert len(rows) == 1
    with pytest.raises(T.TechnicalFailure):
        T.raise_on_technical_failure(tmp_path)


def test_the_turn_entry_proof_binds_the_current_run_not_a_stale_ppa(tmp_path):
    project = tmp_path
    (project / 'constraints').mkdir()
    (project / 'reports').mkdir()
    (project / 'backend').mkdir()
    (project / 'constraints/config.mk').write_text('export CORE_UTILIZATION = 60\n')
    env = partial_task(project)                      # RUN_00 bound; ppa.json -> RUN_00
    (project / 'constraints/config.mk').write_text('export CORE_UTILIZATION = 55\n')   # recovery changed config
    newest = bound_run(project, 'RUN_01', env['R2G_TASK_MANIFEST'], 'DPL-0038', full=True)
    proof = T.partial_execution_proof(project, env=env)
    assert proof and proof['run_dir'] == str(newest) and proof['error_code'] == 'DPL-0038'


def test_a_newest_run_that_does_not_bind_the_current_config_gives_no_proof(tmp_path):
    project = tmp_path
    (project / 'constraints').mkdir()
    (project / 'reports').mkdir()
    (project / 'backend').mkdir()
    (project / 'constraints/config.mk').write_text('export CORE_UTILIZATION = 60\n')
    env = partial_task(project)
    bound_run(project, 'RUN_01', env['R2G_TASK_MANIFEST'], 'DPL-0038', full=True)
    (project / 'constraints/config.mk').write_text('export CORE_UTILIZATION = 50\n')   # config moved on
    assert T.partial_execution_proof(project, env=env) is None


def test_a_tehm_composition_maps_its_recheck_from_the_query_check(monkeypatch):
    """Compositions are derived from the current check by construction; on the place-abort
    path that check is orfs_stage, which the fix loop calls route (contract vocabulary)."""
    import types
    import runtime_router as R
    comp = types.ModuleType('tehm.retrieval.compose')
    comp.compositions = lambda *a, **k: [{'id': 'c1', 'reason': 'r', 'predicted_severity': 0.1,
                                          'edits': {'CORE_UTILIZATION': '30'}, 'dims': ['UTIL'],
                                          'tested': True, 'components': []}]
    vet = types.ModuleType('tehm.retrieval.vetoes')
    vet.verified_outcomes = lambda conn: []
    monkeypatch.setitem(sys.modules, 'tehm.retrieval.compose', comp)
    monkeypatch.setitem(sys.modules, 'tehm.retrieval.vetoes', vet)
    backend = SimpleNamespace(_open=lambda: (None, None))
    for check, expected in (('orfs_stage', 'route'), ('route', 'route'), ('drc', 'drc')):
        out = R._composition_strategies(backend, check, {}, {}, None)
        assert out[0]['recheck'] == expected and C.contract_errors(out[0]) == []
