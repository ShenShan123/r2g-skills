import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from prepare_experiment4_unseen_inventory import (
    exclusion_reasons,
    source_fingerprints,
    structural_similarity,
    task_source_profile,
)


def test_repository_closure_and_content_exclusions_are_independent():
    exposed = {'tasks': {'old'}, 'groups': {'old_repo'}, 'closures': {'old_hash'}, 'rtl_hashes': {'shared'}}
    row = {'task_id': 'new', 'source_group': 'new_repo'}
    assert exclusion_reasons(row, {'source_closure_sha256': 'new_hash'}, {'unique'}, exposed) == []
    assert exclusion_reasons(row, {'source_closure_sha256': 'new_hash'}, {'shared'}, exposed) == ['exact_rtl_file_overlap']
    assert exclusion_reasons(dict(row, source_group='old_repo'), {}, {'unique'}, exposed) == ['previous_repository']
    assert exclusion_reasons(row, {'source_closure_sha256': 'old_hash'}, {'unique'}, exposed) == ['previous_source_closure']


def test_source_fingerprints_reject_missing_and_out_of_root(tmp_path):
    root = tmp_path / 'source'
    root.mkdir()
    (root / 'a.v').write_text('module a; endmodule\n')
    task = {'source_root': str(root), 'rtl_files': ['a.v']}
    assert len(source_fingerprints(task)) == 1
    for files in ([], ['absent.v'], ['../escape.v']):
        with pytest.raises(ValueError):
            source_fingerprints(dict(task, rtl_files=files))


def test_structural_profile_ignores_comments_formatting_and_identifiers(tmp_path):
    roots = []
    sources = [
        'module first(input a, output y); assign y = ~a; endmodule\n',
        '// renamed clone\nmodule second ( input b, output z );\nassign z=~b; endmodule\n',
    ]
    for index, source in enumerate(sources):
        root = tmp_path / str(index)
        root.mkdir()
        (root / 'top.v').write_text(source)
        roots.append(task_source_profile({'source_root': str(root), 'rtl_files': ['top.v']}))
    assert roots[0]['normalized_source_sha256'] != roots[1]['normalized_source_sha256']
    assert roots[0]['structural_source_sha256'] == roots[1]['structural_source_sha256']
    assert structural_similarity(roots[0], roots[1]) == 1.0


def test_near_duplicate_reason_is_explicit():
    exposed = {key: set() for key in ('tasks', 'groups', 'closures', 'rtl_hashes',
                                      'normalized_sources', 'structural_sources')}
    reasons = exclusion_reasons(
        {'task_id': 'new', 'source_group': 'new_repo'}, {}, {'unique'}, exposed,
        {'normalized_source_sha256': 'n', 'structural_source_sha256': 's'},
        [{'task_id': 'old', 'similarity': 0.95}],
    )
    assert reasons == ['near_duplicate_rtl']
