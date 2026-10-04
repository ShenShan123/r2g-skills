import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from inventory_downstream_dataset import (COVERAGE, REQUIRED_RESULTS, aggregate_labels,
                                         coverage_state, group_sources, physical_record, inventory_graphs,
                                         sha256_file, verify_sources)


def test_coverage_unknown_is_not_complete():
    assert coverage_state({}) == 'unknown'
    assert coverage_state({'constraint_coverage': dict.fromkeys(COVERAGE, False)}) == 'unknown'
    counts = dict.fromkeys(COVERAGE, 0)
    assert coverage_state({'constraint_coverage': counts}) == 'zero_missing_counts'
    counts['unconstrained_endpoints'] = 7
    assert coverage_state({'constraint_coverage': counts}) == 'nonzero_missing_counts'


def test_physical_inputs_are_not_the_same_as_timing_eligibility(tmp_path):
    task = dict(task_id='task', repo_url='https://github.com/A/B.git', commit='commit',
                top_module='top', source_root=str(tmp_path))
    base = tmp_path / 'task/baseline'
    run = base / 'backend/run'
    (run / 'results').mkdir(parents=True)
    for name in REQUIRED_RESULTS:
        (run / 'results' / name).write_text('artifact')
    result = dict(task_id='task', run_dir=str(run), mapped_cells=100, strict_clean=True,
                  timing_evaluation_incomplete=False,
                  constraint_coverage=dict.fromkeys(COVERAGE, 0))
    result['constraint_coverage']['unconstrained_endpoints'] = 4
    (base / 'repair_family_probe_result.json').write_text(json.dumps(result))
    row = physical_record(task, tmp_path)
    assert row['raw_export_candidate']
    assert row['timing_label_review_required']
    assert row['coverage_state'] == 'nonzero_missing_counts'
    (run / 'results/4_cts.odb').write_text('')
    row = physical_record(task, tmp_path)
    assert not row['raw_export_candidate']
    assert row['missing_artifacts'] == ['4_cts.odb']
    result['run_dir'] = str(tmp_path.parent)
    (base / 'repair_family_probe_result.json').write_text(json.dumps(result))
    assert 'run_outside_baseline_project' in physical_record(task, tmp_path)['exclusion_reasons']


def test_source_hashes_checked_and_grouping_transitive(tmp_path):
    path = tmp_path / 'top.v'
    path.write_text('module top; endmodule')
    task = dict(source_root=str(tmp_path), source_closure_sha256='closure')
    index = {(str(tmp_path), 'closure'): [dict(path='top.v', sha256=sha256_file(path))]}
    assert verify_sources(task, index, {})['status'] == 'PASS'
    path.write_text('changed')
    assert verify_sources(task, index, {})['status'] == 'FAIL'
    rows = [dict(task_id='a', repo='repo1', normalized_source_sha256='x'),
            dict(task_id='b', repo='repo1', normalized_source_sha256='y'),
            dict(task_id='c', repo='repo2', normalized_source_sha256='y'),
            dict(task_id='d', repo='repo3', normalized_source_sha256='z')]
    assert group_sources(rows) == {'a': ['a', 'b', 'c'], 'd': ['d']}


def test_label_coverage_preserves_zero_and_never_multiplies_stages():
    columns = [dict(entity_type='net', column='ground_cap_pF', unit='pF',
                    total_count=10, mask_valid_count=7),
               dict(entity_type='gate', column='ir_drop_mV', unit='mV',
                    total_count=5, mask_valid_count=0)]
    graphs = [dict(status='recorded_pass', label_columns=columns),
              dict(status='requires_review', label_columns=columns)]
    rows = aggregate_labels(graphs)
    assert rows[0]['valid_count'] == 7
    assert rows[0]['coverage_fraction'] == .7
    assert rows[1]['designs_with_valid_labels'] == 0


def test_graph_stage_names_and_existing_record_status(tmp_path):
    base = tmp_path / 'exp1_example/generated'
    (base / 'statistics').mkdir(parents=True)
    stats = dict(status='PASS', stages=dict(route=dict(node_counts={'net': 3}, tensor_hashes={})),
                 column_statistics=[])
    (base / 'statistics/four_stage_data_statistics.json').write_text(json.dumps(stats))
    (base / 'four_stage.validation.json').write_text('{"status":"PASS"}')
    for stage in ('floorplan', 'placement', 'cts', 'route'):
        target = base / 'stages' / stage / 'heterograph.pt'
        target.parent.mkdir(parents=True)
        target.write_bytes(b'existing tensor')
    assert inventory_graphs(tmp_path)[0]['status'] == 'recorded_pass'
    (base / 'stages/placement/heterograph.pt').unlink()
    assert inventory_graphs(tmp_path)[0]['missing_stages'] == ['placement']
