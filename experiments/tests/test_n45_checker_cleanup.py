"""Real process tests use their own session; never signal the pytest group."""
import os
from pathlib import Path
import shlex
import signal
import subprocess
import time

import pytest


ROOT = Path('/home/yangao/r2g_nangate45_baseline_20260917')
HELPER = ROOT/'runtime/r2g-skills/signoff-loop/scripts/flow/_bounded_run.sh'


def alive(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0] != 'Z'
    except FileNotFoundError:
        return False


@pytest.mark.parametrize('mode', ['normal', 'timeout', 'cancel', 'startup_cancel'])
def test_setsid_startup_and_cleanup(tmp_path, mode):
    proxy = tmp_path/'setsid'
    proxy.write_text('#!/bin/bash\nsleep 0.4\nexec /usr/bin/setsid "$@"\n')
    proxy.chmod(0o755)
    pidfile = tmp_path/'checker.pid'
    command = f'echo $$ > {shlex.quote(str(pidfile))}; '
    command += 'exit 7' if mode == 'normal' else 'exec sleep 60'
    script = (
        f'source {shlex.quote(str(HELPER))}\n'
        "trap 'r2g_bounded_cleanup' EXIT\n"
        "trap 'r2g_bounded_cleanup; exit 143' TERM\n"
        f'r2g_bounded_run {1 if mode == "timeout" else 60} 0 '
        f'{shlex.quote(str(tmp_path/"log"))} bash -c {shlex.quote(command)}\n'
    )
    proc = subprocess.Popen(['bash', '-c', script], start_new_session=True,
        env={**os.environ, 'PATH': str(tmp_path)+':'+os.environ['PATH']},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    checker = None
    try:
        if mode == 'startup_cancel':
            time.sleep(0.15)
            proc.terminate()
        elif mode == 'cancel':
            deadline = time.monotonic()+5
            while not pidfile.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert pidfile.exists()
            checker = int(pidfile.read_text())
            proc.terminate()
        _, err = proc.communicate(timeout=15)
        assert proc.returncode == {'normal': 7, 'timeout': 124, 'cancel': 143,
                                   'startup_cancel': 143}[mode], err
        time.sleep(0.5)
        if pidfile.exists():
            checker = int(pidfile.read_text())
            assert not alive(checker)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate(timeout=3)
        if checker and alive(checker):
            os.kill(checker, signal.SIGKILL)


def test_outer_timeout_allows_descendant_cleanup(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location('cleanup_runner', ROOT/'runner.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    child_script = tmp_path/'wrapper.sh'
    child_script.write_text(
        f'source {shlex.quote(str(HELPER))}\n'
        "trap 'r2g_bounded_cleanup; exit 143' TERM\n"
        f'r2g_bounded_run 60 0 {tmp_path}/checker.log bash -c '
        f"'echo $$ > {tmp_path}/checker.pid; exec sleep 60'\n")
    leader = ('import subprocess, time; '
              f'subprocess.Popen(["bash", {str(child_script)!r}]); time.sleep(60)')
    rc, timed_out = runner.call(['python3', '-c', leader], tmp_path/'outer.log', os.environ.copy(), 2)
    assert (rc, timed_out) == (124, True)
    pid = int((tmp_path/'checker.pid').read_text())
    try:
        assert not alive(pid)
    finally:
        if alive(pid):
            os.kill(pid, signal.SIGKILL)
