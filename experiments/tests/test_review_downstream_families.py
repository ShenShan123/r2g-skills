import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from review_downstream_families import apply_decisions


def test_transitive_merges_without_changing_input():
    rows = [{'task_id': str(i), 'family_id': f'f{i}'} for i in range(4)]
    decisions = [{'families': ['f2', 'f3'], 'reason': 'source', 'evidence': ['a']},
                 {'families': ['f1', 'f2'], 'reason': 'source', 'evidence': ['b']}]
    mapping = apply_decisions(rows, decisions)
    assert mapping == {'0': 'f0', '1': 'f1', '2': 'f1', '3': 'f1'}
    assert rows[2]['family_id'] == 'f2'


def test_unknown_or_unsupported_merges_rejected():
    rows = [{'task_id': 'a', 'family_id': 'f1'}, {'task_id': 'b', 'family_id': 'f2'}]
    with pytest.raises(ValueError, match='known'):
        apply_decisions(rows, [{'families': ['f1', 'missing']}])
    with pytest.raises(ValueError, match='evidence'):
        apply_decisions(rows, [{'families': ['f1', 'f2'], 'reason': 'same name'}])
