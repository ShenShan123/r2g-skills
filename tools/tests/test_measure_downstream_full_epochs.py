import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import measure_downstream_full_epochs as measure


def gpu(index, memory=0, name='A100'):
    return dict(uuid=f'GPU-{index}', name=name, memory_mib=memory, utilization=0)


def test_only_idle_supported_unassigned_gpus(monkeypatch):
    monkeypatch.setattr(measure, 'gpu_info', lambda i: {
        0: gpu(0, name='Blackwell'), 1: gpu(1, memory=20000), 2: gpu(2), 3: gpu(3)}[i])
    assert [i for i, _ in measure.available_gpus([0, 1, 2, 3], {3: {}})] == [2]


def test_shared_gpus_use_free_memory_not_utilization(monkeypatch):
    monkeypatch.setattr(measure, 'gpu_info', lambda i: dict(
        gpu(i, memory=35000, name='Blackwell' if i == 0 else 'A100'), utilization=100))
    monkeypatch.setattr(measure, 'free_memory_mib', lambda i: {1: 4000, 2: 1000, 3: 5000}[i])
    rows = measure.available_gpus([0, 1, 2, 3], {3: {}}, True, 2048)
    assert [i for i, _ in rows] == [1]
    assert rows[0][1]['free_memory_mib'] == 4000


def test_commands_preserve_full_data_budget_and_isolation(tmp_path):
    command = measure.training_command(tmp_path/'manifest.json', tmp_path/'result', 'route', 'congestion', '144,145')
    assert command[:3] == ['taskset', '-c', '144,145']
    assert command[command.index('--epochs')+1] == '2'
    assert '--pilot' not in command and '--finalize' not in command
    assert command[command.index('--device')+1] == 'cuda:0'


def test_busy_gpus_do_not_create_output(monkeypatch, tmp_path):
    m = tmp_path/'manifest.json'
    m.write_text(json.dumps(dict(formal_training_ready=True)))
    monkeypatch.setattr(sys, 'argv', ['run', '--manifest', str(m), '--output', str(tmp_path/'out'), '--gpu', '1', '2'])
    monkeypatch.setattr(measure, 'gpu_info', lambda i: gpu(i, memory=20000))
    with pytest.raises(RuntimeError, match='occupied'):
        measure.main()
    assert not (tmp_path/'out').exists()


def test_two_jobs_start_on_distinct_gpus(monkeypatch, tmp_path):
    m = tmp_path/'manifest.json'
    m.write_text(json.dumps(dict(formal_training_ready=True)))
    monkeypatch.setattr(sys, 'argv', ['run', '--manifest', str(m), '--output', str(tmp_path/'out'), '--gpu', '2', '1'])
    monkeypatch.setattr(measure, 'gpu_info', lambda i: gpu(i))
    monkeypatch.setattr(measure.time, 'sleep', lambda _: None)
    calls, statuses = [], []
    real_save = measure.save
    def save(path, data):
        if path.name == 'status.json':
            statuses.append(data)
        real_save(path, data)
    monkeypatch.setattr(measure, 'save', save)
    class Process:
        def __init__(self, command, **kwargs):
            calls.append((command, kwargs['env']))
            self.pid = 100+len(calls)
            root = Path(command[command.index('--output')+1])
            root.mkdir()
            (root/'result.json').write_text(json.dumps(dict(test_evaluated=False, timing={})))
            (root/'status.json').write_text(json.dumps(dict(epochs_complete=2)))
            (root/'history.json').write_text(json.dumps([dict(epoch=1, train_seconds=1, epoch_seconds=2)]))
        def poll(self):
            return 0
    monkeypatch.setattr(measure.subprocess, 'Popen', Process)
    measure.main()
    assert len(calls) == 2
    assert {env['CUDA_VISIBLE_DEVICES'] for _, env in calls} == {'GPU-1', 'GPU-2'}
    assert len({command[2] for command, _ in calls}) == 2
    assert max(len(s['active']) for s in statuses) == 2
    assert statuses[-1]['phase'] == 'complete'
    assert statuses[-1]['completed'] == 2

    # Emulate a paused four-task queue with two existing completed jobs.
    output = tmp_path/'out'
    prior = json.loads((output/'status.json').read_text())
    prior.update(phase='paused_no_idle_gpu', planned=4,
                 pending=['route_wirelength_gine', 'route_congestion_gine'])
    (output/'status.json').write_text(json.dumps(prior))
    original = (output/'cts_wirelength_gine/result.json').read_bytes()
    monkeypatch.setattr(sys, 'argv', ['run', '--manifest', str(m), '--output', str(output),
        '--gpu', '2', '1', '--stages', 'cts', 'route', '--resume', '--allow-shared-gpu'])
    monkeypatch.setattr(measure, 'free_memory_mib', lambda i: 4000)
    measure.main()
    assert len(calls) == 4
    assert statuses[-1]['completed'] == 4
    assert statuses[-1]['allow_shared_gpu'] is True
    assert (output/'cts_wirelength_gine/result.json').read_bytes() == original
    assert all('route' == c[c.index('--stage')+1] for c, _ in calls[2:])
    assert len(list(output.glob('status.before_resume.*.json'))) == 1
