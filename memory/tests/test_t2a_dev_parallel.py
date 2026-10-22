import json
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import t2a_dev_parallel as P


def registration(tmp_path):
    reg = {'split': 'DEV', 'paper_data': False,
           'plan': [{'task': 'riscv_core/u19', 'arm': a, 'repeat': 1}
                    for a in ['EXPEL', 'AMEM', 'TM0', 'NB', 'RECIPE', 'AWM', 'RET']]}
    for row in reg['plan'][:4]:
        p = tmp_path / 'episodes' / P.name(row); p.mkdir(parents=True)
        (p / 'episode.json').write_text(json.dumps({'status': 'RUNNING' if row['arm'] == 'NB' else 'NOT_ACCEPTED',
            'final': {'acceptance': {'checks': {'A3_lvs': 'pass'}, 'unknown': []}}}))
    return reg


def test_only_pending_three_episodes_launch_without_overlap(tmp_path, monkeypatch):
    reg = registration(tmp_path)
    monkeypatch.setattr(P.os, 'sched_getaffinity', lambda _: set(range(192)))
    active = set(range(27, 35)) | set(range(123, 131))
    plan = P.handoff_plan(tmp_path, reg, active)
    assert [r['arm'] for r in plan] == list(P.PENDING)
    used = set(active)
    for row in plan:
        assert not used & set(row['cpu_affinity'])
        used.update(row['cpu_affinity'])


def test_existing_recipe_never_retries(tmp_path):
    reg = registration(tmp_path)
    p = tmp_path / 'episodes' / P.name(reg['plan'][4]); p.mkdir()
    (p / 'episode.json').write_text('{"status":"RUNNING"}')
    with pytest.raises(RuntimeError, match='Pending episodes changed'):
        P.handoff_plan(tmp_path, reg, set())


def test_formal_test_registration_cannot_use_dev_handoff(tmp_path):
    reg = registration(tmp_path); reg['split'] = 'TEST'; reg['paper_data'] = True
    with pytest.raises(RuntimeError, match='non-paper DEV'):
        P.handoff_plan(tmp_path, reg, set())


def test_overlapping_or_unavailable_cpu_is_rejected(tmp_path, monkeypatch):
    reg = registration(tmp_path)
    monkeypatch.setattr(P.os, 'sched_getaffinity', lambda _: set(range(192)))
    with pytest.raises(RuntimeError, match='overlapping'):
        P.handoff_plan(tmp_path, reg, {5})
    monkeypatch.setattr(P.os, 'sched_getaffinity', lambda _: {27})
    with pytest.raises(RuntimeError, match='Unavailable'):
        P.handoff_plan(tmp_path, reg, set())


def test_unverified_terminal_restoration_cannot_pass_dev(tmp_path):
    reg = registration(tmp_path)
    for row in reg['plan'][3:]:
        p = tmp_path / 'episodes' / P.name(row); p.mkdir(exist_ok=True)
        (p / 'episode.json').write_text(json.dumps({'status': 'NOT_ACCEPTED',
            'final': {'acceptance': {'checks': {'A3_lvs': 'pass'}, 'unknown': []}}}))
    p = tmp_path / 'episodes' / P.name(reg['plan'][2]) / 'episode.json'
    p.write_text('{"status":"NOT_ACCEPTED","final":{"execution_recovery":"UNVERIFIED","recovery_rc":2}}')
    assert P.terminal_gaps(tmp_path, reg) == ['TM0']
