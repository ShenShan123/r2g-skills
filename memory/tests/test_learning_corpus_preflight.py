"""Metadata preparation cannot turn duplicate/contaminated designs into probes."""
import importlib.util
from pathlib import Path
import pytest

FILE = Path(__file__).resolve().parents[1] / 'evaluation/tools/learning_corpus_preflight.py'
spec = importlib.util.spec_from_file_location('learning_preflight_test', FILE)
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)


def item(i, owner=None, repository=None, sha=None, fp=None, frontend='verilog-native'):
    return {'design_id': str(i), 'owner': owner or 'o' + str(i),
            'repository': repository or 'repo' + str(i), 'source_hashes': [sha or str(i)],
            'fingerprint': fp, 'frontend': frontend}


@pytest.mark.parametrize('key', ['owner', 'repository', 'sha', 'fp'])
def test_shared_identity_never_crosses_learn_probe(key):
    a, b = item(1, **{key: 'same'}), item(2, **{key: 'same'})
    g = C.grouped([a, b], set())
    assert len(g) == 1 and set(g[0]['member_ids']) == {'1', '2'}
    assert C.grouped([b, a], set()) == g


def test_exact_past_overlap_quarantines_entire_component():
    g = C.grouped([item(1, owner='same', sha='past'), item(2, owner='same')], {'past'})
    assert g[0]['split_reservation'] == 'HELD_FOR_REVIEW'


def test_known_owner_and_unadapted_vhdl_are_not_independent_probes():
    for d in (item(1, owner='SecWorks'), item(2, frontend='ghdl-08')):
        assert C.grouped([d], set())[0]['split_reservation'] == 'HELD_FOR_REVIEW'


def test_unknown_fingerprints_do_not_merge_unrelated_designs():
    g = C.grouped([item(1), item(2)], set())
    assert len(g) == 2 and all(x['repair_qualified'] is False for x in g)


def test_paths_cannot_escape_source(tmp_path):
    with pytest.raises(ValueError): C.inside(tmp_path, '../outside')


def test_existing_preparation_is_not_overwritten(tmp_path):
    with pytest.raises(RuntimeError, match='never overwrite'): C.audit(tmp_path, tmp_path)
