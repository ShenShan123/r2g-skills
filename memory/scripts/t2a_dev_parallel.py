#!/usr/bin/env python3
"""Explicit, no-retry scheduling handoff for the already running seven-arm DEV.

The frozen episode runtime is never edited. Stop only the old batch supervisor,
adopt its independent episode process, and launch previously unstarted episodes.
This is a DEV-only scheduling amendment, not a TEST or extra-attempt launcher.
"""
import argparse
import fcntl
import hashlib
import importlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

PENDING = ('RECIPE', 'AWM', 'RET')
SLOTS = (list(range(5, 13)) + list(range(101, 109)),
         list(range(13, 21)) + list(range(109, 117)),
         list(range(39, 47)) + list(range(135, 143)))
TERMINAL = {'ACCEPTED', 'NOT_ACCEPTED', 'ACCOUNTING_FAILURE', 'UNVERIFIED_OR_SAFETY_STOP'}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')


def identity(pid):
    try:
        p = Path('/proc') / str(pid)
        fields = p.joinpath('stat').read_text().rsplit(')', 1)[1].split()
        return {'pid': pid, 'start_ticks': fields[19], 'state': fields[0],
                'argv': p.joinpath('cmdline').read_bytes().split(b'\0')[:-1]}
    except (FileNotFoundError, ProcessLookupError):
        return None


def alive(original):
    current = identity(original['pid'])
    return bool(current and current['start_ticks'] == original['start_ticks']
                and current['state'] not in ('Z', 'X'))


def name(row):
    return row['task'].replace('/', '__') + '__' + row['arm'] + '__r' + str(row['repeat'])


def handoff_plan(root, reg, active_cpus):
    if reg.get('split') != 'DEV' or reg.get('paper_data') is not False or len(reg['plan']) != 7:
        raise RuntimeError('Only the seven-episode, non-paper DEV registration is in scope')
    pending = []
    for row in reg['plan']:
        project = root / 'episodes' / name(row)
        if not project.exists():
            pending.append(row)
        else:
            path = project / 'episode.json'
            if not path.exists() or json.loads(path.read_text()).get('status') not in TERMINAL | {'RUNNING'}:
                raise RuntimeError('Incomplete or unknown existing worker: ' + name(row))
    if {r['arm'] for r in pending} != set(PENDING) or len(pending) != 3:
        raise RuntimeError('Pending episodes changed; no duplicate launch or implicit retry')
    used = set(active_cpus)
    available = set(os.sched_getaffinity(0))
    launches = []
    for arm, slot in zip(PENDING, SLOTS):
        if used & set(slot) or not set(slot) <= available:
            raise RuntimeError('Unavailable or overlapping CPU allocation')
        used.update(slot)
        row = next(r for r in pending if r['arm'] == arm)
        launches.append({**row, 'cpu_affinity': slot})
    return launches


def terminal_gaps(root, reg):
    gaps = []
    for row in reg['plan']:
        path = root / 'episodes' / name(row) / 'episode.json'
        if not path.exists():
            gaps.append(row['arm']); continue
        final = json.loads(path.read_text()).get('final') or {}
        recovery = final.get('execution_recovery')
        acceptance = final.get('acceptance') or {}
        if (recovery not in (None, 'VERIFIED') or not acceptance.get('checks')
                or acceptance.get('unknown')):
            gaps.append(row['arm'])
    return gaps


def run(root, amendment, old_pid):
    amendment.mkdir(parents=True, exist_ok=True)
    lease = (amendment / 'supervisor.lock').open('a')
    fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if (amendment / 'started.json').exists():
        raise RuntimeError('No supervisor restart or implicit episode retry')
    sys.path.insert(0, str(root / 'code/memory/scripts'))
    B = importlib.import_module('t2a_batch')
    D = importlib.import_module('t2a_dev')
    reg = B.preflight(root, split='DEV')
    old = identity(old_pid)
    expected = str(root / 'code/memory/scripts/t2a_dev.py').encode()
    if (not old or expected not in old['argv'] or b'batch' not in old['argv']
            or str(root).encode() not in old['argv']):
        raise RuntimeError('Exact original DEV supervisor not found; no signals sent')
    stopped = False
    transferred = False
    handles = []
    children = []
    try:
        os.kill(old_pid, signal.SIGSTOP); stopped = True
        for _ in range(20):
            if (identity(old_pid) or {}).get('state') in ('T', 't'): break
            time.sleep(.05)
        else: raise RuntimeError('Original supervisor did not stop')
        # The original worker uses start_new_session=True. Never signal a group.
        adopted = []
        active_cpus = set()
        child_ids = Path('/proc', str(old_pid), 'task', str(old_pid), 'children').read_text().split()
        for text_pid in child_ids:
            pid = int(text_pid); entry = identity(pid)
            if not entry or entry['state'] in ('Z', 'X'): continue
            if (str(root / 'code/memory/scripts/t2a_batch.py').encode() not in entry['argv']
                    or b'episode' not in entry['argv'] or os.getsid(pid) != pid):
                raise RuntimeError('Unexpected supervisor child; no handoff')
            cpus = sorted(os.sched_getaffinity(pid)); active_cpus.update(cpus)
            adopted.append({**entry, 'argv': [a.decode() for a in entry['argv']], 'cpu_affinity': cpus})
        launches = handoff_plan(root, reg, active_cpus)
        running = [r for r in reg['plan'] if (root / 'episodes' / name(r) / 'episode.json').exists()
                   and json.loads((root / 'episodes' / name(r) / 'episode.json').read_text()).get('status') == 'RUNNING']
        if len(running) != len(adopted) or any(r['arm'] != 'NB' for r in running):
            raise RuntimeError('Active episode ownership changed; no handoff')
        registration = {'schema': 't2a-dev-scheduling-amendment-p1', 'split': 'DEV', 'paper_data': False,
            'authorization': 'User requested RECIPE / AWM / RET concurrently on 2026-10-12',
            'registered_at': time.time(), 'root': str(root),
            'registration_sha256': digest(root / 'registration.json'),
            'controller': str(Path(__file__).resolve()), 'controller_sha256': digest(__file__),
            'original_supervisor': {**old, 'argv': [a.decode() for a in old['argv']]},
            'adopted_workers': adopted, 'launches': launches,
            'pair_barrier': False, 'scope': 'Remaining DEV scheduling only',
            'runtime_unchanged': True, 'three_repair_turns_unchanged': True,
            'no_episode_restart': True, 'no_phase_budget_reset': True,
            'phase_binding': reg['phase_binding'],
            'terminal_integrity_required': True, 'formal_launch_allowed': False,
            'reason': 'Idle host capacity; timings from mixed DEV waves are not matched-load paper evidence'}
        save(amendment / 'registration.json', registration)
        # Terminate ONLY the paused batch supervisor. Its independent NB worker
        # survives. SIGCONT delivers the pending SIGTERM to the stopped parent.
        os.kill(old_pid, signal.SIGTERM); os.kill(old_pid, signal.SIGCONT)
        transferred = True
        for _ in range(40):
            if not alive(old): break
            time.sleep(.05)
        else: raise RuntimeError('Old supervisor still alive; refusing new launches')
        save(amendment / 'started.json', {'started': time.time(), 'old_supervisor_stopped': True,
                                         'adopted': adopted, 'launched': []})
        env = dict(os.environ); env.update(PYTHONDONTWRITEBYTECODE='1', CUDA_VISIBLE_DEVICES='')
        started = []
        for row in launches:
            if (root / 'episodes' / name(row)).exists():
                raise RuntimeError('Episode appeared after handoff; no retry')
            handle = (root / (name(row) + '.worker.log')).open('a'); handles.append(handle)
            cmd = ['taskset', '-c', ','.join(map(str, row['cpu_affinity'])), sys.executable,
                   str(root / 'code/memory/scripts/t2a_batch.py'), 'episode', '--root', str(root),
                   '--task', row['task'], '--arm', row['arm'], '--repeat', str(row['repeat']), '--split', 'DEV']
            child = subprocess.Popen(cmd, env=env, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
            children.append(child); started.append({**row, 'pid': child.pid, 'started': time.time()})
            save(amendment / 'started.json', {'started': registration['registered_at'],
                'old_supervisor_stopped': True, 'adopted': adopted, 'launched': started})
        all_slots = [a['cpu_affinity'] for a in adopted] + [r['cpu_affinity'] for r in launches]
        with (root / 'machine-load.jsonl').open('a') as log:
            while any(c.poll() is None for c in children) or any(alive(a) for a in adopted):
                log.write(json.dumps({'ts': time.time(), 'loadavg': os.getloadavg(), 'slots': all_slots,
                                      'scheduling_amendment': str(amendment / 'registration.json')}) + '\n')
                log.flush(); time.sleep(30)
        if any(c.returncode for c in children):
            raise RuntimeError('A new worker aborted; no retry')
        if any(not (root / 'episodes' / name(r) / 'episode.json').exists()
               or json.loads((root / 'episodes' / name(r) / 'episode.json').read_text()).get('status') == 'RUNNING'
               for r in reg['plan']):
            raise RuntimeError('A worker ended without a terminal record; no retry')
        save(root / 'batch-complete.json', {'ended': time.time(), 'episodes': 7,
             'scheduling_amendment': str(amendment / 'registration.json')})
        D.collect(root)
        # Independent stricter gate: do not accept the current collector's
        # NOT_ACCEPTED label when a restoration stopped before final verification.
        path = root / 'dev-validation.json'; receipt = json.loads(path.read_text())
        save(amendment / 'original-collector-receipt.json', receipt)
        gaps = terminal_gaps(root, reg)
        receipt['terminal_integrity_failed_arms'] = gaps
        receipt['scheduling_amendment'] = str(amendment / 'registration.json')
        receipt['original_collector_receipt_sha256'] = digest(amendment / 'original-collector-receipt.json')
        for row in receipt['records']:
            row['terminal_verification_complete'] = row['arm'] not in gaps
            row['criteria_passed'] = row['criteria_passed'] and row['terminal_verification_complete']
        receipt['real_seven_arm_dev_validated'] = receipt['real_seven_arm_dev_validated'] and not gaps
        save(path, receipt)
        save(amendment / 'complete.json', {'ended': time.time(), 'terminal_integrity_failed_arms': gaps,
             'real_seven_arm_dev_validated': receipt['real_seven_arm_dev_validated'], 'formal_launch_allowed': False})
    except Exception as exc:
        save(amendment / 'error.json', {'time': time.time(), 'error': str(exc),
                                      'ownership_transferred': transferred, 'no_retry': True})
        raise
    finally:
        if stopped and not transferred and alive(old): os.kill(old_pid, signal.SIGCONT)
        for handle in handles: handle.close()


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--amendment', type=Path, required=True)
    ap.add_argument('--old-supervisor', type=int, required=True)
    args = ap.parse_args()
    run(args.root.resolve(), args.amendment.resolve(), args.old_supervisor)
