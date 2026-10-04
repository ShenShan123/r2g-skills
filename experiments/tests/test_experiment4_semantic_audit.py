import copy
import importlib.util
from pathlib import Path

import pytest
import torch
from torch_geometric.data import HeteroData

SPEC = importlib.util.spec_from_file_location('semantic_audit', Path(__file__).parents[1] / 'experiment4_semantic_audit.py')
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def test_aliases_bus_offsets_and_constants():
    module = {'netnames': {'bus': {'bits': [10, 11], 'offset': 2},
                          'alias': {'bits': [10]}},
              'cells': {'g': {'connections': {'A': [10], 'Y': [11]}}},
              'ports': {'p': {'bits': [10, 11], 'offset': 2}}}
    truth = audit.logical_oracle(module)
    assert truth['aliases']['bus[2]'] == truth['aliases']['alias']
    assert truth['io_pin'] == {'p[2]', 'p[3]'}
    assert truth['edges'][audit.CORE_EDGES[1]] == {(('g', 'A'), '10'), (('g', 'Y'), '11')}
    assert list(audit.bit_names('up', {'bits': [1, 2], 'offset': 5, 'upto': 1})) == [('up[6]', '1'), ('up[5]', '2')]


def test_duplicates_do_not_inflate_precision():
    score = audit.identity_metrics(['a', 'a'], {'a', 'b'})
    assert score['tp'] == 1 and score['fp'] == 1 and score['fn'] == 1
    assert score['precision'] == score['recall'] == 0.5


def test_sta_uses_data_endpoint_not_capture_clock():
    report = '''Startpoint: FF1
Endpoint: FF2
  0.1 0.2 ^ FF1/Q (DFF)
  0.0 0.3 ^ FF2/D (DFF)
      0.3 data arrival time
      1.0 ^ FF2/CLK (DFF)
      0.7 slack (MET)
Startpoint: FF3
  0.0 0.4 v FF2/D (DFF)
      0.4 data arrival time
      -0.2 slack (VIOLATED)
'''
    assert audit.read_slacks(report) == {'values': {'FF2/D': -0.2}, 'blocks': 2, 'unparsed_blocks': 0}
    assert audit.read_slacks('Startpoint: bad\n').get('unparsed_blocks') == 1


def test_missing_values_are_not_successes():
    result = audit.compare_values({'a': float('nan'), 'b': 1000}, {'a': 1., 'b': 1.}, .001)
    assert result['missing'] == 1
    assert result['incorrect'] == 1


def test_hpwl_tolerance_accounts_for_two_dbu_rounded_axes():
    assert audit.POLICY['hpwl_atol_um'] > 2 * audit.POLICY['coordinate_atol_um']


def test_independent_def_wirelength_parser(tmp_path):
    path = tmp_path / 'route.def'
    path.write_text('''UNITS DISTANCE MICRONS 1000 ;
NETS 2 ;
- n1 ( a A ) + ROUTED M1 ( 0 0 ) ( 1000 * ) NEW M2 ( 1000 0 ) ( * 2000 ) ;
- n2 ( b Y ) + FIXED M1 ( -500 100 ) ( 500 100 ) ;
END NETS
''')
    assert audit.read_routed_lengths(path) == {'n1': 3.0, 'n2': 1.0}


def test_independent_spef_ground_cap_parser(tmp_path):
    path = tmp_path / 'route.spef'
    path.write_text('''*SPEF "IEEE 1481-1998"
*C_UNIT 1 FF
*NAME_MAP
*1 n1
*D_NET *1 10
*CAP
1 *1:1 2.5
2 *1:1 *1:2 7.5
*RES
*END
''')
    assert audit.read_spef_ground_caps(path) == pytest.approx({'n1': 0.0025})


def test_no_coverage_is_not_a_pass():
    assert audit.aggregate_status({'NOT_APPLICABLE': 16}) == 'NOT_APPLICABLE'
    assert audit.aggregate_status({}) == 'NOT_APPLICABLE'
    with pytest.raises(ValueError):
        audit.row_values(['a', 'b'], [1.])
    with pytest.raises(ValueError):
        audit.row_values(['a', 'a'], [1., 2.])


@pytest.fixture
def fixture(tmp_path):
    truth = audit.logical_oracle({'netnames': {'a': {'bits': [2]}, 'b': {'bits': [3]}},
                                 'cells': {'g': {'connections': {'A': [2], 'Y': [3]}}},
                                 'ports': {'a': {'bits': [2]}, 'b': {'bits': [3]}}})
    physical = {}
    for stage in audit.STAGES:
        g = HeteroData()
        g['gate'].inst_name = ['g']
        g['pin'].inst_name, g['pin'].pin_name = ['g', 'g'], ['A', 'Y']
        g['net'].net_name, g['io_pin'].iopin_name = ['a', 'b'], ['a', 'b']
        for node in audit.NODES:
            fields = {'gate': ['x_um', 'y_um', 'center_x_um', 'center_y_um', 'center_x_normalized', 'center_y_normalized'],
                      'pin': ['pin_x_um', 'pin_y_um'], 'io_pin': ['pin_x_um', 'pin_y_um'],
                      'net': ['net_bbox_width_um', 'net_bbox_height_um', 'hpwl_um']}[node]
            n = 1 if node == 'gate' else 2
            g[node].x_schema = fields
            g[node].x = torch.full((n, len(fields)), float('nan'))
            if stage in ('cts', 'route') and node == 'gate':
                g[node].x[:] = torch.tensor([1., 2., 1.5, 2.5, .15, .125])
            if stage in ('cts', 'route') and node == 'pin':
                g[node].x[:] = torch.tensor([[1., 2.], [3., 4.]])
            if stage != 'floorplan' and node == 'io_pin':
                g[node].x[:] = torch.tensor([[0., 0.], [10., 20.]])
            if stage in ('cts', 'route') and node == 'net':
                g[node].x[:] = torch.tensor([[1., 2., 3.], [7., 16., 23.]])
            g[node].y_schema = ['setup_slack_ns', 'hold_slack_ns']
            g[node].y = torch.ones(n, 2)
            g[node].y_valid_mask = torch.ones(n, 2, dtype=torch.bool)
        for relation, edges in zip(audit.CORE_EDGES, [[[0, 0], [0, 1]], [[0, 1], [0, 1]], [[0, 1], [0, 1]]]):
            g[relation].edge_index = torch.tensor(edges, dtype=torch.int64)
            g[relation].edge_attr = torch.ones(2, 2)
        g.global_feature_schema = ['die_width_um', 'die_height_um', 'die_area_um2', 'dbu_per_um']
        g.global_features = torch.tensor([[10., 20., 200., 1000.]])
        dest = tmp_path / 'stages' / stage
        dest.mkdir(parents=True)
        torch.save(g, dest / 'heterograph.pt')
        if stage != 'floorplan':
            physical[stage] = dict(zip(g.global_feature_schema, g.global_features[0].tolist()))
            physical[stage]['instances'] = {'g': {'x_um': 1., 'y_um': 2., 'center_x_um': 1.5, 'center_y_um': 2.5,
                                                  'center_x_normalized': .15, 'center_y_normalized': .125,
                                                  'status': 'PLACED', 'is_block': False}}
            physical[stage]['pins'] = {'g': {
                'A': {'pin_x_um': 1., 'pin_y_um': 2., 'status': 'PLACED', 'is_block': False},
                'Y': {'pin_x_um': 3., 'pin_y_um': 4., 'status': 'PLACED', 'is_block': False}}}
            physical[stage]['io_pins'] = {
                'a': {'pin_x_um': 0., 'pin_y_um': 0.}, 'b': {'pin_x_um': 10., 'pin_y_um': 20.}}
            physical[stage]['nets'] = {
                'a': {'endpoints': ['io:a', 'pin:["g","A"]'], 'endpoint_count': 2,
                      'valid_endpoint_count': 2, 'net_bbox_width_um': 1., 'net_bbox_height_um': 2., 'hpwl_um': 3.},
                'b': {'endpoints': ['io:b', 'pin:["g","Y"]'], 'endpoint_count': 2,
                      'valid_endpoint_count': 2, 'net_bbox_width_um': 7., 'net_bbox_height_um': 16., 'hpwl_um': 23.}}
    slacks = {k: {'values': {'a': 1., 'b': 1., 'g/A': 1., 'g/Y': 1.}, 'blocks': 4, 'unparsed_blocks': 0}
              for k in ['setup_slack_ns', 'hold_slack_ns']}
    return tmp_path, truth, physical, slacks


def test_hand_computed_fixture_passes_scoped_checks(fixture):
    result = audit.audit_graphs(*fixture)
    assert all(c['status'] == 'PASS' for c in result['checks'])
    assert result['core_semantic_status'] == 'NOT_VERIFIED'


@pytest.mark.parametrize('mutation,expected_group', [
    ('edge', 'topology'), ('duplicate', 'identity'), ('bounds', 'topology'),
    ('nan_label', 'label'), ('unit', 'numeric'), ('leak', 'causal'), ('label_change', 'alignment')])
def test_mutations_detected(fixture, mutation, expected_group):
    root, truth, physical, slacks = fixture
    stage = 'floorplan' if mutation == 'leak' else 'cts'
    path = root / 'stages' / stage / 'heterograph.pt'
    g = torch.load(path, weights_only=False)
    if mutation == 'edge':
        g[audit.CORE_EDGES[1]].edge_index[1, 0] = 1
    elif mutation == 'duplicate':
        g['net'].net_name = ['a', 'a']
    elif mutation == 'bounds':
        g[audit.CORE_EDGES[1]].edge_index[1, 0] = -1
    elif mutation == 'nan_label':
        g['pin'].y[:] = float('nan')
        g['pin'].y_valid_mask[:] = False
    elif mutation == 'unit':
        g['gate'].x *= 1000
    elif mutation == 'leak':
        g['net'].x[:] = 12.
    elif mutation == 'label_change':
        g['pin'].y[0, 0] = -5.
    torch.save(g, path)
    result = audit.audit_graphs(root, truth, physical, slacks)
    assert result['groups'][expected_group]['status'] != 'PASS'


def test_entity_order_permutation_preserves_semantic_scores(fixture):
    root, truth, physical, slacks = fixture
    path = root / 'stages/cts/heterograph.pt'
    g = torch.load(path, weights_only=False)
    g['net'].net_name = ['b', 'a']
    for key in ('x', 'y', 'y_valid_mask'):
        g['net'][key] = g['net'][key].flip(0)
    for rel in audit.CORE_EDGES[1:]:
        g[rel].edge_index[1] = 1 - g[rel].edge_index[1]
    torch.save(g, path)
    assert all(c['status'] == 'PASS' for c in audit.audit_graphs(root, truth, physical, slacks)['checks'])


def test_routed_length_ignores_relative_patch_rectangles(tmp_path):
    path = tmp_path/'route.def'
    path.write_text('''UNITS DISTANCE MICRONS 1000 ;
NETS 1 ;
- n ( a P ) + ROUTED met1 ( 80000 60000 ) ( 81000 * )
 NEW met1 ( 81000 60000 ) RECT ( -255 -70 0 70 ) ;
END NETS
''')
    assert audit.read_routed_lengths(path) == {'n': 1.}
