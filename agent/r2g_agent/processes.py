"""Linux process identity and confirmed group termination for EDA stages."""
from __future__ import annotations

import ctypes
import errno
import os
from pathlib import Path
import signal
import time
import logging


class ProcessInspectionError(RuntimeError):
    pass


def _departed(path: Path, start: str | None = None) -> bool:
    """Recheck an unreadable /proc entry before declaring inspection unknown.

    A process can exit or become a zombie between stat and environ, and Linux
    may report EACCES rather than ENOENT during that transition. A live process
    with the same identity must still fail closed.
    """
    try:
        fields = (path / 'stat').read_text().rsplit(')', 1)[1].split()
        return fields[0] in ('Z', 'X') or (start is not None and fields[19] != start)
    except (FileNotFoundError, ProcessLookupError):
        return True
    except (OSError, ValueError, IndexError):
        return False


def _privileged_login_branch(path: Path, not_before: str) -> bool:
    """Prove that an unreadable sshd belongs to a separate root-owned login.

    OpenSSH drops to the login uid but keeps its environment non-dumpable.
    Its root-owned monitor and pre-existing root sshd daemon cannot descend
    from this unprivileged EDA stage. A name alone, an orphan, or an ordinary
    unreadable same-uid child provides no such proof and remains unresolved.
    """
    if os.getuid() == 0:
        return False
    try:
        if (path / 'comm').read_text().strip() != 'sshd':
            return False
        fields = (path / 'stat').read_text().rsplit(')', 1)[1].split()
        parent = int(fields[1])
        for _ in range(8):
            if parent <= 1:
                return False
            entry = Path('/proc') / str(parent)
            if (entry / 'comm').read_text().strip() != 'sshd':
                return False
            status = (entry / 'status').read_text().splitlines()
            uids = next(line.split()[1:] for line in status if line.startswith('Uid:'))
            if len(uids) != 4 or any(int(uid) != 0 for uid in uids):
                return False
            fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            if int(fields[19]) < int(not_before):
                return True
            parent = int(fields[1])
    except (OSError, ValueError, IndexError, StopIteration):
        return False
    return False


def _pidfd_syscall(number: int, *args) -> int:
    # Some Python builds omit pidfd wrappers even on a supporting Linux kernel.
    # These syscall numbers are shared by the supported 64-bit Linux ABIs.
    if os.uname().machine not in ('x86_64', 'aarch64', 'riscv64'):
        raise OSError(errno.ENOSYS, 'pidfd requires a supported Linux ABI')
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    result = libc.syscall(ctypes.c_long(number), *args)
    if result == -1:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    return result


def boot_id() -> str:
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def proc_start(pid: int) -> str | None:
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
    except FileNotFoundError:
        return None
    except (OSError, IndexError) as e:
        raise ProcessInspectionError('cannot establish process identity') from e


def group_members(pgid: int) -> list[int]:
    """Only live processes can write or hold licenses; zombies await parent reaping."""
    members = []
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        identity = None
        try:
            fields = (path / 'stat').read_text().rsplit(')', 1)[1].split()
            identity = fields[19]
            if int(fields[2]) == pgid and fields[0] not in ('Z', 'X'):
                members.append(int(path.name))
        except (FileNotFoundError, ProcessLookupError):
            continue
        except (OSError, ValueError, IndexError) as e:
            if _departed(path, identity):
                continue
            raise ProcessInspectionError('cannot enumerate process group: pid=' + path.name) from e
    return members


def execution_members(execution_id: str, not_before: str) -> list[int]:
    """Find inherited stage markers, including children that called setsid().

    This is for the trusted local EDA runner, not a sandbox for adversarial code.
    An unreadable live process of our uid makes absence unproven.
    """
    marker = ('EDACODE_EXECUTION_ID=' + execution_id).encode()
    members = []
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        identity = None
        try:
            if path.stat().st_uid != os.getuid():
                continue
            fields = (path / 'stat').read_text().rsplit(')', 1)[1].split()
            identity = fields[19]
            # A stage marker cannot have been inherited before its leader was
            # born. This also excludes pre-existing privileged login helpers.
            if fields[0] in ('Z', 'X') or int(fields[19]) < int(not_before):
                continue
            if marker in (path / 'environ').read_bytes().split(b'\0'):
                members.append(int(path.name))
        except (FileNotFoundError, ProcessLookupError):
            continue
        except (OSError, ValueError, IndexError) as e:
            if _departed(path, identity):
                continue
            if isinstance(e, PermissionError) and _privileged_login_branch(path, not_before):
                continue
            raise ProcessInspectionError('cannot enumerate execution children: pid=' + path.name) from e
    return members


def _signal_process(pid: int, sig: int, pgid: int, execution_id: str | None) -> None:
    # Pin the process identity while inspecting and signalling it: a numeric PID
    # alone can be recycled between the /proc read and kill().
    start = proc_start(pid)
    if start is None:
        return
    try:
        fd = os.pidfd_open(pid) if hasattr(os, 'pidfd_open') else _pidfd_syscall(434, pid, 0)
    except ProcessLookupError:
        return
    try:
        if proc_start(pid) == start:
            try:
                fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
                in_group = int(fields[2]) == pgid
                marked = execution_id and ('EDACODE_EXECUTION_ID=' + execution_id).encode() in \
                    Path(f'/proc/{pid}/environ').read_bytes().split(b'\0')
                if not in_group and not marked:
                    return
                if hasattr(signal, 'pidfd_send_signal'):
                    signal.pidfd_send_signal(fd, sig)
                else:
                    _pidfd_syscall(424, fd, int(sig), ctypes.c_void_p(), 0)
            except (FileNotFoundError, ProcessLookupError):
                pass
    finally:
        os.close(fd)


def stop_orphan(pgid, project: str, grace: float = 10.0, *,
                expected_start: str | None = None, execution_id: str | None = None) -> bool:
    # /proc is not a snapshot: concurrent short-lived EDA processes can vanish
    # or become temporarily unreadable between stat and environ. Retry the
    # complete inspection, never infer successful termination from an error.
    deadline = time.monotonic() + max(grace, 0.1)
    while True:
        try:
            return _stop_orphan_once(pgid, project, grace,
                                     expected_start=expected_start, execution_id=execution_id)
        except (OSError, ProcessInspectionError) as exc:
            if time.monotonic() >= deadline:
                logging.getLogger(__name__).warning('Unconfirmed stage cleanup pgid=%s: %s (%s)',
                                                   pgid, exc, exc.__cause__ or type(exc).__name__)
                return False
            time.sleep(0.1)


def _stop_orphan_once(pgid, project: str, grace: float = 10.0, *,
                      expected_start: str | None = None, execution_id: str | None = None) -> bool:
    """False means unresolved. Never treat signal delivery as confirmed exit."""
    if not isinstance(pgid, int) or pgid <= 1:
        return False
    if execution_id and not expected_start:
        return False
    try:
        members = group_members(pgid)
        detached = execution_members(execution_id, expected_start) if execution_id else []
        if not members and not detached:
            return True
        # Refuse recycled leaders even if their argv happens to mention the project.
        leader_start = proc_start(pgid)
        if expected_start and leader_start and expected_start != leader_start:
            return False
        ours = not members or bool(expected_start and leader_start == expected_start)
        for pid in members:
            try:
                if execution_id:
                    marker = ('EDACODE_EXECUTION_ID=' + execution_id).encode()
                    ours |= marker in Path(f'/proc/{pid}/environ').read_bytes().split(b'\0')
                elif project:
                    ours |= project.encode() in Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
            except (FileNotFoundError, ProcessLookupError):
                continue
            except OSError:
                if not ours:
                    return False
        if not ours:
            return False
        for sig in (signal.SIGTERM, signal.SIGKILL):
            deadline = time.monotonic() + max(grace, 0.1)
            while True:
                # Rescan after each signal: timeout and setsid may have moved a
                # child out of the initial group, even after its leader exited.
                members = group_members(pgid)
                leader_start = proc_start(pgid)
                if expected_start and leader_start and expected_start != leader_start:
                    return False
                detached = execution_members(execution_id, expected_start) if execution_id else []
                live = set(members) | set(detached)
                if not live:
                    return True
                for pid in live:
                    _signal_process(pid, sig, pgid, execution_id)
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
        return not group_members(pgid) and not (execution_id and execution_members(execution_id, expected_start))
    except (OSError, ProcessInspectionError):
        raise
