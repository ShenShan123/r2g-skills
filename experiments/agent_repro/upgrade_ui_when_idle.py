#!/usr/bin/env python3
"""Replace one identified UI service only after all recorded jobs have drained.

This is a deployment helper, not a job runner. It never cancels experiments.
--apply is required to arm the eventual service restart. A changed service PID
or changed Python source stops deployment so a stale helper cannot replace a
newer service or deploy code that was not validated when it was armed.
"""
import argparse
from contextlib import closing
import datetime
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import time
import urllib.request


def start_time(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
    except FileNotFoundError:
        return None


def active_jobs(home):
    with closing(sqlite3.connect(f'file:{home}/jobs.sqlite?mode=ro', uri=True, timeout=30)) as conn:
        rows = conn.execute("SELECT j.id,j.status,COUNT(d.name) FROM jobs j LEFT JOIN designs d "
                            "ON d.job_id=j.id AND d.status IN ('pending','running','recovery_required') "
                            "GROUP BY j.id HAVING COUNT(d.name)>0 OR j.status IN "
                            "('pending','running','finalizing','cancelling','recovery_required')").fetchall()
    return [{'id': i, 'status': s, 'unfinished': n} for i, s, n in rows]


def source_hashes(repo):
    root = repo/'agent/r2g_agent'
    return {str(p.relative_to(repo)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*.py'))}


def observe_jobs(home, status):
    """An unreadable busy database is unknown, never evidence of an idle runner."""
    try:
        return active_jobs(home)
    except sqlite3.OperationalError as exc:
        if not any(word in str(exc).lower() for word in ('locked', 'busy')):
            raise
        status('waiting_for_database', reason=str(exc))
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--home', type=Path, required=True)
    ap.add_argument('--repo', type=Path, required=True)
    ap.add_argument('--pid', type=int, required=True)
    ap.add_argument('--r2g', type=Path, required=True)
    ap.add_argument('--state-dir', type=Path, required=True)
    ap.add_argument('--port', type=int, default=8770)
    ap.add_argument('--poll', type=float, default=30)
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    a.state_dir.mkdir(parents=True, exist_ok=True)
    original_start = start_time(a.pid)
    assert original_start, 'Original UI is not alive'
    argv = Path(f'/proc/{a.pid}/cmdline').read_bytes().split(b'\0')
    assert str(a.r2g).encode() in argv and b'ui' in argv and str(a.port).encode() in argv
    frozen = source_hashes(a.repo)
    record = {'old_pid': a.pid, 'old_start': original_start, 'port': a.port,
              'armed': a.apply, 'source_sha256': frozen}
    (a.state_dir/'deployment.json').write_text(json.dumps(record, indent=2)+'\n')

    def status(state, **fields):
        data = {'state': state, 'at': datetime.datetime.now().astimezone().isoformat(),
                'old_pid': a.pid, **fields}
        (a.state_dir/'status.json').write_text(json.dumps(data, indent=2)+'\n')
        print(json.dumps(data), flush=True)

    idle_since = None
    while True:
        if start_time(a.pid) != original_start:
            status('aborted', reason='Original service changed; no process signalled')
            return 1
        active = observe_jobs(a.home, status)
        if active is None:
            idle_since = None
            if not a.apply:
                return 1
            time.sleep(a.poll)
            continue
        if not a.apply:
            status('dry_run', active_jobs=active)
            return 0
        if active:
            idle_since = None
            status('waiting_for_jobs', active_jobs=active)
        elif idle_since is None:
            idle_since = time.monotonic()
            status('confirming_idle')
        elif time.monotonic() - idle_since >= 30:
            if source_hashes(a.repo) != frozen:
                status('aborted', reason='Python source changed after validation; revalidate and re-arm')
                return 1
            # Re-read immediately before signalling, preserving new submissions
            # that arrived while the service was being observed idle.
            latest = observe_jobs(a.home, status)
            if latest is None or latest:
                idle_since = None
                time.sleep(a.poll)
                continue
            break
        time.sleep(a.poll)

    # Inherit the service's environment in memory only; do not log or persist it.
    env = dict(v.decode().split('=', 1) for v in Path(f'/proc/{a.pid}/environ').read_bytes().split(b'\0') if b'=' in v)
    cwd = os.readlink(f'/proc/{a.pid}/cwd')
    if start_time(a.pid) != original_start:
        status('aborted', reason='Service identity changed before restart')
        return 1
    status('restarting_idle_service')
    os.kill(a.pid, signal.SIGTERM)
    for _ in range(60):
        if start_time(a.pid) != original_start:
            break
        time.sleep(1)
    else:
        status('aborted', reason='Old service did not exit; no forced kill or second server')
        return 1
    with (a.state_dir/'ui.log').open('ab') as log:
        p = subprocess.Popen([str(a.r2g), 'ui', '--port', str(a.port)], cwd=cwd, env=env,
                             stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                             start_new_session=True)
    for _ in range(60):
        if p.poll() is not None:
            status('failed', reason='Replacement service exited', new_pid=p.pid)
            return 1
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{a.port}/api/jobs', timeout=3) as response:
                jobs = json.load(response)
            if not jobs or all('dual_clean' in j for j in jobs):
                status('deployed', new_pid=p.pid)
                return 0
        except (OSError, ValueError):
            pass
        time.sleep(1)
    status('failed', reason='Replacement service health check timed out', new_pid=p.pid)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
