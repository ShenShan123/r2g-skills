import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import export_downstream_corpus as corpus


def row(key, cells, family=None):
    return {'task_id': key, 'mapped_cells': cells, 'export_candidate': True,
            'provisional_family_id': family or key, 'source_verification': {'status': 'PASS'},
            'run_dir': '/run/'+key, 'result_path': '/results/'+key, 'result_sha256': 'r',
            'repo': 'repo/'+(family or key), 'commit': 'c', 'top_module': 'top_'+key,
            'normalized_source_sha256': 'n'+key, 'source_closure_sha256': 's'+key,
            'structural_source_sha256': 't'+key, 'coverage_state': 'unknown',
            'physical_clean_flag': False, 'timing_label_review_required': True, 'artifacts': {}}


def test_queue_balances_sizes_without_losing_or_repeating_designs():
    rows = [row('s1', 100, 'shared'), row('s2', 101, 'shared'), row('s3', 150),
            row('m1', 500), row('m2', 900), row('l1', 2000), row('l2', 5000)]
    original = copy.deepcopy(rows)
    queue = corpus.queue_rows({'records': rows})
    assert [r['task_id'] for r in queue[:4]] == ['s1', 'm1', 'l1', 's3']
    assert {r['task_id'] for r in queue} == {r['task_id'] for r in rows}
    assert len(queue) == len(rows) and rows == original
    assert all('split' not in r for r in queue)


def test_unverified_source_rejected_and_ineligible_not_queued():
    bad = row('bad', 100)
    bad['source_verification']['status'] = 'FAIL'
    with pytest.raises(ValueError, match='Unverified'):
        corpus.queue_rows({'records': [bad]})
    bad['export_candidate'] = False
    assert corpus.queue_rows({'records': [bad]}) == []


def test_family_review_does_not_merge_identical_top_names():
    rows = corpus.queue_rows({'records': [row('a', 100), row('b', 200)]})
    for r in rows:
        r['top_module'] = 'fifo'
    review = corpus.family_review(rows)
    assert len(review['groups']) == 2
    assert review['family_reviewed'] is False
    assert len(review['same_top_across_groups_review_only']) == 1


def test_collect_preserves_failed_cases_and_unassigned_split(tmp_path):
    rows = corpus.queue_rows({'records': [row('a', 100), row('b', 200), row('c', 500)]})
    corpus.save_json(tmp_path/'checkpoints/a.json', {
        'task_id': 'a', 'status': 'PASS', 'readiness': 'review_label_coverage', 'elapsed_seconds': 4,
        'record': {'design_id': 'a', 'split': 'unassigned'},
        'audit': {'gate_distribution': {'n': 12}, 'occupied_grid_distribution': {'n': 5}}})
    corpus.save_json(tmp_path/'checkpoints/b.json', {
        'task_id': 'b', 'status': 'FAILED', 'error': 'invalid label', 'elapsed_seconds': 2})
    summary = corpus.collect(tmp_path, {'rows': rows}, status='batch_complete')
    assert summary['processed'] == 2 and summary['remaining'] == 1
    assert summary['counts'] == {'PASS': 1, 'FAILED': 1}
    assert summary['valid_gates'] == 12 and summary['occupied_grids'] == 5
    manifest = json.loads((tmp_path/'available_graphs.json').read_text())
    assert manifest['records'][0]['split'] == 'unassigned'
    assert manifest['excluded_or_failed'][0]['error'] == 'invalid label'
