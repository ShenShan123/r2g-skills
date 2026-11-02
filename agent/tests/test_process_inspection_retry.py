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


@pytest.mark.parametrize('privileged', [True, False])
def test_protected_login_needs_root_owned_daemon_ancestry(monkeypatch, privileged):
    from types import SimpleNamespace
    entries = {700: (701, 300), 701: (702, 250), 702: (1, 100)}
    def read(path, *args, **kwargs):
        pid = int(path.parent.name)
        parent, start = entries[pid]
        if path.name == 'comm': return 'sshd\n'
        if path.name == 'status': return 'Uid:\t' + '\t'.join(['0' if privileged else '1017'] * 4) + '\n'
        if path.name == 'stat':
            fields = ['S', str(parent), '700'] + ['0'] * 16 + [str(start)]
            return str(pid) + ' (sshd) ' + ' '.join(fields)
        raise AssertionError(str(path))
    monkeypatch.setattr(processes.os, 'getuid', lambda: 1017)
    monkeypatch.setattr(Path, 'iterdir', lambda p: iter([Path('/proc/700')]))
    monkeypatch.setattr(Path, 'stat', lambda *a: SimpleNamespace(st_uid=1017))
    monkeypatch.setattr(Path, 'read_text', read)
    monkeypatch.setattr(Path, 'read_bytes', lambda *a: (_ for _ in ()).throw(PermissionError('protected login')))
    if privileged:
        assert processes.execution_members('stage', '200') == []
    else:
        with pytest.raises(processes.ProcessInspectionError):
            processes.execution_members('stage', '200')


@pytest.mark.parametrize('manager_parent,manager_args,expect_skip', [
    (1, b'/lib/systemd/systemd\0--user\0', True),       # the real user manager's helper
    (555, b'/lib/systemd/systemd\0--user\0', False),    # manager not a child of pid 1
    (1, b'/lib/systemd/systemd\0--system\0', False),    # not a user manager
])
def test_user_manager_pam_helper_is_not_a_stage_descendant(monkeypatch, manager_parent, manager_args, expect_skip):
    """2026-11-01 (203): a restarted 'systemd --user' left an unreadable '(sd-pam)' that kept
    every later stage cleanup unconfirmed. Only the manager's own helper is exempt."""
    from types import SimpleNamespace
    procs = {800: ('(sd-pam)', 801, 300), 801: ('systemd', manager_parent, 299)}
    def read(path, *args, **kwargs):
        pid = int(path.parent.name)
        comm, parent, start = procs[pid]
        if path.name == 'comm': return comm + '\n'
        if path.name == 'status': return 'Uid:\t' + '\t'.join(['1017'] * 4) + '\n'
        if path.name == 'stat':
            fields = ['S', str(parent), str(pid)] + ['0'] * 16 + [str(start)]
            return '%d (%s) %s' % (pid, comm, ' '.join(fields))
        raise AssertionError(str(path))
    def read_bytes(path, *args):
        if path.name == 'cmdline' and int(path.parent.name) == 801:
            return manager_args
        raise PermissionError('non-dumpable')
    monkeypatch.setattr(processes.os, 'getuid', lambda: 1017)
    monkeypatch.setattr(Path, 'iterdir', lambda p: iter([Path('/proc/800')]))
    monkeypatch.setattr(Path, 'stat', lambda *a: SimpleNamespace(st_uid=1017))
    monkeypatch.setattr(Path, 'read_text', read)
    monkeypatch.setattr(Path, 'read_bytes', read_bytes)
    if expect_skip:
        assert processes.execution_members('stage', '200') == []
    else:
        with pytest.raises(processes.ProcessInspectionError):
            processes.execution_members('stage', '200')
