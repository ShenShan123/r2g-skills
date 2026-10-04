import copy
import csv
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch
from torch_geometric.data import HeteroData

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model import GraphHead
from stage_data import (FEATURES, GLOBALS, NODE_TYPES, Preprocessor,
                        SchemaFeatureEncoder, labels, split_families, validate_manifest)
from stage_train import evaluate, metrics, grid_values
import stage_train
from stage_data import digest, load_graph, save_json


def graph():
    g = HeteroData()
    g.encode_map_sha256 = 'same-map'
    g.global_feature_schema = GLOBALS
    g.global_features = torch.tensor([[10., 20., 200., 3., 2.]])
    for kind in NODE_TYPES:
        n = {'gate': 3, 'net': 3, 'pin': 4, 'io_pin': 2}[kind]
        names = FEATURES[kind] + ['graph_id']
        g[kind].x_schema = names
        g[kind].x = torch.ones(n, len(names))
        g[kind].x[:, -1] = 98765
    g['net'].y_schema = ['routed_wirelength_um', 'ground_cap_pF']
    g['net'].y_unit = ['um', 'pF']
    g['net'].y = torch.tensor([[0., 0.], [4., .02], [float('nan'), float('nan')]])
    g['net'].y_valid_mask = torch.isfinite(g['net'].y)
    g['gate'].y_schema = ['cell_congestion']
    g['gate'].y_unit = ['dimensionless']
    g['gate'].y = torch.tensor([[.2], [.2], [float('nan')]])
    g['gate'].y_valid_mask = torch.isfinite(g['gate'].y)
    g['gate'].target_grid = torch.tensor([[1, 2], [1, 2], [-1, -1]])
    g['gate', 'has', 'pin'].edge_index = torch.tensor([[0, 0, 1, 2], [0, 1, 2, 3]])
    g['pin', 'connects_to', 'net'].edge_index = torch.tensor([[0, 1, 2, 3], [0, 1, 1, 2]])
    g['io_pin', 'connects_to', 'net'].edge_index = torch.tensor([[0, 1], [0, 2]])
    g['net', 'rc_coupling', 'net'].edge_index = torch.tensor([[0, 1], [1, 0]])
    return g


def model(p, layers=2):
    args = SimpleNamespace(hid_dim=8, task='regression', task_level='node', model='gine',
                           num_gnn_layers=layers, src_dst_agg='add', num_head_layers=2,
                           use_bn=False, act_fn='relu', dropout=0., layer_norm=True)
    return GraphHead(args, SchemaFeatureEncoder(p.state, 8))


def test_split_and_normalization_isolation():
    rows = [{'design_id': str(i), 'family_id': str(i//2), 'source_sha256': str(i)} for i in range(8)]
    rows = split_families(rows)
    validate_manifest({'records': rows}, pilot=True)
    for i in range(0, 8, 2):
        assert rows[i]['split'] == rows[i+1]['split']
    with pytest.raises(ValueError, match='reviewed'):
        validate_manifest({'records': rows})
    broken = copy.deepcopy(rows)
    broken[1]['split'] = 'test' if broken[0]['split'] != 'test' else 'train'
    with pytest.raises(ValueError, match='overlaps'):
        validate_manifest({'records': broken}, pilot=True)
    p = Preprocessor.fit([graph()], 'wirelength')
    state = copy.deepcopy(p.state)
    heldout = graph()
    heldout['net'].y[:2, 0] *= 1000
    heldout['gate'].x[:, 0] = 5000
    transformed = p.transform(heldout)
    assert p.state == state
    assert torch.equal(transformed.x[:3, 0], torch.zeros(3))  # unknown category


@pytest.mark.parametrize('target', ['wirelength', 'ground_cap', 'congestion'])
def test_mask_raw_roundtrip_and_relation_whitelist(target):
    g = graph()
    p = Preprocessor.fit([g], target)
    d = p.transform(g)
    assert d.valid_mask.sum() == 2
    assert torch.isnan(d.y[~d.valid_mask]).all()
    torch.testing.assert_close(p.inverse(d.y[d.valid_mask]), d.y_raw[d.valid_mask], atol=1e-7, rtol=1e-6)
    assert d.edge_index.size(1) == 20  # only three logical relations plus reverse
    assert d.edge_types.max() == 5
    assert not (d.x == 98765).any()  # graph ID is never an input
    changed = graph()
    changed['net', 'rc_coupling', 'net'].edge_index = torch.zeros(2, 100, dtype=torch.long)
    assert torch.equal(p.transform(changed).edge_index, d.edge_index)


def test_congestion_gate_offset_and_grid_is_not_input(tmp_path):
    g = graph()
    p = Preprocessor.fit([g], 'congestion')
    d = p.transform(g)
    assert torch.where(d.valid_mask)[0].tolist() == [0, 1]
    assert d.entity_index[d.valid_mask].tolist() == [0, 1]
    changed = copy.deepcopy(g)
    changed['gate'].target_grid += 123
    other = p.transform(changed)
    assert torch.equal(other.x, d.x)
    assert torch.equal(other.edge_index, d.edge_index)
    args = SimpleNamespace(pilot=True, layers=2, neighbors=2, batch_size=1, target='congestion')
    result = evaluate(model(p), [{'design_id': 'a'}, {'design_id': 'b'}], {'a': d, 'b': d},
                      p, args, torch.device('cpu'), tmp_path/'pred.npz')
    assert result['pooled']['n'] == 4
    assert result['occupied_grid']['pooled']['n'] == 2
    import numpy as np
    with np.load(tmp_path/'pred.npz') as saved:
        assert 'gate_index' in saved and 'net_index' not in saved
        assert saved['target_grid'].shape == (4, 2)


def test_grid_evaluation_weights_grids_not_gate_count():
    pred, truth = grid_values([0, 2, 5], [1, 1, 2], [[0, 0], [0, 0], [0, 1]])
    assert pred == [1, 5] and truth == [1, 2]
    assert metrics(pred, truth)['mae'] == 1.5
    with pytest.raises(ValueError, match='Inconsistent'):
        grid_values([0, 2], [1, 2], [[0, 0], [0, 0]])


def test_congestion_sidecar_hash_and_value_checks(tmp_path):
    g = graph()
    g.prediction_stage, g.feature_cutoff = 'cts', 'placement'
    g.topology_source, g.y_is_raw_physical_value = 'verilog', True
    g.normalization_applied, g.leakage_warning = False, ''
    g['gate'].inst_name = ['a', 'b', 'c']
    path, evidence, sidecar = tmp_path/'graph.pt', tmp_path/'validation.json', tmp_path/'labels.csv'
    torch.save(g, path)
    save_json(evidence, {'status': 'PASS'})
    fields = ['inst_name', 'cell_congestion', 'congestion_valid', 'congestion_grid_x',
              'congestion_grid_y', 'congestion_label_unit', 'congestion_label_transform']
    with sidecar.open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(fields)
        writer.writerows([['a', '.2', 1, 1, 2, 'dimensionless', 'none'],
                          ['b', '.2', 1, 1, 2, 'dimensionless', 'none'],
                          ['c', 'nan', 0, -1, -1, 'dimensionless', 'none']])
    row = {'graphs': {'cts': {'path': str(path), 'sha256': digest(path),
           'validation_path': str(evidence), 'validation_sha256': digest(evidence)}},
           'congestion_labels': {'path': str(sidecar), 'sha256': digest(sidecar)}}
    assert load_graph(row, 'cts', 'congestion')['gate'].target_grid[:2].tolist() == [[1, 2], [1, 2]]
    sidecar.write_text(sidecar.read_text().replace('.2', '.3'))
    with pytest.raises(ValueError, match='sidecar changed'):
        load_graph(row, 'cts', 'congestion')
    row['congestion_labels']['sha256'] = digest(sidecar)
    with pytest.raises(ValueError, match='value mismatch'):
        load_graph(row, 'cts', 'congestion')


def test_invalid_target_and_unit_fail_closed():
    g = graph()
    g['net'].y_valid_mask[0, 0] = False
    with pytest.raises(ValueError, match='validity'):
        labels(g, 'wirelength')
    g = graph()
    g['net'].y_unit[1] = 'fF'
    with pytest.raises(ValueError, match='unit'):
        labels(g, 'ground_cap')


def test_missing_position_mask():
    g = graph()
    g['gate'].x[:, FEATURES['gate'].index('placement_valid')] = 0
    p = Preprocessor.fit([g], 'wirelength')
    d = p.transform(g)
    idx = 2*FEATURES['gate'].index('x_um')
    assert d.x[:3, idx:idx+2].count_nonzero() == 0


def test_no_physical_geometry_ablation_removes_values_and_validity_indicators():
    g = graph()
    p = Preprocessor.fit([g], 'wirelength', 'no_physical_geometry')
    for kind in NODE_TYPES:
        names = [column['name'] for column in p.state['nodes'][kind]]
        assert not ({'x_um', 'y_um', 'pin_x_um', 'pin_y_um', 'hpwl_um',
                     'placement_valid', 'pin_position_valid', 'hpwl_valid',
                     'die_width_um', 'die_height_um', 'die_area_um2'} & set(names))
    d = p.transform(g)
    assert d.edge_index.size(1) == 20
    assert d.valid_mask.sum() == 2


def test_constant_training_column_does_not_amplify_heldout():
    p = Preprocessor.fit([graph()], 'wirelength')
    heldout = graph()
    idx = FEATURES['gate'].index('drive_strength')
    heldout['gate'].x[:, idx] = 2
    assert p.transform(heldout).x[:3, 2*idx].tolist() == [1., 1., 1.]


def test_seed_only_and_negative_one_is_valid_scaled_label():
    p = Preprocessor.fit([graph()], 'wirelength')
    d = p.transform(graph())
    d.valid_mask[:] = True
    d.y[:] = -1  # a legitimate normalized target
    d.batch_size = 1
    net = model(p)
    pred, _, y = net(d)
    assert pred.shape == (1, 1) and y.tolist() == [-1]
    (pred.reshape(-1)-y).square().mean().backward()
    assert net.layers[0].eps.requires_grad


def test_evaluation_each_target_once_and_singleton_batch():
    torch.set_num_threads(1)
    p = Preprocessor.fit([graph()], 'wirelength')
    d = p.transform(graph())
    args = SimpleNamespace(pilot=True, layers=2, neighbors=2, batch_size=1, target='wirelength')
    result = evaluate(model(p), [{'design_id': 'a'}], {'a': d}, p, args, torch.device('cpu'))
    assert result['pooled']['n'] == 2
    assert result['unit'] == 'um'
    assert result['per_design']['a']['n'] == 2


def test_metrics_known_units_and_constant_target():
    m = metrics([2, 6], [1, 4])
    assert m['mae'] == 1.5
    assert metrics([1], [1])['r2'] is None
    with pytest.raises(ValueError):
        metrics([float('inf')], [1])


def test_checkpoint_resume_matches_uninterrupted(tmp_path, monkeypatch):
    records = []
    for i, split in enumerate(('train', 'validation', 'test')):
        g = graph()
        g.prediction_stage = 'cts'
        g.feature_cutoff = 'placement'
        g.topology_source = 'verilog'
        g.y_is_raw_physical_value = True
        g.normalization_applied = False
        g.leakage_warning = ''
        path = tmp_path / f'{i}.pt'
        torch.save(g, path)
        evidence = tmp_path / f'{i}.json'
        save_json(evidence, {'status': 'PASS'})
        records.append({'design_id': str(i), 'family_id': str(i), 'source_sha256': str(i),
                        'split': split, 'graphs': {'cts': {'path': str(path), 'sha256': digest(path),
                        'validation_path': str(evidence), 'validation_sha256': digest(evidence)}}})
    manifest = tmp_path / 'manifest.json'
    save_json(manifest, {'records': records})
    args = SimpleNamespace(command='train', manifest=str(manifest), output=str(tmp_path / 'complete'),
                           stage='cts', target='wirelength', model='gine', device='cpu', epochs=2,
                           hidden=8, layers=2, neighbors=2, batch_size=1, cpu_threads=1,
                           lr=.001, seed=42, pilot=True, resume=False, finalize=False)
    stage_train.run(args)
    complete = torch.load(Path(args.output)/'last.pt', weights_only=False)
    args.output = str(tmp_path/'interrupted')
    original = stage_train.evaluate
    calls = 0
    def fail_second_validation(*a, **kw):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError('simulated interruption')
        return original(*a, **kw)
    monkeypatch.setattr(stage_train, 'evaluate', fail_second_validation)
    with pytest.raises(RuntimeError, match='simulated interruption'):
        stage_train.run(args)
    monkeypatch.setattr(stage_train, 'evaluate', original)
    args.resume = True
    stage_train.run(args)
    resumed = torch.load(Path(args.output)/'last.pt', weights_only=False)
    for name in complete['model']:
        torch.testing.assert_close(complete['model'][name], resumed['model'][name], rtol=0, atol=0)
    timing_fields = {'train_seconds', 'epoch_seconds'}
    assert [{k: v for k, v in row.items() if k not in timing_fields} for row in complete['history']] == [
        {k: v for k, v in row.items() if k not in timing_fields} for row in resumed['history']]
