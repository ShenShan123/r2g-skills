import importlib.util
import json
from pathlib import Path
import sys

import pytest
import torch

sys.path.insert(0, str(Path(__file__).parents[1]))
import experiment4_metamorphic_canary as canary
import experiment4_batch_smoke as batch_smoke
import report_experiment4_geometry_validation as geometry_report

SPEC = importlib.util.spec_from_file_location('audit_fixtures', Path(__file__).with_name('test_experiment4_semantic_audit.py'))
fixtures = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixtures)
fixture = fixtures.fixture


def test_payload_invariant_to_consistent_reordering(fixture):
    root = fixture[0]
    g = torch.load(root / 'stages/cts/heterograph.pt', weights_only=False)
    before = canary.core_payload(g)
    g['net'].net_name = list(reversed(g['net'].net_name))
    for field in ('x', 'y', 'y_valid_mask'):
        g['net'][field] = g['net'][field].flip(0)
    for rel in canary.CORE_EDGES[1:]:
        g[rel].edge_index[1] = 1 - g[rel].edge_index[1]
    assert canary.core_payload(g) == before
    g['pin'].y[0, 0] = 42
    assert canary.core_payload(g) != before
    g['net'].net_name = ['a', 'a']
    with pytest.raises(ValueError):
        canary.core_payload(g)


def test_variant_manifest_preserves_original_and_other_inputs(tmp_path):
    source = tmp_path / 'original.json'
    data = {'artifacts': {'yosys_netlist': {'path': 'original.v', 'sha256': 'old'},
                          'placement_def': {'path': 'place.def', 'sha256': 'unchanged'}}}
    source.write_text(json.dumps(data))
    netlist = tmp_path / 'variant.v'
    netlist.write_text('module x; endmodule\n')
    dest = tmp_path / 'variant.json'
    canary.variant_manifest(source, netlist, dest)
    new = json.loads(dest.read_text())
    assert json.loads(source.read_text()) == data
    assert new['artifacts']['yosys_netlist']['sha256'] == canary.digest(netlist)
    assert new['artifacts']['placement_def'] == {'path': str(tmp_path / 'place.def'), 'sha256': 'unchanged'}


def test_batch_smoke_with_masked_features(fixture):
    path = fixture[0] / 'stages/cts/heterograph.pt'
    graphs = [torch.load(path, weights_only=False) for _ in range(2)]
    assert batch_smoke.validate_batch(graphs)['status'] == 'PASS'
    graphs[1]['pin'].y_valid_mask[0, 0] = False
    with pytest.raises(ValueError):
        batch_smoke.validate_batch(graphs)


def test_slack_negative_control(fixture):
    assert '1.05 slack (MET)' in canary.perturb_slack('  -0.20 slack (VIOLATED)\n')
    with pytest.raises(ValueError):
        canary.perturb_slack('empty report')
    g = torch.load(fixture[0] / 'stages/cts/heterograph.pt', weights_only=False)
    features, all_values = canary.core_payload(g, False), canary.core_payload(g)
    g['pin'].y += 1.25
    assert canary.core_payload(g, False) == features
    assert canary.core_payload(g) != all_values


def test_geometry_scope_check_detects_unrelated_changes(fixture):
    import copy
    g = torch.load(fixture[0] / 'stages/cts/heterograph.pt', weights_only=False)
    changed = copy.deepcopy(g)
    changed['gate'].x[0, list(changed['gate'].x_schema).index('center_x_um')] += 1.
    assert geometry_report.compare_graphs(g, changed)['status'] == 'PASS'
    changed['pin'].y[0, 0] += 1.
    assert geometry_report.compare_graphs(g, changed)['status'] == 'FAIL'
