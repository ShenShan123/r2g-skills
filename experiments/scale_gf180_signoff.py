#!/usr/bin/env python3
"""Expand a legacy GF180 signoff queue without restarting its running checks.

The legacy controller and its launch shells are stopped, while its already
running podman checks keep computing. Their capture pipes are drained here;
real zombie wait statuses and complete reports are adopted before retiring the
old queued work. Only disjoint pending designs enter the expanded queue.

Checker code, flat deck, switches, artifacts and per-design budgets stay intact.
This is a Linux/same-uid, operator-invoked migration, not a general job server.
"""
from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from collections import Counter
import ctypes
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import shlex
import signal
import subprocess
import time
import threading
import xml.etree.ElementTree as ET


def read(path, default=None):
    return json.loads(path.read_text()) if path.is_file() else default


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f'.{os.getpid()}.tmp')
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def proc(pid):
    fields = Path('/proc', str(pid), 'stat').read_text().rsplit(')', 1)[1].split()
    return {'pid': pid, 'state': fields[0], 'ppid': int(fields[1]),
            'start': fields[19], 'wait_status': int(fields[49])}


def argv(pid):
    return [s.decode() for s in Path('/proc', str(pid), 'cmdline').read_bytes().split(b'\0') if s]


def children(pid):
    found = []
    for path in Path('/proc').glob('[0-9]*'):
        try:
            p = proc(int(path.name))
            if p['ppid'] == pid:
                found.append(p)
        except (OSError, ValueError, IndexError):
            continue
    return found


def pinned_signal(identity, sig):
    import errno
    try:
        if hasattr(os, 'pidfd_open'):
            fd = os.pidfd_open(identity['pid'])
        else:
            if os.uname().machine not in ('x86_64', 'aarch64', 'riscv64'):
                raise RuntimeError('Unsupported pidfd syscall ABI')
            libc = ctypes.CDLL(None, use_errno=True)
            libc.syscall.restype = ctypes.c_long
            fd = libc.syscall(ctypes.c_long(434), ctypes.c_int(identity['pid']), ctypes.c_uint(0))
            if fd < 0:
                code = ctypes.get_errno()
                raise OSError(code, os.strerror(code))
    except OSError as exc:
        if exc.errno != errno.ENOSYS:
            raise
        # Older Linux kernels lack pidfds. Restrict this fallback to an
        # identity checked twice immediately before signalling, and check
        # again afterwards; every other pidfd failure remains fatal.
        for _ in range(2):
            current = proc(identity['pid'])
            if (current['start'], current['ppid']) != (identity['start'], identity['ppid']):
                raise RuntimeError('Process identity changed before legacy signal')
        os.kill(identity['pid'], sig)
        try:
            current = proc(identity['pid'])
        except FileNotFoundError:
            if sig not in (signal.SIGTERM, signal.SIGKILL):
                raise
        else:
            if current['start'] != identity['start']:
                raise RuntimeError('Process PID reused after legacy signal')
        return
    try:
        if proc(identity['pid'])['start'] != identity['start']:
            raise RuntimeError('Process identity changed')
        if hasattr(signal, 'pidfd_send_signal'):
            signal.pidfd_send_signal(fd, sig)
        else:
            libc = ctypes.CDLL(None, use_errno=True)
            libc.syscall.restype = ctypes.c_long
            result = libc.syscall(ctypes.c_long(424), ctypes.c_int(fd), ctypes.c_int(sig),
                                  ctypes.c_void_p(), ctypes.c_uint(0))
            if result < 0:
                code = ctypes.get_errno()
                raise OSError(code, os.strerror(code))
    finally:
        os.close(fd)


def classify_podman(command, projroot):
    if Path(command[0]).name != 'podman' or command[1:3] != ['run', '--rm']:
        raise RuntimeError('Unexpected legacy subprocess')
    mounts = [command[i + 1] for i, arg in enumerate(command[:-1]) if arg == '-v']
    sources = [m.split(':', 1)[0] for m in mounts if ':/src:ro,' in m]
    if len(sources) != 1:
        raise RuntimeError('Missing unique read-only GDS source')
    source = Path(sources[0]).resolve()
    project = source.parent.parent.parent
    if (not project.is_relative_to(projroot.resolve()) or project.parent != projroot.resolve()
            or source.relative_to(project).as_posix() != 'backend/RUN_1/results'):
        raise RuntimeError('Legacy source does not match this project root')
    if not any(m.startswith(str(project / 'drc') + ':/out:') for m in mounts):
        raise RuntimeError('Legacy output does not match its source project')
    expected_mounts = {str(projroot.parent / 'gf180_deck_signoff') + ':/deck:ro,Z',
                       str(source) + ':/src:ro,Z', str(project / 'drc') + ':/out:Z'}
    if set(mounts) != expected_mounts or len(mounts) != 3:
        raise RuntimeError('Unexpected deck/source/output mounts')
    if command[-4:-1] != ['r2g-orfs:26Q3', 'bash', '-lc']:
        raise RuntimeError('Legacy image or shell differs')
    shell = command[-1]
    if not all(t in shell for t in ('timeout --kill-after=60 21600 ', 'klayout -b -r main.drc',
                                    'run_mode=flat', 'offgrid=false', 'thr=4')):
        raise RuntimeError('Legacy checker contract differs')
    top = re.search(r'-rd topcell=(\S+)', shell)
    if not top:
        raise RuntimeError('Missing legacy topcell')
    switches = ('metal_top=9K', 'mim_option=B', 'metal_level=5LM', 'feol=true',
                'beol=true', 'conn_drc=true', 'run_mode=flat', 'offgrid=false', 'thr=4')
    expected = ['cd', '/deck', '&&', 'timeout', '--kill-after=60', '21600',
                'klayout', '-b', '-r', 'main.drc', '-rd', 'input=/src/6_final.gds',
                '-rd', 'topcell=' + top.group(1), '-rd', 'report=/out/6_drc.lyrdb']
    for switch in switches:
        expected.extend(['-rd', switch])
    if shlex.split(shell) != expected:
        raise RuntimeError('Legacy DRC command is not the exact frozen checker')
    return {'design': project.name, 'project': str(project), 'top': top.group(1)}


def pipe_readers(parent, child):
    """Open the stopped parent's existing capture read ends, without consuming yet."""
    readers = []
    try:
        for channel in (1, 2):
            link = os.readlink(f'/proc/{child}/fd/{channel}')
            if not link.startswith('pipe:['):
                raise RuntimeError('Legacy child output is not a capture pipe')
            candidates = [p for p in Path('/proc', str(parent), 'fd').iterdir()
                          if os.readlink(p) == link]
            if len(candidates) != 1:
                raise RuntimeError('Cannot identify unique legacy pipe reader')
            fd = os.open(candidates[0], os.O_RDONLY | os.O_NONBLOCK)
            readers.append(fd)
        return readers
    except BaseException:
        for fd in readers:
            os.close(fd)
        raise


def stop_controller(pid, driver_path):
    identity = proc(pid)
    command = argv(pid)
    if str(driver_path) not in command or '--platform' not in command or 'gf180' not in command:
        raise RuntimeError('Legacy controller identity/arguments do not match')
    shells = []
    parent = identity['ppid']
    while parent > 1:
        p = proc(parent)
        cmd = argv(parent)
        if Path(cmd[0]).name != 'bash' or not any(
                x.endswith('/rerun_baseline.sh') or '/launch_gf180_20261026/run.sh' in x for x in cmd):
            break
        shells.append(p)
        parent = p['ppid']
    if not shells:
        raise RuntimeError('Cannot establish the old automatic phase launcher')
    stopped = []
    try:
        for p in [identity] + shells:
            pinned_signal(p, signal.SIGSTOP)
            stopped.append(p)
            deadline = time.monotonic() + 5
            while proc(p['pid'])['state'] not in ('T', 't'):
                if time.monotonic() > deadline:
                    raise RuntimeError('Legacy process did not stop')
                time.sleep(.02)
        return identity, shells
    except BaseException:
        for p in stopped:
            pinned_signal(p, signal.SIGCONT)
        raise


def prepare_adoption(args):
    controller, shells = stop_controller(args.legacy_pid, args.driver)
    active, readers = [], {}
    try:
        for p in children(args.legacy_pid):
            if p['state'] in ('Z', 'X'):
                raise RuntimeError('Child exited during snapshot; resume and retry')
            info = classify_podman(argv(p['pid']), args.projroot)
            row = {**p, **info}
            if read(args.runs / row['design'] / 'result.json', {}).get('drc_status'):
                raise RuntimeError('Active legacy design already has a verdict')
            readers[p['pid']] = pipe_readers(args.legacy_pid, p['pid'])
            # Verify pinned original and in-flight copy before accepting a check.
            original = args.runs / row['design'] / '6_final.gds'
            staged = Path(row['project']) / 'backend/RUN_1/results/6_final.gds'
            row['gds_sha256'] = sha(original)
            if sha(staged) != row['gds_sha256']:
                raise RuntimeError('Legacy signoff GDS differs from frozen baseline')
            active.append(row)
        if len(active) != 10 or len({a['design'] for a in active}) != 10:
            raise RuntimeError('Expected exactly ten distinct legacy checks')
        projects = {p.name for p in args.projroot.iterdir() if p.is_dir()}
        if projects != {a['design'] for a in active}:
            raise RuntimeError('Legacy project inventory does not match active checks')
        save(args.control / 'legacy_adoption.json', {
            'controller': controller, 'shells': shells, 'active': active,
            'driver_sha256': sha(args.driver), 'committed': True})
        return controller, shells, active, readers
    except BaseException:
        for fds in readers.values():
            for fd in fds:
                os.close(fd)
        # No new jobs or result writes have started; restoring the original
        # controller is safe only before this adoption transaction commits.
        for p in [controller] + shells:
            pinned_signal(p, signal.SIGCONT)
        raise


def prepare_successor(args):
    """Reconfigure an already-paused expander, preserving its active checkers."""
    previous = args.previous_control
    recorded = read(previous / 'controller.json')
    controller = proc(args.legacy_pid)
    if (recorded['pid'] != args.legacy_pid or recorded['start'] != controller['start']
            or controller['state'] not in ('T', 't')):
        raise RuntimeError('Previous expander is not the pinned stopped controller')
    inherited = read(previous / 'legacy_adoption.json')
    shells = [inherited['controller']] + inherited['shells']
    known = {r['pid']: r for r in read(previous / 'fixed64_children.json')['children']}
    known.update({r['pid']: r for r in inherited['active']})
    active, readers, unknown = [], {}, []
    direct = children(args.legacy_pid)
    original = children(inherited['controller']['pid'])
    try:
        for p in direct + original:
            cached = known.get(p['pid'], {})
            command = argv(p['pid'])
            if command:
                info = classify_podman(command, args.projroot)
            elif cached.get('design') and cached.get('project'):
                info = {'design': cached['design'], 'project': cached['project'], 'top': cached.get('top')}
            elif cached.get('info'):
                info = cached['info']
            else:
                if p['state'] not in ('Z', 'X'):
                    raise RuntimeError('Live checker has no project identity')
                unknown.append(p)
                continue
            if read(args.runs / info['design'] / 'result.json', {}).get('drc_status'):
                continue
            row = {**p, **info}
            readers[p['pid']] = pipe_readers(args.legacy_pid, p['pid']) if p['state'] not in ('Z', 'X') else []
            row['gds_sha256'] = sha(args.runs / row['design'] / '6_final.gds')
            if sha(Path(row['project']) / 'backend/RUN_1/results/6_final.gds') != row['gds_sha256']:
                raise RuntimeError('Successor found a changed baseline layout')
            active.append(row)
        active_names = {a['design'] for a in active}
        unattributed = [p for p in args.projroot.iterdir() if p.is_dir() and p.name not in active_names
                        and not read(args.runs / p.name / 'result.json', {}).get('drc_status')]
        # A finished child can lose argv before an operator snapshots it. Do
        # not infer a per-design exit status from ordering or a report alone.
        # Preserve those reports and repeat only those already finished checks.
        archive = args.control / 'unattributed_finished'
        for project in unattributed:
            destination = archive / project.name
            destination.mkdir(parents=True, exist_ok=True)
            if (project / 'drc').is_dir():
                shutil.copytree(project / 'drc', destination / 'drc', dirs_exist_ok=True)
        save(args.control / 'unattributed_rechecks.json', {
            'unattributed_finished_processes': unknown,
            'recheck_designs': [p.name for p in unattributed],
            'reason': 'Completed check could not be bound to its CLI exit status; not accepted without recheck'})
        save(args.control / 'legacy_adoption.json', {
            'controller': controller, 'shells': shells, 'active': active,
            'driver_sha256': sha(args.driver), 'committed': True,
            'previous_control': str(previous)})
        return controller, shells, active, readers
    except BaseException:
        for fds in readers.values():
            for fd in fds:
                os.close(fd)
        # Caller deliberately keeps the expander paused until this operation
        # succeeds; do not reactivate its superseded automatic scaling policy.
        raise


def report_items(path):
    count, root, items = 0, None, False
    for event, node in ET.iterparse(path, events=('start', 'end')):
        if root is None:
            root = node.tag
            if root != 'report-database':
                raise ValueError('Not a KLayout report database')
        if event == 'start' and node.tag == 'items':
            items = True
        if event == 'end' and node.tag == 'item':
            count += 1
        if event == 'end':
            node.clear()
    if not items:
        raise ValueError('KLayout report has no item container')
    return count


def adopt(row, fds, args, driver):
    logpath = args.control / 'adopted_logs' / (row['design'] + '.log')
    logpath.parent.mkdir(exist_ok=True)
    tail = bytearray()
    try:
        with logpath.open('ab', buffering=0) as logfile:
            while True:
                for fd in fds:
                    while True:
                        try:
                            block = os.read(fd, 65536)
                        except BlockingIOError:
                            break
                        if not block:
                            break
                        logfile.write(block)
                        tail.extend(block)
                        if len(tail) > 200000:
                            del tail[:-200000]
                p = proc(row['pid'])
                if p['start'] != row['start']:
                    raise RuntimeError('Legacy child PID was reused')
                if p['state'] in ('Z', 'X'):
                    # The child may have written between the first drain and
                    # its exit. Drain once more after the writer is gone.
                    for fd in fds:
                        while True:
                            try:
                                block = os.read(fd, 65536)
                            except BlockingIOError:
                                break
                            if not block:
                                break
                            logfile.write(block)
                            tail.extend(block)
                            if len(tail) > 200000:
                                del tail[:-200000]
                    break
                time.sleep(.2)
        rc = os.waitstatus_to_exitcode(p['wait_status'])
        project = Path(row['project'])
        original = args.runs / row['design']
        if sha(original / '6_final.gds') != row['gds_sha256'] or sha(
                project / 'backend/RUN_1/results/6_final.gds') != row['gds_sha256']:
            raise RuntimeError('Frozen GDS changed during adopted check')
        duration = float(Path('/proc/uptime').read_text().split()[0]) - int(row['start']) / os.sysconf('SC_CLK_TCK')
        out = {'design': row['design'], 'drc_status': 'drc_failed', 'violations': None,
               'seconds': round(duration, 1), 'gds_rebuilt': False,
               'adopted_legacy_check': True, 'podman_exit_code': rc,
               'gds_sha256': row['gds_sha256']}
        report = project / 'drc/6_drc.lyrdb'
        if rc in (124, 137):
            out['drc_status'] = 'timeout'
        elif rc == 0 and report.is_file():
            count = report_items(report)
            out.update(drc_status='clean' if count == 0 else 'violations', violations=count,
                       report_sha256=sha(report))
        if report.is_file():
            shutil.copy2(report, original / '6_drc.lyrdb')
        text = tail.decode(errors='replace')
        text += f'\nAdopted original check: podman_rc={rc}; complete_report={report.is_file()}\n'
        if out['violations'] is not None:
            text += f"DRC completed: {out['violations']} violations\n"
        (original / 'drc_signoff.log').write_text(text[-20000:])
        driver.record(original, out)
        save(args.control / 'adopted_results' / (row['design'] + '.json'), out)
        # Do not remove the legacy project: it is the retained adoption evidence.
        return out
    finally:
        for fd in fds:
            os.close(fd)


def cpu_sample():
    return [int(v) for v in Path('/proc/stat').read_text().splitlines()[0].split()[1:]]


def resources(before):
    after = cpu_sample()
    delta = [b - a for a, b in zip(before, after)]
    total = max(1, sum(delta))
    mem = next(int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()
               if line.startswith('MemAvailable:')) / 1024 / 1024
    return after, {'cpu_busy_pct': round(100 * (sum(delta) - delta[3] - delta[4]) / total, 1),
                   'iowait_pct': round(100 * delta[4] / total, 1), 'available_gb': round(mem, 1),
                   'load1': round(os.getloadavg()[0], 2)}


def snapshot(args, running, target, phase='drc'):
    rows = []
    for p in sorted(args.runs.glob('*/result.json')):
        r = read(p, {})
        if r.get('status') == 'pass':
            rows.append({'design': p.parent.name, 'drc_status': r.get('drc_status', 'pending'),
                         'violations': r.get('drc_violations_signoff'), 'seconds': r.get('drc_seconds'),
                         'lvs_status': r.get('lvs_status', 'pending')})
    save(args.out, [r for r in rows if r['drc_status'] != 'pending'])
    state = {'updated_at': time.time(), 'phase': phase, 'controller_pid': os.getpid(),
             'target_concurrency': target, 'active_checks': running,
             'drc': dict(Counter(r['drc_status'] for r in rows)),
             'lvs': dict(Counter(r['lvs_status'] for r in rows))}
    save(args.control / 'progress.json', state)
    return state


def retire_legacy(controller, shells):
    identities = [controller] + shells
    own = {p['pid'] for p in identities}
    remaining = [p for identity in identities for p in children(identity['pid'])
                 if p['pid'] not in own and p['state'] not in ('Z', 'X')]
    if remaining:
        raise RuntimeError('Cannot retire legacy controller with live children')
    # A stopped controller must never be resumed after new work was dispatched:
    # it still owns its stale queued futures. SIGKILL prevents Python finalizers
    # from draining that stale queue. All of its actual checks have exited.
    for p in [controller] + shells:
        pinned_signal(p, signal.SIGKILL)


def final_lvs(text):
    finals = re.findall(r'^Final result:[ \t]*([^\r\n]*)', text, re.M)
    if not finals:
        return ''
    value = finals[-1].strip()
    if value in ('Circuits match uniquely.', 'Netlists match uniquely.'):
        return 'clean'
    if value == 'Top level cell failed pin matching.':
        return 'pin_mismatch'
    if 'do not match' in value or '**MISMATCH**' in value:
        return 'mismatch'
    if value == 'Circuits match with warnings.':
        return 'clean_with_warnings'
    return ''


def run_lvs_stage(args):
    base = args.driver.parent
    spec = importlib.util.spec_from_file_location('legacy_lvs', base / 'lvs130.py')
    lvs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lvs)
    # Same extraction and comparison; read the final top-level result, never a
    # successful subcell that happened to appear earlier in the checker log.
    lvs.verdict = final_lvs
    large = threading.Semaphore(1)
    todo = []
    for path in args.runs.glob('*/result.json'):
        r = read(path, {})
        if r.get('status') == 'pass' and not r.get('lvs_status'):
            todo.append((r.get('cells') or 0, path.parent))
    todo.sort(key=lambda row: row[0])
    projroot = base / 'rerun_gf180_lvsproj'
    projroot.mkdir(exist_ok=True)
    save(args.control / 'lvs_launch.json', {'workers': 80, 'large_design_workers': 1,
         'large_design_threshold_cells': 100000, 'timeout': args.timeout,
         'checker_sha256': sha(base / 'lvs130.py'), 'final_top_level_verdict_required': True,
         'designs': [p.name for _, p in todo]})
    def check(cells, path):
        if cells > 100000:
            with large:
                return lvs.one_and_record(path, projroot, args.timeout, 'gf180')
        return lvs.one_and_record(path, projroot, args.timeout, 'gf180')
    results = []
    lvs_workers = args.workers if args.fixed_workers else 80
    with ThreadPoolExecutor(max_workers=lvs_workers) as pool:
        futures = {pool.submit(check, cells, path): path.name for cells, path in todo}
        while futures:
            done, _ = wait(futures, timeout=15, return_when=FIRST_COMPLETED)
            for f in done:
                name = futures.pop(f)
                result = f.result()
                results.append(result)
                print(json.dumps({'lvs_finished': name, 'result': result}), flush=True)
            save(args.out.parent / 'netgen_lvs.json', results)
            snapshot(args, len(futures), lvs_workers, phase='lvs')


def audit_official_reports(args):
    base = args.driver.parent
    deck = base / 'miniconda3/envs/eda/share/pdk/gf180mcuC/libs.tech/klayout/drc/rule_decks'
    # This is get_list_of_tables(default flat) in the pinned official runner,
    # plus --antenna. Keep split tables in the inventory, even if their body
    # is inactive with split_deep=false, exactly as the official runner does.
    expected = {p.stem for p in deck.glob('*.drc')
                if all(t not in str(p) for t in ('antenna', 'density', 'main', 'layers_def', 'tail'))}
    expected.add('antenna')
    if len(expected) < 40:
        raise RuntimeError('Unexpected official DRC table inventory')
    audits = []
    for row in read(args.out.parent / 'official_drc.json', []):
        path = args.runs / row['design'] / 'result.json'
        r = read(path, {})
        if r.get('drc_checker') != 'pdk_run_drc.py':
            continue
        work = base / 'rerun_gf180_offdrc' / row['design']
        found = {p.name.removeprefix('6_final_').removesuffix('.lyrdb')
                 for p in work.glob('6_final_*.lyrdb')}
        missing = sorted(expected - found)
        invalid = []
        for table in sorted(expected & found):
            try:
                report_items(work / ('6_final_' + table + '.lyrdb'))
            except (OSError, ValueError, ET.ParseError):
                invalid.append(table)
        audit = {'design': row['design'], 'expected_tables': sorted(expected),
                 'missing_tables': missing, 'invalid_tables': invalid,
                 'complete': not missing and not invalid}
        if not audit['complete']:
            r['drc_official_unqualified_status'] = r.get('drc_status')
            r['drc_status'] = 'drc_failed'
            r['drc_scope_audit'] = audit
            save(path, r)
        audits.append(audit)
    save(args.control / 'official_scope_audit.json', audits)


def run(args):
    spec = importlib.util.spec_from_file_location('legacy_signoff', args.driver)
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    controller, shells, active, readers = prepare_successor(args) if args.previous_control else prepare_adoption(args)
    reserved = {a['design'] for a in active}
    pending = [p.parent for p in sorted(args.runs.glob('*/result.json'))
               if p.parent.name not in reserved and read(p, {}).get('status') == 'pass'
               and not read(p, {}).get('drc_status')]
    save(args.control / 'expanded_queue.json', {
        'initial_workers': args.workers, 'maximum_workers': args.max_workers,
        'adopted': sorted(reserved), 'new_designs': [p.name for p in pending],
        'timeout': args.timeout, 'checker_sha256': sha(args.driver),
        'checker_switches': list(driver.GF180_SWITCHES)})
    legacy_remaining = len(active)
    target = args.workers
    save(args.control / 'runtime_limits.json', {'design_workers': target,
         'automatic': not args.fixed_workers, 'maximum': target if args.fixed_workers else args.max_workers})
    sample = cpu_sample()
    last_sample = time.monotonic()
    queue = iter(pending)
    exhausted = False
    futures = {}
    retired = False
    errors = []
    with ThreadPoolExecutor(max_workers=args.max_workers) as pool:
        for a in active:
            f = pool.submit(adopt, a, readers[a['pid']], args, driver)
            futures[f] = ('adopt', a['design'])
        while futures or not exhausted:
            limits = read(args.control / 'runtime_limits.json', {})
            maximum = min(args.max_workers, int(limits.get('maximum', args.max_workers)))
            automatic = limits.get('automatic', not args.fixed_workers)
            if not automatic:
                target = min(maximum, max(10, int(limits.get('design_workers', args.workers))))
            while not exhausted and len(futures) < target:
                p = next(queue, None)
                if p is None:
                    exhausted = True
                    break
                if read(p / 'result.json', {}).get('drc_status'):
                    continue
                f = pool.submit(driver.one_and_record, p, args.projroot, args.timeout, 'gf180')
                futures[f] = ('new', p.name)
            done, _ = wait(futures, timeout=5, return_when=FIRST_COMPLETED)
            for f in done:
                kind, name = futures.pop(f)
                try:
                    result = f.result()
                except Exception as exc:
                    result = {'design': name, 'drc_status': 'drc_failed',
                              'message': str(exc), 'violations': None, 'seconds': None}
                    save(args.control / 'control_errors' / (name + '.json'), result)
                    errors.append(result)
                    if kind == 'new':
                        driver.record(args.runs / name, result)
                print(json.dumps({'finished': name, 'kind': kind, 'result': result}), flush=True)
                if kind == 'adopt':
                    legacy_remaining -= 1
            if not legacy_remaining and not retired:
                retire_legacy(controller, shells)
                retired = True
                save(args.control / 'legacy_retired.json', {'at': time.time(), 'all_original_checks_finished': True})
            if done or time.monotonic() - last_sample >= 30:
                if time.monotonic() - last_sample >= 30:
                    sample, measured = resources(sample)
                    if automatic and (measured['available_gb'] < 64 or measured['iowait_pct'] > 20):
                        target = max(16, target - 8)
                    elif automatic and measured['cpu_busy_pct'] < 85:
                        target = min(maximum, target + 8)
                    elif automatic and measured['cpu_busy_pct'] > 98 and measured['load1'] > (os.cpu_count() or 96) * 1.5:
                        target = max(16, target - 8)
                    last_sample = time.monotonic()
                else:
                    _, measured = resources(sample)
                state = snapshot(args, len(futures), target)
                print(json.dumps({'resources': measured, 'progress': state}), flush=True)
        snapshot(args, 0, target, phase='official_drc')
    if not retired:
        raise RuntimeError('Legacy checks did not all retire')
    if errors:
        raise RuntimeError('Recorded control errors need review before later signoff stages')
    base = args.driver.parent
    commands = [
        ('official_drc', ['python3', '-I', str(base / 'gf180_hybrid_drc.py'), '--runs', str(args.runs),
                         '--out', str(args.out.parent / 'official_drc.json'),
                         '--work', str(base / 'rerun_gf180_offdrc'), '--workers', str(args.workers // 4 if args.fixed_workers else 20), '--mp', '4',
                         '--timeout', str(args.timeout)]),
    ]
    for phase, command in commands:
        snapshot(args, 0, 20 if phase == 'official_drc' else 80, phase=phase)
        with (args.control / (phase + '.log')).open('ab') as log:
            p = subprocess.Popen(command, cwd=base, stdout=log, stderr=subprocess.STDOUT)
            save(args.control / (phase + '_launch.json'), {'pid': p.pid, 'argv': command, 'at': time.time()})
            while p.poll() is None:
                snapshot(args, 0, 20 if phase == 'official_drc' else 80, phase=phase)
                time.sleep(15)
        if p.returncode != 0:
            raise RuntimeError(f'{phase} exited {p.returncode}; inspect phase log')
    audit_official_reports(args)
    run_lvs_stage(args)
    launch = args.driver.parent / 'launch_gf180_20261026'
    with (args.control / 'input_check_end.log').open('wb') as log:
        subprocess.run(['sha256sum', '-c', str(launch / 'inputs.sha256')],
                       cwd=args.driver.parent, stdout=log, stderr=subprocess.STDOUT, check=True)
    snapshot(args, 0, 0, phase='complete')
    save(args.control / 'exit_code.json', {'code': 0, 'completed_at': time.time()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--legacy-pid', type=int, required=True)
    parser.add_argument('--driver', type=Path, required=True)
    parser.add_argument('--runs', type=Path, required=True)
    parser.add_argument('--projroot', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--control', type=Path, required=True)
    parser.add_argument('--previous-control', type=Path)
    parser.add_argument('--fixed-workers', action='store_true')
    parser.add_argument('--workers', type=int, default=40)
    parser.add_argument('--max-workers', type=int, default=96)
    parser.add_argument('--timeout', type=int, default=21600)
    args = parser.parse_args()
    for key in ('driver', 'runs', 'projroot', 'out', 'control'):
        setattr(args, key, getattr(args, key).resolve())
    if args.timeout != 21600 or not 10 <= args.workers <= args.max_workers <= 96:
        parser.error('Preserve the six-hour bound and use 10..96 design slots')
    args.control.mkdir(parents=True, exist_ok=True)
    with (args.control / '.controller.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (args.control / 'legacy_adoption.json').exists():
            raise RuntimeError('An adoption receipt already exists; do not blindly re-adopt')
        save(args.control / 'controller.json', {'pid': os.getpid(), 'start': proc(os.getpid())['start'],
                                              'args': {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()}})
        run(args)


if __name__ == '__main__':
    main()
