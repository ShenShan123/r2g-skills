import importlib.util
from pathlib import Path

from r2g_agent import backend


def subject(tmp_path):
    (tmp_path / 'constraints').mkdir()
    (tmp_path / 'constraints/config.mk').write_text('export PLATFORM = sky130hd\n')
    return tmp_path


def test_incremental_tree_failure_is_bounded_and_keeps_signoff_settings(tmp_path):
    project = subject(tmp_path)
    log = "[ERROR RSZ-0074] Failed to build tree from global routes for pin 'a'"
    repair = backend.candidate(project, 'sky130hd', log)
    assert repair['from_stage'] == 'route'
    assert repair['config_edits'] == {'SKIP_INCREMENTAL_REPAIR': '1'}
    backend.apply(project, repair)
    assert backend.candidate(project, 'sky130hd', log) is None
    assert 'SKIP_ANTENNA_REPAIR' not in (project / 'constraints/config.mk').read_text()


def test_unrelated_route_failure_is_not_guessed(tmp_path):
    assert backend.candidate(subject(tmp_path), 'sky130hd', '[ERROR GRT-0232] congestion') is None


def test_spef_encoding_requires_real_negative_index_and_retains_other_hooks(tmp_path):
    project = subject(tmp_path)
    path = project / 'backend/RUN_1/results/6_final.spef'
    path.parent.mkdir(parents=True)
    log = '[ERROR STA-1670] 6_final.spef line 12, syntax error'
    path.write_text('*NAME_MAP\n*1 ordinary[0]\n')
    assert backend.candidate(project, 'sky130hd', log) is None
    path.write_text('*NAME_MAP\n*1 bus[\\-1]\n')
    repair = backend.candidate(project, 'sky130hd', log)
    assert repair['from_stage'] == 'finish'
    backend.apply(project, repair)
    assert backend.candidate(project, 'sky130hd', log) is None


def test_spef_reencoding_preserves_every_decoded_token_and_rc_value():
    script = backend.signoff_scripts() / 'flow/normalize_spef_names.py'
    spec = importlib.util.spec_from_file_location('spef_normalizer', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    text = '*NAME_MAP\n*1 bus[\\-1]\n*D_NET *1 0.125\n*CONN\n*P bus[\\-1] I\n*CAP\n1 bus[\\-1] 0.125\n*RES\n1 bus[\\-1] *1:1 42.5\n*END\n'
    repaired, mappings = module.normalize(text)
    assert mappings
    assert [module.decode(x) for x in text.split()] == [module.decode(x) for x in repaired.split()]
    assert module.normalize(repaired) == (repaired, {})
