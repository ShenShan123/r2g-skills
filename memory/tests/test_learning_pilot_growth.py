"""Immutable growth commits and visibility, using the real canonical backend."""
import copy
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest
from tehm import neutral_export as nx
from tehm_backend import TehmMemoryBackend

FILE = Path(__file__).resolve().parents[1] / 'evaluation/tools/learning_pilot_growth.py'
spec = importlib.util.spec_from_file_location('learning_growth_test', FILE)
G = importlib.util.module_from_spec(spec)
spec.loader.exec_module(G)


def units(name, rolled_back=False, after=0, session='s'):
    row = {'fix_session_id': session, 'iter': 1, 'ts': '2026-10-15T00:00:00+00:00',
           'check': 'drc', 'violation_class': 'li.3', 'strategy': 'density_relief',
           'from_stage': 'floorplan', 'before': 4, 'after': after, 'verdict': 'cleared',
           'rolled_back': rolled_back, 'config_before': {'CORE_UTILIZATION': '20'},
           'config_delta': {'CORE_UTILIZATION': '15'},
           'situation': {'v': 'sit-v2', 'check': 'drc', 'platform': 'sky130hd',
                         'violation_class': 'li.3', 'die_mode': 'auto', 'util_band': '13-30',
                         'error_code': None, 'timeout': False, 'count_band': '1-5', 'perimeter_band': None}}
    return nx.build_units([{'path': '/diagnostic/fix_log.jsonl', 'design': name, 'platform': 'sky130hd',
                            'project': name, 'rows': [(1, row)]}])[0]


def bundle(name, u):
    return {'schema': 'learning-episode-export-v1', 'task': name, 'role': 'LEARN',
            'episode_status': 'ACCEPTED', 'input_hashes': {}, 'units': u,
            'diagnostic_fixture': True, 'cost': {'llm_requests': 0, 'total_tokens': 0},
            'candidate_audit': [], 'capture_complete': True}


@pytest.fixture
def growth(tmp_path):
    m0 = tmp_path / 'm0'
    b = TehmMemoryBackend(db_path=m0 / 'tehm.sqlite', artifact_root=m0 / 'artifacts')
    c, s = b._open()
    nx.import_units(c, s, units('historical'))
    b.rebuild()
    b.close()
    root = tmp_path / 'growth'
    G.initialize(root, m0, ['a', 'b'], ['p'])
    return root, m0


def count(path):
    with sqlite3.connect('file:' + str(path / 'tehm.sqlite') + '?mode=ro', uri=True) as c:
        return c.execute('SELECT count(*) FROM tehm_attempts').fetchone()[0]


def test_future_learning_is_invisible_to_an_already_bound_probe(growth, tmp_path):
    root, m0 = growth
    G.bind(root, 'p', 'PROBE', tmp_path / 'probe-before')
    first = G.commit(root, 'a', bundle('a', units('a')))
    G.bind(root, 'p', 'PROBE', tmp_path / 'probe-after')
    assert count(tmp_path / 'probe-before') == count(m0) == 1
    assert count(tmp_path / 'probe-after') == 2
    assert first['experienced_learn_designs'] == 1


def test_only_the_next_learn_task_can_commit(growth):
    root, _ = growth
    for task, data in [('p', bundle('p', units('p'))), ('b', bundle('b', units('b')))]:
        with pytest.raises(ValueError, match='next LEARN'):
            G.commit(root, task, data)


def test_wrong_design_units_and_probe_export_cannot_leak(growth):
    root, _ = growth
    with pytest.raises(ValueError, match='scope'):
        G.commit(root, 'a', bundle('a', units('p')))
    b = bundle('a', units('a'))
    b['role'] = 'PROBE'
    with pytest.raises(ValueError, match='scope'):
        G.commit(root, 'a', b)


def test_running_episode_or_incomplete_capture_cannot_commit(growth):
    root, _ = growth
    for changes in ({'episode_status': 'RUNNING'}, {'capture_complete': False}):
        b = {**bundle('a', units('a')), **changes}
        with pytest.raises(ValueError, match='terminal|capture'):
            G.commit(root, 'a', b)


def test_duplicate_unit_is_counted_once_and_never_forces_a_repair(growth):
    root, _ = growth
    u = units('a')
    first = G.commit(root, 'a', bundle('a', u + copy.deepcopy(u)))
    assert first['new_canonical_units'] == 1
    with pytest.raises(ValueError, match='next LEARN'):
        G.commit(root, 'a', bundle('a', u))


def test_no_action_episode_advances_experience_but_not_memory_units(growth):
    root, _ = growth
    first = G.commit(root, 'a', bundle('a', []))
    assert first['experienced_learn_designs'] == 1 and first['new_canonical_units'] == 0
    assert first['new_first_admitted_units'] == 0


@pytest.mark.parametrize('marker', [True, None])
def test_rolled_back_and_unknown_rollback_never_add_positive_units(growth, marker):
    root, _ = growth
    u = units('a', rolled_back=marker)
    if marker is None:
        u = units('a', rolled_back=None)
    receipt = G.commit(root, 'a', bundle('a', u))
    assert receipt['new_canonical_units'] == 1
    assert receipt['new_first_admitted_units'] == 0


def test_failed_rebuild_keeps_the_old_head_and_preserves_failure_cost(growth, monkeypatch):
    root, _ = growth
    before = (root / 'head.json').read_bytes()
    def fail(self):
        raise RuntimeError('injected rebuild failure')
    monkeypatch.setattr(TehmMemoryBackend, 'rebuild', fail)
    with pytest.raises(RuntimeError, match='injected'):
        G.commit(root, 'a', bundle('a', units('a')))
    assert (root / 'head.json').read_bytes() == before
    log = [json.loads(l) for l in (root / 'maintenance.jsonl').read_text().splitlines()]
    assert log[-1]['status'] == 'FAILED' and log[-1]['wall_s'] >= 0
    with pytest.raises(RuntimeError, match='partial|retry'):
        G.commit(root, 'a', bundle('a', units('a')))


def test_explicit_no_snapshot_overwrite(growth, tmp_path):
    root, _ = growth
    G.bind(root, 'p', 'PROBE', tmp_path / 'p')
    with pytest.raises(FileExistsError):
        G.bind(root, 'p', 'PROBE', tmp_path / 'p')


def test_discarded_task_branch_is_not_positive():
    rows = [{'ts': '2026-10-15T00:00:00+00:00', 'rolled_back': False},
            {'ts': '2026-10-15T00:02:00+00:00', 'rolled_back': False}]
    events = [{'type': 'task_entry_fallback', 'stage': 'start', 'ts': 1792022460}]
    result = G.mark_discarded_branch(rows, events)
    assert result[0]['rolled_back'] is True and result[1]['rolled_back'] is False
    assert rows[0]['rolled_back'] is False


def test_unverified_or_unexecuted_llm_candidate_never_creates_success(tmp_path):
    event = {'turn': 1, 'status': 'REJECTED_PROPOSAL', 'flow_launched': False,
             'rolled_back': False, 'situation': {'check': 'physical'}, 'ts': 1, 'proposal': '{}'}
    row = G.candidate_row(tmp_path, event)
    assert row['before'] is None and row['after'] is None and row['from_stage'] is None
    assert row['verdict'].startswith('stop_')
