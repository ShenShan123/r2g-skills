from r2g_agent import processes
from pathlib import Path
import os
import pytest


def test_transient_proc_inspection_retries_before_confirming_exit(monkeypatch):
    calls = []

    def groups(_):
        calls.append(1)
        if len(calls) == 1:
            raise processes.ProcessInspectionError('process disappeared during scan')
        return []

    monkeypatch.setattr(processes, 'group_members', groups)
    monkeypatch.setattr(processes.time, 'sleep', lambda _: None)
    assert processes.stop_orphan(123, '/design', grace=0.1)
    assert len(calls) == 2


def test_persistently_unreadable_proc_never_confirms_exit(monkeypatch):
    def groups(_):
        raise processes.ProcessInspectionError('permission denied')

    ticks = iter([0.0, 0.0, 0.2])
    monkeypatch.setattr(processes, 'group_members', groups)
    monkeypatch.setattr(processes.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(processes.time, 'sleep', lambda _: None)
    assert not processes.stop_orphan(123, '/design', grace=0.1)


@pytest.mark.parametrize('exited', [True, False])
def test_unreadable_environment_rechecks_same_process_identity(monkeypatch, exited):
    entry = Path('/proc') / str(os.getpid())
    original_read = Path.read_text
    stat = (entry / 'stat').read_text()
    count = 0
    def read(path, *args, **kwargs):
        nonlocal count
        if path == entry / 'stat':
            count += 1
            if exited and count > 1:
                prefix, rest = stat.rsplit(')', 1)
                fields = rest.split()
                fields[0] = 'Z'
                return prefix + ') ' + ' '.join(fields)
            return stat
        return original_read(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'iterdir', lambda p: iter([entry]))
    monkeypatch.setattr(Path, 'read_text', read)
    monkeypatch.setattr(Path, 'read_bytes', lambda *a: (_ for _ in ()).throw(PermissionError('transition')))
    if exited:
        assert processes.execution_members('marker', '0') == []
    else:
        with pytest.raises(processes.ProcessInspectionError):
            processes.execution_members('marker', '0')
