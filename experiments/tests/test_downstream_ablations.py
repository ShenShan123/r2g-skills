import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prepare_downstream_ablations as prepare
import run_downstream_ablations as run


def parent_manifest():
    records = []
    for i in range(8):
        records.append({'design_id': f't{i}', 'family_id': f'f{i}', 'source_sha256': f's{i}',
                        'split': 'train'})
    records += [{'design_id': 'v', 'family_id': 'v', 'source_sha256': 'sv', 'split': 'validation'},
                {'design_id': 'q', 'family_id': 'q', 'source_sha256': 'sq', 'split': 'test'}]
    return {'formal_training_ready': True, 'pilot_subset': False, 'records': records}


def test_nested_family_subsets_preserve_heldout():
    parent = parent_manifest()
    a = prepare.build_subset(parent, .25, 9, 'sha')
    b = prepare.build_subset(parent, .50, 9, 'sha')
    fa = set(a['ablation']['selected_train_families'])
    fb = set(b['ablation']['selected_train_families'])
    assert len(fa) == 2 and len(fb) == 4 and fa < fb
    assert [r for r in a['records'] if r['split'] != 'train'] == parent['records'][-2:]


def test_ablation_matrix_is_exactly_27_jobs(tmp_path):
    rows = run.jobs(tmp_path/'full.json', tmp_path/'25.json', tmp_path/'50.json')
    assert len(rows) == len({row['name'] for row in rows}) == 27
    assert {row['condition'] for row in rows} == {'scale25', 'scale50', 'no_geometry'}
    assert {row['target'] for row in rows} == set(run.TARGETS)
    assert {row['seed'] for row in rows} == set(run.SEEDS)
    assert all(row['stage'] == 'cts' and row['model'] == 'gine' for row in rows)


def test_geometry_command_is_frozen_in_identity(tmp_path):
    job = [row for row in run.jobs(tmp_path/'full', tmp_path/'25', tmp_path/'50')
           if row['condition'] == 'no_geometry'][0]
    command = run.command(Path('/python'), tmp_path/'runtime', tmp_path/'out', job, '1,2', 'train')
    assert command[command.index('--feature-ablation')+1] == 'no_physical_geometry'
    assert '--finalize' not in command


def test_scale_only_matrix_supports_lower_data_fractions(tmp_path):
    rows = run.scale_jobs([(5, tmp_path/'5.json'), (10, tmp_path/'10.json')])
    assert len(rows) == len({row['name'] for row in rows}) == 18
    assert {row['condition'] for row in rows} == {'scale5', 'scale10'}
    assert {row['target'] for row in rows} == set(run.TARGETS)
    assert {row['seed'] for row in rows} == set(run.SEEDS)
