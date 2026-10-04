import importlib.util
import json
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('freeze_experiment4_unseen_cohort', REPO / 'tools/freeze_experiment4_unseen_cohort.py')
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_pair_audit_reports_highest_similarity():
    profiles = {
        'a': {'structural_shingles': {1, 2, 3}},
        'b': {'structural_shingles': {1, 2, 4}},
        'c': {'structural_shingles': {8, 9}},
    }
    result = MODULE.build_pair_audit([{'task_id': key} for key in profiles], profiles)
    assert result['pair_count'] == 3
    assert result['maximum_similarity'] == 0.5
    assert result['highest_similarity_pairs'][0]['left'] == 'a'


def test_select_rows_rejects_same_repo_and_structural_clone():
    candidates = [
        {'task_id': 'used', 'source_group': 'repo-a', 'size_band': 'small_100_499'},
        {'task_id': 'same_repo', 'source_group': 'repo-a', 'size_band': 'small_100_499'},
        {'task_id': 'clone', 'source_group': 'repo-b', 'size_band': 'small_100_499'},
        {'task_id': 'good', 'source_group': 'repo-c', 'size_band': 'small_100_499'},
    ]
    profiles = {
        'used': {'normalized_source_sha256': 'n0', 'structural_source_sha256': 'x', 'structural_token_count': 100, 'structural_shingles': {1, 2}},
        'same_repo': {'normalized_source_sha256': 'n1', 'structural_source_sha256': 'y', 'structural_token_count': 100, 'structural_shingles': {8}},
        'clone': {'normalized_source_sha256': 'n2', 'structural_source_sha256': 'x', 'structural_token_count': 100, 'structural_shingles': {1, 2}},
        'good': {'normalized_source_sha256': 'n3', 'structural_source_sha256': 'z', 'structural_token_count': 100, 'structural_shingles': {9}},
    }
    chosen = MODULE.select_rows(
        candidates, profiles, seed='seed', split='hidden_test', band='small_100_499', count=1,
        used_groups={'repo-a'}, selected=[candidates[0]], threshold=0.9, minimum_tokens=40,
    )
    assert [row['task_id'] for row in chosen] == ['good']
