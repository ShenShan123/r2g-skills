"""T2 source lock (decision D-a, 2026-10-17): a candidate entering the T2 qualification
shortlist is isolated from the learning-curve candidate pool at once (TEMP_LOCK) and fixed
as TEST at registration (REGISTERED_TEST). The lock is append-only; a conflict with any
roster the learning curve uses is reported, never silently resolved.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'memory' / 'scripts'))
import t2_source_lock as SL  # noqa: E402


def entry(h, g, design='d'):
    return {'handoff_id': h, 'group_id': g, 'design': design, 'owner': 'o', 'repository': 'r'}


def test_lock_is_append_only_and_records_reason_and_status(tmp_path):
    p = tmp_path / 'lock.json'
    SL.lock(p, [entry('C1', 'g1'), entry('C2', 'g2')], reason='qualification shortlist', by='user decision D-a')
    d = SL.load(p)
    assert {e['status'] for e in d['entries']} == {'TEMP_LOCK'} and all(e['locked_at'] for e in d['entries'])
    SL.lock(p, [entry('C3', 'g3')], reason='second batch', by='user decision D-a')
    assert [e['handoff_id'] for e in SL.load(p)['entries']] == ['C1', 'C2', 'C3']
    with pytest.raises(ValueError):
        SL.lock(p, [entry('C1', 'g1')], reason='again', by='x')        # never re-added


def test_registration_fixes_a_locked_entry_as_test_and_never_unlocks(tmp_path):
    p = tmp_path / 'lock.json'
    SL.lock(p, [entry('C1', 'g1')], reason='r', by='b')
    SL.register(p, ['C1'], registration='t2a-table2-t2-xxxx')
    e = SL.load(p)['entries'][0]
    assert e['status'] == 'REGISTERED_TEST' and e['registration'] == 't2a-table2-t2-xxxx'
    with pytest.raises(ValueError):
        SL.register(p, ['C9'], registration='x')                      # must be locked first
    with pytest.raises(AttributeError):
        SL.unlock                                                     # no such operation


def test_conflicts_against_a_roster_by_group_or_handoff_id(tmp_path):
    p = tmp_path / 'lock.json'
    SL.lock(p, [entry('C1', 'g1'), entry('C2', 'g2')], reason='r', by='b')
    roster = tmp_path / 'roster.json'
    roster.write_text(json.dumps({'learn': [{'handoff_id': 'C7', 'group_id': 'g7'}, {'id': 'C2'}],
                                  'probe': [{'group_id': 'g1'}]}))
    c = SL.conflicts(SL.load(p), roster)
    assert sorted(x['handoff_id'] for x in c) == ['C1', 'C2']
    assert {x['matched_by'] for x in c} == {'group_id', 'handoff_id'}


def test_a_missing_roster_is_an_error_not_a_clean_result(tmp_path):
    p = tmp_path / 'lock.json'
    SL.lock(p, [entry('C1', 'g1')], reason='r', by='b')
    with pytest.raises(FileNotFoundError):
        SL.conflicts(SL.load(p), tmp_path / 'missing.json')


def test_locked_groups_exposes_every_status(tmp_path):
    p = tmp_path / 'lock.json'
    SL.lock(p, [entry('C1', 'g1'), entry('C2', 'g2')], reason='r', by='b')
    SL.register(p, ['C2'], registration='x')
    assert SL.locked_groups(p) == {'g1', 'g2'}
