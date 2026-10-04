import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_downstream_formal as formal


def test_balanced_fixed_design():
    rows = formal.jobs()
    assert len(rows) == len({r['name'] for r in rows}) == 24
    for model in ('gine', 'mlp'):
        assert len([r for r in rows if r['model'] == model]) == 12
    assert {r['seed'] for r in rows} == {42, 43, 44}


def test_command_separates_training_and_test(tmp_path):
    args = (Path('/python'), tmp_path/'runtime', tmp_path/'manifest', tmp_path,
            formal.jobs()[0], '144,145')
    train = formal.command(*args, 'train')
    assert '--finalize' not in train and '--pilot' not in train
    assert train[train.index('--epochs')+1] == '30'
    with pytest.raises(ValueError, match='checkpoint'):
        formal.command(*args, 'test')
    (tmp_path/'last.pt').touch()
    test = formal.command(*args, 'test')
    assert '--resume' in test and '--finalize' in test


def test_short_measurement_is_not_formal_completed(tmp_path):
    (tmp_path/'status.json').write_text(json.dumps(dict(epochs_complete=2)))
    (tmp_path/'result.json').write_text(json.dumps(dict(status='development_complete', test_evaluated=False)))
    assert not formal.completed(tmp_path, 'train')
    (tmp_path/'status.json').write_text(json.dumps(dict(epochs_complete=30)))
    assert formal.completed(tmp_path, 'train')
    assert not formal.completed(tmp_path, 'test')
    (tmp_path/'result.json').write_text(json.dumps(dict(status='complete', test_evaluated=True)))
    assert formal.completed(tmp_path, 'test')


def test_four_slots_finish_training_before_any_test(monkeypatch, tmp_path):
    manifest = tmp_path/'manifest.json'
    manifest.write_text(json.dumps(dict(formal_training_ready=True)))
    for name in ('gpu0_blackwell_validation.json', 'a100_new_environment_validation.json'):
        (tmp_path/name).write_text(json.dumps(dict(status='PASS', manifest_sha256=formal.sha(manifest),
                                                 versions={'torch': 'test'})))
    output = tmp_path/'formal'
    monkeypatch.setattr(sys, 'argv', ['run', '--manifest', str(manifest), '--output', str(output),
                                    '--python', '/python'])
    monkeypatch.setattr(formal.time, 'sleep', lambda _: None)
    monkeypatch.setattr(formal, 'gpu_info',
                        lambda i: dict(uuid=f'GPU-{i}', free_mib=5000, foreign_pids=[]))
    calls = []

    class Process:
        def __init__(self, command, **kwargs):
            scoring = '--finalize' in command
            if scoring:
                assert all(formal.completed(output/j['name'], 'train') for j in formal.jobs())
            calls.append((scoring, kwargs['env']['CUDA_VISIBLE_DEVICES']))
            self.pid = len(calls)
            run = Path(command[command.index('--output')+1])
            run.mkdir(exist_ok=True)
            (run/'last.pt').touch()
            (run/'result.json').write_text(json.dumps(dict(
                status='complete' if scoring else 'development_complete', test_evaluated=scoring)))
            (run/'status.json').write_text(json.dumps(dict(epochs_complete=30)))

        def poll(self):
            return 0

    monkeypatch.setattr(formal.subprocess, 'Popen', Process)
    formal.main()
    assert len(calls) == 48
    assert not any(scoring for scoring, _ in calls[:24])
    assert all(scoring for scoring, _ in calls[24:])
    assert {gpu for _, gpu in calls[:4]} == {'GPU-0', 'GPU-1', 'GPU-2', 'GPU-3'}
    state = json.loads((output/'status.json').read_text())
    assert state['phase'] == 'complete' and state['trained'] == state['test_scored'] == 24
